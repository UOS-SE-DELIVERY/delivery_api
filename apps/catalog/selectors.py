from __future__ import annotations
from typing import Iterable

from django.db.models import Exists, F, OuterRef, Q, QuerySet
from django.utils import timezone

from .conf import CATALOG_TZ, CATALOG_ADDONS_SLUG
from .models import (
    MenuCategory, MenuItem, ItemAvailability,
    DinnerType, DinnerTypeDefaultItem,
)

# ---- "지금 가용" 필터 (자정 넘김 포함) ----
# ItemAvailability.dow: 0=일 … 6=토
# datetime.weekday():   0=월 … 6=일 → (weekday+1)%7 로 매핑
def _filter_items_available_now(qs: QuerySet[MenuItem]) -> QuerySet[MenuItem]:
    now = timezone.now().astimezone(CATALOG_TZ)
    today = now.date()
    now_t = now.time()
    dow = (now.weekday() + 1) % 7

    normal_range = Q(start_time__lte=F("end_time")) & Q(start_time__lte=now_t, end_time__gte=now_t)
    overnight    = Q(start_time__gt=F("end_time")) & (Q(start_time__lte=now_t) | Q(end_time__gte=now_t))

    base = (ItemAvailability.objects
            .filter(item_id=OuterRef("pk"), dow=dow)
            .filter((normal_range | overnight))
            .filter(Q(start_date__isnull=True) | Q(start_date__lte=today))
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=today)))

    any_avail = ItemAvailability.objects.filter(item_id=OuterRef("pk"))

    return (qs
            .annotate(_has_avail=Exists(any_avail))
            .annotate(_avail_now=Exists(base))
            .filter(Q(_avail_now=True) | Q(_has_avail=False)))

# 디너 기본구성 아이템 코드 집합
def _dinner_default_item_codes(dinner: DinnerType) -> Iterable[str]:
    return (DinnerTypeDefaultItem.objects
            .filter(dinner_type=dinner)
            .select_related("item")
            .values_list("item__code", flat=True))

# Add-ons 후보 쿼리셋 (카드/리스트 공용; 옵션 prefetch 없음)
def addons_candidates_qs(dinner: DinnerType) -> QuerySet[MenuItem]:
    addons_cat = MenuCategory.objects.filter(active=True, slug=CATALOG_ADDONS_SLUG).first()
    if not addons_cat:
        return MenuItem.objects.none()

    excluded_codes = list(_dinner_default_item_codes(dinner))

    qs = (MenuItem.objects
          .filter(active=True, category=addons_cat)
          .exclude(code__in=excluded_codes)
          .prefetch_related("tags"))

    qs = _filter_items_available_now(qs)
    return qs.order_by("name")
