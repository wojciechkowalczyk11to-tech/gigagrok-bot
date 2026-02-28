"""GigaGrok Bot configuration — Pydantic BaseSettings with .env support."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_SYSTEM_PROMPT: str = (
    "Jesteś GigaGrok — najinteligentniejszy asystent AI zasilany Grok 4.1 Fast Reasoning.\n"
    "\n"
    "Twoje cechy:\n"
    "- Myślisz głęboko przed odpowiedzią, ale pokazujesz tylko wynik i wnioski (bez ujawniania toku rozumowania).\n"
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

PERSONALITY_PROFILES: dict[str, str] = {
    "expert": (
        "Jesteś ekspertem technicznym. Odpowiadaj precyzyjnie, z detalami technicznymi. "
        "Używaj terminologii fachowej. Podawaj przykłady kodu gdy to stosowne."
    ),
    "simple": (
        "Odpowiadaj prosto i zrozumiale, jak dla osoby bez wiedzy technicznej. "
        "Unikaj żargonu. Używaj analogii i przykładów z życia codziennego."
    ),
    "creative": (
        "Bądź kreatywny i inspirujący. Myśl nieszablonowo. "
        "Proponuj innowacyjne rozwiązania. Używaj metafor i obrazowego języka."
    ),
    "concise": (
        "Odpowiadaj maksymalnie zwięźle. Bez wstępów, bez podsumowań. "
        "Tylko esencja odpowiedzi. Bullet pointy zamiast akapitów."
    ),
}


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
    webhook_url: str
    webhook_secret: str

    # --- Optional with defaults ---
    allowed_user_ids: str = ""
    admin_user_id: int = 0
    webhook_path: str = "webhook"
    webhook_port: int = 8443
    secondary_bot_token: str = ""
    secondary_webhook_path: str = "webhook2"
    secondary_webhook_port: int = 8444
    groq_api_key: str = ""
    xai_base_url: str = "https://api.x.ai/v1"
    xai_model_reasoning: str = "grok-4-1-fast-reasoning"
    xai_model_fast: str = "grok-4-1-fast"
    db_path: str = "gigagrok.db"
    max_history: int = 20
    max_output_tokens: int = 16000
    default_reasoning_effort: str = "high"
    log_level: str = "INFO"
    github_token: str = ""
    workspace_base: str = "/opt/gigagrok/workspaces"
    workspace_whitelist: list[str] = ["/opt/gigagrok/workspaces"]
    enable_responses_tools: bool = False
    xai_collection_id: str = "collection_54242e4b-351f-4c70-89d3-833165fca772"
    gigagrok_stage2_enabled: bool = True
    gigagrok_verify_pass: bool = True
    gigagrok_max_output_tokens: int = 16000
    gigagrok_context_messages: int = 5

    @property
    def allowed_users(self) -> set[int]:
        """Zwróć set dozwolonych user IDs."""
        users: set[int] = set()
        if self.allowed_user_ids:
            for uid in self.allowed_user_ids.split(","):
                value = uid.strip()
                if value.isdigit():
                    users.add(int(value))
        if self.admin_user_id:
            users.add(self.admin_user_id)
        return users

    @property
    def admin_id(self) -> int:
        """Pierwszy ID z listy ALLOWED_USER_IDS to admin."""
        if self.allowed_user_ids:
            first = self.allowed_user_ids.split(",")[0].strip()
            if first.isdigit():
                return int(first)
        return self.admin_user_id

    def is_allowed(self, user_id: int) -> bool:
        """Zwróć True jeśli użytkownik ma dostęp do bota."""
        return user_id in self.allowed_users

    def is_admin(self, user_id: int) -> bool:
        """Zwróć True jeśli użytkownik jest adminem."""
        return user_id == self.admin_id


settings = Settings()  # type: ignore[call-arg]
