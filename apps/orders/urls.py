from django.urls import path
from .views import OrderListCreateAPIView, OrderDetailAPIView, OrderPricePreviewAPIView

app_name = "orders"

urlpatterns = [
    path("price/preview", OrderPricePreviewAPIView.as_view(), name="order-price-preview"),
    path("", OrderListCreateAPIView.as_view(), name="order-list-create"),        # GET, POST
    path("<int:pk>", OrderDetailAPIView.as_view(), name="order-detail"),
]
