"""M2 验收测试：R-201 状态机 / R-202 审核流 / R-203 版本号 / R-204 下架与违规 / R-205 检索。"""

# pylint: disable=redefined-outer-name,unused-argument,too-many-arguments
# pylint: disable=too-many-positional-arguments,too-many-lines
# 理由：pytest 的 fixture 按参数名注入；db fixture 不显式使用也要写在签名里。

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied

from accounts.permissions import can
from audit.models import RESULT_DENIED, OperationLog
from catalog.models import (
    DELETED,
    DRAFT,
    LOCKED,
    OFFSHELF,
    PENDING,
    PRESALE,
    PRODUCT_STATUSES,
    PUBLISHED,
    REJECTED,
    SOLD,
    TRADING,
    VIOLATED,
    AuditRecord,
    Category,
    Product,
    ProductImage,
)
from catalog.search import PAGE_SIZE, listable, search, visible_to
from catalog.services import (
    DraftInput,
    InvalidContent,
    VersionConflict,
    add_image,
    as_bool,
    audit,
    back_to_draft,
    delete_draft,
    revise_content,
    save_draft,
    submit_for_audit,
    takedown,
    unpublish,
    update_draft,
)
from catalog.state import ALLOWED_PAIRS, StateConflict, can_transit, transit

User = get_user_model()

EXPECTED_TRANSITIONS = (
    (DRAFT, PENDING),
    (DRAFT, DELETED),
    (PENDING, PUBLISHED),
    (PENDING, PRESALE),
    (PENDING, REJECTED),
    (PENDING, DRAFT),
    (REJECTED, DRAFT),
    (PUBLISHED, PENDING),
    (PUBLISHED, OFFSHELF),
    (PUBLISHED, LOCKED),
    (PUBLISHED, VIOLATED),
    (PRESALE, PENDING),
    (PRESALE, OFFSHELF),
    (PRESALE, TRADING),
    (PRESALE, VIOLATED),
    (LOCKED, TRADING),
    (LOCKED, PUBLISHED),
    (LOCKED, VIOLATED),
    (TRADING, SOLD),
    (TRADING, PUBLISHED),
    (TRADING, PRESALE),
    (TRADING, OFFSHELF),
    (OFFSHELF, DRAFT),
    (OFFSHELF, VIOLATED),
    (VIOLATED, DRAFT),
)


@pytest.fixture
def category(db):
    """一个分类，所有用例共用。"""
    return Category.objects.create(name="数码")


@pytest.fixture
def seller(db):
    """卖家本人。"""
    return User.objects.create_user(username="seller", phone="13800000001", password="pw-A1b2c3")


@pytest.fixture
def other(db):
    """与商品无关的另一个普通用户。"""
    return User.objects.create_user(username="other", phone="13800000002", password="pw-A1b2c3")


@pytest.fixture
def admin(db):
    """管理员，用于审核与违规下架。"""
    return User.objects.create_user(
        username="root", phone="13800000003", password="pw-A1b2c3", is_staff=True
    )


@pytest.fixture
def support(db):
    """客服：只有退款与裁定终止权，没有下架权（REQ-002）。"""
    return User.objects.create_user(
        username="cs", phone="13800000004", password="pw-A1b2c3", is_support=True
    )


def make_product(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    seller,
    category,
    *,
    status=PUBLISHED,
    title="旧相机",
    price="100.00",
    is_presale=False,
    with_image=True,
):
    """直接落在指定状态，用于测状态机本身（绕过用例层，因此也可用来造假数据）。"""
    product = Product.objects.create(
        seller=seller,
        category=category,
        title=title,
        price=Decimal(price),
        is_presale=is_presale,
        status=DRAFT,
    )
    if with_image:
        ProductImage.objects.create(product=product, url="https://img.example.com/base.jpg")
    if status != DRAFT:
        Product.objects.filter(pk=product.pk).update(status=status)
        product.refresh_from_db()
    return product


def count_open_audits(product):
    """还没结论（也没被撤回）的待审流水条数。"""
    return AuditRecord.objects.filter(
        product=product, approve__isnull=True, withdrawn_at__isnull=True
    ).count()


# ---------- R-201 状态机 ----------


