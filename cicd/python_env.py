"""Chuẩn bị môi trường Python cho project: tạo <thư mục project>/.venv và cài thư viện."""
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

VENV_DIR = ".venv"
_MARK_PYTHON = ".cicd-python"     # interpreter đã dùng để tạo .venv
_MARK_DEPS = ".cicd-deps-hash"    # hash các file khai báo thư viện ở lần cài gần nhất


def venv_path(workdir: Path) -> Path:
    return workdir / VENV_DIR


def venv_python(workdir: Path) -> Path:
    v = venv_path(workdir)
    return v / "Scripts" / "python.exe" if config.IS_WINDOWS else v / "bin" / "python"


def venv_activate(workdir: Path) -> Path:
    v = venv_path(workdir)
    return v / "Scripts" / "activate" if config.IS_WINDOWS else v / "bin" / "activate"


def default_python() -> str:
    """Python mặc định để tạo .venv: chính Python gốc đang chạy CICD App."""
    return getattr(sys, "_base_executable", None) or sys.executable


def base_python_cmd(python_cmd: str) -> list[str]:
    cmd = (python_cmd or "").strip()
    if not cmd:
        return [default_python()]
    parts = shlex.split(cmd, posix=not config.IS_WINDOWS)
    return [p.strip('"') for p in parts]


def clean_env(env: dict) -> dict:
    """Bỏ các biến Python của CICD App để không ảnh hưởng .venv của project."""
    env = {k: v for k, v in env.items() if k not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "__PYVENV_LAUNCHER__")}
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PIP_DISABLE_PIP_VERSION_CHECK="1")
    return env


def _deps_spec(workdir: Path) -> tuple[list[str], list[Path]] | None:
    """Xác định cách cài thư viện: requirements.txt > pyproject.toml > setup.py."""
    req = workdir / "requirements.txt"
    if req.is_file():
        return ["-r", "requirements.txt"], [req]
    pyproject = workdir / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^\[(project|build-system)\]", text, re.M):
            return ["-e", "."], [pyproject] + [f for f in (workdir / "setup.cfg", workdir / "setup.py") if f.is_file()]
    setup_py = workdir / "setup.py"
    if setup_py.is_file():
        return ["-e", "."], [setup_py] + [f for f in (workdir / "setup.cfg",) if f.is_file()]
    return None


def python_hints(text: str) -> list[str]:
    t = text.lower()
    hints = []
    if "ensurepip is not available" in t or "no module named venv" in t or "no module named ensurepip" in t:
        hints.append("Thiếu gói venv của Python: chạy 'sudo apt install python3-venv' (hoặc python3.X-venv đúng phiên bản).")
    if "no matching distribution" in t or "could not find a version that satisfies" in t:
        hints.append("Không tìm thấy gói/phiên bản trong requirements.txt: kiểm tra tên gói, số phiên bản, "
                     "hoặc gói chưa hỗ trợ phiên bản Python đang dùng.")
    if "python.h" in t or ("gcc" in t and "error" in t) or "failed building wheel" in t:
        hints.append("Gói cần biên dịch: Linux chạy 'sudo apt install python3-dev build-essential'; "
                     "Windows cài 'Microsoft C++ Build Tools'.")
    if "microsoft visual c++" in t:
        hints.append("Cần 'Microsoft C++ Build Tools' để biên dịch gói này trên Windows.")
    if "newconnectionerror" in t or "proxyerror" in t or "could not fetch url" in t or "connection to pypi" in t \
            or "temporary failure in name resolution" in t:
        hints.append("Không kết nối được PyPI: kiểm tra mạng. Mạng công ty có proxy thì đặt biến HTTPS_PROXY "
                     "hoặc cấu hình 'pip config set global.proxy http://proxy:port'.")
    if "access is denied" in t or "permission denied" in t or "winerror 5" in t or "winerror 32" in t:
        hints.append("File trong .venv đang bị khoá: hãy dừng app Python của project đang chạy (dùng .venv này) rồi chạy lại.")
    if "is not recognized" in t or ("no such file or directory" in t and "python" in t):
        hints.append("Không tìm thấy Python được chỉ định: kiểm tra ô 'Python dùng để tạo .venv' trong phần Sửa project.")
    return hints


