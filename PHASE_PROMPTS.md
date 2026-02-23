# GigaGrok — Phase Prompts for Claude Code

Plik zawiera kompletne prompty do realizacji każdej fazy projektu GigaGrok.
Każdy prompt jest samowystarczalny — zawiera pełny kontekst, zasady i specyfikację.

Użycie:
```bash
cd ~/gigagrok-bot
claude "Realizujemy Fazę N. [wklej prompt fazy N]"
```

Lub automatycznie:
```bash
claude "$(cat PHASE_PROMPTS.md | sed -n '/^## PROMPT: FAZA 1$/,/^## PROMPT: FAZA 2$/p' | head -n -1)"
```

---

## PROMPT: FAZA 1

Jesteś ekspertem od budowy Telegram botów z Python i xAI API. Realizujesz FAZĘ 1 projektu GigaGrok — fundament bota z Grok 4.1 Fast Reasoning.

ZASADY ABSOLUTNE:
- Zero placeholderów, zero TODO, zero "implement later", zero "add here"
- Każdy plik musi być KOMPLETNY i gotowy do uruchomienia
- Type hints wszędzie
- Logowanie z structlog
- Obsługa błędów na KAŻDYM poziomie — żaden exception nie może wylecieć do usera bez czytelnego komunikatu
- Webhook mode (NIE polling) — bot nasłuchuje na localhost:8443
- Single-user bot — tylko ADMIN_USER_ID ma dostęp

STACK:
- Python 3.12
- python-telegram-bot==21.0.1
- httpx==0.27.0
- aiosqlite==0.20.0
- pydantic-settings==2.1.0
- structlog
- python-dotenv==1.0.1

STRUKTURA PLIKÓW DO STWORZENIA:
```
gigagrok-bot/
├── main.py
├── config.py
├── grok_client.py
├── db.py
├── utils.py
├── handlers/
│   ├── __init__.py
│   ├── start.py
│   └── chat.py
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

=== SPECYFIKACJA KAŻDEGO PLIKU ===

1. config.py — Pydantic BaseSettings z .env:

```python
# Wymagane:
XAI_API_KEY: str              # xAI API key z console.x.ai
TELEGRAM_BOT_TOKEN: str       # Token od @BotFather
ADMIN_USER_ID: int            # Twój Telegram user ID
WEBHOOK_URL: str              # "https://grok.nexus-oc.pl"
WEBHOOK_PATH: str = "webhook" # Path for webhook
WEBHOOK_PORT: int = 8443      # Port lokalny
WEBHOOK_SECRET: str           # Losowy string do weryfikacji

# Opcjonalne z domyślnymi:
XAI_BASE_URL: str = "https://api.x.ai/v1"
XAI_MODEL_REASONING: str = "grok-4-1-fast-reasoning"
XAI_MODEL_FAST: str = "grok-4-1-fast"
DB_PATH: str = "gigagrok.db"
MAX_HISTORY: int = 20
MAX_OUTPUT_TOKENS: int = 16000
DEFAULT_REASONING_EFFORT: str = "high"  # low/medium/high
LOG_LEVEL: str = "INFO"
```

System prompt (wbudowany w config jako stała, NIE w .env):
```
Jesteś GigaGrok — najinteligentniejszy asystent AI zasilany Grok 4.1 Fast Reasoning.

Twoje cechy:
- Myślisz głęboko przed odpowiedzią (chain-of-thought reasoning)
- Odpowiadasz konkretnie, bez zbędnego fluffu
- Kod formatujesz w blokach z oznaczeniem języka
- Jesteś ekspertem od programowania, analizy danych, strategii biznesowej
- Mówisz po polsku gdy pytany po polsku, po angielsku gdy po angielsku
- Jesteś szczery — mówisz "nie wiem" gdy nie wiesz
- Przy złożonych problemach rozkładasz je na kroki

Formatowanie:
- Markdown
- Kod w blokach ```język
- Listy numerowane dla kroków
- Pogrubienie dla kluczowych pojęć
- Bądź zwięzły ale kompletny

Aktualna data: {current_date}
```

2. db.py — Async SQLite z aiosqlite:

Tabele:
```sql
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,           -- 'user', 'assistant', 'system'
    content TEXT NOT NULL,
    reasoning_content TEXT,       -- reasoning tokens content (jeśli dostępne)
    model TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    system_prompt TEXT,
    reasoning_effort TEXT DEFAULT 'high',
    voice_enabled INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,           -- 'YYYY-MM-DD'
    total_requests INTEGER DEFAULT 0,
    total_tokens_in INTEGER DEFAULT 0,
    total_tokens_out INTEGER DEFAULT 0,
    total_reasoning_tokens INTEGER DEFAULT 0,
    total_cost_usd REAL DEFAULT 0.0,
    UNIQUE(user_id, date)
);

CREATE INDEX IF NOT EXISTS idx_conv_user_time ON conversations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stats_user_date ON usage_stats(user_id, date);
```

Funkcje:
- async init_db() — tworzy tabele
- async save_message(user_id, role, content, reasoning_content=None, model=None, tokens_in=0, tokens_out=0, reasoning_tokens=0, cost_usd=0.0) — zapisz wiadomość
- async get_history(user_id, limit=20) -> list[dict] — pobierz historię (role, content)
- async clear_history(user_id) -> int — wyczyść, zwróć ile usunięto
- async update_daily_stats(user_id, tokens_in, tokens_out, reasoning_tokens, cost_usd) — aktualizuj stats
- async get_daily_stats(user_id, date=None) -> dict — dzisiejsze statystyki
- async get_all_time_stats(user_id) -> dict — łączne statystyki
- async set_user_setting(user_id, key, value) — zapisz ustawienie
- async get_user_setting(user_id, key) -> str|None — pobierz ustawienie

