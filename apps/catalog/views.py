from __future__ import annotations
from typing import List, Dict

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import generics
from rest_framework import serializers

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

# 1) 부트스트랩
@extend_schema(
    tags=["Catalog"],
    summary="카탈로그 부트스트랩",
    description=(
        "첫 진입 시 필요한 카탈로그 정적 데이터(루트 카테고리 트리, 태그 목록, 활성화된 디너 타입)를 한 번에 반환합니다. "
        "`categories`는 활성 루트 카테고리부터 자식들이 재귀적으로 포함됩니다."
    ),
    responses=CatalogBootstrapSerializer,
    examples=[
        OpenApiExample(
            name="응답 예시",
            value={
                "categories": [
                    {"category_id": 1, "name": "Drinks", "slug": "drinks", "rank": 10, "active": True, "children": []},
                    {"category_id": 2, "name": "Mains", "slug": "mains", "rank": 20, "active": True, "children": []},
                    {"category_id": 3, "name": "Bread", "slug": "bread", "rank": 30, "active": True, "children": []},
                    {"category_id": 4, "name": "Coffee & Tea", "slug": "coffee-tea", "rank": 40, "active": True, "children": []},
                    {"category_id": 5, "name": "Add-ons", "slug": "addons", "rank": 90, "active": True, "children": []}
                ],
                "tags": [{"tag_id": 1, "name": "alcohol"}, {"tag_id": 6, "name": "caffeine"}],
                "dinners": [
                    {"dinner_type_id": 1, "code": "valentine", "name": "Valentine Dinner", "description": "특별한 날을 위한 2인 코스", "base_price_cents": 150000, "active": True},
                    {"dinner_type_id": 2, "code": "french", "name": "French Dinner", "description": "클래식 프렌치 코스", "base_price_cents": 160000, "active": True},
                    {"dinner_type_id": 3, "code": "english", "name": "English Dinner", "description": "브리티시 스타일 코스", "base_price_cents": 140000, "active": True},
                    {"dinner_type_id": 4, "code": "champagne_feast", "name": "Champagne Feast", "description": "최고급 디너", "base_price_cents": 180000, "active": True}
                ]
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
                "category": {"category_id": 5, "name": "Add-ons", "slug": "addons", "rank": 90, "active": True, "parent_id": None},
                "items": [
                    {"code": "garlic_bread", "name": "Garlic Bread", "base_price_cents": 4000, "tags": []},
                    {"code": "sparkling_water", "name": "Sparkling Water", "base_price_cents": 3000, "tags": []},
                    {"code": "petit_dessert", "name": "Petit Dessert", "base_price_cents": 5000, "tags": []},
                    {"code": "espresso_shot", "name": "Espresso Shot", "base_price_cents": 2500, "tags": []}
                ],
                "meta": {"count": 4},
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
        category_dict = (
            MenuCategorySerializer(addons_cat).data if addons_cat else
            {"category_id": None, "name": "Add-ons", "slug": "addons", "rank": 90, "active": True, "parent_id": None}
        )
        data = {
            "category": category_dict,
            "items": AddonCardItemSerializer(items, many=True).data,
            "meta": {"count": len(items)},
        }
        # 이미 dict을 조립했으므로 그대로 반환(스키마는 AddonsPageResponseSerializer로 문서화)
        return Response(data)


# 3) 추천 카드 (장바구니 직전, 최대 N개)
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
                "items": [{"code": "garlic_bread", "name": "Garlic Bread", "base_price_cents": 4000, "tags": []}],
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
            name="steak (옵션 그룹 예시: 굽기, 사이즈, 시즈닝, 소스)",
            value={
                "item_id": 3, "code": "steak", "name": "Steak",
                "description": "", "base_price_cents": 30000, "active": True,
                "category": {"category_id": 2, "name": "Mains", "slug": "mains"},
                "option_groups": [
                    {"group_id": 1, "name": "굽기", "select_mode": "single", "min_select": 1, "max_select": 1,
                     "is_required": True, "is_variant": False, "price_mode": "addon", "rank": 1,
                     "options": [
                         {"option_id": 1, "name": "레어", "price_delta_cents": 0, "multiplier": "1.000", "rank": 1},
                         {"option_id": 2, "name": "미디엄", "price_delta_cents": 0, "multiplier": "1.000", "rank": 2},
                         {"option_id": 3, "name": "웰던", "price_delta_cents": 0, "multiplier": "1.000", "rank": 3}
                     ]},
                    {"group_id": 4, "name": "사이즈", "select_mode": "single", "min_select": 1, "max_select": 1,
                     "is_required": True, "is_variant": True, "price_mode": "multiplier", "rank": 2,
                     "options": [
                         {"option_id": 9, "name": "150g", "price_delta_cents": 0, "multiplier": "1.000", "rank": 1},
                         {"option_id": 10, "name": "250g", "price_delta_cents": 0, "multiplier": "1.500", "rank": 2},
                         {"option_id": 11, "name": "350g", "price_delta_cents": 0, "multiplier": "2.000", "rank": 3}
                     ]},
                    {"group_id": 6, "name": "시즈닝", "select_mode": "multi", "min_select": 0, "max_select": 2,
                     "is_required": False, "is_variant": False, "price_mode": "addon", "rank": 3,
                     "options": [
                         {"option_id": 14, "name": "후추 추가", "price_delta_cents": 0, "multiplier": "1.000", "rank": 1},
                         {"option_id": 15, "name": "갈릭 버터", "price_delta_cents": 2000, "multiplier": "1.000", "rank": 2},
                         {"option_id": 16, "name": "트러플 소금", "price_delta_cents": 3000, "multiplier": "1.000", "rank": 3}
                     ]},
                    {"group_id": 7, "name": "소스", "select_mode": "single", "min_select": 0, "max_select": 1,
                     "is_required": False, "is_variant": False, "price_mode": "addon", "rank": 4,
                     "options": [
                         {"option_id": 17, "name": "페퍼 소스", "price_delta_cents": 1000, "multiplier": "1.000", "rank": 1},
                         {"option_id": 18, "name": "머쉬룸 소스", "price_delta_cents": 1000, "multiplier": "1.000", "rank": 2},
                         {"option_id": 19, "name": "레드와인 소스", "price_delta_cents": 1500, "multiplier": "1.000", "rank": 3}
                     ]}
                ]
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
    description=(
        "디너 타입, 기본 포함 아이템, 허용 Serving Style, 옵션 그룹/옵션을 한 번에 제공합니다. "
        "**참고:** Serving Style/Dinner Option의 `price_mode`는 `addon|multiplier` 이며, "
        "실제 가격 계산은 주문 API에서 multiplier를 addon(가산)으로 환산합니다."
    ),
    parameters=[
        OpenApiParameter("dinner_code", str, OpenApiParameter.PATH, description="디너 코드"),
    ],
    responses=DinnerFullSerializer,
    examples=[
        OpenApiExample(
            name="응답 예시(champagne_feast)",
            value={
                "dinner": {"dinner_type_id": 4, "code": "champagne_feast", "name": "Champagne Feast", "description": "최고급 디너", "base_price_cents": 180000, "active": True},
                "default_items": [
                    {"item": {"item_id": 1, "code": "champagne", "name": "Champagne (Bottle)", "description": "", "base_price_cents": 70000, "active": True,
                              "category": {"category_id": 1, "name": "Drinks", "slug": "drinks"}, "option_groups": []},
                     "default_qty": "1", "included_in_base": True, "notes": ""},
                    {"item": {"item_id": 4, "code": "baguette", "name": "Baguette", "description": "", "base_price_cents": 3000, "active": True,
                              "category": {"category_id": 3, "name": "Bread", "slug": "bread"}, "option_groups": []},
                     "default_qty": "4", "included_in_base": True, "notes": ""},
                    {"item": {"item_id": 5, "code": "coffee_pot", "name": "Coffee (Pot)", "description": "", "base_price_cents": 8000, "active": True,
                              "category": {"category_id": 4, "name": "Coffee & Tea", "slug": "coffee-tea"}, "option_groups": []},
                     "default_qty": "1", "included_in_base": True, "notes": ""},
                    {"item": {"item_id": 2, "code": "wine", "name": "Wine (Bottle)", "description": "", "base_price_cents": 50000, "active": True,
                              "category": {"category_id": 1, "name": "Drinks", "slug": "drinks"}, "option_groups": []},
                     "default_qty": "1", "included_in_base": True, "notes": ""},
                    {"item": {"item_id": 3, "code": "steak", "name": "Steak", "description": "", "base_price_cents": 30000, "active": True,
                              "category": {"category_id": 2, "name": "Mains", "slug": "mains"}, "option_groups": []},
                     "default_qty": "1", "included_in_base": True, "notes": ""}
                ],
                "allowed_styles": [
                    {"style_id": 1, "code": "simple", "name": "Simple", "price_mode": "multiplier", "price_value": "1.00", "notes": ""},
                    {"style_id": 2, "code": "grand", "name": "Grand", "price_mode": "multiplier", "price_value": "1.20", "notes": ""},
                    {"style_id": 3, "code": "deluxe", "name": "Deluxe", "price_mode": "multiplier", "price_value": "1.40", "notes": ""}
                ],
                "option_groups": [
                    {"group_id": 5, "name": "Champagne Extras", "select_mode": "multi", "min_select": 0, "max_select": 3, "is_required": False, "price_mode": "addon", "rank": 10,
                     "options": [{"option_id": 8, "item_code": "champagne", "item_name": "Champagne (Bottle)", "name": None, "price_delta_cents": 70000, "multiplier": None, "is_default": False, "rank": 1}]}
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
