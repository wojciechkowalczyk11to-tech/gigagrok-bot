"""GigaGrok Bot configuration — Pydantic BaseSettings with .env support."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_SYSTEM_PROMPT: str = (
    "Jesteś GigaGrok — najinteligentniejszy asystent AI zasilany Grok 4.1 Fast Reasoning.\n"
    "\n"
    "Twoje cechy:\n"
    "- Myślisz głęboko przed odpowiedzią (chain-of-thought reasoning)\n"
    "- Odpowiadasz konkretnie, bez zbędnego fluffu\n"
    "- Kod formatujesz w blokach z oznaczeniem języka\n"
    "- Jesteś ekspertem od programowania, analizy danych, strategii biznesowej\n"
    "- Mówisz po polsku gdy pytany po polsku, po angielsku gdy po angielsku\n"
    "- Jesteś szczery — mówisz \"nie wiem\" gdy nie wiesz\n"
    "- Przy złożonych problemach rozkładasz je na kroki\n"
    "\n"
    "Formatowanie:\n"
    "- Markdown\n"
    "- Kod w blokach ```język\n"
    "- Listy numerowane dla kroków\n"
    "- Pogrubienie dla kluczowych pojęć\n"
    "- Bądź zwięzły ale kompletny\n"
    "\n"
    "Aktualna data: {current_date}"
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # --- Required ---
    xai_api_key: str
    telegram_bot_token: str
    admin_user_id: int
    webhook_url: str
    webhook_secret: str

    # --- Optional with defaults ---
    webhook_path: str = "webhook"
    webhook_port: int = 8443
    xai_base_url: str = "https://api.x.ai/v1"
    xai_model_reasoning: str = "grok-4-1-fast-reasoning"
    xai_model_fast: str = "grok-4-1-fast"
    db_path: str = "gigagrok.db"
    max_history: int = 20
    max_output_tokens: int = 16000
    default_reasoning_effort: str = "high"
    log_level: str = "INFO"


settings = Settings()  # type: ignore[call-arg]
