from __future__ import annotations
from typing import List, Dict

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import generics

from .conf import ADDONS_RECO_MAX
from .models import (
    MenuCategory, ItemTag,
    MenuItem, ItemOptionGroup, ItemOption, ItemAvailability,
    ServingStyle, DinnerType, DinnerTypeDefaultItem,
    DinnerOptionGroup, DinnerOption,
)
from .serializers import (
    # 부트스트랩/공용
    MenuCategoryTreeSerializer, MenuCategorySerializer, ItemTagSerializer,
    # 상세/디너
    ItemDetailResponseSerializer,
    DinnerTypeSerializer, DinnerTypeDefaultItemSerializer,
    ServingStyleSerializer, DinnerOptionGroupSerializer, DinnerFullSerializer,
    CatalogBootstrapSerializer,
    # Add-ons
    AddonCardItemSerializer, AddonsPageResponseSerializer,
)
from .selectors import addons_candidates_qs


# 1) 부트스트랩
class CatalogBootstrapAPIView(APIView):
    def get(self, request):
        roots = (MenuCategory.objects
                 .filter(active=True, parent__isnull=True)
                 .order_by("rank", "category_id"))
        tags = ItemTag.objects.all().order_by("name")[:100]
        dinners = DinnerType.objects.filter(active=True).order_by("name")

        payload = {
            "categories": roots,
            "tags": tags,
            "dinners": dinners,
        }
        return Response(CatalogBootstrapSerializer(payload).data)

# 2) 추가메뉴 페이지 (카드 포맷, 클릭 시 #5 상세 호출)
# GET /api/catalog/menu/addons/<dinner_code>
class AddonsListPageAPIView(APIView):
    def get(self, request, dinner_code: str):
        dinner = get_object_or_404(DinnerType, code=dinner_code, active=True)
        items = list(addons_candidates_qs(dinner))
        addons_cat = MenuCategory.objects.filter(slug="addons", active=True).first()
        data = {
            "category": MenuCategorySerializer(addons_cat).data if addons_cat else {"slug": "addons", "name": "Add-ons"},
            "items": AddonCardItemSerializer(items, many=True).data,
            "meta": {"count": len(items)},
        }
        # instance 기반 직렬화(검증 없이 표현만) → dict도 안전 처리
        return Response(AddonsPageResponseSerializer(data).data)

# 3) 추천 카드 (장바구니 직전, 최대 6개)
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

# 5) 아이템 단건(+선택 확장) - 모달용
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

# 6) 디너 풀 패키지
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
            "dinner": dinner,
            "default_items": list(defaults),
            "allowed_styles": list(styles),
            "option_groups": list(opt_groups),
        }
        return Response(DinnerFullSerializer(payload).data)