def setup(project: dict, workdir: Path, env: dict, timeout: int, log, run, before_change=None) -> int:
    """Tạo/cập nhật .venv và cài thư viện. `run` = pipeline._run. Trả về exit code.

    `before_change()` được gọi ngay trước khi .venv bị thay đổi (xoá/tạo lại/cài thư viện) - dùng để dừng app
    đang chạy (Windows khoá file của .venv khi app đang dùng). Không gọi nếu .venv giữ nguyên."""
    before_change = before_change or (lambda: None)
    env = clean_env(env)
    venv = venv_path(workdir)
    py = venv_python(workdir)
    base = base_python_cmd(project.get("python_cmd") or "")
    want = " ".join(base)

    def fail(code: int) -> int:
        for h in python_hints("\n".join(log.tail)):
            log.line("Gợi ý: " + h)
        return code

    # 1. Kiểm tra .venv hiện có
    recreate = False
    if py.exists():
        chk = subprocess.run([str(py), "-c", "import sys; print(sys.version.split()[0])"],
                             capture_output=True, text=True, env=env, timeout=60)
        marker = venv / _MARK_PYTHON
        if chk.returncode != 0:
            log.line(f".venv bị hỏng ({(chk.stderr or chk.stdout).strip()[:200]}) -> tạo lại")
            recreate = True
        elif marker.exists() and marker.read_text(encoding="utf-8").strip() != want:
            log.line(f"Python dùng để tạo .venv đã đổi ({marker.read_text(encoding='utf-8').strip()} -> {want}) -> tạo lại")
            recreate = True
        else:
            log.line(f".venv đã có (Python {chk.stdout.strip()}) -> dùng lại: {venv}")
    elif venv.exists():
        log.line(".venv tồn tại nhưng không có python -> tạo lại")
        recreate = True
    else:
        log.line(f"Chưa có .venv -> tạo mới: {venv}")

    if recreate:
        before_change()
        try:
            shutil.rmtree(venv)
        except OSError as e:
            log.line(f"!! Không xoá được .venv cũ: {e}")
            log.tail.append("access is denied")
            return fail(1)

    # 2. Tạo .venv
    if not py.exists():
        before_change()
        log.line("Đang tạo .venv - thường mất 10-30 giây, không có output trong lúc chờ ...")
        code = run([*base, "-m", "venv", str(venv)], str(workdir), env, timeout, log)
        if code != 0:
            return fail(code)
        (venv / _MARK_PYTHON).write_text(want, encoding="utf-8")
        ver = subprocess.run([str(py), "--version"], capture_output=True, text=True, env=env).stdout.strip()
        log.line(f"Đã tạo .venv ({ver})")

    # 3. Cài thư viện (bỏ qua nếu các file khai báo không đổi)
    spec = _deps_spec(workdir)
    if spec is None:
        log.line("Không có requirements.txt / pyproject.toml / setup.py -> không cần cài thư viện.")
        return 0
    args, files = spec
    h = hashlib.sha256((" ".join(args) + want).encode())
    for f in files:
        h.update(f.name.encode() + f.read_bytes())
    digest = h.hexdigest()
    marker = venv / _MARK_DEPS
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == digest:
        log.line(f"{files[0].name} không thay đổi từ lần cài trước -> bỏ qua cài thư viện.")
        return 0
    before_change()
    first = not marker.exists()
    log.line(f"Cài thư viện từ {files[0].name}" + (" - lần đầu có thể mất vài phút (tải và cài từng gói, xem tiến trình bên dưới)"
                                                     if first else ""))
    code = run([str(py), "-m", "pip", "install", *args], str(workdir), env, timeout, log)
    if code != 0:
        marker.unlink(missing_ok=True)
        return fail(code)
    marker.write_text(digest, encoding="utf-8")
    return 0


def step_env(workdir: Path, env: dict) -> tuple[dict, str]:
    """Env + đoạn lệnh bash kích hoạt .venv cho các bước build/deploy."""
    env = clean_env(env)
    act = venv_activate(workdir)
    if not act.exists():
        return env, ""
    env["CICD_VENV"] = str(venv_path(workdir))
    env["CICD_VENV_ACTIVATE"] = str(act).replace("\\", "/")
    return env, 'source "$CICD_VENV_ACTIVATE"\n'
