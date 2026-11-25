"""隔离内测开关、工作台权限和业务时间边界。"""

from datetime import datetime, timedelta, timezone

import pytest
from django.test import Client

from audit.models import OperationLog
from catalog.models import Product
from demo.clock import business_now
from demo.models import DemoClock
from trade.models import Payment

from tests.conftest import make_presale

pytestmark = pytest.mark.django_db


def _post(client, path, **body):
    return client.post(path, body, content_type="application/json")


def test_demo_defaults_closed(client, settings, buyer):
    """未启用界面时，即使误开其他两个开关也不会启用跳过和业务快进。"""
    settings.DEMO_UI = False
    settings.DEMO_SMS_SKIP = True
    settings.DEMO_CLOCK = True
    DemoClock.objects.create(pk=1, offset=timedelta(days=10))
    assert abs(business_now() - datetime.now(timezone.utc)) < timedelta(seconds=2)
    assert client.get("/").status_code == 404
    assert client.get("/demo/desk/").status_code == 404
    client.force_login(buyer)
    assert _post(client, "/demo/clock/", step="day").status_code == 404
    assert client.post("/accounts/register", {
        "phone": "13910001000", "skip_sms": "1", "password": "local-strong-5xJ",
    }).status_code == 400


def test_explicit_sms_skip_and_csrf(settings):
    """演示配置可关闭固定码；跳过仅在明确勾选后生效，且仍受 CSRF 和密码校验。"""
    settings.DEMO_UI = settings.DEMO_SMS_SKIP = True
    settings.SMS_DEV_CODE = ""
    client = Client(enforce_csrf_checks=True)
    assert client.get("/").status_code == 200
    token = client.cookies["csrftoken"].value
    form = {"phone": "13910001001", "password": "local-strong-5xJ"}
    assert client.post("/accounts/register", {**form, "skip_sms": "1"}).status_code == 403
    assert client.post("/accounts/register", form, HTTP_X_CSRFTOKEN=token).status_code == 400
    result = client.post("/accounts/register", {**form, "skip_sms": "1"}, HTTP_X_CSRFTOKEN=token)
    assert result.status_code == 201
    assert client.get("/accounts/logout").status_code == 405
    assert client.post("/accounts/logout").status_code == 403
    token = client.cookies["csrftoken"].value
    assert client.post("/accounts/logout", HTTP_X_CSRFTOKEN=token).status_code == 200


def test_workbench_is_scoped(client, settings, buyer, seller, admin, *, category):
    """游客、无关成员和管理员不会在工作台看见卖家未送审的草稿。"""
    settings.DEMO_UI = True
    draft = Product.objects.create(title="仅卖家可见", seller=seller, category=category, price=45)
    assert "products" not in client.get("/demo/desk/").json()
    for actor in (buyer, admin, seller):
        client.force_login(actor)
        response = client.get("/demo/desk/")
        items = response.json()["products"]
        assert [item["id"] for item in items] == ([draft.pk] if actor == seller else [])
        assert "no-store" in response.headers["Cache-Control"]


def test_demo_clock_reuses_payment_window(client, settings, buyer, seller, admin, *, category):
    """普通成员不能改时钟；次日业务时间放开尾款，但支付开关仍有效。"""
    settings.DEMO_UI = settings.DEMO_CLOCK = True
    client.force_login(buyer)
    assert _post(client, "/demo/clock/", step="midnight").status_code == 403
    product = make_presale(seller, category)
    created = _post(client, f"/api/products/{product.pk}/reservations/", request_key="demo")
    assert created.status_code == 201
    base = f"/api/reservations/{created.json()['id']}/"
    assert _post(client, base + "pay-deposit/", txn_no="demo-deposit").status_code == 200
    client.force_login(seller)
    for action in ("confirm", "arrive"):
        assert _post(client, base + action + "/").status_code == 200
    client.force_login(buyer)
    assert _post(client, base + "pay-final/", txn_no="demo-final").status_code == 409
    assert Payment.objects.count() == 1
    client.force_login(admin)
    assert _post(client, "/demo/clock/", step=["day"]).status_code == 400
    assert _post(client, "/demo/clock/", step="midnight").status_code == 200
    assert OperationLog.objects.filter(action="demo.clock").exists()
    client.force_login(buyer)
    settings.SIMULATED_PAYMENTS = False
    assert _post(client, base + "pay-final/", txn_no="demo-final").status_code == 503
    settings.SIMULATED_PAYMENTS = True
    result = _post(client, base + "pay-final/", txn_no="demo-final")
    assert result.status_code == 200 and result.json()["order_id"]
    assert Payment.objects.count() == 2