def test_transition_table_is_exactly_the_25_pairs(db):
    """转换表逐条对照：25 条边一条不多一条不少，11 个状态一个不少（REQ-010）。"""
    # 状态与边按 REQ-010 原文独立抄写一遍，不从实现里的表生成，才能查出「漏一条又多一条」。
    expected_statuses = "草稿 待审核 已驳回 已发布 已预售 待付款锁定 交易中 已售出 已下架 违规下架 已删除".split()
    assert sorted(PRODUCT_STATUSES) == sorted(expected_statuses)
    assert ALLOWED_PAIRS == frozenset(EXPECTED_TRANSITIONS)
    assert len(EXPECTED_TRANSITIONS) == 25
    assert not can_transit(OFFSHELF, PUBLISHED)
    assert not can_transit(PRESALE, SOLD)
    assert not can_transit(VIOLATED, PUBLISHED)


def test_state_machine_covers_all_121_combinations(db, category, seller):
    """11×11=121 组合全部跑一遍：25 条通过、96 条被拒（REQ-010）。"""
    allowed = 0
    rejected = 0
    for source in PRODUCT_STATUSES:
        for target in PRODUCT_STATUSES:
            product = make_product(seller, category, status=source)
            if can_transit(source, target):
                transit(product, target)
                assert product.status == target
                allowed += 1
            else:
                with pytest.raises(StateConflict):
                    transit(product, target)
                product.refresh_from_db()
                assert product.status == source
                rejected += 1
    assert (allowed, rejected, allowed + rejected) == (25, 96, 121)


def test_terminal_states_have_no_outgoing_edge(db):
    """已售出与已删除是终态，没有任何出边。"""
    assert not any(source in (SOLD, DELETED) for source, _ in ALLOWED_PAIRS)


# ---------- R-202 提交审核与驳回 ----------


def test_submit_requires_at_least_one_image(db, category, seller):
    """没有图片不允许提交审核（REQ-004）。"""
    product = save_draft(
        DraftInput(seller=seller, category=category, title="镜头", price=Decimal("9.90"))
    )
    with pytest.raises(InvalidContent, match="图片"):
        submit_for_audit(product, actor=seller)
    product.refresh_from_db()
    assert product.status == DRAFT


def test_submit_moves_to_pending_and_opens_one_audit_record(db, category, seller):
    """提交后进入待审核，并开出一条审核流水（REQ-004）。"""
    product = save_draft(
        DraftInput(seller=seller, category=category, title="镜头", price=Decimal("9.90"))
    )
    add_image(product, "https://img.example.com/a.jpg", actor=seller)
    submit_for_audit(product, actor=seller)
    assert product.status == PENDING
    assert AuditRecord.objects.filter(product=product).count() == 1


def test_duplicate_submit_does_not_open_a_second_record(db, category, seller):
    """待审核期间重复提交是幂等的，流水仍只有一条（REQ-004）。"""
    product = save_draft(
        DraftInput(seller=seller, category=category, title="镜头", price=Decimal("9.90"))
    )
    add_image(product, "https://img.example.com/a.jpg", actor=seller)
    submit_for_audit(product, actor=seller)
    submit_for_audit(product, actor=seller)
    assert product.status == PENDING
    assert AuditRecord.objects.filter(product=product).count() == 1


def test_approve_publishes_normal_product(db, category, seller, admin):
    """审核通过：普通商品进入已发布（REQ-005）。"""
    product = make_product(seller, category, status=PENDING)
    audit(product, auditor=admin, approve=True)
    assert product.status == PUBLISHED


def test_approve_publishes_presale_product_as_presale(db, category, seller, admin):
    """审核通过：勾了预售的商品进入已预售而不是已发布（REQ-005）。"""
    product = make_product(seller, category, status=PENDING, is_presale=True)
    audit(product, auditor=admin, approve=True)
    assert product.status == PRESALE


def test_approve_rechecks_content(db, category, seller, admin):
    """审核通过前内容要再过一遍校验：没图片的脏数据不能靠审核过关（REQ-005）。"""
    product = make_product(seller, category, status=PENDING, with_image=False)
    with pytest.raises(InvalidContent, match="图片"):
        audit(product, auditor=admin, approve=True)
    product.refresh_from_db()
    assert product.status == PENDING
    assert OperationLog.objects.filter(action="product.audit", result=RESULT_DENIED).exists()


