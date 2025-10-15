from __future__ import annotations
from decimal import Decimal
from typing import Dict, List

from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Customer
from apps.catalog.models import (
    MenuItem, DinnerType, ServingStyle,
    DinnerTypeDefaultItem, DinnerOption
)
from apps.promotion.services import evaluate_discounts, redeem_discounts

from .models import (
    Order, OrderDinner, OrderDinnerItem,
    OrderItemOption, OrderDinnerOption
)
from .serializers import (
    OrderCreateRequestSerializer, OrderOutSerializer,
    # 프리뷰 IO
    PricePreviewRequestSerializer, PricePreviewResponseSerializer,
    LineItemOutSerializer, LineOptionOutSerializer,
    AdjustmentOutSerializer, DiscountLineOutSerializer,
)
from .services.pricing import (
    as_cents_int, as_cents_dec,
    calc_item_unit_cents, apply_style_to_base,
    validate_style_allowed, validate_item_options_for_item, resolve_dinner_options_for_dinner,
)

# ---------- 주문 목록/생성 ----------
class OrderListCreateAPIView(generics.ListCreateAPIView):
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

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        s = OrderCreateRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data

        customer = Customer.objects.filter(pk=data["customer_id"]).first()
        if not customer:
            return Response({"detail": "Invalid customer_id"}, status=400)

        # 헤더 생성(옵션 필드 일괄 매핑)
        optional_fields = [
            "receiver_name","receiver_phone","delivery_address",
            "geo_lat","geo_lng","place_label","address_meta",
            "payment_token","card_last4","meta",
        ]
        payload = {k: (data.get(k) or None) for k in optional_fields}
        order = Order.objects.create(
            customer=customer,
            status="pending",
            order_source=data.get("order_source", "GUI"),
            subtotal_cents=0, discount_cents=0, total_cents=0,
            **payload,
        )

        subtotal = 0

        # ---- 디너(필수) ----
        dsel = data["dinner"]
        dinner = DinnerType.objects.filter(code=dsel["code"], active=True).first()
        if not dinner:
            return Response({"detail": "Invalid dinner.code"}, status=400)

        style = ServingStyle.objects.filter(code=dsel["style"]).first()
        if not style:
            return Response({"detail": "Invalid dinner.style"}, status=400)

        try:
            validate_style_allowed(dinner, style)
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)

        unit_cents, style_adjust_cents = apply_style_to_base(dinner, style)
        qty = Decimal(dsel.get("quantity") or "1")

        try:
            dinner_opts = resolve_dinner_options_for_dinner(dinner, dsel.get("dinner_options") or [])
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)

        for dop in dinner_opts:
            if (dop.group.price_mode or "addon") == "addon":
                unit_cents += int(dop.price_delta_cents or 0)
            else:
                m = Decimal(dop.multiplier or "1")
                unit_cents = as_cents_int(Decimal(unit_cents) * m)

        dinner_subtotal = as_cents_int(Decimal(unit_cents) * qty)
        subtotal += dinner_subtotal

        od = OrderDinner.objects.create(
            order=order, dinner_type=dinner, style=style,
            person_label=None, quantity=qty,
            base_price_cents=dinner.base_price_cents,
            style_adjust_cents=style_adjust_cents, notes=None
        )

        # 디너 옵션 스냅샷
        for dop in dinner_opts:
            if (dop.group.price_mode or "addon") == "addon":
                OrderDinnerOption.objects.create(
                    order_dinner=od,
                    option_group_name=dop.group.name,
                    option_name=(dop.item.name if dop.item_id else dop.name),
                    price_delta_cents=int(dop.price_delta_cents or 0),
                    multiplier=None
                )
            else:
                OrderDinnerOption.objects.create(
                    order_dinner=od,
                    option_group_name=dop.group.name,
                    option_name=(dop.item.name if dop.item_id else dop.name),
                    price_delta_cents=0,
                    multiplier=Decimal(dop.multiplier or "1")
                )

        # 디너 기본 아이템 스냅샷
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

        # ---- 개별 아이템 ----
        for it in data.get("items", []):
            item = MenuItem.objects.filter(code=it["code"], active=True).first()
            if not item:
                return Response({"detail": f"Invalid item.code: {it['code']}"}, status=400)

            try:
                sel_opts = validate_item_options_for_item(item, it.get("options") or [])
            except ValueError as e:
                return Response({"detail": str(e)}, status=400)

            unit_item_cents, snaps = calc_item_unit_cents(item, sel_opts)
            qty_item = Decimal(it["qty"])
            line_sub = as_cents_int(Decimal(unit_item_cents) * qty_item)
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

        # ---- 프로모션 평가지원 (promotion 서비스 호출) ----
        coupon_codes = [c["code"] for c in data.get("coupons", [])]
        discounts, total_disc, total_after = evaluate_discounts(
            subtotal_cents=subtotal,
            customer_id=data["customer_id"],
            channel=data.get("order_source") or "GUI",
            dinner_code=dinner.code,
            item_lines=[],  # 필요 시 라인 전달 가능
            style_code=style.code,
            dinner_option_ids=[dop.pk for dop in dinner_opts],
            coupon_codes=coupon_codes,
        )

        # 합계 고정
        order.subtotal_cents = int(subtotal)
        order.discount_cents = int(total_disc)
        order.total_cents = int(total_after)

        meta = data.get("meta") or {}
        if discounts:
            meta = {**meta, "discounts": discounts}
        order.meta = meta or None
        order.save(update_fields=["subtotal_cents", "discount_cents", "total_cents", "meta"])

        # 사용량 확정
        redeem_discounts(
            order=order,
            customer_id=data["customer_id"],
            channel=data.get("order_source") or "GUI",
            discounts=discounts,  # code/amount_cents 포함된 라인 배열을 넘김
        )

        return Response(OrderOutSerializer(order).data, status=201)

