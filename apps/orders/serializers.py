# apps/orders/serializers.py
from __future__ import annotations
from decimal import Decimal
from rest_framework import serializers

from .models import (
    Order, OrderDinner, OrderDinnerItem,
    OrderItemOption, OrderDinnerOption,
)

# ---------- 옵션/라인 스냅샷 (응답) ----------
class OrderItemOptionOutSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItemOption
        fields = ("id", "option_group_name", "option_name", "price_delta_cents")


class OrderDinnerOptionOutSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderDinnerOption
        fields = ("id", "option_group_name", "option_name", "price_delta_cents")


class OrderDinnerItemOutSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    options = OrderItemOptionOutSerializer(many=True, read_only=True)

    class Meta:
        model = OrderDinnerItem
        fields = (
            "id", "item_code", "item_name",
            "final_qty", "unit_price_cents",
            "is_default", "change_type",
            "options",
        )


class OrderDinnerOutSerializer(serializers.ModelSerializer):
    dinner_code = serializers.CharField(source="dinner_type.code", read_only=True)
    dinner_name = serializers.CharField(source="dinner_type.name", read_only=True)
    style_code = serializers.CharField(source="style.code", read_only=True)
    style_name = serializers.CharField(source="style.name", read_only=True)
    items = OrderDinnerItemOutSerializer(many=True, read_only=True)
    options = OrderDinnerOptionOutSerializer(many=True, read_only=True)

    class Meta:
        model = OrderDinner
        fields = (
            "id",
            "dinner_code", "dinner_name",
            "style_code", "style_name",
            "person_label", "quantity",
            "base_price_cents", "style_adjust_cents",
            "notes",
            "items", "options",
        )


class OrderOutSerializer(serializers.ModelSerializer):
    dinners = OrderDinnerOutSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = (
            "id", "customer_id", "ordered_at", "status", "order_source",
            "receiver_name", "receiver_phone", "delivery_address",
            "geo_lat", "geo_lng", "place_label", "address_meta",
            "payment_token", "card_last4",
            "subtotal_cents", "discount_cents", "total_cents",
            "meta",
            "dinners",
        )


# ---------- 생성/프리뷰 입력 DTO ----------
class OrderItemSelectionSerializer(serializers.Serializer):
    code = serializers.CharField()
    qty = serializers.DecimalField(max_digits=10, decimal_places=2)
    options = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False, allow_empty=True, default=list
    )


class DefaultOverrideInSerializer(serializers.Serializer):
    code = serializers.CharField()
    qty = serializers.DecimalField(max_digits=10, decimal_places=2)


class OrderDinnerSelectionSerializer(serializers.Serializer):
    code = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default="1")
    style = serializers.CharField()
    dinner_options = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False, allow_empty=True, default=list
    )
    default_overrides = DefaultOverrideInSerializer(many=True, required=False, default=list)


class CouponCodeSerializer(serializers.Serializer):
    code = serializers.CharField()


class OrderCreateRequestSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField()
    order_source = serializers.ChoiceField(choices=["GUI", "VOICE"], default="GUI")
    fulfillment_type = serializers.ChoiceField(choices=["DELIVERY", "PICKUP"], default="DELIVERY")

    dinner = OrderDinnerSelectionSerializer(required=True)
    items = serializers.ListField(child=OrderItemSelectionSerializer(), required=False, default=list)

    # 배송/결제/메타 (조건부 필수는 validate에서 강제)
    receiver_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    receiver_phone = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    delivery_address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    geo_lat = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    geo_lng = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    place_label = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    address_meta = serializers.JSONField(required=False, allow_null=True)

    payment_token = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    card_last4 = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    meta = serializers.JSONField(required=False, allow_null=True)

    coupons = CouponCodeSerializer(many=True, required=False, default=list)

    def validate(self, attrs):
        if attrs.get("fulfillment_type") == "DELIVERY":
            for f in ("receiver_name", "receiver_phone", "delivery_address"):
                if not attrs.get(f):
                    raise serializers.ValidationError({f: "required for DELIVERY"})
        return attrs


class PricePreviewRequestSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField(required=False)
    order_source = serializers.ChoiceField(choices=["GUI", "VOICE"], required=False, default="GUI")

    dinner = OrderDinnerSelectionSerializer(required=True)
    items = serializers.ListField(child=OrderItemSelectionSerializer(), required=False, default=list)
    coupons = CouponCodeSerializer(many=True, required=False, default=list)


# ---------- 프리뷰 출력 ----------
class LineOptionOutSerializer(serializers.Serializer):
    option_group_name = serializers.CharField()
    option_name = serializers.CharField()
    price_delta_cents = serializers.IntegerField()


class LineItemOutSerializer(serializers.Serializer):
    item_code = serializers.CharField()
    name = serializers.CharField()
    qty = serializers.DecimalField(max_digits=10, decimal_places=2)
    unit_price_cents = serializers.IntegerField()
    options = LineOptionOutSerializer(many=True)
    subtotal_cents = serializers.IntegerField()


class AdjustmentOutSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["style", "dinner_option", "default_override"])
    label = serializers.CharField()
    mode = serializers.ChoiceField(choices=["addon", "remove", "decrease"])
    value_cents = serializers.IntegerField(allow_null=True, required=False)


class DiscountLineOutSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["membership", "coupon"])
    label = serializers.CharField()
    code = serializers.CharField(required=False, allow_null=True)
    amount_cents = serializers.IntegerField()


class PricePreviewResponseSerializer(serializers.Serializer):
    line_items = LineItemOutSerializer(many=True)
    adjustments = AdjustmentOutSerializer(many=True)
    subtotal_cents = serializers.IntegerField()
    discounts = DiscountLineOutSerializer(many=True)
    discount_cents = serializers.IntegerField()
    total_cents = serializers.IntegerField()
