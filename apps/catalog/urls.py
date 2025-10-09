from django.urls import path

from .views import (
    CategoryListAPIView,
    ItemListAPIView, ItemDetailAPIView, ItemAvailabilityAPIView,
    ItemTagListAPIView,
    DinnerListAPIView, DinnerDetailAPIView, DinnerDefaultItemsAPIView,
    DinnerStylesAPIView, DinnerOptionGroupsAPIView,
    PricePreviewAPIView
)

app_name = "catalog"

urlpatterns = [
    # categories
    path("categories", CategoryListAPIView.as_view(), name="category-list"),

    # items
    path("items", ItemListAPIView.as_view(), name="item-list"),
    path("items/<str:code>", ItemDetailAPIView.as_view(), name="item-detail"),
    path("items/<str:code>/availability", ItemAvailabilityAPIView.as_view(), name="item-availability"),

    # tags
    path("tags", ItemTagListAPIView.as_view(), name="tag-list"),

    # dinners
    path("dinners", DinnerListAPIView.as_view(), name="dinner-list"),
    path("dinners/<str:dinner_code>", DinnerDetailAPIView.as_view(), name="dinner-detail"),
    path("dinners/<str:dinner_code>/default-items", DinnerDefaultItemsAPIView.as_view(), name="dinner-default-items"),
    path("dinners/<str:dinner_code>/styles", DinnerStylesAPIView.as_view(), name="dinner-styles"),
    path("dinners/<str:dinner_code>/option-groups", DinnerOptionGroupsAPIView.as_view(), name="dinner-option-groups"),

    # price preview
    path("price/preview", PricePreviewAPIView.as_view(), name="price-preview"),
]
