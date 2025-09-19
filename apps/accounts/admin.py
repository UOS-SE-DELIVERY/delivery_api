from django.contrib import admin
from .models import Customer

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("customer_id", "username", "real_name", "phone", "loyalty_tier", "created_at")
    search_fields = ("username", "real_name", "phone")
