import os
import shlex
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from database import Database


class AppRunner:
    def __init__(self, db: Database):
        self.db = db
        self.processes: Dict[int, subprocess.Popen] = {}

    def detect_start_command(self, app_dir: Path, port: int) -> Optional[List[str]]:
        if (app_dir / "app.py").exists():
            return ["python", "app.py"]
        if (app_dir / "main.py").exists():
            return ["python", "main.py"]
        if (app_dir / "server.py").exists():
            return ["python", "server.py"]
        if (app_dir / "package.json").exists():
            return ["npm", "start"]
        if (app_dir / "requirements.txt").exists():
            if (app_dir / "wsgi.py").exists():
                return ["gunicorn", "--bind", f"0.0.0.0:{port}", "wsgi:app"]
            return ["python", "-m", "http.server", str(port)]

        procfile = app_path / "Procfile"
        if procfile.exists():
            try:
                content = procfile.read_text(encoding="utf-8").strip()
                if content.startswith("web:"):
                    command = content.replace("web:", "", 1).strip()
                    return shlex.split(command)
            except Exception:
                pass

        return None

    def install_requirements(self, app_dir: Path) -> None:
        if (app_dir / "requirements.txt").exists():
            subprocess.run(["python", "-m", "pip", "install", "-r", "requirements.txt"], cwd=str(app_dir), check=False)
        if (app_dir / "package.json").exists():
            subprocess.run(["npm", "install"], cwd=str(app_dir), check=False)

    def start_app(self, app_id: int) -> bool:
        app = self.db.get_app(app_id)
        if not app:
            return False
        if app_id in self.processes:
            return True

        app_dir = Path("hosted_apps") / f"app_{app_id}"
        if not app_dir.exists():
            self.db.add_log(app_id, "error", "Diretório da aplicação não encontrado.")
            return False

        port = int(app["port"])
        command = self.detect_start_command(app_dir, port)
        if not command:
            self.db.add_log(app_id, "error", "Nenhum comando de inicialização detectado para a aplicação.")
            return False

        env = os.environ.copy()
        env["PORT"] = str(port)
        env["HOST"] = "0.0.0.0"

        self.db.update_app_status(app_id, "starting")
        self.db.add_log(app_id, "info", f"Iniciando app em http://localhost:{port}")
        try:
            process = subprocess.Popen(
                command,
                cwd=str(app_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self.processes[app_id] = process
            self.db.update_app_status(app_id, "running")
            self.db.add_log(app_id, "success", f"Processo iniciado com PID {process.pid}")
            return True
        except Exception as exc:
            self.db.update_app_status(app_id, "error")
            self.db.add_log(app_id, "error", f"Erro ao iniciar app: {str(exc)}")
            return False

    def stop_app(self, app_id: int) -> bool:
        process = self.processes.pop(app_id, None)
        if not process:
            self.db.update_app_status(app_id, "stopped")
            return False
        try:
            process.terminate()
            process.wait(timeout=5)
            self.db.update_app_status(app_id, "stopped")
            self.db.add_log(app_id, "success", "Aplicação parada com sucesso.")
            return True
        except Exception as exc:
            self.db.add_log(app_id, "error", f"Erro ao parar app: {str(exc)}")
            return False

    def restart_app(self, app_id: int) -> bool:
        self.stop_app(app_id)
        return self.start_app(app_id)

    def sync_and_deploy(self, app_id: int, repo_url: str, branch: str) -> bool:
        from git_manager import GitManager
        gm = GitManager()
        try:
            self.db.add_log(app_id, "info", f"Clonando repositório {repo_url}")
            gm.clone_repo(app_id, repo_url, branch)
            self.install_requirements(Path("hosted_apps") / f"app_{app_id}")
            self.db.add_log(app_id, "success", "Clone concluído.")
            self.db.update_app_status(app_id, "ready")
            return True
        except Exception as exc:
            self.db.add_log(app_id, "error", f"Erro ao clonar: {str(exc)}")
            self.db.update_app_status(app_id, "error")
            return False

    def sync_existing_repo(self, app_id: int, branch: str) -> bool:
        from git_manager import GitManager
        gm = GitManager()
        try:
            gm.pull_repo(app_id, branch)
            self.db.add_log(app_id, "info", f"Atualização aplicada na branch {branch}")
            return True
        except Exception as exc:
            self.db.add_log(app_id, "error", f"Erro ao atualizar repo: {str(exc)}")
            return False
