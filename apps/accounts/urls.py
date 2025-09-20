from django.urls import path
from .views import (
    SignupView, LoginView, MeView,
    ContactUpdateView, AddressCreateView, AddressSetDefaultView,
    ConsentUpdateView, LogoutView,
)

urlpatterns = [
    path("signup",            SignupView.as_view()),              # POST
    path("login",             LoginView.as_view()),               # POST
    path("me",                MeView.as_view()),                  # GET
    path("contact",           ContactUpdateView.as_view()),       # PATCH
    path("addresses",         AddressCreateView.as_view()),       # POST (단건 추가)
    path("addresses/default", AddressSetDefaultView.as_view()),   # PATCH (idx/label)
    path("profile-consent",   ConsentUpdateView.as_view()),       # PATCH
    path("logout",            LogoutView.as_view()),              # POST
]
