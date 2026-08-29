from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from .automation import AutomationConfig, run_daily_workflow
from .client import InnoKnightClient, InnoKnightSession

LOGIN_URL = "https://iot.innoknight.com/#/"
DEFAULT_CDP_PORT = 9224
DEFAULT_PROFILE_DIR = "/tmp/chrome-innoknight-cron"
# GitHub Actions Ubuntu runner 預裝 Google Chrome；正式路徑由
# INNOKNIGHT_CHROME_PATH 提供（workflow 內用 setup-chrome 的輸出）。
DEFAULT_CHROME_PATH = "/usr/bin/google-chrome"

# 設定錯誤（永遠不會自己好）→ 非 0，讓 GitHub Actions 失敗通知信變成告警管道。
# device_not_ready 等「今天本來就不用做事」的情況維持 0。
CONFIG_ERROR_REASONS = {"device_not_found", "device_missing_schedule_target"}


@dataclass(frozen=True)
class BrowserLoginConfig:
    """瀏覽器登入流程需要的帳密、CDP port 與 Chromium 啟動設定。"""

    username: str
    password: str
    cdp_port: int = DEFAULT_CDP_PORT
    login_url: str = LOGIN_URL
    timeout_seconds: int = 90
    chrome_path: str = DEFAULT_CHROME_PATH
    profile_dir: str = DEFAULT_PROFILE_DIR
    headless: bool = False


def parse_user_cookie(cookie: str) -> InnoKnightSession:
    """從瀏覽器的 `user` cookie 解析 InnoKnight session。

    只接受登入後頁面實際寫入的 cookie，避免排程流程依賴人工複製
    bearer token。若 cookie 缺少 `uuid` 或 `token`，會直接丟出錯誤，讓
    排程安全停止而不是送出不完整的遠端請求。
    """

    parts = dict(item.split("=", 1) for item in cookie.split("; ") if "=" in item)
    encoded_user = parts.get("user")
    if not encoded_user:
        raise RuntimeError("Browser session does not contain InnoKnight user cookie")
    user = json.loads(urllib.parse.unquote(encoded_user))
    if not isinstance(user, dict):
        raise RuntimeError("InnoKnight user cookie is not a JSON object")
    user_id = user.get("uuid") or user.get("user_id") or user.get("id")
    token = user.get("token")
    if not user_id or not token:
        raise RuntimeError("InnoKnight user cookie does not include uuid/token")
    return InnoKnightSession(user_id=str(user_id), token=str(token), raw_user=user)


def build_focus_script(field: str) -> str:
    """產生「找到並 focus 登入欄位」的 JavaScript。

    腳本刻意不接收帳號密碼——憑證一律由 CDP `Input.insertText` 直接送進
    已 focus 的欄位，不以字面出現在任何會被執行或印出的字串裡。GitHub
    secret masking 是字面比對，跳脫後的內嵌字串無法被遮罩，公開 repo 的
    Actions log 又全世界可讀，這條規則是硬性要求（docs/design.md §4）。
    """

    if field not in ("username", "password"):
        raise ValueError(f"unknown login field: {field}")
    selector_js = (
        "document.querySelector('input[type=\"password\"]')"
        " || inputs.filter(visible).find(el => el.type === 'password')"
        if field == "password"
        else "document.querySelector("
        "'input[type=\"email\"], input[name*=\"user\" i], input[placeholder*=\"帳\"], '"
        " + 'input[placeholder*=\"手機\"], input[placeholder*=\"email\" i]'"
        ") || inputs.filter(visible).find(el => el.type !== 'password')"
    )
    return f"""
(() => {{
  const inputs = Array.from(document.querySelectorAll('input'));
  function visible(el) {{
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }}
  const target = {selector_js};
  if (!target) {{
    return {{ ok: false, reason: 'login_input_not_found', field: '{field}', inputCount: inputs.length }};
  }}
  target.focus();
  target.value = '';
  return {{ ok: true, field: '{field}' }};
}})()
""".strip()


NOTIFY_ACTIVE_ELEMENT_SCRIPT = """
(() => {
  const el = document.activeElement;
  if (!el) return { ok: false, reason: 'no_active_element' };
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return { ok: true };
})()
""".strip()


def build_click_login_script() -> str:
    """產生尋找並點擊登入按鈕的 JavaScript（同樣不含任何憑證）。"""

    return """
(() => {
  function visible(el) {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }
  const buttons = Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"], a'));
  const loginButton = buttons.find(el => /會員登入充電|登入|login/i.test((el.innerText || el.value || '').trim()))
    || buttons.filter(visible).find(el => /submit|button/i.test(el.type || ''));
  if (!loginButton) {
    const passwordInput = document.querySelector('input[type="password"]');
    passwordInput?.form?.submit();
    return { ok: true, submitted: 'form' };
  }
  loginButton.click();
  return { ok: true, clicked: (loginButton.innerText || loginButton.value || '').trim() };
})()
""".strip()


