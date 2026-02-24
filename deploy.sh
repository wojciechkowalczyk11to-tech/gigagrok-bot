#!/bin/bash
# Deploy latest code to production
set -euo pipefail

PROD_VM="gigagrok-prod"  # GCE instance name
ZONE="us-central1-c"

echo "🚀 Deploying GigaGrok..."
gcloud compute ssh $PROD_VM --zone=$ZONE --command="
    cd /opt/gigagrok &&
    sudo -u gigagrok git pull &&
    sudo -u gigagrok ./venv/bin/pip install -r requirements.txt --quiet &&
    sudo systemctl restart gigagrok &&
    sleep 3 &&
    sudo systemctl status gigagrok --no-pager
"
echo "✅ Deploy complete"
