from django.urls import path

from .views import (
    CatalogBootstrapAPIView,
    # Add-ons (고정 라우트)
    AddonsRecommendationsAPIView,
    AddonsListPageAPIView,
    # 일반 메뉴/상세/디너
    MenuByCategoryAPIView,
    ItemDetailWithExpandAPIView,
    DinnerFullAPIView,
    # 가격 미리보기
    PricePreviewAPIView,
)

app_name = "catalog"

urlpatterns = [
    # 부트스트랩
    path("bootstrap", CatalogBootstrapAPIView.as_view()),

    # Add-ons: 추천 카드 / 리스트 페이지 (dinner_code는 path param)
    path("addons/<str:dinner_code>", AddonsRecommendationsAPIView.as_view()),
    path("menu/addons/<str:dinner_code>", AddonsListPageAPIView.as_view()),

    # 일반 메뉴 / 상세 / 디너
    path("menu", MenuByCategoryAPIView.as_view()),                     # GET ?category=<slug>[&include=...]
    path("items/<str:item_code>", ItemDetailWithExpandAPIView.as_view()),
    path("dinners/<str:dinner_code>", DinnerFullAPIView.as_view()),

    # 가격 미리보기
    path("price/preview", PricePreviewAPIView.as_view()),
]
