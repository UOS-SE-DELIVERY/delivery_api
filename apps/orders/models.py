from django.utils import timezone

from django.db import transaction

from django.db import models
from django.db.models import Q
from apps.accounts.models import Customer
from apps.catalog.models import (
    DinnerType, ServingStyle, MenuItem
)


class OrderStatus(models.TextChoices):
    PENDING   = "pending", "Pending"
    PREP      = "preparing", "Preparing"
    OUT       = "out_for_delivery", "OutForDelivery"
    DELIVERED = "delivered", "Delivered"
    CANCELED  = "canceled", "Canceled"


class OrderSource(models.TextChoices):
    GUI   = "GUI",   "GUI"
    VOICE = "VOICE", "VOICE"


class Order(models.Model):
    id = models.BigAutoField(primary_key=True)
    customer = models.ForeignKey(Customer, on_delete=models.RESTRICT, related_name="orders")
    ordered_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.PENDING)
    order_source = models.CharField(max_length=10, choices=OrderSource.choices, default=OrderSource.GUI)

    # 배송 스냅샷
    receiver_name = models.TextField(null=True, blank=True)
    receiver_phone = models.TextField(null=True, blank=True)
    delivery_address = models.TextField(null=True, blank=True)
    geo_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    geo_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    place_label = models.TextField(null=True, blank=True)
    address_meta = models.JSONField(null=True, blank=True)

    # 결제 스냅샷
    payment_token = models.TextField(null=True, blank=True)
    card_last4 = models.CharField(max_length=4, null=True, blank=True)

    # 합계
    subtotal_cents = models.PositiveIntegerField(default=0)
    discount_cents = models.PositiveIntegerField(default=0)
    total_cents = models.PositiveIntegerField(default=0)

    meta = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "orders"
        indexes = [
            models.Index(
                fields=["customer", "-ordered_at"],
                name="idx_orders_customer_recent",
            )
        ]

    def __str__(self) -> str:
        return f"Order#{self.id}"

    # ===== Domain helpers (refactor) =====
    def _append_staff_op(self, event: str, by: int | None, note: str | None = None) -> None:
        """
        staff_ops에 이벤트 로그를 쌓는다.
        orders_notify() 트리거가 'action' 필드를 보고 ready 여부를 계산하므로
        event와 action을 동일하게 넣어 준다.
        """
        m = dict(self.meta or {}) if self.meta else {}
        ops = list(m.get("staff_ops", []))
        from django.utils import timezone as _tz

        entry = {
            "event": event,
            "action": event,  # PG 함수는 op->>'action'을 참조하므로 둘 다 채운다.
            "by": by,
            "at": _tz.now().isoformat(),
            "note": note or "",
        }
        ops.append(entry)
        m["staff_ops"] = ops
        self.meta = m

    def _compute_ready_flag(self) -> bool:
        """
        meta.staff_ops 안에 mark_ready 기록이 있는지로 ready 여부를 계산한다.
        'action' 또는 'event' 둘 중 하나가 mark_ready면 ready=True.
        """
        meta = self.meta or {}
        ops = meta.get("staff_ops") or []
        if not isinstance(ops, list):
            return False

        for op in ops:
            if not isinstance(op, dict):
                continue
            action = op.get("action") or op.get("event")
            if action == "mark_ready":
                return True
        return False

    def _notify(self, event_name: str, payload: dict | None = None) -> None:
        """
        Postgres NOTIFY를 통해 staff SSE에 이벤트를 보낸다.

        항상 다음 정보를 포함하도록 확장했다.
        - event      : 논리 이벤트 이름(order_created, order_status_changed, ...)
        - order_id   : 주문 PK
        - id         : order_id와 동일(기존 payload와 호환)
        - status     : 현재 주문 status (또는 payload에서 override)
        - ready      : meta.staff_ops 기반 ready 플래그
        - ordered_at : ISO8601 문자열
        - order      : OrderOutSerializer 스냅샷 (bootstrap과 동일한 구조)
        """
        try:
            from django.conf import settings as _settings
            from django.db import connections as _connections, transaction as _tx
            import json as _json

            msg = dict(payload or {})

            oid = getattr(self, "id", getattr(self, "pk", None))
            if oid is not None:
                msg.setdefault("order_id", oid)
                msg.setdefault("id", oid)

            # status / ready / ordered_at 기본값 채우기
            if "status" not in msg and hasattr(self, "status"):
                msg["status"] = self.status
            if "ready" not in msg:
                msg["ready"] = self._compute_ready_flag()
            if "ordered_at" not in msg and getattr(self, "ordered_at", None) is not None:
                msg["ordered_at"] = self.ordered_at.isoformat()

            # full order 스냅샷 추가 (bootstrap과 동일한 구조)
            try:
                from .serializers import OrderOutSerializer
                msg.setdefault("order", OrderOutSerializer(self).data)
            except Exception:
                # 직렬화 실패해도 최소 정보만 가진 이벤트는 날린다.
                pass

            msg.setdefault("event", event_name)

            channels = list(getattr(_settings, "ORDERS_NOTIFY_CHANNELS", ["orders_events"]))
            using = "default"
            raw = _json.dumps(msg, ensure_ascii=False)

            def _do_notify() -> None:
                with _connections[using].cursor() as cur:
                    for ch in channels:
                        cur.execute("SELECT pg_notify(%s, %s)", [ch, raw])

            if _tx.get_connection(using).in_atomic_block:
                _tx.on_commit(_do_notify)
            else:
                _do_notify()
        except Exception:
            # NOTIFY 실패해도 비즈니스 로직까지 죽이지 않는다.
            pass

    # ===== State transitions (behavior methods) =====
    def accept(self, by_staff_id: int | None = None) -> "Order":
        if self.status != OrderStatus.PENDING:
            raise Exception("Only pending orders can be accepted.")
        from django.db import transaction as _tx
        with _tx.atomic():
            self.status = OrderStatus.PREP
            self._append_staff_op("accept", by_staff_id)
            self.save(update_fields=["status", "meta"])
            # 상태 전이 이벤트 (preparing)
            self._notify(
                "order_status_changed",
                {
                    "order_id": getattr(self, "id", getattr(self, "pk", None)),
                    "status": self.status,
                },
            )
        return self

    def mark_ready(self, by_staff_id: int | None = None) -> "Order":
        if self.status != OrderStatus.PREP:
            raise Exception("Only preparing orders can be marked ready.")
        from django.db import transaction as _tx
        with _tx.atomic():
            self._append_staff_op("mark_ready", by_staff_id)
            self.save(update_fields=["meta"])
            # ready 상태로 승격: 상태 이벤트로 취급하고,
            # SSE payload에는 ready=True + full order가 포함된다.
            self._notify(
                "order_status_changed",
                {
                    "order_id": getattr(self, "id", getattr(self, "pk", None)),
                    # DB status는 여전히 preparing이지만,
                    # ready 플래그와 full order 스냅샷이 함께 나간다.
                    "status": self.status,
                },
            )
        return self

    def out_for_delivery(self, by_staff_id: int | None = None) -> "Order":
        if self.status != OrderStatus.PREP:
            raise Exception("Only preparing orders can go out for delivery.")
        from django.db import transaction as _tx
        with _tx.atomic():
            self.status = OrderStatus.OUT
            self._append_staff_op("out_for_delivery", by_staff_id)
            self.save(update_fields=["status", "meta"])
            self._notify(
                "order_status_changed",
                {
                    "order_id": getattr(self, "id", getattr(self, "pk", None)),
                    "status": self.status,
                },
            )
        return self

    def deliver(self, by_staff_id: int | None = None) -> "Order":
        if self.status != OrderStatus.OUT:
            raise Exception("Only orders out for delivery can be delivered.")
        from django.db import transaction as _tx
        with _tx.atomic():
            self.status = OrderStatus.DELIVERED
            self._append_staff_op("deliver", by_staff_id)
            self.save(update_fields=["status", "meta"])
            self._notify(
                "order_status_changed",
                {
                    "order_id": getattr(self, "id", getattr(self, "pk", None)),
                    "status": self.status,
                },
            )
        return self

    def cancel(self, by_staff_id: int | None = None, reason: str | None = None) -> "Order":
        if self.status in (OrderStatus.DELIVERED, OrderStatus.CANCELED):
            raise Exception("Cannot cancel already completed/canceled order.")
        from django.db import transaction as _tx
        with _tx.atomic():
            self.status = OrderStatus.CANCELED
            self._append_staff_op("cancel", by_staff_id, reason)
            self.save(update_fields=["status", "meta"])
            self._notify(
                "order_status_changed",
                {
                    "order_id": getattr(self, "id", getattr(self, "pk", None)),
                    "status": self.status,
                    "reason": reason or "",
                },
            )
        return self


