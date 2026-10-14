"""Basic tests for discovery client."""

from unittest.mock import MagicMock, patch

import pytest


def test_import():
    from acps_cli.discovery import client

    assert hasattr(client, "trigger_sync")
    assert hasattr(client, "query")


def test_trigger_sync_calls_gateway_endpoints_in_order() -> None:
    from acps_cli.discovery.client import trigger_sync

    with patch("acps_cli.discovery.client.time.sleep"), patch("httpx.request") as mock_request:
        hard_reset_response = MagicMock()
        hard_reset_response.status_code = 200
        hard_reset_response.text = "{}"

        previous_status_response = MagicMock()
        previous_status_response.status_code = 200
        previous_status_response.text = (
            '{"object_count_by_type": {"acs": 0}, '
            '"needs_snapshot": true, "last_sync_time": null, "last_seq": null, '
            '"manual_sync_in_progress": false}'
        )

        sync_response = MagicMock()
        sync_response.status_code = 200
        sync_response.text = "{}"

        running_status_response = MagicMock()
        running_status_response.status_code = 200
        running_status_response.text = (
            '{"object_count_by_type": {"acs": 0}, '
            '"needs_snapshot": true, "last_sync_time": null, "last_seq": null, '
            '"manual_sync_in_progress": true}'
        )

        completed_status_response = MagicMock()
        completed_status_response.status_code = 200
        completed_status_response.text = (
            '{"object_count_by_type": {"acs": 1}, '
            '"needs_snapshot": false, "last_sync_time": "2026-06-16T00:00:00Z", '
            '"last_seq": 42, "manual_sync_in_progress": false}'
        )

        mock_request.side_effect = [
            hard_reset_response,
            previous_status_response,
            sync_response,
            running_status_response,
            completed_status_response,
        ]

        status_payload = trigger_sync("http://localhost:9000")

    assert mock_request.call_count == 5
    assert mock_request.call_args_list[0].kwargs["timeout"] == 30
    assert mock_request.call_args_list[1].args[1].endswith("/admin/dsp/status")
    assert mock_request.call_args_list[2].kwargs["timeout"] == 180
    assert mock_request.call_args_list[3].args[1].endswith("/admin/dsp/status")
    assert mock_request.call_args_list[4].args[1].endswith("/admin/dsp/status")
    assert status_payload["object_count_by_type"]["acs"] == 1


def test_trigger_sync_without_hard_reset_skips_reset_call() -> None:
    from acps_cli.discovery.client import trigger_sync

    with patch("acps_cli.discovery.client.time.sleep"), patch("httpx.request") as mock_request:
        previous_status_response = MagicMock()
        previous_status_response.status_code = 200
        previous_status_response.text = (
            '{"object_count_by_type": {"acs": 0}, '
            '"needs_snapshot": true, "last_sync_time": null, "last_seq": null, '
            '"manual_sync_in_progress": false}'
        )

        sync_response = MagicMock()
        sync_response.status_code = 200
        sync_response.text = '{"success": true}'

        running_status_response = MagicMock()
        running_status_response.status_code = 200
        running_status_response.text = (
            '{"object_count_by_type": {"acs": 0}, '
            '"needs_snapshot": true, "last_sync_time": null, "last_seq": null, '
            '"manual_sync_in_progress": true}'
        )

        completed_status_response = MagicMock()
        completed_status_response.status_code = 200
        completed_status_response.text = (
            '{"object_count_by_type": {"acs": 0}, '
            '"needs_snapshot": false, "last_sync_time": "2026-06-16T00:00:00Z", '
            '"last_seq": 42, "manual_sync_in_progress": false}'
        )

        mock_request.side_effect = [
            previous_status_response,
            sync_response,
            running_status_response,
            completed_status_response,
        ]

        status_payload = trigger_sync("http://localhost:9000", hard_reset=False, min_acs_count=0)

    assert mock_request.call_count == 4
    assert mock_request.call_args_list[0].args[1].endswith("/admin/dsp/status")
    assert mock_request.call_args_list[1].args[1].endswith("/admin/dsp/sync")
    assert mock_request.call_args_list[2].args[1].endswith("/admin/dsp/status")
    assert mock_request.call_args_list[3].args[1].endswith("/admin/dsp/status")
    assert status_payload["object_count_by_type"]["acs"] == 0


def test_trigger_sync_polls_status_after_gateway_timeout() -> None:
    from acps_cli.discovery.client import trigger_sync

    with patch("acps_cli.discovery.client.time.sleep"), patch("httpx.request") as mock_request:
        previous_status_response = MagicMock()
        previous_status_response.status_code = 200
        previous_status_response.text = (
            '{"object_count_by_type": {"acs": 0}, '
            '"needs_snapshot": true, "last_sync_time": null, "last_seq": null, '
            '"manual_sync_in_progress": false}'
        )

        sync_response = MagicMock()
        sync_response.status_code = 504
        sync_response.text = "Gateway Timeout"

        running_status_response = MagicMock()
        running_status_response.status_code = 200
        running_status_response.text = (
            '{"object_count_by_type": {"acs": 0}, '
            '"needs_snapshot": true, "last_sync_time": null, "last_seq": null, '
            '"manual_sync_in_progress": true}'
        )

        completed_status_response = MagicMock()
        completed_status_response.status_code = 200
        completed_status_response.text = (
            '{"object_count_by_type": {"acs": 2}, '
            '"needs_snapshot": false, "last_sync_time": "2026-06-16T00:00:00Z", '
            '"last_seq": 42, "manual_sync_in_progress": false}'
        )

        mock_request.side_effect = [
            previous_status_response,
            sync_response,
            running_status_response,
            completed_status_response,
        ]

        status_payload = trigger_sync("http://localhost:9000", hard_reset=False)

    assert mock_request.call_count == 4
    assert mock_request.call_args_list[0].args[1].endswith("/admin/dsp/status")
    assert mock_request.call_args_list[1].args[1].endswith("/admin/dsp/sync")
    assert mock_request.call_args_list[2].args[1].endswith("/admin/dsp/status")
    assert mock_request.call_args_list[3].args[1].endswith("/admin/dsp/status")
    assert status_payload["object_count_by_type"]["acs"] == 2


