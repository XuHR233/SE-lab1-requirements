"""账号模型：一个账号同时是买家和卖家（REQ-001）。"""

from django.contrib.auth.models import AbstractUser
from django.db import models

ROLE_GUEST = "guest"
ROLE_MEMBER = "member"
ROLE_SUPPORT = "support"
ROLE_ADMIN = "admin"


class User(AbstractUser):
    """统一账号。手机号唯一；角色由能力位推导，不是独占枚举。"""

    phone = models.CharField("手机号", max_length=11, unique=True)
    is_support = models.BooleanField("客服", default=False)

    class Meta:
        """模型元信息。"""

        verbose_name = "用户"
        verbose_name_plural = "用户"

    @property
    def role(self) -> str:
        """四类主体：管理员 > 客服 > 普通用户（买家/卖家双身份）。"""
        if self.is_staff:
            return ROLE_ADMIN
        if self.is_support:
            return ROLE_SUPPORT
        return ROLE_MEMBER

    def __str__(self) -> str:
        return f"{self.username}({self.phone})"
