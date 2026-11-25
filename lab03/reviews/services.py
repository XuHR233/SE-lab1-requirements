"""互评、自动评价及评价申诉。"""

from datetime import timedelta
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from accounts.models import ROLE_ADMIN, ROLE_MEMBER
from accounts.permissions import role_of
from audit.models import RESULT_ALLOWED, RESULT_DENIED, record
from common.errors import Conflict, InvalidInput
from common.timebox import local_midnight
from demo.clock import business_now
from trade.models import COMPLETED, Order

from .models import Appeal, Review


def _log(action, order, actor=None, result=RESULT_ALLOWED, **detail):
    record(action=action, result=result, actor=actor, target_type="order", target_id=order.pk,
           detail={"product_id": order.product_id, **detail})


def _deny(action, order, actor, message):
    _log(action, order, actor, RESULT_DENIED, reason=message)
    raise PermissionDenied(message)


def deadline(order):
    """完成日之后的第 14 个自然日结束时关闭窗口。"""
    return local_midnight(order.received_at, days=15)


def _check_author(order, actor, action, moment):
    if role_of(actor) != ROLE_MEMBER or actor.pk not in (order.buyer_id, order.seller_id):
        raise PermissionDenied(f"只有订单双方可以执行 {action}")
    if order.status != COMPLETED or not order.received_at:
        raise Conflict("只有已完成订单可以评价")
    if moment >= deadline(order):
        raise Conflict("评价窗口已结束")


def _validate(rating, content):
    if not isinstance(rating, int) or isinstance(rating, bool) or not 1 <= rating <= 5:
        raise InvalidInput("评分必须是 1 至 5 的整数")
    if not isinstance(content, str) or len(content.strip()) > 1000:
        raise InvalidInput("评价必须为不超过 1000 字的文本")


def _log_failures(action):
    def decorate(operation):
        @wraps(operation)
        def run(order, actor, **kwargs):
            try:
                return operation(order, actor, **kwargs)
            except (PermissionDenied, Conflict, InvalidInput) as exc:
                _log(action, order, actor, RESULT_DENIED, reason=str(exc))
                raise
        return run
    return decorate


@_log_failures("review.submit")
def submit(order, actor, *, rating, content="", now=None):
    """同一方重复提交修改原评价；订单行锁与补评任务共用。"""
    _validate(rating, content)
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        _check_author(order, actor, "review.submit", now or business_now())
        target_id = order.seller_id if order.buyer_id == actor.pk else order.buyer_id
        review, created = Review.objects.get_or_create(
            order=order, author=actor, defaults={"target_id": target_id, "rating": rating}
        )
        before = review.rating if not created else None
        if review.deleted_at or review.hidden_at or review.automatic:
            raise Conflict("已删除、屏蔽或自动生成的评价不可修改")
        review.rating, review.content = rating, content.strip()
        review.save(update_fields=["rating", "content", "updated_at"])
    _log("review.submit", order, actor, review_id=review.pk, before=before, after=rating)
    return review


@_log_failures("review.delete")
def delete(order, actor, *, now=None):
    """在窗口内删除自己的展示记录，不擦除历史评分。"""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        moment = now or business_now()
        _check_author(order, actor, "review.delete", moment)
        count = Review.objects.filter(
            order=order, author=actor, automatic=False, deleted_at__isnull=True
        ).update(deleted_at=moment, updated_at=moment)
        if not count:
            raise Conflict("没有可删除的评价")
    _log("review.delete", order, actor)


def _appeal_due(moment):
    """加三个工作日；未接入节假日历，按周一至周五。"""
    due = timezone.localtime(moment)
    remaining = 3
    while remaining:
        due += timedelta(days=1)
        remaining -= due.weekday() < 5
    return due


def appeal(review, actor, *, reason, now=None):
    """被评价方可申诉；申诉不会自动隐藏评价。"""
    if role_of(actor) != ROLE_MEMBER or actor.pk != review.target_id:
        _deny("review.appeal", review.order, actor, "只有被评价方可申诉")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
        raise InvalidInput("请填写不超过 500 字的申诉原因")
    moment = now or business_now()
    if not review.order.received_at or moment >= deadline(review.order):
        text = "评价申诉窗口已结束"
        _log("review.appeal", review.order, actor, RESULT_DENIED, reason=text)
        raise Conflict(text)
    entry, created = Appeal.objects.get_or_create(
        review=review,
        defaults={"reason": reason.strip(), "due_at": _appeal_due(moment)},
    )
    if not created:
        raise Conflict("该评价已有申诉")
    _log("review.appeal", review.order, actor, appeal_id=entry.pk)
    return entry


def resolve(entry, actor, *, upheld, note, now=None):
    """管理员处理一次；申诉成立后标记评价屏蔽。"""
    if role_of(actor) != ROLE_ADMIN:
        _deny("review.resolve", entry.review.order, actor, "只有管理员可处理申诉")
    valid_note = isinstance(note, str) and 1 <= len(note.strip()) <= 500
    if not isinstance(upheld, bool) or not valid_note:
        raise InvalidInput("请填写裁定结论和不超过 500 字的处理说明")
    moment = now or business_now()
    with transaction.atomic():
        updated = Appeal.objects.filter(pk=entry.pk, resolved_at__isnull=True).update(
            upheld=upheld, note=note.strip(), resolved_at=moment, handler=actor
        )
        if not updated:
            raise Conflict("申诉已经处理")
        if upheld:
            Review.objects.filter(pk=entry.review_id).update(hidden_at=moment)
    _log("review.resolve", entry.review.order, actor, appeal_id=entry.pk, upheld=upheld)


def _auto_review(order_id, moment):
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id, status=COMPLETED).first()
        if not order or not order.received_at or moment < deadline(order):
            return 0
        created = 0
        parties = (order.buyer_id, order.seller_id)
        for author, target in (parties, parties[::-1]):
            review, added = Review.objects.get_or_create(
                order=order, author_id=author,
                defaults={"target_id": target, "rating": 5, "automatic": True},
            )
            if added:
                _log("review.auto", order, review_id=review.pk, after=5)
                created += 1
    return created


def sweep(*, now=None):
    """补齐缺失的评价，标记待管理员处理的超期申诉。"""
    moment = now or business_now()
    cutoff = local_midnight(moment, days=-14)
    candidates = Order.objects.filter(status=COMPLETED, received_at__lt=cutoff).annotate(
        review_count=Count("reviews")
    ).filter(review_count__lt=2).values_list("pk", flat=True)
    created = sum(_auto_review(order_id, moment) for order_id in candidates.iterator())
    reminded = Appeal.objects.filter(
        resolved_at__isnull=True, reminded_at__isnull=True, due_at__lte=moment
    ).update(reminded_at=moment)
    return created, reminded
