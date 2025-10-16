from django.urls import path
from .views import (
    RegisterView, LoginView, LogoutView, MeViewSet
)

me_base = MeViewSet.as_view({"get": "retrieve", "patch": "partial_update"})
me_password = MeViewSet.as_view({"post": "change_password"})
me_addresses = MeViewSet.as_view({"get": "addresses", "post": "addresses"})
me_address_item = MeViewSet.as_view({"patch": "modify_address", "delete": "modify_address"})
me_address_default = MeViewSet.as_view({"patch": "set_default_address"})
me_username = MeViewSet.as_view({"post": "change_username"})

urlpatterns = [
    # Auth
    path("register", RegisterView.as_view(), name="auth-register"),
    path("login", LoginView.as_view(), name="auth-login"),
    path("logout", LogoutView.as_view(), name="auth-logout"),

    # Me
    path("me/", me_base, name="me"),
    path("me/password/", me_password, name="me-password"),
    path("me/addresses/", me_addresses, name="me-addresses"),
    path("me/addresses/<int:idx>/", me_address_item, name="me-address-item"),
    path("me/addresses/<int:idx>/default/", me_address_default, name="me-address-default"),
    path("me/username/", me_username, name="me-username"),
]
