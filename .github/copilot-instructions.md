# GigaGrok — Copilot Instructions

## Projekt
GigaGrok — Telegram bot zasilany Grok 4.1 Fast Reasoning API (xAI). Single-process Python, SQLite, webhook via Cloudflare Tunnel.

## Stack
- Python 3.10+ (VM ma 3.10, NIE używaj features z 3.11+)
- python-telegram-bot 21.x (webhook mode, NIE polling)
- httpx (async HTTP client do xAI API)
- aiosqlite (async SQLite)
- pydantic-settings (config z .env)
- structlog (logowanie)
- aiohttp (jeśli multi-bot webhook)

## Zasady absolutne
1. **Zero placeholderów** — żadnych TODO, "implement later", "add here", pass
2. **Każdy plik kompletny** — gotowy do uruchomienia
3. **Type hints wszędzie**
4. **Error handling na KAŻDYM poziomie** — żaden exception nie wyleci do usera
5. **Python 3.10 kompatybilność** — nie używaj: match/case, getLevelNamesMapping(), tomllib, ExceptionGroup, TaskGroup
6. **Backward compatible** — nowe fazy nie łamią istniejącego kodu

## Architektura
```
gigagrok-bot/
├── main.py              # Entry point + webhook server
├── config.py            # Pydantic BaseSettings z .env
├── grok_client.py       # xAI API client (OpenAI-compatible format)
├── db.py                # SQLite + async CRUD
├── utils.py             # Helpery, auth check, text processing
├── tools.py             # Agent Tools API definitions (faza 3+)
├── file_utils.py        # File extraction (faza 4+)
├── handlers/
│   ├── __init__.py
│   ├── start.py         # /start, /help
│   ├── chat.py          # Message handler + streaming
│   ├── admin.py         # /users, /adduser, /removeuser (admin only)
│   ├── mode.py          # /fast, /think, /clear (faza 2+)
│   ├── settings.py      # /system, /stats (faza 2+)
│   ├── search.py        # /websearch, /xsearch (faza 3+)
│   ├── code.py          # /code (faza 3+)
│   ├── analyze.py       # /analyze (faza 3+)
│   ├── image.py         # /image + photo handler (faza 4+)
│   ├── file.py          # /file + document handler (faza 4+)
│   ├── collection.py    # /collection (faza 5+)
│   ├── export.py        # /export (faza 5+)
│   ├── voice.py         # voice + /voice (faza 6+)
│   ├── gigagrok.py      # /gigagrok full power (faza 7+)
│   ├── github.py        # /github (faza 8+)
│   └── workspace.py     # /workspace (faza 8+)
├── requirements.txt
├── .env                 # NIE COMMITUJ — w .gitignore
├── .env.example         # Szablon
└── .gitignore
```

## Autoryzacja (po Fazie 1.5)
- Multi-user: `ALLOWED_USER_IDS=id1,id2,id3,id4,id5` w .env
- NIE używaj `if user_id != settings.admin_user_id` — to stare
- UŻYWAJ `check_access(update, settings)` z utils.py — zwraca True/False
- Admin check: `settings.is_admin(user_id)` — pierwszy ID z listy
- Dynamic users: /adduser, /removeuser przez admina (zapisane w DB)
- Każdy user ma osobną historię i ustawienia (per user_id w DB)

## xAI API
- Base URL: https://api.x.ai/v1
- Format: OpenAI-compatible (POST /chat/completions)
- Model reasoning: grok-4-1-fast-reasoning (z parametrem "reasoning": {"effort": "high"})
- Model fast: grok-4-1-fast (BEZ parametru "reasoning")
- Pricing: $0.20/1M input, $0.50/1M output tokens
- Agent Tools (server-side): web_search, x_search, code_execution, collections_search
- Image input: base64 w messages (type: image_url)
- Streaming: SSE, delta.content + delta.reasoning_content
- WAŻNE: Web search NIE działa przez tools parameter. Używaj:
  body["search"] = {"enabled": True}
Parametr "search" dodaje się bezpośrednio do body requestu, nie jako tool.
grok_client.py ma parametr search: dict | None w chat_stream() i chat().

## Telegram
- Webhook mode na grok.nexus-oc.pl/webhook (port 8443)
- Streaming: edytuj wiadomość co 1.5s
- Markdown/HTML parse mode
- Max message length: 4096 — split_message() w utils.py
- Footer pod każdą odpowiedzią: model | tokeny | koszt | czas

## DB Schema (aktualne)
- conversations: user_id, role, content, reasoning_content, model, tokens_in/out, reasoning_tokens, cost_usd, created_at
- user_settings: user_id, system_prompt, reasoning_effort, voice_enabled
- usage_stats: user_id, date, total_requests, total_tokens_in/out/reasoning, total_cost_usd
- dynamic_users: user_id, added_by, added_at

## Styl kodu
- async/await wszędzie (bot jest async)
- structlog do logowania (NIE print)
- f-strings
- Docstringi po polsku lub angielsku
- Import: from config import settings (singleton)
- Handlery: każda komenda w osobnej async function
- Error messages po polsku (bot jest po polsku)

## Czego NIE robić
- NIE używaj Docker — single process Python
- NIE używaj PostgreSQL/Redis — SQLite only
- NIE używaj FastAPI — webhook przez python-telegram-bot lub aiohttp
- NIE używaj polling — tylko webhook
- NIE twórz testów jednostkowych — smoke test ręczny
- NIE dodawaj features spoza aktualnej fazy
- NIE zmieniaj .env — to plik usera, zmieniaj .env.example
