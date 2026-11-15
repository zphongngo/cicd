"""Engine chạy pipeline: lấy mã nguồn từ GitHub/Bitbucket, chạy các bước build/deploy, ghi log."""
import os
import signal
import subprocess
import threading
import time
import traceback
from collections import deque
from pathlib import Path

import yaml

from . import app_runner, config, python_env, secrets_box, store

# ---------------------------------------------------------------- khoá / hàng đợi
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()
_pending: set[int] = set()  # dự án cần chạy lại ngay khi run hiện tại xong


def _lock_for(pid: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(pid, threading.Lock())


lock_for = _lock_for  # dùng khi cần chặn run trong lúc di chuyển thư mục project


def is_running(pid: int) -> bool:
    return _lock_for(pid).locked()


def request_rerun(pid: int) -> None:
    _pending.add(pid)


def start_run(pid: int, trigger: str) -> int | None:
    """Bắt đầu một run trong thread nền. Trả về run_id, hoặc None nếu dự án đang chạy."""
    lock = _lock_for(pid)
    if not lock.acquire(blocking=False):
        return None
    try:
        rid = store.execute(
            "INSERT INTO runs(project_id,trigger,status,started_at) VALUES(?,?,'running',?)",
            (pid, trigger, time.time()),
        )
    except Exception:
        lock.release()
        raise
    threading.Thread(target=_thread_main, args=(rid, pid, lock), daemon=True, name=f"run-{rid}").start()
    return rid


def _thread_main(rid: int, pid: int, lock: threading.Lock) -> None:
    try:
        _execute(rid, pid)
    finally:
        lock.release()
    if pid in _pending:
        _pending.discard(pid)
        p = store.get_project(pid)
        if p and p["enabled"]:
            start_run(pid, "Chạy lại: có commit mới trong lúc đang chạy")


# ---------------------------------------------------------------- git helpers
ASKPASS_SCRIPT = """#!/bin/sh
# Bearer token / no token: do not answer, so git never sends username + wrong password
# (repeated failed logins make Bitbucket lock the account).
[ "$CICD_GIT_NOPROMPT" = "1" ] && exit 1
case "$1" in
  Username*|username*) printf '%s\\n' "$CICD_GIT_USERNAME" ;;
  *) printf '%s\\n' "$CICD_GIT_TOKEN" ;;
esac
"""

GIT = ["git", "-c", "credential.helper="]


class GitError(Exception):
    pass


class ConfigError(Exception):
    pass


def _askpass_path() -> Path:
    p = config.DATA_DIR / "git-askpass.sh"
    if not p.exists() or p.read_text(encoding="utf-8", errors="replace") != ASKPASS_SCRIPT:
        p.write_text(ASKPASS_SCRIPT, encoding="utf-8", newline="\n")
        os.chmod(p, 0o700)
    return p


def git_env(project: dict) -> dict:
    """Env cho git. Token không bao giờ nằm trong URL, dòng lệnh hay .git/config.

    - basic : username + token qua GIT_ASKPASS (GitHub, Bitbucket Cloud, token cá nhân Bitbucket Server)
    - bearer: header "Authorization: Bearer <token>" qua GIT_CONFIG_* (token repository/project của
              Bitbucket Server / Data Center). Cần git >= 2.31.
    """
    env = os.environ.copy()
    token = secrets_box.decrypt(project.get("token_enc") or "")
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_ASKPASS=str(_askpass_path()),
        CICD_GIT_USERNAME=project.get("username") or "git",
        CICD_GIT_TOKEN=token,
        LC_ALL="C",
    )
    for k in [k for k in env if k.startswith("GIT_CONFIG_")]:
        del env[k]
    bearer = project.get("auth_mode") == "bearer"
    if bearer and token:
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader",
                   GIT_CONFIG_VALUE_0=f"Authorization: Bearer {token}")
    # Bearer: nếu token bị từ chối, git sẽ hỏi username/mật khẩu -> KHÔNG trả lời (nếu không sẽ thành
    # một lần đăng nhập sai bằng username thật). Không có token: cũng không gửi gì.
    env["CICD_GIT_NOPROMPT"] = "1" if (bearer or not token) else "0"
    return env


