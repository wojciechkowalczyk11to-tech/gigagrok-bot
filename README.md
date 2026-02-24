# 🧠 GigaGrok

Telegram bot powered by Grok 4.1 Fast Reasoning.

## Setup
1. `pip install -r requirements.txt`
2. `cp .env.example .env` — uzupełnij klucze
3. `python main.py`

## Requirements
- Python 3.12+
- xAI API key (console.x.ai)
- ffmpeg (`sudo apt install ffmpeg`) — wymagane do odpowiedzi głosowych TTS
- Telegram Bot token (@BotFather)
- Cloudflare Tunnel na grok.nexus-oc.pl → localhost:8443

## Production Deployment (GCE e2-micro)
1. Uruchom `/opt/gigagrok/setup_vm.sh` na nowej VM (Ubuntu 24.04), potem edytuj `/opt/gigagrok/.env`.
2. Wgraj i aktywuj unit: `sudo cp gigagrok.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now gigagrok`.
3. Kolejne wdrożenia: `./deploy.sh` (z lokalnej/dev VM z `gcloud`).
4. Backup bazy przez cron: `0 3 * * * /opt/gigagrok/backup.sh`.
5. Healthcheck: `GET /health` na porcie `8080` (np. UptimeRobot co 5 minut).
