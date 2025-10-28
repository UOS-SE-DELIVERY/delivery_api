from rest_framework import serializers
from .models import Staff
from apps.promotion.models import Coupon, Membership
from apps.orders.models import (
    Order, OrderDinner, OrderDinnerItem, OrderItemOption, OrderDinnerOption
)

# ---- Auth ----
class StaffLoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()

# ---- Coupons ----
class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = (
            "code", "name", "label", "active",
            "kind", "value",
            "valid_from", "valid_until",
            "min_subtotal_cents", "max_discount_cents",
            "stackable_with_membership", "stackable_with_coupons",
            "channel",
            "max_redemptions_global", "max_redemptions_per_user",
            "notes",
            "created_at", "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

# ---- Memberships ----
class MembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = Membership
        fields = ("id", "customer", "label", "percent_off", "active", "valid_from", "valid_until")

# ---- Staff /me ----
class StaffMeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Staff
        fields = ("id", "username", "role", "is_active", "created_at")
        read_only_fields = fields

# ===== Orders (Detail for Staff) =====

class OrderItemOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItemOption
        fields = ("id", "option_group_name", "option_name", "price_delta_cents", "multiplier")

class OrderDinnerOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderDinnerOption
        fields = ("id", "option_group_name", "option_name", "price_delta_cents", "multiplier")

class OrderDinnerItemSerializer(serializers.ModelSerializer):
    item = serializers.SerializerMethodField()
    options = OrderItemOptionSerializer(many=True, read_only=True)

    class Meta:
        model = OrderDinnerItem
        fields = (
            "id",
            "item",            # {id, name}
            "final_qty",
            "unit_price_cents",
            "is_default",
            "change_type",
            "options",
        )

    def get_item(self, obj):
        # MenuItem 스냅샷: id + name만 노출(이름 필드가 없을 가능성 대비 getattr)
        return {
            "id": getattr(obj, "item_id", None),
            "name": getattr(getattr(obj, "item", None), "name", None),
        }

class OrderDinnerSerializer(serializers.ModelSerializer):
    dinner_type = serializers.SerializerMethodField()
    style = serializers.SerializerMethodField()
    items = OrderDinnerItemSerializer(many=True, read_only=True)
    options = OrderDinnerOptionSerializer(many=True, read_only=True)

    class Meta:
        model = OrderDinner
        fields = (
            "id",
            "dinner_type",      # {id, name}
            "style",            # {id, name}
            "person_label",
            "quantity",
            "base_price_cents",
            "style_adjust_cents",
            "notes",
            "items",
            "options",
        )

    def get_dinner_type(self, obj):
        dt = getattr(obj, "dinner_type", None)
        return {"id": getattr(dt, "id", None), "name": getattr(dt, "name", None)}

    def get_style(self, obj):
        st = getattr(obj, "style", None)
        return {"id": getattr(st, "id", None), "name": getattr(st, "name", None)}

class StaffOrderDetailSerializer(serializers.ModelSerializer):
    dinners = OrderDinnerSerializer(many=True, read_only=True)
    coupons = serializers.SerializerMethodField()
    membership = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            # 기본
            "id", "customer_id", "ordered_at", "status", "order_source",
            # 배송 스냅샷
            "receiver_name", "receiver_phone", "delivery_address",
            "geo_lat", "geo_lng", "place_label", "address_meta",
            # 결제(토큰은 제외, 마지막 4자리만 노출)
            "card_last4",
            # 합계
            "subtotal_cents", "discount_cents", "total_cents",
            # 메타
            "meta",
            # 구성
            "dinners",
            # 부가
            "coupons", "membership",
        )

    def get_coupons(self, order: Order):
        # reverse name: coupon_redemptions
        redemptions = getattr(order, "coupon_redemptions", None)
        if not redemptions:
            return []
        out = []
        for r in redemptions.all():
            c = getattr(r, "coupon", None)
            out.append({
                "coupon": getattr(c, "code", None),
                "amount_cents": getattr(r, "amount_cents", None),
                "channel": getattr(r, "channel", None),
                "redeemed_at": getattr(r, "redeemed_at", None),
            })
        return out

    def get_membership(self, order: Order):
        cust = getattr(order, "customer", None)
        m = getattr(cust, "membership", None) if cust else None
        if not m:
            return None
        return {
            "customer_id": getattr(cust, "id", None),
            "percent_off": getattr(m, "percent_off", None),
            "active": getattr(m, "active", None),
            "valid_from": getattr(m, "valid_from", None),
            "valid_until": getattr(m, "valid_until", None),
        }

class InventoryItemUpdateSerializer(serializers.Serializer):
    code = serializers.CharField()
    qty = serializers.IntegerField(min_value=0, required=False)
    delta = serializers.IntegerField(required=False)
    active = serializers.BooleanField(required=False)
    reason = serializers.CharField(allow_blank=True, required=False)

class InventoryItemPartialUpdateSerializer(serializers.Serializer):
    qty = serializers.IntegerField(min_value=0, required=False)
    delta = serializers.IntegerField(required=False)
    active = serializers.BooleanField(required=False)
    reason = serializers.CharField(allow_blank=True, required=False)