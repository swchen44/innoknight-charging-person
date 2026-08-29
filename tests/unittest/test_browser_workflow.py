from __future__ import annotations

from datetime import date
from typing import Any

from innoknight_scheduler.automation import AutomationConfig, run_daily_workflow
from innoknight_scheduler.client import InnoKnightClient, InnoKnightSession

DEVICE_NAME = "測試充電樁A-1"


def test_innoknight_client_sets_schedule_by_encrypted_device_id_when_available() -> None:
    captured: dict[str, Any] = {}
    client = InnoKnightClient()
    client.session = InnoKnightSession(user_id="user-1", token="token-1", raw_user={})

    def fake_post_mqtt(endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"success": True}

    client.post_mqtt = fake_post_mqtt  # type: ignore[method-assign]

    client.set_schedule(
        device={"name": DEVICE_NAME, "device_id": 1124},
        schedule_payload={"weekly": False, "date": "2026-05-24", "start_time": "00:30", "end_time": "06:00"},
    )

    assert captured["endpoint"] == "schedule_set"
    assert "device_id" in captured["body"]
    assert "device_sn" not in captured["body"]
    assert captured["body"]["device_id"] != "1124"


class ScheduleFallbackClient:
    def __init__(self) -> None:
        self.created: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def list_schedules(self) -> list[dict[str, Any]]:
        return [
            {
                "id": 3671,
                "device_id": 1124,
                "Device": {"name": DEVICE_NAME, "id": 448},
                "date": 0,
                "weekly": 4,
            }
        ]

    def remove_schedule(self, schedule_id: int | str) -> dict[str, Any]:
        return {"success": True}

    def list_devices(self, keyword: str = "") -> list[dict[str, Any]]:
        return []

    def get_device_status(self, device: dict[str, Any]) -> str:
        assert device["device_id"] == 1124
        assert device["device_uid"] == 448
        return "充電樁已就緒"

    def set_schedule(self, *, device: dict[str, Any], schedule_payload: dict[str, Any]) -> dict[str, Any]:
        self.created.append((device, schedule_payload))
        return {"success": True}


def test_daily_workflow_falls_back_to_device_id_from_existing_schedules() -> None:
    client = ScheduleFallbackClient()

    result = run_daily_workflow(
        client,
        AutomationConfig(device_name=DEVICE_NAME),
        today=date(2026, 5, 24),
        execute=True,
    )

    assert result.created is True
    assert client.created[0][0]["device_id"] == 1124
    assert client.created[0][0]["device_uid"] == 448
    assert client.created[0][1]["start_time"] == "00:30"


class ScheduleFallbackWithoutNestedDeviceIdClient(ScheduleFallbackClient):
    def list_schedules(self) -> list[dict[str, Any]]:
        return [
            {
                "id": 3694,
                "device_id": 1124,
                "Device": {"name": DEVICE_NAME, "id": None},
                "date": "2026-05-24",
                "weekly": 0,
            }
        ]

    def get_device_status(self, device: dict[str, Any]) -> str:
        assert device["device_id"] == 1124
        assert device["device_uid"] == 1124
        return "充電樁已就緒"


def test_daily_workflow_uses_schedule_device_id_as_status_uid_when_nested_device_id_is_missing() -> None:
    client = ScheduleFallbackWithoutNestedDeviceIdClient()

    result = run_daily_workflow(
        client,
        AutomationConfig(device_name=DEVICE_NAME),
        today=date(2026, 5, 25),
        execute=True,
    )

    assert result.created is True
    assert client.created[0][0]["device_uid"] == 1124
