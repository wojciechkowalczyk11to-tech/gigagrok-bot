#!/bin/bash
# Cron: 0 3 * * * /opt/gigagrok/backup.sh
set -euo pipefail

TIMESTAMP=$(date +%Y%m%d_%H%M)
cp /opt/gigagrok/gigagrok.db /tmp/gigagrok_${TIMESTAMP}.db
gsutil cp /tmp/gigagrok_${TIMESTAMP}.db gs://gigagrok-backups/
rm /tmp/gigagrok_${TIMESTAMP}.db
# Zachowaj ostatnie 30 backupów
gsutil ls gs://gigagrok-backups/ | head -n -30 | xargs -r gsutil rm
