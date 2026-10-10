import subprocess
from pathlib import Path
from typing import Optional


class GitManager:
    def __init__(self, base_dir: str = "hosted_apps"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def repo_path(self, app_id: int) -> Path:
        return self.base_dir / f"app_{app_id}"

    def clone_repo(self, app_id: int, repo_url: str, branch: str = "main") -> bool:
        repo_dir = self.repo_path(app_id)
        if repo_dir.exists():
            return True

        try:
            subprocess.run(
                ["git", "clone", "--branch", branch, repo_url, str(repo_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            return True
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(exc.stderr or exc.stdout or str(exc))

    def pull_repo(self, app_id: int, branch: str = "main") -> bool:
        repo_dir = self.repo_path(app_id)
        if not repo_dir.exists():
            raise FileNotFoundError(f"Repositório da app {app_id} não existe")

        try:
            subprocess.run(
                ["git", "checkout", branch],
                cwd=str(repo_dir),
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "pull", "--ff-only", "origin", branch],
                cwd=str(repo_dir),
                check=True,
                capture_output=True,
                text=True,
            )
            return True
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(exc.stderr or exc.stdout or str(exc))

    def get_status(self, app_id: int) -> str:
        repo_dir = self.repo_path(app_id)
        if not repo_dir.exists():
            return "not_found"
        try:
            result = subprocess.run(["git", "status", "--short"], cwd=str(repo_dir), capture_output=True, text=True, check=True)
            return result.stdout.strip() or "clean"
        except Exception:
            return "error"
