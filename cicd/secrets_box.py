"""Mã hoá token truy cập Git trước khi lưu vào database."""
import os

from cryptography.fernet import Fernet, InvalidToken

from . import config

_fernet: Fernet | None = None


def _get() -> Fernet:
    global _fernet
    if _fernet is None:
        key_file = config.DATA_DIR / "secret.key"
        if not key_file.exists():
            key_file.write_bytes(Fernet.generate_key())
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
        _fernet = Fernet(key_file.read_bytes().strip())
    return _fernet


def encrypt(value: str) -> str:
    return _get().encrypt(value.encode()).decode() if value else ""


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _get().decrypt(value.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError(
            "Không giải mã được token (file secret.key đã bị thay đổi?). Hãy nhập lại token trong phần Sửa dự án."
        ) from e