def test_wait_for_dsp_status_uses_manual_sync_flag_when_background_runtime_stays_running() -> None:
    from acps_cli.discovery.client import wait_for_dsp_status

    with patch("acps_cli.discovery.client.time.sleep"), patch("httpx.request") as mock_request:
        running_status_response = MagicMock()
        running_status_response.status_code = 200
        running_status_response.text = (
            '{"object_count_by_type": {"acs": 1}, '
            '"needs_snapshot": false, "last_sync_time": "2026-06-16T00:00:00Z", '
            '"last_seq": 41, "is_running": true, "manual_sync_in_progress": true}'
        )

        completed_status_response = MagicMock()
        completed_status_response.status_code = 200
        completed_status_response.text = (
            '{"object_count_by_type": {"acs": 1}, '
            '"needs_snapshot": false, "last_sync_time": "2026-06-16T00:00:01Z", '
            '"last_seq": 42, "is_running": true, "manual_sync_in_progress": false}'
        )

        mock_request.side_effect = [running_status_response, completed_status_response]

        status_payload = wait_for_dsp_status(
            "http://localhost:9000",
            previous_last_seq=40,
            previous_last_sync_time="2026-06-15T23:59:00Z",
        )

    assert mock_request.call_count == 2
    assert status_payload["last_seq"] == 42


def test_wait_for_dsp_status_treats_missing_manual_sync_flag_as_legacy_status() -> None:
    from acps_cli.discovery.client import wait_for_dsp_status

    with patch("acps_cli.discovery.client.time.sleep"), patch("httpx.request") as mock_request:
        completed_status_response = MagicMock()
        completed_status_response.status_code = 200
        completed_status_response.text = (
            '{"object_count_by_type": {"acs": 1}, '
            '"needs_snapshot": false, "last_sync_time": "2026-06-16T00:00:01Z", '
            '"last_seq": 42, "is_running": true}'
        )
        mock_request.return_value = completed_status_response

        status_payload = wait_for_dsp_status(
            "http://localhost:9000",
            previous_last_seq=41,
            previous_last_sync_time="2026-06-16T00:00:00Z",
        )

    assert mock_request.call_count == 1
    assert status_payload["last_seq"] == 42


def test_wait_for_dsp_status_raises_manual_sync_error_without_waiting_for_timeout() -> None:
    from acps_cli.discovery.client import DiscoveryError, wait_for_dsp_status

    with patch("acps_cli.discovery.client.time.sleep") as mock_sleep, patch("httpx.request") as mock_request:
        failed_status_response = MagicMock()
        failed_status_response.status_code = 200
        failed_status_response.text = (
            '{"object_count_by_type": {"acs": 0}, '
            '"needs_snapshot": true, "last_sync_time": null, "last_seq": null, '
            '"manual_sync_in_progress": false, "manual_sync_error": "registry unavailable"}'
        )
        mock_request.return_value = failed_status_response

        with pytest.raises(DiscoveryError, match="后台任务失败: registry unavailable"):
            wait_for_dsp_status("http://localhost:9000", wait_timeout=600)

    assert mock_request.call_count == 1
    mock_sleep.assert_not_called()


def test_trigger_sync_raises_gateway_error() -> None:
    from acps_cli.discovery.client import DiscoveryError, trigger_sync

    with patch("httpx.request") as mock_request:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_request.return_value = mock_response
        with pytest.raises(DiscoveryError, match="DSP hard-reset 失败"):
            trigger_sync("http://localhost:9000")


def test_get_dsp_status_applies_min_acs_count() -> None:
    from acps_cli.discovery.client import DiscoveryError, get_dsp_status

    with patch("httpx.request") as mock_request:
        status_response = MagicMock()
        status_response.status_code = 200
        status_response.text = '{"object_count_by_type": {"acs": 1}}'
        mock_request.return_value = status_response

        with pytest.raises(DiscoveryError, match="ACS 对象不足"):
            get_dsp_status("http://localhost:9000", min_acs_count=2)


def test_register_webhook_returns_json_payload() -> None:
    from acps_cli.discovery.client import register_webhook

    with patch("httpx.request") as mock_request:
        response = MagicMock()
        response.status_code = 200
        response.text = '{"id": "wh-001", "status": "active"}'
        mock_request.return_value = response

        payload = register_webhook(
            "http://localhost:9000",
            {
                "url": "http://localhost:9015/admin/dsp/webhooks/receive",
                "secret": "shared-secret",
                "types": ["acs"],
                "events": ["data_change"],
            },
            headers={"Authorization": "Bearer admin-token"},
        )

    assert payload["id"] == "wh-001"
    assert mock_request.call_args.kwargs["headers"]["Authorization"] == "Bearer admin-token"


def test_discovery_error_is_exception():
    from acps_cli.discovery.client import DiscoveryError

    err = DiscoveryError("test error")
    assert str(err) == "test error"
    assert isinstance(err, Exception)
