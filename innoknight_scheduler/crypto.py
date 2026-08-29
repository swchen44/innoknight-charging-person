from __future__ import annotations

import base64
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

AES: Any
try:
    from Crypto.Cipher import AES
except ModuleNotFoundError:  # pragma: no cover - exercised when pycryptodome is absent
    AES = None
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


LOGIN_IV = "17ygVAPIMqtt<3e!"
SCHEDULE_KEY = "9aks*231!829371~"
SCHEDULE_IV = "9aks*2310sOw73j!"


def pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len]) * pad_len


def pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise ValueError("empty padded data")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError("invalid PKCS#7 padding")
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("invalid PKCS#7 padding bytes")
    return data[:-pad_len]


def login_key(now: datetime | None = None) -> str:
    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    return "17ygVAPI" + now.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d")


def _encrypt_aes_cbc(data: bytes, *, key: str, iv: str) -> bytes:
    if AES is not None:
        cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
        return cast(bytes, cipher.encrypt(data))
    cipher = Cipher(algorithms.AES(key.encode("utf-8")), modes.CBC(iv.encode("utf-8")))
    encryptor = cipher.encryptor()
    return cast(bytes, encryptor.update(data) + encryptor.finalize())


def _decrypt_aes_cbc(data: bytes, *, key: str, iv: str) -> bytes:
    if AES is not None:
        cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
        return cast(bytes, cipher.decrypt(data))
    cipher = Cipher(algorithms.AES(key.encode("utf-8")), modes.CBC(iv.encode("utf-8")))
    decryptor = cipher.decryptor()
    return cast(bytes, decryptor.update(data) + decryptor.finalize())


def innoknight_encrypt(plaintext: str, *, key: str, iv: str) -> str:
    encrypted = _encrypt_aes_cbc(pkcs7_pad(plaintext.encode("utf-8")), key=key, iv=iv)
    return base64.b64encode(encrypted).decode("ascii")


def innoknight_decrypt(ciphertext_b64: str, *, key: str, iv: str) -> str:
    encrypted = base64.b64decode(ciphertext_b64)
    return pkcs7_unpad(_decrypt_aes_cbc(encrypted, key=key, iv=iv)).decode("utf-8")