def test_reject_requires_reason(db, category, seller, admin):
    """驳回必须填理由，缺理由时状态不变（REQ-005）。"""
    product = make_product(seller, category, status=PENDING)
    with pytest.raises(InvalidContent, match="理由"):
        audit(product, auditor=admin, approve=False)
    assert product.status == PENDING


def test_reject_then_resubmit_accumulates_two_records(db, category, seller, admin):
    """驳回→改稿→重提，审核流水累计两条且旧记录保留（REQ-005）。"""
    product = make_product(seller, category, status=PENDING)
    audit(product, auditor=admin, approve=False, reason="图片不清晰")
    assert product.status == REJECTED
    update_draft(product, actor=seller, title="旧相机（补图）")
    add_image(product, "https://img.example.com/b.jpg", actor=seller)
    submit_for_audit(product, actor=seller)
    assert product.status == PENDING
    assert AuditRecord.objects.filter(product=product).count() == 2


def test_auditor_cannot_audit_own_product(db, admin):
    """审核人不得审核与自己有关的商品（REQ-002/005）。"""
    assert can(admin, "product.audit", owner_id=admin.pk) is False
    assert can(admin, "product.audit", owner_id=admin.pk + 1) is True


def test_audit_conflicts_when_state_changed(db, category, seller, admin):
    """商品已不在待审核时，审核操作报状态冲突（REQ-005）。"""
    product = make_product(seller, category, status=PUBLISHED)
    with pytest.raises(StateConflict, match="状态已变更"):
        audit(product, auditor=admin, approve=True)


def test_submit_withdraw_resubmit_leaves_no_open_record(db, category, seller, admin):
    """提交→撤回→重提→审核通过，库里只留一条待审流水与一条通过流水（REQ-005）。"""
    product = make_product(seller, category, status=DRAFT)
    submit_for_audit(product, actor=seller)
    back_to_draft(product, actor=seller)
    assert count_open_audits(product) == 0
    assert AuditRecord.objects.filter(product=product, withdrawn_at__isnull=False).count() == 1
    submit_for_audit(product, actor=seller)
    audit(product, auditor=admin, approve=True)
    assert product.status == PUBLISHED
    assert count_open_audits(product) == 0
    assert AuditRecord.objects.filter(product=product).count() == 2


# ---------- R-203 版本号校验 ----------


def test_revise_bumps_version_and_returns_to_pending(db, category, seller, admin):
    """改内容后回待审核、版本号 +1，再审核通过回到已发布（REQ-007）。"""
    product = make_product(seller, category, status=PUBLISHED, price="100.00")
    older = product.version
    revise_content(product, actor=seller, expected_version=older, price=Decimal("88.00"))
    assert product.status == PENDING
    assert product.version == older + 1
    assert product.price == Decimal("88.00")
    audit(product, auditor=admin, approve=True)
    assert product.status == PUBLISHED


def test_revise_opens_audit_record(db, category, seller):
    """改稿要开一条新的待审流水，条数与提交时版本号对齐（REQ-005/007）。"""
    product = make_product(seller, category, status=PUBLISHED)
    revise_content(product, actor=seller, expected_version=product.version, price=Decimal("77.00"))
    assert count_open_audits(product) == 1
    record = AuditRecord.objects.get(product=product, approve__isnull=True)
    assert record.submitted_version == product.version


def test_abba_stale_page_is_rejected_and_new_content_kept(db, category, seller, admin):
    """两个编辑页停在 v1：后提交者必须被拒，库里留下先提交者的内容与版本（REQ-007）。"""
    product = make_product(seller, category, status=PUBLISHED, price="100.00")
    page_a = Product.objects.get(pk=product.pk)
    page_b = Product.objects.get(pk=product.pk)
    assert (page_a.version, page_b.version) == (1, 1)
    revise_content(page_b, actor=seller, expected_version=1, price=Decimal("88.00"))
    audit(page_b, auditor=admin, approve=True)
    assert (page_b.status, page_b.version, page_b.price) == (PUBLISHED, 2, Decimal("88.00"))
    with pytest.raises(VersionConflict, match="请刷新"):
        revise_content(page_a, actor=seller, expected_version=1, price=Decimal("1.00"))
    fresh = Product.objects.get(pk=product.pk)
    assert (fresh.status, fresh.version, fresh.price) == (PUBLISHED, 2, Decimal("88.00"))


