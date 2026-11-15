#!/usr/bin/env bash
# Cài CICD App trên Linux Mint / Ubuntu thành dịch vụ systemd chạy dưới user hiện tại.
#   ./install.sh               -> cài, chạy ở cổng 8088 trên mọi địa chỉ IP của máy
#   CICD_PORT=9000 ./install.sh
#   CICD_HOST=127.0.0.1 ./install.sh -> chỉ cho chính máy này truy cập
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${CICD_PORT:-8088}"
HOST="${CICD_HOST:-0.0.0.0}"
SERVICE_DIR="$HOME/.config/systemd/user"

if [[ $EUID -eq 0 ]]; then
  echo "Đừng chạy bằng root/sudo. Hãy chạy bằng user sẽ sở hữu các thư mục app." >&2
  exit 1
fi

echo "==> Cài gói hệ thống (git, python3-venv)"
sudo apt-get update -qq
sudo apt-get install -y git python3 python3-venv python3-pip

echo "==> Tạo môi trường Python ảo"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip -q
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "==> Tạo dịch vụ systemd (user): cicd-app"
mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_DIR/cicd-app.service" <<EOF
[Unit]
Description=CICD App - mini CI/CD
After=network-online.target

[Service]
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/run.py
Environment=CICD_HOST=$HOST
Environment=CICD_PORT=$PORT
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5
# Các app do CICD App deploy (chạy nền) không bị dừng theo khi restart/stop CICD App
KillMode=process

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now cicd-app.service

# Cho phép dịch vụ chạy cả khi chưa đăng nhập / sau khi reboot
sudo loginctl enable-linger "$USER" || true

echo
echo "Hoàn tất! Mở trình duyệt:"
echo "  http://127.0.0.1:$PORT"
if [[ $HOST == 0.0.0.0 ]]; then
  for ip in $(hostname -I); do
    if [[ $ip == *.*.*.* ]]; then echo "  http://$ip:$PORT"; fi
  done
  echo "  (máy khác trong mạng dùng các địa chỉ IP ở trên; nếu bật ufw: sudo ufw allow $PORT/tcp)"
fi
echo "  Trạng thái : systemctl --user status cicd-app"
echo "  Log app    : journalctl --user -u cicd-app -f"
echo "  Khởi động lại: systemctl --user restart cicd-app"
