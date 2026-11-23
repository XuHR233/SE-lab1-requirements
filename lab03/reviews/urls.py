"""M6 路由。"""

from django.urls import path

from . import views

urlpatterns = [
    path("orders/<int:order_id>/review/", views.order_review),
    path("products/<int:product_id>/reviews/", views.product_reviews),
    path("reviews/<int:review_id>/appeal/", views.raise_appeal),
    path("staff/review-appeals/", views.appeal_queue),
    path("staff/review-appeals/<int:appeal_id>/resolve/", views.resolve_appeal),
]
