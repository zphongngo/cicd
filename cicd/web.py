"""Giao diện web của CICD App."""
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import app_runner, config, netaddr, pipeline, python_env, scheduler, secrets_box, store

app = Flask(__name__)
PUBLIC_ENDPOINTS = {"static", "webhook", "login", "setup"}


# ---------------------------------------------------------------- bảo mật
@app.before_request
def guard():
    has_admin = bool(store.get_setting("admin_hash"))
    if request.endpoint == "webhook":
        return None
    if not has_admin and request.endpoint != "static" and not netaddr.is_loopback(request.remote_addr):
        # Chưa có mật khẩu quản trị: chỉ cho tạo từ chính máy chạy CICD App (tránh người khác trong mạng LAN tạo trước)
        return render_template("setup_local_only.html", local_urls=netaddr.access_urls("127.0.0.1", config.PORT)), 403
    if not has_admin and request.endpoint != "setup":
        return redirect(url_for("setup"))
    if has_admin and request.endpoint not in PUBLIC_ENDPOINTS and not session.get("auth"):
        return redirect(url_for("login", next=request.path))
    if request.method == "POST":
        token = session.get("csrf")
        if not token or not secrets.compare_digest(request.form.get("csrf", ""), token):
            abort(400, "CSRF token không hợp lệ - hãy tải lại trang.")
    return None


@app.context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return {"csrf": session["csrf"], "is_running": pipeline.is_running, "base_dir": base_dir(), "sep": os.sep,
            "bbk_server": store.get_setting("bbk_server") or "", "bbk_username": store.get_setting("bbk_username") or "",
            "default_python": python_env.default_python(), "venv_dir": python_env.VENV_DIR,
            "pause_mark": pipeline.PAUSE_MARK,
            "access_urls": netaddr.access_urls(config.HOST, config.PORT), "listen_host": config.HOST}


# ---------------------------------------------------------------- thư mục
NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9_-])?$")
WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def base_dir() -> str:
    """Thư mục gốc chứa các project (thiết lập trong trang Cài đặt)."""
    return store.get_setting("base_dir") or str(Path.home() / "githubapps")


def norm_path(path: str) -> str:
    """Chuẩn hoá đường dẫn để so sánh (Windows: không phân biệt hoa/thường và dấu / với \\)."""
    n = os.path.normcase(os.path.abspath(os.path.expanduser(path)))
    return n if Path(n).parent == Path(n) else n.rstrip("\\/")


