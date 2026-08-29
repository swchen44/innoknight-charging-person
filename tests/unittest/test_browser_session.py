from __future__ import annotations

import json
import urllib.parse

import pytest

from innoknight_scheduler.browser_session import (
    NOTIFY_ACTIVE_ELEMENT_SCRIPT,
    build_click_login_script,
    build_focus_script,
    parse_user_cookie,
)


def test_parse_user_cookie_extracts_session_without_logging_full_cookie() -> None:
    user = {"uuid": "user-1234", "token": "secret-token", "gcc_rlt": {"action": "login"}}
    cookie = "foo=bar; user=" + urllib.parse.quote(json.dumps(user, separators=(",", ":"))) + "; theme=dark"

    session = parse_user_cookie(cookie)

    assert session.user_id == "user-1234"
    assert session.token == "secret-token"
    assert session.raw_user == user


def test_focus_scripts_locate_login_fields_without_any_credentials() -> None:
    # 公開 repo 的硬性規則：任何會被執行/印出的 JS 都不能含帳密。
    # focus 腳本連參數都不收憑證，這裡驗證選擇器邏輯仍在。
    username_script = build_focus_script("username")
    password_script = build_focus_script("password")

    assert "document.querySelectorAll" in username_script
    assert "placeholder*=\"帳\"" in username_script
    assert 'input[type="password"]' in password_script
    assert "target.focus()" in username_script
    assert "target.focus()" in password_script


def test_focus_script_rejects_unknown_field() -> None:
    with pytest.raises(ValueError):
        build_focus_script("token")


def test_click_login_script_targets_the_member_login_button() -> None:
    script = build_click_login_script()

    assert "會員登入充電" in script
    assert "loginButton.click()" in script


def test_notify_script_dispatches_input_and_change_events() -> None:
    assert "document.activeElement" in NOTIFY_ACTIVE_ELEMENT_SCRIPT
    assert "new Event('input'" in NOTIFY_ACTIVE_ELEMENT_SCRIPT
    assert "new Event('change'" in NOTIFY_ACTIVE_ELEMENT_SCRIPT


def test_fill_login_form_sends_credentials_only_via_insert_text() -> None:
    from innoknight_scheduler.browser_session import BrowserLoginConfig, _fill_login_form

    class FakeCdp:
        def __init__(self) -> None:
            self.evaluated: list[str] = []
            self.inserted: list[str] = []

        def evaluate(self, expression: str) -> dict[str, bool]:
            self.evaluated.append(expression)
            return {"ok": True}

        def insert_text(self, text: str) -> None:
            self.inserted.append(text)

    cdp = FakeCdp()
    config = BrowserLoginConfig(username="alice@example.com", password="p@ss'word")

    _fill_login_form(cdp, config)  # type: ignore[arg-type]

    assert cdp.inserted == ["alice@example.com", "p@ss'word"]
    # 憑證絕不出現在任何被 evaluate 的 JS 字串裡。
    for script in cdp.evaluated:
        assert "alice@example.com" not in script
        assert "p@ss'word" not in script


def test_fill_login_form_raises_when_field_is_missing() -> None:
    from innoknight_scheduler.browser_session import BrowserLoginConfig, _fill_login_form

    class MissingFieldCdp:
        def evaluate(self, expression: str) -> dict[str, object]:
            return {"ok": False, "reason": "login_input_not_found"}

        def insert_text(self, text: str) -> None:  # pragma: no cover - should not be reached
            raise AssertionError("credentials must not be sent when the field is missing")

    config = BrowserLoginConfig(username="u", password="p")
    with pytest.raises(RuntimeError, match="login_input_not_found"):
        _fill_login_form(MissingFieldCdp(), config)  # type: ignore[arg-type]
