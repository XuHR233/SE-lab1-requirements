"""M3/M4 入口层验收：真实 HTTP 入口的完整链路 + 越权矩阵（REQ-006/013~017/026）。

与 test_m1_entries.py 同一口径：先让对象处在合法状态，拒绝只能来自权限或当事人身份；
每条越权用例都断言 403、留 denied 日志、对象未被改动、响应不泄露商品标题。
"""

# pylint: disable=redefined-outer-name,unused-argument,too-many-arguments
# pylint: disable=too-many-positional-arguments,too-many-lines,too-many-locals
# 理由：pytest fixture 按参数名注入；越权矩阵按 (动作, 身份) 展开更贴近需求卡的写法。

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import Client

from audit.models import RESULT_ALLOWED, RESULT_DENIED, OperationLog
from catalog.models import PUBLISHED, SOLD, TRADING
from reservations.models import AWAITING_SELLER, COMPLETED
from tests.helpers import build_order, build_reservation, plain_product, presale_product
from trade.models import WAITING_PAY as ORDER_WAITING_PAY
from trade.models import WAITING_RECEIPT as ORDER_WAITING_RECEIPT
from trade.models import WAITING_SHIP as ORDER_WAITING_SHIP
from trade.models import Order, Settlement

pytestmark = pytest.mark.django_db


