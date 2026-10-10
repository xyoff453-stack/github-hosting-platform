import os
import secrets
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

class Config:
    GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "SEU_CLIENT_ID")
    GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "SEU_CLIENT_SECRET")
    GITHUB_REDIRECT_URI = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:8000/callback")
    BASE_URL = os.getenv("BASE_URL", "http://localhost")
    CONTROL_PANEL_PORT = int(os.getenv("CONTROL_PANEL_PORT", "8000"))
    HOSTING_PORT = int(os.getenv("HOSTING_PORT", "9000"))
    DB_PATH = os.getenv("DB_PATH", "platform.db")
    APPS_DIR = Path(os.getenv("APPS_DIR", "hosted_apps"))
    SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))

    @staticmethod
    def ensure_dirs() -> None:
        Config.APPS_DIR.mkdir(parents=True, exist_ok=True)