Kalkulacja kosztów (stałe w pliku):
```python
COST_PER_M_INPUT = 0.20    # $0.20 per 1M input tokens
COST_PER_M_OUTPUT = 0.50   # $0.50 per 1M output tokens (reasoning tokens też)

def calculate_cost(tokens_in: int, tokens_out: int, reasoning_tokens: int) -> float:
    input_cost = (tokens_in / 1_000_000) * COST_PER_M_INPUT
    output_cost = ((tokens_out + reasoning_tokens) / 1_000_000) * COST_PER_M_OUTPUT
    return round(input_cost + output_cost, 6)
```

3. grok_client.py — xAI API Client:

Klasa GrokClient:
- __init__(api_key, base_url) — tworzy httpx.AsyncClient z timeout=120s
- async chat_stream(messages, model, reasoning_effort=None, tools=None) — generator yielding tuples:
  * Yield: ("content", chunk_text) | ("reasoning", chunk_text) | ("status", status_msg) | ("done", usage_dict)
  * usage_dict: {"prompt_tokens": int, "completion_tokens": int, "reasoning_tokens": int}
- async chat(messages, model, reasoning_effort=None, tools=None) -> dict — non-streaming, zwraca pełną odpowiedź
- async close() — zamknij client

Implementacja streaming:
```
POST {base_url}/chat/completions
Headers: Authorization: Bearer {api_key}, Content-Type: application/json
Body:
{
    "model": "grok-4-1-fast-reasoning",
    "messages": [...],
    "stream": true,
    "max_tokens": 16000,
    "reasoning": {"effort": "high"}   // TYLKO dla modelu reasoning, pomiń dla fast
}

Response SSE format (OpenAI-compatible):
data: {"id":"...","choices":[{"index":0,"delta":{"reasoning_content":"thinking..."}}]}
data: {"id":"...","choices":[{"index":0,"delta":{"content":"Hello"}}]}
data: {"id":"...","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":50,"completion_tokens_details":{"reasoning_tokens":30}}}
data: [DONE]
```

WAŻNE:
- reasoning_content przychodzi PRZED content
- usage przychodzi w ostatnim DONE-adjacent chunk lub w chunk z finish_reason
- reasoning_tokens są w usage.completion_tokens_details.reasoning_tokens
- Parametr "reasoning" dodawaj TYLKO dla modelu reasoning, NIE dla fast
- Retry z exponential backoff: 3 próby, 1s/2s/4s delay
- Timeout: 120s (reasoning może trwać długo)

4. utils.py — Helpery:

- escape_html(text: str) -> str — escape < > & dla HTML parse mode
- split_message(text: str, max_length: int = 4000) -> list[str] — dziel na kawałki dla Telegrama (limit 4096)
  - Dziel po podwójnym newline, potem po newline, potem po spacji
  - Nigdy nie tnij w środku bloku kodu (``` ... ```)
- format_footer(model: str, tokens_in: int, tokens_out: int, reasoning_tokens: int, cost_usd: float, elapsed_seconds: float) -> str
  - Format: "⚙️ grok-4-1-fast-reasoning | 📥 234 📤 567 🧠 890 | 💰 $0.0004 | ⏱ 3.2s"
- format_number(n: int) -> str — 1234 → "1.2K", 1234567 → "1.2M"
- get_current_date() -> str — "2026-02-23"

5. handlers/start.py:

/start — Powitanie:
```
🧠 **GigaGrok** — Twój asystent AI

Zasilany przez **Grok 4.1 Fast Reasoning**
• 2M tokenów kontekstu
• Deep reasoning (chain-of-thought)
• Web search, X search, code execution
• Analiza obrazów i dokumentów

Wyślij mi wiadomość, a odpowiem z pełną mocą reasoning.

Wpisz /help po listę komend.
```

/help — Lista komend (sformatowana czytelnie):
```
📚 **Komendy GigaGrok**

💬 **Chat:**
Wyślij wiadomość → odpowiedź z reasoning

⚡ **/fast** <tekst> → szybka odpowiedź bez reasoning
🧠 **/think** <tekst> → deep reasoning mode
🔍 **/websearch** <query> → szukaj w internecie
🐦 **/xsearch** <query> → szukaj na X/Twitter
💻 **/code** <prompt> → generuj i uruchom kod
🔬 **/analyze** <tekst> → głęboka analiza
🖼 **/image** → wyślij zdjęcie do analizy
📎 **/file** → wyślij plik do analizy
🚀 **/gigagrok** <prompt> → FULL POWER mode

⚙️ **Ustawienia:**
/system <prompt> → ustaw system prompt
/clear → wyczyść historię
/stats → statystyki użycia
/voice → toggle odpowiedzi głosowych

📦 **/collection** → zarządzaj bazą wiedzy
📥 **/export** → eksportuj historię
```

6. handlers/chat.py — Główny handler wiadomości:

