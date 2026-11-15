"""Deploy: chạy app của project sau khi cập nhật mã nguồn + thiết lập môi trường.

- Chế độ "service": mỗi dòng lệnh là một tiến trình chạy nền (vd. web app, bot). CICD App dừng phiên bản cũ,
  khởi động phiên bản mới, kiểm tra nó còn sống (và URL kiểm tra phản hồi), ghi output vào log riêng.
  Tiến trình vẫn chạy tiếp khi pipeline kết thúc hoặc khi tắt CICD App.
- Chế độ "once": chạy lệnh và chờ kết thúc (script chạy 1 lần) - xử lý trong pipeline như một bước thường.
"""
import json
import logging
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

import psutil

from . import config, python_env, store

MAX_LOG_BYTES = 10 * 1024 * 1024
logger = logging.getLogger("cicd.app")


class TextLog:
    """Logger đơn giản (cho nút Khởi động/Dừng trên web và tự khởi động) - cùng giao diện với RunLogger."""

    def __init__(self):
        self.lines: list[str] = []
        self.tail: deque[str] = deque(maxlen=40)

    def line(self, text: str = "") -> None:
        self.lines.append(text)
        self.tail.append(text)


def commands(project: dict) -> list[str]:
    return [l.strip() for l in (project.get("run_cmd") or "").splitlines() if l.strip() and not l.strip().startswith("#")]


def log_path(pid: int, idx: int) -> Path:
    return config.DATA_DIR / "applogs" / f"project_{pid}_{idx + 1}.log"


def _load(project: dict) -> list[dict]:
    try:
        return json.loads(project.get("app_procs") or "[]")
    except ValueError:
        return []


def _save(pid: int, recs: list[dict]) -> None:
    store.execute("UPDATE projects SET app_procs=? WHERE id=?", (json.dumps(recs), pid))


def _alive(rec: dict) -> psutil.Process | None:
    try:
        p = psutil.Process(rec["pid"])
        if rec.get("ct") and abs(p.create_time() - rec["ct"]) > 1.0:   # PID đã bị hệ điều hành cấp cho tiến trình khác
            return None
        if p.status() == psutil.STATUS_ZOMBIE:
            return None
        return p
    except (psutil.Error, KeyError, TypeError):
        return None


def status(project: dict) -> list[dict]:
    """Trạng thái từng tiến trình của app (theo từng dòng lệnh)."""
    recs = {r.get("idx"): r for r in _load(project)}
    out = []
    for i, cmd in enumerate(commands(project)):
        rec = recs.get(i)
        alive = _alive(rec) if rec and rec.get("cmd") == cmd else None
        out.append({"idx": i, "cmd": cmd, "running": alive is not None, "pid": rec["pid"] if alive else None,
                    "started_at": rec.get("started_at") if alive else None, "log": str(log_path(project["id"], i)),
                    "has_log": log_path(project["id"], i).exists()})
    return out


def is_running(project: dict) -> bool:
    return any(_alive(r) for r in _load(project))


def _append_marker(pid: int, idx: int, text: str) -> None:
    p = log_path(pid, idx)
    if p.exists():
        with open(p, "ab") as f:
            f.write(f"===== {time.strftime('%d/%m/%Y %H:%M:%S')} {text} =====\n".encode("utf-8"))


def stop(project: dict, log=None) -> int:
    """Dừng mọi tiến trình của app (cả tiến trình con). Trả về số tiến trình chính đã dừng."""
    recs = _load(project)
    targets, stopped = [], 0
    for rec in recs:
        p = _alive(rec)
        if p is None:
            continue
        stopped += 1
        try:
            targets += p.children(recursive=True)
        except psutil.Error:
            pass
        targets.append(p)
        if log:
            log.line(f"■ Dừng [{rec['idx'] + 1}] {rec['cmd']} (PID {rec['pid']})")
    for t in targets:
        try:
            t.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(targets, timeout=10)
    for t in alive:
        try:
            t.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=5)
    for rec in recs:
        _append_marker(project["id"], rec.get("idx", 0), "Đã dừng")
    _save(project["id"], [])
    return stopped


