"""沟通与纠纷的模型：会话、消息、纠纷提示与工单（REQ-018/019）。

App 名是 `communication`，因为 `chat` 这个名字已经被 M1 的权限动作占用
（`chat.view` / `chat.send`），同名 app 会让 `can()` 的口径更容易看混。
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from .api import (
    ORDER_COMPLETED,
    ORDER_DISPUTING,
    ORDER_WAITING_RECEIPT,
    ORDER_WAITING_SHIP,
    Order,
    Product,
)

# 一张订单处于这些状态时允许发起纠纷介入（REQ-019 前置「存在进行中的订单」）。
# 待发货与待收货覆盖了「未发货裁定退款 / 已发货裁定终止」两种裁定之前的状态，
# 也覆盖纠纷被驳回后恢复履约的两种落点。
OPEN_ORDER_STATUSES: tuple[str, ...] = (
    ORDER_WAITING_SHIP,
    ORDER_WAITING_RECEIPT,
    ORDER_DISPUTING,
    ORDER_COMPLETED,
)

# 客服裁定的两种结论（REQ-019：未发货裁定退款、已发货裁定终止）。
VERDICT_REFUND = "退款"
VERDICT_TERMINATE = "终止"
VERDICTS: tuple[str, ...] = (VERDICT_REFUND, VERDICT_TERMINATE)

# 纠纷工单状态（REQ-019）。
DISPUTE_OPEN = "待处理"
DISPUTE_CLOSED = "已处理"
DISPUTE_STATUSES: tuple[str, ...] = (DISPUTE_OPEN, DISPUTE_CLOSED)


class Conversation(models.Model):
    """买家与卖家就某个商品的一对一会话。同一对账号 + 同一商品只允许一条（REQ-018）。"""

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="chats",
        verbose_name="商品",
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="chats",
        verbose_name="买家",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="chats_as_seller",
        verbose_name="卖家",
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chats",
        verbose_name="关联订单",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    last_message_at = models.DateTimeField("最后一条消息时间", null=True, blank=True)

    class Meta:
        """同一买卖双方就同一商品复用同一个会话。"""

        verbose_name = "会话"
        verbose_name_plural = "会话"
        constraints = [
            models.UniqueConstraint(
                fields=["product", "buyer", "seller"], name="ux_conversation_parties"
            )
        ]


class Message(models.Model):
    """会话内的一条消息：文字或图片。没有删除接口，只能本地隐藏（REQ-018）。"""

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="会话",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="messages",
        verbose_name="发送者",
    )
    text = models.TextField("文字", blank=True, default="")
    staff_role = models.CharField("工作人员身份快照", max_length=10, blank=True, default="")
    # 图片只存对象存储/本地媒体目录的地址，不依赖 Pillow 的 ImageField。
    image = models.URLField("图片地址", max_length=500, blank=True, default="")
    created_at = models.DateTimeField("发送时间", auto_now_add=True)

    class Meta:
        """消息按时间顺序读取。"""

        verbose_name = "消息"
        verbose_name_plural = "消息"
        ordering = ("created_at", "id")


class MessageHide(models.Model):
    """本地隐藏：只是「这个人自己看不见」，消息本身不动（REQ-018 不可单方面删除）。"""

    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="hides",
        verbose_name="消息",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hidden_messages",
        verbose_name="隐藏者",
    )
    created_at = models.DateTimeField("隐藏时间", auto_now_add=True)

    class Meta:
        """同一条消息对同一个人只有一条隐藏记录。"""

        verbose_name = "本地隐藏"
        verbose_name_plural = "本地隐藏"
        constraints = [
            models.UniqueConstraint(fields=["message", "user"], name="ux_message_hide")
        ]


class DisputeSignal(models.Model):
    """纠纷识别只做提示，不自动判定责任、不自动退款（REQ-019）。"""

    KIND_KEYWORD = "关键词"
    KIND_STALLED = "长时间无响应"
    KIND_REPEATED_REFUND = "重复退款申请"
    KINDS: tuple[str, ...] = (KIND_KEYWORD, KIND_STALLED, KIND_REPEATED_REFUND)

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="signals",
        verbose_name="会话",
    )
    kind = models.CharField("类型", max_length=20, choices=[(k, k) for k in KINDS])
    reason = models.CharField("命中理由", max_length=200)
    ignored_at = models.DateTimeField("用户忽略时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        """同一会话同一类型只提示一次。"""

        verbose_name = "纠纷提示"
        verbose_name_plural = "纠纷提示"
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "kind"], name="ux_dispute_signal_kind"
            )
        ]


class Dispute(models.Model):
    """纠纷工单：同一订单只允许一条（REQ-019）。"""

    RAISED_USER = "用户"
    RAISED_SYSTEM = "系统"

    order = models.OneToOneField(
        Order,
        on_delete=models.PROTECT,
        related_name="dispute",
        verbose_name="订单",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="disputes",
        verbose_name="会话",
    )
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="disputes",
        verbose_name="发起人",
    )
    raised_source = models.CharField("发起方式", max_length=10, default=RAISED_USER)
    status = models.CharField(
        "状态", max_length=10, default=DISPUTE_OPEN, choices=[(s, s) for s in DISPUTE_STATUSES]
    )
    reason = models.CharField("介入原因", max_length=200, blank=True, default="")
    verdict = models.CharField("裁定结论", max_length=10, blank=True, default="")
    note = models.TextField("处理说明", blank=True, default="")
    handler = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handled_disputes",
        verbose_name="处理人",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    closed_at = models.DateTimeField("处理时间", null=True, blank=True)
    escalated_at = models.DateTimeField("提交管理员时间", null=True, blank=True)
    escalation_reason = models.CharField("转交原因", max_length=200, blank=True, default="")

    class Meta:
        """同一订单重复请求介入只创建一条工单。"""

        verbose_name = "纠纷工单"
        verbose_name_plural = "纠纷工单"

    @property
    def is_open(self) -> bool:
        """工单是否还在处理中。"""
        return self.status == DISPUTE_OPEN
