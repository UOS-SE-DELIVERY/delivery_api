from rest_framework import serializers
from .models import (
    Order, OrderDinner, OrderDinnerItem,
    OrderItemOption, OrderDinnerOption
)

# ---- 옵션 스냅샷 ----

class OrderItemOptionOutSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItemOption
        fields = ("id", "option_group_name", "option_name", "price_delta_cents", "multiplier")


class OrderDinnerOptionOutSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderDinnerOption
        fields = ("id", "option_group_name", "option_name", "price_delta_cents", "multiplier")

# ---- 디너/아이템 스냅샷 ----

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

# ---- 주문 응답 ----

class OrderOutSerializer(serializers.ModelSerializer):
    dinners = OrderDinnerOutSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = (
            "id", "customer_id", "ordered_at", "status", "order_source",
            # 배송 스냅샷
            "receiver_name", "receiver_phone", "delivery_address",
            "geo_lat", "geo_lng", "place_label", "address_meta",
            # 결제 스냅샷
            "payment_token", "card_last4",
            # 합계
            "subtotal_cents", "discount_cents", "total_cents",
            # 기타
            "meta",
            # 하위 스냅샷
            "dinners",
        )

# ---- 주문 생성 요청 DTO ----

class OrderItemSelectionSerializer(serializers.Serializer):
    code = serializers.CharField()
    qty = serializers.DecimalField(max_digits=10, decimal_places=2)
    options = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False, allow_empty=True, default=list
    )

class OrderDinnerSelectionSerializer(serializers.Serializer):
    code = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default="1")
    style = serializers.CharField()  # models 상 필수이므로 required
    dinner_options = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False, allow_empty=True, default=list
    )

class OrderCreateRequestSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField()
    order_source = serializers.ChoiceField(choices=["GUI", "VOICE"], default="GUI")

    dinner = OrderDinnerSelectionSerializer(required=True)  # 모델 구조상 최소 1 디너가 있어야 아이템을 담을 수 있음
    items = serializers.ListField(child=OrderItemSelectionSerializer(), required=False, default=list)

    # 배송/결제/메타
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
