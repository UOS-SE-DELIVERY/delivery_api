from decimal import Decimal
from django.core.validators import MinValueValidator
from rest_framework import serializers

from .models import (
    MenuCategory, ItemTag, MenuItem, ItemOptionGroup, ItemOption,
    ServingStyle, DinnerType, DinnerTypeDefaultItem, DinnerOptionGroup,
    DinnerOption, ItemAvailability
)

# ---------- Category ----------

class MenuCategorySerializer(serializers.ModelSerializer):
    parent_id = serializers.IntegerField(source="parent_id", read_only=True, allow_null=True)

    class Meta:
        model = MenuCategory
        fields = ("category_id", "parent_id", "name", "slug", "rank", "active")


class MenuCategoryTreeSerializer(MenuCategorySerializer):
    children = serializers.SerializerMethodField()

    def get_children(self, obj):
        # related_name="children"
        qs = obj.children.filter(active=True).order_by("rank", "category_id")
        return MenuCategoryTreeSerializer(qs, many=True, context=self.context).data


# ---------- Tags ----------

class ItemTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemTag
        fields = ("tag_id", "name")


# ---------- Item / Options ----------

class CategoryRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = MenuCategory
        fields = ("category_id", "name", "slug")


class MenuItemSummarySerializer(serializers.ModelSerializer):
    category = CategoryRefSerializer(read_only=True)

    class Meta:
        model = MenuItem
        fields = (
            "item_id", "code", "name", "description", "category",
            "unit", "base_price_cents", "active", "attrs",
        )


class ItemOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemOption
        fields = ("option_id", "name", "price_delta_cents", "multiplier", "is_default", "rank")


class ItemOptionGroupSerializer(serializers.ModelSerializer):
    # ItemOption.group.related_name="options"
    options = ItemOptionSerializer(many=True, read_only=True)

    class Meta:
        model = ItemOptionGroup
        fields = (
            "group_id", "name", "select_mode", "min_select", "max_select",
            "is_required", "is_variant", "price_mode", "rank", "options",
        )


class MenuItemDetailSerializer(serializers.Serializer):
    item = MenuItemSummarySerializer()
    option_groups = ItemOptionGroupSerializer(many=True)


# ---------- Serving Style / Dinner ----------

class ServingStyleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServingStyle
        fields = ("style_id", "code", "name", "price_mode", "price_value", "notes")


class DinnerTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DinnerType
        fields = ("dinner_type_id", "code", "name", "description", "base_price_cents", "active")


class DinnerTypeDefaultItemSerializer(serializers.ModelSerializer):
    item = MenuItemSummarySerializer(read_only=True)

    class Meta:
        model = DinnerTypeDefaultItem
        fields = ("item", "default_qty", "included_in_base", "notes")


class DinnerOptionSerializer(serializers.ModelSerializer):
    # 옵션 자체에는 price_mode 필드가 없고, group에 존재
    item_code = serializers.CharField(source="item.code", read_only=True, allow_null=True)
    item_name = serializers.CharField(source="item.name", read_only=True, allow_null=True)

    class Meta:
        model = DinnerOption
        fields = ("option_id", "item_code", "item_name", "name", "price_delta_cents", "multiplier", "is_default", "rank")


class DinnerOptionGroupSerializer(serializers.ModelSerializer):
    # DinnerOption.group.related_name="options"
    options = DinnerOptionSerializer(many=True, read_only=True)

    class Meta:
        model = DinnerOptionGroup
        fields = (
            "group_id", "name", "select_mode", "min_select", "max_select",
            "is_required", "price_mode", "rank", "options",
        )


# ---------- Availability ----------

class ItemAvailabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemAvailability
        fields = ("dow", "start_time", "end_time", "start_date", "end_date")


# ---------- Price preview (request / response DTOs) ----------

class PreviewItemSelectionSerializer(serializers.Serializer):
    code = serializers.CharField()
    qty = serializers.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    options = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, allow_empty=True, default=list
    )


class PreviewDinnerSerializer(serializers.Serializer):
    code = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default=Decimal("1"))
    style = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class PricePreviewRequestSerializer(serializers.Serializer):
    dinner = PreviewDinnerSerializer(required=False)
    items = serializers.ListField(child=PreviewItemSelectionSerializer(), required=False, default=list)


class LineOptionOutSerializer(serializers.Serializer):
    group_name = serializers.CharField()
    option_name = serializers.CharField()
    mode = serializers.ChoiceField(choices=["addon", "multiplier"])
    value = serializers.DecimalField(max_digits=12, decimal_places=3)  # addon: delta(원), multiplier: 배수


class LineItemOutSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    qty = serializers.DecimalField(max_digits=10, decimal_places=2)
    unit_price_cents = serializers.IntegerField()
    options = LineOptionOutSerializer(many=True)
    subtotal_cents = serializers.IntegerField()


class AdjustmentOutSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["style", "dinner_option", "item_adjust"])
    label = serializers.CharField()
    mode = serializers.ChoiceField(choices=["addon", "multiplier"])
    value_cents = serializers.IntegerField(allow_null=True, required=False)
    multiplier = serializers.DecimalField(max_digits=10, decimal_places=3, required=False, allow_null=True)


class PricePreviewResponseSerializer(serializers.Serializer):
    line_items = LineItemOutSerializer(many=True)
    adjustments = AdjustmentOutSerializer(many=True)
    subtotal_cents = serializers.IntegerField()
    discount_cents = serializers.IntegerField()
    total_cents = serializers.IntegerField()