class CdpClient:
    """最小化 Chrome DevTools Protocol client。

    只包裝本專案需要的 CDP 呼叫：等待 port、開頁、執行 JavaScript、送出
    文字輸入與關閉 websocket。保持小而明確，讓無人值守登入流程容易除錯。
    """

    def __init__(self, *, port: int, timeout: int = 10) -> None:
        self.port = port
        self.timeout = timeout
        self._next_id = 1
        self._ws: Any | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def wait_until_ready(self, *, timeout_seconds: int = 30) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                response = requests.get(f"{self.base_url}/json/version", timeout=2)
                if response.ok:
                    return
            except requests.RequestException as exc:
                last_error = exc
            time.sleep(0.5)
        raise RuntimeError(f"CDP port {self.port} did not become ready: {last_error}")

    def open(self, url: str) -> None:
        self.wait_until_ready(timeout_seconds=self.timeout)
        quoted_url = urllib.parse.quote(url, safe=":/#?&=")
        response = requests.put(f"{self.base_url}/json/new?{quoted_url}", timeout=self.timeout)
        if not response.ok:
            response = requests.get(f"{self.base_url}/json", timeout=self.timeout)
            response.raise_for_status()
            tabs = response.json()
            target = tabs[0] if isinstance(tabs, list) and tabs else None
        else:
            target = response.json()
        if not isinstance(target, dict) or not target.get("webSocketDebuggerUrl"):
            raise RuntimeError("Could not create or find a CDP page target")
        try:
            import websocket
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("Install websocket-client to use browser login automation") from exc
        self._ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=self.timeout)
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Page.navigate", {"url": url})

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("CDP websocket is not connected")
        request_id = self._next_id
        self._next_id += 1
        self._ws.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self._ws.recv())
            if message.get("id") == request_id:
                if "error" in message:
                    # 錯誤訊息只帶 CDP 回傳的 error 物件（code/message），
                    # 不回印我們送出的 params——insertText 的 params 內含密碼。
                    raise RuntimeError(f"CDP {method} failed: {message['error']}")
                result = message.get("result", {})
                return result if isinstance(result, dict) else {}

    def evaluate(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
        )
        remote = result.get("result", {})
        if isinstance(remote, dict):
            return remote.get("value")
        return None

    def insert_text(self, text: str) -> None:
        """把文字送進目前 focus 的欄位（等同使用者輸入/貼上）。"""

        self.call("Input.insertText", {"text": text})

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None


