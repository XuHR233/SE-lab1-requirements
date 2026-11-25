"""权限矩阵（REQ-002）：先判角色，再判归属，两步都过才允许。"""

from __future__ import annotations

from typing import Any

from .models import ROLE_ADMIN, ROLE_GUEST, ROLE_MEMBER, ROLE_SUPPORT

ALLOWED_ROLES: dict[str, frozenset[str]] = {
    "product.browse": frozenset({ROLE_GUEST, ROLE_MEMBER, ROLE_SUPPORT, ROLE_ADMIN}),
    "reservation.create": frozenset({ROLE_MEMBER}),
    "reservation.view": frozenset({ROLE_MEMBER, ROLE_SUPPORT, ROLE_ADMIN}),
    "reservation.confirm": frozenset({ROLE_MEMBER}),
    "reservation.cancel": frozenset({ROLE_MEMBER}),
    "reservation.pay": frozenset({ROLE_MEMBER}),
    "order.pay": frozenset({ROLE_MEMBER}),
    "order.ship": frozenset({ROLE_MEMBER}),
    "order.receive": frozenset({ROLE_MEMBER}),
    "order.cancel": frozenset({ROLE_MEMBER}),
    "order.refund_request": frozenset({ROLE_MEMBER}),
    "product.create": frozenset({ROLE_MEMBER}),
    "product.edit": frozenset({ROLE_MEMBER}),
    "product.delete": frozenset({ROLE_MEMBER}),
    "product.audit": frozenset({ROLE_ADMIN}),
    "product.takedown": frozenset({ROLE_ADMIN}),
    "order.create": frozenset({ROLE_MEMBER}),
    "order.view": frozenset({ROLE_MEMBER, ROLE_SUPPORT, ROLE_ADMIN}),
    "order.refund": frozenset({ROLE_SUPPORT, ROLE_ADMIN}),
    "account.ban": frozenset({ROLE_ADMIN}),
    "account.view": frozenset({ROLE_MEMBER, ROLE_SUPPORT, ROLE_ADMIN}),
    "chat.view": frozenset({ROLE_MEMBER, ROLE_SUPPORT, ROLE_ADMIN}),
    "chat.send": frozenset({ROLE_MEMBER}),
    # M5 沟通与纠纷（REQ-018/019）：读工单给纠纷双方与客服/管理员，
    # 发起介入只给 member，接单与裁定只给客服/管理员。
    "dispute.read": frozenset({ROLE_SUPPORT, ROLE_ADMIN}),
    "dispute.raise": frozenset({ROLE_MEMBER}),
    "dispute.handle": frozenset({ROLE_SUPPORT, ROLE_ADMIN}),
    "dispute.escalate": frozenset({ROLE_SUPPORT}),
    "dispute.takeover": frozenset({ROLE_ADMIN}),
    # 客服工作台是纯后台功能，不进 member 的可用动作（读工单仍是 dispute.read）。
    "dispute.workbench": frozenset({ROLE_SUPPORT, ROLE_ADMIN}),
    "log.view": frozenset({ROLE_SUPPORT, ROLE_ADMIN}),
    "profile.view": frozenset({ROLE_MEMBER, ROLE_SUPPORT, ROLE_ADMIN}),
}

# 普通用户只能操作属于自己的数据；客服与管理员按职责放行。
OWNER_ONLY: frozenset[str] = frozenset(
    {
        "product.edit",
        "product.delete",
        "order.view",
        "order.pay",
        "order.ship",
        "order.receive",
        "order.refund_request",
        "reservation.view",
        "reservation.confirm",
        "reservation.pay",
        "account.view",
        "chat.view",
        "chat.send",
        "profile.view",
    }
)

# 管理员不得审核与自己有关的商品（REQ-002）。
SELF_EXCLUDED: frozenset[str] = frozenset({"product.audit"})

STAFF_ROLES: frozenset[str] = frozenset({ROLE_SUPPORT, ROLE_ADMIN})


def role_of(user: Any) -> str:
    """把请求里的用户映射成四类主体之一。"""
    if user is None or not getattr(user, "is_authenticated", False):
        return ROLE_GUEST
    return user.role


def allowed_by_role(user: Any, action: str) -> bool:
    """只判角色能不能碰这个动作，不看数据归属（REQ-002 第一步）。

    给「先做角色预检、再在用例层判当事人」的模块用：能进这个动作不表示能碰某条数据，
    当事人校验必须由调用方自己补上。
    """
    role = role_of(user)
    return action in ALLOWED_ROLES and role in ALLOWED_ROLES[action]


def can(user: Any, action: str, *, owner_id: int | None = None) -> bool:
    """判断能否执行动作。owner_id 是目标数据的归属人。"""
    role = role_of(user)
    if action not in ALLOWED_ROLES or role not in ALLOWED_ROLES[action]:
        return False
    if action in SELF_EXCLUDED and owner_id is not None and owner_id == user.pk:
        return False
    # 同一账号不能购买自己发布的商品（REQ-001），此处 owner_id 是卖家。
    if action in ("order.create", "reservation.create") and owner_id is not None:
        if owner_id == user.pk:
            return False
    if role not in STAFF_ROLES and action in OWNER_ONLY:
        return owner_id is not None and owner_id == user.pk
    return True
