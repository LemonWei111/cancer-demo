import json

from click.testing import CliRunner

from acps_cli.main import main


class StubAdminClient:
    def __init__(self):
        self.login_calls: list[tuple[str, str]] = []
        self.disable_calls: list[tuple[str, str]] = []
        self.enable_calls: list[str] = []

    def login(self, username: str, password: str):
        self.login_calls.append((username, password))
        return {
            "access_token": "token",
            "token_type": "bearer",
            "refresh_token": "refresh",
        }

    def list_review_agents(self, page_num: int, page_size: int, statuses: list[str]):
        return {"items": [{"id": "1", "approval_status": "PENDING"}], "total": 1}

    def process_review(self, agent_id: str, approve: bool, comments: str | None):
        return {
            "id": agent_id,
            "approval_status": "APPROVED" if approve else "REJECTED",
            "aic": "AIC-001",
            "approved": approve,
            "comments": comments,
        }

    def disable_agent(self, agent_id: str, reason: str):
        self.disable_calls.append((agent_id, reason))
        return {
            "id": agent_id,
            "aic": "AIC-001",
            "is_disabled": True,
            "disabled_reason": reason,
        }

    def enable_agent(self, agent_id: str):
        self.enable_calls.append(agent_id)
        return {
            "id": agent_id,
            "aic": "AIC-001",
            "is_disabled": False,
        }


def test_review_list_default_status(monkeypatch, empty_conf):
    runner = CliRunner()
    monkeypatch.setattr(
        "acps_cli.registry.unified.RegistryApiClient",
        lambda config: StubAdminClient(),
    )

    result = runner.invoke(
        main,
        ["--config", str(empty_conf), "admin", "registry", "review", "list", "--json"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["total"] == 1


def test_review_reject(monkeypatch, empty_conf):
    runner = CliRunner()
    monkeypatch.setattr(
        "acps_cli.registry.unified.RegistryApiClient",
        lambda config: StubAdminClient(),
    )

    result = runner.invoke(
        main,
        [
            "--config",
            str(empty_conf),
            "admin",
            "registry",
            "review",
            "reject",
            "--agent-id",
            "agent-1",
            "--comments",
            "invalid acs",
        ],
    )

    assert result.exit_code == 0
    assert "Rejected" in result.output


def test_review_approve_json_contains_flat_fields(monkeypatch, empty_conf):
    runner = CliRunner()
    monkeypatch.setattr(
        "acps_cli.registry.unified.RegistryApiClient",
        lambda config: StubAdminClient(),
    )

    result = runner.invoke(
        main,
        [
            "--config",
            str(empty_conf),
            "admin",
            "registry",
            "review",
            "approve",
            "--agent-id",
            "agent-1",
            "--json",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["agent_id"] == "agent-1"


def test_admin_login_uses_env_credentials(monkeypatch, empty_conf):
    runner = CliRunner()
    client = StubAdminClient()
    monkeypatch.setenv("REGISTRY_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("REGISTRY_ADMIN_PASSWORD", "admin123")
    monkeypatch.setattr("acps_cli.registry.unified.RegistryApiClient", lambda config: client)

    result = runner.invoke(
        main,
        ["--config", str(empty_conf), "admin", "auth", "login", "--json"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["username"] == "admin"
    assert client.login_calls == [("admin", "admin123")]


def test_admin_login_prompts_for_credentials(monkeypatch, tmp_path, empty_conf):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("acps_cli.shared.config.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("REGISTRY_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("REGISTRY_ADMIN_PASSWORD", raising=False)
    client = StubAdminClient()
    monkeypatch.setattr("acps_cli.registry.unified.RegistryApiClient", lambda config: client)

    result = runner.invoke(
        main,
        ["--config", str(empty_conf), "admin", "auth", "login", "--json"],
        input="prompt-admin\nprompt-secret\n",
    )

    assert result.exit_code == 0
    data = json.loads(result.output[result.output.find("{") :])
    assert data["username"] == "prompt-admin"
    assert client.login_calls == [("prompt-admin", "prompt-secret")]


def test_disable_agent_json_contains_flat_fields(monkeypatch, empty_conf):
    runner = CliRunner()
    client = StubAdminClient()
    monkeypatch.setattr("acps_cli.registry.unified.RegistryApiClient", lambda config: client)

    result = runner.invoke(
        main,
        [
            "--config",
            str(empty_conf),
            "admin",
            "registry",
            "agent",
            "disable",
            "--agent-id",
            "agent-1",
            "--reason",
            "manual review",
            "--json",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["agent_id"] == "agent-1"
    assert data["is_disabled"] is True
    assert data["disabled_reason"] == "manual review"
    assert client.disable_calls == [("agent-1", "manual review")]


def test_enable_agent_json_contains_flat_fields(monkeypatch, empty_conf):
    runner = CliRunner()
    client = StubAdminClient()
    monkeypatch.setattr("acps_cli.registry.unified.RegistryApiClient", lambda config: client)

    result = runner.invoke(
        main,
        [
            "--config",
            str(empty_conf),
            "admin",
            "registry",
            "agent",
            "enable",
            "--agent-id",
            "agent-1",
            "--json",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["agent_id"] == "agent-1"
    assert data["is_disabled"] is False
    assert client.enable_calls == ["agent-1"]
