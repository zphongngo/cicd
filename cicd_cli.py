#!/usr/bin/env python3
"""Quản lý CICD App chạy nền.

    cicd start [PORT]      khởi động ở cổng PORT (mặc định 8088), chạy nền
    cicd stop              dừng
    cicd restart [PORT]    dừng rồi khởi động lại; không ghi PORT = giữ cổng đang chạy
    cicd status            đang chạy hay không, PID, cổng, địa chỉ, thời gian chạy
    cicd logs [-f]         xem log của CICD App (-f: theo dõi liên tục, Ctrl+C để thoát)

PORT nhận các dạng: 8888 | --port 8888 | -port 8888 | -p 8888. Thêm --host 0.0.0.0 để máy khác trong mạng LAN truy cập.
Các app đã deploy (sieucp, accstock...) KHÔNG bị dừng khi dừng / khởi động lại CICD App.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

import psutil  # noqa: E402

from cicd import config  # noqa: E402  (DATA_DIR, cổng mặc định)
from cicd.netaddr import access_urls as urls, lan_ips  # noqa: E402,F401

DEFAULT_PORT = 8088
STATE_FILE = config.DATA_DIR / "cicd-app.json"
LOG_FILE = config.DATA_DIR / "cicd-app.log"
MAX_LOG = 10 * 1024 * 1024
IS_LINUX = sys.platform.startswith("linux")

if sys.stdout.isatty() and os.name == "posix":
    G, R, Y, B, N = "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[0m"
else:
    G = R = Y = B = N = ""


def ok(msg):
    print(f"{G}✓{N} {msg}")


def warn(msg):
    print(f"{Y}!{N} {msg}")


def err(msg):
    print(f"{R}✗{N} {msg}", flush=True)


# ---------------------------------------------------------------- trạng thái
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    config.ensure_dirs()
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def clear_state() -> None:
    STATE_FILE.unlink(missing_ok=True)


def running_proc(state: dict) -> psutil.Process | None:
    """Tiến trình CICD App do lệnh 'cicd start' tạo (kiểm tra cả thời điểm tạo để tránh nhầm PID)."""
    try:
        p = psutil.Process(state["pid"])
        if abs(p.create_time() - state.get("create_time", 0)) > 1.0 or p.status() == psutil.STATUS_ZOMBIE:
            return None
        return p
    except (psutil.Error, KeyError, TypeError):
        return None


# ---------------------------------------------------------------- dịch vụ systemd (do install.sh tạo)
def systemd_active() -> bool:
    if not IS_LINUX or not shutil.which("systemctl"):
        return False
    return subprocess.run(["systemctl", "--user", "is-active", "--quiet", "cicd-app"], check=False).returncode == 0


def systemd_port() -> str:
    r = subprocess.run(["systemctl", "--user", "show", "cicd-app", "-p", "Environment"],
                       capture_output=True, text=True, check=False)
    for part in r.stdout.replace("Environment=", "").split():
        if part.startswith("CICD_PORT="):
            return part.split("=", 1)[1]
    return str(DEFAULT_PORT)


# ---------------------------------------------------------------- cổng / địa chỉ
def port_owner(port: int, host: str = "0.0.0.0") -> str | None:
    """None nếu cổng còn trống, ngược lại mô tả chương trình đang dùng cổng."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return None
        except OSError:
            pass
    try:
        for c in psutil.net_connections(kind="tcp"):
            if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid:
                try:
                    return f"PID {c.pid}: {' '.join(psutil.Process(c.pid).cmdline())[:150]}"
                except psutil.Error:
                    return f"PID {c.pid}"
    except (psutil.Error, OSError):
        pass
    return "một chương trình khác"


def responds(port: int) -> int | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=2) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return None


