import os
import shutil
from pathlib import Path

IS_WINDOWS = os.name == "nt"


def _default_data_dir() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "cicd-app"
    return Path.home() / ".local/share/cicd-app"


def _find_bash() -> str | None:
    """Linux: bash có sẵn. Windows: tìm bash.exe của Git for Windows (không dùng bash.exe của WSL)."""
    if os.environ.get("CICD_SHELL"):
        return os.environ["CICD_SHELL"]
    if not IS_WINDOWS:
        return "bash"
    candidates = []
    git = shutil.which("git")
    if git:
        # git.exe nằm ở Git\cmd, Git\bin hoặc Git\mingw64\bin -> dò ngược lên thư mục Git
        for parent in list(Path(git).resolve().parents)[:3]:
            candidates += [parent / "bin" / "bash.exe", parent / "usr" / "bin" / "bash.exe"]
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"),
                 os.environ.get("LOCALAPPDATA") and os.path.join(os.environ["LOCALAPPDATA"], "Programs")):
        if base:
            candidates.append(Path(base) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file() and "system32" not in str(c).lower():
            return str(c)
    return None


DATA_DIR = Path(os.environ.get("CICD_DATA_DIR") or _default_data_dir()).expanduser()
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "cicd.db"
# 0.0.0.0 = lắng nghe trên mọi địa chỉ IP của máy (máy khác mở được bằng bất kỳ IP nào của máy).
# Chỉ cho chính máy này: CICD_HOST=127.0.0.1 (hoặc --host 127.0.0.1).
HOST = os.environ.get("CICD_HOST", "0.0.0.0")
PORT = int(os.environ.get("CICD_PORT", "8088"))
# Shell dùng để chạy các bước build/deploy
SHELL = _find_bash()
# Tự mở trình duyệt khi khởi động (run.bat bật cờ này)
OPEN_BROWSER = os.environ.get("CICD_OPEN_BROWSER") == "1"
# Chu kỳ (giây) mà bộ lập lịch kiểm tra các dự án
SCHEDULER_TICK = 5


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass
