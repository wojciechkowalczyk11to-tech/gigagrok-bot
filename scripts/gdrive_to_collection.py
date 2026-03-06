#!/usr/bin/env python3
"""Google Drive → local developer-files extractor.

Recursively scans a Google Drive, downloads only developer/code files,
organises them into categorised directories, and extracts pure source code
(e.g. code cells from Jupyter notebooks).

Usage:
    # First time — opens browser for OAuth consent:
    python scripts/gdrive_to_collection.py

    # With service-account JSON key:
    python scripts/gdrive_to_collection.py --service-account /path/to/key.json

    # Restrict to a specific Drive folder:
    python scripts/gdrive_to_collection.py --root-folder-id 1AbC...XyZ

    # Dry-run (list files without downloading):
    python scripts/gdrive_to_collection.py --dry-run

Environment variables (optional — can also use CLI flags):
    GDRIVE_ROOT_FOLDER_ID   — root folder ID to start scanning
    GDRIVE_OUTPUT_DIR        — output base directory (default: ./gdrive_export)
    GDRIVE_SERVICE_ACCOUNT   — path to service-account JSON key
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Google API imports
# ---------------------------------------------------------------------------
try:
    from google.oauth2 import service_account as sa_module
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
except ImportError:
    sys.exit(
        "Brak zależności Google API.  Zainstaluj:\n"
        "  pip install -r scripts/requirements-gdrive.txt"
    )

import io

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("gdrive_export")

# ---------------------------------------------------------------------------
# Constants — file categories and extensions
# ---------------------------------------------------------------------------

# Map: category_name -> set of lowercase extensions (with dot)
CATEGORY_EXTENSIONS: Dict[str, Set[str]] = {
    "python": {".py", ".pyw", ".pyi", ".pyx", ".pxd"},
    "javascript": {".js", ".jsx", ".mjs", ".cjs"},
    "typescript": {".ts", ".tsx", ".mts", ".cts"},
    "web": {".html", ".htm", ".css", ".scss", ".sass", ".less", ".svg"},
    "config": {
        ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
        ".env", ".env.example", ".env.local", ".properties",
        ".editorconfig", ".prettierrc", ".eslintrc",
    },
    "shell": {".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".bat", ".cmd"},
    "database": {".sql", ".sqlite", ".prisma"},
    "docker": set(),  # matched by filename below
    "notebooks": {".ipynb"},
    "csharp": {".cs", ".csx", ".csproj", ".sln"},
    "java": {".java", ".kt", ".kts", ".gradle", ".groovy"},
    "go": {".go", ".mod", ".sum"},
    "rust": {".rs", ".toml"},  # Cargo.toml caught by config too
    "cpp": {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx", ".hh"},
    "ruby": {".rb", ".rake", ".gemspec"},
    "php": {".php", ".phtml"},
    "swift": {".swift"},
    "r_lang": {".r", ".rmd"},
    "lua": {".lua"},
    "perl": {".pl", ".pm"},
    "elixir": {".ex", ".exs"},
    "scala": {".scala", ".sc"},
    "haskell": {".hs", ".lhs", ".cabal"},
    "dart": {".dart"},
    "terraform": {".tf", ".tfvars"},
    "proto": {".proto"},
    "markdown_docs": {".md", ".rst", ".adoc", ".txt"},
    "xml": {".xml", ".xsl", ".xslt", ".xsd", ".wsdl", ".pom"},
    "other_dev": {
        ".graphql", ".gql", ".cmake", ".makefile", ".mk",
        ".asm", ".s", ".v", ".vhdl", ".vhd",
    },
}

# Filenames (case-insensitive) that are always developer files
DEV_FILENAMES: Set[str] = {
    "dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "makefile", "cmakelists.txt", "rakefile", "gemfile",
    "pipfile", "setup.py", "setup.cfg", "pyproject.toml",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "tsconfig.json", "jsconfig.json", "webpack.config.js",
    "vite.config.ts", "vite.config.js",
    ".gitignore", ".gitattributes", ".dockerignore",
    ".babelrc", ".eslintrc.json", ".prettierrc.json",
    "requirements.txt", "go.mod", "go.sum", "cargo.toml", "cargo.lock",
    "build.gradle", "settings.gradle", "pom.xml",
    "vagrantfile", "procfile", "justfile",
}

# Extensions to skip unconditionally (binary / media / non-dev)
SKIP_EXTENSIONS: Set[str] = {
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".tif",
    ".psd", ".ai", ".eps", ".raw", ".cr2", ".nef",
    # Video
    ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v",
    # Audio
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a",
    # Archives (we don't extract archives — too risky)
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".xz",
    # Office / non-code docs
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".pdf",
    # Executables / compiled
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".lib",
    ".class", ".jar", ".war", ".ear", ".pyc", ".pyo",
    ".whl", ".egg",
    # Databases
    ".db", ".sqlite3", ".mdb", ".accdb",
    # Fonts
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    # Misc binary
    ".bin", ".dat", ".iso", ".dmg", ".deb", ".rpm",
    ".apk", ".ipa",
}

# Google Drive MIME types for Google Docs-format files we can export
GOOGLE_EXPORT_MIMES: Dict[str, Tuple[str, str]] = {
    # mime_type -> (export_mime, extension)
    "application/vnd.google-apps.script": ("application/vnd.google-apps.script+json", ".json"),
}

# Google Workspace files we skip (Sheets, Slides, Drawings, etc.)
GOOGLE_SKIP_MIMES: Set[str] = {
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.drawing",
    "application/vnd.google-apps.form",
    "application/vnd.google-apps.site",
    "application/vnd.google-apps.map",
    "application/vnd.google-apps.photo",
    "application/vnd.google-apps.video",
    "application/vnd.google-apps.audio",
    "application/vnd.google-apps.folder",
    "application/vnd.google-apps.shortcut",
    "application/vnd.google-apps.fusiontable",
}

# OAuth scopes
SCOPES: List[str] = ["https://www.googleapis.com/auth/drive.readonly"]

# Rate limiting
_PAGE_SIZE: int = 1000
_DOWNLOAD_DELAY: float = 0.05  # seconds between downloads to avoid quota hits


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def classify_file(name: str) -> Optional[str]:
    """Return the category for a filename, or None if it's not a dev file."""
    lower = name.lower()

    # Check known dev filenames first
    if lower in DEV_FILENAMES:
        # Dockerfile-like → docker, otherwise infer from extension
        if lower.startswith("docker"):
            return "docker"
        ext = os.path.splitext(lower)[1]
        for cat, exts in CATEGORY_EXTENSIONS.items():
            if ext in exts:
                return cat
        return "config"  # Most known filenames are config-like

    ext = os.path.splitext(lower)[1]
    if not ext:
        return None  # no extension, skip

    # Explicit skip
    if ext in SKIP_EXTENSIONS:
        return None

    # Find matching category
    for cat, exts in CATEGORY_EXTENSIONS.items():
        if ext in exts:
            return cat

    return None


