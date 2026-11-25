"""沟通与纠纷的路由（REQ-018/019）。由 `config/urls.py` 一行 include 挂到 `api/chats/`。"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "communication"

urlpatterns = [
    path("", views.chats, name="chats"),
    path("disputes/", views.disputes, name="disputes"),
    path("disputes/workbench/", views.dispute_orders, name="workbench"),
    path("disputes/orders/<int:pk>/", views.order_dispute, name="order_dispute"),
    path("disputes/<int:pk>/", views.dispute_detail, name="dispute_detail"),
    path("disputes/<int:pk>/<str:action>/", views.dispute_action, name="dispute_action"),
    path("<int:pk>/", views.chat_detail, name="chat_detail"),
    path("<int:pk>/messages/", views.chat_message, name="chat_message"),
    path("<int:pk>/<str:action>/", views.chat_action, name="chat_action"),
]
