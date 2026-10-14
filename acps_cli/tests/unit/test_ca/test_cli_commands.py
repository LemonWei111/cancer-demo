"""单元测试 — cert 查询命令。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509 import ocsp
from cryptography.x509.oid import NameOID

from acps_cli.ca.keys import generate_private_key
from acps_cli.main import main


def _build_certificate(
    subject_name: str,
    issuer_name: x509.Name,
    public_key,
    signer_key,
    *,
    is_ca: bool = False,
) -> x509.Certificate:
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_name)]))
        .issuer_name(issuer_name)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
    )
    if is_ca:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    return builder.sign(private_key=signer_key, algorithm=hashes.SHA256())


def _write_ca_config(tmp_path: Path, aic: str) -> Path:
    keyfiles = tmp_path / "keyfiles"
    (keyfiles / "certs").mkdir(parents=True, exist_ok=True)
    (keyfiles / "accounts").mkdir(parents=True, exist_ok=True)
    (keyfiles / "private").mkdir(parents=True, exist_ok=True)
    (keyfiles / "csr").mkdir(parents=True, exist_ok=True)

    config_path = tmp_path / "acps-cli.toml"
    config_path.write_text(
        "[ca]\n"
        'base_url = "http://localhost:9003"\n'
        f'account_keys_dir = "{keyfiles / "accounts"}"\n'
        f'private_keys_dir = "{keyfiles / "private"}"\n'
        f'certs_dir = "{keyfiles / "certs"}"\n'
        f'csr_dir = "{keyfiles / "csr"}"\n'
        f'trust_bundle_path = "{keyfiles / "trust-bundle.pem"}"\n',
        encoding="utf-8",
    )
    return config_path


def _prepare_local_certificates(tmp_path: Path, aic: str) -> tuple[Path, bytes, bytes]:
    issuer_key = generate_private_key("ec")
    leaf_key = generate_private_key("ec")
    unrelated_key = generate_private_key("ec")

    issuer_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Issuer CA")])
    unrelated_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Unrelated CA")])

    issuer_cert = _build_certificate(
        "Issuer CA",
        issuer_name,
        issuer_key.public_key(),
        issuer_key,
        is_ca=True,
    )
    unrelated_cert = _build_certificate(
        "Unrelated CA",
        unrelated_name,
        unrelated_key.public_key(),
        unrelated_key,
        is_ca=True,
    )
    leaf_cert = _build_certificate(
        f"Agent {aic}",
        issuer_name,
        leaf_key.public_key(),
        issuer_key,
    )

    leaf_pem = leaf_cert.public_bytes(serialization.Encoding.PEM)
    issuer_pem = issuer_cert.public_bytes(serialization.Encoding.PEM)
    unrelated_pem = unrelated_cert.public_bytes(serialization.Encoding.PEM)

    cert_path = tmp_path / "keyfiles" / "certs" / f"{aic}.pem"
    cert_path.write_bytes(leaf_pem)

    trust_bundle_path = tmp_path / "keyfiles" / "trust-bundle.pem"
    trust_bundle_path.write_bytes(unrelated_pem + issuer_pem)

    return cert_path, leaf_pem, issuer_pem


@pytest.mark.unit
class TestCaCliQueryCommands:
    def test_check_ocsp_with_aic_emits_json_and_resolves_issuer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        aic = "1.2.3.4.TEST"
        config_path = _write_ca_config(tmp_path, aic)
        _, expected_cert_pem, expected_issuer_pem = _prepare_local_certificates(tmp_path, aic)
        captured: dict[str, object] = {}

        response = SimpleNamespace(
            response_status=ocsp.OCSPResponseStatus.SUCCESSFUL,
            certificate_status=ocsp.OCSPCertStatus.GOOD,
            serial_number=0xABCD,
            this_update_utc=datetime(2026, 4, 20, 8, 0, tzinfo=timezone.utc),
            next_update_utc=datetime(2026, 4, 21, 8, 0, tzinfo=timezone.utc),
            revocation_time_utc=None,
            revocation_reason=None,
        )

        def _fake_check_ocsp(self, cert_pem: bytes, issuer_pem: bytes, method: str = "post"):
            captured["cert_pem"] = cert_pem
            captured["issuer_pem"] = issuer_pem
            captured["method"] = method
            return response

        monkeypatch.setattr("acps_cli.ca.commands.AcmeClient.check_ocsp", _fake_check_ocsp)

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--config",
                str(config_path),
                "cert",
                "ocsp",
                "check",
                "--aic",
                aic,
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["responseStatus"] == "SUCCESSFUL"
        assert payload["certificateStatus"] == "GOOD"
        assert payload["serialNumber"] == "ABCD"
        assert captured["cert_pem"] == expected_cert_pem
        assert captured["issuer_pem"] == expected_issuer_pem
        assert captured["method"] == "post"

    def test_crl_info_outputs_json(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        config_path = _write_ca_config(tmp_path, "1.2.3.4.TEST")

        monkeypatch.setattr(
            "acps_cli.ca.commands.AcmeClient.get_crl_info",
            lambda self: {
                "version": "2026042008",
                "issuer": "CN=Agent CA",
                "thisUpdate": "2026-04-20T08:00:00+00:00",
                "nextUpdate": "2026-04-21T08:00:00+00:00",
                "revokedCertificatesCount": 1,
                "crlSize": 2048,
                "distributionPoint": "http://localhost:9003/acps-atr-v2/crl/current",
                "signature": {"algorithm": "sha256", "key_id": "issuer-key"},
            },
        )

        runner = CliRunner()
        result = runner.invoke(main, ["--config", str(config_path), "cert", "crl", "info"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["version"] == "2026042008"
        assert payload["revokedCertificatesCount"] == 1

    def test_ocsp_cert_status_derives_serial_from_local_cert(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        aic = "1.2.3.4.TEST"
        config_path = _write_ca_config(tmp_path, aic)
        cert_path, _, _ = _prepare_local_certificates(tmp_path, aic)
        expected_serial = format(x509.load_pem_x509_certificate(cert_path.read_bytes()).serial_number, "X")
        captured: dict[str, str] = {}

        def _fake_get_certificate_status(self, serial_number: str) -> dict[str, str]:
            captured["serial"] = serial_number
            return {
                "serialNumber": serial_number,
                "certificateStatus": "good",
            }

        monkeypatch.setattr(
            "acps_cli.ca.commands.AcmeClient.get_certificate_status",
            _fake_get_certificate_status,
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--config", str(config_path), "cert", "ocsp", "cert-status", "--aic", aic],
        )

        assert result.exit_code == 0, result.output
        assert captured["serial"] == expected_serial
        payload = json.loads(result.output)
        assert payload["serialNumber"] == expected_serial
