from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .automation import AutomationConfig, run_daily_workflow
from .browser_session import CONFIG_ERROR_REASONS
from .client import InnoKnightClient


def _today_taipei() -> datetime:
    return datetime.now(ZoneInfo("Asia/Taipei"))


def main() -> int:
    """Direct API 登入的開發測試入口。

    正式無人值守流程走 `browser_session`（網站對 direct API 登入可能要求
    reCAPTCHA/MFA）；這個入口保留給開發測試使用。
    """

    load_dotenv(override=True)
    parser = argparse.ArgumentParser(description="Nightly InnoKnight charging reservation helper (direct API)")
    parser.add_argument("--execute", action="store_true", help="Perform remote mutations. Default is dry-run.")
    parser.add_argument("--recaptcha-token", default=os.getenv("INNOKNIGHT_RECAPTCHA_TOKEN"))
    parser.add_argument("--device-name", default=os.getenv("INNOKNIGHT_DEVICE_NAME"))
    parser.add_argument("--start-time", default=os.getenv("INNOKNIGHT_START_TIME", "00:30"))
    parser.add_argument("--end-time", default=os.getenv("INNOKNIGHT_END_TIME", "06:00"))
    parser.add_argument(
        "--target-offset-days",
        type=int,
        default=int(os.getenv("INNOKNIGHT_TARGET_OFFSET_DAYS", "1")),
        help="預約目標日 = 今天 + N 天。預設 1（明天）。",
    )
    args = parser.parse_args()

    username = os.getenv("INNOKNIGHT_USERNAME")
    password = os.getenv("INNOKNIGHT_PASSWORD")
    if not username or not password:
        raise SystemExit("Set INNOKNIGHT_USERNAME and INNOKNIGHT_PASSWORD (GitHub Secrets or .env)")
    if not args.device_name:
        raise SystemExit("Set INNOKNIGHT_DEVICE_NAME (GitHub Secrets or .env); there is no default device")

    client = InnoKnightClient()
    session = client.login(username, password, recaptcha_token=args.recaptcha_token)
    print(f"Login OK: user_id={session.user_id[:4]}***")

    config = AutomationConfig(
        device_name=args.device_name,
        start_time=args.start_time,
        end_time=args.end_time,
    )
    today = _today_taipei().date()
    target_date = today + timedelta(days=args.target_offset_days)
    result = run_daily_workflow(client, config, today=today, execute=args.execute, target_date=target_date)

    for line in result.log_lines:
        print(line)

    if not args.execute:
        print("Dry-run only; no schedule was changed. Add --execute to mutate remote state.")

    if result.skipped_reason == "schedule_set_failed":
        return 1
    if result.skipped_reason in CONFIG_ERROR_REASONS:
        print(f"Configuration error ({result.skipped_reason}); failing the job so the alert email fires.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
