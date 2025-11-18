# apps/orders/views.py
from __future__ import annotations

from decimal import Decimal
from typing import List, Tuple, Dict

from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Customer
from apps.catalog.models import (
    MenuItem, DinnerType, ServingStyle,
    DinnerTypeDefaultItem
)
from apps.promotion.services import evaluate_discounts, redeem_discounts

from .models import (
    Order, OrderDinner, OrderDinnerItem,
    OrderItemOption, OrderDinnerOption
)
from .serializers import (
    OrderCreateRequestSerializer, OrderOutSerializer,
    PricePreviewRequestSerializer, PricePreviewResponseSerializer,
    LineItemOutSerializer, LineOptionOutSerializer,
    AdjustmentOutSerializer, DiscountLineOutSerializer,
    OrderDinnerSelectionSerializer, OrderItemSelectionSerializer,
)
from .services.pricing import (
    as_cents_int,
    calc_item_unit_cents, apply_style_to_base,
    validate_style_allowed, validate_item_options_for_item, resolve_dinner_options_for_dinner,
)

from drf_spectacular.utils import (
    extend_schema, OpenApiParameter, OpenApiExample, OpenApiResponse, inline_serializer
)
from rest_framework import serializers


# ---------- 공통: 입력 정규화 ----------
def _normalize_payloads(raw: dict) -> List[Dict]:
    """
    세 가지 입력형을 모두 지원하여 '디너-아이템' 묶음 리스트로 정규화한다.
    반환 구조: [{'dinner': validated_dsel, 'items': [validated_item, ...]}, ...]
    우선순위: orders > dinners > dinner
    """
    packs: List[Dict] = []

    # A) orders: [{dinner:{...}, items:[...]}]
    if raw.get("orders") is not None:
        orders_raw = raw.get("orders") or []
        if not orders_raw:
            raise serializers.ValidationError({"orders": "must contain at least one element"})
        for idx, o in enumerate(orders_raw):
            if "dinner" not in o:
                raise serializers.ValidationError({f"orders[{idx}].dinner": "required"})
            ds = OrderDinnerSelectionSerializer(data=o["dinner"])
            ds.is_valid(raise_exception=True)
            items_norm = []
            for it in o.get("items", []):
                iser = OrderItemSelectionSerializer(data=it)
                iser.is_valid(raise_exception=True)
                items_norm.append(iser.validated_data)
            packs.append({"dinner": ds.validated_data, "items": items_norm})
        return packs

    # B) dinners: [ {...}, ... ] (+ 상단 items는 첫 디너에 귀속)
    if raw.get("dinners") is not None:
        dinners_raw = raw.get("dinners") or []
        if not dinners_raw:
            raise serializers.ValidationError({"dinners": "must contain at least one dinner"})
        items_top = []
        for it in raw.get("items", []):
            iser = OrderItemSelectionSerializer(data=it)
            iser.is_valid(raise_exception=True)
            items_top.append(iser.validated_data)

        for i, dsel in enumerate(dinners_raw):
            ds = OrderDinnerSelectionSerializer(data=dsel)
            ds.is_valid(raise_exception=True)
            packs.append({"dinner": ds.validated_data, "items": (items_top if i == 0 else [])})
        return packs

    # C) dinner: {...} (+ 상단 items는 해당 단일 디너에 귀속)
    if raw.get("dinner") is not None:
        ds = OrderDinnerSelectionSerializer(data=raw["dinner"])
        ds.is_valid(raise_exception=True)
        items_norm = []
        for it in raw.get("items", []):
            iser = OrderItemSelectionSerializer(data=it)
            iser.is_valid(raise_exception=True)
            items_norm.append(iser.validated_data)
        packs.append({"dinner": ds.validated_data, "items": items_norm})
        return packs

    raise serializers.ValidationError({"dinner": "required (or provide 'dinners' / 'orders')"})


