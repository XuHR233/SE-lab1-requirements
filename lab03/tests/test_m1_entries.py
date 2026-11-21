"""R-103 越权用例的真实入口版（REQ-026）。

矩阵测试只能证明 `can()` 的判断对；这一组改成打 HTTP 视图，逐条证明四件事：
真实入口会调权限检查、响应是 403 且不含商品内容、业务没执行（状态与版本没动）、
每次拒绝都写了一条 denied 操作日志。
"""

# pylint: disable=redefined-outer-name,unused-argument,too-many-arguments,too-many-positional-arguments

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from audit.models import RESULT_DENIED, OperationLog
from catalog.models import (
    DRAFT,
    PENDING,
    PUBLISHED,
    SOLD,
    TRADING,
    Category,
    Product,
    ProductImage,
)

TARGET_TITLE = "越权目标相机"

# 每个动作配一份「如果权限放行就足以造成后果」的请求体。
PAYLOADS = {
    "submit": {},
    "update": {"title": "被改掉的标题", "price": "0.01"},
    "images": {"url": "https://img.example.com/evil.jpg"},
    "revise": {"version": 1, "price": "0.01"},
    "audit": {"approve": True},
    "unpublish": {},
    "withdraw": {},
    "takedown": {"reason": "越权下架"},
    "terminate": {"reason": "越权终止"},
    "delete": {},
}

# 商品必须先在合法状态，拒绝才只能来自权限而不是状态前置。
STATUS_FOR = {
    "submit": DRAFT,
    "update": DRAFT,
    "images": DRAFT,
    "delete": DRAFT,
    "audit": PENDING,
    "withdraw": PENDING,
    "revise": PUBLISHED,
    "unpublish": PUBLISHED,
    "takedown": PUBLISHED,
    "terminate": TRADING,
}

# 只允许「卖家本人」的动作（REQ-002 第二重：数据是否属于本人）。
EDIT_ACTIONS = ("submit", "update", "images", "delete", "withdraw", "revise", "unpublish")
DENIED_ROLES = ("guest", "support", "admin", "other")

DENY_CASES = [(role, action) for action in EDIT_ACTIONS for role in DENIED_ROLES] + [
    ("guest", "audit"),
    ("support", "audit"),
    ("other", "audit"),
    ("member", "takedown"),
    ("guest", "takedown"),
    ("support", "takedown"),
    ("guest", "terminate"),
    ("other", "terminate"),
]


def _build(seller, category, status, title: str = TARGET_TITLE) -> Product:
    """直接建一个指定状态的商品；越权用例不关心它是怎么走到这个状态的。"""
    product = Product.objects.create(
        seller=seller,
        category=category,
        title=title,
        brand="尼康",
        description="九成新，功能正常",
        price=Decimal("88.00"),
        status=status,
    )
    if status != DRAFT:
        ProductImage.objects.create(product=product, url="https://img.example.com/a.jpg")
    return product


def _post(client: Client, product: Product, action: str, payload: dict):
    """打商品的写操作入口，返回响应。"""
    return client.post(
        f"/api/products/{product.pk}/{action}/",
        data=json.dumps(payload),
        content_type="application/json",
    )


@pytest.fixture
def category(db):
    """一个可用分类。"""
    return Category.objects.create(name="数码")


@pytest.fixture
def actors(db):
    """六种身份：游客(None)、普通会员、另一会员、卖家、管理员、客服。"""
    user = get_user_model()
    return {
        "guest": None,
        "member": user.objects.create_user(
            username="13800000001", phone="13800000001", password="pw12345678"
        ),
        "other": user.objects.create_user(
            username="13800000002", phone="13800000002", password="pw12345678"
        ),
        "seller": user.objects.create_user(
            username="13800000003", phone="13800000003", password="pw12345678"
        ),
        "admin": user.objects.create_user(
            username="13800000004", phone="13800000004", password="pw12345678", is_staff=True
        ),
        "support": user.objects.create_user(
            username="13800000005", phone="13800000005", password="pw12345678", is_support=True
        ),
    }


