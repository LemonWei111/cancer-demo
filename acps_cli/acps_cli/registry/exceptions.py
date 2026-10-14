"""Registry 客户端自定义异常。"""

from __future__ import annotations


class RegistryClientError(RuntimeError):
    """Registry API 客户端错误，可附带 HTTP 元数据。"""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        payload: object | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload

    @property
    def error_name(self) -> str | None:
        if not isinstance(self.payload, dict):
            return None
        error_name = self.payload.get("error_name")
        if isinstance(error_name, str):
            return error_name
        code = self.payload.get("code")
        if isinstance(code, str):
            return code
        error = self.payload.get("error")
        if isinstance(error, dict):
            nested_code = error.get("code")
            if isinstance(nested_code, str):
                return nested_code
        return None

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"{self.message} (status={self.status_code})"
