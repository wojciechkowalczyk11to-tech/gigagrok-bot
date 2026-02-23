# 🧠 GigaGrok — Ultimate Grok 4.1 Fast Reasoning Telegram Bot

## Master Build Plan v2.0

**Cel:** Zbudować od zera najinteligentniejszego możliwego asystenta AI na Telegramie, zasilanego Grok 4.1 Fast Reasoning z pełnym arsenałem narzędzi — prosto, modularnie, bez zagubienia się.

**Repo:** `gigagrok-bot` (nowe, czyste repo na GitHubie)
**Stack:** Python 3.12 + python-telegram-bot + httpx + SQLite (aiosqlite) + xAI API
**Domena:** `grok.nexus-oc.pl` (webhook via Cloudflare Tunnel)
**VM:** GCE e2-standard-8 (dev, do 13 marca) → e2-micro Always Free (prod)

---

## Infrastruktura

### VM Strategy

| Okres | VM | RAM | Koszt | Cel |
|---|---|---|---|---|
| Teraz → 13 marca | e2-standard-8 (obecna) | 32GB | kredyty GCP | Development, VS Code, testy |
| 13 marca → ∞ | e2-micro (us-central1) | 1GB | $0 Always Free | GigaGrok 24/7 produkcja |

**Nie zmieniaj VM teraz.** Masz kredyty — korzystaj z mocy. Bot na Pythonie + SQLite spokojnie stanie na e2-micro potem.

Migracja po 13 marca:
```bash
# Na nowej e2-micro:
git clone https://ghp_TOKEN@github.com/USER/gigagrok-bot.git
cd gigagrok-bot && pip install -r requirements.txt
cp .env.example .env  # uzupełnij klucze
sudo cp gigagrok.service /etc/systemd/system/
sudo systemctl enable --now gigagrok
# Przenieś cloudflared tunnel config na nową VM
```

### Domena & Cloudflare

```
Telegram API → POST https://grok.nexus-oc.pl/webhook
    → Cloudflare (SSL, DDoS protection)
    → Cloudflare Tunnel
    → VM localhost:8443
    → GigaGrok bot
```

Istniejące CNAME na nexus-oc.pl:
- `code.nexus-oc.pl` → VS Code Server
- `providers.nexus-oc.pl` → inna usługa
- `grok.nexus-oc.pl` → **NOWY** → GigaGrok webhook

### Git Setup na VM (jednorazowo)

```bash
git config --global user.name "TwojeImie"
git config --global user.email "twoj@email.com"
git config --global credential.helper store

# Clone z tokenem w URL = zero pytań o hasło
git clone https://ghp_TWOJ_TOKEN@github.com/TWOJ_USER/gigagrok-bot.git
cd gigagrok-bot
```

Token: github.com → Settings → Developer settings → Personal access tokens → Tokens (classic) → scope: repo

### Cloudflare Tunnel Setup

Jeśli masz ISTNIEJĄCY tunnel (dla code.nexus-oc.pl), dodaj hostname do niego:

```bash
# Sprawdź istniejące tunnele:
cloudflared tunnel list

# Dodaj DNS route:
cloudflared tunnel route dns <EXISTING_TUNNEL_NAME> grok.nexus-oc.pl

# Edytuj config — dodaj hostname:
nano ~/.cloudflared/config.yml
```

Dodaj do ingress w config.yml:
```yaml
ingress:
  - hostname: grok.nexus-oc.pl
    service: http://localhost:8443
  - hostname: code.nexus-oc.pl
    service: http://localhost:8080
  # ... inne istniejące
  - service: http_status:404
```

```bash
# Restart:
sudo systemctl restart cloudflared
```

