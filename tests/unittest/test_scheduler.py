from datetime import date
from typing import Any

from innoknight_scheduler.crypto import innoknight_decrypt, innoknight_encrypt, pkcs7_pad
from innoknight_scheduler.scheduler import build_schedule_payload, cleanup_candidates, has_equivalent_schedule


def test_pkcs7_pad_adds_full_block_for_aligned_input() -> None:
    assert pkcs7_pad(b"1234567890abcdef") == b"1234567890abcdef" + bytes([16]) * 16


def test_encrypt_decrypt_roundtrip_with_fixed_key_iv() -> None:
    key = "9aks*231!829371~"
    iv = "9aks*2310sOw73j!"
    plaintext = '{"start_time":"00:05","end_time":"06:00"}'
    ciphertext = innoknight_encrypt(plaintext, key=key, iv=iv)
    assert ciphertext != plaintext
    assert innoknight_decrypt(ciphertext, key=key, iv=iv) == plaintext


def test_build_schedule_payload_defaults_to_midnight_window() -> None:
    payload = build_schedule_payload(target_date=date(2026, 5, 24))
    assert payload == {
        "weekly": False,
        "date": "2026-05-24",
        "start_time": "00:30",
        "end_time": "06:00",
        "enable": True,
    }


def test_cleanup_candidates_keeps_only_the_most_recently_expired_schedule() -> None:
    schedules: list[dict[str, Any]] = [
        {"id": 1, "date": "2026-05-10", "weekly": False},
        {"id": 2, "date": "2026-05-20", "weekly": False},
        {"id": 3, "date": "2026-05-01", "weekly": True},
        {"id": 4, "date": 0, "weekly": False},
    ]
    # 2026-05-20（id 2）離 today 最近，保留；其餘過期的一次性排程移除。
    # weekly（id 3）與無法解析日期的排程（id 4）一律不動。
    assert cleanup_candidates(schedules, today=date(2026, 5, 23)) == [1]


def test_cleanup_candidates_removes_nothing_when_at_most_one_expired() -> None:
    schedules: list[dict[str, Any]] = [
        {"id": 1, "date": "2026-05-20", "weekly": False},
    ]
    assert cleanup_candidates(schedules, today=date(2026, 5, 23)) == []
    assert cleanup_candidates([], today=date(2026, 5, 23)) == []


def test_cleanup_candidates_never_removes_todays_or_future_schedules() -> None:
    schedules: list[dict[str, Any]] = [
        {"id": 1, "date": "2026-05-19", "weekly": False},
        {"id": 2, "date": "2026-05-23", "weekly": False},  # 今天
        {"id": 3, "date": "2026-05-24", "weekly": False},  # 未來（明天的預約）
    ]
    # 只有 id 1 過期，且是唯一一筆過期排程，依規則保留、不移除任何東西。
    assert cleanup_candidates(schedules, today=date(2026, 5, 23)) == []


def test_has_equivalent_schedule_treats_hh_mm_and_hh_mm_ss_as_same_window() -> None:
    schedules: list[dict[str, Any]] = [
        {"id": 3694, "date": "2026-05-24", "weekly": 0, "start_time": "00:30:00", "end_time": "06:00:00"}
    ]

    assert has_equivalent_schedule(
        schedules,
        target_date=date(2026, 5, 24),
        start_time="00:30",
        end_time="06:00",
    )


def test_has_equivalent_schedule_distinguishes_target_dates() -> None:
    # 前一晚 22:05 檢查「明天」時，今天既有的預約不能被誤認為明天已有預約。
    schedules: list[dict[str, Any]] = [
        {"id": 1, "date": "2026-05-23", "weekly": False, "start_time": "00:30", "end_time": "06:00"}
    ]

    assert not has_equivalent_schedule(schedules, target_date=date(2026, 5, 24))
    assert has_equivalent_schedule(schedules, target_date=date(2026, 5, 23))
