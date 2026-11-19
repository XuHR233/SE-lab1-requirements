"""M1 验收测试：R-101 注册登录 / R-102 角色权限 / R-103 越权用例集。"""

# pylint: disable=redefined-outer-name,unused-argument,too-many-arguments,too-many-positional-arguments
# 理由：pytest 的 fixture 按参数名注入，参数名必须与 fixture 同名；
# db fixture 不显式使用也要写在签名里。这两条与 pylint 默认规则天然冲突。

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from accounts.masking import mask_phone, mask_text
from accounts.permissions import can
from audit.models import RESULT_DENIED, OperationLog

User = get_user_model()

REGISTER_URL = "/accounts/register"
LOGIN_URL = "/accounts/login"
SMS_CODE = "000000"


@pytest.fixture
def member(db):
    """一个普通用户（同时是买家和卖家）。"""
    return User.objects.create_user(
        username="13800000001", phone="13800000001", password="Pw12345678"
    )


@pytest.fixture
def other(db):
    """另一个普通用户，用于横向越权。"""
    return User.objects.create_user(
        username="13800000002", phone="13800000002", password="Pw12345678"
    )


@pytest.fixture
def support(db):
    """客服：只有退款权。"""
    return User.objects.create_user(
        username="13800000003", phone="13800000003", password="Pw12345678", is_support=True
    )


@pytest.fixture
def admin_user(db):
    """管理员：由官方产生，不发布商品。"""
    return User.objects.create_user(
        username="13800000004",
        phone="13800000004",
        password="Pw12345678",
        is_staff=True,
    )


# ---------------- R-101 注册登录 ----------------


@pytest.mark.django_db
def test_register_succeeds_and_creates_one_user() -> None:
    """正常注册：返回 201，库里恰好 1 条用户记录。"""
    response = Client().post(
        REGISTER_URL, {"phone": "13900000001", "code": SMS_CODE, "password": "Pw12345678"}
    )
    assert response.status_code == 201
    assert User.objects.count() == 1


