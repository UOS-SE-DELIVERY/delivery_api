from django.urls import path

from .views import (
    CatalogBootstrapAPIView,
    # Add-ons
    AddonsRecommendationsAPIView,
    AddonsListPageAPIView,
    # 상세/디너
    ItemDetailWithExpandAPIView,
    DinnerFullAPIView,
)

app_name = "catalog"

urlpatterns = [
    # 부트스트랩
    path("bootstrap", CatalogBootstrapAPIView.as_view()),

    # Add-ons: 추천 카드 / 전체 리스트
    path("addons/<str:dinner_code>", AddonsRecommendationsAPIView.as_view()),
    path("menu/addons/<str:dinner_code>", AddonsListPageAPIView.as_view()),

    # 상세 / 디너
    path("items/<str:item_code>", ItemDetailWithExpandAPIView.as_view()),
    path("dinners/<str:dinner_code>", DinnerFullAPIView.as_view()),
]
