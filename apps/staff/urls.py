from django.urls import path
from .views import (
    # Auth & Me
    StaffLoginView, StaffLogoutView, StaffMeView,
    # Coupons
    CouponsView, CouponDetailView,
    # Memberships
    MembershipsView, MembershipDetailView,
    # Orders (detail) & SSE
    StaffOrderDetailView, OrdersSSEView,
    # Inventory
    InventoryItemsView, InventoryItemDetailView, InventoryUploadView,
)

urlpatterns = [
    # Inventory
    path("inventory/items", InventoryItemsView.as_view(), name="staff-inventory-items"),
    path("inventory/items/<str:code>", InventoryItemDetailView.as_view(), name="staff-inventory-item-detail"),
    path("inventory/upload", InventoryUploadView.as_view(), name="staff-inventory-upload"),

    # Auth
    path("login", StaffLoginView.as_view(), name="staff-login"),
    path("logout", StaffLogoutView.as_view(), name="staff-logout"),

    # Me
    path("me", StaffMeView.as_view(), name="staff-me"),

    # Orders
    path("orders/<int:order_id>", StaffOrderDetailView.as_view(), name="staff-order-detail"),

    # Coupons
    path("coupons", CouponsView.as_view(), name="staff-coupons"),
    path("coupons/<str:code>", CouponDetailView.as_view(), name="staff-coupons-detail"),

    # SSE (orders)
    path("sse/orders", OrdersSSEView.as_view(), name="staff-sse-orders"),
]
