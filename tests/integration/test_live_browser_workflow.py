"""真實環境整合測試：瀏覽器登入 InnoKnight + dry-run 每日流程。

這個測試就是 docs/plan.md Phase 2 的 go/no-go 驗證載體——在 GitHub Actions
上跑它，等於實測「Actions 共用 IP 會不會被 InnoKnight 風控擋下」。

執行條件（缺一即自動 skip）：
- 環境變數 INNOKNIGHT_USERNAME / INNOKNIGHT_PASSWORD / INNOKNIGHT_DEVICE_NAME
- Chrome 存在（INNOKNIGHT_CHROME_PATH 或預設路徑）；headless 模式不需要 Xvfb

只做 dry-run（execute=False），絕不改動遠端資料。
CI 的單元測試 job 不會執行本目錄（只跑 tests/unittest）。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from innoknight_scheduler.automation import AutomationConfig, run_daily_workflow
from innoknight_scheduler.browser_session import (
    DEFAULT_CHROME_PATH,
    BrowserLoginConfig,
    login_with_browser,
)
from innoknight_scheduler.client import InnoKnightClient

REQUIRED_ENV = ("INNOKNIGHT_USERNAME", "INNOKNIGHT_PASSWORD", "INNOKNIGHT_DEVICE_NAME")
_CHROME = os.getenv("INNOKNIGHT_CHROME_PATH", DEFAULT_CHROME_PATH)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        any(not os.getenv(name) for name in REQUIRED_ENV),
        reason="需要 INNOKNIGHT_USERNAME/PASSWORD/DEVICE_NAME 環境變數",
    ),
    pytest.mark.skipif(not os.path.exists(_CHROME), reason=f"找不到 Chrome：{_CHROME}"),
]


def test_browser_login_and_dry_run_daily_workflow() -> None:
    session = login_with_browser(
        BrowserLoginConfig(
            username=os.environ["INNOKNIGHT_USERNAME"],
            password=os.environ["INNOKNIGHT_PASSWORD"],
            chrome_path=os.getenv("INNOKNIGHT_CHROME_PATH", DEFAULT_CHROME_PATH),
            timeout_seconds=int(os.getenv("INNOKNIGHT_LOGIN_TIMEOUT", "90")),
        )
    )
    # 登入成功即代表沒有被 IP 風控／reCAPTCHA 擋下（go/no-go 的核心判準）。
    assert session.user_id
    assert session.token

    client = InnoKnightClient()
    client.session = session

    schedules = client.list_schedules()
    assert isinstance(schedules, list)

    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    result = run_daily_workflow(
        client,
        AutomationConfig(device_name=os.environ["INNOKNIGHT_DEVICE_NAME"]),
        today=today,
        execute=False,  # dry-run：絕不改動遠端資料
        target_date=today + timedelta(days=1),
    )

    # dry-run 一定會產生 log；設定錯誤（找不到裝置）要在這裡直接失敗，
    # 而不是綠燈通過。
    assert result.log_lines
    assert result.skipped_reason not in ("device_not_found", "device_missing_schedule_target")
