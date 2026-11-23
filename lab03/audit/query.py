"""按权限查询日志，普通买卖家仅查看本人的订单时间线（REQ-023）。"""

from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db.models import CharField, Q, Subquery
from django.db.models.functions import Cast
from django.utils import timezone

from accounts.models import ROLE_ADMIN, ROLE_MEMBER
from accounts.permissions import can, role_of
from common.api import optional_int
from common.errors import InvalidInput
from common.timebox import read_period
from trade.models import Order

from .models import RESULT_ALLOWED, RESULT_DENIED, OperationLog, record

PAGE_SIZE = 50
MAX_ROWS = 1000
TIMELINE = frozenset({
    "order.create", "order.pay", "order.ship", "order.receive", "order.cancel", "order.refund",
    "order.dispute", "order.settle", "order.refund_request", "dispute.raise",
})
FILTERS = ("order_id", "product_id", "actor_id", "action", "result", "start", "end")


def _deny(actor, order_id):
    record(action="log.view", actor=actor, result=RESULT_DENIED, target_type="order",
           target_id=order_id, detail={"reason": "无权查询操作日志"})
    raise PermissionDenied("无权查询操作日志")


def _scope(actor, order_id, now):
    role = role_of(actor)
    logs = OperationLog.objects.order_by("-created_at", "-pk")
    if role != ROLE_ADMIN:
        logs = logs.filter(created_at__gte=now - timedelta(days=180))
    if can(actor, "log.view"):
        return logs, True
    if role != ROLE_MEMBER or not order_id:
        _deny(actor, order_id)
    if not Order.objects.filter(pk=order_id).filter(Q(buyer=actor) | Q(seller=actor)).exists():
        _deny(actor, order_id)
    return logs.filter(target_type="order", target_id=str(order_id), action__in=TIMELINE,
                       result=RESULT_ALLOWED), False


def _product_scope(logs, product_id):
    order_ids = Order.objects.filter(product_id=product_id).annotate(
        text_id=Cast("pk", output_field=CharField())
    ).values("text_id").order_by()
    return logs.filter(Q(target_type="product", target_id=str(product_id))
                       | Q(target_type="order", target_id__in=Subquery(order_ids))
                       | Q(detail__product_id=product_id))


def _filter(logs, params, order_id, staff):
    if order_id:
        logs = logs.filter(Q(target_type="order", target_id=str(order_id))
                           | Q(detail__order_id=order_id))
    product = optional_int(params.get("product_id"), "商品编号")
    actor_id = optional_int(params.get("actor_id"), "操作人 ID") if staff else None
    if product is not None:
        logs = _product_scope(logs, product)
    if actor_id is not None:
        logs = logs.filter(actor_id=actor_id)
    for key in ("action", "result"):
        if params.get(key):
            logs = logs.filter(**{key: params[key]})
    return logs


def _serialize(log, staff):
    row = {"id": log.pk, "action": log.action, "result": log.result, "at": log.created_at}
    if staff:
        row.update(
            actor_id=log.actor_id, actor_role=log.actor_role, target_type=log.target_type,
            target_id=log.target_id, ip=log.ip, client_type=log.client_type, detail=log.detail,
        )
    return row


def _period(logs, start, end):
    if start and end and start >= end:
        raise InvalidInput("开始时间必须早于结束时间")
    if start:
        logs = logs.filter(created_at__gte=start)
    if end:
        logs = logs.filter(created_at__lt=end)
    return logs


def query_logs(actor, params, *, now=None):
    """按时间倒序返回有限页数，空筛选需确认默认查询范围。"""
    moment = now or timezone.now()
    order_id = optional_int(params.get("order_id"), "订单号")
    logs, staff = _scope(actor, order_id, moment)
    start, end = read_period(params)
    default_period = not any(params.get(key) for key in FILTERS)
    if default_period and params.get("confirm") != "1":
        return {"confirmation_required": True, "default_days": 7, "rows": []}
    if default_period:
        start = moment - timedelta(days=7)
    page = optional_int(params.get("page", 1), "页码")
    if page is None or not 1 <= page <= MAX_ROWS // PAGE_SIZE:
        raise InvalidInput("页码超出查询上限；请缩小范围")
    logs = _period(_filter(logs, params, order_id, staff), start, end)
    offset = (page - 1) * PAGE_SIZE
    batch = list(logs[offset:offset + PAGE_SIZE + 1])
    return {
        "page": page, "rows": [_serialize(log, staff) for log in batch[:PAGE_SIZE]],
        "has_next": len(batch) > PAGE_SIZE and page * PAGE_SIZE < MAX_ROWS,
        "limit_reached": len(batch) > PAGE_SIZE and page * PAGE_SIZE == MAX_ROWS,
    }


def read_log(actor, log_id, *, now=None):
    """仅工作人员可读取变更明细，仍受 180 天归档限制。"""
    if not can(actor, "log.view"):
        _deny(actor, None)
    rows, _ = _scope(actor, None, now or timezone.now())
    log = rows.filter(pk=log_id).first()
    return _serialize(log, True) if log else None
