"""预售预约模型（M3）：7 个状态，同一商品同时最多 1 条有效预约（REQ-006）。"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from catalog.models import Product

WAITING_DEPOSIT = "待付定金"
AWAITING_SELLER = "待确认"
WAITING_ARRIVAL = "待到货"
WAITING_FINAL = "待付尾款"
COMPLETED = "已完成"
CANCELED = "已取消"
REJECTED = "已拒绝"

# 状态枚举取自实验二 UML 类图（预约状态机 7 态），没有额外的「已过期」：
# 定金超时、尾款逾期、有效期届满都归入已取消，原因写在 close_reason。
RESERVATION_STATUSES: tuple[str, ...] = (
    WAITING_DEPOSIT,
    AWAITING_SELLER,
    WAITING_ARRIVAL,
    WAITING_FINAL,
    COMPLETED,
    CANCELED,
    REJECTED,
)

# 「有效预约」四个状态：占着这件货，因此他人看不到商品、卖家不能改内容。
ACTIVE_STATUSES: tuple[str, ...] = (
    WAITING_DEPOSIT,
    AWAITING_SELLER,
    WAITING_ARRIVAL,
    WAITING_FINAL,
)

STATUS_CHOICES = [(status, status) for status in RESERVATION_STATUSES]


def active_product_ids() -> models.QuerySet:
    """当前被有效预约占货的商品 id（列表与检索据此隐藏，REQ-003/006）。"""
    return Reservation.objects.filter(status__in=ACTIVE_STATUSES).values_list(
        "product_id", flat=True
    )


def has_active(product: Product) -> bool:
    """该商品当前是否有有效预约。"""
    return Reservation.objects.filter(
        product_id=product.pk, status__in=ACTIVE_STATUSES
    ).exists()


class Reservation(models.Model):  # pylint: disable=too-many-instance-attributes
    """一条预约：从待付定金走到已完成的唯一路径由 services 层落地。"""

    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="reservations", verbose_name="商品"
    )
    # pylint: disable=duplicate-code
    # 理由：与 trade.models.Order 的买家/卖家外键逐字相同，但要靠各自模块的
    # settings.AUTH_USER_MODEL 与 related_name 声明；抽 mixin 会削弱字段可读性。
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reservations",
        verbose_name="买家",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="卖家",
    )
    status = models.CharField(
        "状态", max_length=16, choices=STATUS_CHOICES, default=WAITING_DEPOSIT, db_index=True
    )
    deposit = models.DecimalField("定金", max_digits=10, decimal_places=2, default=Decimal("0"))
    final_amount = models.DecimalField(
        "尾款", max_digits=10, decimal_places=2, default=Decimal("0")
    )
    deposit_due_at = models.DateTimeField("定金截止时间")
    expires_at = models.DateTimeField("预约有效期")
    deposit_paid_at = models.DateTimeField("定金支付时间", null=True, blank=True)
    seller_confirmed_at = models.DateTimeField("卖家确认预约时间", null=True, blank=True)
    arrived_at = models.DateTimeField("确认到货时间", null=True, blank=True)
    final_due_at = models.DateTimeField("尾款截止时间", null=True, blank=True)
    request_key = models.CharField("请求标识", max_length=64, blank=True)
    product_snapshot = models.JSONField("预约商品快照", default=dict, blank=True)
    refund_due = models.DecimalField(
        "应原路退回的定金", max_digits=10, decimal_places=2, default=Decimal("0")
    )
    close_reason = models.CharField("关闭原因", max_length=200, blank=True)
    closed_at = models.DateTimeField("关闭时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True, db_index=True)

    class Meta:
        """同一商品只允许一条有效预约，由部分唯一索引在数据库层保证并发安全。"""

    # pylint: disable=duplicate-code
    # 理由：与 trade.models.Order 是同一条「同一商品只允许一条有效单据」的约束，
    # 只是约束名与状态集合不同；抽公共函数要在 Meta 里跨模块调用，反而更难读。

        verbose_name = "预售预约"
        verbose_name_plural = "预售预约"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product"],
                condition=Q(status__in=ACTIVE_STATUSES),
                name="ux_reservation_active_per_product",
            )
        ]
        indexes = [
            models.Index(fields=["buyer", "status"]),
            models.Index(fields=["product", "status"]),
        ]

    def __str__(self) -> str:
        return f"预约#{self.pk} {self.product_id} {self.status}"

    def is_active(self) -> bool:
        """是否仍然占货。"""
        return self.status in ACTIVE_STATUSES

    def deposit_paid(self) -> bool:
        """定金是否已收到（退款口径据此区分「未收款直接关闭」与「原路退」。"""
        return self.deposit_paid_at is not None
