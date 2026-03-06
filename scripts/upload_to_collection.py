#!/usr/bin/env python3
"""Upload extracted developer files to xAI Grok API collection.

Reads files from the categorised directory tree produced by
``gdrive_to_collection.py`` and uploads them to an xAI collection
via the ``POST /documents`` endpoint.

Usage:
    # Upload all files from the export directory:
    python scripts/upload_to_collection.py --input-dir ./gdrive_export

    # Specify a collection ID (overrides .env):
    python scripts/upload_to_collection.py --collection-id col_abc123

    # Dry-run — just list what would be uploaded:
    python scripts/upload_to_collection.py --input-dir ./gdrive_export --dry-run

Environment variables:
    XAI_API_KEY         — required, xAI API key
    XAI_BASE_URL        — optional, defaults to https://api.x.ai/v1
    XAI_COLLECTION_ID   — collection ID to upload into
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError:
    sys.exit("Brak httpx.  Zainstaluj:  pip install httpx")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("upload_collection")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MAX_RETRIES: int = 3
_RETRY_DELAYS: tuple = (2.0, 5.0, 10.0)
_RATE_LIMIT_DELAY: float = 5.0
_UPLOAD_DELAY: float = 0.3  # seconds between uploads

# Extensions of text files we upload (source code only)
UPLOADABLE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".sql", ".prisma",
    ".cs", ".csx", ".java", ".kt", ".go", ".rs",
    ".c", ".cpp", ".cc", ".h", ".hpp",
    ".rb", ".php", ".swift", ".r", ".lua", ".pl", ".pm",
    ".ex", ".exs", ".scala", ".hs", ".dart",
    ".tf", ".tfvars", ".proto",
    ".md", ".rst", ".txt", ".adoc",
    ".xml", ".xsl", ".graphql", ".gql",
    ".svg",  # SVG is text-based
    ".env", ".conf", ".properties", ".editorconfig",
    ".mk", ".cmake",
}

# Max size for a single document upload (bytes)
_MAX_UPLOAD_SIZE: int = 512 * 1024  # 512 KB — xAI limit for text docs


# ---------------------------------------------------------------------------
# xAI Collection API client
# ---------------------------------------------------------------------------

class CollectionUploader:
    """Upload text files to an xAI collection."""

    def __init__(self, api_key: str, base_url: str, collection_id: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._collection_id = collection_id
        self._client = httpx.Client(
            timeout=httpx.Timeout(60.0),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def upload_document(self, name: str, content: str) -> Dict[str, Any]:
        """Upload a single text document to the collection.

        Uses the xAI documents API:
          POST /v1/collections/{collection_id}/documents
        """
        url = f"{self._base_url}/collections/{self._collection_id}/documents"
        payload = {
            "content": content,
            "title": name,
        }

        last_error: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._client.post(url, json=payload)

                if resp.status_code == 429:
                    log.warning(
                        "Rate limited (429) — czekam %.1fs (próba %d/%d)",
                        _RATE_LIMIT_DELAY, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(_RATE_LIMIT_DELAY)
                    continue

                resp.raise_for_status()
                return resp.json()

            except Exception as exc:
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[attempt]
                    log.warning(
                        "Błąd uploadu %s — retry za %.1fs (próba %d/%d): %s",
                        name, delay, attempt + 1, _MAX_RETRIES, exc,
                    )
                    time.sleep(delay)

        if last_error:
            raise last_error
        raise RuntimeError(f"Upload failed for {name} — all retries exhausted")

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def discover_files(input_dir: Path) -> List[Path]:
    """Find all uploadable text files in the export directory tree."""
    files: List[Path] = []

    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        # Skip manifest/summary
        if path.name in ("manifest.json", "summary.json"):
            continue
        # Check extension
        if path.suffix.lower() in UPLOADABLE_EXTENSIONS:
            files.append(path)
        # Also include files without extension that are small text files (e.g. Makefile, Dockerfile)
        elif not path.suffix:
            try:
                size = path.stat().st_size
                if size < _MAX_UPLOAD_SIZE:
                    # Quick binary check
                    sample = path.read_bytes()[:1024]
                    if b"\x00" not in sample:
                        files.append(path)
            except OSError:
                pass

    return files


# ---------------------------------------------------------------------------
# Main upload logic
# ---------------------------------------------------------------------------

def run_upload(
    uploader: CollectionUploader,
    input_dir: Path,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Upload all discovered files to the collection."""
    files = discover_files(input_dir)

    if not files:
        log.warning("Nie znaleziono plików do uploadu w %s", input_dir)
        return {"uploaded": 0, "skipped": 0, "errors": 0}

    log.info("Znaleziono %d plików do uploadu", len(files))

    uploaded = 0
    skipped = 0
    errors = 0
    results: List[Dict[str, Any]] = []

    for path in files:
        rel = path.relative_to(input_dir)
        size = path.stat().st_size

        if size > _MAX_UPLOAD_SIZE:
            log.info("Pominięto (za duży: %d KB): %s", size // 1024, rel)
            skipped += 1
            continue

        if size == 0:
            skipped += 1
            continue

        # Construct document title with category context
        title = str(rel)

        if dry_run:
            log.info("[DRY-RUN] Upload: %s (%d KB)", title, size // 1024)
            uploaded += 1
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("Nie mogę odczytać %s: %s", rel, exc)
            errors += 1
            continue

        try:
            result = uploader.upload_document(name=title, content=content)
            uploaded += 1
            results.append({"file": title, "doc_id": result.get("id", "?")})
            log.info("✓ Uploaded: %s → %s", title, result.get("id", "?"))
        except Exception as exc:
            log.warning("✗ Błąd uploadu %s: %s", title, exc)
            errors += 1

        time.sleep(_UPLOAD_DELAY)

    # Save upload results
    if results:
        results_path = input_dir / "upload_results.json"
        results_path.write_text(
            json.dumps(results, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info("Wyniki uploadu zapisane w: %s", results_path)

    summary = {"uploaded": uploaded, "skipped": skipped, "errors": errors}

    log.info("=" * 60)
    log.info("PODSUMOWANIE UPLOADU")
    log.info("  Uploaded:  %d", uploaded)
    log.info("  Pominięto: %d", skipped)
    log.info("  Błędy:     %d", errors)
    log.info("=" * 60)

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Upload plików developerskich do kolekcji xAI Grok API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--input-dir",
        default=os.environ.get("GDRIVE_OUTPUT_DIR", "./gdrive_export"),
        help="Katalog z wyeksportowanymi plikami (domyślnie: ./gdrive_export)",
    )
    parser.add_argument(
        "--collection-id",
        default=os.environ.get("XAI_COLLECTION_ID", ""),
        help="ID kolekcji xAI (domyślnie: env XAI_COLLECTION_ID)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("XAI_API_KEY", ""),
        help="xAI API key (domyślnie: env XAI_API_KEY)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1"),
        help="xAI API base URL (domyślnie: https://api.x.ai/v1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tylko wylistuj pliki bez uploadu",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Szczegółowe logowanie (DEBUG)",
    )

    return parser.parse_args()


def main() -> None:
    """Entry point."""
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    api_key = args.api_key
    if not api_key:
        sys.exit(
            "Brak XAI_API_KEY.\n"
            "Ustaw zmienną środowiskową XAI_API_KEY lub użyj --api-key."
        )

    collection_id = args.collection_id
    if not collection_id:
        sys.exit(
            "Brak XAI_COLLECTION_ID.\n"
            "Ustaw zmienną środowiskową XAI_COLLECTION_ID lub użyj --collection-id."
        )

    input_dir = Path(args.input_dir).resolve()
    if not input_dir.is_dir():
        sys.exit(f"Katalog nie istnieje: {input_dir}")

    log.info("Katalog źródłowy: %s", input_dir)
    log.info("Collection ID:    %s", collection_id)

    uploader = CollectionUploader(
        api_key=api_key,
        base_url=args.base_url,
        collection_id=collection_id,
    )

    try:
        summary = run_upload(uploader, input_dir, dry_run=args.dry_run)
    finally:
        uploader.close()

    if summary["errors"] > 0:
        log.warning("Zakończono z %d błędami.", summary["errors"])
        sys.exit(1)

    log.info("Upload zakończony pomyślnie!")


if __name__ == "__main__":
    main()
