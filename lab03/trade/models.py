"""交易与资金模型（M4）：订单、支付、退款、结算（REQ-013~017）。"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from catalog.models import Product
from reservations.models import Reservation

WAITING_PAY = "待付款"
WAITING_SHIP = "待发货"
WAITING_RECEIPT = "待收货"
COMPLETED = "已完成"
REFUNDED = "已退款"
CANCELED = "已取消"
DISPUTING = "纠纷中"

ORDER_STATUSES: tuple[str, ...] = (
    WAITING_PAY,
    WAITING_SHIP,
    WAITING_RECEIPT,
    COMPLETED,
    REFUNDED,
    CANCELED,
    DISPUTING,
)

# 有效订单：占着这件货，同一商品同时最多一笔（REQ-013）。
ACTIVE_STATUSES: tuple[str, ...] = (WAITING_PAY, WAITING_SHIP, WAITING_RECEIPT, DISPUTING)

ORDER_STATUS_CHOICES = [(status, status) for status in ORDER_STATUSES]

KIND_ORDER = "订单支付"
KIND_DEPOSIT = "定金"
KIND_FINAL = "尾款"
PAYMENT_KINDS: tuple[str, ...] = (KIND_ORDER, KIND_DEPOSIT, KIND_FINAL)

RESULT_OK = "成功"
RESULT_LATE = "迟到退回"


class Order(models.Model):  # pylint: disable=too-many-instance-attributes
    """一笔订单。普通订单下单即锁货；预售订单由尾款支付后生成（REQ-006/013）。"""

    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="orders", verbose_name="商品"
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="买家",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="卖家",
    )
    source_reservation = models.OneToOneField(
        Reservation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="order",
        verbose_name="来源预约",
    )
    status = models.CharField(
        "状态", max_length=16, choices=ORDER_STATUS_CHOICES, default=WAITING_PAY, db_index=True
    )
    amount = models.DecimalField("成交额", max_digits=10, decimal_places=2)
    deposit_amount = models.DecimalField(
        "其中定金", max_digits=10, decimal_places=2, default=Decimal("0")
    )
    final_amount = models.DecimalField(
        "其中尾款", max_digits=10, decimal_places=2, default=Decimal("0")
    )
    version = models.PositiveIntegerField("版本号", default=1)
    request_key = models.CharField("请求标识", max_length=64, blank=True)
    expires_at = models.DateTimeField("支付截止时间", null=True, blank=True)
    auto_confirm_at = models.DateTimeField("自动确认收货时间", null=True, blank=True)
    paid_at = models.DateTimeField("支付时间", null=True, blank=True)
    shipped_at = models.DateTimeField("发货时间", null=True, blank=True)
    received_at = models.DateTimeField("收货时间", null=True, blank=True)
    tracking_no = models.CharField("运单号", max_length=60, blank=True)
    close_reason = models.CharField("关闭原因", max_length=200, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True, db_index=True)

    class Meta:
        """同一商品同时最多一笔有效订单，由部分唯一索引兜住并发。"""

    # pylint: disable=duplicate-code
    # 理由：与 reservations.models.Reservation 是同一条「同一商品只允许一条有效单据」的约束，
    # 只是约束名与状态集合不同；抽公共函数要在 Meta 里跨模块调用，反而更难读。

        verbose_name = "订单"
        verbose_name_plural = "订单"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product"],
                condition=Q(status__in=ACTIVE_STATUSES),
                name="ux_order_active_per_product",
            )
        ]
        indexes = [
            models.Index(fields=["buyer", "status"]),
            models.Index(fields=["seller", "status"]),
        ]

    def __str__(self) -> str:
        return f"订单#{self.pk} {self.product_id} {self.status}"

    @property
    def is_presale(self) -> bool:
        """预售订单的自动确认与退款恢复口径都跟普通订单不同（REQ-016）。"""
        return self.source_reservation_id is not None


class Payment(models.Model):
    """一条支付流水。支付流水号唯一，重复回调据此幂等（REQ-014）。"""

    order = models.ForeignKey(
        Order, null=True, blank=True, on_delete=models.PROTECT, related_name="payments"
    )
    reservation = models.ForeignKey(
        Reservation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    kind = models.CharField("款项类型", max_length=16, choices=[(k, k) for k in PAYMENT_KINDS])
    txn_no = models.CharField("支付流水号", max_length=64, unique=True)
    amount = models.DecimalField("金额", max_digits=10, decimal_places=2)
    result = models.CharField(
        "结果", max_length=16, choices=[(RESULT_OK, RESULT_OK), (RESULT_LATE, RESULT_LATE)]
    )
    paid_at = models.DateTimeField("支付时间")

    class Meta:
        """按时间查看流水。"""

        verbose_name = "支付流水"
        verbose_name_plural = "支付流水"
        ordering = ["-paid_at", "-id"]

    def __str__(self) -> str:
        return f"{self.kind} {self.txn_no} {self.amount}"


class Refund(models.Model):
    """一笔退款：未发货全额退款，或关闭后迟到款项原路退回（REQ-014/016）。"""

    order = models.ForeignKey(
        Order, null=True, blank=True, on_delete=models.PROTECT, related_name="refunds"
    )
    reservation = models.ForeignKey(
        Reservation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="refunds",
    )
    amount = models.DecimalField("退款金额", max_digits=10, decimal_places=2)
    reason = models.CharField("原因", max_length=200)
    created_at = models.DateTimeField("退款时间", auto_now_add=True)

    class Meta:
        """按时间查看退款。"""

        verbose_name = "退款"
        verbose_name_plural = "退款"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"退款 {self.amount} {self.reason}"


class Settlement(models.Model):
    """一笔结算：手续费 1.5%（0.6% 通道费 + 0.9% 平台费），封顶 200（REQ-017）。"""

    order = models.OneToOneField(
        Order, on_delete=models.PROTECT, related_name="settlement", verbose_name="订单"
    )
    gross = models.DecimalField("成交额", max_digits=10, decimal_places=2)
    fee = models.DecimalField("手续费", max_digits=10, decimal_places=2)
    channel_fee = models.DecimalField("通道费", max_digits=10, decimal_places=2)
    platform_fee = models.DecimalField("平台服务费", max_digits=10, decimal_places=2)
    seller_net = models.DecimalField("卖家实收", max_digits=10, decimal_places=2)
    created_at = models.DateTimeField("结算时间", auto_now_add=True)
    paid_out_at = models.DateTimeField("放款时间", null=True, blank=True)

    class Meta:
        """按时间查看结算。"""

        verbose_name = "结算记录"
        verbose_name_plural = "结算记录"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"结算#{self.pk} {self.gross} 手续费 {self.fee}"