def api(client: Client, method: str, url: str, body: dict | None = None):
    """用 JSON 打一个入口，返回状态码与解析后的响应体。"""
    if method == "GET":
        response = client.get(url)
    else:
        response = client.post(
            url, data=json.dumps(body or {}), content_type="application/json"
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response.status_code, payload


def as_user(user) -> Client:
    """以指定身份登录的测试客户端；游客不登录。"""
    client = Client()
    if user.is_authenticated:
        client.force_login(user)
    return client


TARGET_TITLE = "预售相机"

DENY_CASES = [
    # 预约：建预约
    ("reserve", "guest", "reservation.create"),
    ("reserve", "support", "reservation.create"),
    ("reserve", "admin", "reservation.create"),
    ("reserve", "seller", "reservation.create"),
    # 预约：付定金
    ("pay_deposit", "guest", "reservation.pay"),
    ("pay_deposit", "support", "reservation.pay"),
    ("pay_deposit", "other", "reservation.pay"),
    ("pay_deposit", "seller", "reservation.pay"),
    # 预约：卖家确认 / 拒绝 / 确认到货
    ("confirm", "guest", "reservation.confirm"),
    ("confirm", "buyer", "reservation.confirm"),
    ("confirm", "other", "reservation.confirm"),
    ("confirm", "admin", "reservation.confirm"),
    ("reject", "buyer", "reservation.confirm"),
    ("reject", "other", "reservation.confirm"),
    ("arrive", "buyer", "reservation.confirm"),
    ("arrive", "support", "reservation.confirm"),
    # 预约：付尾款 / 取消
    ("pay_final", "guest", "reservation.pay"),
    ("pay_final", "seller", "reservation.pay"),
    ("pay_final", "other", "reservation.pay"),
    ("cancel", "guest", "reservation.cancel"),
    ("cancel", "other", "reservation.cancel"),
    ("cancel", "support", "reservation.cancel"),
    # 订单：下单
    ("order_create", "guest", "order.create"),
    ("order_create", "support", "order.create"),
    ("order_create", "admin", "order.create"),
    ("order_create", "seller", "order.create"),
    # 订单：支付 / 取消 / 发货 / 收货 / 退款
    ("order_pay", "guest", "order.pay"),
    ("order_pay", "seller", "order.pay"),
    ("order_pay", "other", "order.pay"),
    ("order_cancel", "guest", "order.cancel"),
    ("order_cancel", "other", "order.cancel"),
    ("order_ship", "buyer", "order.ship"),
    ("order_ship", "admin", "order.ship"),
    ("order_receive", "seller", "order.receive"),
    ("order_receive", "other", "order.receive"),
    ("order_refund", "guest", "order.refund_request"),
    ("order_refund", "other", "order.refund_request"),
]

CASE_STEPS = {
    "pay_deposit": "待付定金",
    "confirm": "待确认",
    "reject": "待确认",
    "arrive": "待到货",
    "pay_final": "待付尾款",
    "cancel": "待付尾款",
}

ORDER_STEPS = {
    "order_pay": "待付款",
    "order_cancel": "待付款",
    "order_ship": "待发货",
    "order_receive": "待收货",
    "order_refund": "待发货",
}


@pytest.mark.parametrize(("case", "actor_name", "expected_action"), DENY_CASES)
def test_deny_matrix(
    case,
    actor_name,
    expected_action,
    request,
    category,
    seller,
    buyer,
    other,
    admin,
    support,
):
    """越权矩阵：真实入口返回 403、留 denied 日志、对象不被改动（REQ-026）。"""
    presale = presale_product(seller, category, title=TARGET_TITLE)
    plain = plain_product(seller, category, title="普通相机")
    reservation = None
    order = None
    if case in CASE_STEPS:
        reservation = build_reservation(
            seller, category, buyer, to=CASE_STEPS[case], product=presale
        )
    if case in ORDER_STEPS:
        order = build_order(seller, category, buyer, to=ORDER_STEPS[case], product=plain)

    client = as_user(_actor(request, actor_name))
    logs_before = OperationLog.objects.count()
    url, body = _request_for(case, presale, plain, reservation, order)
    status, payload = api(client, "POST", url, body)

    assert status == 403
    assert TARGET_TITLE not in json.dumps(payload, ensure_ascii=False)
    assert OperationLog.objects.count() == logs_before + 1
    last = OperationLog.objects.order_by("-id").first()
    assert last.result == RESULT_DENIED
    assert last.action == expected_action
    if reservation is not None:
        reservation.refresh_from_db()
        assert reservation.status == CASE_STEPS[case]
    if order is not None:
        order.refresh_from_db()
        assert order.status == ORDER_STEPS[case]


def _actor(request, name):
    """按名字取身份夹具；游客没有账号，用未登录的客户端。"""
    if name == "guest":
        return AnonymousUser()
    return request.getfixturevalue(name)


def _request_for(case, presale, plain, reservation, order):
    """给每条用例拼 URL 与请求体。"""
    if case == "reserve":
        return f"/api/products/{presale.pk}/reservations/", {"request_key": "rk"}
    if case == "order_create":
        return "/api/orders/", {"product_id": plain.pk, "request_key": "rk"}
    if case.startswith("order"):
        base = f"/api/orders/{order.pk}"
    else:
        base = f"/api/reservations/{reservation.pk}"
    action = case.split("_", 1)[1] if case.startswith("order") else case
    if case in ("pay_deposit", "pay_final", "order_pay"):
        return f"{base}/{action.replace('_', '-')}/", {"txn_no": f"TX-{case}"}
    if case == "order_ship":
        return f"{base}/ship/", {"tracking_no": "SF999999"}
    return f"{base}/{action}/", {}


def test_deny_matrix_has_enough_cases():
    """越权用例数量不低于 REQ-026 要求的 30 条。"""
    assert len(DENY_CASES) >= 30


def test_presale_chain_through_views(seller, buyer, admin, category):
    """完整预售链路：建草稿→送审→审核→预约→定金→确认→到货→尾款→订单（REQ-006）。"""
    seller_client = as_user(seller)
    payload = {
        "title": TARGET_TITLE,
        "price": "100.00",
        "is_presale": True,
        "deposit": "20.00",
        "final_amount": "80.00",
        "presale_valid_days": 30,
        "category_id": category.pk,
    }
    status, body = api(seller_client, "POST", "/api/products/", payload)
    assert status == 201
    product_pk = body["id"]
    api(
        seller_client,
        "POST",
        f"/api/products/{product_pk}/images/",
        {"url": "https://i.example.com/a.jpg"},
    )
    api(seller_client, "POST", f"/api/products/{product_pk}/submit/", {})
    api(as_user(admin), "POST", f"/api/products/{product_pk}/audit/", {"approve": True})

    status, body = api(
        as_user(buyer),
        "POST",
        f"/api/products/{product_pk}/reservations/",
        {"request_key": "k1"},
    )
    assert status == 201
    rid = body["id"]
    status, body = api(
        as_user(buyer), "POST", f"/api/reservations/{rid}/pay-deposit/", {"txn_no": "D-1"}
    )
    assert status == 200 and body["status"] == AWAITING_SELLER
    api(as_user(seller), "POST", f"/api/reservations/{rid}/confirm/", {})
    api(as_user(seller), "POST", f"/api/reservations/{rid}/arrive/", {})
    status, body = api(
        as_user(buyer), "POST", f"/api/reservations/{rid}/pay-final/", {"txn_no": "F-1"}
    )
    assert status == 200 and body["status"] == COMPLETED
    assert body["order_id"]

    order = Order.objects.get(pk=body["order_id"])
    product = type(order.product).objects.get(pk=product_pk)
    assert (order.status, product.status) == (ORDER_WAITING_SHIP, TRADING)


def test_order_chain_through_views(seller, buyer, category):
    """普通商品链路：下单→支付→发货→收货→结算，手续费封顶 200（REQ-013~017）。"""
    product = plain_product(seller, category, title=TARGET_TITLE, price="20000.00")
    status, body = api(as_user(buyer), "POST", "/api/orders/", {"product_id": product.pk})
    assert status == 201
    oid = body["id"]
    status, body = api(
        as_user(buyer), "POST", f"/api/orders/{oid}/pay/", {"txn_no": "T-900"}
    )
    assert status == 200 and body["status"] == ORDER_WAITING_SHIP
    status, body = api(
        as_user(seller),
        "POST",
        f"/api/orders/{oid}/ship/",
        {"tracking_no": "SF888888"},
    )
    assert status == 200 and body["status"] == ORDER_WAITING_RECEIPT
    status, body = api(as_user(buyer), "POST", f"/api/orders/{oid}/receive/", {})
    assert status == 200 and body["status"] == "已完成"
    product.refresh_from_db()
    assert product.status == SOLD
    settlement = Settlement.objects.get(order_id=oid)
    assert str(settlement.fee) == "200.00"


def test_same_buyer_repeat_order_returns_same_id(seller, buyer, category):
    """同一买家重复提交返回原待付款订单号（REQ-013）。"""
    product = plain_product(seller, category)
    status, first = api(
        as_user(buyer),
        "POST",
        "/api/orders/",
        {"product_id": product.pk, "request_key": "r1"},
    )
    assert status == 201
    status, again = api(
        as_user(buyer),
        "POST",
        "/api/orders/",
        {"product_id": product.pk, "request_key": "r1"},
    )
    assert status == 201
    assert again["id"] == first["id"]


def test_stale_version_is_rejected_but_repeat_submit_still_gets_original(
    seller, buyer, other, category
):
    """同买家重复提交返回原单；换一个买家拿着旧版本号下单则被拒（REQ-013）。

    锁货会把商品版本 +1，所以「同买家已有待付款订单」必须先于版本号判定：
    该买家重复点击（哪怕页面版本已过期）都拿到原单，别人才会收到 409「商品已被修改」。
    """
    product = plain_product(seller, category)
    stale = product.version
    status, first = api(
        as_user(buyer),
        "POST",
        "/api/orders/",
        {"product_id": product.pk, "request_key": "r2", "version": stale},
    )
    assert status == 201
    status, again = api(
        as_user(buyer),
        "POST",
        "/api/orders/",
        {"product_id": product.pk, "request_key": "r2", "version": stale},
    )
    assert status == 201
    assert again["id"] == first["id"]
    assert Order.objects.filter(product_id=product.pk).count() == 1

    status, payload = api(
        as_user(other),
        "POST",
        "/api/orders/",
        {"product_id": product.pk, "version": stale},
    )
    assert status == 409
    assert "已被修改" in payload["error"]


def test_locked_product_is_hidden_from_list(seller, buyer, other, category):
    """锁定期商品从列表与检索里消失（REQ-013）。"""
    product = plain_product(seller, category, title=TARGET_TITLE)
    api(as_user(buyer), "POST", "/api/orders/", {"product_id": product.pk})
    for user in (buyer, seller, other):
        status, body = api(as_user(user), "GET", "/api/products/")
        assert status == 200
        assert all(item["title"] != TARGET_TITLE for item in body["items"])


def test_reserved_presale_is_hidden_from_others(seller, buyer, other, category):
    """有效预约期间已预售商品对他人不可见，当事人与客服可见（REQ-006）。"""
    product = presale_product(seller, category, title=TARGET_TITLE)
    status, body = api(
        as_user(buyer), "POST", f"/api/products/{product.pk}/reservations/", {"request_key": "r9"}
    )
    assert status == 201
    for user in (other, seller):
        status, listing = api(as_user(user), "GET", "/api/products/")
        assert all(item["title"] != TARGET_TITLE for item in listing["items"])
    status, listing = api(as_user(seller), "GET", "/api/reservations/")
    assert status == 200 and len(listing["items"]) == 1


def test_reservation_list_scope(buyer, other, support, seller, category):
    """预约列表口径：当事人与客服可见，无关会员看不到（REQ-002）。"""
    product = presale_product(seller, category)
    api(as_user(buyer), "POST", f"/api/products/{product.pk}/reservations/", {})
    status, body = api(as_user(support), "GET", "/api/reservations/")
    assert status == 200 and len(body["items"]) == 1
    status, body = api(as_user(other), "GET", "/api/reservations/")
    assert status == 200 and body["items"] == []


def test_detail_of_other_users_reservation_is_404(buyer, other, seller, category):
    """别人的预约详情返回 404，不泄露存在性（REQ-012 口径）。"""
    product = presale_product(seller, category)
    status, body = api(as_user(buyer), "POST", f"/api/products/{product.pk}/reservations/", {})
    assert status == 201
    status, _ = api(as_user(other), "GET", f"/api/reservations/{body['id']}/")
    assert status == 404


def test_overdue_deposit_payment_returns_409(seller, buyer, category):
    """定金窗口已过期：409（时间冲突与权限拒绝要能区分）。"""
    from datetime import timedelta  # pylint: disable=import-outside-toplevel

    from django.utils import timezone  # pylint: disable=import-outside-toplevel

    product = presale_product(seller, category)
    reservation = build_reservation(
        seller, category, buyer, to="待付定金", now=timezone.now() - timedelta(minutes=20)
    )
    status, body = api(
        as_user(buyer),
        "POST",
        f"/api/reservations/{reservation.pk}/pay-deposit/",
        {"txn_no": "D-late"},
    )
    assert status == 409
    assert "超时" in body["error"]


def test_string_true_approve_is_rejected(seller, admin, category):
    """审核标志必须是真布尔：字符串 "yes" 被 400 拒绝，不会因 truthy 放行。"""
    status, body = api(
        as_user(seller),
        "POST",
        "/api/products/",
        {"title": TARGET_TITLE, "price": "10.00", "category_id": category.pk},
    )
    assert status == 201
    pid = body["id"]
    api(
        as_user(seller),
        "POST",
        f"/api/products/{pid}/images/",
        {"url": "https://i.example.com/b.jpg"},
    )
    api(as_user(seller), "POST", f"/api/products/{pid}/submit/", {})
    status, body = api(as_user(admin), "POST", f"/api/products/{pid}/audit/", {"approve": "yes"})
    assert status == 400
    assert "true 或 false" in body["error"]


def test_unknown_action_is_404(buyer, seller, category):
    """未定义动作 404，且不写 denied 日志（不是越权，而是没有这个入口）。"""
    product = plain_product(seller, category)
    status, body = api(as_user(buyer), "POST", "/api/orders/", {"product_id": product.pk})
    oid = body["id"]
    logs_before = OperationLog.objects.count()
    status, _ = api(as_user(buyer), "POST", f"/api/orders/{oid}/do-something/", {})
    assert status == 404
    assert OperationLog.objects.count() == logs_before


def test_cancel_then_reorder_works(seller, buyer, category):
    """取消后商品恢复可售，买家能再次下单（REQ-013）。"""
    product = plain_product(seller, category)
    status, body = api(as_user(buyer), "POST", "/api/orders/", {"product_id": product.pk})
    oid = body["id"]
    api(as_user(seller), "POST", f"/api/orders/{oid}/cancel/", {"reason": "没货了"})
    assert OperationLog.objects.filter(action="order.cancel", result=RESULT_ALLOWED).exists()
    status, body = api(as_user(buyer), "POST", "/api/orders/", {"product_id": product.pk})
    assert status == 201
    assert body["status"] == ORDER_WAITING_PAY


def test_refund_restores_sale(seller, buyer, category):
    """未发货全额退款后商品回到已发布并公开可见（REQ-016）。"""
    product = plain_product(seller, category, title=TARGET_TITLE)
    status, body = api(as_user(buyer), "POST", "/api/orders/", {"product_id": product.pk})
    oid = body["id"]
    api(as_user(buyer), "POST", f"/api/orders/{oid}/pay/", {"txn_no": "T-777"})
    status, body = api(
        as_user(buyer), "POST", f"/api/orders/{oid}/refund/", {"reason": "不想要了"}
    )
    assert status == 200 and body["status"] == "已退款"
    product.refresh_from_db()
    assert product.status == PUBLISHED
