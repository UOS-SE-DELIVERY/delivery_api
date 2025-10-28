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
        indexes = [models.Index(fields=["customer", "-ordered_at"], name="idx_orders_customer_recent")]

    def __str__(self): return f"Order#{self.id}"


    # ===== Domain helpers (refactor) =====
    def _append_staff_op(self, event: str, by: int | None, note: str | None = None) -> None:
        m = dict(self.meta or {}) if self.meta else {}
        ops = list(m.get("staff_ops", []))
        from django.utils import timezone as _tz
        ops.append({"event": event, "by": by, "at": _tz.now().isoformat(), "note": note or ""})
        m["staff_ops"] = ops
        self.meta = m

    def _notify(self, event_name: str, payload: dict) -> None:
        # Minimal Postgres NOTIFY to integrate with staff SSE (channel: orders_events)
        try:
            from django.conf import settings as _settings
            from django.db import connections as _connections, transaction as _tx
            import json as _json
            channels = list(getattr(_settings, "ORDERS_NOTIFY_CHANNELS", ["orders_events"]))
            using = "default"
            msg = dict(payload or {})
            msg.setdefault("event", event_name)
            raw = _json.dumps(msg, ensure_ascii=False)

            def _do_notify():
                with _connections[using].cursor() as cur:
                    for ch in channels:
                        cur.execute("SELECT pg_notify(%s, %s)", [ch, raw])

            if _tx.get_connection(using).in_atomic_block:
                _tx.on_commit(_do_notify)
            else:
                _do_notify()
        except Exception:
            # No-op on failure
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
            self._notify("order_status_changed", {"order_id": getattr(self, "id", getattr(self, "pk", None)), "status": self.status})
        return self

    def mark_ready(self, by_staff_id: int | None = None) -> "Order":
        if self.status != OrderStatus.PREP:
            raise Exception("Only preparing orders can be marked ready.")
        from django.db import transaction as _tx
        with _tx.atomic():
            self._append_staff_op("mark_ready", by_staff_id)
            self.save(update_fields=["meta"])
            self._notify("order_updated", {"order_id": getattr(self, "id", getattr(self, "pk", None)), "ready": True})
        return self

    def out_for_delivery(self, by_staff_id: int | None = None) -> "Order":
        if self.status != OrderStatus.PREP:
            raise Exception("Only preparing orders can go out for delivery.")
        from django.db import transaction as _tx
        with _tx.atomic():
            self.status = OrderStatus.OUT
            self._append_staff_op("out_for_delivery", by_staff_id)
            self.save(update_fields=["status", "meta"])
            self._notify("order_status_changed", {"order_id": getattr(self, "id", getattr(self, "pk", None)), "status": self.status})
        return self

    def deliver(self, by_staff_id: int | None = None) -> "Order":
        if self.status != OrderStatus.OUT:
            raise Exception("Only orders out for delivery can be delivered.")
        from django.db import transaction as _tx
        with _tx.atomic():
            self.status = OrderStatus.DELIVERED
            self._append_staff_op("deliver", by_staff_id)
            self.save(update_fields=["status", "meta"])
            self._notify("order_status_changed", {"order_id": getattr(self, "id", getattr(self, "pk", None)), "status": self.status})
        return self

    def cancel(self, by_staff_id: int | None = None, reason: str | None = None) -> "Order":
        if self.status in (OrderStatus.DELIVERED, OrderStatus.CANCELED):
            raise Exception("Cannot cancel already completed/canceled order.")
        from django.db import transaction as _tx
        with _tx.atomic():
            self.status = OrderStatus.CANCELED
            self._append_staff_op("cancel", by_staff_id, reason)
            self.save(update_fields=["status", "meta"])
            self._notify("order_status_changed", {"order_id": getattr(self, "id", getattr(self, "pk", None)), "status": self.status, "reason": reason or ""})
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