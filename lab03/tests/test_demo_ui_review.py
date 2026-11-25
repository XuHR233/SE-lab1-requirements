"""复验实际文件上传、角色边界和会话附件可见性。"""

from datetime import timedelta
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from catalog.models import DRAFT
from communication import services as communication
from tests.conftest import make_product
from tests.helpers_m5 import paid_order
from trade.models import COMPLETED, Order, Settlement

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_settings(settings, tmp_path):
    """图片写入独立临时目录，不访问验收库的媒体目录。"""
    settings.DEMO_UI = True
    settings.DEMO_MEDIA_ROOT = tmp_path


def _picture():
    with BytesIO() as buffer:
        Image.new("RGB", (18, 9), "orange").save(buffer, "PNG")
        return SimpleUploadedFile("demo.png", buffer.getvalue(), content_type="image/png")


def test_product_file_preview_and_approval(client, seller, buyer, admin, *, category):
    """卖家上传后可预览，待审时管理员可看，通过后买家可看。"""
    product = make_product(seller, category, status=DRAFT, with_image=False)
    product.images.create(url="/static/demo/item.svg")
    path = f"/demo/products/{product.pk}/image/"
    client.force_login(seller)
    upload = client.post(path, {"file": _picture(), "version": product.version})
    assert upload.status_code == 201
    image_url = upload.json()["thumbnail"]
    assert client.get(image_url).headers["Content-Type"] == "image/webp"
    client.force_login(buyer)
    assert client.get(image_url).status_code == 404
    assert client.post(path, {"file": _picture()}).status_code == 403
    client.force_login(seller)
    assert client.post(f"/api/products/{product.pk}/submit/", {},
                       content_type="application/json").status_code == 200
    client.force_login(admin)
    assert client.get("/demo/desk/").json()["products"][0]["thumbnail"] == image_url
    assert client.get(image_url).status_code == 200
    assert client.post(f"/api/products/{product.pk}/audit/", {"approve": True},
                       content_type="application/json").status_code == 200
    client.logout()
    assert client.get(image_url).status_code == 200


def test_upload_rejects_invalid_and_stale(client, seller, category):
    """伪装成图片的文本和过期版本不留下图片记录。"""
    product = make_product(seller, category, status=DRAFT, with_image=False)
    client.force_login(seller)
    path = f"/demo/products/{product.pk}/image/"
    invalid = SimpleUploadedFile("demo.png", b"not an image", content_type="image/png")
    assert client.post(path, {"file": invalid}).status_code == 400
    assert client.post(path, {"file": _picture(), "version": 999}).status_code == 409
    assert not product.images.exists()


def test_staff_workbench_has_no_personal_trade_tabs(client, admin):
    """管理角色的工作台不下发订单及预约列表，保留纠纷入口。"""
    client.force_login(admin)
    desk = client.get("/demo/desk/").json()
    assert "orders" not in desk and "reservations" not in desk
    assert "disputes" in desk


def test_home_uses_lab2_templates_and_styles(client, admin):
    """访客与后台复用 Lab2 的 HTML 和样式，不回退到旧主题。"""
    guest = client.get("/").content.decode()
    for marker in ('class="topbar"', 'class="brandside"', 'id="lab2-order"',
                   'id="lab2-reservation"', 'id="lab2-chat"', 'id="lab2-inventory"',
                   '/static/demo/lab2-ui.css', '/static/demo/lab2-pages.css'):
        assert marker in guest
    assert '/static/demo/style.css' not in guest
    client.force_login(admin)
    staff = client.get("/").content.decode()
    assert 'class="aside"' in staff and 'class="adminbar"' in staff
    assert 'id="auth-dialog"' not in staff


def test_lab2_dashboard_shows_only_authorized_sales(client, seller, buyer, admin, *, category):
    """原型的趋势和成交表使用真实已完成订单，并限制详细数据的可见范围。"""
    product = make_product(seller, category)
    sale = Order.objects.create(product=product, seller=seller, buyer=buyer, status=COMPLETED,
                                amount="100.00", received_at=timezone.now() - timedelta(seconds=1))
    Settlement.objects.create(order=sale, gross="100.00", fee="1.50", channel_fee="0.60",
                              platform_fee="0.90", seller_net="98.50")
    url = f"/demo/sellers/{seller.pk}/stats/?period=7"
    for actor in (buyer, seller, admin):
        client.force_login(actor)
        response = client.get(url)
        if actor == buyer:
            assert response.status_code == 403
            continue
        assert response.status_code == 200
        payload = response.json()
        dashboard = payload["dashboard"]
        assert payload["stats"]["completed"] == 1
        assert dashboard["amount"] == "100.00" and dashboard["fee"] == "1.50"
        assert sum(row["count"] for row in dashboard["daily"]) == 1
        assert dashboard["categories"][0]["product__category__name"] == category.name
        assert dashboard["latest"][0]["product__title"] == product.title


def test_chat_file_is_private(client, buyer, seller, other, category):
    """只有会话参与人能发送并查看直接上传的图片。"""
    product = make_product(seller, category)
    client.force_login(buyer)
    opened = client.post("/api/chats/", {"product_id": product.pk},
                         content_type="application/json").json()
    chat = f"/api/chats/{opened['id']}/"
    upload = client.post(f"/demo/chats/{opened['id']}/image/", {"file": _picture()})
    assert upload.status_code == 201
    image_url = upload.json()["image"]
    client.force_login(seller)
    response = client.get(chat).json()
    assert response["conversation"]["title"] == product.title
    assert response["items"][0]["image"] == image_url
    assert client.get(image_url).status_code == 200
    client.force_login(other)
    assert client.get(image_url).status_code == 404
    rejected = client.post(f"/demo/chats/{opened['id']}/image/", {"file": _picture()})
    assert rejected.status_code == 403


def test_assigned_staff_can_share_image(client, buyer, seller, support, *, category):
    """接单的客服可发图，转交后不能继续上传；买家仍能查看已发图。"""
    order = paid_order(seller, category, buyer)
    ticket = communication.raise_dispute(order.pk, buyer)
    upload = f"/demo/chats/{ticket.conversation_id}/image/"
    client.force_login(support)
    assert client.post(upload, {"file": _picture(), "dispute_id": ticket.pk}).status_code == 403
    communication.accept_dispute(ticket, support)
    response = client.post(upload, {"file": _picture(), "dispute_id": ticket.pk})
    assert response.status_code == 201
    communication.escalate_dispute(ticket, support, note="请管理员复核")
    assert client.post(upload, {"file": _picture(), "dispute_id": ticket.pk}).status_code == 403
    client.force_login(buyer)
    assert client.get(response.json()["image"]).status_code == 200