def test_revise_rejects_invalid_content_before_writing(db, category, seller):
    """负价格在写库前就被拦下，商品仍停在已发布（REQ-004/007）。"""
    product = make_product(seller, category, status=PUBLISHED, price="100.00")
    with pytest.raises(InvalidContent, match="大于 0"):
        revise_content(product, actor=seller, expected_version=1, price=Decimal("-1.00"))
    fresh = Product.objects.get(pk=product.pk)
    assert (fresh.status, fresh.price) == (PUBLISHED, Decimal("100.00"))


def test_trading_product_cannot_be_revised(db, category, seller):
    """交易中的商品不允许修改内容（REQ-007）。"""
    product = make_product(seller, category, status=TRADING)
    with pytest.raises(StateConflict, match="不允许修改"):
        revise_content(
            product, actor=seller, expected_version=product.version, price=Decimal("1.00")
        )


def test_revise_by_other_member_is_denied_and_logged(db, category, seller, other):
    """别人不能改我发布的内容，且要留下一条 denied 日志（REQ-002/026）。"""
    product = make_product(seller, category, status=PUBLISHED)
    with pytest.raises(PermissionDenied, match="只有卖家本人"):
        revise_content(product, actor=other, expected_version=1, price=Decimal("1.00"))
    assert OperationLog.objects.filter(action="product.edit", result=RESULT_DENIED).exists()


# ---------- R-204 下架与违规处置 ----------


def test_unpublish_hides_product(db, category, seller):
    """主动下架后商品从检索结果里消失（REQ-008）。"""
    product = make_product(seller, category, status=PUBLISHED)
    unpublish(product, actor=seller)
    assert product.status == OFFSHELF
    assert not list(search(keyword="旧相机"))


def test_relist_must_pass_audit_again(db, category, seller):
    """已下架不能直接上架，退回草稿重提后是待审核（REQ-008）。"""
    product = make_product(seller, category, status=PUBLISHED)
    unpublish(product, actor=seller)
    back_to_draft(product, actor=seller)
    add_image(product, "https://img.example.com/c.jpg", actor=seller)
    submit_for_audit(product, actor=seller)
    assert product.status == PENDING


def test_takedown_requires_reason_and_admin_role(db, category, seller, admin, other):
    """违规下架必须给理由，且只有管理员能执行（REQ-009）。"""
    product = make_product(seller, category, status=PUBLISHED)
    with pytest.raises(InvalidContent, match="理由"):
        takedown(product, actor=admin, reason="   ")
    with pytest.raises(PermissionDenied, match="无权"):
        takedown(product, actor=other, reason="违规")
    takedown(product, actor=admin, reason="违规发布")
    assert product.status == VIOLATED


def test_sold_product_cannot_be_taken_down(db, category, seller, admin):
    """已售出商品不在违规下架的可处置范围内（REQ-009）。"""
    product = make_product(seller, category, status=SOLD)
    with pytest.raises(StateConflict):
        takedown(product, actor=admin, reason="违规")


def test_seller_cannot_unpublish_locked_or_trading(db, category, seller):
    """待付款锁定与交易中都不给卖家主动下架这条转换（状态图二 SLOCK 注）。"""
    for source in (LOCKED, TRADING):
        product = make_product(seller, category, status=source)
        with pytest.raises(StateConflict, match="不允许卖家主动下架"):
            unpublish(product, actor=seller)
        product.refresh_from_db()
        assert product.status == source


def test_trading_to_offshell_is_left_to_the_order_flow(db, category, seller):
    """交易中→已下架（状态图一 S6→S8）只作为状态原语存在，前置由 M4 订单/退款流程提供。

    M2 不提供 `terminate_trade()` 这类只看全局权限、不看订单的入口：实测证明「客服用一个
    商品 id 就能把交易中商品干掉」不是闭环业务动作，宁可不给入口，让 M4 满足前置后调 transit()。
    """
    product = make_product(seller, category, status=TRADING)
    assert can_transit(TRADING, OFFSHELF) is True
    transit(product, OFFSHELF)
    assert product.status == OFFSHELF