# ---------- 주문 목록/생성 ----------
@extend_schema(
    methods=['GET'],
    tags=['Orders'],
    summary='주문 목록 조회',
    description="주문 목록을 최신순으로 반환합니다. `customer_id`로 특정 고객의 주문만 필터링할 수 있습니다.",
    parameters=[
        OpenApiParameter(name='customer_id', type=int, location=OpenApiParameter.QUERY,
                         description='특정 고객의 주문만 조회'),
    ],
    responses=OrderOutSerializer,
)
@extend_schema(
    methods=['POST'],
    tags=['Orders'],
    summary='주문 생성(단일/다중 디너 + 디너별 아이템)',
    description=(
        "- `orders` 배열 - 각 원소에 `dinner`(필수)와 그 디너 전용 `items`(선택)\n\n"
        "각 디너의 `dinner_options`/`default_overrides`는 **디너별로 독립 처리**됩니다. "
        "`default_overrides`는 기본 포함 아이템 수량 조정을 위한 필드입니다.(선택)"
        "상단 공통 필드(`receiver_name`, `receiver_phone`, `delivery_address`, `coupons` 등)는 한 번만 전달하면 됩니다."
    ),
    request=OrderCreateRequestSerializer,  # 상단 공통 필드 검증용 (기존 호환)
    responses={201: OrderOutSerializer, 400: OpenApiResponse(description='유효하지 않은 입력')},
    examples=[
        OpenApiExample(
            name='orders 배열(권장)',
            value={
                "customer_id": 1,
                "order_source": "GUI",
                "fulfillment_type": "DELIVERY",
                "orders": [
                    {
                        "dinner": {
                            "code": "valentine",
                            "quantity": "1",
                            "style": "simple",
                            "dinner_options": [11, 12],
                            "default_overrides": [{"code": "wine", "qty": "0"}]
                        },
                        "items": [
                            {"code": "steak", "qty": "2", "options": [101, 102]},
                            {"code": "wine",  "qty": "3"}
                        ]
                    },
                    {
                        "dinner": {"code": "champagne_feast", "quantity": "1", "style": "simple"},
                        "items": [{"code": "caviar", "qty": "1"}]
                    }
                ],
                "receiver_name": "홍길동",
                "receiver_phone": "010-1111-2222",
                "delivery_address": "서울 중구 을지로 00",
                "coupons": [{"code": "WELCOME10"}]
            },
            request_only=True
        ),
    ]
)
class OrderListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = OrderOutSerializer

    def get_queryset(self):
        qs = (Order.objects
              .select_related("customer")
              .prefetch_related(
                  Prefetch("dinners",
                           queryset=(OrderDinner.objects
                                    .select_related("dinner_type", "style")
                                    .prefetch_related("items__options", "options"))))
              .order_by("-ordered_at"))
        cid = self.request.query_params.get("customer_id")
        if cid:
            qs = qs.filter(customer_id=cid)
        return qs

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        raw = request.data

        # 상단 공통 필드 검증
        tmp_payload = dict(raw)
        if "orders" in raw and "dinner" not in raw:
            # orders가 있을 때도 상단 필드 검증을 위해 대표 dinner를 임시 주입
            first = (raw.get("orders") or [{}])[0]
            if not first or "dinner" not in first:
                return Response({"detail": "orders must contain at least one element with 'dinner'"},
                                status=400)
            tmp_payload["dinner"] = first["dinner"]
        elif "dinners" in raw and "dinner" not in raw:
            dinners_raw = raw.get("dinners") or []
            if not dinners_raw:
                return Response({"detail": "dinners must contain at least one dinner"}, status=400)
            tmp_payload["dinner"] = dinners_raw[0]

        s = OrderCreateRequestSerializer(data=tmp_payload)
        s.is_valid(raise_exception=True)
        data = s.validated_data

        # 디너-아이템 묶음 정규화
        try:
            packs = _normalize_payloads(raw)
        except serializers.ValidationError as e:
            return Response(e.detail, status=400)

        # 고객
        customer = Customer.objects.filter(pk=data["customer_id"]).first()
        if not customer:
            return Response({"detail": "Invalid customer_id"}, status=400)

        # 주문 헤더
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
        all_dinner_option_ids: List[int] = []

        # 디너들 생성
        for pack in packs:
            dsel = pack["dinner"]
            dinner = DinnerType.objects.filter(code=dsel["code"], active=True).first()
            if not dinner:
                return Response({"detail": f"Invalid dinner.code: {dsel['code']}"}, status=400)

            style = ServingStyle.objects.filter(code=dsel["style"]).first()
            if not style:
                return Response({"detail": f"Invalid dinner.style: {dsel['style']}"}, status=400)

            try:
                validate_style_allowed(dinner, style)
            except ValueError as e:
                return Response({"detail": str(e)}, status=400)

            # base + style
            unit_cents, style_adjust_cents = apply_style_to_base(dinner, style)
            qty = Decimal(dsel.get("quantity") or "1")

            # 디너 옵션
            try:
                dinner_opts = resolve_dinner_options_for_dinner(dinner, dsel.get("dinner_options") or [])
            except ValueError as e:
                return Response({"detail": str(e)}, status=400)

            opt_deltas: List[int] = []
            for dop in dinner_opts:
                if (getattr(dop.group, "price_mode", None) or "addon") == "addon":
                    delta = int(getattr(dop, "price_delta_cents", 0) or 0)
                else:
                    m = Decimal(getattr(dop, "multiplier", None) or "1")
                    delta = as_cents_int(Decimal(unit_cents) * (m - Decimal("1")))
                unit_cents += delta
                opt_deltas.append(delta)
                all_dinner_option_ids.append(dop.pk)

            dinner_subtotal = as_cents_int(Decimal(unit_cents) * qty)
            subtotal += dinner_subtotal

            od = OrderDinner.objects.create(
                order=order, dinner_type=dinner, style=style,
                person_label=None, quantity=qty,
                base_price_cents=dinner.base_price_cents,
                style_adjust_cents=style_adjust_cents, notes=None
            )

            # 디너 옵션 스냅샷
            for dop, delta in zip(dinner_opts, opt_deltas):
                OrderDinnerOption.objects.create(
                    order_dinner=od,
                    option_group_name=dop.group.name,
                    option_name=(dop.item.name if getattr(dop, "item_id", None) else dop.name),
                    price_delta_cents=int(delta),
                    multiplier=None
                )

            # 기본 아이템 스냅샷
            defaults = (DinnerTypeDefaultItem.objects
                        .filter(dinner_type=dinner)
                        .select_related("item")
                        .order_by("item__name"))
            created_default_map = {}  # code -> (odi, default_qty)
            for di in defaults:
                unit = 0 if getattr(di, "included_in_base", False) else di.item.base_price_cents
                odi = OrderDinnerItem.objects.create(
                    order_dinner=od, item=di.item,
                    final_qty=di.default_qty,
                    unit_price_cents=unit,
                    is_default=True, change_type="unchanged"
                )
                created_default_map[di.item.code] = (odi, Decimal(di.default_qty))

            # default_overrides 적용
            for ov in (dsel.get("default_overrides") or []):
                code = str(ov["code"]).strip()
                qty_override = Decimal(str(ov["qty"]))
                if code not in created_default_map:
                    return Response({"detail": f"Invalid default_overrides.code: {code}"}, status=400)
                odi, orig = created_default_map[code]
                if qty_override < 0 or qty_override > orig:
                    return Response({"detail": f"default_overrides.qty must be between 0 and {orig} for code={code}"},
                                    status=400)
                odi.final_qty = qty_override
                odi.change_type = "removed" if qty_override == 0 else ("decreased" if qty_override < orig else "unchanged")
                odi.save(update_fields=["final_qty", "change_type"])

            # (NEW) 이 디너 전용 items 처리
            for it in pack.get("items", []):
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

                odi, created = OrderDinnerItem.objects.get_or_create(
                    order_dinner=od, item=item,
                    defaults={
                        "final_qty": qty_item,
                        "unit_price_cents": unit_item_cents,
                        "is_default": False, "change_type": "added"
                    }
                )
                if not created:
                    odi.final_qty = Decimal(odi.final_qty) + qty_item
                    if odi.is_default and odi.change_type == "unchanged":
                        odi.change_type = "added"
                    odi.save(update_fields=["final_qty", "change_type"])

                for sopt in snaps:
                    OrderItemOption.objects.create(
                        order_dinner_item=odi,
                        option_group_name=sopt["option_group_name"],
                        option_name=sopt["option_name"],
                        price_delta_cents=sopt["price_delta_cents"],
                        multiplier=None
                    )

        # 프로모션(대표: 첫 묶음 기준, 옵션 id는 전체 합산)
        rep = packs[0]["dinner"]
        coupon_codes = [c["code"] for c in data.get("coupons", [])]
        discounts, total_disc, total_after = evaluate_discounts(
            subtotal_cents=subtotal,
            customer_id=data["customer_id"],
            channel=data.get("order_source") or "GUI",
            dinner_code=rep["code"],
            item_lines=[],  # 필요시 라인 전달
            style_code=rep["style"],
            dinner_option_ids=all_dinner_option_ids,
            coupon_codes=coupon_codes,
        )

        order.subtotal_cents = int(subtotal)
        order.discount_cents = int(total_disc)
        order.total_cents = int(total_after)

        meta = data.get("meta") or {}
        if discounts:
            meta = {**meta, "discounts": discounts}
        order.meta = meta or None
        order.save(update_fields=["subtotal_cents", "discount_cents", "total_cents", "meta"])

        redeem_discounts(
            order=order,
            customer_id=data["customer_id"],
            channel=data.get("order_source") or "GUI",
            discounts=discounts,
        )

        return Response(OrderOutSerializer(order).data, status=201)