Jeśli NIE masz cloudflared:
```bash
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared
cloudflared tunnel login
cloudflared tunnel create gigagrok
cloudflared tunnel route dns gigagrok grok.nexus-oc.pl
# Stwórz config.yml jak wyżej
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

---

## Architektura

| Nexus Omega (stary) | GigaGrok (nowy) |
|---|---|
| 133 plików, 5 kontenerów Docker | ~20 plików, 1 proces Python |
| PostgreSQL + Redis + Celery + FastAPI | SQLite + in-memory cache |
| 7 providerów AI z routingiem | 1 provider (Grok) z dual-mode |
| Enterprise RBAC, JWT, payments | Single-user, env-based auth |
| Skomplikowany deployment | `python main.py` i działa |

---

## Komendy bota (finalne)

| Komenda | Opis | Faza |
|---|---|---|
| `/start` | Powitanie + info o bocie | 1 |
| `/help` | Lista komend z opisami | 1 |
| (zwykła wiadomość) | Rozmowa z Grok reasoning | 1 |
| `/fast` | Odpowiedź BEZ reasoning (tanie, szybkie) | 2 |
| `/think` | Wymuszone deep reasoning | 2 |
| `/clear` | Wyczyść historię | 2 |
| `/stats` | Statystyki: tokeny, koszty | 2 |
| `/system <prompt>` | Custom system prompt | 2 |
| `/websearch <query>` | Web search (xAI Agent Tools) | 3 |
| `/xsearch <query>` | X/Twitter search | 3 |
| `/code <prompt>` | Generuj i uruchom kod | 3 |
| `/analyze` | Głęboka analiza z narzędziami | 3 |
| `/image` | Analiza obrazu (multimodal) | 4 |
| `/file` | Analiza pliku (PDF/DOCX/ZIP/TXT) | 4 |
| `/collection` | Zarządzanie kolekcjami xAI | 5 |
| `/export` | Eksport historii (.md/.json) | 5 |
| `/voice` | Toggle odpowiedzi głosowych | 6 |
| `/gigagrok <prompt>` | FULL POWER: wszystko naraz | 7 |
| `/github <repo> <task>` | Dostęp do repo GitHub | 8 |
| `/workspace` | Live folder access na VM | 8 |

---

## Fazy — przegląd

| Faza | Co | Czas |
|---|---|---|
| 0 | Infra: repo, git, tunnel, CNAME | 30 min (ręcznie) |
| 1 | MVP: chat + streaming + DB + webhook | 1 dzień |
| 2 | Tryby + settings + stats | 0.5 dnia |
| 3 | Agent Tools (web, X, code, analyze) | 1 dzień |
| 4 | Multimodal (obrazy, pliki) | 1 dzień |
| 5 | Collections + Export | 0.5 dnia |
| 6 | Voice (STT/TTS) | 0.5 dnia |
| 7 | /gigagrok Full Power | 0.5 dnia |
| 8 | GitHub + Workspace | 1 dzień |
| 9 | Production Deploy (e2-micro) | 0.5 dnia |

---

## Oszczędzanie tokenów (wbudowane od Fazy 1)

1. Smart history pruning: max 20 wiadomości, obcinaj jeśli > 50K tokenów
2. Dual-mode: /fast bez reasoning = ~3x tańsze
3. Response cache: identyczne zapytania w 5 min → cache
4. System prompt zwięzły, bez duplikacji
5. File truncation: > 100K znaków → smart truncate
6. Reasoning effort: none/low/medium/high per komendę
7. Usage alerts: ostrzeżenie przy $1, $5, $10 dziennie

---

## Koszty

### Grok 4.1 Fast API: $0.20/M input, $0.50/M output

| Scenariusz | Miesięcznie |
|---|---|
| Light (20 zapytań/dzień) | ~$0.90 |
| Medium (50 zapytań, reasoning) | ~$4.50 |
| Heavy (100 zapytań, /gigagrok) | ~$15 |

### Darmowe kredyty xAI
- $25 signup bonus
- $150/miesiąc data sharing (console.x.ai → opt-in)

### Hosting: $0
- e2-micro Always Free
- Cloudflare Tunnel: $0
- Domena: ~40 PLN/rok

---

## Jak używać z Claude Code

```bash
cd ~/gigagrok-bot

# Realizuj fazę (prompty są w PHASE_PROMPTS.md):
claude "$(cat PHASE_PROMPTS.md | sed -n '/^## PROMPT: FAZA 1$/,/^## PROMPT: FAZA 2$/p' | head -n -1)"

# Test:
python main.py

# Commit:
git add -A && git commit -m "Phase 1: MVP" && git push

# Następna faza:
claude "$(cat PHASE_PROMPTS.md | sed -n '/^## PROMPT: FAZA 2$/,/^## PROMPT: FAZA 3$/p' | head -n -1)"
```

---

*Plan v2.0 — Claude Opus 4.6 × GigaGrok. Domena: grok.nexus-oc.pl*
