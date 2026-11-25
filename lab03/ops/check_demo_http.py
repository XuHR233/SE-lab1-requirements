"""Run the acceptance scenario over loopback HTTP against the disposable test DB only."""

import json
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

BASE = "http://127.0.0.1:18738"


class Session:
    """One real HTTP cookie session per role."""

    def __init__(self):
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        page = self.request("/")
        assert "二手集市 · Lab3 内测" in page and '"smsSkip": true' in page

    def request(self, path, data=None, *, status=200, form=False):
        """Send a GET or a CSRF-checked POST; assert the expected HTTP status."""
        headers = {}
        payload = None
        if data is not None:
            token = next(item.value for item in self.cookies if item.name == "csrftoken")
            headers["X-CSRFToken"] = token
            headers["Content-Type"] = ("application/x-www-form-urlencoded" if form
                                       else "application/json")
            payload = (urlencode(data) if form else json.dumps(data)).encode()
        try:
            with self.opener.open(Request(BASE + path, data=payload, headers=headers),
                                  timeout=12) as response:
                actual, content_type, raw = response.status, response.headers, response.read()
        except HTTPError as error:
            actual, content_type, raw = error.code, error.headers, error.read()
            error.close()
        text = raw.decode()
        assert actual == status, f"{path}: HTTP {actual}, expected {status}: {text[:400]}"
        return json.loads(text) if content_type.get_content_type() == "application/json" else text


def _publish(seller, admin, title, *, presale=False):
    desk = seller.request("/demo/desk/")
    form = {"title": title, "category_id": desk["categories"][0]["id"], "price": "100.00"}
    if presale:
        form.update(is_presale=True, deposit="20", final_amount="80", presale_valid_days=7)
    key = seller.request("/api/products/", form, status=201)["id"]
    base = f"/api/products/{key}/"
    assert key not in {row["id"] for row in admin.request("/demo/desk/")["products"]}
    assert seller.request(base + "submit/", {}, status=400)["error"]
    seller.request(base + "images/", {"url": "/static/demo/item.svg", "version": 1})
    assert seller.request(base + "submit/", {})["status"] == "待审核"
    assert key in {row["id"] for row in admin.request("/demo/desk/")["products"]}
    row = admin.request(base + "audit/", {"approve": True})
    assert row["status"] == ("已预售" if presale else "已发布")
    return row


def _order(buyer, product):
    result = buyer.request("/api/orders/", {"product_id": product["id"],
                           "version": product["version"], "request_key": str(uuid4())}, status=201)
    assert result["status"] == "待付款"
    return f"/api/orders/{result['id']}/", result["id"]


def _fulfill(buyer, seller, base):
    result = seller.request(base + "ship/", {"tracking_no": "TEST123456"})
    assert result["status"] == "待收货"
    assert buyer.request(base + "receive/", {})["status"] == "已完成"


def _deal(buyer, seller, admin):
    product = _publish(seller, admin, f"现货演练 {uuid4().hex[:8]}")
    assert any(row["id"] == product["id"] for row in buyer.request("/api/products/?" +
               urlencode({"keyword": product["title"]}))["items"])
    base, order_id = _order(buyer, product)
    assert buyer.request(base + "pay/", {"txn_no": str(uuid4())})["status"] == "待发货"
    _fulfill(buyer, seller, base)
    review_id = buyer.request(base + "review/", {"rating": 4, "content": "验收测试"})["id"]
    seller.request(base + "review/", {"rating": 5, "content": "验收互评"})
    appeal = seller.request(f"/api/reviews/{review_id}/appeal/", {"reason": "模拟申诉"}, status=201)
    queue = admin.request("/api/staff/review-appeals/")["pending"]
    assert appeal["id"] in {row["id"] for row in queue}
    admin.request(f"/api/staff/review-appeals/{appeal['id']}/resolve/",
                  {"upheld": True, "note": "按演练脚本屏蔽"})
    items = buyer.request("/demo/desk/")["orders"]
    order = next(row for row in items if row["id"] == order_id)
    assert order["settlement"] == {"fee": "1.50", "net": "98.50"}
    assert len(order["reviews"]) == 1
    assert buyer.request(f"/api/logs/?order_id={order_id}")["rows"]
    assert seller.request(f"/api/sellers/{order['seller_id']}/")["stats"]["completed"] >= 1


