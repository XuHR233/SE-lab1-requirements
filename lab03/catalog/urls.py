"""商品中心路由（挂在 /api/ 下）。"""

from django.urls import path

from reservations import views as reservation_views

from . import views

app_name = "catalog"

urlpatterns = [
    path("products/", views.products, name="products"),
    path(
        "products/<int:pk>/reservations/",
        reservation_views.reserve_view,
        name="reserve",
    ),
    path("products/<int:pk>/<str:action>/", views.product_action, name="product-action"),
    path("products/<int:pk>/", views.product_detail, name="product-detail"),
]