def test_trading_product_cannot_be_taken_down(db, category, seller, admin):
    """交易中不可直接违规下架，只能走裁定终止（状态图二 S9 注）。"""
    product = make_product(seller, category, status=TRADING)
    with pytest.raises(StateConflict, match="不允许违规下架"):
        takedown(product, actor=admin, reason="违规发布")
    product.refresh_from_db()
    assert product.status == TRADING


def test_support_has_no_takedown_right(db, category, seller, support):
    """客服只有退款与裁定权，没有违规下架权（REQ-002）。"""
    assert can(support, "product.takedown") is False
    product = make_product(seller, category, status=PUBLISHED)
    with pytest.raises(PermissionDenied, match="无权"):
        takedown(product, actor=support, reason="违规发布")


def test_rejected_transition_is_logged(db, category, seller):
    """被状态机拒绝的转换要留下一条 denied 日志（REQ-010 异常）。"""
    product = make_product(seller, category, status=TRADING)
    with pytest.raises(StateConflict):
        unpublish(product, actor=seller)
    log = OperationLog.objects.get(action="product.transit", target_id=str(product.pk))
    assert log.result == RESULT_DENIED
    assert log.detail["from"] == TRADING
    assert log.detail["to"] == OFFSHELF


def test_illegal_withdraw_is_logged(db, category, seller):
    """已发布不能直接退回草稿，拒绝也要记日志（REQ-010/026）。"""
    product = make_product(seller, category, status=PUBLISHED)
    with pytest.raises(StateConflict, match="不能退回草稿"):
        back_to_draft(product, actor=seller)
    product.refresh_from_db()
    assert product.status == PUBLISHED
    assert OperationLog.objects.filter(action="product.transit", result=RESULT_DENIED).exists()


def test_presale_product_can_be_unpublished_by_seller(db, category, seller):
    """已预售→已下架同样是卖家自主下架这一条边（状态图一 S5→S8）。"""
    product = make_product(seller, category, status=PRESALE, is_presale=True)
    unpublish(product, actor=seller)
    assert product.status == OFFSHELF


def test_seller_withdraws_pending_submission(db, category, seller):
    """待审核→草稿：卖家可以自己撤回，撤回时待审流水一起关闭（状态图二 S2→S1）。"""
    product = make_product(seller, category, status=PENDING)
    submit_record = AuditRecord.objects.create(product=product, submitted_version=1)
    back_to_draft(product, actor=seller)
    assert product.status == DRAFT
    submit_record.refresh_from_db()
    assert submit_record.withdrawn_at is not None
    assert submit_record.reason == "卖家撤回提交"


def test_violated_product_goes_back_to_draft(db, category, seller, admin):
    """违规下架→草稿是唯一出路，一期没有申诉通道（状态图二 S9→S1）。"""
    product = make_product(seller, category, status=PUBLISHED)
    takedown(product, actor=admin, reason="违规发布")
    back_to_draft(product, actor=seller)
    assert product.status == DRAFT


def test_only_draft_can_be_deleted_and_row_is_kept(db, category, seller):
    """草稿→已删除是逻辑删除：状态变、行留着（状态图二 S1→S10 与 S10 注）。"""
    product = save_draft(
        DraftInput(seller=seller, category=category, title="镜头", price=Decimal("9.90"))
    )
    delete_draft(product, actor=seller)
    assert product.status == DELETED
    assert Product.objects.filter(pk=product.pk).exists()
    published = make_product(seller, category, status=PUBLISHED)
    with pytest.raises(StateConflict, match="只有草稿可以删除"):
        delete_draft(published, actor=seller)


# ---------- R-204 补充：服务层的权限与改稿入口 ----------


def test_admin_cannot_create_product(db, category, admin):
    """管理员不能发布商品，服务层拦下并记 denied 日志（REQ-002）。"""
    with pytest.raises(PermissionDenied, match="可以发布商品"):
        save_draft(
            DraftInput(seller=admin, category=category, title="违规发布", price=Decimal("1.00"))
        )
    assert not Product.objects.filter(title="违规发布").exists()
    assert OperationLog.objects.filter(action="product.create", result=RESULT_DENIED).exists()


def test_guest_cannot_create_product(db, category):
    """游客（未登录）不能发布商品（REQ-002）。"""
    with pytest.raises(PermissionDenied):
        save_draft(
            DraftInput(
                seller=AnonymousUser(), category=category, title="路人发布", price=Decimal("1.00")
            )
        )
    assert not Product.objects.filter(title="路人发布").exists()