def _is_inside(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


def bbk_repo_url(server: str, username: str, repo: str) -> str:
    """URL repo cá nhân trên Bitbucket Server: https://<server>/scm/~<username>/<repo>.git"""
    return f"{server.rstrip('/')}/scm/~{username.lower()}/{repo.lower()}.git"


def _move_project_dir(old: str, new: str) -> str | None:
    """Di chuyển/đổi tên thư mục project. Trả về thông báo lỗi, hoặc None nếu thành công."""
    old_p, new_p = Path(os.path.expanduser(old)), Path(new)
    if not old_p.exists():
        return None  # chưa clone lần nào -> chỉ cần đổi đường dẫn
    if new_p.exists() and any(new_p.iterdir()):
        return f"Không thể chuyển thư mục project: {new_p} đã tồn tại và không trống."
    try:
        new_p.parent.mkdir(parents=True, exist_ok=True)
        if new_p.exists():
            new_p.rmdir()
        shutil.move(str(old_p), str(new_p))
    except OSError as e:
        return (f"Không di chuyển được thư mục {old_p} → {new_p}: {e.strerror or e}. "
                "Hãy đóng các chương trình đang mở thư mục này (Explorer, VS Code, terminal, app đang chạy...) rồi thử lại.")
    return None


def _valid_folder_name(name: str) -> bool:
    return bool(NAME_RE.match(name)) and name.split(".")[0].lower() not in WIN_RESERVED


@app.template_filter("dt")
def fmt_dt(ts):
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M:%S") if ts else "-"


@app.template_filter("dur")
def fmt_dur(row):
    start, end = row.get("started_at"), row.get("finished_at")
    if not start:
        return "-"
    s = int((end or time.time()) - start)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if store.get_setting("admin_hash"):
        return redirect(url_for("login"))
    if request.method == "POST":
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        if len(pw) < 8:
            flash("Mật khẩu tối thiểu 8 ký tự.", "error")
        elif pw != pw2:
            flash("Hai mật khẩu không khớp.", "error")
        else:
            store.set_setting("admin_hash", generate_password_hash(pw))
            session.clear()
            session["auth"] = True
            flash("Đã tạo mật khẩu quản trị.", "ok")
            return redirect(url_for("index"))
    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        time.sleep(0.5)  # làm chậm brute-force
        if check_password_hash(store.get_setting("admin_hash") or "", request.form.get("password", "")):
            session.clear()
            session["auth"] = True
            session.permanent = True
            nxt = request.args.get("next", "/")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
        flash("Sai mật khẩu.", "error")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- dự án
@app.get("/")
def index():
    projects = store.list_projects()
    # Trạng thái app của từng project: None = chưa cấu hình chạy nền, True/False = đang chạy/đã dừng
    app_state = {p["id"]: app_runner.is_running(p) for p in projects}
    return render_template("index.html", projects=projects, app_state=app_state)


def _form_to_project(form, existing: dict | None) -> tuple[dict, list[str]]:
    errors = []
    data = {
        "name": form.get("name", "").strip(),
        "provider": form.get("provider", "github"),
        "auth_mode": form.get("auth_mode", "basic"),
        "repo_url": form.get("repo_url", "").strip(),
        "branch": form.get("branch", "").strip() or "main",
        "username": form.get("username", "").strip(),
        "deploy_dir": "",
        "commands": form.get("commands", "").replace("\r\n", "\n").strip(),
        "enabled": 1 if form.get("enabled") else 0,
        "python_venv": 1 if form.get("python_venv") else 0,
        "python_cmd": form.get("python_cmd", "").strip(),
        "run_cmd": form.get("run_cmd", "").replace("\r\n", "\n").strip(),
        "run_mode": form.get("run_mode", "service"),
        "run_health_url": form.get("run_health_url", "").strip(),
        "app_autostart": 1 if form.get("app_autostart") else 0,
        "run_detect": 1 if form.get("run_detect") else 0,
    }
    if data["run_mode"] not in ("service", "once"):
        errors.append("Kiểu chạy app không hợp lệ.")
    elif data["run_mode"] == "once":
        servers = [c for c in data["run_cmd"].splitlines() if app_runner.looks_like_server(c)]
        if servers:
            errors.append(f"Lệnh '{servers[0].strip()}' khởi động server chạy liên tục (không tự kết thúc) - hãy chọn "
                          "Kiểu chạy 'Chạy nền liên tục'. Chế độ 'Chạy 1 lần' sẽ treo pipeline tới hết timeout.")
    if len(data["run_cmd"]) > 2000:
        errors.append("Lệnh chạy app quá dài.")
    if data["run_health_url"] and not re.match(r"^https?://\S+$", data["run_health_url"]):
        errors.append("URL kiểm tra phải dạng http://127.0.0.1:8899/...")
    try:
        data["run_wait"] = int(form.get("run_wait", "8"))
        if not 1 <= data["run_wait"] <= 600:
            errors.append("Thời gian theo dõi sau khi khởi động phải từ 1 đến 600 giây.")
    except ValueError:
        errors.append("Thời gian theo dõi phải là số.")
    if len(data["python_cmd"]) > 300 or "\n" in data["python_cmd"]:
        errors.append("Ô 'Python dùng để tạo .venv' không hợp lệ.")
    if not data["name"]:
        errors.append("Tên project không được để trống.")
    elif not _valid_folder_name(data["name"]):
        errors.append("Tên project chỉ gồm chữ cái không dấu, số, dấu '-', '_' hoặc '.', bắt đầu bằng chữ/số, tối đa 64 ký tự, không dùng tên dành riêng của Windows như CON, NUL "
                      "(tên này cũng được dùng làm tên thư mục).")
    elif store.find_project_by_name(data["name"], existing and existing["id"]):
        errors.append(f"Tên project '{data['name']}' đã tồn tại. Hãy chọn tên khác.")
    if data["provider"] not in ("github", "bitbucket", "bitbucket-server"):
        errors.append("Nhà cung cấp không hợp lệ.")
    if data["auth_mode"] not in ("basic", "bearer"):
        errors.append("Kiểu xác thực không hợp lệ.")
    elif data["auth_mode"] == "basic" and not data["username"] and form.get("token", "").strip():
        errors.append("Kiểu 'Username + token' cần nhập Username.")
    bbk_server = store.get_setting("bbk_server") or ""
    if not data["repo_url"] and data["provider"] == "bitbucket-server" and bbk_server and data["username"] \
            and _valid_folder_name(data["name"]):
        data["repo_url"] = bbk_repo_url(bbk_server, data["username"], data["name"])
    if not re.match(r"^https://[^\s@]+/[^\s]+$", data["repo_url"]):
        errors.append("URL repository không hợp lệ. Ví dụ: https://bbk-rndsoft.ingenico.com/scm/~username/worklog.git, "
                      "https://github.com/<user>/<repo>.git (không chứa user@).")
    if not re.match(r"^[A-Za-z0-9._/\-]+$", data["branch"]) or ".." in data["branch"]:
        errors.append("Tên nhánh không hợp lệ.")
    parent = form.get("parent_dir", "").strip()
    if not parent or not os.path.isabs(os.path.expanduser(parent)):
        errors.append(f"Thư mục chứa project phải là đường dẫn tuyệt đối, vd: {base_dir()}")
    elif data["name"] and _valid_folder_name(data["name"]):
        # Thư mục project = <thư mục chứa project>/<tên project>
        dd = os.path.join(os.path.abspath(os.path.expanduser(parent)), data["name"])
        data["deploy_dir"] = dd
        nd = norm_path(dd)
        if nd == norm_path(str(Path.home())):
            errors.append("Thư mục project không được trùng với thư mục home - hãy chọn thư mục chứa khác.")
        else:
            others = store.query("SELECT name, deploy_dir FROM projects WHERE id != ?", ((existing or {}).get("id", 0),))
            for other in others:
                od = norm_path(other["deploy_dir"])
                if _is_inside(nd, od) or _is_inside(od, nd):
                    errors.append(f"Thư mục {dd} trùng hoặc lồng nhau với thư mục của project '{other['name']}' "
                                  f"({other['deploy_dir']}). Hãy chọn thư mục chứa khác.")
                    break
    try:
        data["poll_interval"] = int(form.get("poll_interval", "60"))
        if data["poll_interval"] != 0 and data["poll_interval"] < 15:
            errors.append("Chu kỳ kiểm tra tối thiểu 15 giây (hoặc 0 để tắt).")
    except ValueError:
        errors.append("Chu kỳ kiểm tra phải là số.")
    try:
        data["step_timeout"] = int(form.get("step_timeout", "1800"))
        if data["step_timeout"] < 10:
            errors.append("Timeout mỗi bước tối thiểu 10 giây.")
    except ValueError:
        errors.append("Timeout phải là số.")
    token = form.get("token", "").strip()
    if token:
        data["token_enc"] = secrets_box.encrypt(token)
    elif form.get("clear_token"):
        data["token_enc"] = ""
    elif existing is None:
        data["token_enc"] = ""
    return data, errors

def _detect_on_save(data: dict, project_id: int | None) -> None:
    """Lệnh chạy app để trống + bật tự nhận diện + thư mục project đã có code -> nhận diện ngay khi lưu."""
    if data["run_cmd"] or not data.get("run_detect") or not os.path.isdir(data["deploy_dir"]):
        return
    det = app_runner.detect(Path(data["deploy_dir"]), app_runner.used_ports(project_id))
    if det:
        data.update(run_cmd=det["run_cmd"], run_health_url=det["run_health_url"], run_mode="service")
        flash(f"Tự nhận diện app {det['kind']}: {det['run_cmd']} (sửa được ở mục 5).", "ok")


@app.route("/projects/new", methods=["GET", "POST"])
def project_new():
    if request.method == "POST":
        data, errors = _form_to_project(request.form, None)
        if not errors:
            _detect_on_save(data, None)
            data.update(webhook_secret=secrets.token_urlsafe(24), created_at=time.time())
            cols = ",".join(data)
            try:
                pid = store.execute(f"INSERT INTO projects({cols}) VALUES({','.join('?' * len(data))})", tuple(data.values()))
            except sqlite3.IntegrityError:
                errors.append(f"Tên project '{data['name']}' đã tồn tại. Hãy chọn tên khác.")
            else:
                flash("Đã tạo project. Bấm 'Kiểm tra kết nối' để chắc chắn token và URL đúng.", "ok")
                return redirect(url_for("project_view", pid=pid))
        for e in errors:
            flash(e, "error")
        return render_template("project_form.html", p=request.form, is_new=True)
    has_bbk = bool(store.get_setting("bbk_server"))
    defaults = {"provider": "bitbucket-server" if has_bbk else "github", "auth_mode": "basic", "branch": "main",
                "username": (store.get_setting("bbk_username") or "") if has_bbk else "",
                "poll_interval": 60, "step_timeout": 1800, "enabled": 1, "parent_dir": base_dir(), "python_venv": 1,
                "run_mode": "service", "run_wait": 8, "app_autostart": 1, "run_detect": 1}
    return render_template("project_form.html", p=defaults, is_new=True)


def _project_or_404(pid: int) -> dict:
    p = store.get_project(pid)
    if not p:
        abort(404)
    return p


@app.route("/projects/<int:pid>/edit", methods=["GET", "POST"])
def project_edit(pid):
    p = _project_or_404(pid)
    if request.method == "POST":
        data, errors = _form_to_project(request.form, p)
        lock = pipeline.lock_for(pid)
        if not errors and not lock.acquire(blocking=False):
            errors.append("Project đang chạy - hãy đợi run kết thúc rồi lưu lại.")
        elif not errors:
            try:
                moved = norm_path(data["deploy_dir"]) != norm_path(p["deploy_dir"])
                err = None
                if moved and app_runner.is_running(p):
                    err = "App của project đang chạy - hãy bấm 'Dừng app' trước khi đổi tên / di chuyển thư mục project."
                elif moved:
                    err = _move_project_dir(p["deploy_dir"], data["deploy_dir"])
                if err:
                    errors.append(err)
                else:
                    _detect_on_save(data, pid)
                    sets = ",".join(f"{k}=?" for k in data)
                    try:
                        store.execute(f"UPDATE projects SET {sets}, last_poll_at=NULL, last_poll_error=NULL WHERE id=?",
                                      (*data.values(), pid))
                    except sqlite3.IntegrityError:
                        if moved:
                            _move_project_dir(data["deploy_dir"], p["deploy_dir"])
                        errors.append(f"Tên project '{data['name']}' đã tồn tại. Hãy chọn tên khác.")
                    else:
                        if moved and os.path.isdir(data["deploy_dir"]):
                            flash(f"Đã chuyển thư mục project: {p['deploy_dir']} → {data['deploy_dir']}", "ok")
                        flash("Đã lưu thay đổi.", "ok")
                        return redirect(url_for("project_view", pid=pid))
            finally:
                lock.release()
        for e in errors:
            flash(e, "error")
        return render_template("project_form.html", p={**p, **request.form, "id": pid, "enabled": request.form.get("enabled"),
                                                         "python_venv": request.form.get("python_venv"),
                                                         "app_autostart": request.form.get("app_autostart"),
                                                         "run_detect": request.form.get("run_detect")}, is_new=False)
    return render_template("project_form.html", p={**p, "parent_dir": os.path.dirname(p["deploy_dir"])}, is_new=False)


@app.post("/projects/<int:pid>/delete")
def project_delete(pid):
    _project_or_404(pid)
    if pipeline.is_running(pid):
        flash("Dự án đang chạy, hãy đợi run kết thúc rồi xoá.", "error")
        return redirect(url_for("project_view", pid=pid))
    p = store.get_project(pid)
    if app_runner.stop(p):
        flash("Đã dừng app của project.", "ok")
    for r in store.query("SELECT id FROM runs WHERE project_id=?", (pid,)):
        pipeline.log_path(r["id"]).unlink(missing_ok=True)
    for i in range(20):
        app_runner.log_path(pid, i).unlink(missing_ok=True)
    store.execute("DELETE FROM projects WHERE id=?", (pid,))
    flash("Đã xoá dự án (thư mục deploy trên máy KHÔNG bị xoá).", "ok")
    return redirect(url_for("index"))


@app.get("/projects/<int:pid>")
def project_view(pid):
    p = _project_or_404(pid)
    hook = url_for("webhook", pid=pid, secret=p["webhook_secret"], _external=True)
    return render_template("project.html", p=p, runs=store.list_runs(pid), webhook_url=hook,
                           running=pipeline.is_running(pid), app=app_runner.status(p), current_run=_current_run(pid))


@app.post("/projects/<int:pid>/test")
def project_test(pid):
    p = _project_or_404(pid)
    try:
        sha = pipeline.remote_head(p)
        flash(f"Kết nối thành công! Nhánh '{p['branch']}' đang ở commit {sha[:10]}.", "ok")
    except Exception as e:  # noqa: BLE001
        flash(f"Kết nối thất bại: {e}", "error")
    return redirect(url_for("project_view", pid=pid))


@app.post("/projects/<int:pid>/app/<action>")
def project_app(pid, action):
    p = _project_or_404(pid)
    if action not in ("start", "restart", "stop"):
        abort(404)
    lock = pipeline.lock_for(pid)
    if not lock.acquire(blocking=False):
        flash("Pipeline của project đang chạy - hãy đợi nó kết thúc.", "error")
        return redirect(url_for("project_view", pid=pid))
    try:
        log = app_runner.TextLog()
        workdir = Path(os.path.expanduser(p["deploy_dir"]))
        if action in ("start", "restart"):
            cmds = app_runner.commands(p)
            if not cmds:
                det = app_runner.apply_detection(p, workdir)
                if not det:
                    flash(f"[{p['name']}] Chưa có lệnh chạy app và không tự nhận diện được"
                          + ("" if workdir.is_dir() else " (chưa có code - hãy bấm 'Chạy pipeline' trước)")
                          + ". Hãy nhập lệnh ở mục 5. Deploy.", "error")
                    return redirect(url_for("project_edit", pid=pid) + "#deploy")
                log.line(f"Tự nhận diện app {det['kind']}: {det['run_cmd']}")
            elif p["run_mode"] == "once":
                if not any(app_runner.looks_like_server(c) for c in cmds):
                    flash(f"[{p['name']}] App đang ở chế độ 'Chạy 1 lần' (chạy trong pipeline). Muốn bật/tắt app chạy nền, "
                          "hãy chọn 'Chạy nền liên tục' ở mục 5. Deploy.", "error")
                    return redirect(url_for("project_edit", pid=pid) + "#deploy")
                store.execute("UPDATE projects SET run_mode='service' WHERE id=?", (pid,))
                log.line("Lệnh chạy app là server chạy liên tục -> đã chuyển sang chế độ 'Chạy nền liên tục'.")
            p = store.get_project(pid)
        if action == "stop":
            n = app_runner.stop(p, log)
            code = 0
            log.line(f"Đã dừng {n} tiến trình." if n else "App không chạy.")
        elif action == "restart":
            code = app_runner.deploy(p, workdir, log)
        else:
            code = 0 if app_runner.is_running(p) else app_runner.start(p, workdir, log)
            if app_runner.is_running(p) and not log.lines:
                log.line("App đang chạy rồi.")
    finally:
        lock.release()
    flash(f"[{p['name']}]\n" + "\n".join(log.lines[-25:]), "ok" if code == 0 else "error")
    nxt = request.form.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//"):   # quay lại trang đã bấm nút (vd. trang chủ)
        return redirect(nxt)
    return redirect(url_for("project_view", pid=pid))


@app.get("/projects/<int:pid>/applog/<int:n>")
def project_applog(pid, n):
    p = _project_or_404(pid)
    return render_template("applog.html", p=p, n=n, app=app_runner.status(p))


@app.get("/api/projects/<int:pid>/applog/<int:n>")
def project_applog_api(pid, n):
    _project_or_404(pid)
    path = app_runner.log_path(pid, n - 1)
    offset = request.args.get("offset", type=int)
    chunk, size = "", 0
    if path.exists():
        size = path.stat().st_size
        if offset is None or offset > size:      # lần đầu (hoặc log đã xoay vòng): lấy ~200KB cuối
            offset = max(size - 200 * 1024, 0)
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(512 * 1024)
        cut = data.rfind(b"\n") + 1
        chunk = data[:cut].decode("utf-8", errors="replace")
        offset += cut
    p = store.get_project(pid)
    st = next((a for a in app_runner.status(p) if a["idx"] == n - 1), None)
    return jsonify(log=chunk, offset=offset or 0, running=bool(st and st["running"]), pid=st and st["pid"])


@app.post("/projects/<int:pid>/enable")
def project_enable(pid):
    _project_or_404(pid)
    # last_poll_at = bây giờ -> lần kiểm tra đầu tiên diễn ra sau 1 chu kỳ, không ngay lập tức
    store.execute("UPDATE projects SET enabled=1, last_poll_error=NULL, last_poll_at=? WHERE id=?", (time.time(), pid))
    flash("Đã bật lại tự động chạy.", "ok")
    return redirect(url_for("project_view", pid=pid))


@app.post("/projects/<int:pid>/run")
def project_run(pid):
    _project_or_404(pid)
    rid = pipeline.start_run(pid, "Chạy thủ công từ web")
    if rid is None:
        # Đang có run (vd. lần chạy đầu do bộ lập lịch tự khởi động) -> mở trang theo dõi của run đó
        cur = _current_run(pid)
        if cur:
            flash(f"Project đang chạy (run #{cur['id']}) - đang hiển thị tiến trình của run này.", "ok")
            return redirect(url_for("run_view", rid=cur["id"]))
        flash("Project đang bận - hãy thử lại sau vài giây.", "error")
        return redirect(url_for("project_view", pid=pid))
    return redirect(url_for("run_view", rid=rid))


def _current_run(pid: int) -> dict | None:
    return store.query_one("SELECT id, trigger, started_at FROM runs WHERE project_id=? AND status='running' "
                           "ORDER BY id DESC LIMIT 1", (pid,))


@app.get("/api/projects/<int:pid>/current-run")
def project_current_run(pid):
    _project_or_404(pid)
    cur = _current_run(pid)
    return jsonify(run_id=cur["id"] if cur else None)


@app.post("/projects/<int:pid>/open-folder")
def project_open_folder(pid):
    p = _project_or_404(pid)
    d = Path(os.path.expanduser(p["deploy_dir"]))
    if not d.is_dir():
        flash(f"Thư mục {d} chưa tồn tại - hãy bấm 'Chạy ngay' một lần để tải code về.", "error")
    else:
        try:
            if config.IS_WINDOWS:
                os.startfile(str(d))  # noqa: S606
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(d)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            flash(f"Đã mở thư mục {d} trong trình quản lý file (trên máy đang chạy CICD App).", "ok")
        except Exception as e:  # noqa: BLE001
            flash(f"Không mở được trình quản lý file: {e}. Đường dẫn: {d}", "error")
    return redirect(url_for("project_view", pid=pid))


# ---------------------------------------------------------------- cài đặt
@app.route("/settings", methods=["GET", "POST"])
def settings():
    f = {"base_dir": base_dir(), "bbk_server": store.get_setting("bbk_server") or "",
         "bbk_username": store.get_setting("bbk_username") or ""}
    if request.method == "POST":
        f = {k: request.form.get(k, "").strip() for k in f}
        f["bbk_server"] = f["bbk_server"].rstrip("/")
        errs = []
        full = os.path.expanduser(f["base_dir"])
        example = "D:\\githubapps" if config.IS_WINDOWS else "/home/<user>/githubapps"
        if not f["base_dir"] or not os.path.isabs(full):
            errs.append(f"Thư mục chứa project mặc định phải là đường dẫn tuyệt đối, vd: {example}")
        if f["bbk_server"] and not re.match(r"^https://[^\s/@]+(/[^\s@]*)?$", f["bbk_server"]):
            errs.append("Bitbucket Server URL phải dạng https://bbk-rndsoft.ingenico.com")
        if f["bbk_username"] and not re.match(r"^[\w.@+-]+$", f["bbk_username"]):
            errs.append("Username bbk không hợp lệ.")
        if not errs:
            try:
                os.makedirs(full, exist_ok=True)
            except OSError as e:
                errs.append(f"Không tạo được thư mục {full}: {e.strerror or e}")
        if not errs:
            store.set_setting("base_dir", os.path.abspath(full))
            store.set_setting("bbk_server", f["bbk_server"])
            store.set_setting("bbk_username", f["bbk_username"])
            flash("Đã lưu cài đặt.", "ok")
            return redirect(url_for("settings"))
        for e in errs:
            flash(e, "error")
    return render_template("settings.html", f=f)


# ---------------------------------------------------------------- duyệt thư mục (nút "Chọn thư mục")
def _roots() -> list[str]:
    if not config.IS_WINDOWS:
        return ["/"]
    if hasattr(os, "listdrives"):
        return list(os.listdrives())
    return [f"{c}:\\" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{c}:\\")]


@app.get("/api/fs")
def fs_list():
    raw = request.args.get("path") or base_dir()
    p = Path(os.path.expanduser(raw))
    while not p.is_dir() and p.parent != p:  # đi lên thư mục cha gần nhất đang tồn tại
        p = p.parent
    if not p.is_dir():
        p = Path.home()
    p = p.resolve()
    dirs, error = [], None
    try:
        with os.scandir(p) as it:
            for e in it:
                try:
                    if e.is_dir() and not e.name.startswith((".", "$")) and e.name != "System Volume Information":
                        dirs.append(e.name)
                except OSError:
                    pass
    except OSError as e:
        error = f"Không đọc được thư mục: {e.strerror or e}"
    dirs.sort(key=str.lower)
    return jsonify(path=str(p), parent=str(p.parent) if p.parent != p else None, dirs=dirs[:1000],
                   roots=_roots(), sep=os.sep, error=error)


@app.post("/api/fs/mkdir")
def fs_mkdir():
    parent, name = request.form.get("path", ""), request.form.get("name", "").strip()
    if not _valid_folder_name(name):
        return jsonify(error="Tên thư mục chỉ gồm chữ cái không dấu, số, '-', '_' hoặc '.'"), 400
    if not os.path.isabs(parent) or not os.path.isdir(parent):
        return jsonify(error="Thư mục cha không tồn tại"), 400
    target = os.path.join(parent, name)
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as e:
        return jsonify(error=f"Không tạo được thư mục: {e.strerror or e}"), 400
    return jsonify(path=target)


# ---------------------------------------------------------------- runs & logs
@app.get("/runs/<int:rid>")
def run_view(rid):
    run = store.get_run(rid)
    if not run:
        abort(404)
    return render_template("run.html", run=run, steps=store.list_steps(rid))


@app.get("/runs/<int:rid>/log.txt")
def run_log_raw(rid):
    path = pipeline.log_path(rid)
    if not path.exists():
        abort(404)
    return Response(path.read_text(encoding="utf-8", errors="replace"), mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f"inline; filename=run_{rid}.log"})


@app.get("/api/runs/<int:rid>")
def run_api(rid):
    run = store.get_run(rid)
    if not run:
        abort(404)
    offset = max(int(request.args.get("offset", 0)), 0)
    chunk, new_offset = "", offset
    path = pipeline.log_path(rid)
    if path.exists():
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(512 * 1024)
        cut = data.rfind(b"\n") + 1  # chỉ gửi các dòng hoàn chỉnh
        chunk = data[:cut].decode("utf-8", errors="replace")
        new_offset = offset + cut
    steps = store.list_steps(rid)
    for s in steps:
        s["duration"] = fmt_dur(s)
    return jsonify(status=run["status"], failed_step=run["failed_step"], error_summary=run["error_summary"],
                   commit_sha=run["commit_sha"], commit_msg=run["commit_msg"], duration=fmt_dur(run),
                   steps=steps, log=chunk, offset=new_offset)


# ---------------------------------------------------------------- webhook
@app.post("/hook/<int:pid>/<secret>")
def webhook(pid, secret):
    p = store.get_project(pid)
    if not p or not secrets.compare_digest(secret, p["webhook_secret"]):
        abort(404)
    if request.headers.get("X-GitHub-Event") == "ping":
        return jsonify(ok=True, msg="pong")
    payload = request.get_json(silent=True) or {}
    if not payload and request.form.get("payload"):
        try:
            payload = json.loads(request.form["payload"])
        except ValueError:
            payload = {}
    branches = set()
    if isinstance(payload.get("ref"), str):  # GitHub
        branches.add(payload["ref"].removeprefix("refs/heads/"))
    for ch in (payload.get("push") or {}).get("changes") or []:  # Bitbucket Cloud
        name = ((ch or {}).get("new") or {}).get("name")
        if name:
            branches.add(name)
    for ch in payload.get("changes") or []:  # Bitbucket Server / Data Center (repo:refs_changed)
        ref = (ch or {}).get("ref") or {}
        if ref.get("type", "BRANCH") == "BRANCH" and ref.get("displayId"):
            branches.add(ref["displayId"])
    if branches and p["branch"] not in branches:
        return jsonify(ok=True, msg=f"Bỏ qua: push vào nhánh {sorted(branches)}, dự án theo dõi '{p['branch']}'"), 202
    if not p["enabled"]:
        return jsonify(ok=False, msg="Dự án đang tắt"), 202
    rid = pipeline.start_run(pid, "Webhook (push)")
    if rid is None:
        pipeline.request_rerun(pid)
        return jsonify(ok=True, msg="Đang chạy - sẽ chạy lại khi run hiện tại kết thúc"), 202
    return jsonify(ok=True, run_id=rid), 201


# ---------------------------------------------------------------- khởi động
def create_app() -> Flask:
    config.ensure_dirs()
    store.init()
    key = store.get_setting("flask_secret")
    if not key:
        key = secrets.token_hex(32)
        store.set_setting("flask_secret", key)
    app.secret_key = key
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=7 * 24 * 3600)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not shutil.which("git"):
        raise SystemExit("Chưa cài git: " + ("https://git-scm.com/download/win" if config.IS_WINDOWS else "sudo apt install git"))
    if not config.SHELL:
        raise SystemExit("Không tìm thấy bash.exe của Git for Windows. Cài Git for Windows, hoặc đặt biến CICD_SHELL "
                         "trỏ tới bash.exe (vd: C:\\Program Files\\Git\\bin\\bash.exe).")
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1" if config.HOST == "localhost" else config.HOST, config.PORT))
        except OSError:
            raise SystemExit(f"Cổng {config.PORT} đang được chương trình khác sử dụng (có thể CICD App đang chạy rồi). "
                             f"Dùng cổng khác, vd: python run.py {config.PORT + 1}")
    create_app()
    scheduler.start()
    import threading
    threading.Thread(target=app_runner.autostart_all, args=(pipeline.lock_for,), daemon=True, name="app-autostart").start()
    urls = netaddr.access_urls(config.HOST, config.PORT)
    url = urls[0]
    logging.info("CICD App đang chạy (lắng nghe %s:%s, dữ liệu: %s, shell: %s). Mở bằng:",
                 config.HOST, config.PORT, config.DATA_DIR, config.SHELL)
    for u in urls:
        logging.info("    %s", u)
    if config.HOST == "0.0.0.0":
        logging.info("Máy khác trong mạng mở được bằng các địa chỉ IP ở trên (cần mở firewall cho cổng %s nếu bị chặn).",
                     config.PORT)
    if config.OPEN_BROWSER:
        import threading
        import webbrowser
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    try:
        from waitress import serve
        serve(app, host=config.HOST, port=config.PORT, threads=8)
    except ImportError:
        app.run(host=config.HOST, port=config.PORT, threaded=True)
