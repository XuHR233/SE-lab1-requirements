"""评价证据和申诉处理记录（REQ-020）。"""

from django.conf import settings
from django.db import models
from django.db.models import Q


class Review(models.Model):
    """软删除保留评价的统计记录；屏蔽只影响公开展示。"""

    order = models.ForeignKey("trade.Order", on_delete=models.PROTECT, related_name="reviews")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    target = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    rating = models.PositiveSmallIntegerField()
    content = models.CharField(max_length=1000, blank=True)
    automatic = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    hidden_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """一个订单的每一方最多有一条评价。"""

        constraints = [
            models.UniqueConstraint(fields=["order", "author"], name="ux_review_order_author"),
            models.CheckConstraint(condition=Q(rating__range=(1, 5)), name="ck_review_rating"),
        ]
        indexes = [models.Index(fields=["target", "-id"])]


class Appeal(models.Model):
    """同一评价的申诉和处理说明，不覆盖原文。"""

    review = models.OneToOneField(Review, on_delete=models.PROTECT, related_name="appeal")
    reason = models.CharField(max_length=500)
    note = models.CharField(max_length=500, blank=True)
    upheld = models.BooleanField(null=True)
    handler = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", null=True
    )
    due_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True)
    reminded_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """按处理状态和期限筛选申诉。"""

        indexes = [models.Index(fields=["resolved_at", "due_at"])]
