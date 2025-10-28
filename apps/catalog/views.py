# apps/catalog/views.py
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

# ==== drf-spectacular ====
from drf_spectacular.utils import (
    extend_schema, OpenApiParameter, OpenApiExample, OpenApiResponse, inline_serializer
)
from rest_framework import serializers


# 1) 부트스트랩
@extend_schema(
    tags=["Catalog"],
    summary="카탈로그 부트스트랩",
    description=(
        "첫 진입 시 필요한 카탈로그 정적 데이터(루트 카테고리 트리, 태그 목록, 활성화된 디너 타입)를 한 번에 내려줍니다. "
        "`categories`는 활성 루트 카테고리부터 자식들이 재귀적으로 포함됩니다."
    ),
    responses=CatalogBootstrapSerializer,
    examples=[
        OpenApiExample(
            name="응답 예시",
            value={
                "categories": [
                    {
                        "category_id": 1, "name": "Sets", "slug": "sets", "rank": 1, "active": True,
                        "children": [
                            {"category_id": 2, "name": "Family Set", "slug": "family", "rank": 1, "active": True, "children": []}
                        ]
                    }
                ],
                "tags": [{"tag_id": 1, "name": "Spicy"}, {"tag_id": 2, "name": "Vegan"}],
                "dinners": [{"dinner_type_id": 10, "code": "valentine", "name": "Valentine", "description": "Romantic", "base_price_cents": 45000, "active": True}]
            },
            response_only=True,
        )
    ],
)
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
@extend_schema(
    tags=["Catalog/Addons"],
    summary="Add-ons 카드 리스트(페이지용)",
    parameters=[
        OpenApiParameter("dinner_code", str, OpenApiParameter.PATH, description="대상 디너 코드"),
    ],
    responses=AddonsPageResponseSerializer,
    examples=[
        OpenApiExample(
            name="응답 예시",
            value={
                "category": {"category_id": 7, "name": "Add-ons", "slug": "addons", "rank": 99, "active": True, "parent_id": None},
                "items": [
                    {"code": "KIMCHI", "name": "김치", "base_price_cents": 2000, "tags": [{"tag_id": 2, "name": "Vegan"}]},
                    {"code": "COKE", "name": "코카콜라", "base_price_cents": 1500, "tags": []},
                ],
                "meta": {"count": 2},
            },
            response_only=True,
        )
    ],
)
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
        return Response(AddonsPageResponseSerializer(data).data)


# 3) 추천 카드 (장바구니 직전, 최대 6개)
# GET /api/catalog/addons/<dinner_code>
@extend_schema(
    tags=["Catalog/Addons"],
    summary="Add-ons 추천(최대 N개)",
    parameters=[
        OpenApiParameter("dinner_code", str, OpenApiParameter.PATH, description="대상 디너 코드"),
    ],
    responses=inline_serializer(
        name="AddonsRecommendationsResp",
        fields={
            "items": AddonCardItemSerializer(many=True),
            "meta": inline_serializer(
                name="AddonsRecommendationsMeta",
                fields={"count": serializers.IntegerField(), "source_category": serializers.CharField()}
            ),
        },
    ),
    examples=[
        OpenApiExample(
            name="응답 예시",
            value={
                "items": [
                    {"code": "GARLIC_BREAD", "name": "갈릭브레드", "base_price_cents": 3000, "tags": []}
                ],
                "meta": {"count": 1, "source_category": "addons"},
            },
            response_only=True,
        )
    ],
)
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
@extend_schema(
    tags=["Catalog/Items"],
    summary="메뉴 아이템 단건 상세 (+선택 확장)",
    parameters=[
        OpenApiParameter("item_code", str, OpenApiParameter.PATH, description="아이템 코드"),
        OpenApiParameter(
            name="expand", required=False, type=str, location=OpenApiParameter.QUERY,
            description="쉼표 구분 확장 필드. 허용값: `availability`, `tags` (예: `expand=availability,tags`)"
        ),
    ],
    responses=ItemDetailResponseSerializer,
    examples=[
        OpenApiExample(
            name="기본 응답(확장 없음)",
            value={
                "item_id": 101, "code": "BULGOGI_SET", "name": "불고기 세트",
                "description": "메인+사이드 구성", "base_price_cents": 15000, "active": True,
                "category": {"category_id": 1, "name": "Sets", "slug": "sets"},
                "option_groups": [
                    {
                        "group_id": 5, "name": "음료 선택", "select_mode": "single",
                        "min_select": 1, "max_select": 1, "is_required": True, "is_variant": False,
                        "price_mode": "delta", "rank": 1,
                        "options": [
                            {"option_id": 1, "name": "콜라", "price_delta_cents": 0, "multiplier": 1.0, "rank": 1},
                            {"option_id": 2, "name": "사이다", "price_delta_cents": 0, "multiplier": 1.0, "rank": 2}
                        ]
                    }
                ]
            },
            response_only=True,
        ),
        OpenApiExample(
            name="확장 포함(availability, tags)",
            value={
                "item_id": 101, "code": "BULGOGI_SET", "name": "불고기 세트",
                "description": "메인+사이드 구성", "base_price_cents": 15000, "active": True,
                "category": {"category_id": 1, "name": "Sets", "slug": "sets"},
                "option_groups": [],
                "availability": [
                    {"dow": 1, "start_time": "11:00:00", "end_time": "22:00:00", "start_date": None, "end_date": None}
                ],
                "tags": [{"tag_id": 1, "name": "Spicy"}]
            },
            response_only=True,
        ),
    ],
)
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
@extend_schema(
    tags=["Catalog/Dinners"],
    summary="디너 타입 풀 패키지",
    description="디너 타입, 기본 포함 아이템, 허용 Serving Style, 옵션 그룹/옵션을 한 번에 제공합니다.",
    parameters=[
        OpenApiParameter("dinner_code", str, OpenApiParameter.PATH, description="디너 코드"),
    ],
    responses=DinnerFullSerializer,
    examples=[
        OpenApiExample(
            name="응답 예시",
            value={
                "dinner": {"dinner_type_id": 10, "code": "valentine", "name": "Valentine", "description": "Romantic", "base_price_cents": 45000, "active": True},
                "default_items": [
                    {"item": {"item_id": 101, "code": "BULGOGI_SET", "name": "불고기 세트", "description": "메인+사이드", "base_price_cents": 15000, "active": True, "category": {"category_id": 1, "name": "Sets", "slug": "sets"}, "option_groups": []},
                     "default_qty": 1, "included_in_base": True, "notes": None}
                ],
                "allowed_styles": [
                    {"style_id": 1, "code": "simple", "name": "Simple", "price_mode": "delta", "price_value": 0, "notes": None}
                ],
                "option_groups": [
                    {"group_id": 1, "name": "사이드 추가", "select_mode": "multi", "min_select": 0, "max_select": 3, "is_required": False, "price_mode": "delta", "rank": 1,
                     "options": [{"option_id": 11, "item_code": "KIMCHI", "item_name": "김치", "name": "김치 추가", "price_delta_cents": 2000, "multiplier": 1.0, "is_default": False, "rank": 1}]}
                ]
            },
            response_only=True,
        )
    ],
)
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
