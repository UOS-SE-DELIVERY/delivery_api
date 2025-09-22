from django.urls import path
from .views import (
    menuView, 
)

urlpatterns = [
    # path("register",          RegisterView.as_view()),
    path("menu", menuView.as_View()), # GET, customerOnly
    path("")


]