Workflow:
```python
async def handle_message(update, context):
    user_id = update.effective_user.id
    
    # 1. Auth check
    if user_id != settings.admin_user_id:
        await update.message.reply_text("⛔ Brak dostępu.")
        return
    
    query = update.message.text
    
    # 2. Pobierz historię z DB
    history = await db.get_history(user_id, limit=settings.max_history)
    
    # 3. Pobierz custom system prompt (lub domyślny)
    custom_prompt = await db.get_user_setting(user_id, "system_prompt")
    system_prompt = custom_prompt or DEFAULT_SYSTEM_PROMPT.format(current_date=get_current_date())
    
    # 4. Zbuduj messages
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": query})
    
    # 5. Wyślij placeholder
    sent = await update.message.reply_text("🧠 <i>Grok myśli...</i>", parse_mode="HTML")
    
    # 6. Stream odpowiedzi
    start_time = time.time()
    full_content = ""
    full_reasoning = ""
    usage = {}
    last_edit = 0
    
    try:
        async for event_type, data in grok.chat_stream(messages, model=settings.xai_model_reasoning, reasoning_effort=settings.default_reasoning_effort):
            if event_type == "reasoning":
                full_reasoning += data
                # Opcjonalnie: pokaż "🧠 Reasoning..." z długością
                now = time.time()
                if now - last_edit > 2.0:
                    await sent.edit_text(f"🧠 <i>Grok myśli... ({len(full_reasoning)} znaków reasoning)</i>", parse_mode="HTML")
                    last_edit = now
            
            elif event_type == "content":
                full_content += data
                now = time.time()
                if now - last_edit > 1.5:
                    display = full_content[:3800]
                    if len(full_content) > 3800:
                        display += "\n\n<i>... (kontynuacja)</i>"
                    try:
                        await sent.edit_text(display, parse_mode="HTML")
                    except Exception:
                        pass  # Ignore edit errors (content unchanged, rate limit)
                    last_edit = now
            
            elif event_type == "done":
                usage = data
    
    except Exception as e:
        logger.error("Grok API error", error=str(e))
        await sent.edit_text(f"❌ Błąd API: {escape_html(str(e))}", parse_mode="HTML")
        return
    
    # 7. Finalna wiadomość z footerem
    elapsed = time.time() - start_time
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)
    reasoning_tokens = usage.get("reasoning_tokens", 0)
    cost = calculate_cost(tokens_in, tokens_out, reasoning_tokens)
    
    footer = format_footer(settings.xai_model_reasoning, tokens_in, tokens_out, reasoning_tokens, cost, elapsed)
    
    # 8. Wyślij finalną wersję (podziel jeśli za długa)
    final_text = f"{full_content}\n\n<code>{footer}</code>"
    parts = split_message(final_text, max_length=4000)
    
    await sent.edit_text(parts[0], parse_mode="HTML")
    for part in parts[1:]:
        await update.message.reply_text(part, parse_mode="HTML")
    
    # 9. Zapisz do DB
    await db.save_message(user_id, "user", query)
    await db.save_message(user_id, "assistant", full_content, reasoning_content=full_reasoning, model=settings.xai_model_reasoning, tokens_in=tokens_in, tokens_out=tokens_out, reasoning_tokens=reasoning_tokens, cost_usd=cost)
    await db.update_daily_stats(user_id, tokens_in, tokens_out, reasoning_tokens, cost)
```

7. main.py — Entry point:

```python
# Webhook mode:
application = Application.builder().token(settings.telegram_bot_token).build()

# Rejestracja handlerów
application.add_handler(CommandHandler("start", start_command))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Webhook setup
await application.bot.set_webhook(
    url=f"{settings.webhook_url}/{settings.webhook_path}",
    secret_token=settings.webhook_secret,
    allowed_updates=["message", "callback_query"],
)

# Run webhook server
application.run_webhook(
    listen="0.0.0.0",
    port=settings.webhook_port,
    url_path=settings.webhook_path,
    webhook_url=f"{settings.webhook_url}/{settings.webhook_path}",
    secret_token=settings.webhook_secret,
)
```

8. requirements.txt:
```
python-telegram-bot==21.0.1
httpx==0.27.0
aiosqlite==0.20.0
pydantic-settings==2.1.0
structlog
python-dotenv==1.0.1
```

9. .env.example:
```bash
# === REQUIRED ===
XAI_API_KEY=your_xai_api_key_from_console.x.ai
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
ADMIN_USER_ID=your_telegram_user_id

# === WEBHOOK (grok.nexus-oc.pl) ===
WEBHOOK_URL=https://grok.nexus-oc.pl
WEBHOOK_PATH=webhook
WEBHOOK_PORT=8443
WEBHOOK_SECRET=wygeneruj_losowy_string_32_znaki

# === OPTIONAL ===
# XAI_BASE_URL=https://api.x.ai/v1
# XAI_MODEL_REASONING=grok-4-1-fast-reasoning
# XAI_MODEL_FAST=grok-4-1-fast
# DB_PATH=gigagrok.db
# MAX_HISTORY=20
# MAX_OUTPUT_TOKENS=16000
# DEFAULT_REASONING_EFFORT=high
# LOG_LEVEL=INFO
```

10. .gitignore:
```
__pycache__/
*.py[cod]
*.so
.env
.env.local
*.db
*.sqlite3
venv/
.venv/
*.log
.DS_Store
.vscode/
.idea/
```

11. README.md:
```markdown
# 🧠 GigaGrok

Telegram bot powered by Grok 4.1 Fast Reasoning.

## Setup
1. `pip install -r requirements.txt`
2. `cp .env.example .env` — uzupełnij klucze
3. `python main.py`

## Requirements
- Python 3.12+
- xAI API key (console.x.ai)
- Telegram Bot token (@BotFather)
- Cloudflare Tunnel na grok.nexus-oc.pl → localhost:8443
```

WYGENERUJ WSZYSTKIE PLIKI. Kompletne, gotowe do uruchomienia po `pip install -r requirements.txt && python main.py`.

## PROMPT: FAZA 2

Realizujesz FAZĘ 2 projektu GigaGrok. Repo ma już działający fundament z Fazy 1.