AUTH_FAIL_PATTERNS = ("authentication failed", "captcha required", "could not read username",
                      "could not read password", "invalid username or password", "invalid credentials",
                      "account has been locked", "http basic: access denied", "returned error: 401")


def is_auth_failure(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in AUTH_FAIL_PATTERNS)


PAUSE_MARK = "[TẠM DỪNG TỰ ĐỘNG]"


def pause_for_auth(pid: int, detail: str) -> None:
    """Đăng nhập thất bại -> tắt tự động cho project để không thử lại liên tục (server sẽ khoá tài khoản)."""
    msg = (f"{PAUSE_MARK} Đăng nhập repository thất bại nên CICD App đã tắt tự động chạy cho project này, "
           f"để tránh thử lại liên tục làm khoá tài khoản.\n"
           f"Sửa username/token (hoặc mở khoá tài khoản) rồi bấm 'Kiểm tra kết nối' và 'Bật lại tự động'.\n\n{detail}")
    store.execute("UPDATE projects SET enabled=0, last_poll_error=?, last_poll_at=? WHERE id=?",
                  (msg[:3000], time.time(), pid))
    _pending.discard(pid)


def _mask(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "****")
    return text


def remote_head(project: dict, timeout: int = 30) -> str:
    """Lấy SHA mới nhất của nhánh trên remote (git ls-remote)."""
    env = git_env(project)
    branch = project["branch"]
    try:
        r = subprocess.run(
            [*GIT, "ls-remote", "--heads", project["repo_url"], branch],
            env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise GitError(f"Hết thời gian ({timeout}s) khi kết nối tới repository")
    if r.returncode != 0:
        msg = _mask((r.stderr or r.stdout).strip(), [env["CICD_GIT_TOKEN"]])
        hints = git_hints(msg)
        raise GitError(msg + ("\nGợi ý: " + " | ".join(hints) if hints else ""))
    for line in r.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.strip() == f"refs/heads/{branch}":
            return sha.strip()
    raise GitError(f"Không tìm thấy nhánh '{branch}' trên repository")


def git_hints(text: str) -> list[str]:
    t = text.lower()
    hints = []
    if "captcha required" in t or "account has been locked" in t:
        hints.append("Tài khoản Bitbucket đã bị KHOÁ do đăng nhập sai nhiều lần. Mở trình duyệt, đăng xuất rồi đăng nhập lại "
                     "Bitbucket và giải CAPTCHA để mở khoá. Trước đó hãy sửa token trong project, nếu không sẽ bị khoá lại.")
    elif is_auth_failure(t):
        hints.append("Xác thực thất bại: kiểm tra username và token (sai, hết hạn, hoặc thiếu quyền đọc repository). "
                     "Bitbucket Server: token tạo trong Repository/Project settings phải chọn kiểu 'Bearer token'; "
                     "token cá nhân (Manage account) thì chọn 'Username + token'.")
    if "repository not found" in t or ("not found" in t and "remote branch" not in t and "remote ref" not in t):
        hints.append("Không tìm thấy repository: kiểm tra lại URL, hoặc token chưa được cấp quyền truy cập repo private này.")
    if "403" in t or ("permission" in t and "denied" in t):
        hints.append("Bị từ chối quyền truy cập (403): token thiếu quyền 'Contents: Read' (GitHub) hoặc 'read:repository' (Bitbucket).")
    if "remote branch" in t or "couldn't find remote ref" in t:
        hints.append("Nhánh (branch) không tồn tại trên remote: kiểm tra lại tên nhánh.")
    if "could not resolve host" in t:
        hints.append("Không phân giải được tên miền: kiểm tra kết nối mạng/DNS của máy Linux Mint.")
    if "ssl certificate problem" in t or "certificate verify failed" in t or "self signed certificate" in t \
            or "unable to get local issuer" in t:
        hints.append("Máy không tin chứng chỉ SSL của server (thường gặp với server nội bộ công ty). "
                     "Linux: copy file CA (.crt) của công ty vào /usr/local/share/ca-certificates/ rồi chạy 'sudo update-ca-certificates'. "
                     "Windows: chạy 'git config --global http.sslBackend schannel' để git dùng chứng chỉ của Windows.")
    if "bad config" in t and "git_config" in t:
        hints.append("Phiên bản git quá cũ cho chế độ Bearer token (cần git >= 2.31): hãy cập nhật git.")
    if "would be overwritten" in t or "untracked working tree files" in t:
        hints.append("Thư mục deploy có file trùng với file trong repo: xoá hoặc di chuyển các file đó.")
    if "not a git repository" in t:
        hints.append("Thư mục deploy có thư mục .git bị hỏng: xoá thư mục .git hoặc chọn thư mục deploy khác.")
    return hints


def exit_code_hints(code: int) -> list[str]:
    return {
        124: ["Bước chạy quá thời gian cho phép (timeout). Nếu lệnh khởi động server chạy mãi (vd: npm start), hãy chạy nó qua systemd/pm2 thay vì chạy trực tiếp."],
        126: ["Không có quyền thực thi: thử 'chmod +x <file>' hoặc gọi qua 'bash <file>'."],
        127: ["Lệnh không tồn tại (command not found): chương trình chưa được cài hoặc không có trong PATH của user chạy CICD App."],
        137: ["Tiến trình bị kill (có thể do hết RAM - OOM)."],
    }.get(code, [])


# ---------------------------------------------------------------- logging
def log_path(rid: int) -> Path:
    return config.LOG_DIR / f"run_{rid}.log"


class RunLogger:
    def __init__(self, rid: int, secrets: list[str]):
        self._f = open(log_path(rid), "a", encoding="utf-8", buffering=1)
        self._secrets = [s for s in secrets if s]
        self._lock = threading.Lock()
        self.tail: deque[str] = deque(maxlen=40)

    def line(self, text: str = "") -> None:
        text = _mask(text, self._secrets)
        with self._lock:
            self._f.write(f"{time.strftime('%H:%M:%S')} | {text}\n")
            self.tail.append(text)

    def close(self) -> None:
        self._f.close()


def _run(args, cwd, env, timeout: int, log: RunLogger, show=None) -> int:
    """Chạy tiến trình, stream stdout+stderr vào log. Trả về exit code (124 nếu timeout)."""
    log.line("$ " + (show or " ".join(str(a) for a in args)))
    kw = {"start_new_session": True} if os.name == "posix" else {}
    try:
        p = subprocess.Popen(
            args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, **kw,
        )
    except FileNotFoundError as e:
        log.line(f"!! Không tìm thấy chương trình: {e.filename}")
        return 127
    timed_out = threading.Event()

    def _kill():
        timed_out.set()
        try:
            if os.name == "posix":
                os.killpg(p.pid, signal.SIGKILL)
            else:  # Windows: dừng cả cây tiến trình (bash -> npm -> node ...)
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    timer = threading.Timer(timeout, _kill)
    timer.daemon = True
    timer.start()
    try:
        for out in p.stdout:
            log.line(out.rstrip("\r\n"))
        code = p.wait()
    finally:
        timer.cancel()
    if timed_out.is_set():
        log.line(f"!! Quá thời gian cho phép ({timeout}s) - đã dừng tiến trình")
        return 124
    return code


# ---------------------------------------------------------------- cấu hình pipeline
def load_steps(project: dict, workdir: Path) -> tuple[list[dict], dict, str]:
    """Đọc các bước từ .cicd.yml trong repo; nếu không có thì dùng lệnh nhập trên web."""
    for fname in (".cicd.yml", ".cicd.yaml"):
        f = workdir / fname
        if not f.is_file():
            continue
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"File {fname} sai cú pháp YAML: {e}")
        if not isinstance(data, dict):
            raise ConfigError(f"File {fname} phải là một object có khoá 'steps'")
        env = data.get("env") or {}
        if not isinstance(env, dict):
            raise ConfigError(f"'env' trong {fname} phải là dạng KEY: value")
        raw = data.get("steps") or []
        if not isinstance(raw, list):
            raise ConfigError(f"'steps' trong {fname} phải là một danh sách")
        steps = []
        for i, s in enumerate(raw, 1):
            if isinstance(s, str):
                s = {"run": s}
            if not isinstance(s, dict) or not isinstance(s.get("run"), str) or not s["run"].strip():
                raise ConfigError(f"Bước thứ {i} trong {fname} thiếu khoá 'run' (lệnh cần chạy)")
            timeout = s.get("timeout")
            if timeout is not None and (not isinstance(timeout, int) or timeout <= 0):
                raise ConfigError(f"Bước thứ {i}: 'timeout' phải là số giây > 0")
            steps.append({
                "name": str(s.get("name") or s["run"].strip().splitlines()[0])[:120],
                "run": s["run"],
                "timeout": timeout,
            })
        return steps, {str(k): str(v) for k, v in env.items()}, fname
    lines = [l.strip() for l in (project.get("commands") or "").splitlines()]
    steps = [{"name": l[:120], "run": l, "timeout": None} for l in lines if l and not l.startswith("#")]
    return steps, {}, "cấu hình trên web"


