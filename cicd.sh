#!/usr/bin/env bash
# CICD App launcher cho Linux (Linux Mint / Ubuntu / Debian).
#
#   ./cicd.sh install        lần đầu, trong thư mục cicd-app: tạo .venv, cài thư viện, tạo lệnh `cicd`
#   cicd start [PORT]        chạy nền ở cổng PORT (mặc định 8088)
#   cicd stop                dừng
#   cicd restart [PORT]      khởi động lại; không ghi PORT = giữ cổng đang chạy
#   cicd status              trạng thái: PID, cổng, địa chỉ, thời gian chạy
#   cicd logs [-f]           xem log (-f: theo dõi liên tục)
#
# PORT: 8888 | --port 8888 | -port 8888 | -p 8888.  Thêm --host 0.0.0.0 để máy khác trong mạng LAN truy cập.
set -euo pipefail

SELF="$(readlink -f "${BASH_SOURCE[0]}")"
APP_DIR="$(dirname "$SELF")"
VENV="$APP_DIR/.venv"
PY="$VENV/bin/python"
MARK="$VENV/requirements.installed"
BIN_DIR="$HOME/.local/bin"

setup_venv() {
    if ! command -v python3 >/dev/null 2>&1; then
        echo "✗ Chưa có Python 3: sudo apt install python3 python3-venv" >&2
        exit 1
    fi
    if [ ! -x "$PY" ]; then
        echo "→ Tạo môi trường Python (.venv) ..."
        python3 -m venv "$VENV" || { echo "✗ Không tạo được .venv: sudo apt install python3-venv" >&2; exit 1; }
    fi
    echo "→ Cài thư viện (requirements.txt) ..."
    "$PY" -m pip install -q --disable-pip-version-check -r "$APP_DIR/requirements.txt"
    cp "$APP_DIR/requirements.txt" "$MARK"
}

if [ "${1:-}" = "install" ]; then
    command -v git >/dev/null 2>&1 || echo "! Chưa có git: sudo apt install git"
    setup_venv
    chmod +x "$SELF"
    mkdir -p "$BIN_DIR"
    ln -sfn "$SELF" "$BIN_DIR/cicd"
    echo "✓ Đã cài lệnh: $BIN_DIR/cicd"
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) echo "! Thêm dòng sau vào ~/.profile rồi mở terminal mới:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac
    echo "  Thử: cicd start   |   cicd start 8888   |   cicd status"
    exit 0
fi

# Tự cài / cập nhật môi trường khi chưa có .venv hoặc requirements.txt đã thay đổi
if [ ! -x "$PY" ] || ! cmp -s "$APP_DIR/requirements.txt" "$MARK" 2>/dev/null; then
    setup_venv
fi

exec "$PY" "$APP_DIR/cicd_cli.py" "$@"