KONTEKST REPO:
- main.py — entry point z webhook
- config.py — Pydantic settings
- grok_client.py — xAI API client ze streaming
- db.py — SQLite z historią i stats
- utils.py — helpery (escape, split, footer)
- handlers/start.py — /start, /help
- handlers/chat.py — obsługa wiadomości ze streaming

ZASADY: Zero placeholderów. Kompletny, działający kod. Type hints. Error handling.

ZADANIA:

1. Stwórz handlers/mode.py:

/fast <prompt> — Odpowiedź BEZ reasoning:
- Model: config.XAI_MODEL_FAST ("grok-4-1-fast")
- NIE dodawaj parametru "reasoning" do API request
- Streaming jak w chat.py ale bez reasoning status
- Footer z info że to tryb FAST
- Jeśli brak promptu po /fast → "Podaj prompt po /fast"

/think <prompt> — Deep reasoning:
- Model: config.XAI_MODEL_REASONING
- reasoning.effort = "high" ZAWSZE (override usera)
- Pokaż w streamie: "🧠 Deep reasoning... (X znaków)"
- Footer z reasoning tokens wyróżnione
- Jeśli brak promptu → "Podaj prompt po /think"

/clear — Wyczyść historię:
- Wywołaj db.clear_history(user_id)
- Odpowiedz: "🗑 Wyczyszczono X wiadomości z historii."

2. Stwórz handlers/settings.py:

/system — Zarządzanie system promptem:
- /system (bez args) → pokaż aktualny prompt (skrócony do 500 znaków)
- /system reset → przywróć domyślny, potwierdź
- /system <tekst> → zapisz jako custom system prompt
  - Zapisz do db.set_user_setting(user_id, "system_prompt", tekst)
  - Potwierdź: "✅ System prompt ustawiony (X znaków)"

/stats — Statystyki:
- Pobierz daily stats i all-time stats
- Format:
```
📊 **Statystyki GigaGrok**

📅 Dzisiaj:
  Zapytania: 15
  Tokeny: 📥 12.3K  📤 8.7K  🧠 5.2K
  Koszt: $0.0089

📈 Łącznie:
  Zapytania: 234
  Tokeny: 📥 180K  📤 95K  🧠 67K
  Koszt: $0.142

⚙️ Tryb: reasoning (high)
📝 System prompt: [domyślny | custom (234 znaków)]
```

3. Zmodyfikuj grok_client.py:
- Metoda chat_stream() i chat() muszą przyjmować parametr model: str
- Jeśli model nie zawiera "reasoning" w nazwie → NIE dodawaj parametru "reasoning" do body
- Reszta bez zmian

4. Zmodyfikuj handlers/chat.py:
- Pobieraj system prompt z DB (custom) lub config (domyślny)
- To powinno już działać z Fazy 1, ale upewnij się

5. Zaktualizuj main.py — dodaj rejestrację:
```python
application.add_handler(CommandHandler("fast", fast_command))
application.add_handler(CommandHandler("think", think_command))
application.add_handler(CommandHandler("clear", clear_command))
application.add_handler(CommandHandler("system", system_command))
application.add_handler(CommandHandler("stats", stats_command))
```

6. Zaktualizuj handlers/start.py /help — dodaj nowe komendy do listy.

Wygeneruj WSZYSTKIE nowe pliki i POKAŻ DOKŁADNE ZMIANY w istniejących plikach.

## PROMPT: FAZA 3

Realizujesz FAZĘ 3 projektu GigaGrok — Agent Tools API (web search, X search, code execution, analiza).

KONTEKST REPO: Fazy 1-2 zrealizowane. Bot ma chat, /fast, /think, /clear, /stats, /system.

KLUCZOWA INFORMACJA O xAI AGENT TOOLS:
Agent Tools API działa SERVER-SIDE. Nie implementujesz logiki narzędzi — xAI robi to za ciebie.
Dodajesz parametr "tools" do API request i Grok sam decyduje kiedy i jak użyć narzędzi.
Tool calls są DARMOWE ($0 za invocation). Płacisz tylko za tokeny.

xAI API FORMAT Z TOOLS:
```json
{
    "model": "grok-4-1-fast-reasoning",
    "messages": [...],
    "stream": true,
    "reasoning": {"effort": "high"},
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web for current information"
            }
        },
        {
            "type": "function",
            "function": {
                "name": "x_search",
                "description": "Search posts on X (Twitter)"
            }
        },
        {
            "type": "function",
            "function": {
                "name": "code_execution",
                "description": "Execute code in a sandboxed environment"
            }
        }
    ]
}
```

STREAMING Z TOOL CALLS:
Gdy Grok decyduje o użyciu narzędzia, stream zawiera:
1. reasoning_content — "Let me search for this..."
2. finish_reason: "tool_calls" + tool_calls array
3. [xAI EXECUTES TOOL SERVER-SIDE]
4. Nowy set of deltas z content (po tool execution)

W response SSE:
```
data: {"choices":[{"delta":{"reasoning_content":"I should search..."}}]}
data: {"choices":[{"delta":{"tool_calls":[{"function":{"name":"web_search","arguments":"{\"query\":\"...\"}"}}]}}]}
data: {"choices":[{"delta":{"content":"Based on my search..."}}]}
```

Grok może wywołać WIELE narzędzi w jednym zapytaniu, sekwencyjnie.

ZADANIA:

