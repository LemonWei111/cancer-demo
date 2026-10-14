"""MqAuthClient：封装向 mq-auth-server 发起 mTLS HTTP 请求的低层客户端。"""

from __future__ import annotations

import ssl
from typing import Any

import click
import httpx


class MqAuthClient:
    """向 mq-auth-server 的 Group API 或 Auth API 发起 mTLS 请求。

    Args:
        base_url: 服务端根地址（含协议和端口，末尾不加斜杠）。
        cert_file: 客户端证书文件路径（PEM）。
        key_file: 客户端私钥文件路径（PEM）。
        ca_cert_file: 服务端 CA 证书文件路径（PEM），用于校验服务端证书；
            为 None 时使用系统默认 CA 存储。
        timeout: 请求超时秒数。
    """

    def __init__(
        self,
        base_url: str,
        cert_file: str,
        key_file: str,
        ca_cert_file: str | None = None,
        timeout: int = 10,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._cert = (cert_file, key_file)
        self._ca_cert_file = ca_cert_file
        self._timeout = timeout

    def _build_ssl_context(self) -> ssl.SSLContext:
        """构建双向 TLS SSL context。"""
        ctx = ssl.create_default_context()
        if self._ca_cert_file:
            ctx.load_verify_locations(cafile=self._ca_cert_file)
        cert_file, key_file = self._cert
        try:
            ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
        except OSError as exc:
            raise click.ClickException(f"无法加载客户端证书 ({cert_file}, {key_file}): {exc}") from exc
        return ctx

    def _make_client(self) -> httpx.Client:
        """构建带 mTLS 配置的 httpx.Client。"""
        ssl_ctx = self._build_ssl_context()
        return httpx.Client(verify=ssl_ctx, timeout=self._timeout)

    def get(self, path: str) -> tuple[int, Any]:
        """发送 GET 请求，返回 (status_code, response_body)。

        response_body 为解析后的 JSON（dict/list）或原始字符串（若非 JSON）。

        Raises:
            click.ClickException: 网络错误或连接失败。
        """
        url = f"{self._base_url}{path}"
        try:
            with self._make_client() as client:
                resp = client.get(url)
        except httpx.RequestError as exc:
            raise click.ClickException(f"连接 {url} 失败: {exc}") from exc
        return resp.status_code, _parse_body(resp)

    def post_form(self, path: str, data: dict[str, str]) -> tuple[int, Any]:
        """发送 application/x-www-form-urlencoded POST 请求，返回 (status_code, response_body)。

        Raises:
            click.ClickException: 网络错误或连接失败。
        """
        url = f"{self._base_url}{path}"
        try:
            with self._make_client() as client:
                resp = client.post(url, data=data)
        except httpx.RequestError as exc:
            raise click.ClickException(f"连接 {url} 失败: {exc}") from exc
        return resp.status_code, _parse_body(resp)

    def delete(self, path: str) -> tuple[int, Any]:
        """发送 DELETE 请求，返回 (status_code, response_body)。

        Raises:
            click.ClickException: 网络错误或连接失败。
        """
        url = f"{self._base_url}{path}"
        try:
            with self._make_client() as client:
                resp = client.delete(url)
        except httpx.RequestError as exc:
            raise click.ClickException(f"连接 {url} 失败: {exc}") from exc
        return resp.status_code, _parse_body(resp)

    def put(self, path: str) -> tuple[int, Any]:
        """发送 PUT 请求（无请求体），返回 (status_code, response_body)。

        Raises:
            click.ClickException: 网络错误或连接失败。
        """
        url = f"{self._base_url}{path}"
        try:
            with self._make_client() as client:
                resp = client.put(url)
        except httpx.RequestError as exc:
            raise click.ClickException(f"连接 {url} 失败: {exc}") from exc
        return resp.status_code, _parse_body(resp)


def _parse_body(resp: httpx.Response) -> Any:
    """解析响应体：尝试 JSON，失败则返回原始文本字符串。"""
    text = resp.text
    if not text:
        return None
    try:
        return resp.json()
    except Exception:
        return text
