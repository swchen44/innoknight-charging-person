from __future__ import annotations

import json
import random
import string
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .crypto import LOGIN_IV, SCHEDULE_IV, SCHEDULE_KEY, innoknight_encrypt, login_key

API_ROOT = "https://iot.innoknight.com/backend/api"
MQTT_ROOT = "https://iot.innoknight.com/backend/mqtt"


def _nonce(length: int = 30) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def _urlenc(value: str) -> str:
    return urllib.parse.quote(value, safe="")


@dataclass
class InnoKnightSession:
    """登入後取得的 InnoKnight 使用者 session 與原始 user payload。"""

    user_id: str
    token: str
    raw_user: dict[str, Any]


class InnoKnightClient:
    """封裝 InnoKnight direct API 與 MQTT endpoint 的 HTTP client。"""

    def __init__(self, *, api_root: str = API_ROOT, mqtt_root: str = MQTT_ROOT, timeout: int = 30):
        self.api_root = api_root.rstrip("/")
        self.mqtt_root = mqtt_root.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()
        self.session: InnoKnightSession | None = None

    def login(self, username: str, password: str, *, recaptcha_token: str | None = None) -> InnoKnightSession:
        """使用 direct API 登入 InnoKnight 並保存 bearer token。

        正式 crontab 通常使用 browser-session 登入；這個方法保留給開發測試，
        或網站當下沒有要求 reCAPTCHA/MFA 時使用。
        """

        payload = {
            "username": username,
            "secret": password,
            "google_captcha": recaptcha_token,
        }
        encrypted = innoknight_encrypt(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            key=login_key(datetime.now(ZoneInfo("Asia/Taipei"))),
            iv=LOGIN_IV,
        )
        response = self.http.post(
            f"{self.api_root}/get_end_user_token",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_nonce()}",
                "User-Agent": "Mozilla/5.0",
            },
            json={"keyset": "mqtt", "access": _urlenc(encrypted), "bring_navs": True},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("success"):
            raise RuntimeError(
                "InnoKnight login failed. The site may require reCAPTCHA, MFA, or the credentials may be invalid."
            )
        user_id = data.get("uuid") or data.get("user_id") or data.get("id")
        token = data.get("token")
        if not user_id or not token:
            raise RuntimeError("Login succeeded but response did not include user id/token")
        self.session = InnoKnightSession(user_id=str(user_id), token=str(token), raw_user=data)
        return self.session

    def post_mqtt(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        """送出已登入 session 的 MQTT API 請求並回傳 JSON dict。"""

        if self.session is None:
            raise RuntimeError("login first")
        response = self.http.post(
            f"{self.mqtt_root}/{endpoint.lstrip('/')}",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session.token}",
            },
            json=body,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    def encrypted_schedule_data(self, payload: dict[str, Any]) -> str:
        plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        return _urlenc(innoknight_encrypt(plaintext, key=SCHEDULE_KEY, iv=SCHEDULE_IV))

    def list_schedules(self) -> list[dict[str, Any]]:
        if self.session is None:
            raise RuntimeError("login first")
        data = self.post_mqtt("read_balance", {"device_id": 0, "user_id": self.session.user_id})
        user = data.get("user", {})
        schedules = user.get("schedules", []) if isinstance(user, dict) else []
        if not isinstance(schedules, list):
            return []
        return [schedule for schedule in schedules if isinstance(schedule, dict)]

    def list_devices(self, keyword: str = "") -> list[dict[str, Any]]:
        data = self.post_mqtt("get_devices", {"keyset": "mqtt", "keyword": _urlenc(keyword), "page": 1, "limit": 25})
        devices = data.get("data", [])
        if not isinstance(devices, list):
            return []
        return [device for device in devices if isinstance(device, dict)]

    def get_device_status(self, device: dict[str, Any]) -> str:
        device_uid = device.get("device_uid") or device.get("uid") or device.get("id")
        if device_uid is None:
            return "其他"
        data = self.post_mqtt(
            "get_latest_charging_record",
            {"user_id": 1, "device_ids": [device_uid], "keyset": "mqtt"},
        )
        if not data.get("success"):
            return "其他"
        records = data.get("records", [])
        if not isinstance(records, list) or not records:
            return "充電樁已就緒"
        record = records[0]
        if not isinstance(record, dict):
            return "其他"
        if record.get("stop_time"):
            return "其他"
        if record.get("start_time") and not record.get("end_time"):
            return "充電中"
        if record.get("status") == "end" or record.get("end_time"):
            return "充電樁已就緒"
        return "其他"

    def set_schedule(self, *, device: dict[str, Any], schedule_payload: dict[str, Any]) -> dict[str, Any]:
        """新增或更新充電預約，優先使用排程來源的 `device_id`。

        InnoKnight 前端送出 `schedule_set` 時會把 `device_id` 以排程金鑰加密；
        若缺少 `device_id`，才退回使用充電樁 SN。這能避免 get_devices 搜尋
        不完整時仍無法建立預約。
        """

        if self.session is None:
            raise RuntimeError("login first")
        body = {
            "user_id": _urlenc(self.session.user_id),
            "schedule_data": self.encrypted_schedule_data(schedule_payload),
        }
        device_id = device.get("device_id") or device.get("schedule_device_id")
        if device_id is not None:
            # schedule_set 的 device_id 必須依網站前端行為加密後再 URL encode。
            body["device_id"] = _urlenc(innoknight_encrypt(str(device_id), key=SCHEDULE_KEY, iv=SCHEDULE_IV))
        else:
            device_sn = device.get("sn") or device.get("serialno") or device.get("serial_no")
            if not device_sn:
                raise RuntimeError("Device does not include device_id or SN for schedule_set")
            body["device_sn"] = _urlenc(str(device_sn))
        return self.post_mqtt(
            "schedule_set",
            body,
        )

    def remove_schedule(self, schedule_id: int | str) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError("login first")
        payload = {"remove_schedule_id": schedule_id}
        return self.post_mqtt(
            "schedule_remove",
            {
                "user_id": _urlenc(self.session.user_id),
                "remove_data": self.encrypted_schedule_data(payload),
            },
        )
