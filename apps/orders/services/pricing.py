from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Tuple

from apps.catalog.models import (
    MenuItem, ItemOption, ItemOptionGroup,
    DinnerType, ServingStyle, DinnerStyleAllowed,
    DinnerOption,
)

# ---------- 공용 반올림 유틸 ----------
def as_cents_dec(x: Decimal | int | str) -> Decimal:
    return Decimal(x).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

def as_cents_int(x: Decimal | int | str) -> int:
    return int(as_cents_dec(x))

# ---------- 검증 도우미 ----------
def validate_style_allowed(dinner: DinnerType, style: ServingStyle) -> None:
    if not DinnerStyleAllowed.objects.filter(dinner_type=dinner, style=style).exists():
        raise ValueError(f"Style '{style.code}' is not allowed for dinner '{dinner.code}'")

def validate_item_options_for_item(item: MenuItem, option_ids: List[int]) -> List[ItemOption]:
    if not option_ids:
        return []
    opts = list(ItemOption.objects.select_related("group").filter(pk__in=option_ids))
    bad = [o.pk for o in opts if o.group.item_id != item.item_id]
    if bad:
        raise ValueError(f"Options {bad} are not valid for item '{item.code}'")
    return opts

def resolve_dinner_options_for_dinner(dinner: DinnerType, opt_ids: List[int]) -> List[DinnerOption]:
    if not opt_ids:
        return []
    opts = list(DinnerOption.objects.select_related("group", "item")
                .filter(pk__in=opt_ids, group__dinner_type=dinner))
    if len(opts) != len(set(opt_ids)):
        raise ValueError("Some dinner_option ids are invalid for this dinner")
    return opts

# ---------- 아이템 단가 계산 ----------
def calc_item_unit_cents(item: MenuItem, selected_opts: List[ItemOption]) -> Tuple[int, List[Dict]]:
    """
    addon: base에 가산
    multiplier: (base+addon)에 곱(단가 레벨), HALF_UP
    """
    base = Decimal(item.base_price_cents or 0)
    addon = Decimal("0")
    mult = Decimal("1")
    snaps: List[Dict] = []

    for o in selected_opts:
        g: ItemOptionGroup = o.group
        if (g.price_mode or "addon") == "addon":
            addon += Decimal(o.price_delta_cents or 0)
            snaps.append({
                "option_group_name": g.name,
                "option_name": o.name,
                "price_delta_cents": int(o.price_delta_cents or 0),
                "multiplier": None
            })
        else:
            m = Decimal(o.multiplier or "1")
            mult *= m
            snaps.append({
                "option_group_name": g.name,
                "option_name": o.name,
                "price_delta_cents": 0,
                "multiplier": m
            })

    unit = as_cents_dec((base + addon) * mult)
    return int(unit), snaps

# ---------- 디너 base에 스타일 적용(배수는 디너 가격에만) ----------
def apply_style_to_base(dinner: DinnerType, style: ServingStyle) -> Tuple[int, int]:
    """
    return: (적용 후 디너 단가 cents, 스타일로 인한 조정금액 cents[참고용])
    """
    base = Decimal(dinner.base_price_cents or 0)
    if (style.price_mode or "addon") == "addon":
        inc = Decimal(style.price_value or 0)
        new_base = base + inc
        return as_cents_int(new_base), as_cents_int(inc)
    else:
        m = Decimal(style.price_value or "1")
        new_base = as_cents_dec(base * m)
        return int(new_base), as_cents_int(new_base - base)