1. Stwórz tools.py — Definicje narzędzi:
```python
TOOL_WEB_SEARCH = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the internet for current information, news, facts, documentation"
    }
}

TOOL_X_SEARCH = {
    "type": "function",
    "function": {
        "name": "x_search",
        "description": "Search posts and discussions on X (Twitter)"
    }
}

TOOL_CODE_EXEC = {
    "type": "function",
    "function": {
        "name": "code_execution",
        "description": "Execute code in a sandboxed environment. Supports Python, JavaScript, and more."
    }
}

TOOLS_ALL = [TOOL_WEB_SEARCH, TOOL_X_SEARCH, TOOL_CODE_EXEC]

def get_tools(command: str) -> list[dict]:
    """Zwróć tools dla danej komendy."""
    mapping = {
        "websearch": [TOOL_WEB_SEARCH],
        "xsearch": [TOOL_X_SEARCH],
        "code": [TOOL_CODE_EXEC],
        "analyze": [TOOL_WEB_SEARCH, TOOL_CODE_EXEC],
        "gigagrok": TOOLS_ALL,
    }
    return mapping.get(command, [])
```

2. Zmodyfikuj grok_client.py:
- Dodaj parametr tools: list[dict] | None do chat_stream() i chat()
- Jeśli tools → dodaj "tools" do request body
- W streaming: obsłuż delta z tool_calls:
  - Gdy delta ma tool_calls → yield ("tool_use", tool_name)
  - Potem kontynuuj zbieranie content
- WAŻNE: xAI wykonuje toole server-side, więc po tool_calls delta dalej lecą content deltas z wynikami

3. Stwórz handlers/search.py:

/websearch <query>:
- Sprawdź auth
- System prompt: "Przeszukaj internet i podaj aktualne, szczegółowe informacje na temat: {query}. Cytuj źródła z URL. Strukturyzuj odpowiedź."
- tools=[TOOL_WEB_SEARCH]
- Model: reasoning z effort=medium (web search nie potrzebuje heavy reasoning)
- Status: "🔍 Szukam w internecie..."
- Streaming + footer

/xsearch <query>:
- System prompt: "Przeszukaj X/Twitter i podaj najnowsze posty i dyskusje na temat: {query}. Podaj autorów (@handle), daty i treść. Podsumuj nastroje/trendy."
- tools=[TOOL_X_SEARCH]
- Model: reasoning z effort=medium
- Status: "🐦 Szukam na X/Twitter..."
- Streaming + footer

4. Stwórz handlers/code.py:

/code <prompt>:
- System prompt: "Wygeneruj czysty, produkcyjny kod. Jeśli to możliwe — uruchom go w sandboxie i pokaż output. Język: wykryj z kontekstu lub Python domyślnie. Tylko kod i output, minimalne wyjaśnienia."
- tools=[TOOL_CODE_EXEC]
- Model: reasoning z effort=medium
- Status: "💻 Generuję kod..."
- Jeśli output kodu > 4000 znaków → wyślij jako plik .txt

5. Stwórz handlers/analyze.py:

/analyze <tekst lub reply na wiadomość>:
- Jeśli reply na wiadomość → pobierz tekst reply jako input
- Jeśli tekst po /analyze → użyj jako input
- System prompt: "Przeprowadź głęboką, wielowarstwową analizę poniższego tekstu/tematu. Rozłóż na czynniki pierwsze. Użyj web search do weryfikacji faktów. Jeśli potrzebne obliczenia — uruchom kod. Strukturyzuj wyniki: 1) Podsumowanie 2) Kluczowe wnioski 3) Analiza szczegółowa 4) Rekomendacje."
- tools=[TOOL_WEB_SEARCH, TOOL_CODE_EXEC]
- Model: reasoning z effort=high
- Status: "🔬 Analizuję głęboko..."

6. Zaktualizuj main.py — rejestracja:
```python
application.add_handler(CommandHandler("websearch", websearch_command))
application.add_handler(CommandHandler("xsearch", xsearch_command))
application.add_handler(CommandHandler("code", code_command))
application.add_handler(CommandHandler("analyze", analyze_command))
```

7. Zaktualizuj /help w start.py.

Wygeneruj WSZYSTKIE nowe pliki i DOKŁADNE ZMIANY w istniejących.

## PROMPT: FAZA 4

Realizujesz FAZĘ 4 projektu GigaGrok — Multimodal (obrazy i pliki).

KONTEKST REPO: Fazy 1-3 zrealizowane. Bot ma chat, tryby, agent tools (web, X, code, analyze).

xAI MULTIMODAL API — IMAGE INPUT:
Grok 4.1 Fast przyjmuje obrazy jako base64 w messages (format OpenAI-compatible):
```json
{
    "messages": [{
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64,{base64_data}"
                }
            },
            {
                "type": "text",
                "text": "Opisz co widzisz"
            }
        ]
    }]
}
```
Ograniczenia: JPEG/PNG only, max 5MB per image, 256-1792 tokenów per obraz.

ZADANIA:

1. Stwórz file_utils.py:

```python
import base64, io, zipfile
from pathlib import Path

async def image_to_base64(file_bytes: bytes, max_size_mb: float = 5.0) -> tuple[str, str]:
    """Konwertuj obraz do base64. Kompresuj jeśli za duży. Zwróć (base64_str, mime_type)."""
    # Użyj Pillow do resize/compress jeśli > max_size_mb
    # Zwróć base64 string i mime type (image/jpeg lub image/png)

async def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Wyciągnij tekst z PDF używając pdfplumber."""

async def extract_text_from_docx(file_bytes: bytes) -> str:
    """Wyciągnij tekst z DOCX używając python-docx."""

async def extract_text_from_zip(file_bytes: bytes) -> dict[str, str]:
    """Wypakuj ZIP, zwróć dict {filename: content} dla plików tekstowych."""
    # Obsłuż: .txt, .md, .py, .js, .json, .csv, .xml, .html, .yaml, .toml
    # Ignoruj: binarne, obrazy, > 1MB per plik

def smart_truncate(text: str, max_chars: int = 100_000) -> str:
    """Jeśli tekst > max_chars: zachowaj 40% z początku + 40% z końca + info o obcięciu."""

def detect_file_type(filename: str) -> str:
    """Zwróć kategorię: 'image', 'pdf', 'docx', 'zip', 'text', 'unknown'."""
    # Na podstawie rozszerzenia
```

