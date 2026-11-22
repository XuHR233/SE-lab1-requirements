"""交易手续费口径（REQ-017）：1.5% 总费率，其中 0.6% 通道费、0.9% 平台服务费，单笔封顶 200 元。"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

RATE = Decimal("0.015")
CHANNEL_RATE = Decimal("0.006")
CAP = Decimal("200.00")
CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    """金额一律保留两位小数、四舍五入。"""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def fee_breakdown(amount: Decimal | str | int) -> dict[str, Decimal]:
    """按成交额算手续费、通道费、平台服务费与卖家实收。

    封顶时平台总收入仍是 200 元：通道费按实际成交额的 0.6% 计，余额算平台服务费。
    未发货全额退款不进这里——交易未完成，平台不扣服务费、通道费由平台承担。
    """
    gross = Decimal(amount)
    fee = min(_money(gross * RATE), CAP)
    channel = _money(gross * CHANNEL_RATE)
    # 极小金额的兜底：通道费不可能超过总手续费，否则卖家实收会多于成交额减去手续费。
    channel = min(channel, fee)
    return {
        "gross": gross,
        "fee": fee,
        "channel_fee": channel,
        "platform_fee": fee - channel,
        "seller_net": gross - fee,
    }
