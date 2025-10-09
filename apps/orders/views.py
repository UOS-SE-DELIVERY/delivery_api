from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Tuple

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Customer
from apps.catalog.models import (
    MenuItem, ItemOption, ItemOptionGroup,
    DinnerType, ServingStyle, DinnerStyleAllowed,
    DinnerTypeDefaultItem, DinnerOption
)
from .models import (
    Order, OrderDinner, OrderDinnerItem,
    OrderItemOption, OrderDinnerOption
)
from .serializers import (
    OrderCreateRequestSerializer, OrderOutSerializer
)

# ---------- helpers ----------

def as_cents(x: Decimal) -> int:
    return int(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def calc_item_unit_cents(item: MenuItem, selected_opts: List[ItemOption]) -> Tuple[int, List[Dict]]:
    """
    (base + Σ addon) × Π multiplier
    반환: (unit_price_cents, option_snapshots[])
    snapshots item:
      - addon: {option_group_name, option_name, price_delta_cents>0, multiplier=None}
      - multiplier: {option_group_name, option_name, price_delta_cents=0, multiplier>1}
    """
    base = Decimal(item.base_price_cents)
    addon = Decimal("0")
    mult = Decimal("1")
    snaps: List[Dict] = []

    for o in selected_opts:
        g: ItemOptionGroup = o.group
        if g.price_mode == "addon":
            addon += Decimal(o.price_delta_cents)
            snaps.append({
                "option_group_name": g.name,
                "option_name": o.name,
                "price_delta_cents": int(o.price_delta_cents),
                "multiplier": None
            })
        else:
            m = Decimal(o.multiplier or "1")
            mult *= m
            snaps.append({
                "option_group_name": g.name,
                "option_name": o.name,
                "price_delta_cents": 0,
                "multiplier": m
            })

    unit = (base + addon) * mult
    return as_cents(unit), snaps

def apply_style_to_base(dinner: DinnerType, style: ServingStyle) -> Tuple[int, int]:
    """
    반환: (unit_cents, style_adjust_cents)
    addon: base + addon, multiplier: round(base * m), adjust는 (new_base - base)
    """
    base = Decimal(dinner.base_price_cents)
    if style.price_mode == "addon":
        new_base = base + Decimal(style.price_value)
        return as_cents(new_base), int(Decimal(style.price_value).quantize(Decimal("1")))
    else:
        new_base = base * Decimal(style.price_value)
        return as_cents(new_base), as_cents(new_base - base)

# ---------- views ----------

class OrderListAPIView(generics.ListAPIView):
    """
    GET /api/orders?customer_id=...
    """
    serializer_class = OrderOutSerializer

    def get_queryset(self):
        qs = (Order.objects
              .select_related("customer")
              .prefetch_related(
                  Prefetch("dinners",
                           queryset=(OrderDinner.objects
                                    .select_related("dinner_type", "style")
                                    .prefetch_related("items__options", "options")))
              )
              .order_by("-ordered_at"))
        cid = self.request.query_params.get("customer_id")
        if cid:
            qs = qs.filter(customer_id=cid)
        return qs

class OrderDetailAPIView(generics.RetrieveAPIView):
    """
    GET /api/orders/{id}
    """
    serializer_class = OrderOutSerializer
    queryset = (Order.objects
                .select_related("customer")
                .prefetch_related(
                    Prefetch("dinners",
                             queryset=(OrderDinner.objects
                                      .select_related("dinner_type", "style")
                                      .prefetch_related("items__options", "options")))
                ))

class OrderCreateAPIView(APIView):
    """
    POST /api/orders
    - models에 맞춰 style은 필수
    - 모든 아이템은 어떤 디너(OrderDinner)에 소속되어 스냅샷됨
    """
    @transaction.atomic
    def post(self, request):
        s = OrderCreateRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data

        # 고객 확인
        try:
            customer = Customer.objects.get(pk=data["customer_id"])
        except Customer.DoesNotExist:
            return Response({"detail": "Invalid customer_id"}, status=400)

        # 주문 헤더 생성(합계는 나중에 업데이트)
        order = Order.objects.create(
            customer=customer,
            status="pending",
            order_source=data.get("order_source", "GUI"),
            receiver_name=data.get("receiver_name") or None,
            receiver_phone=data.get("receiver_phone") or None,
            delivery_address=data.get("delivery_address") or None,
            geo_lat=data.get("geo_lat"),
            geo_lng=data.get("geo_lng"),
            place_label=data.get("place_label") or None,
            address_meta=data.get("address_meta") or None,
            payment_token=data.get("payment_token") or None,
            card_last4=data.get("card_last4") or None,
            subtotal_cents=0, discount_cents=0, total_cents=0,
            meta=data.get("meta") or None,
        )

        subtotal = 0

        # ---- Dinner (필수) ----
        dsel = data["dinner"]
        dinner = DinnerType.objects.filter(code=dsel["code"], active=True).first()
        if not dinner:
            return Response({"detail": "Invalid dinner.code"}, status=400)

        style = ServingStyle.objects.filter(code=dsel["style"]).first()
        if not style:
            return Response({"detail": "Invalid dinner.style"}, status=400)

        # 허용 조합 검증
        if not DinnerStyleAllowed.objects.filter(dinner_type=dinner, style=style).exists():
            return Response({"detail": "Style not allowed for this dinner"}, status=400)

        unit_cents, style_adjust_cents = apply_style_to_base(dinner, style)
        qty = Decimal(dsel.get("quantity") or "1")
        dinner_subtotal = int(qty * unit_cents)
        subtotal += dinner_subtotal

        od = OrderDinner.objects.create(
            order=order, dinner_type=dinner, style=style,
            person_label=None, quantity=qty,
            base_price_cents=dinner.base_price_cents,
            style_adjust_cents=style_adjust_cents, notes=None
        )

        # 디너 기본 아이템 스냅샷(포함 여부에 따라 단가 0 처리 가능)
        defaults = (DinnerTypeDefaultItem.objects
                    .filter(dinner_type=dinner)
                    .select_related("item")
                    .order_by("item__name"))
        for di in defaults:
            unit = 0 if getattr(di, "included_in_base", False) else di.item.base_price_cents
            OrderDinnerItem.objects.create(
                order_dinner=od, item=di.item,
                final_qty=di.default_qty,
                unit_price_cents=unit,
                is_default=True, change_type="unchanged"
            )

        # 디너 옵션 스냅샷
        for opt_id in dsel.get("dinner_options", []):
            dop = (DinnerOption.objects
                   .filter(pk=opt_id, group__dinner_type=dinner)
                   .select_related("group", "item")
                   .first())
            if not dop:
                return Response({"detail": f"Invalid dinner_option id={opt_id}"}, status=400)

            if dop.group.price_mode == "addon":
                delta = int(Decimal(dop.price_value).quantize(Decimal("1")))
                OrderDinnerOption.objects.create(
                    order_dinner=od,
                    option_group_name=dop.group.name,
                    option_name=(dop.item.name if dop.item_id else dop.name),
                    price_delta_cents=delta,
                    multiplier=None
                )
                subtotal += delta * int(qty)
            else:
                m = Decimal(dop.price_value)
                OrderDinnerOption.objects.create(
                    order_dinner=od,
                    option_group_name=dop.group.name,
                    option_name=(dop.item.name if dop.item_id else dop.name),
                    price_delta_cents=0,
                    multiplier=m
                )
                # 배수는 단가에 곱 → 차액 반영(단순화)
                new_unit = as_cents(Decimal(unit_cents) * m)
                subtotal += int(qty * (new_unit - unit_cents))
                unit_cents = new_unit

        # ---- 개별 아이템(해당 디너에 추가) ----
        for it in data.get("items", []):
            item = MenuItem.objects.filter(code=it["code"], active=True).first()
            if not item:
                return Response({"detail": f"Invalid item.code: {it['code']}"}, status=400)

            sel_opts = []
            if it.get("options"):
                opts = (ItemOption.objects
                        .filter(pk__in=it["options"])
                        .select_related("group", "group__item"))
                for o in opts:
                    if o.group.item_id != item.item_id:
                        return Response({"detail": f"Option {o.pk} not for item {item.code}"}, status=400)
                    sel_opts.append(o)

            unit_item_cents, snaps = calc_item_unit_cents(item, sel_opts)
            qty_item = Decimal(it["qty"])
            line_sub = int(qty_item * unit_item_cents)
            subtotal += line_sub

            odi = OrderDinnerItem.objects.create(
                order_dinner=od, item=item,
                final_qty=qty_item,
                unit_price_cents=unit_item_cents,
                is_default=False, change_type="added"
            )
            for sopt in snaps:
                OrderItemOption.objects.create(
                    order_dinner_item=odi,
                    option_group_name=sopt["option_group_name"],
                    option_name=sopt["option_name"],
                    price_delta_cents=sopt["price_delta_cents"],
                    multiplier=sopt["multiplier"]
                )

        # 합계 고정
        order.subtotal_cents = subtotal
        order.discount_cents = 0
        order.total_cents = subtotal
        order.save(update_fields=["subtotal_cents", "discount_cents", "total_cents"])

        return Response(OrderOutSerializer(order).data, status=201)
