"""商品中心路由（挂在 /api/ 下）。"""

from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("products/", views.products, name="products"),
    path("products/<int:pk>/<str:action>/", views.product_action, name="product-action"),
    path("products/<int:pk>/", views.product_detail, name="product-detail"),
]
