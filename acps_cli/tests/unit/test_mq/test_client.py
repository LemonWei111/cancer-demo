"""单元测试 — MqAuthClient HTTP 客户端（mock httpx.Client）。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from acps_cli.mq.client import MqAuthClient


def _make_client(tmp_path: Path, base_url: str = "https://localhost:9007") -> MqAuthClient:
    """创建测试用 MqAuthClient，跳过 SSL context 构建。"""
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    cert.write_text("FAKE CERT")
    key.write_text("FAKE KEY")
    return MqAuthClient(
        base_url=base_url,
        cert_file=str(cert),
        key_file=str(key),
        ca_cert_file=None,
        timeout=5,
    )


def _mock_response(status_code: int, text: str, content_type: str = "text/plain") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = {"content-type": content_type}
    # 纯文本响应：.json() 应抛出异常，让 _parse_body 回退到 .text
    resp.json.side_effect = ValueError("Not JSON")
    return resp


def _mock_json_response(status_code: int, data: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = json.dumps(data)
    resp.headers = {"content-type": "application/json"}
    resp.json.return_value = data
    return resp


def _patched_client_ctx(mock_http_client: MagicMock) -> MagicMock:
    """返回 _make_client 的 mock context manager。"""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_http_client)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


@pytest.mark.unit
class TestMqAuthClientInit:
    def test_base_url_stored(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path, base_url="https://mq.example.com:9007")
        assert client._base_url == "https://mq.example.com:9007"

    def test_base_url_trailing_slash_stripped(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path, base_url="https://mq.example.com:9007/")
        assert not client._base_url.endswith("/")

    def test_timeout_stored(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        assert client._timeout == 5

    def test_cert_tuple_stored(self, tmp_path: Path) -> None:
        cert = tmp_path / "client.crt"
        key = tmp_path / "client.key"
        cert.write_text("FAKE CERT")
        key.write_text("FAKE KEY")
        client = MqAuthClient(
            base_url="https://localhost:9007",
            cert_file=str(cert),
            key_file=str(key),
            timeout=5,
        )
        assert client._cert == (str(cert), str(key))


@pytest.mark.unit
class TestMqAuthClientMethods:
    """使用 patch _make_client 测试各方法。"""

    def test_get_returns_status_and_body(self, tmp_path: Path) -> None:
        mock_http = MagicMock()
        mock_http.get.return_value = _mock_response(200, "ok")

        client = _make_client(tmp_path)
        with patch.object(client, "_make_client", return_value=_patched_client_ctx(mock_http)):
            status, body = client.get("/health")

        assert status == 200
        assert body == "ok"

    def test_post_form_sends_form_encoded(self, tmp_path: Path) -> None:
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_response(200, "allow")

        client = _make_client(tmp_path)
        with patch.object(client, "_make_client", return_value=_patched_client_ctx(mock_http)):
            status, body = client.post_form("/auth/user", {"username": "AIC-001", "password": ""})

        assert status == 200
        assert body == "allow"
        # 确认是 form-encoded（data= 参数），而非 json=
        call_kwargs = mock_http.post.call_args
        assert "data" in call_kwargs.kwargs

    def test_delete_returns_204(self, tmp_path: Path) -> None:
        mock_http = MagicMock()
        mock_http.delete.return_value = _mock_response(204, "")

        client = _make_client(tmp_path)
        with patch.object(client, "_make_client", return_value=_patched_client_ctx(mock_http)):
            status, _body = client.delete("/groups/l1/g1")

        assert status == 204

    def test_put_returns_status_and_body(self, tmp_path: Path) -> None:
        mock_http = MagicMock()
        mock_http.put.return_value = _mock_json_response(200, {"ok": True})

        client = _make_client(tmp_path)
        with patch.object(client, "_make_client", return_value=_patched_client_ctx(mock_http)):
            status, body = client.put("/groups/l1/g1/members/m1")

        assert status == 200
        assert body == {"ok": True}

    def test_json_response_parsed(self, tmp_path: Path) -> None:
        mock_http = MagicMock()
        mock_http.get.return_value = _mock_json_response(200, {"status": "ok"})

        client = _make_client(tmp_path)
        with patch.object(client, "_make_client", return_value=_patched_client_ctx(mock_http)):
            status, body = client.get("/health")

        assert status == 200
        assert body == {"status": "ok"}
