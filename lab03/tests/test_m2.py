"""M2 验收测试：R-201 状态机 / R-202 审核流 / R-203 版本号 / R-204 下架与违规 / R-205 检索。"""

# pylint: disable=redefined-outer-name,unused-argument,too-many-arguments
# pylint: disable=too-many-positional-arguments
# 理由：pytest 的 fixture 按参数名注入；db fixture 不显式使用也要写在签名里。

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from accounts.permissions import can
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
)
from catalog.search import PAGE_SIZE, search, visible_to
from catalog.services import (
    DraftInput,
    InvalidContent,
    VersionConflict,
    add_image,
    audit,
    back_to_draft,
    revise_content,
    save_draft,
    submit_for_audit,
    takedown,
    unpublish,
)
from catalog.state import ALLOWED_PAIRS, StateConflict, can_transit, transit

User = get_user_model()


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


def make_product(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    seller,
    category,
    *,
    status=PUBLISHED,
    title="旧相机",
    price="100.00",
    is_presale=False,
):
    """直接落在指定状态，用于测状态机本身。"""
    product = Product.objects.create(
        seller=seller,
        category=category,
        title=title,
        price=Decimal(price),
        is_presale=is_presale,
        status=DRAFT,
    )
    if status != DRAFT:
        Product.objects.filter(pk=product.pk).update(status=status)
        product.refresh_from_db()
    return product


# ---------- R-201 状态机 ----------


def test_transition_table_is_exactly_the_25_pairs(db):
    """转换表就是 REQ-010 的 25 条，11 个状态一个不少。"""
    assert len(ALLOWED_PAIRS) == 25
    assert len(PRODUCT_STATUSES) == 11
    assert can_transit(DRAFT, PENDING)
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


def test_submit_moves_to_pending_and_opens_one_audit_record(db, category, seller):
    """提交后进入待审核，并开出一条审核流水（REQ-004）。"""
    product = save_draft(
        DraftInput(seller=seller, category=category, title="镜头", price=Decimal("9.90"))
    )
    add_image(product, "https://img.example.com/a.jpg")
    submit_for_audit(product, actor=seller)
    assert product.status == PENDING
    assert AuditRecord.objects.filter(product=product).count() == 1


def test_duplicate_submit_does_not_open_a_second_record(db, category, seller):
    """待审核期间重复提交是幂等的，流水仍只有一条（REQ-004）。"""
    product = save_draft(
        DraftInput(seller=seller, category=category, title="镜头", price=Decimal("9.90"))
    )
    add_image(product, "https://img.example.com/a.jpg")
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
    back_to_draft(product, actor=seller)
    add_image(product, "https://img.example.com/b.jpg")
    submit_for_audit(product, actor=seller)
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


def test_stale_version_is_rejected_and_content_untouched(db, category, seller, admin):
    """两个编辑页同时打开：后提交者拿旧版本号，必须被拒且不覆盖（REQ-007）。"""
    product = make_product(seller, category, status=PUBLISHED, price="100.00")
    stale = product.version
    revise_content(product, actor=seller, expected_version=stale, price=Decimal("88.00"))
    audit(product, auditor=admin, approve=True)
    assert product.version == stale + 1
    with pytest.raises(VersionConflict, match="请刷新"):
        revise_content(product, actor=seller, expected_version=stale, price=Decimal("1.00"))
    product.refresh_from_db()
    assert product.price == Decimal("88.00")


def test_trading_product_cannot_be_revised(db, category, seller):
    """交易中的商品不允许修改内容（REQ-007）。"""
    product = make_product(seller, category, status=TRADING)
    with pytest.raises(StateConflict, match="不允许修改"):
        revise_content(
            product, actor=seller, expected_version=product.version, price=Decimal("1.00")
        )


# ---------- R-204 下架与违规处置 ----------


def test_unpublish_hides_product(db, category, seller, other):
    """主动下架后商品从别人的检索结果里消失（REQ-008）。"""
    product = make_product(seller, category, status=PUBLISHED)
    unpublish(product, actor=seller)
    assert product.status == OFFSHELF
    assert not list(search(user=other, keyword="旧相机"))


def test_relist_must_pass_audit_again(db, category, seller):
    """已下架不能直接上架，退回草稿重提后是待审核（REQ-008）。"""
    product = make_product(seller, category, status=PUBLISHED)
    unpublish(product, actor=seller)
    back_to_draft(product, actor=seller)
    add_image(product, "https://img.example.com/c.jpg")
    submit_for_audit(product, actor=seller)
    assert product.status == PENDING


