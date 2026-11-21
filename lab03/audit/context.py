"""请求级上下文：把来源 IP 与端类型带给每一个日志写入点（REQ-022/026）。

日志要求"谁、从哪、用什么端、做了什么、成没成"。用例层（services）不接触 request，
所以来源信息放在上下文变量里，由中间件在进入请求时绑定、请求结束时复位。
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_ip: ContextVar[str | None] = ContextVar("audit_request_ip", default=None)
_client_type: ContextVar[str] = ContextVar("audit_client_type", default="")


def bind(*, ip: str | None, client_type: str) -> tuple[Token, Token]:
    """进入请求时绑定来源信息，返回的两个 token 供 reset 用。"""
    return _ip.set(ip), _client_type.set(client_type)


def reset(tokens: tuple[Token, Token]) -> None:
    """请求结束时还原上一个上下文（中间件嵌套或测试里连续请求时不能串味）。"""
    ip_token, client_token = tokens
    _ip.reset(ip_token)
    _client_type.reset(client_token)


def current_ip() -> str | None:
    """当前请求的来源 IP；不在请求里（如单元测试直接调服务层）时为 None。"""
    return _ip.get()


def current_client_type() -> str:
    """当前请求的端类型；不在请求里时返回空串。"""
    return _client_type.get()