def _env(project: dict, workdir: Path) -> tuple[dict, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CICD_GIT_")}
    if project.get("python_venv", 1):
        env, prelude = python_env.step_env(workdir, env)
    else:
        env, prelude = python_env.clean_env(env), ""
    env.update(CICD_PROJECT=project["name"], CICD_DEPLOY_DIR=str(workdir))
    if config.IS_WINDOWS:
        env["CHERE_INVOKING"] = "1"
    return env, prelude


def _spawn(cmd: str, workdir: Path, env: dict, prelude: str, logfile: Path) -> subprocess.Popen:
    logfile.parent.mkdir(parents=True, exist_ok=True)
    if logfile.exists() and logfile.stat().st_size > MAX_LOG_BYTES:
        logfile.replace(logfile.with_suffix(".log.1"))
    with open(logfile, "ab") as f:
        f.write(f"\n===== {time.strftime('%d/%m/%Y %H:%M:%S')} Khởi động: {cmd} =====\n".encode("utf-8"))
        f.flush()
        kw = dict(cwd=str(workdir), env=env, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, close_fds=True)
        args = [config.SHELL, "-lc", prelude + cmd]
        if os.name == "posix":
            return subprocess.Popen(args, start_new_session=True, **kw)
        # Windows: tách khỏi cửa sổ run.bat để app vẫn chạy khi đóng CICD App
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            return subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kw)
        except OSError:
            return subprocess.Popen(args, creationflags=flags, **kw)


def crash_hints(text: str) -> list[str]:
    t = text.lower()
    hints = []
    if "already in use" in t or "address in use" in t or "10048" in t:
        hints.append("Cổng (port) đang bị chiếm: app có thể đang chạy ngoài CICD App (VS Code, terminal, 'sieucp start', "
                     "dịch vụ tự khởi động...). Hãy dừng phiên bản đó rồi bấm Khởi động lại.")
    if "modulenotfounderror" in t or "no module named" in t:
        hints.append("Thiếu thư viện Python: thêm gói còn thiếu vào requirements.txt (bước Môi trường Python sẽ tự cài).")
    if "can't open file" in t or "no such file or directory" in t:
        hints.append("Sai tên file / đường dẫn trong lệnh chạy app (lệnh chạy trong thư mục project).")
    if "command not found" in t or "is not recognized" in t:
        hints.append("Lệnh không tồn tại: kiểm tra lại lệnh chạy app, hoặc bật 'Dự án Python' để chạy trong .venv.")
    if "telegram_bot_token" in t or ("token" in t and "telegram" in t):
        hints.append("Bot Telegram thiếu token: đặt file .env chứa TELEGRAM_BOT_TOKEN vào thư mục project "
                     "(file này không nằm trong git nên phải copy tay một lần).")
    return hints


