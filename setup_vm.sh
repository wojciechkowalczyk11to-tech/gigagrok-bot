#!/bin/bash
# GigaGrok Production Setup for GCE e2-micro (Ubuntu 24.04)
set -euo pipefail

# System update
sudo apt update && sudo apt upgrade -y

# Python 3.12
sudo apt install -y python3.12 python3.12-venv python3-pip

# System deps
sudo apt install -y ffmpeg git curl

# Cloudflared
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install -y cloudflared

# User
sudo useradd -r -m -s /bin/bash gigagrok

# Clone repo
sudo -u gigagrok git clone https://ghp_TOKEN@github.com/USER/gigagrok-bot.git /opt/gigagrok
cd /opt/gigagrok

# Venv
sudo -u gigagrok python3.12 -m venv venv
sudo -u gigagrok ./venv/bin/pip install -r requirements.txt

# .env
sudo -u gigagrok cp .env.example .env
echo ">>> EDYTUJ /opt/gigagrok/.env z kluczami API"

# Systemd
sudo cp gigagrok.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable gigagrok

echo ">>> Setup complete. Edit .env then: sudo systemctl start gigagrok"
