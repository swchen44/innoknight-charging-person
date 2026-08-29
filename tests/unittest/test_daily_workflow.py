from __future__ import annotations

import unittest
from datetime import date
from typing import Any

from innoknight_scheduler.automation import (
    AutomationConfig,
    run_daily_workflow,
)

DEVICE_NAME = "測試充電樁A-1"


class FakeClient:
    def __init__(
        self,
        *,
        schedules: list[dict[str, Any]] | None = None,
        devices: list[dict[str, Any]] | None = None,
        status: str = "其他",
    ) -> None:
        self.schedules = schedules or []
        self.devices = devices or []
        self.status = status
        self.removed: list[int | str] = []
        self.created: list[tuple[str, dict[str, Any]]] = []

    def list_schedules(self) -> list[dict[str, Any]]:
        return list(self.schedules)

    def remove_schedule(self, schedule_id: int | str) -> dict[str, Any]:
        self.removed.append(schedule_id)
        return {"success": True}

    def list_devices(self, keyword: str = "") -> list[dict[str, Any]]:
        return [device for device in self.devices if keyword in device.get("name", "")]

    def get_device_status(self, device: dict[str, Any]) -> str:
        return self.status

    def set_schedule(self, *, device: dict[str, Any], schedule_payload: dict[str, Any]) -> dict[str, Any]:
        self.created.append((str(device.get("sn") or device.get("device_id")), schedule_payload))
        return {"success": True}


class DailyWorkflowTest(unittest.TestCase):
    def config(self) -> AutomationConfig:
        return AutomationConfig(
            device_name=DEVICE_NAME,
            start_time="00:30",
            end_time="06:00",
            ready_status="充電樁已就緒",
        )

    def test_removes_all_but_the_most_recently_expired_schedule_before_other_checks(self) -> None:
        client = FakeClient(
            schedules=[
                {"id": 7, "date": "2026-05-16", "weekly": False},
                {"id": 8, "date": "2026-05-17", "weekly": False},
                {"id": 9, "date": "2026-05-01", "weekly": True},
                {"id": 10, "date": "2026-05-23", "weekly": False},
            ],
            devices=[{"name": DEVICE_NAME, "sn": "XP000000000000"}],
            status="充電樁已就緒",
        )

        result = run_daily_workflow(client, self.config(), today=date(2026, 5, 23), execute=True)

        # id 8（2026-05-17）離今天最近，保留；id 7 是另一筆過期排程，移除。
        # weekly（id 9）與今天的排程（id 10）都不受影響。
        self.assertEqual(client.removed, [7])
        self.assertIn("移除過期舊預約: 7", result.log_lines)

    def test_exits_without_creating_when_target_schedule_already_exists(self) -> None:
        client = FakeClient(
            schedules=[{"id": 1, "date": "2026-05-23", "weekly": False, "start_time": "00:30", "end_time": "06:00"}],
            devices=[{"name": DEVICE_NAME, "sn": "XP000000000000"}],
            status="充電樁已就緒",
        )

        result = run_daily_workflow(client, self.config(), today=date(2026, 5, 23), execute=True)

        self.assertEqual(client.created, [])
        self.assertEqual(result.skipped_reason, "target_schedule_exists")

    def test_creates_schedule_for_explicit_target_date_tomorrow(self) -> None:
        # 雲端版核心行為：前一晚（today）觸發，target_date = 明天。
        # 今天既有的預約不能擋下明天的建立。
        client = FakeClient(
            schedules=[{"id": 1, "date": "2026-05-23", "weekly": False, "start_time": "00:30", "end_time": "06:00"}],
            devices=[{"name": DEVICE_NAME, "sn": "XP000000000000"}],
            status="充電樁已就緒",
        )

        result = run_daily_workflow(
            client,
            self.config(),
            today=date(2026, 5, 23),
            execute=True,
            target_date=date(2026, 5, 24),
        )

        self.assertTrue(result.created)
        self.assertEqual(client.created[0][1]["date"], "2026-05-24")

    def test_skips_when_target_date_tomorrow_already_has_schedule(self) -> None:
        client = FakeClient(
            schedules=[{"id": 2, "date": "2026-05-24", "weekly": False, "start_time": "00:30", "end_time": "06:00"}],
            devices=[{"name": DEVICE_NAME, "sn": "XP000000000000"}],
            status="充電樁已就緒",
        )

        result = run_daily_workflow(
            client,
            self.config(),
            today=date(2026, 5, 23),
            execute=True,
            target_date=date(2026, 5, 24),
        )

        self.assertEqual(client.created, [])
        self.assertEqual(result.skipped_reason, "target_schedule_exists")

    def test_creates_today_schedule_when_target_device_is_ready(self) -> None:
        client = FakeClient(
            devices=[{"name": DEVICE_NAME, "sn": "XP000000000000"}],
            status="充電樁已就緒",
        )

        result = run_daily_workflow(client, self.config(), today=date(2026, 5, 23), execute=True)

        self.assertTrue(result.created)
        self.assertEqual(client.created[0][0], "XP000000000000")
        self.assertEqual(
            client.created[0][1],
            {"weekly": False, "date": "2026-05-23", "start_time": "00:30", "end_time": "06:00", "enable": True},
        )

    def test_skips_when_target_device_status_is_other(self) -> None:
        client = FakeClient(
            devices=[{"name": DEVICE_NAME, "sn": "XP000000000000"}],
            status="其他",
        )

        result = run_daily_workflow(client, self.config(), today=date(2026, 5, 23), execute=True)

        self.assertEqual(client.created, [])
        self.assertEqual(result.skipped_reason, "device_not_ready")

    def test_reports_device_not_found_as_skipped_reason(self) -> None:
        # 裝置名稱設錯是設定錯誤：CLI 入口會把這個 skipped_reason 轉成非 0
        # exit code，讓 GitHub Actions 的失敗通知信變成告警管道。
        client = FakeClient(devices=[], status="充電樁已就緒")

        result = run_daily_workflow(client, self.config(), today=date(2026, 5, 23), execute=True)

        self.assertEqual(result.skipped_reason, "device_not_found")


if __name__ == "__main__":
    unittest.main()