def _check_url(url: str) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:  # noqa: S310 - URL do người dùng cấu hình
            return r.status
    except urllib.error.HTTPError as e:
        return e.code if e.code < 500 else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def start(project: dict, workdir: Path, log) -> int:
    """Khởi động các tiến trình của app và kiểm tra chúng chạy ổn định. Trả về exit code."""
    cmds = commands(project)
    if not cmds:
        log.line("Chưa có lệnh chạy app.")
        return 0
    if not workdir.is_dir():
        log.line(f"!! Thư mục project {workdir} chưa tồn tại - hãy chạy pipeline một lần để tải code về.")
        return 1
    env, prelude = _env(project, workdir)
    if prelude:
        log.line(f"Chạy trong .venv: {python_env.venv_path(workdir)}")
    procs, recs, offsets = [], [], {}
    for i, cmd in enumerate(cmds):
        lp = log_path(project["id"], i)
        offsets[i] = lp.stat().st_size if lp.exists() and lp.stat().st_size <= MAX_LOG_BYTES else 0
        p = _spawn(cmd, workdir, env, prelude, lp)
        try:
            ct = psutil.Process(p.pid).create_time()
        except psutil.Error:
            ct = None
        procs.append(p)
        recs.append({"idx": i, "cmd": cmd, "pid": p.pid, "ct": ct, "started_at": time.time()})
        log.line(f"▶ [{i + 1}] {cmd}  ->  PID {p.pid}  (log: {lp})")
    _save(project["id"], recs)

    def stream():
        for i in range(len(cmds)):
            lp = log_path(project["id"], i)
            try:
                with open(lp, "rb") as f:
                    f.seek(offsets[i])
                    data = f.read()
            except OSError:
                continue
            cut = data.rfind(b"\n") + 1
            offsets[i] += cut
            for l in data[:cut].decode("utf-8", errors="replace").splitlines():
                if l.strip():
                    log.line(f"[app {i + 1}] {l}")

    wait = max(int(project.get("run_wait") or 8), 1)
    url = (project.get("run_health_url") or "").strip()
    t0 = time.time()
    url_ok = not url
    log.line(f"Theo dõi {wait}s" + (f" và chờ {url} phản hồi" if url else "") + " ...")
    while True:
        stream()
        for i, p in enumerate(procs):
            rc = p.poll()
            if rc is not None:
                time.sleep(0.5)
                stream()
                log.line(f"!! [{i + 1}] '{cmds[i]}' đã dừng ngay sau khi khởi động (exit code {rc}).")
                for h in crash_hints("\n".join(log.tail)):
                    log.line("Gợi ý: " + h)
                stop(project, log)
                return rc or 1
        if not url_ok:
            code = _check_url(url)
            if code is not None:
                url_ok = True
                log.line(f"URL kiểm tra {url} phản hồi HTTP {code}")
        elapsed = time.time() - t0
        if elapsed >= wait and url_ok:
            break
        if elapsed >= max(wait, 90):
            stream()
            log.line(f"!! {url} không phản hồi sau {int(elapsed)}s (app vẫn đang chạy - xem log của app).")
            return 1
        time.sleep(0.5)
    stream()
    log.line("✔ App đang chạy: " + ", ".join(f"[{r['idx'] + 1}] PID {r['pid']}" for r in recs))
    return 0


def deploy(project: dict, workdir: Path, log) -> int:
    n = stop(project, log)
    log.line(f"Đã dừng phiên bản cũ ({n} tiến trình)." if n else "Không có phiên bản nào đang chạy.")
    return start(project, workdir, log)


# ---------------------------------------------------------------- tự nhận diện lệnh chạy app
SERVER_RE = re.compile(r"\b(uvicorn|gunicorn|hypercorn|daphne|waitress-serve|streamlit\s+run|flask\b.*\brun|runserver|http\.server)\b",
                       re.I)


def looks_like_server(cmd: str) -> bool:
    """Lệnh khởi động server chạy liên tục (không tự kết thúc) -> phải dùng chế độ chạy nền."""
    return bool(SERVER_RE.search(cmd))


_PORT_RE = re.compile(r"(?:--port[= ]|server\.port[= ]|runserver\s+[\w.]*:|--bind[= ][\w.]*:)(\d{2,5})")


def used_ports(exclude_pid: int | None = None) -> set[int]:
    """Các port đã được project khác dùng trong lệnh chạy app."""
    ports = set()
    for p in store.query("SELECT id, run_cmd FROM projects WHERE id != ?", (exclude_pid or 0,)):
        ports.update(int(x) for x in _PORT_RE.findall(p["run_cmd"] or ""))
    return ports


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _pick_port(start: int, taken: set[int]) -> int:
    port = start
    while port in taken or not _port_free(port):
        port += 1
    return port


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:300_000] if path.is_file() else ""
    except OSError:
        return ""