def test_add_image_requires_owner(db, category, seller, other):
    """别人不能往我的草稿里塞图片（REQ-002/004）。"""
    product = make_product(seller, category, status=DRAFT)
    with pytest.raises(PermissionDenied, match="只有卖家本人"):
        add_image(product, "https://img.example.com/x.jpg", actor=other)
    assert product.images.count() == 1
    assert OperationLog.objects.filter(action="product.edit", result=RESULT_DENIED).exists()


def test_add_image_on_published_triggers_reaudit(db, category, seller):
    """在架商品补图不能直接生效，商品回到待审核并开一条带快照的流水（REQ-007）。"""
    product = make_product(seller, category, status=PUBLISHED)
    older = product.version
    add_image(product, "https://img.example.com/y.jpg", actor=seller)
    assert (product.status, product.version) == (PENDING, older + 1)
    assert product.images.count() == 2
    record = AuditRecord.objects.get(product=product)
    assert record.submitted_version == older + 1
    assert "https://img.example.com/y.jpg" in record.snapshot["images"]


def test_add_image_on_offshell_is_denied(db, category, seller):
    """已下架/已售出等状态不给改图，只能先回到草稿（REQ-007 只放开在架商品）。"""
    product = make_product(seller, category, status=OFFSHELF)
    with pytest.raises(StateConflict, match="不能改图片"):
        add_image(product, "https://img.example.com/z.jpg", actor=seller)
    assert product.images.count() == 1
    assert OperationLog.objects.filter(action="product.transit", result=RESULT_DENIED).exists()


def test_stale_add_image_cannot_enter_pending(db, category, seller):
    """读到草稿→别人提交审核→旧请求再补图：状态判定在行锁里重读库，图片进不去（高 2）。"""
    product = make_product(seller, category, status=DRAFT)
    stale_page = Product.objects.get(pk=product.pk)
    submit_for_audit(Product.objects.get(pk=product.pk), actor=seller)
    with pytest.raises(StateConflict, match="不能改图片"):
        add_image(stale_page, "https://img.example.com/late.jpg", actor=seller)
    product.refresh_from_db()
    assert (product.status, product.images.count()) == (PENDING, 1)
    assert AuditRecord.objects.filter(product=product).count() == 1


def test_submit_by_other_while_pending_is_denied_and_logged(db, category, seller, other):
    """待审核期间别人来提交：照样 403 并写 denied 日志，幂等只对本人成立（高 1）。"""
    product = make_product(seller, category, status=PENDING)
    before = OperationLog.objects.count()
    with pytest.raises(PermissionDenied, match="只有卖家本人"):
        submit_for_audit(product, actor=other)
    assert OperationLog.objects.count() == before + 1
    log = OperationLog.objects.latest("id")
    assert (log.action, log.result) == ("product.edit", RESULT_DENIED)
    assert not OperationLog.objects.filter(action="product.transit").exists()
    assert AuditRecord.objects.filter(product=product).count() == 0


def test_audit_permission_is_checked_before_status(db, category, seller, other):
    """别人审核草稿得到 403，而不是「状态不允许」的 409（高 7）。"""
    product = make_product(seller, category, status=DRAFT)
    with pytest.raises(PermissionDenied, match="无权审核该商品"):
        audit(product, auditor=other, approve=True)
    assert OperationLog.objects.filter(action="product.audit", result=RESULT_DENIED).exists()
    product.refresh_from_db()
    assert product.status == DRAFT


def test_update_draft_from_rejected_returns_to_draft(db, category, seller, admin):
    """驳回后改稿：回到草稿、版本号 +1，再提交重审（REQ-004/005）。"""
    product = make_product(seller, category, status=PENDING)
    audit(product, auditor=admin, approve=False, reason="标题太笼统")
    older = product.version
    update_draft(product, actor=seller, title="旧相机（含镜头）", price=Decimal("95.00"))
    assert (product.status, product.version) == (DRAFT, older + 1)
    assert product.title == "旧相机（含镜头）"
    submit_for_audit(product, actor=seller)
    assert product.status == PENDING