@pytest.mark.parametrize("role,action", DENY_CASES)
def test_denied_case_blocks_business_and_logs(client, actors, category, role, action):
    """越权请求走真实视图：403、一条 denied 日志、商品状态与版本不变。"""
    product = _build(actors["seller"], category, STATUS_FOR[action])
    actor = actors[role]
    if actor is not None:
        client.force_login(actor)
    logs_before = OperationLog.objects.count()

    response = _post(client, product, action, PAYLOADS[action])

    assert response.status_code == 403
    assert TARGET_TITLE not in response.content.decode()
    assert OperationLog.objects.count() == logs_before + 1
    log = OperationLog.objects.order_by("-id").first()
    assert log is not None and log.result == RESULT_DENIED

    product.refresh_from_db()
    assert (product.status, product.version, product.title) == (
        STATUS_FOR[action],
        1,
        TARGET_TITLE,
    )


@pytest.mark.parametrize("role", ("guest", "support", "admin"))
def test_cannot_create_product_through_view(client, actors, category, role):
    """游客、客服、管理员都不能发布商品，且没有留下任何商品行。"""
    actor = actors[role]
    if actor is not None:
        client.force_login(actor)
    before = Product.objects.count()
    response = client.post(
        "/api/products/",
        data=json.dumps({"title": "越权新建", "price": "10.00", "category_id": category.pk}),
        content_type="application/json",
    )
    assert response.status_code == 403
    assert Product.objects.count() == before
    assert OperationLog.objects.filter(result=RESULT_DENIED).exists()


def test_self_audit_is_denied_through_view(client, actors, category):
    """管理员不能审核自己发布的商品（REQ-002 自审排除）。"""
    product = _build(actors["admin"], category, PENDING)
    client.force_login(actors["admin"])
    response = _post(client, product, "audit", {"approve": True})
    assert response.status_code == 403
    product.refresh_from_db()
    assert product.status == PENDING


def test_seller_submits_and_admin_approves_through_views(client, actors, category):
    """正常链路：卖家建草稿→传图→提交，管理员审核通过，证明入口不是一律拒绝。"""
    client.force_login(actors["seller"])
    created = client.post(
        "/api/products/",
        data=json.dumps({"title": "自用镜头", "price": "88.00", "category_id": category.pk}),
        content_type="application/json",
    )
    assert created.status_code == 201
    product = Product.objects.get(pk=created.json()["id"])

    uploaded = _post(client, product, "images", {"url": "https://img.example.com/l.jpg"})
    assert uploaded.status_code == 200
    assert _post(client, product, "submit", {}).status_code == 200
    product.refresh_from_db()
    assert product.status == PENDING

    client.force_login(actors["admin"])
    approved = _post(client, product, "audit", {"approve": True})
    assert approved.status_code == 200
    product.refresh_from_db()
    assert product.status == PUBLISHED


def test_list_hides_sold_for_every_role(client, actors, category):
    """列表口径与角色无关，已售出对谁都不可见（REQ-012）。"""
    for seller_key in ("seller", "other"):
        _build(actors[seller_key], category, PUBLISHED, title=f"在售{seller_key}")
        _build(actors[seller_key], category, SOLD, title=f"已售{seller_key}")
    for role in ("guest", "member", "admin", "support"):
        actor = actors[role]
        if actor is not None:
            client.force_login(actor)
        response = client.get("/api/products/")
        assert response.status_code == 200
        titles = [row["title"] for row in response.json()["items"]]
        assert "已售seller" not in titles
        assert "已售other" not in titles
        assert "在售seller" in titles


def test_detail_hides_other_users_draft(client, actors, category):
    """别人的草稿当不存在（404），本人能打开自己的草稿。"""
    product = _build(actors["seller"], category, DRAFT)
    client.force_login(actors["other"])
    assert client.get(f"/api/products/{product.pk}/").status_code == 404
    client.force_login(actors["seller"])
    assert client.get(f"/api/products/{product.pk}/").status_code == 200


def test_stale_revise_is_409_through_view(client, actors, category):
    """两个编辑页同时改稿：旧版本号的请求 409，库里的新内容不被覆盖。"""
    product = _build(actors["seller"], category, PUBLISHED)
    client.force_login(actors["seller"])

    first = _post(client, product, "revise", {"version": 1, "price": "99.00"})
    assert first.status_code == 200
    product.refresh_from_db()
    assert (product.version, product.status, str(product.price)) == (2, PENDING, "99.00")

    second = _post(client, product, "revise", {"version": 1, "price": "1.00"})
    assert second.status_code == 409
    product.refresh_from_db()
    assert (product.version, str(product.price)) == (2, "99.00")
