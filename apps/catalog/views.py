from __future__ import annotations
from decimal import Decimal
from typing import Dict, List

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from .conf import ADDONS_RECO_MAX
from .models import (
    # 카테고리/태그
    MenuCategory, ItemTag,
    # 아이템/옵션/가용시간
    MenuItem, ItemOptionGroup, ItemOption, ItemAvailability,
    # 디너/스타일
    ServingStyle, DinnerType, DinnerTypeDefaultItem, DinnerStyleAllowed,
    DinnerOptionGroup, DinnerOption,
)
from .serializers import (
    # 공용/합본
    MenuCategoryTreeSerializer, MenuCategorySerializer,
    ItemTagSerializer,
    MenuItemDetailWithExpandSerializer, MenuItemDetailSerializer,
    DinnerTypeSerializer, DinnerTypeDefaultItemSerializer,
    ServingStyleSerializer, DinnerOptionGroupSerializer,
    CatalogBootstrapSerializer, MenuPageResponseSerializer,
    DinnerFullSerializer, ItemDetailResponseSerializer,
    # Add-ons
    AddonCardItemSerializer,
    # 가격 미리보기
    PricePreviewRequestSerializer, PricePreviewResponseSerializer,
    LineItemOutSerializer, LineOptionOutSerializer, AdjustmentOutSerializer,
)
from .selectors import addons_candidates_qs, category_items_qs


# ---------- 1) 부트스트랩 ----------
class CatalogBootstrapAPIView(APIView):
    def get(self, request):
        roots = (MenuCategory.objects
                 .filter(active=True, parent__isnull=True)
                 .order_by("rank", "category_id"))
        tags = ItemTag.objects.all().order_by("name")[:100]
        dinners = DinnerType.objects.filter(active=True).order_by("name")

        payload = {
            "categories": MenuCategoryTreeSerializer(roots, many=True).data,
            "tags": ItemTagSerializer(tags, many=True).data,
            "dinners": DinnerTypeSerializer(dinners, many=True).data,
        }
        return Response(CatalogBootstrapSerializer(payload).data)


# ---------- 2) 추가메뉴 페이지 (고정 라우트) ----------
# GET /api/catalog/menu/addons/<dinner_code>
class AddonsListPageAPIView(APIView):
    def get(self, request, dinner_code: str):
        dinner = get_object_or_404(DinnerType, code=dinner_code, active=True)
        qs = addons_candidates_qs(dinner)
        # 리스트는 option_groups 전체 + tags 포함이므로 그대로 직렬화
        items = list(qs)
        # addons 카테고리 메타
        addons_cat = MenuCategory.objects.filter(slug="addons", active=True).first()
        data = {
            "category": MenuCategorySerializer(addons_cat).data if addons_cat else {"slug": "addons", "name": "Add-ons"},
            "items": MenuItemDetailWithExpandSerializer(items, many=True, context={"expand": {"tags"}}).data,
            "meta": {"count": len(items)},
        }
        return Response(MenuPageResponseSerializer(data).data)


# ---------- 3) 추천 카드 (장바구니 직전) ----------
# GET /api/catalog/addons/<dinner_code>
class AddonsRecommendationsAPIView(APIView):
    def get(self, request, dinner_code: str):
        dinner = get_object_or_404(DinnerType, code=dinner_code, active=True)
        items = list(addons_candidates_qs(dinner)[:ADDONS_RECO_MAX])
        out = {
            "items": AddonCardItemSerializer(items, many=True).data,
            "meta": {"count": len(items), "source_category": "addons"},
        }
        return Response(out)


# ---------- 4) 카테고리 단일 페이지(일반 메뉴) ----------
# GET /api/catalog/menu?category=<slug>[&include=items.tags,items.availability]
class MenuByCategoryAPIView(APIView):
    def get(self, request):
        slug = request.query_params.get("category")
        if not slug:
            return Response({"detail": "category slug is required"}, status=400)

        include = set((request.query_params.get("include") or "").split(","))
        include = {s.strip() for s in include if s.strip()}
        include_tags = ("items.tags" in include) or (not include)
        include_avail = "items.availability" in include

        qs = category_items_qs(slug, include_tags=include_tags, include_availability=include_avail)
        items = list(qs)

        data = {
            "category": MenuCategorySerializer(get_object_or_404(MenuCategory, slug=slug, active=True)).data,
            "items": MenuItemDetailWithExpandSerializer(
                items, many=True,
                context={"expand": {s.split(".", 1)[1] for s in include if s.startswith("items.")}}
            ).data,
            "meta": {"count": len(items)},
        }
        return Response(MenuPageResponseSerializer(data).data)


# ---------- 5) 아이템 단건(+선택 확장) ----------
# GET /api/catalog/items/<item_code>?expand=availability,tags
class ItemDetailWithExpandAPIView(generics.RetrieveAPIView):
    lookup_field = "code"
    lookup_url_kwarg = "item_code"
    serializer_class = ItemDetailResponseSerializer

    def get_queryset(self):
        qs = (MenuItem.objects
              .prefetch_related(
                  Prefetch("option_groups", queryset=ItemOptionGroup.objects.order_by("rank", "group_id")
                           .prefetch_related(Prefetch("options", queryset=ItemOption.objects.order_by("rank", "option_id"))))
              )
              .select_related("category"))
        expand = set((self.request.query_params.get("expand") or "").split(","))
        expand = {s.strip() for s in expand if s.strip()}
        if "availability" in expand:
            qs = qs.prefetch_related(
                Prefetch("itemavailability_set", queryset=ItemAvailability.objects.order_by("dow", "start_time"))
            )
        if "tags" in expand:
            qs = qs.prefetch_related("tags")
        return qs

    def get_serializer_context(self):
        expand = set((self.request.query_params.get("expand") or "").split(","))
        expand = {s.strip() for s in expand if s.strip()}
        return {"request": self.request, "expand": expand}