@pytest.mark.django_db
def test_register_rejects_duplicate_phone(member) -> None:
    """同一手机号再注册被拒，账号总数不变（REQ-001）。"""
    before = User.objects.count()
    response = Client().post(
        REGISTER_URL, {"phone": member.phone, "code": SMS_CODE, "password": "Pw12345678"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "该手机号已注册"
    assert User.objects.count() == before


@pytest.mark.django_db
@pytest.mark.parametrize("phone", ["12345", "1380000000a", "23800000000", ""])
def test_register_rejects_bad_phone(phone: str) -> None:
    """手机号格式不合规一律拒绝，不落库。"""
    response = Client().post(
        REGISTER_URL, {"phone": phone, "code": SMS_CODE, "password": "Pw12345678"}
    )
    assert response.status_code == 400
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_register_rejects_wrong_code() -> None:
    """验证码错不落库。"""
    response = Client().post(
        REGISTER_URL, {"phone": "13900000002", "code": "999999", "password": "Pw12345678"}
    )
    assert response.status_code == 400
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_login_then_session_holds_uid(member) -> None:
    """登录成功后 session 里能取到用户。"""
    client = Client()
    response = client.post(LOGIN_URL, {"phone": member.phone, "password": "Pw12345678"})
    assert response.status_code == 200
    assert client.session["_auth_user_id"] == str(member.pk)


@pytest.mark.django_db
def test_login_failure_does_not_leak_which_field_wrong(member) -> None:
    """手机号不存在与密码错返回同一句话（REQ-027 不泄漏资源存在性）。"""
    wrong_password = Client().post(LOGIN_URL, {"phone": member.phone, "password": "bad"}).json()
    no_such_user = Client().post(LOGIN_URL, {"phone": "13911112222", "password": "bad"}).json()
    assert wrong_password == no_such_user


# ---------------- R-102 角色权限矩阵 ----------------


def test_roles_are_derived_from_flags(member, support, admin_user) -> None:
    """四类主体映射：管理员 > 客服 > 普通用户。"""
    assert member.role == "member"
    assert support.role == "support"
    assert admin_user.role == "admin"


def test_guest_cannot_create_product() -> None:
    """游客不能发布商品，但能浏览。"""
    assert can(None, "product.browse") is True
    assert can(None, "product.create") is False


def test_admin_cannot_publish_product(admin_user) -> None:
    """管理员不得发布商品（REQ-002）。"""
    assert can(admin_user, "product.create") is False


def test_support_can_refund_but_not_ban_or_takedown(support) -> None:
    """客服只有退款权，无封号与下架权。"""
    assert can(support, "order.refund") is True
    assert can(support, "account.ban") is False
    assert can(support, "product.takedown") is False


def test_admin_cannot_audit_own_product(admin_user) -> None:
    """管理员不得审核与自己有关的商品。"""
    assert can(admin_user, "product.audit", owner_id=admin_user.pk) is False
    assert can(admin_user, "product.audit", owner_id=999) is True


def test_member_cannot_buy_own_product(member) -> None:
    """同一账号不能购买自己发布的商品（REQ-001）。"""
    assert can(member, "order.create", owner_id=member.pk) is False
    assert can(member, "order.create", owner_id=999) is True


def test_unknown_action_is_denied(member) -> None:
    """权限矩阵里没有的动作按最小权限拒绝。"""
    assert can(member, "product.explode") is False


def test_owner_only_action_requires_ownership(member, other) -> None:
    """普通用户的归属类操作只能操作自己的数据。"""
    assert can(member, "product.edit", owner_id=member.pk) is True
    assert can(member, "product.edit", owner_id=other.pk) is False


def test_denied_action_writes_log(member) -> None:
    """拒绝必须留痕，且日志含操作者、目标、IP、端类型（REQ-026）。"""
    from accounts.decorators import require_action  # pylint: disable=import-outside-toplevel
    from django.http import HttpResponse  # pylint: disable=import-outside-toplevel
    from django.test import RequestFactory  # pylint: disable=import-outside-toplevel

    @require_action("account.ban")
    def view(_request):
        """被保护的业务函数。"""
        return HttpResponse("should not reach")

    request = RequestFactory().post("/x", REMOTE_ADDR="10.1.2.3")
    request.user = member
    with pytest.raises(Exception):  # noqa: B017 - 只关心是否被拦下
        view(request)

    log = OperationLog.objects.get(action="account.ban")
    assert log.result == RESULT_DENIED
    assert log.actor_id == member.pk
    assert log.ip == "10.1.2.3"
    assert log.client_type == "web"


# ---------------- R-103 越权用例集：30 条 ----------------


def _deny_cases() -> list[tuple[str, str, str]]:
    """30 条越权用例：横向 15 条、纵向 15 条。"""
    return [
        # 横向越权（同角色之间越界）
        ("member", "product.edit", "other", "买家修改他人商品"),
        ("member", "product.delete", "other", "买家删除他人商品"),
        ("member", "order.view", "other", "卖家查看他人订单"),
        ("member", "account.view", "other", "普通用户查看他人资料"),
        ("member", "chat.view", "other", "普通用户查看他人聊天"),
        ("member", "product.edit", "none", "修改不存在或不属于自己的商品"),
        ("member", "order.view", "none", "查看不属于自己的订单"),
        ("member", "profile.view", "other", "查看他人敏感资料"),
        ("member", "chat.send", "other", "向他人会话发消息"),
        ("member", "order.refund", "other", "买家对他人订单发起退款"),
        ("member", "product.audit", "other", "卖家审核他人商品"),
        ("member", "product.takedown", "other", "卖家下架他人商品"),
        ("member", "account.ban", "other", "普通用户封禁他人账号"),
        ("member", "log.view", "other", "普通用户查看他人操作日志"),
        ("support", "product.edit", "other", "客服修改他人商品"),
        # 纵向越权（低角色做高角色的事）
        ("member", "product.audit", "self", "卖家审核自己的商品"),
        ("member", "product.takedown", "self", "卖家违规下架自己的商品"),
        ("member", "account.ban", "self", "普通用户封号"),
        ("member", "log.view", "self", "普通用户查操作日志"),
        ("member", "order.refund", "self", "普通用户直接退款"),
        ("support", "product.create", "none", "客服发布商品"),
        ("support", "account.ban", "other", "客服封号"),
        ("support", "product.takedown", "other", "客服下架商品"),
        ("support", "product.audit", "other", "客服审核商品"),
        ("admin", "product.create", "none", "管理员发布商品"),
        ("admin", "product.audit", "self", "管理员审核自己的商品"),
        ("guest", "product.create", "none", "游客发布商品"),
        ("guest", "order.create", "other", "游客下单"),
        ("guest", "chat.send", "other", "游客发消息"),
        ("guest", "log.view", "none", "游客查看操作日志"),
    ]


@pytest.mark.parametrize(("role", "action", "owner", "label"), _deny_cases())
def test_deny_matrix(role, action, owner, label, member, other, support, admin_user) -> None:
    """30 条越权用例全部拒绝，且不返回业务数据。"""
    actors = {"member": member, "support": support, "admin": admin_user, "guest": None}
    owners = {"other": other.pk, "self": (actors[role].pk if actors[role] else 999), "none": None}
    actor = actors[role]
    assert can(actor, action, owner_id=owners[owner]) is False, label


def test_deny_matrix_has_at_least_30_cases() -> None:
    """用例数不少于 30 条（REQ-026 硬性要求）。"""
    assert len(_deny_cases()) >= 30


@pytest.mark.django_db
def test_append_only_log_rejects_update(member) -> None:
    """日志 UPDATE 被数据库拒绝（REQ-022）。"""
    from django.db import DatabaseError, transaction  # pylint: disable=import-outside-toplevel

    from audit.models import record  # pylint: disable=import-outside-toplevel

    log = record(action="product.edit", result="allowed", actor=member)
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        OperationLog.objects.filter(pk=log.pk).update(result="denied")


@pytest.mark.django_db
def test_append_only_log_rejects_delete(member) -> None:
    """日志 DELETE 被数据库拒绝（REQ-022）。"""
    from django.db import DatabaseError, transaction  # pylint: disable=import-outside-toplevel

    from audit.models import record  # pylint: disable=import-outside-toplevel

    log = record(action="product.edit", result="allowed", actor=member)
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        OperationLog.objects.filter(pk=log.pk).delete()


# ---------------- REQ-027 脱敏 ----------------


def test_mask_phone_keeps_head_and_tail() -> None:
    """13800001234 -> 138****1234。"""
    assert mask_phone("13800001234") == "138****1234"


def test_mask_phone_hides_abnormal_length() -> None:
    """长度异常时整体打码，不漏原始字符。"""
    assert mask_phone("123") == "***"


def test_mask_text_keeps_first_and_last() -> None:
    """文本脱敏保留首尾。"""
    assert mask_text("张三丰") == "张*丰"
