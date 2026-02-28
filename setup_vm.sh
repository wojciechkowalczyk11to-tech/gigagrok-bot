#!/bin/bash
# GigaGrok Production Setup for GCE e2-micro (Ubuntu 24.04)
set -euo pipefail

echo "=== GigaGrok VM Setup ==="

# System update
sudo apt update && sudo apt upgrade -y

# Python 3.12 + deps
sudo apt install -y python3.12 python3.12-venv python3-pip

# System deps (ffmpeg required for voice TTS)
sudo apt install -y ffmpeg git curl

# Cloudflared
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install -y cloudflared

# Dedicated system user
if ! id gigagrok &>/dev/null; then
  sudo useradd -r -m -s /bin/bash gigagrok
  echo ">>> Utworzono użytkownika: gigagrok"
fi

# App directory
sudo mkdir -p /opt/gigagrok
sudo chown gigagrok:gigagrok /opt/gigagrok

# Workspace directory
sudo mkdir -p /opt/gigagrok/workspaces
sudo chown gigagrok:gigagrok /opt/gigagrok/workspaces

# Clone repo
REPO_URL="${REPO_URL:-https://github.com/wojciechkowalczyk11to-tech/gigagrok-bot.git}"
if [ -n "${GITHUB_TOKEN:-}" ]; then
  REPO_URL="https://${GITHUB_TOKEN}@${REPO_URL#https://}"
fi

if [ -d /opt/gigagrok/.git ]; then
  echo ">>> Repo już istnieje — pomijam clone"
else
  sudo -u gigagrok git clone "$REPO_URL" /opt/gigagrok
fi

# Python venv + dependencies
sudo -u gigagrok python3.12 -m venv /opt/gigagrok/venv
sudo -u gigagrok /opt/gigagrok/venv/bin/pip install --upgrade pip
sudo -u gigagrok /opt/gigagrok/venv/bin/pip install -r /opt/gigagrok/requirements.txt

# .env config
if [ ! -f /opt/gigagrok/.env ]; then
  sudo -u gigagrok cp /opt/gigagrok/.env.example /opt/gigagrok/.env
  sudo chmod 600 /opt/gigagrok/.env
  echo ">>> EDYTUJ /opt/gigagrok/.env z kluczami API przed uruchomieniem!"
else
  echo ">>> /opt/gigagrok/.env już istnieje — nie nadpisuję"
fi

# Systemd service
sudo cp /opt/gigagrok/gigagrok.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable gigagrok

echo ""
echo "=== Setup zakończony ==="
echo ">>> Kroki po instalacji:"
echo "  1. Uzupełnij: sudo nano /opt/gigagrok/.env"
echo "  2. Uruchom:   sudo systemctl start gigagrok"
echo "  3. Sprawdź:   sudo systemctl status gigagrok"
echo "  4. Logi:      sudo journalctl -u gigagrok -f"
