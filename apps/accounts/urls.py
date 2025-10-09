from django.urls import path
from .views import (
    RegisterView, LoginView, LogoutView, MeView,
    ContactUpdateView, ConsentUpdateView, PasswordChangeView,
    AddressListCreateView, AddressDetailView, AddressSetDefaultView,
)

urlpatterns = [
    # auth
    path("register",        RegisterView.as_view()),   # POST
    path("login",           LoginView.as_view()),      # POST
    path("logout",          LogoutView.as_view()),     # POST
    path("me",              MeView.as_view()),         # GET

    # profile
    path("contact",         ContactUpdateView.as_view()),   # PATCH
    path("profile-consent", ConsentUpdateView.as_view()),   # PATCH
    path("password",        PasswordChangeView.as_view()),  # PATCH

    # addresses
    path("addresses",                 AddressListCreateView.as_view()),  # GET & POST
    path("addresses/<int:idx>",       AddressDetailView.as_view()),      # PATCH/DELETE
    path("addresses/default",         AddressSetDefaultView.as_view()),  # PATCH
]
