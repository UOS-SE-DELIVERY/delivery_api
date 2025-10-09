from django.urls import path
from .views import OrderListAPIView, OrderDetailAPIView, OrderCreateAPIView

app_name = "orders"

urlpatterns = [
    path("", OrderListAPIView.as_view(), name="order-list"),
    path("<int:pk>", OrderDetailAPIView.as_view(), name="order-detail"),
    path("", OrderCreateAPIView.as_view(), name="order-create"),  # POST same path
]
