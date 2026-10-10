import sqlite3
from typing import Any, Dict, List, Optional


class Database:
    def __init__(self, db_path: str = "platform.db"):
        self.db_path = db_path
        self.init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                github_id INTEGER UNIQUE,
                github_login TEXT UNIQUE,
                github_token TEXT,
                name TEXT,
                avatar TEXT,
                email TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS apps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                repo_url TEXT,
                repo_name TEXT,
                repo_owner TEXT,
                app_name TEXT,
                branch TEXT DEFAULT 'main',
                status TEXT DEFAULT 'stopped',
                port INTEGER,
                deploy_key TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id INTEGER,
                level TEXT,
                message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(app_id) REFERENCES apps(id)
            )
            """
        )

        conn.commit()
        conn.close()

    def add_or_update_user(self, github_id: int, github_login: str, github_token: str, name: str, avatar: str, email: str) -> int:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (github_id, github_login, github_token, name, avatar, email)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(github_id) DO UPDATE SET
                github_login = excluded.github_login,
                github_token = excluded.github_token,
                name = excluded.name,
                avatar = excluded.avatar,
                email = excluded.email
            """,
            (github_id, github_login, github_token, name, avatar, email),
        )
        conn.commit()
        user = cursor.execute("SELECT id FROM users WHERE github_id = ?", (github_id,)).fetchone()
        conn.close()
        return int(user["id"]) if user else 0

    def get_user_by_github_id(self, github_id: int) -> Optional[sqlite3.Row]:
        conn = self._connect()
        user = conn.execute("SELECT * FROM users WHERE github_id = ?", (github_id,)).fetchone()
        conn.close()
        return user

    def get_user_by_id(self, user_id: int) -> Optional[sqlite3.Row]:
        conn = self._connect()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        return user

    def add_app(self, user_id: int, repo_url: str, repo_name: str, repo_owner: str, app_name: str, branch: str, port: int, deploy_key: str) -> int:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO apps (user_id, repo_url, repo_name, repo_owner, app_name, branch, port, deploy_key, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued')
            """,
            (user_id, repo_url, repo_name, repo_owner, app_name, branch, port, deploy_key),
        )
        conn.commit()
        app_id = cursor.lastrowid
        conn.close()
        return int(app_id)

    def get_app(self, app_id: int) -> Optional[sqlite3.Row]:
        conn = self._connect()
        app = conn.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
        conn.close()
        return app

    def get_user_apps(self, user_id: int) -> List[sqlite3.Row]:
        conn = self._connect()
        apps = conn.execute("SELECT * FROM apps WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()
        conn.close()
        return apps

    def update_app_status(self, app_id: int, status: str) -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE apps SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, app_id),
        )
        conn.commit()
        conn.close()

    def add_log(self, app_id: int, level: str, message: str) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT INTO logs (app_id, level, message) VALUES (?, ?, ?)",
            (app_id, level, message),
        )
        conn.commit()
        conn.close()

    def get_logs(self, app_id: int, limit: int = 100) -> List[sqlite3.Row]:
        conn = self._connect()
        logs = conn.execute(
            "SELECT * FROM logs WHERE app_id = ? ORDER BY created_at DESC LIMIT ?",
            (app_id, limit),
        ).fetchall()
        conn.close()
        return logs

    def get_available_port(self, start: int = 9001, end: int = 9999) -> int:
        conn = self._connect()
        used = {row["port"] for row in conn.execute("SELECT port FROM apps WHERE port IS NOT NULL").fetchall()}
        conn.close()
        for port in range(start, end + 1):
            if port not in used:
                return port
        return start

    def get_next_port(self) -> int:
        return self.get_available_port()

    def close(self):
        pass