def tail(path: Path, n: int = 20) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(f.tell() - 64 * 1024, 0))
            return f.read().decode("utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def uptime(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return (f"{d} ngày " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------- lệnh
def cmd_start(port: int | None, host: str | None) -> int:
    state = load_state()
    p = running_proc(state)
    port = port or DEFAULT_PORT
    if p:
        if state.get("port") == port:
            ok(f"CICD App đã đang chạy (PID {p.pid}) tại {', '.join(urls(state.get('host'), state['port']))}")
            return 0
        warn(f"CICD App đang chạy ở cổng {state.get('port')} (PID {p.pid}). Muốn chuyển sang cổng {port}: cicd restart {port}")
        return 1
    clear_state()
    if systemd_active():
        err(f"CICD App đang chạy bằng dịch vụ systemd 'cicd-app' (cổng {systemd_port()}), do install.sh tạo.")
        print("  Muốn quản lý bằng lệnh 'cicd' thì dừng dịch vụ trước:")
        print("    systemctl --user stop cicd-app")
        print("    systemctl --user disable cicd-app    # (tuỳ chọn) không tự chạy dịch vụ khi khởi động máy")
        return 1
    host = host or os.environ.get("CICD_HOST") or "0.0.0.0"
    owner = port_owner(port, host)
    if owner:
        err(f"Cổng {port} đang được dùng bởi {owner}.")
        print(f"  Chọn cổng khác, vd: cicd start {port + 1}")
        return 1

    config.ensure_dirs()
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG:
        LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
    env = os.environ.copy()
    env.update(CICD_PORT=str(port), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    env.pop("CICD_OPEN_BROWSER", None)
    env["CICD_HOST"] = host
    args = [sys.executable, str(APP_DIR / "run.py"), "--port", str(port), "--host", host]
    with open(LOG_FILE, "ab") as log:
        log.write(f"\n===== {time.strftime('%d/%m/%Y %H:%M:%S')} cicd start: cổng {port}, host {host} =====\n".encode())
        log.flush()
        kw = dict(cwd=str(APP_DIR), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True)
        if os.name == "posix":
            proc = subprocess.Popen(args, start_new_session=True, **kw)
        else:
            proc = subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW, **kw)
    try:
        ct = psutil.Process(proc.pid).create_time()
    except psutil.Error:
        ct = 0
    save_state({"pid": proc.pid, "create_time": ct, "port": port, "host": host, "started_at": time.time()})

    print(f"Đang khởi động CICD App ở cổng {port} ...", flush=True)
    deadline = time.time() + 40
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        if responds(port):
            ok(f"CICD App đang chạy (PID {proc.pid})")
            for u in urls(host, port):
                print(f"    {B}{u}{N}")
            print(f"  Log: {LOG_FILE}")
            return 0
        time.sleep(0.5)
    err("CICD App không khởi động được. Log gần nhất:")
    for line in tail(LOG_FILE, 15):
        print("    " + line)
    if proc.poll() is None:
        stop_process(psutil.Process(proc.pid))
    clear_state()
    return 1


def stop_process(p: psutil.Process) -> None:
    # Chỉ dừng tiến trình CICD App (và tiến trình 'run.py' con của nó trên Windows - launcher của .venv).
    # KHÔNG dừng các app đã deploy, dù chúng là tiến trình con.
    targets = [p]
    try:
        targets += [c for c in p.children(recursive=True) if "run.py" in " ".join(c.cmdline())]
    except psutil.Error:
        pass
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


def cmd_stop() -> int:
    state = load_state()
    p = running_proc(state)
    if not p:
        clear_state()
        print("CICD App (chạy bằng lệnh 'cicd') không chạy.")
        if systemd_active():
            warn(f"Dịch vụ systemd 'cicd-app' đang chạy (cổng {systemd_port()}). Dừng bằng: systemctl --user stop cicd-app")
        return 0
    stop_process(p)
    clear_state()
    ok(f"Đã dừng CICD App (PID {p.pid}, cổng {state.get('port')}). Các app đã deploy vẫn chạy.")
    return 0


def cmd_restart(port: int | None, host: str | None) -> int:
    state = load_state()
    port = port or state.get("port") or DEFAULT_PORT
    host = host or state.get("host")
    if running_proc(state):
        cmd_stop()
    return cmd_start(port, host)


def cmd_status() -> int:
    state = load_state()
    p = running_proc(state)
    if p:
        port = state["port"]
        code = responds(port)
        ok(f"CICD App đang chạy")
        print(f"  PID        : {p.pid}")
        print(f"  Cổng       : {port}   (lắng nghe {state.get('host', '0.0.0.0')}"f"{' = mọi địa chỉ IP' if state.get('host', '0.0.0.0') == '0.0.0.0' else ''})")
        print(f"  Thời gian  : {uptime(time.time() - p.create_time())}  (từ {time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(p.create_time()))})")
        print(f"  Phản hồi   : {'HTTP ' + str(code) if code else 'CHƯA phản hồi'}")
        try:
            mem = p.memory_info().rss + sum(c.memory_info().rss for c in p.children() if "run.py" in " ".join(c.cmdline()))
            print(f"  Bộ nhớ     : {mem / 1024 / 1024:.0f} MB")
        except psutil.Error:
            pass
        print("  Địa chỉ    :")
        for u in urls(state.get("host"), port):
            print(f"    {u}")
        print(f"  Log        : {LOG_FILE}")
        return 0
    clear_state()
    if systemd_active():
        ok(f"CICD App đang chạy bằng dịch vụ systemd 'cicd-app' (cổng {systemd_port()}).")
        print("  Xem chi tiết: systemctl --user status cicd-app")
        return 0
    print("CICD App đã dừng. Khởi động: cicd start [PORT]")
    return 3


def cmd_logs(follow: bool, lines: int) -> int:
    if not LOG_FILE.exists():
        print(f"Chưa có log ({LOG_FILE}).")
        return 0
    for line in tail(LOG_FILE, lines):
        print(line)
    if not follow:
        return 0
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            while True:
                chunk = f.read()
                if chunk:
                    sys.stdout.write(chunk.decode("utf-8", errors="replace"))
                    sys.stdout.flush()
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------- tham số
def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"cổng phải là số, nhận được '{value}'")
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"cổng phải từ 1 đến 65535, nhận được {port}")
    return port


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cicd", description="Quản lý CICD App chạy nền",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Ví dụ: cicd start | cicd start 8888 | cicd restart 9999 | cicd status | cicd stop")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="{start,stop,restart,status,logs}")
    for name, helptext in (("start", "khởi động (mặc định cổng 8088)"),
                           ("restart", "khởi động lại (không ghi cổng = giữ cổng đang chạy)")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("port_pos", nargs="?", type=_port, metavar="PORT")
        sp.add_argument("--port", "-port", "-p", type=_port, dest="port")
        sp.add_argument("--host", "-host", dest="host", help="0.0.0.0 (mặc định, mọi địa chỉ IP của máy) hoặc 127.0.0.1 (chỉ máy này)")
    sub.add_parser("stop", help="dừng")
    sub.add_parser("status", help="xem trạng thái")
    lp = sub.add_parser("logs", help="xem log")
    lp.add_argument("-f", "--follow", action="store_true", help="theo dõi liên tục")
    lp.add_argument("-n", type=int, default=50, help="số dòng cuối (mặc định 50)")
    return ap


def main(argv: list[str]) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if a.cmd in ("start", "restart"):
        if a.port and a.port_pos and a.port != a.port_pos:
            ap.error(f"hai cổng khác nhau: {a.port_pos} và --port {a.port}")
        port = a.port or a.port_pos
        return cmd_start(port, a.host) if a.cmd == "start" else cmd_restart(port, a.host)
    if a.cmd == "stop":
        return cmd_stop()
    if a.cmd == "status":
        return cmd_status()
    return cmd_logs(a.follow, a.n)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
