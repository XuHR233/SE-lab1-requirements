"""仅隔离内测实例开放的网页入口。"""

from django.urls import path

from . import media, views

urlpatterns = [
    path("", views.home, name="demo-home"),
    path("demo/desk/", views.desk, name="demo-desk"),
    path("demo/clock/", views.advance_clock, name="demo-clock"),
    path("demo/sellers/<int:pk>/stats/", views.seller_dashboard, name="demo-seller-dashboard"),
    path("demo/products/<int:pk>/image/", media.upload_product, name="demo-product-upload"),
    path("demo/chats/<int:pk>/image/", media.upload_chat, name="demo-chat-upload"),
    path("demo/media/products/<int:pk>/<uuid:key>.webp", media.product_file,
         name="demo-product-file"),
    path("demo/media/chats/<int:pk>/<uuid:key>.webp", media.chat_file, name="demo-chat-file"),
]
