"""Utilities for image and file processing in multimodal handlers."""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

import pdfplumber
from docx import Document
from PIL import Image

_MAX_ZIP_TEXT_FILE_BYTES = 1 * 1024 * 1024  # 1MB
_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".py",
    ".js",
    ".json",
    ".csv",
    ".xml",
    ".html",
    ".yaml",
    ".yml",
    ".toml",
}


async def image_to_base64(file_bytes: bytes, max_size_mb: float = 5.0) -> tuple[str, str]:
    """Konwertuj obraz do base64 i zwróć ``(base64_str, mime_type)``.

    :param file_bytes: Surowe bajty obrazu.
    :param max_size_mb: Maksymalny rozmiar obrazu po kompresji.
    """
    max_bytes = int(max_size_mb * 1024 * 1024)

    image = Image.open(io.BytesIO(file_bytes))
    image.load()
    image_format = (image.format or "").upper()
    mime_type = "image/png" if image_format == "PNG" else "image/jpeg"

    if len(file_bytes) <= max_bytes and image_format in {"JPEG", "JPG", "PNG"}:
        return base64.b64encode(file_bytes).decode("utf-8"), mime_type

    processed = image.copy()
    best = b""
    quality = 90

    for _ in range(7):
        buffer = io.BytesIO()
        if mime_type == "image/png":
            processed.save(buffer, format="PNG", optimize=True)
        else:
            if processed.mode not in ("RGB", "L"):
                processed = processed.convert("RGB")
            processed.save(buffer, format="JPEG", optimize=True, quality=quality)
        candidate = buffer.getvalue()
        if not best or len(candidate) < len(best):
            best = candidate
        if len(candidate) <= max_bytes:
            return base64.b64encode(candidate).decode("utf-8"), mime_type

        width, height = processed.size
        if width < 32 or height < 32:
            break
        processed = processed.resize((int(width * 0.85), int(height * 0.85)))
        quality = max(55, quality - 8)

    if best and len(best) <= max_bytes:
        return base64.b64encode(best).decode("utf-8"), mime_type
    raise ValueError("Obraz jest zbyt duży (max 5MB po kompresji).")


async def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Wyciągnij tekst z PDF i zwróć połączoną treść stron.

    :param file_bytes: Surowe bajty pliku PDF.
    """
    chunks: list[str] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                chunks.append(text.strip())
    return "\n\n".join(chunks)


async def extract_text_from_docx(file_bytes: bytes) -> str:
    """Wyciągnij tekst z DOCX i zwróć połączoną treść akapitów.

    :param file_bytes: Surowe bajty pliku DOCX.
    """
    document = Document(io.BytesIO(file_bytes))
    lines = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text]
    return "\n".join(line for line in lines if line)


async def extract_text_from_zip(file_bytes: bytes) -> dict[str, str]:
    """Wypakuj ZIP i zwróć mapę ``{filename: content}`` dla plików tekstowych.

    :param file_bytes: Surowe bajty archiwum ZIP.
    """
    extracted: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            suffix = Path(member.filename).suffix.lower()
            if suffix not in _TEXT_EXTENSIONS:
                continue
            if member.file_size > _MAX_ZIP_TEXT_FILE_BYTES:
                continue
            data = archive.read(member)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = data.decode("cp1250")
                except UnicodeDecodeError:
                    text = data.decode("latin-1", errors="replace")
            if text.strip():
                extracted[member.filename] = text
    return extracted


def smart_truncate(text: str, max_chars: int = 100_000) -> str:
    """Skróć długi tekst zachowując 40% początku i 40% końca.

    :param text: Wejściowy tekst.
    :param max_chars: Maksymalna liczba znaków po skróceniu.
    """
    if len(text) <= max_chars:
        return text
    head = int(max_chars * 0.4)
    tail = int(max_chars * 0.4)
    removed = len(text) - head - tail
    return (
        f"{text[:head]}\n\n"
        f"... [OBCIĘTO {removed} znaków] ...\n\n"
        f"{text[-tail:]}"
    )


def detect_file_type(filename: str) -> str:
    """Zwróć kategorię pliku: image/pdf/docx/zip/text/unknown.

    :param filename: Nazwa pliku do sklasyfikowania.
    """
    suffix = Path(filename).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png"}:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    if suffix == ".zip":
        return "zip"
    if suffix in _TEXT_EXTENSIONS:
        return "text"
    return "unknown"