2. Stwórz handlers/image.py:

Auto-detect zdjęć — MessageHandler na filters.PHOTO:
- Pobierz największą wersję zdjęcia (update.message.photo[-1])
- Pobierz file z Telegram API (await context.bot.get_file(file_id))
- Pobierz bytes (await file.download_as_bytearray())
- Konwertuj do base64
- Prompt domyślny: "Szczegółowo opisz i przeanalizuj ten obraz. Co widzisz? Jakie wnioski?"
- Jeśli user dodał caption → użyj jako prompt zamiast domyślnego
- Wyślij do Grok jako multimodal message
- Streaming + footer

/image jako reply na zdjęcie + tekst:
- Pobierz zdjęcie z replied message
- Tekst po /image jako prompt
- j.w.

3. Stwórz handlers/file.py:

Auto-detect plików — MessageHandler na filters.Document.ALL:
- Pobierz dokument (update.message.document)
- Pobierz bytes
- Rozpoznaj typ (detect_file_type)
- Routing:
  * image → przekieruj do logiki image handler
  * pdf → extract_text_from_pdf → wyślij do Grok
  * docx → extract_text_from_docx → wyślij do Grok
  * zip → extract_text_from_zip → wyślij do Grok z listą plików
  * text (.txt, .md, .py, .js, .json, .csv, .xml, .html) → odczytaj jako tekst
  * unknown → "Nieobsługiwany format pliku"
- Smart truncation jeśli tekst > 100K znaków
- Caption jako prompt (lub domyślny: "Przeanalizuj ten plik")
- Streaming + footer

4. Zaktualizuj requirements.txt:
```
+ pdfplumber==0.11.0
+ python-docx==1.1.0
+ Pillow==10.4.0
```

5. Zaktualizuj main.py — dodaj handlery:
```python
# WAŻNE: Photo i Document handlery PRZED text handlerem
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
# ... potem text handler
```

6. Zaktualizuj /help.

Wygeneruj WSZYSTKIE nowe pliki i DOKŁADNE ZMIANY.

## PROMPT: FAZA 5

Realizujesz FAZĘ 5 projektu GigaGrok — Collections API + Export.

KONTEKST: Fazy 1-4 zrealizowane.

xAI COLLECTIONS API:
Collections to persistent knowledge stores w xAI. Uploadujesz dokumenty, Grok może po nich szukać.

