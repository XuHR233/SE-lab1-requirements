"""预约路由：列表、详情、按动作分派。建预约入口挂在商品下（catalog/urls.py）。"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "reservations"

urlpatterns = [
    path("", views.reservation_list, name="list"),
    path("<int:pk>/", views.reservation_detail, name="detail"),
    path("<int:pk>/<str:action>/", views.reservation_action, name="action"),
]
