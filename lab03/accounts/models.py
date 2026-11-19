"""账号模型：一个账号同时是买家和卖家（REQ-001）。"""

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """统一账号。手机号唯一：一个手机号只能注册一个账号。"""

    phone = models.CharField("手机号", max_length=11, unique=True)
    is_support = models.BooleanField("客服", default=False)

    class Meta:
        """模型元信息。"""

        verbose_name = "用户"
        verbose_name_plural = "用户"

    def __str__(self) -> str:
        return f"{self.username}({self.phone})"
