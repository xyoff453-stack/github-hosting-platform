import requests
from typing import Dict, Optional

from config import Config


class GitHubOAuth:
    def __init__(self):
        self.client_id = Config.GITHUB_CLIENT_ID
        self.client_secret = Config.GITHUB_CLIENT_SECRET
        self.redirect_uri = Config.GITHUB_REDIRECT_URI

    def auth_url(self) -> str:
        return (
            "https://github.com/login/oauth/authorize"
            f"?client_id={self.client_id}"
            f"&redirect_uri={self.redirect_uri}"
            "&scope=repo,user"
        )

    def exchange_code(self, code: str) -> Optional[Dict]:
        try:
            response = requests.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                },
                headers={"Accept": "application/json"},
                timeout=20,
            )
            return response.json()
        except Exception:
            return None

    def get_user_info(self, token: str) -> Optional[Dict]:
        try:
            response = requests.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/json",
                },
                timeout=20,
            )
            return response.json()
        except Exception:
            return None