def test_takedown_requires_reason_and_admin_role(db, category, seller, admin, other):
    """违规下架必须给理由，且只有管理员能执行（REQ-009）。"""
    product = make_product(seller, category, status=PUBLISHED)
    with pytest.raises(InvalidContent, match="理由"):
        takedown(product, actor=admin, reason="   ")
    with pytest.raises(StateConflict, match="无权"):
        takedown(product, actor=other, reason="违规")
    takedown(product, actor=admin, reason="违规发布")
    assert product.status == VIOLATED


def test_sold_product_cannot_be_taken_down(db, category, seller, admin):
    """已售出商品不在违规下架的可处置范围内（REQ-009）。"""
    product = make_product(seller, category, status=SOLD)
    with pytest.raises(StateConflict):
        takedown(product, actor=admin, reason="违规")


# ---------- R-205 查询与可见性 ----------


def test_guest_sees_only_public_products(db, category, seller):
    """游客只看得到已发布与已预售（REQ-003）。"""
    make_product(seller, category, status=PUBLISHED, title="公开")
    make_product(seller, category, status=PRESALE, title="预售")
    make_product(seller, category, status=DRAFT, title="草稿")
    make_product(seller, category, status=LOCKED, title="锁定")
    titles = set(visible_to(None).values_list("title", flat=True))
    assert titles == {"公开", "预售"}


def test_seller_sees_own_drafts_but_not_other_peoples(db, category, seller, other):
    """卖家能看到自己的草稿，看不到别人的（REQ-003）。"""
    make_product(seller, category, status=DRAFT, title="我的草稿")
    make_product(other, category, status=DRAFT, title="别人的草稿")
    titles = set(visible_to(seller).values_list("title", flat=True))
    assert "我的草稿" in titles
    assert "别人的草稿" not in titles


def test_staff_sees_every_state(db, category, seller, admin):
    """管理员可查看任意状态的商品（REQ-012）。"""
    make_product(seller, category, status=DRAFT, title="草稿")
    make_product(seller, category, status=VIOLATED, title="违规")
    assert visible_to(admin).count() == 2


def test_keyword_words_are_or_ed_under_one_condition(db, category, seller):
    """多关键词之间是「或」，命中任一关键词即返回（REQ-011）。"""
    make_product(seller, category, status=PUBLISHED, title="尼康 相机")
    make_product(seller, category, status=PUBLISHED, title="佳能 镜头")
    make_product(seller, category, status=PUBLISHED, title="三脚架")
    hits = search(user=None, keyword="尼康 镜头")
    assert {item.title for item in hits} == {"尼康 相机", "佳能 镜头"}


def test_category_and_price_range_are_anded(db, category, seller):
    """关键词、分类、价格区间之间是「与」（REQ-011）。"""
    other_category = Category.objects.create(name="家具")
    make_product(seller, category, status=PUBLISHED, title="便宜相机", price="50.00")
    make_product(seller, category, status=PUBLISHED, title="贵的相机", price="500.00")
    make_product(seller, other_category, status=PUBLISHED, title="相机柜", price="60.00")
    hits = search(user=None, keyword="相机", category_id=category.pk, price_min="40", price_max="100")
    assert [item.title for item in hits] == ["便宜相机"]


def test_sort_by_price_ascending(db, category, seller):
    """按价格升序排序（REQ-011）。"""
    make_product(seller, category, status=PUBLISHED, title="中", price="300.00")
    make_product(seller, category, status=PUBLISHED, title="低", price="100.00")
    make_product(seller, category, status=PUBLISHED, title="高", price="900.00")
    hits = search(user=None, sort="price_asc")
    assert [item.title for item in hits] == ["低", "中", "高"]


def test_invalid_price_range_raises(db):
    """价格下限大于上限直接报参数错误（REQ-011）。"""
    with pytest.raises(ValueError, match="下限"):
        search(user=None, price_min="200", price_max="100")


def test_page_beyond_last_returns_empty(db, category, seller):
    """页码越界返回空列表而不是报错（REQ-011）。"""
    make_product(seller, category, status=PUBLISHED)
    assert not list(search(user=None, page=99))
    assert len(search(user=None, page=1)) == 1


def test_page_size_is_stable(db, category, seller):
    """分页大小稳定，第二页只剩溢出的一条（REQ-011）。"""
    for index in range(PAGE_SIZE + 1):
        make_product(seller, category, status=PUBLISHED, title=f"商品{index}")
    assert len(search(user=None, page=1)) == PAGE_SIZE
    assert len(search(user=None, page=2)) == 1


def test_sold_product_disappears_from_search(db, category, seller):
    """已售出商品不出现在任何人的检索结果里（REQ-012）。"""
    product = make_product(seller, category, status=SOLD, title="已成交相机")
    assert not list(search(user=None, keyword="已成交"))
    assert product.status == SOLD