# ---------- 6) 디너 풀 패키지 ----------
# GET /api/catalog/dinners/<dinner_code>
class DinnerFullAPIView(generics.RetrieveAPIView):
    lookup_field = "code"
    lookup_url_kwarg = "dinner_code"

    def get_queryset(self):
        return DinnerType.objects.filter(active=True)

    def retrieve(self, request, *args, **kwargs):
        dinner: DinnerType = self.get_object()

        defaults = (DinnerTypeDefaultItem.objects
                    .filter(dinner_type=dinner)
                    .select_related("item", "item__category")
                    .order_by("item__name"))

        styles = (ServingStyle.objects
                  .filter(dinnerstyleallowed__dinner_type=dinner)
                  .order_by("name"))

        opt_groups = (DinnerOptionGroup.objects
                      .filter(dinner_type=dinner)
                      .prefetch_related(Prefetch("options", queryset=DinnerOption.objects.order_by("rank", "option_id")))
                      .order_by("rank", "name"))

        payload = {
            "dinner": DinnerTypeSerializer(dinner).data,
            "default_items": DinnerTypeDefaultItemSerializer(defaults, many=True).data,
            "allowed_styles": ServingStyleSerializer(styles, many=True).data,
            "option_groups": DinnerOptionGroupSerializer(opt_groups, many=True).data,
        }
        return Response(DinnerFullSerializer(payload).data)


# ---------- 7) 가격 미리보기 ----------
# POST /api/catalog/price/preview
class PricePreviewAPIView(APIView):
    def post(self, request):
        req = PricePreviewRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        data = req.validated_data

        running_total = 0
        line_items: List[Dict] = []
        adjustments: List[Dict] = []

        # ----- 아이템 합계 (옵션 price_mode 반영)
        for li in data.get("items", []):
            item = get_object_or_404(MenuItem, code=li["item_code"], active=True)
            qty: Decimal = li["qty"]
            unit_cents = int(item.base_price_cents)

            opt_out = []
            if li.get("option_ids"):
                opt_qs = (ItemOption.objects
                          .select_related("group")
                          .filter(option_id__in=li["option_ids"])
                          .order_by("rank", "option_id"))
                # 그룹 price_mode에 따라 가산/곱셈
                for o in opt_qs:
                    if o.group.price_mode == "addon":
                        unit_cents += int(o.price_delta_cents or 0)
                    else:
                        mult = Decimal(o.multiplier or "1.0")
                        unit_cents = int(Decimal(unit_cents) * mult)

                    opt_out.append(LineOptionOutSerializer({
                        "option_id": o.option_id,
                        "name": o.name,
                        "price_delta_cents": int(o.price_delta_cents or 0),
                    }).data)

            subtotal = int(Decimal(unit_cents) * qty)
            line_items.append(LineItemOutSerializer({
                "item_code": item.code,
                "name": item.name,
                "qty": qty,
                "unit_price_cents": unit_cents,
                "options": opt_out,
                "subtotal_cents": subtotal,
            }).data)
            running_total += subtotal

        # ----- 디너 조정(스타일/옵션)
        dinner_sel = data.get("dinner") or {}
        if dinner_sel.get("serving_style_code"):
            style = get_object_or_404(ServingStyle, code=dinner_sel["serving_style_code"])
            if (style.price_mode or "addon") == "addon":
                val_cents = int(style.price_value or 0)
                running_total += val_cents
                adjustments.append(AdjustmentOutSerializer({
                    "type": "style", "label": style.name,
                    "mode": "addon", "value_cents": val_cents, "multiplier": None,
                }).data)
            else:
                mult = Decimal(style.price_value or "1.0")
                running_total = int(Decimal(running_total) * mult)
                adjustments.append(AdjustmentOutSerializer({
                    "type": "style", "label": style.name,
                    "mode": "multiplier", "value_cents": None, "multiplier": mult,
                }).data)

        if dinner_sel.get("dinner_option_ids"):
            opt_qs = (DinnerOption.objects
                      .select_related("group", "item")
                      .filter(option_id__in=dinner_sel["dinner_option_ids"])
                      .order_by("rank", "option_id"))
            for opt in opt_qs:
                label = opt.name or (opt.item.name if opt.item_id else "Option")
                if (opt.group.price_mode or "addon") == "addon":
                    val_cents = int(opt.price_delta_cents or 0)
                    running_total += val_cents
                    adjustments.append(AdjustmentOutSerializer({
                        "type": "dinner_option", "label": label,
                        "mode": "addon", "value_cents": val_cents, "multiplier": None,
                    }).data)
                else:
                    mult = Decimal(opt.multiplier or "1.0")
                    running_total = int(Decimal(running_total) * mult)
                    adjustments.append(AdjustmentOutSerializer({
                        "type": "dinner_option", "label": label,
                        "mode": "multiplier", "value_cents": None, "multiplier": mult,
                    }).data)

        out = {
            "line_items": line_items,
            "adjustments": adjustments,
            "subtotal_cents": running_total,   # 할인 전
            "discount_cents": 0,               # 쿠폰/멤버십은 주문 도메인에서
            "total_cents": running_total,
        }
        return Response(PricePreviewResponseSerializer(out).data)
