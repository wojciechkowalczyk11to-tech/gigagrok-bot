#!/bin/bash
# Deploy latest code to production GCE VM
set -euo pipefail

PROD_VM="${PROD_VM:-gigagrok-prod}"   # GCE instance name (można nadpisać env var)
ZONE="${ZONE:-us-central1-c}"

echo "🚀 Deploying GigaGrok to ${PROD_VM} (${ZONE})..."

gcloud compute ssh "$PROD_VM" --zone="$ZONE" --command="
  set -euo pipefail
  cd /opt/gigagrok
  sudo -u gigagrok git pull --ff-only
  sudo -u gigagrok ./venv/bin/pip install -r requirements.txt --quiet
  sudo systemctl restart gigagrok
  sleep 3
  sudo systemctl status gigagrok --no-pager
"

echo "✅ Deploy complete"