# ---------------------------------------------------------------- các bước
def _checkout(project: dict, workdir: Path, log: RunLogger) -> int:
    env = git_env(project)
    url, br, to = project["repo_url"], project["branch"], project["step_timeout"]
    w = str(workdir)
    if (workdir / ".git").is_dir():
        log.line(f"Thư mục {w} đã có repository -> fetch & cập nhật lên commit mới nhất")
        cmds = [
            [*GIT, "-C", w, "remote", "set-url", "origin", url],
            [*GIT, "-C", w, "fetch", "--prune", "origin", f"+refs/heads/{br}:refs/remotes/origin/{br}"],
            [*GIT, "-C", w, "checkout", "-f", "-B", br, f"origin/{br}"],
            [*GIT, "-C", w, "reset", "--hard", f"origin/{br}"],
        ]
    else:
        if workdir.exists() and any(workdir.iterdir()):
            log.line(f"!! Thư mục {w} đã tồn tại, KHÔNG trống và không phải git repository.")
            log.line("Gợi ý: chọn thư mục deploy khác, hoặc di chuyển/xoá nội dung hiện có rồi chạy lại.")
            return 1
        workdir.parent.mkdir(parents=True, exist_ok=True)
        log.line(f"Clone repository vào {w}")
        cmds = [[*GIT, "clone", "--branch", br, "--single-branch", url, w]]
    for args in cmds:
        code = _run(args, None, env, to, log)
        if code != 0:
            out = "\n".join(log.tail)
            for h in git_hints(out):
                log.line("Gợi ý: " + h)
            if is_auth_failure(out):
                pause_for_auth(project["id"], out[-1500:])
                log.line(f"!! {PAUSE_MARK} Đã tắt tự động chạy cho project này để tránh khoá tài khoản. "
                         "Sửa token rồi bấm 'Bật lại tự động' trên trang project.")
            return code
    if (workdir / ".gitmodules").is_file():
        code = _run([*GIT, "-C", w, "submodule", "update", "--init", "--recursive"], None, env, to, log)
        if code != 0:
            return code
    return 0


