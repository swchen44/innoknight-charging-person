from __future__ import annotations

from datetime import date
from typing import Any


def build_schedule_payload(
    *,
    target_date: date,
    start_time: str = "00:30",
    end_time: str = "06:00",
    weekly: bool = False,
) -> dict[str, Any]:
    """建立 InnoKnight `schedule_set` 需要的預約 payload。

    預設建立今天的一次性夜間充電預約；若未來要建立 weekly 排程，
    InnoKnight API 期待 `date` 為 `0`。
    """

    return {
        "weekly": weekly,
        "date": target_date.isoformat() if not weekly else 0,
        "start_time": start_time,
        "end_time": end_time,
        "enable": True,
    }


def _parse_schedule_date(value: Any) -> date | None:
    if not value or value == 0:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def cleanup_candidates(
    schedules: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> list[Any]:
    """找出已過期的一次性預約 id，只留離今天最近的一筆，其餘全部標記移除。

    過期定義為 `date < today`；當天與未來的預約一律保留，避免誤刪今晚才要
    生效的排程。
    """

    today = today or date.today()
    expired: list[tuple[date, Any]] = []
    for schedule in schedules:
        if schedule.get("weekly"):
            continue
        schedule_date = _parse_schedule_date(schedule.get("date"))
        if schedule_date is not None and schedule_date < today and schedule.get("id") is not None:
            expired.append((schedule_date, schedule["id"]))

    if len(expired) <= 1:
        return []

    # 依日期新到舊排序，保留最近的一筆（index 0），其餘標記移除。
    expired.sort(key=lambda item: item[0], reverse=True)
    return [schedule_id for _, schedule_id in expired[1:]]


def _normalize_time(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    parts = value.split(":")
    if len(parts) < 2:
        return value
    return ":".join(parts[:2])


def has_equivalent_schedule(
    schedules: list[dict[str, Any]],
    *,
    target_date: date,
    start_time: str = "00:30",
    end_time: str = "06:00",
) -> bool:
    """判斷今天是否已存在相同日期與時間的充電預約。"""

    expected_start = _normalize_time(start_time)
    expected_end = _normalize_time(end_time)
    for schedule in schedules:
        if _normalize_time(schedule.get("start_time")) != expected_start:
            continue
        if _normalize_time(schedule.get("end_time")) != expected_end:
            continue
        if _parse_schedule_date(schedule.get("date")) == target_date:
            return True
    return False
