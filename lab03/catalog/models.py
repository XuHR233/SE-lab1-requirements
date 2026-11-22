"""商品中心（M2）：分类、标签、商品、图片、审核记录。"""

from __future__ import annotations

from django.conf import settings
from django.db import models

DRAFT = "草稿"
PENDING = "待审核"
REJECTED = "已驳回"
PUBLISHED = "已发布"
PRESALE = "已预售"
LOCKED = "待付款锁定"
TRADING = "交易中"
SOLD = "已售出"
OFFSHELF = "已下架"
VIOLATED = "违规下架"
DELETED = "已删除"

PRODUCT_STATUSES: tuple[str, ...] = (
    DRAFT,
    PENDING,
    REJECTED,
    PUBLISHED,
    PRESALE,
    LOCKED,
    TRADING,
    SOLD,
    OFFSHELF,
    VIOLATED,
    DELETED,
)

STATUS_CHOICES = [(status, status) for status in PRODUCT_STATUSES]


class Category(models.Model):
    """商品分类，平台统一维护。"""

    name = models.CharField("名称", max_length=32, unique=True)
    sort = models.IntegerField("排序", default=0)

    class Meta:
        """按既定顺序展示。"""

        verbose_name = "分类"
        verbose_name_plural = "分类"
        ordering = ["sort", "id"]

    def __str__(self) -> str:
        return self.name


class Tag(models.Model):
    """平台标签，卖家只能选用不能自造（REQ-011）。"""

    name = models.CharField("标签", max_length=32, unique=True)

    class Meta:
        """标签按名称排列。"""

        verbose_name = "标签"
        verbose_name_plural = "标签"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Product(models.Model):  # pylint: disable=too-many-instance-attributes
    """商品主体。状态改动一律走 catalog/state.py 的 transit()。"""

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="products",
        verbose_name="卖家",
    )
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products", verbose_name="分类"
    )
    title = models.CharField("标题", max_length=120, db_index=True)
    brand = models.CharField("品牌", max_length=60, blank=True)
    description = models.TextField("描述", blank=True)
    price = models.DecimalField("价格", max_digits=10, decimal_places=2, db_index=True)
    is_presale = models.BooleanField("预售", default=False)
    deposit = models.DecimalField("定金金额", max_digits=10, decimal_places=2, default=0)
    final_amount = models.DecimalField("尾款金额", max_digits=10, decimal_places=2, default=0)
    presale_valid_days = models.PositiveSmallIntegerField("预约有效期（天）", default=0)
    status = models.CharField(
        "状态", max_length=16, choices=STATUS_CHOICES, default=DRAFT, db_index=True
    )
    version = models.PositiveIntegerField("版本号", default=1)
    view_count = models.PositiveIntegerField("浏览数", default=0)
    tags = models.ManyToManyField(Tag, blank=True, related_name="products", verbose_name="标签")
    created_at = models.DateTimeField("创建时间", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        """常用查询走状态与时间索引。"""

        verbose_name = "商品"
        verbose_name_plural = "商品"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["category", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.title}({self.status})"

    def has_images(self) -> bool:
        """提交审核要求至少一张图片（REQ-004）。"""
        return self.images.exists()

    def is_public(self) -> bool:
        """对外可见只包括已发布与已预售（REQ-003）。"""
        return self.status in (PUBLISHED, PRESALE)


class ProductImage(models.Model):
    """商品图片。本期只存对象存储地址，不做上传通道。"""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="images", verbose_name="商品"
    )
    url = models.CharField("地址", max_length=300)
    sort = models.IntegerField("排序", default=0)

    class Meta:
        """按排序值展示。"""

        verbose_name = "商品图片"
        verbose_name_plural = "商品图片"
        ordering = ["sort", "id"]

    def __str__(self) -> str:
        return self.url


class AuditRecord(models.Model):
    """一条审核流水：提交时开一条，审核时补结论，旧记录永久保留（REQ-005）。

    卖家撤回提交时这条流水记 withdrawn_at 关闭，不再算「待审」，
    否则撤回→重提会让库里留下永远开着、没人处理的旧流水。
    """

    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="audits", verbose_name="商品"
    )
    auditor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="审核人",
    )
    approve = models.BooleanField("是否通过", null=True, blank=True)
    reason = models.CharField("理由", max_length=200, blank=True)
    submitted_version = models.PositiveIntegerField("提交时版本号")
    snapshot = models.JSONField("提交时的内容快照", default=dict, blank=True)
    created_at = models.DateTimeField("提交时间", auto_now_add=True)
    audited_at = models.DateTimeField("审核时间", null=True, blank=True)
    withdrawn_at = models.DateTimeField("撤回时间", null=True, blank=True)

    class Meta:
        """按时间正序查看审核流水。"""

        verbose_name = "审核记录"
        verbose_name_plural = "审核记录"
        ordering = ["id"]

    def __str__(self) -> str:
        if self.withdrawn_at is not None:
            verdict = "撤回"
        elif self.approve is None:
            verdict = "-"
        else:
            verdict = "通过" if self.approve else "驳回"
        return f"#{self.product_id} {verdict}"
