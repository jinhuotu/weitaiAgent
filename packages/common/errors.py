"""业务错误码与可抛异常。

路由层抛 ``AppError``，由 ``api.app`` 全局处理器转成统一 JSON。
"""

from enum import IntEnum


class ErrorCode(IntEnum):
    """业务码（与 HTTP status 分离：HTTP 表示传输，code 表示业务原因）。"""

    OK = 0
    BAD_REQUEST = 40000
    UNAUTHORIZED = 40100
    FORBIDDEN = 40300
    NOT_FOUND = 40400
    CONFLICT = 40900
    VALIDATION = 42200
    INTERNAL = 50000


class AppError(Exception):
    """可预期的业务错误。``status_code`` 写入 HTTP 状态。"""

    def __init__(
        self,
        code: int = ErrorCode.INTERNAL,
        msg: str = "internal error",
        status_code: int = 400,
    ) -> None:
        self.code = code
        self.msg = msg
        self.status_code = status_code
        super().__init__(msg)