class OrderDinner(models.Model):
    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="dinners")
    dinner_type = models.ForeignKey(DinnerType, on_delete=models.RESTRICT)
    style = models.ForeignKey(ServingStyle, on_delete=models.RESTRICT)
    person_label = models.TextField(null=True, blank=True)  # 수취인/좌석 라벨 등
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    base_price_cents = models.PositiveIntegerField()        # 디너 기준가 스냅샷
    style_adjust_cents = models.PositiveIntegerField(default=0)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "order_dinner"


class ChangeType(models.TextChoices):
    UNCHANGED = "unchanged", "Unchanged"
    ADDED     = "added", "Added"
    REMOVED   = "removed", "Removed"
    INCREASED = "increased", "Increased"
    DECREASED = "decreased", "Decreased"


class OrderDinnerItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    order_dinner = models.ForeignKey(OrderDinner, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(MenuItem, on_delete=models.RESTRICT)
    final_qty = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price_cents = models.PositiveIntegerField()
    is_default = models.BooleanField(default=False)
    change_type = models.CharField(max_length=12, choices=ChangeType.choices, default=ChangeType.UNCHANGED)

    class Meta:
        db_table = "order_dinner_item"
        unique_together = (("order_dinner", "item"),)


class OrderItemOption(models.Model):
    id = models.BigAutoField(primary_key=True)
    order_dinner_item = models.ForeignKey(OrderDinnerItem, on_delete=models.CASCADE, related_name="options")
    option_group_name = models.TextField()
    option_name = models.TextField()
    price_delta_cents = models.PositiveIntegerField(default=0)
    multiplier = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)

    class Meta:
        db_table = "order_item_option"


class OrderDinnerOption(models.Model):
    id = models.BigAutoField(primary_key=True)
    order_dinner = models.ForeignKey(OrderDinner, on_delete=models.CASCADE, related_name="options")
    option_group_name = models.TextField()
    option_name = models.TextField()
    price_delta_cents = models.PositiveIntegerField(default=0)
    multiplier = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)

    class Meta:
        db_table = "order_dinner_option"


# ===== Signals: order_created 시 full SSE 이벤트 송출 =====
from django.db.models.signals import post_save  # noqa: E402
from django.dispatch import receiver  # noqa: E402


@receiver(post_save, sender=Order)
def _order_created_notify(sender, instance: Order, created: bool, **kwargs) -> None:
    """
    새 Order가 INSERT 되었을 때 order_created 이벤트를 한 번 쏜다.
    payload에는 최소 order_id만 넣고, 나머지(ready/status/order 전체)는
    _notify()에서 채우도록 한다.
    """
    if not created:
        return
    try:
        instance._notify("order_created", {"order_id": instance.pk})
    except Exception:
        # NOTIFY 실패해도 트랜잭션은 그대로 진행
        pass
