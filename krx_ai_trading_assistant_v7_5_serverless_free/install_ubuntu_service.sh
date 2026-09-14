#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="$(id -un)"
PYTHON_BIN="$(command -v python3)"
SERVICE_NAME="krx-ai-mobile"

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "[중요] $ROOT/.env 파일에 KIS/DART/Telegram 키를 입력한 뒤 이 스크립트를 다시 실행하세요."
  exit 1
fi

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip

if [ ! -d "$ROOT/.venv" ]; then
  "$PYTHON_BIN" -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt"
chmod 600 "$ROOT/.env"

sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<EOF
[Unit]
Description=KRX/US Global Multi-Asset Trading Assistant v6
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${ROOT}
EnvironmentFile=${ROOT}/.env
Environment=PYTHONUNBUFFERED=1
Environment=TZ=Asia/Seoul
ExecStart=${ROOT}/.venv/bin/python ${ROOT}/run.py app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"
echo
sudo systemctl --no-pager status "$SERVICE_NAME" || true
echo
echo "설치 완료. 로그: journalctl -u ${SERVICE_NAME} -f"
echo "외부 인터넷에 공개한다면 APP_API_TOKEN + HTTPS를 반드시 사용하세요."