# ---------- 주문 단건 ----------
class OrderDetailAPIView(generics.RetrieveAPIView):
    serializer_class = OrderOutSerializer
    queryset = (Order.objects
                .select_related("customer")
                .prefetch_related(
                    Prefetch("dinners",
                             queryset=(OrderDinner.objects
                                      .select_related("dinner_type", "style")
                                      .prefetch_related("items__options", "options")))
                ))

# ---------- 가격 프리뷰 ----------
class OrderPricePreviewAPIView(APIView):
    """
    POST /api/orders/price/preview
    - 디너 base 포함, multiplier는 디너 가격에만
    - 스타일/옵션 소속 검증
    - HALF_UP 반올림
    - 할인은 promotion.evaluate_discounts 위임
    """
    def post(self, request):
        s = PricePreviewRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data

        dsel = data["dinner"]

        dinner = DinnerType.objects.filter(code=dsel["code"], active=True).first()
        if not dinner:
            return Response({"detail": "Invalid dinner.code"}, status=400)

        style = ServingStyle.objects.filter(code=dsel["style"]).first()
        if not style:
            return Response({"detail": "Invalid dinner.style"}, status=400)

        try:
            validate_style_allowed(dinner, style)
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)

        unit_cents, _style_adj = apply_style_to_base(dinner, style)
        qty = Decimal(dsel.get("quantity") or "1")

        try:
            dinner_opts = resolve_dinner_options_for_dinner(dinner, dsel.get("dinner_options") or [])
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)

        adjustments = []
        # 스타일 라인(표시용)
        if (style.price_mode or "addon") == "addon":
            adjustments.append(AdjustmentOutSerializer({
                "type": "style", "label": style.name,
                "mode": "addon", "value_cents": int(style.price_value or 0), "multiplier": None,
            }).data)
        else:
            m = Decimal(style.price_value or "1.0")
            adjustments.append(AdjustmentOutSerializer({
                "type": "style", "label": style.name,
                "mode": "multiplier", "value_cents": None, "multiplier": m,
            }).data)
            unit_cents = as_cents_int(Decimal(unit_cents))  # 이미 apply_style_to_base에서 반영됨

        # 디너 옵션은 디너 가격에만 반영
        for dop in dinner_opts:
            if (dop.group.price_mode or "addon") == "addon":
                delta = int(dop.price_delta_cents or 0)
                unit_cents += delta
                adjustments.append(AdjustmentOutSerializer({
                    "type": "dinner_option",
                    "label": dop.name or (dop.item.name if dop.item_id else "Option"),
                    "mode": "addon", "value_cents": delta, "multiplier": None,
                }).data)
            else:
                m = Decimal(dop.multiplier or "1.0")
                unit_cents = as_cents_int(Decimal(unit_cents) * m)
                adjustments.append(AdjustmentOutSerializer({
                    "type": "dinner_option",
                    "label": dop.name or (dop.item.name if dop.item_id else "Option"),
                    "mode": "multiplier", "value_cents": None, "multiplier": m,
                }).data)

        dinner_subtotal = as_cents_int(Decimal(unit_cents) * qty)

        # 아이템 라인
        items_total = 0
        line_items = []
        for it in data.get("items", []):
            item = MenuItem.objects.filter(code=it["code"], active=True).first()
            if not item:
                return Response({"detail": f"Invalid item.code: {it['code']}"}, status=400)

            try:
                sel_opts = validate_item_options_for_item(item, it.get("options") or [])
            except ValueError as e:
                return Response({"detail": str(e)}, status=400)

            unit_item_cents, snaps = calc_item_unit_cents(item, sel_opts)
            qty_item = Decimal(it["qty"])
            line_sub = as_cents_int(Decimal(unit_item_cents) * qty_item)
            items_total += line_sub

            line_items.append(LineItemOutSerializer({
                "item_code": item.code,
                "name": item.name,
                "qty": qty_item,
                "unit_price_cents": unit_item_cents,
                "options": [LineOptionOutSerializer(snap).data for snap in snaps],
                "subtotal_cents": line_sub,
            }).data)

        subtotal = dinner_subtotal + items_total

        # 할인 위임
        coupon_codes = [c["code"] for c in data.get("coupons", [])]
        discounts, total_disc, total_after = evaluate_discounts(
            subtotal_cents=subtotal,
            customer_id=data.get("customer_id"),
            channel=data.get("order_source") or "GUI",
            dinner_code=dinner.code,
            item_lines=[{"code": li["item_code"], "qty": str(li["qty"])} for li in line_items],
            style_code=style.code,
            dinner_option_ids=[dop.pk for dop in dinner_opts],
            coupon_codes=coupon_codes,
        )

        out = {
            "line_items": line_items,
            "adjustments": adjustments,
            "subtotal_cents": int(subtotal),
            "discounts": [DiscountLineOutSerializer(d).data for d in discounts],
            "discount_cents": int(total_disc),
            "total_cents": int(total_after),
        }
        return Response(PricePreviewResponseSerializer(out).data, status=200)
