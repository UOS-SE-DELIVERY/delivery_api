from __future__ import annotations
from decimal import Decimal
from typing import Any, Dict, Iterable

from django.core.validators import MinValueValidator
from rest_framework import serializers

from .models import (
    # 카테고리/태그
    MenuCategory, ItemTag,
    # 아이템/옵션/가용시간
    MenuItem, ItemOptionGroup, ItemOption, ItemAvailability,
    # 디너/스타일
    ServingStyle, DinnerType, DinnerTypeDefaultItem,
    DinnerOptionGroup, DinnerOption,
)

# ---------- Category / Tag ----------

class MenuCategorySerializer(serializers.ModelSerializer):
    parent_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = MenuCategory
        fields = ("category_id", "name", "slug", "rank", "active", "parent_id")


class MenuCategoryTreeSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()

    class Meta:
        model = MenuCategory
        fields = ("category_id", "name", "slug", "rank", "active", "children")

    def get_children(self, obj: MenuCategory) -> Iterable[Dict[str, Any]]:
        qs = obj.children.filter(active=True).order_by("rank", "category_id")
        return MenuCategoryTreeSerializer(qs, many=True).data


class ItemTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemTag
        fields = ("tag_id", "name")


# ---------- Item & Options ----------

class CategoryRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = MenuCategory
        fields = ("category_id", "name", "slug")


class ItemOptionLiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemOption
        fields = ("option_id", "name", "price_delta_cents", "rank")


class ItemOptionGroupLiteSerializer(serializers.ModelSerializer):
    options = ItemOptionLiteSerializer(many=True, read_only=True)

    class Meta:
        model = ItemOptionGroup
        fields = ("group_id", "name", "select_mode", "rank", "options")


class ItemOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemOption
        fields = ("option_id", "name", "price_delta_cents", "multiplier", "rank")


class ItemOptionGroupSerializer(serializers.ModelSerializer):
    options = ItemOptionSerializer(many=True, read_only=True)

    class Meta:
        model = ItemOptionGroup
        fields = (
            "group_id", "name", "select_mode", "min_select", "max_select",
            "is_required", "is_variant", "price_mode", "rank", "options"
        )


class MenuItemSummarySerializer(serializers.ModelSerializer):
    category = CategoryRefSerializer(read_only=True)

    class Meta:
        model = MenuItem
        fields = ("item_id", "code", "name", "base_price_cents", "active", "category")


class ItemAvailabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemAvailability
        fields = ("dow", "start_time", "end_time", "start_date", "end_date")


class MenuItemDetailSerializer(serializers.ModelSerializer):
    category = CategoryRefSerializer(read_only=True)
    option_groups = ItemOptionGroupSerializer(many=True, read_only=True)

    class Meta:
        model = MenuItem
        fields = (
            "item_id", "code", "name", "description",
            "base_price_cents", "active",
            "category", "option_groups"
        )


class MenuItemDetailWithExpandSerializer(MenuItemDetailSerializer):
    availability = ItemAvailabilitySerializer(source="itemavailability_set", many=True, required=False)
    tags = ItemTagSerializer(many=True, required=False)

    class Meta(MenuItemDetailSerializer.Meta):
        fields = MenuItemDetailSerializer.Meta.fields + ("availability", "tags")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        expand: set[str] = set(self.context.get("expand", []))
        if "availability" not in expand:
            data.pop("availability", None)
        if "tags" not in expand:
            data.pop("tags", None)
        return data


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
    item_code = serializers.CharField(source="item.code", read_only=True, allow_null=True)
    item_name = serializers.CharField(source="item.name", read_only=True, allow_null=True)

    class Meta:
        model = DinnerOption
        fields = ("option_id", "item_code", "item_name", "name",
                  "price_delta_cents", "multiplier", "is_default", "rank")


class DinnerOptionGroupSerializer(serializers.ModelSerializer):
    options = DinnerOptionSerializer(many=True, read_only=True)

    class Meta:
        model = DinnerOptionGroup
        fields = (
            "group_id", "name", "select_mode", "min_select", "max_select",
            "is_required", "price_mode", "rank", "options"
        )


# ---------- 합본 응답 ----------

class CatalogBootstrapSerializer(serializers.Serializer):
    categories = MenuCategoryTreeSerializer(many=True)
    tags = ItemTagSerializer(many=True)
    dinners = DinnerTypeSerializer(many=True)


class MenuPageResponseSerializer(serializers.Serializer):
    category = MenuCategorySerializer()
    items = MenuItemDetailWithExpandSerializer(many=True)
    meta = serializers.DictField(required=False)


class ItemDetailResponseSerializer(MenuItemDetailWithExpandSerializer):
    pass


class DinnerFullSerializer(serializers.Serializer):
    dinner = DinnerTypeSerializer()
    default_items = DinnerTypeDefaultItemSerializer(many=True)
    allowed_styles = ServingStyleSerializer(many=True)
    option_groups = DinnerOptionGroupSerializer(many=True)


# ---------- Add-ons 카드 ----------

class AddonCardItemSerializer(serializers.ModelSerializer):
    option_groups = ItemOptionGroupLiteSerializer(many=True, read_only=True)
    tags = ItemTagSerializer(many=True, read_only=True)
    quick_add_eligible = serializers.SerializerMethodField()

    class Meta:
        model = MenuItem
        fields = ("code", "name", "base_price_cents", "option_groups", "tags", "quick_add_eligible")

    def get_quick_add_eligible(self, obj: MenuItem) -> bool:
        groups = list(obj.option_groups.all())
        if not groups:
            return True
        if len(groups) == 1:
            g = groups[0]
            # 단일 선택 + 옵션 ≤ 5 → 빠른추가 가능
            return (g.select_mode == "single") and (len(list(g.options.all())) <= 5)
        return False


# ---------- 가격 미리보기 ----------

class PreviewItemSelectionSerializer(serializers.Serializer):
    item_code = serializers.CharField()
    qty = serializers.DecimalField(max_digits=8, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    option_ids = serializers.ListField(child=serializers.IntegerField(), required=False)


class PreviewDinnerSerializer(serializers.Serializer):
    dinner_code = serializers.CharField(required=False, allow_null=True)
    serving_style_code = serializers.CharField(required=False, allow_null=True)
    dinner_option_ids = serializers.ListField(child=serializers.IntegerField(), required=False)


class PricePreviewRequestSerializer(serializers.Serializer):
    items = PreviewItemSelectionSerializer(many=True)
    dinner = PreviewDinnerSerializer(required=False)


class LineOptionOutSerializer(serializers.Serializer):
    option_id = serializers.IntegerField()
    name = serializers.CharField()
    price_delta_cents = serializers.IntegerField()


class LineItemOutSerializer(serializers.Serializer):
    item_code = serializers.CharField()
    name = serializers.CharField()
    qty = serializers.DecimalField(max_digits=8, decimal_places=2)
    unit_price_cents = serializers.IntegerField()
    options = LineOptionOutSerializer(many=True)
    subtotal_cents = serializers.IntegerField()


class AdjustmentOutSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["style", "dinner_option"])
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