# ---------- 주문 단건 ----------
@extend_schema(tags=['Orders'], summary='주문 단건 조회', responses=OrderOutSerializer)
class OrderDetailAPIView(generics.RetrieveAPIView):
    serializer_class = OrderOutSerializer
    queryset = (Order.objects
                .select_related("customer")
                .prefetch_related(
                    Prefetch("dinners",
                             queryset=(OrderDinner.objects
                                      .select_related("dinner_type", "style")
                                      .prefetch_related("items__options", "options")))))


# ---------- 가격 프리뷰(orders 지원) ----------
@extend_schema(
    tags=['Orders/Price'],
    summary='가격 프리뷰(단일/다중/orders 모두 지원)',
    description=(
        "`dinner` / `dinners` / `orders` 입력을 모두 지원합니다. "
        "`orders` 사용 시 각 디너별 아이템을 해당 디너에 귀속해 집계합니다. "
        "모든 multiplier는 addon(가산)으로 환산합니다."
    ),
    request=PricePreviewRequestSerializer,
    responses=PricePreviewResponseSerializer,
    examples=[
        OpenApiExample(
            name='프리뷰_orders',
            value={
                "order_source": "GUI",
                "orders": [
                    {"dinner": {"code": "valentine", "quantity": "1", "style": "simple", "dinner_options": [11]},
                     "items": [{"code": "steak", "qty": "2"}]},
                    {"dinner": {"code": "champagne_feast", "quantity": "1", "style": "simple"}}
                ],
                "coupons": [{"code": "WELCOME10"}]
            },
            request_only=True
        ),
    ]
)
class OrderPricePreviewAPIView(APIView):
    def post(self, request):
        raw = request.data
        # 상단 필드 최소 검증
        tmp_payload = dict(raw)
        if "orders" in raw and "dinner" not in raw:
            first = (raw.get("orders") or [{}])[0]
            if not first or "dinner" not in first:
                return Response({"detail": "orders must contain at least one element with 'dinner'"},
                                status=400)
            tmp_payload["dinner"] = first["dinner"]
        elif "dinners" in raw and "dinner" not in raw:
            dinners_raw = raw.get("dinners") or []
            if not dinners_raw:
                return Response({"detail": "dinners must contain at least one dinner"}, status=400)
            tmp_payload["dinner"] = dinners_raw[0]

        s = PricePreviewRequestSerializer(data=tmp_payload)
        s.is_valid(raise_exception=True)

        # 정규화
        try:
            packs = _normalize_payloads(raw)
        except serializers.ValidationError as e:
            return Response(e.detail, status=400)

        adjustments = []
        subtotal = 0
        all_dinner_option_ids: List[int] = []
        line_items = []

        # 디너별 합산
        for pack in packs:
            dsel = pack["dinner"]
            dinner = DinnerType.objects.filter(code=dsel["code"], active=True).first()
            if not dinner:
                return Response({"detail": f"Invalid dinner.code: {dsel['code']}"}, status=400)
            style = ServingStyle.objects.filter(code=dsel["style"]).first()
            if not style:
                return Response({"detail": f"Invalid dinner.style: {dsel['style']}"}, status=400)

            try:
                validate_style_allowed(dinner, style)
            except ValueError as e:
                return Response({"detail": str(e)}, status=400)

            unit_cents, style_adj = apply_style_to_base(dinner, style)
            qty = Decimal(dsel.get("quantity") or "1")

            adjustments.append(AdjustmentOutSerializer({
                "type": "style",
                "label": f"{style.name} @ {dinner.name}",
                "mode": "addon",
                "value_cents": int(style_adj or 0),
            }).data)

            try:
                dinner_opts = resolve_dinner_options_for_dinner(dinner, dsel.get("dinner_options") or [])
            except ValueError as e:
                return Response({"detail": str(e)}, status=400)

            for dop in dinner_opts:
                if (getattr(dop.group, "price_mode", None) or "addon") == "addon":
                    delta = int(getattr(dop, "price_delta_cents", 0) or 0)
                else:
                    m = Decimal(getattr(dop, "multiplier", None) or "1.0")
                    delta = as_cents_int(Decimal(unit_cents) * (m - Decimal("1.0")))
                unit_cents += delta
                adjustments.append(AdjustmentOutSerializer({
                    "type": "dinner_option",
                    "label": f"{dop.name or (dop.item.name if getattr(dop, 'item_id', None) else 'Option')} @ {dinner.name}",
                    "mode": "addon",
                    "value_cents": int(delta),
                }).data)
                all_dinner_option_ids.append(dop.pk)

            # 기본 아이템 삭제/감소(표시)
            default_map = {di.item.code: di for di in DinnerTypeDefaultItem.objects
                           .filter(dinner_type=dinner).select_related("item")}
            for ov in (dsel.get("default_overrides") or []):
                code = str(ov["code"]).strip()
                if code not in default_map:
                    return Response({"detail": f"Invalid default_overrides.code: {code}"}, status=400)
                orig = Decimal(str(default_map[code].default_qty))
                newq = Decimal(str(ov["qty"]))
                if newq < 0 or newq > orig:
                    return Response({"detail": f"default_overrides.qty must be between 0 and {orig} for code={code}"},
                                    status=400)
                mode = "remove" if newq == 0 else ("decrease" if newq < orig else "noop")
                if mode != "noop":
                    adjustments.append(AdjustmentOutSerializer({
                        "type": "default_override",
                        "label": f"{default_map[code].item.name} @ {dinner.name}",
                        "mode": mode,
                        "value_cents": 0,
                    }).data)

            subtotal += as_cents_int(Decimal(unit_cents) * qty)

            # 디너 전용 items 미리보기 라인
            for it in pack.get("items", []):
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

                snaps_norm = [{**snap} for snap in snaps]
                line_items.append(LineItemOutSerializer({
                    "item_code": item.code,
                    "name": f"{item.name} @ {dinner.name}",
                    "qty": qty_item,
                    "unit_price_cents": unit_item_cents,
                    "options": [LineOptionOutSerializer(snap).data for snap in snaps_norm],
                    "subtotal_cents": line_sub,
                }).data)

        # 할인(대표: 첫 묶음 기준)
        rep = packs[0]["dinner"]
        coupon_codes = [c["code"] for c in (s.validated_data.get("coupons") or [])]
        discounts, total_disc, total_after = evaluate_discounts(
            subtotal_cents=subtotal,
            customer_id=s.validated_data.get("customer_id"),
            channel=s.validated_data.get("order_source") or "GUI",
            dinner_code=rep["code"],
            item_lines=[{"code": li["item_code"], "qty": str(li["qty"])} for li in line_items],
            style_code=rep["style"],
            dinner_option_ids=all_dinner_option_ids,
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


# ---------- 상태 전이 액션 ----------
@extend_schema(
    tags=['Orders/Actions'],
    summary='주문 액션 실행',
    description=(
        "주문 상태 전이 액션을 실행합니다.\n\n"
        "- `accept` → preparing\n- `mark-ready` → 타임스탬프 기록\n"
        "- `out-for-delivery` → out_for_delivery\n- `deliver` → delivered\n- `cancel` → canceled"
    ),
    parameters=[OpenApiParameter(name='id', type=int, location=OpenApiParameter.PATH, description='주문 ID')],
    request=inline_serializer(
        name='OrderActionReq',
        fields={
            'action': serializers.ChoiceField(
                choices=['accept','mark-ready','ready','out-for-delivery','dispatch','out','deliver','delivered','cancel']
            ),
            'reason': serializers.CharField(required=False, allow_null=True, allow_blank=True),
        }
    ),
    responses={200: OrderOutSerializer, 400: OpenApiResponse(description='잘못된 입력'), 409: OpenApiResponse(description='도메인 규칙 위반')},
    examples=[
        OpenApiExample(name='accept', value={"action": "accept"}, request_only=True),
        OpenApiExample(name='out-for-delivery', value={"action": "out-for-delivery"}, request_only=True),
        OpenApiExample(name='cancel', value={"action": "cancel", "reason": "고객 요청 취소"}, request_only=True),
    ]
)
class OrderActionAPIView(APIView):
    def post(self, request, pk: int):
        order = get_object_or_404(Order, pk=pk)
        action = str(request.data.get("action", "")).strip().lower()
        reason = request.data.get("reason") or None
        staff_id = getattr(getattr(request, "user", None), "id", None)
        try:
            if action == "accept":
                order.accept(staff_id)
            elif action in ("mark-ready", "ready"):
                order.mark_ready(staff_id)
            elif action in ("out-for-delivery", "dispatch", "out"):
                order.out_for_delivery(staff_id)
            elif action in ("deliver", "delivered"):
                order.deliver(staff_id)
            elif action == "cancel":
                order.cancel(staff_id, reason=reason)
            else:
                return Response({"detail": "Unsupported action"}, status=400)
        except Exception as e:
            return Response({"detail": str(e)}, status=409)
        return Response(OrderOutSerializer(order).data, status=200)
    

# ---------- 주문 수정(PATCH: pending에서만, 헤더 부분갱신 + 라인 전체교체) ----------
@extend_schema(
    methods=['PATCH'],
    tags=['Orders'],
    summary='주문 수정(대기 상태에서만)',
    description=(
        "주문이 `pending`일 때만 수정 가능.\n\n"
        "- 헤더(배송/결제/메타)는 **넘겨준 필드만 부분 갱신**\n"
        "- 라인(디너/아이템)은 본문에 `orders`(또는 레거시 `dinner`/`dinners`)가 오면 "
        "**기존 라인을 전부 삭제하고 payload로 재빌드(전체 교체)**\n"
        "- 라인이 안 오면 라인은 그대로 두고 헤더/쿠폰만 갱신"
    ),
    request=inline_serializer(
        name='OrderPatchReq',
        fields={
            # 공통 헤더 필드(옵셔널)
            'receiver_name': serializers.CharField(required=False, allow_blank=True, allow_null=True),
            'receiver_phone': serializers.CharField(required=False, allow_blank=True, allow_null=True),
            'delivery_address': serializers.CharField(required=False, allow_blank=True, allow_null=True),
            'geo_lat': serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True),
            'geo_lng': serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True),
            'place_label': serializers.CharField(required=False, allow_blank=True, allow_null=True),
            'address_meta': serializers.JSONField(required=False, allow_null=True),
            'payment_token': serializers.CharField(required=False, allow_blank=True, allow_null=True),
            'card_last4': serializers.CharField(required=False, allow_blank=True, allow_null=True),
            'meta': serializers.JSONField(required=False, allow_null=True),
            'coupons': serializers.ListField(
                child=inline_serializer(
                    name='CouponCode',
                    fields={'code': serializers.CharField()}
                ), required=False
            ),
            # 라인 교체용(셋 중 하나만 오면 됨)
            'orders': serializers.ListField(child=inline_serializer(
                name='OrderPack',
                fields={
                    'dinner': OrderDinnerSelectionSerializer(),
                    'items': serializers.ListField(child=OrderItemSelectionSerializer(), required=False)
                }
            ), required=False),
            'dinners': serializers.ListField(child=OrderDinnerSelectionSerializer(), required=False),
            'dinner': OrderDinnerSelectionSerializer(required=False),
            'items': serializers.ListField(child=OrderItemSelectionSerializer(), required=False),
        }
    ),
    responses={
        200: OrderOutSerializer,
        400: OpenApiResponse(description='유효하지 않은 입력'),
        409: OpenApiResponse(description='pending이 아님 / 전이 불가'),
    },
    examples=[
        OpenApiExample(
            name='헤더만 부분 갱신',
            value={
                "receiver_phone": "010-3333-4444",
                "delivery_address": "서울 성동구 왕십리로 00",
                "meta": {"note": "벨 누르지 마세요"}
            },
            request_only=True
        ),
        OpenApiExample(
            name='라인 전체 교체(orders 사용, 다중 디너)',
            value={
                "orders": [
                    {
                        "dinner": {
                            "code": "valentine", "quantity": "1", "style": "simple",
                            "default_overrides": [{"code": "wine", "qty": "0"}]
                        },
                        "items": [{"code": "wine", "qty": "3"}]
                    },
                    {
                        "dinner": {"code": "champagne_feast", "quantity": "1", "style": "grand"},
                        "items": [{"code": "caviar", "qty": "1"}]
                    }
                ],
                "coupons": [{"code": "WELCOME10"}]
            },
            request_only=True
        ),
    ]
)
class OrderUpdateAPIView(APIView):
    """
    PATCH /api/orders/{id}
    - pending에서만 허용
    - 헤더: 부분 갱신
    - 라인: orders/dinners/dinner가 오면 기존 라인 삭제 후 재빌드
    """
    HEADER_FIELDS = [
        "receiver_name","receiver_phone","delivery_address",
        "geo_lat","geo_lng","place_label","address_meta",
        "payment_token","card_last4","meta",
    ]

    def _extract_coupon_codes(self, order: Order, body: dict) -> list[str]:
        """body에 coupons가 오면 그것을, 없으면 기존 meta.discounts에서 coupon 코드 재추출"""
        if "coupons" in body:
            return [c["code"] for c in (body.get("coupons") or [])]
        # 기존 메타에서 유지
        prev = (order.meta or {}).get("discounts") or []
        return [d.get("code") for d in prev if isinstance(d, dict) and d.get("type") == "coupon" and d.get("code")]

    def _rebuild_lines(self, order: Order, packs: list[dict]) -> tuple[int, list[int], dict]:
        """
        기존 디너/아이템/옵션 스냅샷 전부 삭제하고 packs로 재구성.
        returns: (subtotal_cents, dinner_option_ids, rep_dinner_dict)
        """
        # 모두 삭제(CASCADE 신뢰)
        order.dinners.all().delete()

        subtotal = 0
        dinner_option_ids: list[int] = []

        for idx, pack in enumerate(packs):
            dsel = pack["dinner"]
            dinner = DinnerType.objects.filter(code=dsel["code"], active=True).first()
            if not dinner:
                raise ValueError(f"Invalid dinner.code: {dsel['code']}")
            style = ServingStyle.objects.filter(code=dsel["style"]).first()
            if not style:
                raise ValueError(f"Invalid dinner.style: {dsel['style']}")

            validate_style_allowed(dinner, style)

            unit_cents, style_adjust_cents = apply_style_to_base(dinner, style)
            qty = Decimal(dsel.get("quantity") or "1")

            dinner_opts = resolve_dinner_options_for_dinner(dinner, dsel.get("dinner_options") or [])

            opt_deltas: list[int] = []
            for dop in dinner_opts:
                if (getattr(dop.group, "price_mode", None) or "addon") == "addon":
                    delta = int(getattr(dop, "price_delta_cents", 0) or 0)
                else:
                    m = Decimal(getattr(dop, "multiplier", None) or "1")
                    delta = as_cents_int(Decimal(unit_cents) * (m - Decimal("1")))
                unit_cents += delta
                opt_deltas.append(delta)
                dinner_option_ids.append(dop.pk)

            dinner_subtotal = as_cents_int(Decimal(unit_cents) * qty)
            subtotal += dinner_subtotal

            od = OrderDinner.objects.create(
                order=order, dinner_type=dinner, style=style,
                person_label=None, quantity=qty,
                base_price_cents=dinner.base_price_cents,
                style_adjust_cents=style_adjust_cents, notes=None
            )

            # 옵션 스냅샷
            for dop, delta in zip(dinner_opts, opt_deltas):
                OrderDinnerOption.objects.create(
                    order_dinner=od,
                    option_group_name=dop.group.name,
                    option_name=(dop.item.name if getattr(dop, "item_id", None) else dop.name),
                    price_delta_cents=int(delta),
                    multiplier=None
                )

            # 기본 포함 아이템 스냅샷
            defaults = (DinnerTypeDefaultItem.objects
                        .filter(dinner_type=dinner)
                        .select_related("item")
                        .order_by("item__name"))
            created_default_map = {}
            for di in defaults:
                unit = 0 if getattr(di, "included_in_base", False) else di.item.base_price_cents
                odi = OrderDinnerItem.objects.create(
                    order_dinner=od, item=di.item,
                    final_qty=di.default_qty,
                    unit_price_cents=unit,
                    is_default=True, change_type="unchanged"
                )
                created_default_map[di.item.code] = (odi, Decimal(di.default_qty))

            # default_overrides 적용
            for ov in (dsel.get("default_overrides") or []):
                code = str(ov["code"]).strip()
                qty_override = Decimal(str(ov["qty"]))
                if code not in created_default_map:
                    raise ValueError(f"Invalid default_overrides.code: {code}")
                odi, orig = created_default_map[code]
                if qty_override < 0 or qty_override > orig:
                    raise ValueError(f"default_overrides.qty must be between 0 and {orig} for code={code}")
                odi.final_qty = qty_override
                odi.change_type = "removed" if qty_override == 0 else ("decreased" if qty_override < orig else "unchanged")
                odi.save(update_fields=["final_qty", "change_type"])

            # 디너 전용 items
            for it in (pack.get("items") or []):
                item = MenuItem.objects.filter(code=it["code"], active=True).first()
                if not item:
                    raise ValueError(f"Invalid item.code: {it['code']}")
                sel_opts = validate_item_options_for_item(item, it.get("options") or [])
                unit_item_cents, snaps = calc_item_unit_cents(item, sel_opts)

                qty_item = Decimal(it["qty"])
                line_sub = as_cents_int(Decimal(unit_item_cents) * qty_item)
                subtotal += line_sub

                odi, created = OrderDinnerItem.objects.get_or_create(
                    order_dinner=od, item=item,
                    defaults={
                        "final_qty": qty_item,
                        "unit_price_cents": unit_item_cents,
                        "is_default": False, "change_type": "added"
                    }
                )
                if not created:
                    odi.final_qty = Decimal(odi.final_qty) + qty_item
                    if odi.is_default and odi.change_type == "unchanged":
                        odi.change_type = "added"
                    odi.save(update_fields=["final_qty", "change_type"])

                for sopt in snaps:
                    OrderItemOption.objects.create(
                        order_dinner_item=odi,
                        option_group_name=sopt["option_group_name"],
                        option_name=sopt["option_name"],
                        price_delta_cents=sopt["price_delta_cents"],
                        multiplier=None
                    )

        # 대표 디너(쿠폰 평가용 컨텍스트): 첫 번째
        rep = packs[0]["dinner"] if packs else {}
        return int(subtotal), dinner_option_ids, rep

    @transaction.atomic
    def patch(self, request, pk: int):
        order = get_object_or_404(Order, pk=pk)
        if order.status != "pending":
            return Response({"detail": "Only PENDING orders can be edited."}, status=409)

        body = request.data or {}

        # 1) 헤더 부분 갱신
        header_updates = {k: (body.get(k) or None) for k in self.HEADER_FIELDS if k in body}
        if header_updates:
            for k, v in header_updates.items():
                setattr(order, k, v)

        # 2) 라인 교체 여부 판단 및 재빌드
        packs = None
        subtotal = None
        dinner_option_ids = []
        rep = {}

        if any(k in body for k in ("orders", "dinners", "dinner")):
            try:
                packs = _normalize_payloads(body)  # 이미 파일 상단에 정의된 정규화 함수 재사용
            except serializers.ValidationError as e:
                return Response(e.detail, status=400)
            try:
                subtotal, dinner_option_ids, rep = self._rebuild_lines(order, packs)
            except ValueError as e:
                return Response({"detail": str(e)}, status=400)
        else:
            # 라인 유지 → 현재 subtotal 유지
            subtotal = int(order.subtotal_cents or 0)
            # rep 컨텍스트가 필요하면 기존 첫 디너 기준으로 보정
            first = order.dinners.select_related("dinner_type", "style").first()
            if first:
                rep = {"code": first.dinner_type.code, "style": first.style.code}

        # 3) 할인 재평가(쿠폰: 온 경우 그대로, 없으면 기존 메타에서 보존)
        coupon_codes = self._extract_coupon_codes(order, body)

        discounts, total_disc, total_after = evaluate_discounts(
            subtotal_cents=subtotal,
            customer_id=getattr(order.customer, "id", None),
            channel=order.order_source or "GUI",
            dinner_code=rep.get("code"),
            item_lines=[],  # 필요 시 라인 요약을 전달하도록 확장 가능
            style_code=rep.get("style"),
            dinner_option_ids=dinner_option_ids,
            coupon_codes=coupon_codes,
        )

        order.subtotal_cents = int(subtotal)
        order.discount_cents = int(total_disc)
        order.total_cents = int(total_after)

        # meta 병합: 넘어온 meta 우선 적용 + discounts 덮어쓰기
        new_meta = body.get("meta") or {}
        if discounts:
            new_meta = {**(order.meta or {}), **new_meta, "discounts": discounts}
        elif new_meta:
            new_meta = {**(order.meta or {}), **new_meta}
        else:
            # 아무것도 안 왔고 discounts도 없으면 기존 meta 유지
            new_meta = order.meta
        order.meta = new_meta or None

        order.save(update_fields=[
            *header_updates.keys(),
            "subtotal_cents", "discount_cents", "total_cents", "meta"
        ])

        # 쿠폰 사용량 확정(간결성을 위해 항상 재확정)
        redeem_discounts(
            order=order,
            customer_id=getattr(order.customer, "id", None),
            channel=order.order_source or "GUI",
            discounts=discounts,
        )

        return Response(OrderOutSerializer(order).data, status=200)
