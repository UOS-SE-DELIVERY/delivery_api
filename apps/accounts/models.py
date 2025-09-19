from django.db import models
from django.core.validators import RegexValidator
from django.db.models import ExpressionWrapper

class LoyaltyTier(models.TextChoices):
    NONE   = "none",   "None"
    SILVER = "silver", "Silver"
    GOLD   = "gold",   "Gold"

phone_validator = RegexValidator(
    regex=r"^010-\d{4}-\d{4}$",
    message="phone number format: 010-0000-0000",
)

class Customer(models.Model):
    customer_id = models.BigAutoField(primary_key=True)
    username = models.TextField(unique=True)
    password = models.CharField(max_length=64)
    real_name = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True, validators=[phone_validator])
    addresses = models.JSONField(default=list)
    loyalty_tier = models.TextField(choices=LoyaltyTier.choices, default=LoyaltyTier.NONE)
    profile_consent = models.BooleanField(default=False)
    profile_consent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customer"
        constraints = [
            models.CheckConstraint(
                name="ck_customer_addresses_json",
                check=ExpressionWrapper(
                    models.expressions.RawSQL(
                        "jsonb_typeof(addresses) = 'array' AND jsonb_array_length(addresses) <= 3", []
                    ),
                    output_field=models.BooleanField(),
                ),
            ),
            models.CheckConstraint(
                name="ck_customer_loyalty_tier",
                check=models.Q(loyalty_tier__in=[c.value for c in LoyaltyTier]),
            ),
        ]

    def __str__(self):
        return self.username
