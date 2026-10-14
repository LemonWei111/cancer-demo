"""集成测试：cert 证书申请命令。

覆盖范围：
    - cert 命令依赖真实运行的 ca-server（localhost:9003）
    - cert issue：使用 EAB 凭证申请 Agent 证书的完整 ACME 流程
    - cert trust-bundle update：拉取并更新本地信任包

测试前提：
    - 共享 PostgreSQL 已启动，且 registry-server / ca-server / discovery-server 已按 README 的本地联调命令启动
    - 测试用 EAB 需通过 registry 流程（cert eab fetch）获取，因此部分测试依赖完整 ATR 流程
  - 纯 ca-server 侧检测（health、directory 端点）不依赖 EAB
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from acps_cli.main import main

pytestmark = pytest.mark.integration


class TestCaCliConfig:
    """cert 配置文件相关测试。"""

    def test_missing_config_exits_with_error(self, work_dir: Path) -> None:
        """未指定配置文件且默认路径不存在时，cert issue 应以非零退出码退出。"""
        # 生成一个不存在的 EAB 文件路径
        eab = work_dir / "nonexistent.json"
        eab.write_text(
            '{"keyId": "k1", "macKey": "bWFja2V5c2VjcmV0MTIzNDU2Nzg5MEFCQ0RFRkdISUpLTE1OT1A=", "aic": "TEST.AIC"}',
            encoding="utf-8",
        )
        runner = CliRunner()

        result = runner.invoke(
            main,
            [
                "--config",
                str(work_dir / "nonexistent.conf"),
                "cert",
                "issue",
                "--aic",
                "TEST.AIC",
                "--eab-file",
                str(eab),
                "--usage",
                "clientAuth",
            ],
        )
        # 配置文件缺失 CA_BASE_URL 时应退出（exit code 2）
        assert result.exit_code != 0


class TestCaServerReachability:
    """验证 ca-server 基础端点可达性。"""

    def test_health_endpoint(self, ca_url: str) -> None:
        """ca-server /health 端点应返回 200。"""
        resp = httpx.get(f"{ca_url}/health", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "healthy"

    def test_acme_directory_endpoint(self, ca_url: str) -> None:
        """ca-server ACME directory 端点应返回有效 ACME 目录对象。"""
        resp = httpx.get(f"{ca_url}/acps-atr-v2/acme/directory", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        # ACME directory 必须包含 newNonce、newAccount、newOrder
        assert "newNonce" in data
        assert "newAccount" in data
        assert "newOrder" in data

    def test_new_nonce_endpoint(self, ca_url: str) -> None:
        """ca-server ACME newNonce 端点应返回 200 或 204。"""
        resp = httpx.head(f"{ca_url}/acps-atr-v2/acme/new-nonce", timeout=5)
        assert resp.status_code in (200, 204)
        # Replay-Nonce 头必须存在
        assert resp.headers.get("replay-nonce") is not None


class TestCaCliUpdateTrustBundle:
    """cert trust-bundle update 命令集成测试。"""

    def test_update_trust_bundle_downloads_pem(
        self,
        work_dir: Path,
        ca_conf: Path,
    ) -> None:
        """cert trust-bundle update 应成功下载并写入 trust bundle PEM 文件。"""
        trust_bundle_path = work_dir / "certs" / "trust-bundle.pem"
        runner = CliRunner()

        result = runner.invoke(
            main,
            [
                "--config",
                str(ca_conf),
                "cert",
                "trust-bundle",
                "update",
                "--output",
                str(trust_bundle_path),
            ],
        )

        assert result.exit_code == 0, f"cert trust-bundle update 失败，输出: {result.output}"
        assert trust_bundle_path.exists(), "trust bundle 文件未生成"
        content = trust_bundle_path.read_text(encoding="utf-8")
        assert "BEGIN CERTIFICATE" in content, "trust bundle 不包含有效证书"

    def test_update_trust_bundle_via_server_url_override(self, work_dir: Path, ca_url: str) -> None:
        """cert trust-bundle update 应支持统一的 group 级 --server-url 覆盖。"""
        trust_bundle_path = work_dir / "certs" / "trust-bundle.pem"
        runner = CliRunner()

        result = runner.invoke(
            main,
            [
                "cert",
                "--server-url",
                ca_url,
                "trust-bundle",
                "update",
                "--output",
                str(trust_bundle_path),
            ],
        )

        assert result.exit_code == 0, f"cert trust-bundle update via --server-url 失败，输出: {result.output}"
        assert trust_bundle_path.exists(), "trust bundle 文件未生成"


class TestCaCliCrlAndOcspQueries:
    """cert 的 CRL / OCSP 查询命令集成测试。"""

    @staticmethod
    def _refresh_crl(runner: CliRunner, ca_conf: Path) -> dict:
        result = runner.invoke(main, ["--config", str(ca_conf), "admin", "ca", "crl", "refresh"])
        assert result.exit_code == 0, f"refresh-crl 失败，输出: {result.output}"
        return json.loads(result.output)

    def test_crl_info_returns_metadata(self, ca_conf: Path) -> None:
        """crl-info 应返回当前 CRL 的元数据。"""
        runner = CliRunner()
        refreshed = self._refresh_crl(runner, ca_conf)

        result = runner.invoke(main, ["--config", str(ca_conf), "cert", "crl", "info"])

        assert result.exit_code == 0, f"crl-info 失败，输出: {result.output}"
        payload = json.loads(result.output)
        assert payload["version"] == refreshed["version"]
        assert "issuer" in payload
        assert "distribution_point" in payload

    def test_crl_detail_returns_revocation_payload(self, ca_conf: Path) -> None:
        """crl-detail 应返回当前 CRL 的详细吊销列表结构。"""
        runner = CliRunner()
        self._refresh_crl(runner, ca_conf)

        result = runner.invoke(main, ["--config", str(ca_conf), "cert", "crl", "detail"])

        assert result.exit_code == 0, f"crl-detail 失败，输出: {result.output}"
        payload = json.loads(result.output)
        assert "version" in payload
        assert "revokedCertificates" in payload
        assert "revokedCertificatesCount" in payload

    def test_crl_list_returns_paged_history(self, ca_conf: Path) -> None:
        """crl-list 应返回分页的 CRL 历史数据。"""
        runner = CliRunner()
        self._refresh_crl(runner, ca_conf)

        result = runner.invoke(main, ["--config", str(ca_conf), "admin", "ca", "crl", "list"])

        assert result.exit_code == 0, f"crl-list 失败，输出: {result.output}"
        payload = json.loads(result.output)
        assert "items" in payload
        assert "total" in payload
        assert payload["page"] == 1

    def test_download_crl_supports_historical_version(self, ca_conf: Path, work_dir: Path) -> None:
        """download-crl 应支持通过 --version 下载历史 CRL。"""
        runner = CliRunner()
        refreshed = self._refresh_crl(runner, ca_conf)
        output_path = work_dir / "certs" / f"ca-{refreshed['version']}.crl"

        result = runner.invoke(
            main,
            [
                "--config",
                str(ca_conf),
                "cert",
                "crl",
                "download",
                "--version",
                refreshed["version"],
                "--output",
                str(output_path),
            ],
        )

        assert result.exit_code == 0, f"download-crl --version 失败，输出: {result.output}"
        assert output_path.exists(), "历史 CRL 文件未生成"
        assert output_path.read_bytes(), "历史 CRL 文件为空"

    def test_ocsp_stats_returns_json(self, ca_conf: Path) -> None:
        """ocsp-stats 应返回 OCSP 服务统计信息。"""
        runner = CliRunner()

        result = runner.invoke(main, ["--config", str(ca_conf), "admin", "ca", "ocsp", "stats"])

        assert result.exit_code == 0, f"ocsp-stats 失败，输出: {result.output}"
        payload = json.loads(result.output)
        assert "total_requests" in payload
        assert "average_response_time_ms" in payload
