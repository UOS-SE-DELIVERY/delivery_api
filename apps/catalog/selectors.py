from __future__ import annotations
from typing import Iterable

from django.db.models import Exists, F, OuterRef, Q, Prefetch, QuerySet
from django.utils import timezone

from .conf import CATALOG_TZ, CATALOG_ADDONS_SLUG
from .models import (
    MenuCategory, MenuItem, ItemAvailability,
    ItemOptionGroup, ItemOption,
    DinnerType, DinnerTypeDefaultItem,
)

# ---------- 가용성(available_now) 필터 ----------
# ItemAvailability.dow: 0=일 … 6=토
# datetime.weekday():   0=월 … 6=일 → (weekday+1)%7 로 매핑
def _filter_items_available_now(qs: QuerySet[MenuItem]) -> QuerySet[MenuItem]:
    now = timezone.now().astimezone(CATALOG_TZ)
    today = now.date()
    now_t = now.time()
    dow = (now.weekday() + 1) % 7

    # 자정 넘김 구간(start_time > end_time)까지 처리
    normal_range = Q(start_time__lte=F("end_time")) & Q(start_time__lte=now_t, end_time__gte=now_t)
    overnight    = Q(start_time__gt=F("end_time")) & (Q(start_time__lte=now_t) | Q(end_time__gte=now_t))

    base = ItemAvailability.objects.filter(
        item_id=OuterRef("pk"),
        dow=dow,
    ).filter(
        (normal_range | overnight),
        Q(start_date__isnull=True) | Q(start_date__lte=today),
        Q(end_date__isnull=True) | Q(end_date__gte=today),
    )

    any_avail = ItemAvailability.objects.filter(item_id=OuterRef("pk"))

    return (qs
            .annotate(_has_avail=Exists(any_avail))
            .annotate(_avail_now=Exists(base))
            .filter(Q(_avail_now=True) | Q(_has_avail=False)))

def _prefetch_item_option_groups() -> Prefetch:
    return Prefetch(
        "option_groups",
        queryset=ItemOptionGroup.objects.order_by("rank", "group_id").prefetch_related(
            Prefetch("options", queryset=ItemOption.objects.order_by("rank", "option_id"))
        ),
    )

# ---------- 디너 기본구성 제외용 코드 집합 ----------
def dinner_default_item_codes(dinner: DinnerType) -> Iterable[str]:
    return (DinnerTypeDefaultItem.objects
            .filter(dinner_type=dinner)
            .select_related("item")
            .values_list("item__code", flat=True))

# ---------- Add-ons 후보 쿼리셋 ----------
def addons_candidates_qs(dinner: DinnerType) -> QuerySet[MenuItem]:
    addons_cat = MenuCategory.objects.filter(active=True, slug=CATALOG_ADDONS_SLUG).first()
    if not addons_cat:
        # 운영 실수로 카테고리가 없으면 빈 결과
        return MenuItem.objects.none()

    excluded_codes = list(dinner_default_item_codes(dinner))

    qs = (MenuItem.objects
          .filter(active=True, category=addons_cat)
          .exclude(code__in=excluded_codes)
          .prefetch_related(_prefetch_item_option_groups(), "tags")
          .select_related("category"))

    # 항상 "가용한 것만"
    qs = _filter_items_available_now(qs)

    # MenuItem에는 rank 필드가 없으므로 name 정렬
    return qs.order_by("name")

# ---------- 카테고리 리스트 공용 ----------
def category_items_qs(slug: str, *, include_tags: bool, include_availability: bool) -> QuerySet[MenuItem]:
    qs = (MenuItem.objects
          .filter(active=True, category__active=True, category__slug=slug)
          .prefetch_related(_prefetch_item_option_groups())
          .select_related("category"))

    if include_tags:
        qs = qs.prefetch_related("tags")
    if include_availability:
        qs = qs.prefetch_related(
            Prefetch("itemavailability_set", queryset=ItemAvailability.objects.order_by("dow", "start_time"))
        )

    # MenuItem에는 rank가 없으므로 name만
    return qs.order_by("name")
