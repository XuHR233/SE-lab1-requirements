"""对 Day 1/2/4 的唯一出入口（REQ-020 的「只调用对外接口」）。

M5 只准从这里拿别人的东西。`tests/test_m5_isolation.py` 会强制这条规则：
删掉本文件里任何一个 import/名字，M5 就会在导入期报错，而不是悄悄多长一根依赖。
下面每个名字都注明来源，改别人家的接口时，只要同步这张清单即可。
"""

from __future__ import annotations

# --- M1 账号与权限 -----------------------------------------------------------
from accounts.models import (  # 角色判定：is_support / is_staff / is_active
    User,
)
from accounts.permissions import (  # 权限矩阵：只读口径，不复制一份
    allowed_by_role,
)

# --- M2 商品中心（状态机是唯一写入口）----------------------------------------
from catalog.models import (  # 状态常量
    PUBLISHED as PRODUCT_PUBLISHED,
)
from catalog.models import (
    SOLD as PRODUCT_SOLD,
    TRADING as PRODUCT_TRADING,
)
from catalog.models import (
    DRAFT as PRODUCT_DRAFT,
    OFFSHELF as PRODUCT_OFFSHELF,
    Product,
)

# --- M4 交易与资金（单据与流程是唯一写入口）----------------------------------
from trade.models import (
    ACTIVE_STATUSES as ORDER_ACTIVE_STATUSES,
    COMPLETED as ORDER_COMPLETED,
    DISPUTING as ORDER_DISPUTING,
    REFUNDED as ORDER_REFUNDED,
    WAITING_RECEIPT as ORDER_WAITING_RECEIPT,
    WAITING_SHIP as ORDER_WAITING_SHIP,
    Order,
)
from trade.services import (  # 订单流程用例：订单状态、版本、日志、商品联动都在里面
    open_dispute as order_open_dispute,
)
from trade.services import (
    refund_after_dispute as order_refund_after_dispute,
)
from trade.services import (
    resume_order as order_resume,
)
from trade.services import (
    terminate_after_dispute as order_terminate_after_dispute,
)

__all__ = [
    "User",
    "allowed_by_role",
    "Order",
    "ORDER_ACTIVE_STATUSES",
    "ORDER_COMPLETED",
    "ORDER_DISPUTING",
    "ORDER_REFUNDED",
    "ORDER_WAITING_RECEIPT",
    "ORDER_WAITING_SHIP",
    "order_open_dispute",
    "order_refund_after_dispute",
    "order_resume",
    "order_terminate_after_dispute",
    "Product",
    "PRODUCT_DRAFT",
    "PRODUCT_OFFSHELF",
    "PRODUCT_PUBLISHED",
    "PRODUCT_SOLD",
    "PRODUCT_TRADING",
]
