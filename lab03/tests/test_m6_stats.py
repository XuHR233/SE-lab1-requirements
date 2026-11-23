"""卖家统计验收（REQ-021）。"""

from tests.conftest import make_product
from tests.helpers import build_order
from trade.services import confirm_receipt, request_refund


def test_seller_statistics(client, seller, buyer, other, category):
    """退款率分母为成交与退款之和；后台可以查询停用账号。"""
    completed = build_order(seller, category, buyer, to="待收货")
    confirm_receipt(completed, actor=buyer)
    order = build_order(seller, category, buyer, to="待发货")
    request_refund(order, actor=buyer)
    make_product(seller, category)
    response = client.get(f"/api/sellers/{seller.pk}/")
    assert response.json()["stats"] == {
        "completed": 1, "refunded": 1, "on_sale": 2, "refund_rate": "0.5000",
    }
    assert client.get(f"/api/sellers/{other.pk}/").json()["stats"]["refund_rate"] == "—"


def test_staff_statistics_retains_inactive_seller(client, seller, buyer, category, support):
    """停用账号不再公开展示；后台可按收货时间读取历史。"""
    completed = build_order(seller, category, buyer, to="待收货")
    completed = confirm_receipt(completed, actor=buyer)
    seller.is_active = False
    seller.save(update_fields=["is_active"])
    assert client.get(f"/api/sellers/{seller.pk}/").status_code == 404
    url = f"/api/staff/sellers/{seller.pk}/stats/"
    assert client.get(url).status_code == 403
    client.force_login(support)
    assert client.get(url).json()["stats"]["completed"] == 1
    result = client.get(url, {"end": completed.received_at.isoformat()})
    assert result.json()["stats"]["completed"] == 0
