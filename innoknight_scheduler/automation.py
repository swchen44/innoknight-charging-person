from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from .scheduler import build_schedule_payload, cleanup_candidates, has_equivalent_schedule


@dataclass(frozen=True)
class AutomationConfig:
    """每日充電預約流程的可調整參數。

    `device_name` 沒有預設值：本專案是公開 repo，裝置名稱屬於個人資訊，
    一律由環境變數（GitHub Secrets）提供。
    """

    device_name: str
    start_time: str = "00:30"
    end_time: str = "06:00"
    ready_status: str = "充電樁已就緒"


@dataclass
class AutomationResult:
    """每日流程的結果摘要，供 CLI log 與測試斷言使用。"""

    removed_schedule_ids: list[int | str] = field(default_factory=list)
    created: bool = False
    skipped_reason: str | None = None
    log_lines: list[str] = field(default_factory=list)


class AutomationClient(Protocol):
    """每日流程需要的最小 client 介面，方便測試使用 fake client。"""

    def list_schedules(self) -> list[dict[str, Any]]: ...

    def remove_schedule(self, schedule_id: int | str) -> dict[str, Any]: ...

    def list_devices(self, keyword: str = "") -> list[dict[str, Any]]: ...

    def get_device_status(self, device: dict[str, Any]) -> str: ...

    def set_schedule(self, *, device: dict[str, Any], schedule_payload: dict[str, Any]) -> dict[str, Any]: ...


def _find_device(devices: list[dict[str, Any]], device_name: str) -> dict[str, Any] | None:
    for device in devices:
        if device.get("name") == device_name:
            return device
    return None


def _find_device_from_schedules(schedules: list[dict[str, Any]], device_name: str) -> dict[str, Any] | None:
    for schedule in schedules:
        nested = schedule.get("Device")
        if not isinstance(nested, dict) or nested.get("name") != device_name:
            continue
        device: dict[str, Any] = {"name": device_name}
        schedule_device_id = schedule.get("device_id")
        if schedule_device_id is not None:
            device["device_id"] = schedule_device_id
        if nested.get("id") is not None:
            device["device_uid"] = nested["id"]
        elif schedule_device_id is not None:
            # read_balance 有時只回 schedule.device_id，Device.id 會是 null；
            # get_latest_charging_record 接受這個 id，不能因此把狀態判成「其他」。
            device["device_uid"] = schedule_device_id
        for key in ("sn", "serialno", "serial_no"):
            if nested.get(key):
                device[key] = nested[key]
        return device
    return None


def _has_schedule_target(device: dict[str, Any]) -> bool:
    return any(device.get(key) for key in ("device_id", "schedule_device_id", "sn", "serialno", "serial_no"))


def run_daily_workflow(
    client: AutomationClient,
    config: AutomationConfig,
    *,
    today: date,
    execute: bool,
    target_date: date | None = None,
) -> AutomationResult:
    """執行每日夜間充電預約流程。

    流程會先清理舊的一次性排程（以 `today` 判斷過期），再檢查 `target_date`
    是否已存在相同預約；只有在目標充電樁狀態為「充電樁已就緒」且
    `execute=True` 時才會送出 `schedule_set` 遠端變更。

    `target_date` 預設等於 `today`；雲端排程版本會在前一晚觸發並把
    `target_date` 設成明天，避開 GitHub Actions 排程觸發器的延遲（詳見
    docs/design.md §4）。
    """

    target_date = target_date or today
    result = AutomationResult()
    schedules = client.list_schedules()

    for schedule_id in cleanup_candidates(schedules, today=today):
        result.log_lines.append(f"移除過期舊預約: {schedule_id}")
        result.removed_schedule_ids.append(schedule_id)
        if execute:
            client.remove_schedule(schedule_id)

    if has_equivalent_schedule(
        schedules,
        target_date=target_date,
        start_time=config.start_time,
        end_time=config.end_time,
    ):
        result.skipped_reason = "target_schedule_exists"
        result.log_lines.append(f"{target_date.isoformat()} 已存在相同預約，結束流程。")
        return result

    device = _find_device(client.list_devices(config.device_name), config.device_name)
    if device is None:
        # get_devices 有時搜不到特定社區充電樁；既有排程裡的 Device.name
        # 反而保有可用的 device_id，因此作為安全 fallback。
        device = _find_device_from_schedules(schedules, config.device_name)
    if device is None:
        result.skipped_reason = "device_not_found"
        result.log_lines.append(f"找不到目標充電樁: {config.device_name}")
        return result

    status = client.get_device_status(device)
    result.log_lines.append(f"目標充電樁狀態: {status}")
    if status != config.ready_status:
        result.skipped_reason = "device_not_ready"
        result.log_lines.append("狀態不是充電樁已就緒，跳過。")
        return result

    if not _has_schedule_target(device):
        result.skipped_reason = "device_missing_schedule_target"
        result.log_lines.append("目標充電樁缺少 device_id/SN，無法新增預約。")
        return result

    schedule_payload = build_schedule_payload(
        target_date=target_date,
        start_time=config.start_time,
        end_time=config.end_time,
    )
    result.log_lines.append(
        f"新增重點預約: {config.device_name} {target_date.isoformat()} {config.start_time}~{config.end_time}"
    )
    if execute:
        response = client.set_schedule(device=device, schedule_payload=schedule_payload)
        if not response.get("success", False):
            result.skipped_reason = "schedule_set_failed"
            result.log_lines.append(f"新增預約失敗: {response.get('message', '')}")
            return result
    result.created = True
    return result
