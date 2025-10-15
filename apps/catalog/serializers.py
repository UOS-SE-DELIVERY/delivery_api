from __future__ import annotations
from typing import Any, Dict, Iterable
from rest_framework import serializers

from .models import (
    MenuCategory, ItemTag,
    MenuItem, ItemOptionGroup, ItemOption, ItemAvailability,
    ServingStyle, DinnerType, DinnerTypeDefaultItem,
    DinnerOptionGroup, DinnerOption,
)

# ---- Category / Tag ----

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

    def get_children(self, obj: MenuCategory):
        qs = obj.children.filter(active=True).order_by("rank", "category_id")
        return MenuCategoryTreeSerializer(qs, many=True).data

class ItemTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemTag
        fields = ("tag_id", "name")

# ---- Item & Options ----

class CategoryRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = MenuCategory
        fields = ("category_id", "name", "slug")

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

class ItemAvailabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemAvailability
        fields = ("dow", "start_time", "end_time", "start_date", "end_date")

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

# ---- Serving Style / Dinner ----

class ServingStyleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServingStyle
        fields = ("style_id", "code", "name", "price_mode", "price_value", "notes")

class DinnerTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DinnerType
        fields = ("dinner_type_id", "code", "name", "description", "base_price_cents", "active")

class DinnerTypeDefaultItemSerializer(serializers.ModelSerializer):
    item = MenuItemDetailSerializer(read_only=True)

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

# ---- 합본 응답 ----

class CatalogBootstrapSerializer(serializers.Serializer):
    categories = MenuCategoryTreeSerializer(many=True)
    tags = ItemTagSerializer(many=True)
    dinners = DinnerTypeSerializer(many=True)

class ItemDetailResponseSerializer(MenuItemDetailWithExpandSerializer):
    pass

class DinnerFullSerializer(serializers.Serializer):
    dinner = DinnerTypeSerializer()
    default_items = DinnerTypeDefaultItemSerializer(many=True)
    allowed_styles = ServingStyleSerializer(many=True)
    option_groups = DinnerOptionGroupSerializer(many=True)

# ---- Add-ons 카드/리스트 (미니멀) ----

class AddonCardItemSerializer(serializers.ModelSerializer):
    tags = ItemTagSerializer(many=True, read_only=True)

    class Meta:
        model = MenuItem
        fields = ("code", "name", "base_price_cents", "tags")

class AddonsPageResponseSerializer(serializers.Serializer):
    category = MenuCategorySerializer()
    items = AddonCardItemSerializer(many=True)
    meta = serializers.DictField(required=False)
