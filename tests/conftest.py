from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("XAI_API_KEY", "test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "token")
os.environ.setdefault("WEBHOOK_URL", "https://example.com")
os.environ.setdefault("WEBHOOK_SECRET", "secret")