def test_update_draft_rejects_stale_version(db, category, seller):
    """草稿编辑页也带版本号，旧页面不能覆盖别人刚改的内容（REQ-007）。"""
    product = make_product(seller, category, status=DRAFT)
    older = product.version
    update_draft(product, actor=seller, expected_version=older, title="改过一次")
    with pytest.raises(VersionConflict, match="请刷新"):
        update_draft(product, actor=seller, expected_version=older, title="旧页面")
    product.refresh_from_db()
    assert product.title == "改过一次"
    assert product.version == older + 1


def test_presale_flag_flows_through_business_entry(db, category, seller, admin):
    """预售勾选走正常业务入口：草稿带 is_presale，审核通过后落已预售（REQ-006）。"""
    product = save_draft(
        DraftInput(
            seller=seller,
            category=category,
            title="预售镜头",
            price=Decimal("120.00"),
            is_presale=True,
        )
    )
    add_image(product, "https://img.example.com/p.jpg", actor=seller)
    submit_for_audit(product, actor=seller)
    audit(product, auditor=admin, approve=True)
    assert (product.status, product.is_presale) == (PRESALE, True)


# ---------- R-205 查询与可见性 ----------


def test_guest_sees_only_public_products(db, category, seller):
    """详情口径：游客只看得到已发布与已预售（REQ-003）。"""
    make_product(seller, category, status=PUBLISHED, title="公开")
    make_product(seller, category, status=PRESALE, title="预售")
    make_product(seller, category, status=DRAFT, title="草稿")
    make_product(seller, category, status=LOCKED, title="锁定")
    titles = set(visible_to(None).values_list("title", flat=True))
    assert titles == {"公开", "预售"}


def test_seller_sees_own_drafts_but_not_other_peoples(db, category, seller, other):
    """详情口径：卖家能看到自己的草稿，看不到别人的（REQ-003）。"""
    make_product(seller, category, status=DRAFT, title="我的草稿")
    make_product(other, category, status=DRAFT, title="别人的草稿")
    titles = set(visible_to(seller).values_list("title", flat=True))
    assert "我的草稿" in titles
    assert "别人的草稿" not in titles


def test_staff_sees_every_state(db, category, seller, admin):
    """详情口径：管理员可查看任意状态的商品（REQ-012）。"""
    make_product(seller, category, status=DRAFT, title="草稿")
    make_product(seller, category, status=VIOLATED, title="违规")
    assert visible_to(admin).count() == 2


def test_listable_is_public_only_for_every_role(db, category, seller, support):
    """列表口径与角色无关：只有已发布/已预售，已售出永不出现（REQ-012）。"""
    make_product(seller, category, status=PUBLISHED, title="在售")
    make_product(seller, category, status=PRESALE, title="预售")
    make_product(seller, category, status=SOLD, title="已成交相机")
    make_product(seller, category, status=TRADING, title="交易中")
    assert set(listable().values_list("title", flat=True)) == {"在售", "预售"}
    assert not list(search(keyword="已成交"))
    for viewer in (seller, support):
        assert "已成交相机" in set(visible_to(viewer).values_list("title", flat=True))


def test_keyword_words_are_or_ed_under_one_condition(db, category, seller):
    """多关键词之间是「或」，命中任一关键词即返回（REQ-011）。"""
    make_product(seller, category, status=PUBLISHED, title="尼康 相机")
    make_product(seller, category, status=PUBLISHED, title="佳能 镜头")
    make_product(seller, category, status=PUBLISHED, title="三脚架")
    hits = search(keyword="尼康 镜头")
    assert {item.title for item in hits} == {"尼康 相机", "佳能 镜头"}


def test_category_and_price_range_are_anded(db, category, seller):
    """关键词、分类、价格区间之间是「与」（REQ-011）。"""
    other_category = Category.objects.create(name="家具")
    make_product(seller, category, status=PUBLISHED, title="便宜相机", price="50.00")
    make_product(seller, category, status=PUBLISHED, title="贵的相机", price="500.00")
    make_product(seller, other_category, status=PUBLISHED, title="相机柜", price="60.00")
    hits = search(keyword="相机", category_id=category.pk, price_min="40", price_max="100")
    assert [item.title for item in hits] == ["便宜相机"]


