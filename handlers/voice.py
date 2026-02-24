"""Voice handlers: STT (Groq Whisper) + TTS (gTTS)."""

from __future__ import annotations

import tempfile
import time
from io import BytesIO
from pathlib import Path

import httpx
import structlog
from gtts import gTTS
from pydub import AudioSegment
from telegram import InputFile, Update
from telegram.ext import ContextTypes

from config import DEFAULT_SYSTEM_PROMPT, settings
from db import (
    calculate_cost,
    get_history,
    get_user_setting,
    save_message,
    set_user_setting,
    update_daily_stats,
)
from grok_client import GrokClient
from utils import check_access, escape_html, format_footer, get_current_date, split_message

logger = structlog.get_logger(__name__)

_GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_WHISPER_MODEL = "whisper-large-v3"


def _is_enabled(value: object) -> bool:
    """Return True when DB setting value means enabled."""
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "on", "yes"}


async def _transcribe_with_groq(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
) -> str:
    """Transcribe audio bytes using Groq Whisper API."""
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY nie jest ustawiony.")

    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
    files = {
        "file": (filename, file_bytes, mime_type or "application/octet-stream"),
    }
    data = {"model": _WHISPER_MODEL}

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        response = await client.post(_GROQ_STT_URL, headers=headers, files=files, data=data)
        response.raise_for_status()
        payload = response.json()
        text = payload.get("text", "")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Brak tekstu w odpowiedzi STT.")
        return text.strip()


def _text_to_ogg_opus(text: str) -> bytes:
    """Generate OGG/OPUS from text using gTTS + pydub."""
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp_dir:
        tmp = Path(tmp_dir)
        mp3_path = tmp / "response.mp3"
        ogg_path = tmp / "response.ogg"

        tts = gTTS(text=text, lang="pl")
        tts.save(str(mp3_path))

        audio = AudioSegment.from_mp3(mp3_path)
        audio.export(ogg_path, format="ogg", codec="libopus")
        return ogg_path.read_bytes()


async def voice_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /voice command: toggle voice responses for current user."""
    del context
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    user_id = update.effective_user.id
    current = await get_user_setting(user_id, "voice_enabled")
    enabled = _is_enabled(current)
    new_value = "0" if enabled else "1"
    await set_user_setting(user_id, "voice_enabled", new_value)

    if new_value == "1":
        await update.message.reply_text("🔊 Odpowiedzi głosowe: WŁĄCZONE")
    else:
        await update.message.reply_text("🔇 Odpowiedzi głosowe: WYŁĄCZONE")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice/audio message: STT -> Grok -> optional TTS."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    if not settings.groq_api_key:
        await update.message.reply_text(
            "❌ Transkrypcja głosu jest niedostępna (brak GROQ_API_KEY)."
        )
        return

    voice = update.message.voice
    audio = update.message.audio
    if voice:
        file_id = voice.file_id
        filename = "voice.ogg"
        mime_type = voice.mime_type or "audio/ogg"
    elif audio:
        file_id = audio.file_id
        filename = audio.file_name or "audio_file"
        mime_type = audio.mime_type or "audio/mpeg"
    else:
        return

    user_id = update.effective_user.id
    grok: GrokClient | None = context.application.bot_data.get("grok_client")
    if grok is None:
        await update.message.reply_text("❌ Klient Grok nie został zainicjalizowany.")
        return

    try:
        telegram_file = await context.bot.get_file(file_id)
        file_bytes = bytes(await telegram_file.download_as_bytearray())
    except Exception as exc:
        logger.error("voice_download_failed", user_id=user_id, error=str(exc))
        await update.message.reply_text("❌ Nie udało się pobrać wiadomości głosowej.")
        return

    try:
        transcript = await _transcribe_with_groq(file_bytes, filename, mime_type)
    except Exception as exc:
        logger.error("voice_transcription_failed", user_id=user_id, error=str(exc))
        await update.message.reply_text("❌ Nie udało się wykonać transkrypcji.")
        return

    status = await update.message.reply_text(
        f"🎤 <b>Transkrypcja:</b> {escape_html(transcript)}\n\n🧠 <i>Grok myśli...</i>",
        parse_mode="HTML",
    )

    history = await get_history(user_id, limit=settings.max_history)
    custom_prompt = await get_user_setting(user_id, "system_prompt")
    system_prompt = custom_prompt or DEFAULT_SYSTEM_PROMPT.format(
        current_date=get_current_date()
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": transcript})

    start_time = time.time()
    full_content = ""
    full_reasoning = ""
    usage: dict[str, int] = {}
    last_edit = 0.0

    try:
        async for event_type, data in grok.chat_stream(
            messages=messages,
            model=settings.xai_model_reasoning,
            max_tokens=settings.max_output_tokens,
            reasoning_effort=settings.default_reasoning_effort,
        ):
            if event_type == "reasoning":
                full_reasoning += str(data)
            elif event_type == "content":
                full_content += str(data)
                now = time.time()
                if now - last_edit > 1.5:
                    display = escape_html(full_content[:3800])
                    if len(full_content) > 3800:
                        display += "\n\n<i>... (kontynuacja)</i>"
                    try:
                        await status.edit_text(display, parse_mode="HTML")
                    except Exception:
                        pass
                    last_edit = now
            elif event_type == "done":
                usage = data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.error("voice_grok_failed", user_id=user_id, error=str(exc))
        await status.edit_text(f"❌ Błąd API: {escape_html(str(exc))}", parse_mode="HTML")
        return

    elapsed = time.time() - start_time
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)
    reasoning_tokens = usage.get("reasoning_tokens", 0)
    cost = calculate_cost(tokens_in, tokens_out, reasoning_tokens)
    footer = format_footer(
        settings.xai_model_reasoning,
        tokens_in,
        tokens_out,
        reasoning_tokens,
        cost,
        elapsed,
    )

    final_text = f"{escape_html(full_content)}\n\n<code>{escape_html(footer)}</code>"
    parts = split_message(final_text, max_length=4000)
    try:
        await status.edit_text(parts[0], parse_mode="HTML")
    except Exception:
        pass
    for part in parts[1:]:
        try:
            await update.message.reply_text(part, parse_mode="HTML")
        except Exception:
            logger.exception("voice_send_part_failed", user_id=user_id)

    voice_enabled = _is_enabled(await get_user_setting(user_id, "voice_enabled"))
    if voice_enabled and full_content.strip():
        try:
            voice_bytes = _text_to_ogg_opus(full_content)
            voice_buffer = BytesIO(voice_bytes)
            voice_buffer.name = "response.ogg"
            await update.message.reply_voice(voice=InputFile(voice_buffer))
        except Exception as exc:
            logger.error("voice_tts_failed", user_id=user_id, error=str(exc))
            await update.message.reply_text(
                "⚠️ Nie udało się wygenerować odpowiedzi głosowej (sprawdź ffmpeg)."
            )

    await save_message(user_id, "user", transcript)
    await save_message(
        user_id,
        "assistant",
        full_content,
        reasoning_content=full_reasoning,
        model=settings.xai_model_reasoning,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        reasoning_tokens=reasoning_tokens,
        cost_usd=cost,
    )
    await update_daily_stats(user_id, tokens_in, tokens_out, reasoning_tokens, cost)