def start_chromium(config: BrowserLoginConfig) -> subprocess.Popen[str]:
    """用 Xvfb 啟動 Chrome/Chromium 並開啟遠端除錯 CDP port。"""

    profile = Path(config.profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    command = [
        "xvfb-run",
        "-a",
        config.chrome_path,
        "--no-sandbox",
        "--disable-dev-shm-usage",
        f"--remote-debugging-port={config.cdp_port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={config.profile_dir}",
    ]
    if config.headless:
        command.append("--headless=new")
    command.append("about:blank")
    # xvfb-run only kills its Xvfb child once the wrapped chromium process
    # exits on its own; terminating xvfb-run directly (as cleanup below
    # does) skips that step and leaks an orphaned Xvfb process. Running it
    # in its own process group lets cleanup signal the whole tree at once.
    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


def _chrome_failure_details(process: subprocess.Popen[str]) -> str:
    """收殮死掉/起不來的 Chrome，回傳 exit code 與輸出尾段供除錯。

    Chrome 的 argv/env 不含任何憑證，輸出可以安全印進 log。
    """

    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        output, _ = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
    tail = "\n".join((output or "").splitlines()[-40:])
    return f"chrome exit_code={process.returncode}\n--- chrome output (tail) ---\n{tail}"


def _fill_login_form(cdp: CdpClient, config: BrowserLoginConfig) -> None:
    """自動填入帳密並送出登入表單。

    憑證只經由 CDP `Input.insertText` 傳遞；focus/點擊腳本本身不含憑證，
    就算腳本或其錯誤訊息整段被印進 log 也不會洩漏任何機密。
    """

    for field, value in (("username", config.username), ("password", config.password)):
        focused = cdp.evaluate(build_focus_script(field))
        if not (isinstance(focused, dict) and focused.get("ok", False)):
            reason = focused.get("reason") if isinstance(focused, dict) else "unexpected_result"
            raise RuntimeError(f"Browser login form automation failed: {reason} ({field})")
        cdp.insert_text(value)
        # insertText 觸發的事件在部分前端框架（v-model 等）不足以更新綁定，
        # 補發一次 input/change 事件確保表單狀態同步。
        cdp.evaluate(NOTIFY_ACTIVE_ELEMENT_SCRIPT)

    clicked = cdp.evaluate(build_click_login_script())
    if not (isinstance(clicked, dict) and clicked.get("ok", False)):
        reason = clicked.get("reason") if isinstance(clicked, dict) else "unexpected_result"
        raise RuntimeError(f"Browser login submit failed: {reason}")


def login_with_browser(config: BrowserLoginConfig) -> InnoKnightSession:
    """透過 Chrome/CDP 登入 InnoKnight 並回傳瀏覽器 session。

    此函式是無人值守入口：啟動瀏覽器、填表登入、輪詢 `document.cookie`，
    取得 `user` cookie 後立刻清理 CDP 連線與瀏覽器程序。任何階段失敗都會
    丟出例外，避免後續預約流程使用錯誤狀態。
    """

    process: subprocess.Popen[str] | None = None
    cdp = CdpClient(port=config.cdp_port)
    try:
        process = start_chromium(config)
        try:
            cdp.wait_until_ready(timeout_seconds=30)
        except RuntimeError as exc:
            raise RuntimeError(f"{exc}\n{_chrome_failure_details(process)}") from exc
        cdp.open(config.login_url)
        time.sleep(3)
        # 先用 CDP 取得 browser session；排程 table 內容一律改由
        # read_balance endpoint 回傳 JSON 驗證，避免依賴畫面文字。
        _fill_login_form(cdp, config)
        deadline = time.monotonic() + config.timeout_seconds
        while time.monotonic() < deadline:
            cookie = cdp.evaluate("document.cookie")
            if isinstance(cookie, str) and "user=" in cookie:
                return parse_user_cookie(cookie)
            time.sleep(2)
        raise RuntimeError("Timed out waiting for InnoKnight browser login cookie")
    finally:
        cdp.close()
        if process is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
        subprocess.run(["pkill", "-f", f"remote-debugging-port={config.cdp_port}"], check=False)


def main() -> int:
    # 本機開發仍可用 .env；GitHub Actions 上沒有 .env 檔，全部環境變數
    # 來自 workflow 的 Secrets/Variables 注入。
    load_dotenv(override=True)
    parser = argparse.ArgumentParser(description="Cron-safe InnoKnight browser-session charging scheduler")
    parser.add_argument("--execute", action="store_true", help="Perform remote mutations. Default is dry-run.")
    parser.add_argument("--device-name", default=os.getenv("INNOKNIGHT_DEVICE_NAME"))
    parser.add_argument("--start-time", default=os.getenv("INNOKNIGHT_START_TIME", "00:30"))
    parser.add_argument("--end-time", default=os.getenv("INNOKNIGHT_END_TIME", "06:00"))
    parser.add_argument(
        "--target-offset-days",
        type=int,
        default=int(os.getenv("INNOKNIGHT_TARGET_OFFSET_DAYS", "1")),
        help="預約目標日 = 今天 + N 天。預設 1（明天）：雲端版在前一晚觸發，避開排程延遲。",
    )
    parser.add_argument("--cdp-port", type=int, default=int(os.getenv("INNOKNIGHT_CDP_PORT", str(DEFAULT_CDP_PORT))))
    parser.add_argument("--chrome-path", default=os.getenv("INNOKNIGHT_CHROME_PATH", DEFAULT_CHROME_PATH))
    parser.add_argument("--profile-dir", default=os.getenv("INNOKNIGHT_CHROME_PROFILE", DEFAULT_PROFILE_DIR))
    parser.add_argument("--login-timeout", type=int, default=int(os.getenv("INNOKNIGHT_LOGIN_TIMEOUT", "90")))
    args = parser.parse_args()

    username = os.getenv("INNOKNIGHT_USERNAME")
    password = os.getenv("INNOKNIGHT_PASSWORD")
    if not username or not password:
        raise SystemExit("Set INNOKNIGHT_USERNAME and INNOKNIGHT_PASSWORD (GitHub Secrets or .env)")
    if not args.device_name:
        raise SystemExit("Set INNOKNIGHT_DEVICE_NAME (GitHub Secrets or .env); there is no default device")

    print("Starting browser-session login flow")
    session = login_with_browser(
        BrowserLoginConfig(
            username=username,
            password=password,
            cdp_port=args.cdp_port,
            timeout_seconds=args.login_timeout,
            chrome_path=args.chrome_path,
            profile_dir=args.profile_dir,
        )
    )
    print(f"Browser login OK: user_id={session.user_id[:4]}***")

    client = InnoKnightClient()
    client.session = session
    config = AutomationConfig(
        device_name=args.device_name,
        start_time=args.start_time,
        end_time=args.end_time,
    )
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
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
