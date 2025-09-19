from django.db import models
from django.db.models import Q
from apps.accounts.models import Customer
from apps.catalog.models import (
    DinnerType, ServingStyle, MenuItem
)

class OrderStatus(models.TextChoices):
    PENDING   = "pending", "Pending"
    PREP      = "preparing", "Preparing"
    OUT       = "out_for_delivery", "OutForDelivery"
    DELIVERED = "delivered", "Delivered"
    CANCELED  = "canceled", "Canceled"

class OrderSource(models.TextChoices):
    GUI   = "GUI",   "GUI"
    VOICE = "VOICE", "VOICE"

class Order(models.Model):
    id = models.BigAutoField(primary_key=True)
    customer = models.ForeignKey(Customer, on_delete=models.RESTRICT, related_name="orders")
    ordered_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.PENDING)
    order_source = models.CharField(max_length=10, choices=OrderSource.choices, default=OrderSource.GUI)

    # 배송 스냅샷
    receiver_name = models.TextField(null=True, blank=True)
    receiver_phone = models.TextField(null=True, blank=True)
    delivery_address = models.TextField(null=True, blank=True)
    geo_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    geo_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    place_label = models.TextField(null=True, blank=True)
    address_meta = models.JSONField(null=True, blank=True)

    # 결제 스냅샷
    payment_token = models.TextField(null=True, blank=True)
    card_last4 = models.CharField(max_length=4, null=True, blank=True)

    # 합계
    subtotal_cents = models.PositiveIntegerField(default=0)
    discount_cents = models.PositiveIntegerField(default=0)
    total_cents = models.PositiveIntegerField(default=0)

    meta = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "orders"
        indexes = [models.Index(fields=["customer", "-ordered_at"], name="idx_orders_customer_recent")]

    def __str__(self): return f"Order#{self.id}"

class OrderDinner(models.Model):
    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="dinners")
    dinner_type = models.ForeignKey(DinnerType, on_delete=models.RESTRICT)
    style = models.ForeignKey(ServingStyle, on_delete=models.RESTRICT)
    person_label = models.TextField(null=True, blank=True)  # 수취인/좌석 라벨 등
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    base_price_cents = models.PositiveIntegerField()        # 디너 기준가 스냅샷
    style_adjust_cents = models.PositiveIntegerField(default=0)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "order_dinner"

class ChangeType(models.TextChoices):
    UNCHANGED = "unchanged", "Unchanged"
    ADDED     = "added", "Added"
    REMOVED   = "removed", "Removed"
    INCREASED = "increased", "Increased"
    DECREASED = "decreased", "Decreased"

class OrderDinnerItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    order_dinner = models.ForeignKey(OrderDinner, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(MenuItem, on_delete=models.RESTRICT)
    final_qty = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price_cents = models.PositiveIntegerField()
    is_default = models.BooleanField(default=False)
    change_type = models.CharField(max_length=12, choices=ChangeType.choices, default=ChangeType.UNCHANGED)

    class Meta:
        db_table = "order_dinner_item"
        unique_together = (("order_dinner", "item"),)

class OrderItemOption(models.Model):
    id = models.BigAutoField(primary_key=True)
    order_dinner_item = models.ForeignKey(OrderDinnerItem, on_delete=models.CASCADE, related_name="options")
    option_group_name = models.TextField()
    option_name = models.TextField()
    price_delta_cents = models.PositiveIntegerField(default=0)
    multiplier = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)

    class Meta:
        db_table = "order_item_option"

class OrderDinnerOption(models.Model):
    id = models.BigAutoField(primary_key=True)
    order_dinner = models.ForeignKey(OrderDinner, on_delete=models.CASCADE, related_name="options")
    option_group_name = models.TextField()
    option_name = models.TextField()
    price_delta_cents = models.PositiveIntegerField(default=0)
    multiplier = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)

    class Meta:
        db_table = "order_dinner_option"
