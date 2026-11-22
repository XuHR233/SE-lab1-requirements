"""订单路由：列表/建单、详情、按动作分派。"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "trade"

urlpatterns = [
    path("", views.orders, name="list"),
    path("<int:pk>/", views.order_detail, name="detail"),
    path("<int:pk>/<str:action>/", views.order_action, name="action"),
]
