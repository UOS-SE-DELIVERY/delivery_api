from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List

from django.shortcuts import get_object_or_404
from django.db.models import Prefetch, Q
from django.http import Http404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    MenuCategory, MenuItem, ItemTag, ItemOptionGroup, ItemOption,
    ServingStyle, DinnerType, DinnerTypeDefaultItem, DinnerStyleAllowed,
    DinnerOptionGroup, DinnerOption, ItemAvailability
)
from .serializers import (
    MenuCategorySerializer, MenuCategoryTreeSerializer,
    MenuItemSummarySerializer, MenuItemDetailSerializer,
    ItemTagSerializer, ItemAvailabilitySerializer,
    ServingStyleSerializer, DinnerTypeSerializer, DinnerTypeDefaultItemSerializer,
    DinnerOptionGroupSerializer,
    PricePreviewRequestSerializer, PricePreviewResponseSerializer, LineOptionOutSerializer, LineItemOutSerializer,
    AdjustmentOutSerializer, ItemOptionGroupSerializer
)

# ---------- helpers ----------

def as_cents(amount: Decimal) -> int:
    # amount is KRW; keep integer cents(=won)
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ---------- Categories ----------

class CategoryListAPIView(generics.ListAPIView):
    """
    GET /api/catalog/categories?tree=true
    """
    serializer_class = MenuCategorySerializer

    def get_queryset(self):
        return MenuCategory.objects.filter(active=True).order_by("rank", "category_id")

    def list(self, request, *args, **kwargs):
        tree = str(request.query_params.get("tree", "true")).lower() == "true"
        if not tree:
            return super().list(request, *args, **kwargs)
        roots = MenuCategory.objects.filter(active=True, parent__isnull=True).order_by("rank", "category_id")
        return Response(MenuCategoryTreeSerializer(roots, many=True).data)


# ---------- Items ----------

class ItemListAPIView(generics.ListAPIView):
    """
    GET /api/catalog/items?category=slug&tag=name&search=...&active=true
    """
    serializer_class = MenuItemSummarySerializer

    def get_queryset(self):
        qs = (MenuItem.objects
              .select_related("category")
              .order_by("name"))
        params = self.request.query_params

        active = params.get("active")
        if active is not None:
            val = str(active).lower()
            if val in ("true", "1"):
                qs = qs.filter(active=True)
            elif val in ("false", "0"):
                qs = qs.filter(active=False)

        if params.get("category"):
            qs = qs.filter(category__slug=params["category"])

        if params.get("search"):
            s = params["search"].strip()
            qs = qs.filter(Q(name__icontains=s) | Q(description__icontains=s))

        if params.get("tag"):
            # ManyToMany through → tags__name 사용
            qs = qs.filter(tags__name=params["tag"])

        return qs.distinct()


class ItemDetailAPIView(APIView):
    """
    GET /api/catalog/items/{code}
    """
    # permission_classes = [AllowAny]

    def get(self, request, code: str):
        item = get_object_or_404(
            MenuItem.objects
                .select_related("category")
                .prefetch_related("option_groups__options"),
            code=code, active=True
        )
        return Response(MenuItemDetailSerializer(item).data, status=200)


class ItemAvailabilityAPIView(generics.ListAPIView):
    """
    GET /api/catalog/items/{code}/availability
    """
    serializer_class = ItemAvailabilitySerializer

    def get_queryset(self):
        code = self.kwargs["code"]
        return ItemAvailability.objects.filter(item__code=code).order_by("dow", "start_time")


# ---------- Tags ----------

class ItemTagListAPIView(generics.ListAPIView):
    """
    GET /api/catalog/tags
    """
    serializer_class = ItemTagSerializer
    queryset = ItemTag.objects.all().order_by("name")


# ---------- Dinners / styles / options ----------

class DinnerListAPIView(generics.ListAPIView):
    """
    GET /api/catalog/dinners?active=true
    """
    serializer_class = DinnerTypeSerializer

    def get_queryset(self):
        qs = DinnerType.objects.all().order_by("name")
        active = self.request.query_params.get("active")
        if active is not None:
            val = str(active).lower()
            if val in ("true", "1"):
                qs = qs.filter(active=True)
            elif val in ("false", "0"):
                qs = qs.filter(active=False)
        return qs


class DinnerDetailAPIView(generics.RetrieveAPIView):
    """
    GET /api/catalog/dinners/{dinner_code}
    """
    queryset = DinnerType.objects.filter(active=True)
    serializer_class = DinnerTypeSerializer
    lookup_field = "code"
    lookup_url_kwarg = "dinner_code"
    # queryset = DinnerType.objects.all()


class DinnerDefaultItemsAPIView(generics.ListAPIView):
    """
    GET /api/catalog/dinners/{dinner_code}/default-items
    """
    serializer_class = DinnerTypeDefaultItemSerializer

    def get_queryset(self):
        code = self.kwargs["dinner_code"]
        return (DinnerTypeDefaultItem.objects
                .filter(dinner_type__code=code)
                .select_related("item", "item__category")
                .order_by("item__name"))


class DinnerStylesAPIView(generics.ListAPIView):
    """
    GET /api/catalog/dinners/{dinner_code}/styles
    """
    serializer_class = ServingStyleSerializer

    def get_queryset(self):
        code = self.kwargs["dinner_code"]
        return (ServingStyle.objects
                .filter(dinnerstyleallowed__dinner_type__code=code)
                .order_by("name"))


