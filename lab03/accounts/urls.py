"""账号相关路由。"""

from django.urls import path

from . import views

urlpatterns = [
    path("register", views.register, name="register"),
    path("login", views.sign_in_view, name="login"),
    path("logout", views.sign_out_view, name="logout"),
]
