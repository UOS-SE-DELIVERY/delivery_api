from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Tuple, Optional

from django.db import transaction
from django.utils import timezone

from apps.promotion.models import Coupon, CouponRedemption, Membership


def _qcent(x) -> Decimal:
    """원 단위 HALF_UP 반올림(Decimal 유지)"""
    return Decimal(x).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _normalize_codes(codes: List[str]) -> List[str]:
    return [c.upper().strip() for c in (codes or []) if str(c).strip()]


def _membership_line(customer_id: Optional[int], subtotal_cents: int) -> Optional[Dict]:
    """고객 멤버십 percent_off 적용. 없으면 None."""
    if not customer_id:
        return None
    m = Membership.objects.filter(customer_id=customer_id, active=True).first()
    if not (m and m.is_valid_now()):
        return None
    pct = Decimal(m.percent_off or 0) / Decimal("100")
    amt = _qcent(Decimal(subtotal_cents) * pct)
    return None if amt <= 0 else {
        "type": "membership", "label": m.label or "Membership", "code": None, "amount_cents": int(amt)
    }


def _coupon_amount(coupon: Coupon, base_amount_cents: int) -> int:
    """쿠폰 1개 할인액(상한 적용, 과할인 방지 전 단계)."""
    if coupon.kind == "percent":
        pct = Decimal(coupon.value) / Decimal("100")
        amt = _qcent(Decimal(base_amount_cents) * pct)
    else:
        amt = _qcent(Decimal(coupon.value))
    if coupon.max_discount_cents is not None:
        amt = min(amt, Decimal(int(coupon.max_discount_cents)))
    return max(0, int(amt))


def evaluate_discounts(
    *,
    subtotal_cents: int,
    customer_id: Optional[int] = None,
    channel: str = "GUI",
    coupon_codes: Optional[List[str]] = None,
    # 아래 파라미터들은 orders API 호환을 위해 받지만, 현재 구현에서는 미사용
    dinner_code: Optional[str] = None,
    item_lines: Optional[List[Dict]] = None,
    style_code: Optional[str] = None,
    dinner_option_ids: Optional[List[int]] = None,
) -> Tuple[List[Dict], int, int]:
    """
    반환: (discounts[], total_discount_cents, total_after_cents)
    정책:
      - 멤버십 → 쿠폰 순서
      - 쿠폰 유효성: 활성/기간/채널/최소금액/사용한도(소프트)
      - 스코프 체크 없음(전체 금액 기준)
      - 비스택형 쿠폰 섞이면 최대 1개, 아니면 순차 스택
      - 과할인 방지: running_total 미만 금지
    """
    now = timezone.now()
    discounts: List[Dict] = []

    # 1) 멤버십
    membership = _membership_line(customer_id, subtotal_cents)
    running = Decimal(subtotal_cents)
    if membership:
        running = _qcent(running - Decimal(membership["amount_cents"]))
        discounts.append(membership)

    # 2) 쿠폰 후보
    codes = _normalize_codes(coupon_codes or [])
    if not codes:
        total_discount = sum(d["amount_cents"] for d in discounts)
        return discounts, int(total_discount), int(running)

    coupons = {c.code: c for c in Coupon.objects.filter(code__in=codes)}
    eligible: List[tuple[Coupon, int]] = []

    for code in codes:
        c = coupons.get(code.upper())
        if not c:
            continue
        if not c.is_valid_now(now):
            continue
        # 채널
        if c.channel not in ("ANY", channel or "GUI"):
            continue
        # 최소금액(할인 전 소계 기준)
        if c.min_subtotal_cents is not None and int(subtotal_cents) < int(c.min_subtotal_cents):
            continue
        # per-user/global 사용 한도(소프트)
        if c.max_redemptions_per_user is not None and customer_id:
            used = CouponRedemption.objects.filter(coupon=c, customer_id=customer_id).count()
            if used >= int(c.max_redemptions_per_user):
                continue
        if c.max_redemptions_global is not None:
            used_g = CouponRedemption.objects.filter(coupon=c).count()
            if used_g >= int(c.max_redemptions_global):
                continue
        # 멤버십과 스택 금지면 제외
        if membership and not c.stackable_with_membership:
            continue

        amount = _coupon_amount(c, int(running))
        if amount > 0:
            eligible.append((c, amount))

    if not eligible:
        total_discount = sum(d["amount_cents"] for d in discounts)
        return discounts, int(total_discount), int(running)

    # 3) 적용: 비스택 섞이면 최대 1개, 아니면 순차 적용
    if any(not c.stackable_with_coupons for (c, _) in eligible):
        best_c, best_amt = max(eligible, key=lambda t: t[1])
        apply_amt = min(best_amt, int(running))
        running = _qcent(running - Decimal(apply_amt))
        discounts.append({
            "type": "coupon",
            "label": best_c.label or best_c.name or best_c.code,
            "code": best_c.code,
            "amount_cents": int(apply_amt),
        })
    else:
        for c, pre_amt in eligible:
            amt = min(pre_amt, int(running))
            if amt <= 0:
                continue
            running = _qcent(running - Decimal(amt))
            discounts.append({
                "type": "coupon",
                "label": c.label or c.name or c.code,
                "code": c.code,
                "amount_cents": int(amt),
            })

    total_discount = sum(d["amount_cents"] for d in discounts)
    return discounts, int(total_discount), int(_qcent(running))


@transaction.atomic
def redeem_discounts(
    *,
    order,
    customer_id: int,
    channel: str,
    discounts: List[Dict],
):
    """
    주문 생성 트랜잭션 내부에서 호출.
    - discounts[] 중 type='coupon' 라인만 확정 기록
    - per-user/global 한도 '하드 체크' 후 CouponRedemption 생성
    - 경쟁 조건 방지를 위해 쿠폰 행 select_for_update()
    """
    if not discounts:
        return []

    # 쿠폰별 합계 금액(같은 코드 중복 라인 방지)
    per_code: Dict[str, int] = {}
    for d in discounts:
        if d.get("type") != "coupon":
            continue
        code = (d.get("code") or "").upper()
        if not code:
            continue
        per_code[code] = per_code.get(code, 0) + int(d.get("amount_cents") or 0)

    if not per_code:
        return []

    # 잠금 후 재검사
    coupons = list(Coupon.objects.select_for_update().filter(code__in=list(per_code.keys())))
    by_code = {c.code: c for c in coupons}
    now = timezone.now()

    rows: List[CouponRedemption] = []
    for code, amt in per_code.items():
        c = by_code.get(code)
        if not c:
            continue
        # 유효성 재검사
        if not c.is_valid_now(now):
            continue
        if c.channel not in ("ANY", channel or "GUI"):
            continue
        # 사용 한도 재검사
        if c.max_redemptions_per_user is not None:
            used = CouponRedemption.objects.filter(coupon=c, customer_id=customer_id).count()
            if used >= int(c.max_redemptions_per_user):
                continue
        if c.max_redemptions_global is not None:
            used_g = CouponRedemption.objects.filter(coupon=c).count()
            if used_g >= int(c.max_redemptions_global):
                continue
        # 한 주문에 중복 방지 (유니크 제약도 있지만 사전 체크)
        if CouponRedemption.objects.filter(coupon=c, order=order).exists():
            continue

        row = CouponRedemption.objects.create(
            coupon=c,
            customer_id=customer_id,
            order=order,
            amount_cents=int(amt),
            channel=channel or "GUI",
        )
        rows.append(row)

    return rows
