"""GitHub/workspace operations executed with git subprocess commands."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

from config import settings

MAX_READ_SIZE_BYTES = 1_048_576  # 1MB


class GitHubClient:
    """Operacje Git na VM."""

    def __init__(self, workspace_dir: str = "/home/user/workspaces"):
        self.workspace_dir = Path(workspace_dir).expanduser().resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    async def clone_or_pull(self, repo_url: str) -> Path:
        """Clone repo lub pull jeśli istnieje. Zwróć ścieżkę."""
        repo_name = self._repo_name_from_url(repo_url)
        repo_path = (self.workspace_dir / repo_name).resolve()

        if (repo_path / ".git").exists():
            await self._run_git(["pull", "--ff-only"], cwd=repo_path)
            return repo_path

        await self._run_git(["clone", repo_url, str(repo_path)], cwd=self.workspace_dir)
        return repo_path

    async def get_file_tree(self, repo_path: Path, max_depth: int = 3) -> str:
        """Zwróć drzewko plików."""
        root = self._validate_workspace_root(repo_path)
        lines: list[str] = [f"{root.name}/"]

        for current_root, dirs, files in os.walk(root):
            current_path = Path(current_root)
            depth = len(current_path.relative_to(root).parts)
            if depth >= max_depth:
                dirs[:] = []
            dirs[:] = sorted([d for d in dirs if d != ".git"])
            files = sorted(files)

            indent = "  " * depth
            for directory in dirs:
                lines.append(f"{indent}- {directory}/")
            for filename in files:
                lines.append(f"{indent}- {filename}")

        return "\n".join(lines)

    async def read_file(self, repo_path: Path, file_path: str) -> str:
        """Odczytaj plik z repo."""
        full_path = self._resolve_repo_file(repo_path, file_path)
        if not full_path.is_file():
            raise FileNotFoundError("Podana ścieżka nie wskazuje na plik.")
        if full_path.stat().st_size > MAX_READ_SIZE_BYTES:
            raise ValueError("Plik jest za duży (limit: 1MB).")
        return full_path.read_text(encoding="utf-8")

    async def write_file(self, repo_path: Path, file_path: str, content: str) -> None:
        """Zapisz plik do repo."""
        full_path = self._resolve_repo_file(repo_path, file_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    async def commit_and_push(self, repo_path: Path, message: str) -> str:
        """Commit all changes i push."""
        root = self._validate_workspace_root(repo_path)
        await self._run_git(["add", "--all"], cwd=root)
        status = await self._run_git(["status", "--porcelain"], cwd=root)
        if not status.strip():
            return "Brak zmian do commitu."
        await self._run_git(["commit", "-m", message], cwd=root)
        push_out = await self._run_git(["push"], cwd=root)
        return push_out.strip() or "Push zakończony sukcesem."

    async def create_pr(self, repo_url: str, title: str, body: str, branch: str) -> str:
        """Stwórz PR via GitHub API. Zwróć URL."""
        token = settings.github_token.strip()
        if not token:
            raise ValueError("Brak GITHUB_TOKEN w konfiguracji.")

        owner, repo = self._owner_repo_from_url(repo_url)
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        payload = {"title": title, "body": body, "head": branch, "base": "main"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        pr_url = data.get("html_url")
        if not pr_url:
            raise RuntimeError("Nie udało się utworzyć PR.")
        return str(pr_url)

    async def _run_git(self, args: list[str], cwd: Path) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if process.returncode != 0:
            detail = err.strip() or out.strip() or "Nieznany błąd git."
            raise RuntimeError(detail)
        return out

    def _resolve_repo_file(self, repo_path: Path, file_path: str) -> Path:
        root = self._validate_workspace_root(repo_path)
        relative = Path(file_path)
        if relative.is_absolute():
            raise ValueError("Ścieżka pliku musi być relatywna.")
        if any(part == ".." for part in relative.parts):
            raise ValueError("Niedozwolona ścieżka (..).")

        resolved = (root / relative).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("Ścieżka wychodzi poza workspace.") from exc
        return resolved

    def _validate_workspace_root(self, repo_path: Path) -> Path:
        resolved = repo_path.expanduser().resolve()
        if not self._is_whitelisted(resolved):
            raise ValueError("Ścieżka workspace jest poza whitelistą.")
        return resolved

    def _is_whitelisted(self, path: Path) -> bool:
        for allowed_root in settings.workspace_whitelist:
            allowed = Path(allowed_root).expanduser().resolve()
            try:
                path.relative_to(allowed)
                return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _repo_name_from_url(repo_url: str) -> str:
        cleaned = repo_url.rstrip("/")
        tail = cleaned.split("/")[-1]
        return tail[:-4] if tail.endswith(".git") else tail

    @staticmethod
    def _owner_repo_from_url(repo_url: str) -> tuple[str, str]:
        if repo_url.startswith("git@"):
            _, rest = repo_url.split(":", 1)
            parts = [part for part in rest.split("/") if part]
            if len(parts) < 2:
                raise ValueError("Nieprawidłowy URL repozytorium.")
            owner, repo = parts[0], parts[1]
            return owner, repo.removesuffix(".git")

        parsed = urlparse(repo_url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise ValueError("Nieprawidłowy URL repozytorium.")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        return owner, repo
