"""敏感信息脱敏（REQ-027）。非本人查看时一律走这里。"""


def mask_phone(phone: str) -> str:
    """13800001234 -> 138****1234；长度不足时整体打码。"""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) != 11:
        return "*" * len(phone)
    return f"{digits[:3]}****{digits[-4:]}"


def mask_text(value: str, *, keep: int = 1) -> str:
    """保留首尾各 keep 个字符，中间打码；过短则整体打码。"""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep * 2)}{value[-keep:]}"