class DinnerOptionGroupsAPIView(generics.ListAPIView):
    """
    GET /api/catalog/dinners/{dinner_code}/option-groups
    """
    serializer_class = DinnerOptionGroupSerializer

    def get_queryset(self):
        code = self.kwargs["dinner_code"]
        return (DinnerOptionGroup.objects
                .filter(dinner_type__code=code)
                .order_by("rank", "name")
                .prefetch_related(Prefetch("options", queryset=DinnerOption.objects.order_by("rank", "option_id"))))


# ---------- Price preview ----------

class PricePreviewAPIView(APIView):
    """
    POST /api/catalog/price/preview
    body:
    {
      "dinner": {"code": "...", "quantity": 2, "style": "grand"},
      "items": [{"code":"...", "qty":2, "options":[11, 21]}]
    }
    """
    def post(self, request):
        in_s = PricePreviewRequestSerializer(data=request.data)
        in_s.is_valid(raise_exception=True)
        payload = in_s.validated_data

        line_items: List[Dict] = []
        adjustments: List[Dict] = []
        running_total = 0

        # ---- Dinner (base + style) ----
        dinner = payload.get("dinner")
        if dinner:
            d = DinnerType.objects.filter(code=dinner["code"], active=True).first()
            if not d:
                return Response({"detail": "Invalid dinner.code"}, status=status.HTTP_400_BAD_REQUEST)

            qty = Decimal(dinner.get("quantity") or "1")
            base = Decimal(d.base_price_cents)

            style_code = (dinner.get("style") or "").strip()
            if style_code:
                st = ServingStyle.objects.filter(code=style_code).first()
                if not st:
                    return Response({"detail": "Invalid dinner.style"}, status=status.HTTP_400_BAD_REQUEST)
                # ensure allowed combination
                allowed = DinnerStyleAllowed.objects.filter(dinner_type=d, style=st).exists()
                if not allowed:
                    return Response({"detail": "Style not allowed for dinner"}, status=status.HTTP_400_BAD_REQUEST)

                if st.price_mode == "addon":
                    adjustments.append(AdjustmentOutSerializer({
                        "type": "style",
                        "label": st.name,
                        "mode": "addon",
                        "value_cents": int(Decimal(st.price_value).quantize(Decimal("1"))),
                    }).data)
                    base = base + Decimal(st.price_value)
                else:
                    adjustments.append(AdjustmentOutSerializer({
                        "type": "style",
                        "label": st.name,
                        "mode": "multiplier",
                        "multiplier": st.price_value,
                    }).data)
                    base = (base * Decimal(st.price_value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

            unit_cents = int(base)
            subtotal = int(Decimal(unit_cents) * qty)
            line_items.append(LineItemOutSerializer({
                "code": d.code,
                "name": d.name,
                "qty": qty,
                "unit_price_cents": unit_cents,
                "options": [],
                "subtotal_cents": subtotal
            }).data)
            running_total += subtotal

        # ---- Items (with options) ----
        for ent in payload.get("items", []):
            item = MenuItem.objects.filter(code=ent["code"], active=True).first()
            if not item:
                return Response({"detail": f"Invalid item.code: {ent['code']}"}, status=status.HTTP_400_BAD_REQUEST)

            qty = Decimal(ent["qty"])
            base = Decimal(item.base_price_cents)

            # selected options
            sel_ids = list(ent.get("options") or [])
            groups = (ItemOptionGroup.objects
                      .filter(item=item)
                      .order_by("rank")
                      .prefetch_related(Prefetch("options", queryset=ItemOption.objects.order_by("rank"))))

            # build lookup
            opt_lookup: Dict[int, ItemOption] = {}
            for g in groups:
                for o in g.options.all():
                    opt_lookup[o.option_id] = o

            opt_out = []
            addon_sum = Decimal("0")
            multiplier_prod = Decimal("1")

            for oid in sel_ids:
                o = opt_lookup.get(oid)
                if not o:
                    return Response({"detail": f"Invalid option_id: {oid}"}, status=status.HTTP_400_BAD_REQUEST)

                if o.group.price_mode == "addon":
                    addon_sum += Decimal(o.price_delta_cents)
                    opt_out.append(LineOptionOutSerializer({
                        "group_name": o.group.name,
                        "option_name": o.name,
                        "mode": "addon",
                        "value": Decimal(o.price_delta_cents)
                    }).data)
                else:
                    m = Decimal(o.multiplier or "1")
                    multiplier_prod *= m
                    opt_out.append(LineOptionOutSerializer({
                        "group_name": o.group.name,
                        "option_name": o.name,
                        "mode": "multiplier",
                        "value": m
                    }).data)

            unit = (base + addon_sum) * multiplier_prod
            unit_cents = as_cents(unit)
            subtotal = int(Decimal(unit_cents) * qty)

            line_items.append(LineItemOutSerializer({
                "code": item.code,
                "name": item.name,
                "qty": qty,
                "unit_price_cents": unit_cents,
                "options": opt_out,
                "subtotal_cents": subtotal
            }).data)
            running_total += subtotal

        out = {
            "line_items": line_items,
            "adjustments": adjustments,
            "subtotal_cents": running_total,
            "discount_cents": 0,
            "total_cents": running_total,  # 할인/쿠폰은 orders 단계에서 처리
        }
        return Response(PricePreviewResponseSerializer(out).data)