def _run_step(step_id: int, label: str, fn, log: RunLogger) -> tuple[bool, str]:
    store.execute("UPDATE steps SET status='running', started_at=? WHERE id=?", (time.time(), step_id))
    log.line()
    log.line(f"==> {label}")
    log.tail.clear()
    t0 = time.time()
    code = fn()
    ok = code == 0
    if not ok:
        for h in exit_code_hints(code):
            log.line("Gợi ý: " + h)
    summary = "\n".join(log.tail)
    store.execute(
        "UPDATE steps SET status=?, exit_code=?, finished_at=? WHERE id=?",
        ("success" if ok else "failed", code, time.time(), step_id),
    )
    log.line(f"<== {label}: {'THÀNH CÔNG' if ok else f'THẤT BẠI (exit code {code})'} - {time.time() - t0:.1f}s")
    return ok, summary


def _execute(rid: int, pid: int) -> None:
    project = store.get_project(pid)
    try:
        token = secrets_box.decrypt(project["token_enc"])
    except Exception:
        token = ""
    log = RunLogger(rid, [token])
    status, failed_step, summary, sha = "failed", None, None, None
    app_stopped = False
    t0 = time.time()
    try:
        run = store.get_run(rid)
        log.line(f"Run #{rid} | Dự án: {project['name']} | Repo: {project['repo_url']} | Nhánh: {project['branch']}")
        log.line(f"Kích hoạt bởi: {run['trigger']}")
        workdir = Path(project["deploy_dir"]).expanduser()
        cfg: dict = {}

        def checkout() -> int:
            nonlocal sha
            code = _checkout(project, workdir, log)
            if code != 0:
                return code
            r = subprocess.run(["git", "-C", str(workdir), "log", "-1", "--format=%H%x1f%s"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            sha, _, msg = r.stdout.strip().partition("\x1f")
            store.execute("UPDATE runs SET commit_sha=?, commit_msg=? WHERE id=?", (sha, msg[:300], rid))
            store.execute("UPDATE projects SET last_sha=? WHERE id=?", (sha, pid))
            log.line(f"Commit hiện tại: {sha[:10]} - {msg}")
            try:
                steps, env, source = load_steps(project, workdir)
            except ConfigError as e:
                log.line(f"!! {e}")
                return 1
            cfg.update(steps=steps, env=env)
            log.line(f"Pipeline lấy từ: {source} ({len(steps)} bước)")
            if not app_runner.commands(project) and project.get("run_detect", 1):
                det = app_runner.apply_detection(project, workdir)
                if det:
                    project.update(store.get_project(pid))
                    log.line(f"Tự nhận diện app {det['kind']}: {det['run_cmd']}"
                             + (f"  (URL kiểm tra: {det['run_health_url']})" if det["run_health_url"] else "")
                             + " - sửa được trong ✎ Sửa -> mục 5")
                else:
                    log.line("Không tự nhận diện được lệnh chạy app - hãy nhập ở ✎ Sửa -> mục 5. Deploy.")
            return 0

        s1 = store.add_step(rid, 1, "Lấy mã nguồn (git)", f"{project['repo_url']} @ {project['branch']}")
        ok, summary = _run_step(s1, "[1] Lấy mã nguồn (git)", checkout, log)
        if not ok:
            failed_step = "Lấy mã nguồn (git)"
            return

        steps = cfg["steps"]
        use_python = bool(project.get("python_venv", 1))
        first = 3 if use_python else 2          # số thứ tự của bước người dùng đầu tiên
        run_cmds = app_runner.commands(project)
        total = len(steps) + first - 1 + (1 if run_cmds else 0)
        deploy_name = ("Deploy: chạy app" if project.get("run_mode") != "once" else "Deploy: chạy app (1 lần)")

        step_env = {k: v for k, v in os.environ.items() if not k.startswith("CICD_GIT_")}
        step_env.update(cfg["env"])
        step_env.update(CICD_RUN_ID=str(rid), CICD_PROJECT=project["name"], CICD_BRANCH=project["branch"],
                        CICD_COMMIT=sha or "", CICD_DEPLOY_DIR=str(workdir))
        if config.IS_WINDOWS:
            step_env["CHERE_INVOKING"] = "1"  # Git Bash: giữ nguyên thư mục làm việc khi chạy login shell

        prelude = ""
        if use_python:
            name = "Môi trường Python (.venv + thư viện)"
            sp = store.add_step(rid, 2, name, f"{python_env.VENV_DIR} ← {' '.join(python_env.base_python_cmd(project.get('python_cmd') or ''))}")
            ids = [store.add_step(rid, i + first, s["name"], s["run"]) for i, s in enumerate(steps)]
            deploy_id = store.add_step(rid, total, deploy_name, " | ".join(run_cmds)) if run_cmds else None

            def stop_app_before_change():
                # App đang chạy giữ file trong .venv (Windows khoá file) -> dừng ngay trước khi sửa .venv
                nonlocal app_stopped
                if not app_stopped and app_runner.is_running(store.get_project(pid)):
                    log.line("Dừng app đang chạy trước khi cập nhật .venv ...")
                    app_runner.stop(store.get_project(pid), log)
                    app_stopped = True

            ok, summary = _run_step(sp, f"[2/{total}] {name}",
                                    lambda: python_env.setup(project, workdir, step_env, project["step_timeout"], log, _run,
                                                             before_change=stop_app_before_change),
                                    log)
            if not ok:
                failed_step = f"[2/{total}] {name}"
                return
            step_env, prelude = python_env.step_env(workdir, step_env)
            if prelude:
                log.line(f"Các bước tiếp theo chạy trong .venv: {python_env.venv_path(workdir)}")
        else:
            ids = [store.add_step(rid, i + first, s["name"], s["run"]) for i, s in enumerate(steps)]
            deploy_id = store.add_step(rid, total, deploy_name, " | ".join(run_cmds)) if run_cmds else None
        if not run_cmds:
            log.line("Chưa cấu hình 'Lệnh chạy app' (Sửa project -> mục 5. Deploy - chạy app) -> KHÔNG chạy app, "
                     "chỉ cập nhật mã nguồn" + (" và môi trường Python." if use_python else "."))

        for i, (sid, s) in enumerate(zip(ids, steps)):
            label = f"[{i + first}/{total}] {s['name']}"
            timeout = s["timeout"] or project["step_timeout"]
            ok, summary = _run_step(
                sid, label,
                lambda: _run([config.SHELL, "-lc", prelude + "set -eo pipefail\n" + s["run"]], str(workdir), step_env,
                             timeout, log, show=s["run"]),
                log,
            )
            if not ok:
                failed_step = f"{label}"
                return

        if run_cmds:
            label = f"[{total}/{total}] {deploy_name}"
            if project.get("run_mode") == "once":
                script = prelude + "set -eo pipefail\n" + "\n".join(run_cmds)
                fn = lambda: _run([config.SHELL, "-lc", script], str(workdir), python_env.clean_env(step_env),  # noqa: E731
                                  project["step_timeout"], log, show=" && ".join(run_cmds))
            else:
                fn = lambda: app_runner.deploy(store.get_project(pid), workdir, log)  # noqa: E731
            ok, summary = _run_step(deploy_id, label, fn, log)
            if not ok:
                failed_step = label
                return
        status, summary = "success", None
    except Exception:
        log.line("!! Lỗi nội bộ của CICD App:")
        for l in traceback.format_exc().splitlines():
            log.line(l)
        failed_step = failed_step or "Lỗi nội bộ CICD App"
        summary = "\n".join(log.tail)
    finally:
        now = time.time()
        if status != "success" and app_stopped:
            log.line("!! App đã được dừng để cập nhật .venv nhưng pipeline lỗi nên CHƯA chạy lại. "
                     "Sửa lỗi rồi chạy lại pipeline, hoặc bấm 'Khởi động app' trên trang project.")
        log.line()
        if status == "success":
            log.line(f"===== KẾT QUẢ: THÀNH CÔNG ({now - t0:.1f}s) =====")
        else:
            log.line(f"===== KẾT QUẢ: THẤT BẠI tại bước: {failed_step} ({now - t0:.1f}s) =====")
        log.close()  # ghi xong log trước khi đánh dấu run kết thúc
        store.execute("UPDATE steps SET status='failed', finished_at=? WHERE run_id=? AND status='running'", (now, rid))
        store.execute("UPDATE steps SET status='skipped' WHERE run_id=? AND status='pending'", (rid,))
        store.execute(
            "UPDATE runs SET status=?, failed_step=?, error_summary=?, finished_at=? WHERE id=?",
            (status, failed_step, summary, now, rid),
        )
