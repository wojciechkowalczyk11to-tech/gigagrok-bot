#!/bin/bash
# Cron: 0 3 * * * /opt/gigagrok/backup.sh
set -euo pipefail

TIMESTAMP=$(date +%Y%m%d_%H%M)
python3 - "$TIMESTAMP" <<'PY'
import sqlite3
import sys

timestamp = sys.argv[1]
source = "/opt/gigagrok/gigagrok.db"
destination = f"/tmp/gigagrok_{timestamp}.db"

with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
    source_db.backup(backup_db)
PY
gsutil cp /tmp/gigagrok_${TIMESTAMP}.db gs://gigagrok-backups/
rm /tmp/gigagrok_${TIMESTAMP}.db
# Zachowaj ostatnie 30 backupów
gsutil ls gs://gigagrok-backups/ | sort -r | tail -n +31 | xargs -r gsutil rm
