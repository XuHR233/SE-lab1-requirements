"""跨模块的业务异常基类：HTTP 层只认这两个基类，据此翻译 409 与 400。"""


class Conflict(Exception):
    """状态、版本或并发冲突（409）。"""


class InvalidInput(Exception):
    """入参或内容不合法（400）。"""
