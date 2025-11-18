from .views import OrderActionAPIView

from django.urls import path
from .views import OrderListCreateAPIView, OrderDetailAPIView, OrderPricePreviewAPIView, OrderUpdateAPIView

app_name = "orders"

urlpatterns = [
    path("price/preview", OrderPricePreviewAPIView.as_view(), name="order-price-preview"),
    path("", OrderListCreateAPIView.as_view(), name="order-list-create"),        # GET, POST
    path("<int:pk>", OrderDetailAPIView.as_view(), name="order-detail"),
    path("<int:pk>/action", OrderActionAPIView.as_view(), name="order-action"),
    path("api/orders/<int:pk>", OrderUpdateAPIView.as_view(), name="order-update"),
]