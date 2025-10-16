from django.urls import path
from .views import (
    StaffLoginView, StaffLogoutView,
    StaffMeView,
    CouponsView, CouponDetailView,
    MembershipsView, MembershipDetailView,
    StaffOrderDetailView,
    OrdersSSEView,
)

urlpatterns = [
    # Auth
    path("login", StaffLoginView.as_view(), name="staff-login"),
    path("logout", StaffLogoutView.as_view(), name="staff-logout"),

    # Me
    path("me", StaffMeView.as_view(), name="staff-me"),
    path("me/", StaffMeView.as_view()),

    # Orders - 단건 상세 (id 기반)
    path("orders/<int:order_id>", StaffOrderDetailView.as_view(), name="staff-order-detail"),

    # Coupons
    path("coupons", CouponsView.as_view(), name="staff-coupons"),
    path("coupons/<str:code>", CouponDetailView.as_view(), name="staff-coupons-detail"),

    # Memberships
    path("memberships", MembershipsView.as_view(), name="staff-memberships"),
    path("memberships/<int:customer_id>", MembershipDetailView.as_view(), name="staff-memberships-detail"),

    # SSE
    path("sse/orders", OrdersSSEView.as_view(), name="staff-sse-orders"),
]