def detect(workdir: Path, taken: set[int] | None = None) -> dict | None:
    """Đoán lệnh chạy app từ mã nguồn. Trả về {run_cmd, run_health_url, kind} hoặc None."""
    taken = taken or set()
    if not workdir.is_dir():
        return None
    # FastAPI -> uvicorn
    for rel, mod in (("app/main.py", "app.main"), ("main.py", "main"), ("app.py", "app"), ("api/main.py", "api.main"),
                     ("src/main.py", "src.main"), ("server.py", "server")):
        t = _read(workdir / rel)
        m = re.search(r"^(\w+)\s*(?::\s*\w+\s*)?=\s*FastAPI\(", t, re.M)
        if m:
            port = _pick_port(8000, taken)
            health = "/health" if re.search(r"""\.(get|api_route)\(\s*["']/health["']""", t) else "/docs"
            return {"kind": "FastAPI", "run_cmd": f"python -m uvicorn {mod}:{m.group(1)} --host 127.0.0.1 --port {port}",
                    "run_health_url": f"http://127.0.0.1:{port}{health}"}
    # Streamlit
    files = ["streamlit_app.py", "app.py", "main.py", "Home.py"] + sorted(p.name for p in workdir.glob("*.py"))
    for f in dict.fromkeys(files):
        t = _read(workdir / f)
        if re.search(r"^\s*(import streamlit|from streamlit\b)", t, re.M) and "st." in t:
            port = _pick_port(8501, taken)
            return {"kind": "Streamlit",
                    "run_cmd": f"python -m streamlit run {f} --server.port={port} --server.headless=true "
                               f"--browser.gatherUsageStats=false",
                    "run_health_url": f"http://127.0.0.1:{port}/_stcore/health"}
    # Django
    if (workdir / "manage.py").is_file() and "django" in _read(workdir / "manage.py").lower():
        port = _pick_port(8000, taken)
        return {"kind": "Django", "run_cmd": f"python manage.py runserver 127.0.0.1:{port} --noreload",
                "run_health_url": f"http://127.0.0.1:{port}/"}
    # Flask
    for rel, mod in (("app.py", "app"), ("main.py", "main"), ("wsgi.py", "wsgi"), ("app/__init__.py", "app")):
        m = re.search(r"^(\w+)\s*=\s*Flask\(", _read(workdir / rel), re.M)
        if m:
            port = _pick_port(5000, taken)
            return {"kind": "Flask", "run_cmd": f"python -m flask --app {mod}:{m.group(1)} run --host 127.0.0.1 --port {port}",
                    "run_health_url": f"http://127.0.0.1:{port}/"}
    # Script Python có điểm vào
    for f in ("main.py", "app.py", "run.py", "bot.py"):
        if "__main__" in _read(workdir / f):
            return {"kind": "Python script", "run_cmd": f"python {f}", "run_health_url": ""}
    return None


def apply_detection(project: dict, workdir: Path) -> dict | None:
    """Nhận diện và LƯU lệnh chạy app (chế độ chạy nền) cho project. Trả về kết quả nhận diện hoặc None."""
    det = detect(workdir, used_ports(project["id"]))
    if det:
        store.execute("UPDATE projects SET run_cmd=?, run_health_url=?, run_mode='service' WHERE id=?",
                      (det["run_cmd"], det["run_health_url"], project["id"]))
    return det


def autostart_all(lock_for) -> None:
    """Khi CICD App khởi động (vd. sau khi reboot máy): chạy lại các app đang được bật tự khởi động."""
    time.sleep(3)
    for p in store.list_projects():
        if p["run_mode"] != "service" or not p["app_autostart"] or not commands(p) or is_running(p):
            continue
        if not store.query_one("SELECT id FROM runs WHERE project_id=? AND status='success' LIMIT 1", (p["id"],)):
            continue  # chưa từng deploy thành công
        workdir = Path(os.path.expanduser(p["deploy_dir"]))
        lock = lock_for(p["id"])
        if not lock.acquire(blocking=False):
            continue
        try:
            log = TextLog()
            code = start(p, workdir, log)
            logger.info("Tự khởi động app '%s': %s", p["name"], "OK" if code == 0 else f"LỖI (exit {code})")
            for l in log.lines:
                logger.info("  %s", l)
        except Exception:  # noqa: BLE001
            logger.exception("Không tự khởi động được app '%s'", p["name"])
        finally:
            lock.release()
