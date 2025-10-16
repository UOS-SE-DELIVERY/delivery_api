from __future__ import annotations
from decimal import Decimal
from django.db import models
from django.utils import timezone

from apps.accounts.models import Customer


class Coupon(models.Model):
    KIND_CHOICES = (
        ("percent", "Percent"),
        ("fixed", "FixedAmount"),
    )
    CHANNEL_CHOICES = (
        ("ANY", "Any"),
        ("GUI", "GUI"),
        ("VOICE", "VOICE"),
    )

    code = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=120)
    label = models.CharField(max_length=120, blank=True, default="")
    active = models.BooleanField(default=True)

    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    value = models.DecimalField(max_digits=10, decimal_places=2)

    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)

    # 최소 주문 금액(할인 전 소계 기준), 최대 할인 상한
    min_subtotal_cents = models.IntegerField(null=True, blank=True)
    max_discount_cents = models.IntegerField(null=True, blank=True)

    # 스택/채널
    stackable_with_membership = models.BooleanField(default=True)
    stackable_with_coupons = models.BooleanField(default=True)
    channel = models.CharField(max_length=8, choices=CHANNEL_CHOICES, default="ANY")

    # 사용 한도
    max_redemptions_global = models.IntegerField(null=True, blank=True)
    max_redemptions_per_user = models.IntegerField(null=True, blank=True)

    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "promotion_coupon"
        indexes = [
            models.Index(fields=["active", "valid_from", "valid_until"]),
        ]

    def __str__(self):
        return f"{self.code} ({self.name})"

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.upper()
        super().save(*args, **kwargs)

    def is_valid_now(self, now=None) -> bool:
        now = now or timezone.now()
        if not self.active:
            return False
        if self.valid_from and now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        return True


class CouponRedemption(models.Model):
    coupon = models.ForeignKey(Coupon, on_delete=models.PROTECT, related_name="redemptions")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="coupon_redemptions")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="coupon_redemptions")

    amount_cents = models.IntegerField(default=0)
    channel = models.CharField(max_length=8, default="GUI")
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "promotion_coupon_redemption"
        constraints = [
            # 한 주문에 같은 쿠폰 중복 방지
            models.UniqueConstraint(fields=["coupon", "order"], name="uq_coupon_order_once"),
        ]
        indexes = [
            models.Index(fields=["coupon", "customer"]),
            models.Index(fields=["coupon", "redeemed_at"]),
        ]

    def __str__(self):
        return f"{self.coupon.code} -> Order#{self.order_id} ({self.amount_cents}c)"


class Membership(models.Model):
    """고객 멤버십(퍼센트 할인). 없으면 미적용."""
    customer = models.OneToOneField(Customer, on_delete=models.CASCADE, related_name="membership")
    label = models.CharField(max_length=120, default="Membership")
    percent_off = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))  # 0~100
    active = models.BooleanField(default=True)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "promotion_membership"

    def __str__(self):
        return f"{self.customer_id} - {self.label} {self.percent_off}%"

    def is_valid_now(self, now=None) -> bool:
        now = now or timezone.now()
        if not self.active:
            return False
        if self.valid_from and now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        return True