def test_sort_by_price_ascending(db, category, seller):
    """按价格升序排序（REQ-011）。"""
    make_product(seller, category, status=PUBLISHED, title="中", price="300.00")
    make_product(seller, category, status=PUBLISHED, title="低", price="100.00")
    make_product(seller, category, status=PUBLISHED, title="高", price="900.00")
    hits = search(sort="price_asc")
    assert [item.title for item in hits] == ["低", "中", "高"]


def test_invalid_price_range_raises(db):
    """价格下限大于上限直接报参数错误（REQ-011）。"""
    with pytest.raises(ValueError, match="下限"):
        search(price_min="200", price_max="100")


def test_page_beyond_last_returns_empty(db, category, seller):
    """页码越界返回空列表而不是报错（REQ-011）。"""
    make_product(seller, category, status=PUBLISHED)
    assert not list(search(page=99))
    assert len(search(page=1)) == 1


def test_page_size_is_stable(db, category, seller):
    """分页大小稳定，第二页只剩溢出的一条（REQ-011）。"""
    for index in range(PAGE_SIZE + 1):
        make_product(seller, category, status=PUBLISHED, title=f"商品{index}")
    assert len(search(page=1)) == PAGE_SIZE
    assert len(search(page=2)) == 1


def test_sold_product_disappears_from_search(db, category, seller, admin):
    """已售出商品不出现在任何人的检索结果里，但详情仍可按权限打开（REQ-012）。"""
    product = make_product(seller, category, status=SOLD, title="已成交相机")
    assert not list(search(keyword="已成交"))
    assert visible_to(admin).filter(pk=product.pk).exists()


# ---------- 日志闭环与内容快照（第三轮复核补丁） ----------


def test_create_edit_and_submit_are_logged_as_allowed(db, category, seller):
    """建草稿、补图、提交审核各写一条 allowed 日志（REQ-022 要求业务事件可追溯）。"""
    product = save_draft(
        DraftInput(seller=seller, category=category, title="镜头", price=Decimal("9.90"))
    )
    add_image(product, "https://img.example.com/a.jpg", actor=seller)
    submit_for_audit(product, actor=seller)
    actions = set(OperationLog.objects.filter(result="allowed").values_list("action", flat=True))
    assert {"product.create", "product.edit", "product.submit"} <= actions


def test_audit_and_unpublish_are_logged_as_allowed(db, category, seller, admin):
    """审核通过与卖家主动下架也要留 allowed 日志（中 6）。"""
    product = make_product(seller, category, status=PENDING)
    audit(product, auditor=admin, approve=True)
    unpublish(product, actor=seller)
    actions = set(OperationLog.objects.filter(result="allowed").values_list("action", flat=True))
    assert {"product.audit", "product.unpublish"} <= actions


def test_audit_record_keeps_content_snapshot(db, category, seller):
    """审核流水存下提交时的内容快照，日后改稿也能还原当时提交的是什么。"""
    product = save_draft(
        DraftInput(
            seller=seller,
            category=category,
            title="镜头",
            price=Decimal("9.90"),
            brand="尼康",
        )
    )
    add_image(product, "https://img.example.com/a.jpg", actor=seller)
    submit_for_audit(product, actor=seller)
    record = AuditRecord.objects.get(product=product)
    assert record.snapshot["title"] == "镜头"
    assert record.snapshot["price"] == "9.90"
    assert record.snapshot["brand"] == "尼康"
    assert record.snapshot["images"] == ["https://img.example.com/a.jpg"]


def test_revise_snapshot_is_taken_after_the_update(db, category, seller):
    """在架改稿的流水快照记的是改后的新内容，不是旧值（否则审核员看到假快照）。"""
    product = make_product(seller, category, status=PUBLISHED, price="100.00")
    revise_content(product, actor=seller, expected_version=product.version, price=Decimal("88.00"))
    record = AuditRecord.objects.get(product=product)
    assert record.snapshot["price"] == "88.00"


def test_as_bool_only_accepts_real_booleans(db):
    """`bool("false")` 是 True，所以布尔解析必须显式拒绝乱字符串（高 4）。"""
    assert as_bool(True) is True
    assert as_bool("false") is False
    assert as_bool("TRUE") is True
    for bad in ("1", "0", "yes", "", 1, None):
        with pytest.raises(InvalidContent):
            as_bool(bad)
