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
        schedule_payload={"weekly": False, "date": "2026-05-24", "start_time": "00:20", "end_time": "06:00"},
    )

    assert captured["endpoint"] == "schedule_set"
    assert "device_id" in captured["body"]
    assert "device_sn" not in captured["body"]
    assert captured["body"]["device_id"] != "1124"


def test_innoknight_client_uses_api_device_id_before_serial_number() -> None:
    captured: dict[str, Any] = {}
    client = InnoKnightClient()
    client.session = InnoKnightSession(user_id="user-1", token="token-1", raw_user={})

    def fake_post_mqtt(endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"success": True}

    client.post_mqtt = fake_post_mqtt  # type: ignore[method-assign]

    client.set_schedule(
        device={"name": DEVICE_NAME, "id": 1124, "sn": "XP012514000111"},
        schedule_payload={"weekly": False, "date": "2026-05-24", "start_time": "00:20", "end_time": "06:00"},
    )

    assert captured["endpoint"] == "schedule_set"
    assert "device_id" in captured["body"]
    assert "device_sn" not in captured["body"]


def test_innoknight_client_lists_devices_across_pages() -> None:
    page_one = [{"name": f"設備-{index}"} for index in range(25)]
    page_two = [{"name": DEVICE_NAME, "device_id": 1124}]
    calls: list[dict[str, Any]] = []
    client = InnoKnightClient()
    client.session = InnoKnightSession(user_id="user-1", token="token-1", raw_user={})

    def fake_post_mqtt(endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        assert endpoint == "get_devices"
        calls.append(body)
        return {"data": page_one if body["page"] == 1 else page_two}

    client.post_mqtt = fake_post_mqtt  # type: ignore[method-assign]

    devices = client.list_devices()

    assert devices == page_one + page_two
    assert [body["page"] for body in calls] == [1, 2]


def test_innoknight_client_stops_paging_after_target_is_found() -> None:
    page_one = [{"name": f"設備-{index}"} for index in range(25)]
    page_two = [{"name": DEVICE_NAME, "device_id": 1124}]
    calls: list[dict[str, Any]] = []
    client = InnoKnightClient()
    client.session = InnoKnightSession(user_id="user-1", token="token-1", raw_user={})

    def fake_post_mqtt(endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        assert endpoint == "get_devices"
        calls.append(body)
        if body["page"] == 1:
            return {"data": page_one}
        if body["page"] == 2:
            return {"data": page_two}
        raise AssertionError("device lookup should stop after the target is found")

    client.post_mqtt = fake_post_mqtt  # type: ignore[method-assign]

    devices = client.list_devices(stop_name=DEVICE_NAME)

    assert devices == page_one + page_two
    assert [body["page"] for body in calls] == [1, 2]


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

    def list_devices(self, keyword: str = "", *, stop_name: str | None = None) -> list[dict[str, Any]]:
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
    assert client.created[0][1]["start_time"] == "00:20"


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
