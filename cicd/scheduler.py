"""Bộ lập lịch: định kỳ kiểm tra repository, có commit mới thì tự động chạy pipeline."""
import logging
import threading
import time

from . import config, pipeline, store

log = logging.getLogger("cicd.scheduler")


def _tick() -> None:
    now = time.time()
    for p in store.list_projects():
        if not p["enabled"] or p["poll_interval"] <= 0:
            continue
        if p["last_poll_at"] and now - p["last_poll_at"] < p["poll_interval"]:
            continue
        if pipeline.is_running(p["id"]):
            continue
        sha, err = None, None
        try:
            sha = pipeline.remote_head(p)
        except Exception as e:  # noqa: BLE001
            err = str(e)[:1000]
            log.warning("Poll '%s' lỗi: %s", p["name"], err)
            if pipeline.is_auth_failure(err):
                # Không thử lại mỗi chu kỳ: đăng nhập sai liên tục sẽ khiến server khoá tài khoản
                pipeline.pause_for_auth(p["id"], err)
                log.warning("'%s': đăng nhập thất bại -> đã tắt tự động chạy", p["name"])
                continue
        store.execute("UPDATE projects SET last_poll_at=?, last_poll_error=? WHERE id=?", (time.time(), err, p["id"]))
        if sha and sha != p["last_sha"]:
            # Ghi nhận sha trước để một commit lỗi không bị chạy lặp lại mỗi chu kỳ
            store.execute("UPDATE projects SET last_sha=? WHERE id=?", (sha, p["id"]))
            log.info("'%s': phát hiện commit mới %s -> chạy pipeline", p["name"], sha[:8])
            pipeline.start_run(p["id"], f"Phát hiện commit mới {sha[:8]}")


def _loop(stop: threading.Event) -> None:
    while not stop.wait(config.SCHEDULER_TICK):
        try:
            _tick()
        except Exception:  # noqa: BLE001
            log.exception("Lỗi trong bộ lập lịch")


def start() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=_loop, args=(stop,), daemon=True, name="scheduler").start()
    return stop