def _presale(buyer, seller, admin):
    product = _publish(seller, admin, f"预售演练 {uuid4().hex[:8]}", presale=True)
    payload = {"version": product["version"], "request_key": str(uuid4())}
    key = buyer.request(f"/api/products/{product['id']}/reservations/", payload, status=201)["id"]
    base = f"/api/reservations/{key}/"
    assert buyer.request(base + "pay-deposit/", {"txn_no": str(uuid4())})["status"] == "待确认"
    assert seller.request(base + "confirm/", {})["status"] == "待到货"
    assert seller.request(base + "arrive/", {})["status"] == "待付尾款"
    payment = {"txn_no": str(uuid4())}
    assert "尚未开启" in buyer.request(base + "pay-final/", payment, status=409)["error"]
    buyer.request("/demo/clock/", {"step": "midnight"}, status=403)
    admin.request("/demo/clock/", {"step": "midnight"})
    result = buyer.request(base + "pay-final/", payment)
    assert result["status"] == "已完成"
    _fulfill(buyer, seller, f"/api/orders/{result['order_id']}/")


def _dispute(buyer, seller, admin, support):
    product = _publish(seller, admin, f"纠纷演练 {uuid4().hex[:8]}")
    base, order_id = _order(buyer, product)
    buyer.request(base + "pay/", {"txn_no": str(uuid4())})
    chat_id = buyer.request("/api/chats/", {"product_id": product["id"],
                                          "order_id": order_id}, status=201)["id"]
    chat = f"/api/chats/{chat_id}/"
    buyer.request(chat + "messages/", {"text": "模拟：我想申请退款"}, status=201, form=True)
    assert seller.request(chat)["signals"]
    support.request(chat, status=403)
    dispute_id = buyer.request("/api/chats/disputes/", {"order_id": order_id,
                               "reason": "模拟客服介入"}, status=201)["id"]
    assert support.request(chat)["items"]
    ticket = f"/api/chats/disputes/{dispute_id}/"
    resolution = {"verdict": "退款", "note": "模拟未发货退款"}
    message = {"text": "内测：正在查看凭证。", "dispute_id": dispute_id}
    support.request(ticket + "resolve/", resolution, status=409)
    support.request(ticket + "accept/", {})
    support.request(chat + "messages/", message, status=201, form=True)
    assert buyer.request(chat)["items"][-1]["staff_role"] == "support"
    support.request(ticket + "escalate/", {"note": "模拟请管理员复核"})
    support.request(chat + "messages/", message, status=403, form=True)
    admin.request(ticket + "resolve/", resolution, status=409)
    assert admin.request(ticket + "accept/", {})["handler_id"]
    admin.request(chat + "messages/", message, status=201, form=True)
    assert seller.request(chat)["items"][-1]["staff_role"] == "admin"
    assert admin.request(ticket + "resolve/", resolution)["status"] == "已处理"
    assert buyer.request(base)["status"] == "已退款"


def main():
    """Run all three workflows on the isolated acceptance-test instance."""
    source = Path(__file__).resolve().parents[1] / "tmp" / "ui-browser-accounts.tsv"
    sessions = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        role, phone, password = line.split("\t")
        session = Session()
        session.request("/accounts/login", {"phone": phone, "password": password}, form=True)
        sessions[role] = session
    buyer, seller, admin, support = (sessions[role] for role in ("买家", "卖家", "管理员", "客服"))
    guest = Session()
    for asset in ("app.js", "ui.js", "products.js", "trade.js", "chats.js", "logs.js", "stats.js",
                  "disputes.js", "lab2-ui.css", "lab2-pages.css", "live.css", "item.svg"):
        assert guest.request(f"/static/demo/{asset}")
    reg = {"phone": "139" + str(int(uuid4().hex[:8], 16) % 100000000).zfill(8),
           "password": str(uuid4())}
    guest.request("/accounts/register", reg, form=True, status=400)
    guest.request("/accounts/register", {**reg, "skip_sms": "1"}, form=True, status=201)
    guest.request("/accounts/logout", {})
    print("PASS: Lab2 page and static assets; four role logins; explicit SMS skip; logout")
    _deal(buyer, seller, admin)
    print("PASS: draft → review → search → simulated payment → delivery → fees"
          " → mutual reviews/appeal → statistics/logs")
    _presale(buyer, seller, admin)
    print("PASS: deposit → seller confirmation → arrival → reject early final payment"
          " → fast-forward → final payment → fulfillment")
    _dispute(buyer, seller, admin, support)
    print("PASS: chat signal → claim → staff chat → escalate → admin takeover/chat → refund")


if __name__ == "__main__":
    main()