Endpoints (base: https://api.x.ai/v1):
- POST /collections — body: {"name": "My Collection"} → {"id": "col_xxx"}
- GET /collections — lista kolekcji
- POST /collections/{id}/documents — body: multipart file upload
- GET /collections/{id}/documents — lista dokumentów
- DELETE /collections/{id} — usuń kolekcję

Tool do search:
```json
{
    "type": "function",
    "function": {
        "name": "collections_search",
        "parameters": {"collection_ids": ["col_xxx", "col_yyy"]}
    }
}
```

UWAGA: Sprawdź aktualną dokumentację xAI na docs.x.ai — API Collections mogło się zmienić.
Jeśli Collections API nie jest dostępne, zaimplementuj lokalne RAG z SQLite FTS5 (Full-Text Search) jako fallback.

ZADANIA:

1. Stwórz handlers/collection.py:

/collection — Pokaż menu/listę kolekcji:
```
📚 **Kolekcje** (2 kolekcje)

1. 📁 Dokumentacja projektu (col_abc) — 5 dokumentów
2. 📁 Notatki (col_def) — 12 dokumentów

Komendy:
/collection create <nazwa>
/collection add <id> (reply na plik)
/collection search <id> <query>
/collection list <id>
/collection delete <id>
```

/collection create <nazwa> — stwórz kolekcję
/collection add <id> — reply na plik, upload do kolekcji
/collection search <id> <query> — szukaj w kolekcji (użyj collections_search tool)
/collection list <id> — listuj dokumenty w kolekcji
/collection delete <id> — usuń kolekcję (z potwierdzeniem)

2. Stwórz handlers/export.py:

/export — Eksport historii:
- Domyślnie: Markdown format
- /export json — JSON format
- /export last 50 — ostatnie N wiadomości
- Wyślij jako plik w Telegramie (send_document)

Format Markdown:
```markdown
# GigaGrok — Historia konwersacji
Eksport: 2026-02-23 15:30
Wiadomości: 156

---

## 2026-02-23 14:00
**User:** Jak działa Grok 4.1 Fast?
**GigaGrok:** [odpowiedź...]
⚙️ grok-4-1-fast-reasoning | 📥 234 📤 567 | $0.0004

---
```

3. Zmodyfikuj tools.py:
- Dodaj TOOL_COLLECTION_SEARCH z dynamic collection_ids
- Funkcja get_collection_tool(collection_ids: list[str]) -> dict

4. Zmodyfikuj db.py:
- Tabela collections (id, xai_collection_id, name, doc_count, created_at)
- CRUD funkcje

5. Zmodyfikuj grok_client.py:
- Dodaj metody: create_collection(), upload_to_collection(), list_collections(), delete_collection()

6. Zaktualizuj main.py, /help.

Wygeneruj WSZYSTKIE nowe pliki i DOKŁADNE ZMIANY.

## PROMPT: FAZA 6

Realizujesz FAZĘ 6 projektu GigaGrok — Voice Chat (STT/TTS).

KONTEKST: Fazy 1-5 zrealizowane.

ZADANIA:

1. Stwórz handlers/voice.py:

STT (Speech-to-Text) — Whisper via Groq API (darmowy, ultra-szybki):
```python
# Groq Whisper API:
# POST https://api.groq.com/openai/v1/audio/transcriptions
# Headers: Authorization: Bearer {GROQ_API_KEY}
# Body: multipart/form-data — file (audio), model="whisper-large-v3"
# Response: {"text": "transkrypcja..."}
```

TTS (Text-to-Speech) — gTTS (darmowy, bez limitu):
```python
from gtts import gTTS
tts = gTTS(text="odpowiedź", lang="pl")
tts.save("response.mp3")
# Konwertuj do OGG/OPUS (Telegram wymaga):
# ffmpeg -i response.mp3 -c:a libopus response.ogg
```

Workflow — auto-detect voice message:
a. User wysyła voice → pobierz OGG z Telegram
b. Transkrybuj via Groq Whisper → tekst
c. Pokaż: "🎤 Transkrypcja: {tekst}" + "🧠 Grok myśli..."
d. Wyślij tekst do Grok (jak zwykły chat)
e. Pobierz odpowiedź
f. Jeśli voice_enabled: wygeneruj TTS → wyślij jako voice message + tekst
g. Jeśli !voice_enabled: wyślij tylko tekst

/voice — Toggle:
- Przełącz voice_enabled w user_settings
- "🔊 Odpowiedzi głosowe: WŁĄCZONE" / "🔇 Odpowiedzi głosowe: WYŁĄCZONE"

2. Zaktualizuj config.py:
- GROQ_API_KEY: str = "" (opcjonalne — jeśli brak, voice STT niedostępne)

3. Zaktualizuj .env.example:
- GROQ_API_KEY=your_groq_api_key (darmowy z console.groq.com)

4. Zaktualizuj requirements.txt:
```
+ gtts==2.5.0
+ pydub==0.25.1
```

5. README: dodaj info o ffmpeg (sudo apt install ffmpeg)

6. Zaktualizuj main.py:
```python
application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
application.add_handler(CommandHandler("voice", voice_toggle))
```

7. Zaktualizuj /help.

Wygeneruj WSZYSTKIE nowe pliki i DOKŁADNE ZMIANY.

## PROMPT: FAZA 7

Realizujesz FAZĘ 7 projektu GigaGrok — /gigagrok Full Power Mode.

KONTEKST: Fazy 1-6 zrealizowane. Bot ma: chat, /fast, /think, web search, X search, code exec, analyze, image, files, collections, voice.

KONCEPT: /gigagrok to "tryb boga" — Grok dostaje WSZYSTKIE narzędzia naraz, reasoning na max, i autonomicznie decyduje co użyć.

ZADANIA:

1. Stwórz handlers/gigagrok.py:

/gigagrok <prompt>:
```python
async def gigagrok_command(update, context):
    # Auth check
    prompt = " ".join(context.args) if context.args else None
    
    # Jeśli reply na wiadomość → użyj jako kontekst + prompt
    if update.message.reply_to_message:
        reply_text = update.message.reply_to_message.text or ""
        # Jeśli reply na zdjęcie → dodaj image analysis
        if update.message.reply_to_message.photo:
            # Pobierz i dodaj jako multimodal
            pass
        prompt = f"Kontekst:\n{reply_text}\n\nZapytanie:\n{prompt or 'Przeanalizuj powyższe.'}"
    
    if not prompt:
        await update.message.reply_text("Podaj prompt po /gigagrok")
        return
    
    # Pobierz collections (jeśli istnieją)
    collections = await db.get_collections(user_id)
    collection_ids = [c["xai_collection_id"] for c in collections]
    
    # Zbuduj tools — WSZYSTKO
    tools = list(TOOLS_ALL)  # web, x, code
    if collection_ids:
        tools.append(get_collection_tool(collection_ids))
    
    # System prompt — FULL POWER
    gigagrok_system = """Jesteś w trybie GIGAGROK — PEŁNA MOC.

Masz dostęp do narzędzi:
🌐 Web Search — szukaj aktualnych informacji w internecie
🐦 X Search — szukaj na X/Twitter
⚡ Code Execution — uruchamiaj kod w sandboxie
📚 Collections Search — szukaj w bazie wiedzy użytkownika

STRATEGIA:
1. MYŚL GŁĘBOKO — full reasoning, nie spiesz się
2. Jeśli potrzebujesz aktualnych danych → web_search
3. Jeśli potrzebujesz opinii/trendów → x_search
4. Jeśli potrzebujesz obliczeń/wizualizacji → code_execution
5. Jeśli temat dotyczy bazy wiedzy → collections_search
6. KOMBINUJ narzędzia w łańcuchy (np. web_search → code_execution do analizy)
7. Daj KOMPLETNĄ, wyczerpującą odpowiedź
8. Strukturyzuj: problem → analiza → wnioski → rekomendacje

Aktualna data: {current_date}
""" + ("\nUser ma kolekcje: " + ", ".join(c["name"] for c in collections) if collections else "")
    
    # Status updates mapping
    tool_status = {
        "web_search": "🌐 Szukam w internecie...",
        "x_search": "🐦 Sprawdzam X/Twitter...",
        "code_execution": "⚡ Uruchamiam kod...",
        "collections_search": "📚 Szukam w kolekcjach...",
    }
    
    # Stream z rozbudowanymi status updates
    sent = await update.message.reply_text("🚀 <b>GIGAGROK MODE</b>\n🧠 Reasoning...", parse_mode="HTML")
    
    # ... streaming logic z tool_use events pokazującymi statusy
    # Na koniec footer z:
    # 🚀 GIGAGROK | model | 📥 tokens 📤 tokens 🧠 reasoning | 🔧 web_search, code_execution | 💰 $cost | ⏱ time
```

2. Zmodyfikuj utils.py:
- format_gigagrok_footer() — rozbudowany footer z listą użytych narzędzi

3. Zaktualizuj main.py:
```python
application.add_handler(CommandHandler("gigagrok", gigagrok_command))
```

4. Zaktualizuj /help.

Wygeneruj KOMPLETNY handlers/gigagrok.py i DOKŁADNE ZMIANY w pozostałych plikach.

## PROMPT: FAZA 8

Realizujesz FAZĘ 8 projektu GigaGrok — GitHub Integration + Live Workspace.

KONTEKST: Fazy 1-7 zrealizowane.

ZADANIA:

1. Stwórz github_client.py:
```python
class GitHubClient:
    """Operacje Git na VM."""
    
    def __init__(self, workspace_dir: str = "/home/user/workspaces"):
        self.workspace_dir = Path(workspace_dir)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
    
    async def clone_or_pull(self, repo_url: str) -> Path:
        """Clone repo lub pull jeśli istnieje. Zwróć ścieżkę."""
    
    async def get_file_tree(self, repo_path: Path, max_depth: int = 3) -> str:
        """Zwróć drzewko plików."""
    
    async def read_file(self, repo_path: Path, file_path: str) -> str:
        """Odczytaj plik z repo."""
    
    async def write_file(self, repo_path: Path, file_path: str, content: str):
        """Zapisz plik do repo."""
    
    async def commit_and_push(self, repo_path: Path, message: str) -> str:
        """Commit all changes i push."""
    
    async def create_pr(self, repo_url: str, title: str, body: str, branch: str) -> str:
        """Stwórz PR via GitHub API. Zwróć URL."""
```

Wszystkie operacje git via subprocess.

2. Stwórz handlers/github.py:

/github <repo_url> <task> — Klonuj i analizuj:
- Clone/pull repo
- Pobierz file tree
- Wyślij do Grok z kontekstem: "Repo structure:\n{tree}\n\nTask: {task}"
- tools=[TOOL_CODE_EXEC] — Grok może uruchomić kod do analizy
- Streaming + footer

/github commit <message> — Commituj zmiany
/github pr <title> — Stwórz Pull Request

3. Stwórz handlers/workspace.py:

/workspace set <path> — Ustaw folder roboczy:
- Whitelist: /home/user/*, /opt/*
- Zapisz do user_settings
- Pokaż strukturę folderu

/workspace — Pokaż aktualny workspace
/workspace ls [subpath] — Listuj pliki
/workspace read <file> — Odczytaj plik, dodaj do następnego zapytania
/workspace write <file> — Grok wygenerował kod? Zapisz do pliku

4. Bezpieczeństwo:
- WORKSPACE_WHITELIST w config: lista dozwolonych bazowych ścieżek
- Walidacja path traversal (no .., no absolute paths outside whitelist)
- Max file size do odczytu: 1MB

5. Zaktualizuj config.py:
- GITHUB_TOKEN: str = ""
- WORKSPACE_BASE: str = "/home/user/workspaces"
- WORKSPACE_WHITELIST: list[str] = ["/home/user", "/opt"]

6. Zaktualizuj main.py, /help.

Wygeneruj WSZYSTKIE nowe pliki i DOKŁADNE ZMIANY.

## PROMPT: FAZA 9

Realizujesz FAZĘ 9 projektu GigaGrok — Production Deploy na GCE e2-micro.

KONTEKST: Fazy 1-8 zrealizowane. Bot działa na e2-standard-8 z kredytami do 13 marca.

ZADANIA — stwórz pliki deployment:

1. setup_vm.sh — Setup skrypt dla nowej e2-micro (us-central1):
```bash
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
```

2. gigagrok.service — systemd unit:
```ini
[Unit]
Description=GigaGrok Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=gigagrok
Group=gigagrok
WorkingDirectory=/opt/gigagrok
ExecStart=/opt/gigagrok/venv/bin/python main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/opt/gigagrok
PrivateTmp=true

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gigagrok

[Install]
WantedBy=multi-user.target
```

3. deploy.sh — Quick deploy (uruchamiaj z dev VM lub lokalnie):
```bash
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
```

4. healthcheck.py — Prosty HTTP healthcheck (port 8080):
```python
# Uruchamia się w osobnym wątku w main.py
# GET /health → {"status":"ok","uptime":"2d 5h","last_message":"2m ago","db_size":"2.3MB"}
# Monitoring: uptimerobot.com (darmowy, sprawdza co 5 min)
```

5. backup.sh — Backup DB:
```bash
#!/bin/bash
# Cron: 0 3 * * * /opt/gigagrok/backup.sh
TIMESTAMP=$(date +%Y%m%d_%H%M)
cp /opt/gigagrok/gigagrok.db /tmp/gigagrok_${TIMESTAMP}.db
gsutil cp /tmp/gigagrok_${TIMESTAMP}.db gs://gigagrok-backups/
rm /tmp/gigagrok_${TIMESTAMP}.db
# Zachowaj ostatnie 30 backupów
gsutil ls gs://gigagrok-backups/ | head -n -30 | xargs -r gsutil rm
```

6. Zaktualizuj main.py — dodaj healthcheck thread.

7. Zaktualizuj README.md — sekcja Production Deployment.

Wygeneruj WSZYSTKIE pliki deployment, kompletne, gotowe do użycia.

## PROMPT: END

Wszystkie fazy zrealizowane. Bot GigaGrok jest gotowy do produkcji.
