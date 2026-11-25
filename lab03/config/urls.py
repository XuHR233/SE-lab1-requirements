"""URL 汇总。"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("demo.urls")),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("api/", include("reviews.urls")),
    path("api/", include("audit.urls")),
    path("api/", include("catalog.urls")),
    path("api/reservations/", include("reservations.urls")),
    path("api/orders/", include("trade.urls")),
    path("api/chats/", include("communication.urls")),
]