def extract_notebook_code(content: bytes) -> str:
    """Extract source code cells from a Jupyter notebook JSON."""
    try:
        nb = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return content.decode("utf-8", errors="replace")

    cells = nb.get("cells", [])
    code_parts: List[str] = []
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", [])
        if isinstance(source, list):
            code_parts.append("".join(source))
        elif isinstance(source, str):
            code_parts.append(source)

    if not code_parts:
        return "# (notebook contained no code cells)\n"

    return "\n\n# --- cell ---\n\n".join(code_parts) + "\n"


def sanitize_path(name: str) -> str:
    """Remove or replace characters unsafe for filesystem paths."""
    # Replace path separators and null bytes
    name = name.replace("/", "_").replace("\\", "_").replace("\x00", "")
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    # Remove other problematic chars
    name = re.sub(r'[<>:"|?*]', "_", name)
    if not name:
        name = "_unnamed_"
    return name


# ---------------------------------------------------------------------------
# Google Drive auth
# ---------------------------------------------------------------------------

def authenticate_service_account(key_path: str) -> Any:
    """Authenticate via a service-account JSON key file."""
    creds = sa_module.Credentials.from_service_account_file(key_path, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


def authenticate_oauth(credentials_json: str = "credentials.json") -> Any:
    """Authenticate via OAuth2 (desktop app flow).

    On first run opens a browser window for consent.
    Stores the token in ``token.json`` for subsequent runs.
    """
    creds: Optional[Credentials] = None
    token_path = Path("token.json")

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path(credentials_json).exists():
                sys.exit(
                    f"Brak pliku {credentials_json}.\n"
                    "Pobierz go z Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs → Download JSON.\n"
                    "Albo użyj --service-account z kluczem service account."
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_json, SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Drive scanning
# ---------------------------------------------------------------------------

def list_all_files(
    service: Any,
    root_folder_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Recursively list all files in Drive (or under a specific folder).

    Returns a flat list of file metadata dicts with keys:
    ``id``, ``name``, ``mimeType``, ``size``, ``parents``, ``driveFolder``.
    """
    query_parts: List[str] = ["trashed = false"]
    if root_folder_id:
        query_parts.append(f"'{root_folder_id}' in parents")

    query = " and ".join(query_parts)
    fields = "nextPageToken, files(id, name, mimeType, size, parents)"

    all_files: List[Dict[str, Any]] = []
    page_token: Optional[str] = None

    log.info("Skanowanie Google Drive…  query=%s", query)

    while True:
        results = (
            service.files()
            .list(
                q=query,
                pageSize=_PAGE_SIZE,
                fields=fields,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )

        files = results.get("files", [])
        all_files.extend(files)

        page_token = results.get("nextPageToken")
        if not page_token:
            break

    # If root_folder_id given, recursively fetch subfolders too
    if root_folder_id:
        folders = [f for f in all_files if f.get("mimeType") == "application/vnd.google-apps.folder"]
        for folder in folders:
            sub_files = list_all_files(service, root_folder_id=folder["id"])
            all_files.extend(sub_files)

    log.info("Znaleziono %d plików/folderów", len(all_files))
    return all_files


def build_path_map(
    service: Any,
    files: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Build a map of file_id → relative Drive path for context."""
    id_to_name: Dict[str, str] = {}
    id_to_parent: Dict[str, Optional[str]] = {}

    for f in files:
        fid = f["id"]
        id_to_name[fid] = f["name"]
        parents = f.get("parents", [])
        id_to_parent[fid] = parents[0] if parents else None

    def resolve(fid: str, depth: int = 0) -> str:
        if depth > 30:
            return ""
        parent_id = id_to_parent.get(fid)
        name = id_to_name.get(fid, "?")
        if not parent_id or parent_id not in id_to_name:
            return name
        return resolve(parent_id, depth + 1) + "/" + name

    return {fid: resolve(fid) for fid in id_to_name}


# ---------------------------------------------------------------------------
# Download & extract
# ---------------------------------------------------------------------------

def download_file(service: Any, file_id: str, mime_type: str) -> bytes:
    """Download a file's binary content from Drive."""
    # Google Workspace files must be exported
    if mime_type in GOOGLE_EXPORT_MIMES:
        export_mime, _ = GOOGLE_EXPORT_MIMES[mime_type]
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
    elif mime_type.startswith("application/vnd.google-apps."):
        # Other Google types we can't download directly
        raise ValueError(f"Unsupported Google type: {mime_type}")
    else:
        request = service.files().get_media(fileId=file_id)

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    return buffer.getvalue()


def process_file(content: bytes, name: str, category: str) -> str:
    """Convert raw file bytes into clean source-code text."""
    if category == "notebooks" and name.lower().endswith(".ipynb"):
        return extract_notebook_code(content)

    # For all other code files, just decode as UTF-8
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="replace")

    return text


# ---------------------------------------------------------------------------
# Main export logic
# ---------------------------------------------------------------------------

def run_export(
    service: Any,
    output_dir: Path,
    root_folder_id: Optional[str] = None,
    dry_run: bool = False,
    max_file_size_mb: float = 10.0,
) -> Dict[str, Any]:
    """Download and categorise all developer files from Drive.

    Returns a summary dict with statistics.
    """
    files = list_all_files(service, root_folder_id)
    path_map = build_path_map(service, files)

    stats: Dict[str, int] = {}
    skipped_count = 0
    error_count = 0
    manifest: List[Dict[str, str]] = []

    max_bytes = int(max_file_size_mb * 1024 * 1024)

    for f in files:
        mime = f.get("mimeType", "")
        name = f.get("name", "")
        fid = f["id"]
        size = int(f.get("size", 0))

        # Skip folders
        if mime == "application/vnd.google-apps.folder":
            continue

        # Skip Google Workspace non-exportable types
        if mime in GOOGLE_SKIP_MIMES:
            skipped_count += 1
            continue

        # Classify
        category = classify_file(name)
        if category is None:
            # Check if it's a Google Apps Script (exportable)
            if mime in GOOGLE_EXPORT_MIMES:
                category = "config"
            else:
                skipped_count += 1
                continue

        # Size limit
        if size > max_bytes and size > 0:
            log.debug("Pominięto (za duży: %d MB): %s", size // (1024 * 1024), name)
            skipped_count += 1
            continue

        drive_path = path_map.get(fid, name)
        safe_name = sanitize_path(name)

        # Deduplicate filenames within a category
        cat_dir = output_dir / category
        dest = cat_dir / safe_name
        counter = 1
        while dest.exists():
            stem = Path(safe_name).stem
            suffix = Path(safe_name).suffix
            dest = cat_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        if dry_run:
            log.info("[DRY-RUN] %s → %s/%s", drive_path, category, dest.name)
            stats[category] = stats.get(category, 0) + 1
            manifest.append({
                "drive_path": drive_path,
                "category": category,
                "local_name": dest.name,
            })
            continue

        # Download
        try:
            raw = download_file(service, fid, mime)
        except Exception as exc:
            log.warning("Błąd pobierania %s: %s", drive_path, exc)
            error_count += 1
            continue

        # Process / extract code
        try:
            text = process_file(raw, name, category)
        except Exception as exc:
            log.warning("Błąd przetwarzania %s: %s", drive_path, exc)
            error_count += 1
            continue

        # Write
        cat_dir.mkdir(parents=True, exist_ok=True)

        # For notebooks, change extension to .py
        if category == "notebooks" and safe_name.lower().endswith(".ipynb"):
            dest = dest.with_suffix(".py")

        dest.write_text(text, encoding="utf-8")

        stats[category] = stats.get(category, 0) + 1
        manifest.append({
            "drive_path": drive_path,
            "category": category,
            "local_path": str(dest),
        })

        log.info("✓ %s → %s", drive_path, dest)

        time.sleep(_DOWNLOAD_DELAY)

    # Write manifest
    manifest_path = output_dir / "manifest.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {
        "total_downloaded": sum(stats.values()),
        "skipped": skipped_count,
        "errors": error_count,
        "by_category": stats,
        "manifest_path": str(manifest_path),
    }

    # Write summary
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    log.info("=" * 60)
    log.info("PODSUMOWANIE EKSPORTU")
    log.info("  Pobrano:    %d plików", summary["total_downloaded"])
    log.info("  Pominięto:  %d", summary["skipped"])
    log.info("  Błędy:      %d", summary["errors"])
    for cat, count in sorted(stats.items()):
        log.info("    %-20s %d", cat, count)
    log.info("  Manifest:   %s", manifest_path)
    log.info("=" * 60)

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Eksport plików developerskich z Google Drive do lokalnych katalogów.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--service-account",
        default=os.environ.get("GDRIVE_SERVICE_ACCOUNT", ""),
        help="Ścieżka do pliku JSON klucza service account (domyślnie: env GDRIVE_SERVICE_ACCOUNT)",
    )
    parser.add_argument(
        "--credentials",
        default="credentials.json",
        help="Ścieżka do pliku OAuth2 credentials.json (domyślnie: credentials.json)",
    )
    parser.add_argument(
        "--root-folder-id",
        default=os.environ.get("GDRIVE_ROOT_FOLDER_ID", ""),
        help="ID folderu root na Drive (domyślnie: cały Drive)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("GDRIVE_OUTPUT_DIR", "./gdrive_export"),
        help="Katalog wyjściowy (domyślnie: ./gdrive_export)",
    )
    parser.add_argument(
        "--max-file-size",
        type=float,
        default=10.0,
        help="Maks. rozmiar pliku w MB (domyślnie: 10)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tylko wylistuj pliki bez pobierania",
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

    # Authenticate
    if args.service_account:
        log.info("Uwierzytelnianie przez service account: %s", args.service_account)
        service = authenticate_service_account(args.service_account)
    else:
        log.info("Uwierzytelnianie przez OAuth2 (desktop flow)…")
        service = authenticate_oauth(args.credentials)

    output_dir = Path(args.output_dir).resolve()
    log.info("Katalog wyjściowy: %s", output_dir)

    root_id = args.root_folder_id or None

    summary = run_export(
        service=service,
        output_dir=output_dir,
        root_folder_id=root_id,
        dry_run=args.dry_run,
        max_file_size_mb=args.max_file_size,
    )

    if summary["errors"] > 0:
        log.warning("Zakończono z %d błędami — sprawdź logi powyżej.", summary["errors"])
        sys.exit(1)

    log.info("Eksport zakończony pomyślnie!")


if __name__ == "__main__":
    main()
