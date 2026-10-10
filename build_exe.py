import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, jsonify, redirect, render_template_string, request

from config import Config
from database import Database
from deployer import AppRunner
from github_api import GitHubOAuth
from git_manager import GitManager

Config.ensure_dirs()

app = Flask(__name__)
app.config["SECRET_KEY"] = Config.SECRET_KEY

db = Database(Config.DB_PATH)
github_api = GitHubOAuth()
app_runner = AppRunner(db)
git_manager = GitManager(str(Config.APPS_DIR))

sessions: Dict[str, Dict[str, Any]] = {}


def get_session_user_id() -> Optional[int]:
    session_id = request.cookies.get("session")
    if not session_id or session_id not in sessions:
        return None
    return sessions[session_id].get("user_id")


@login_route = "/"


@app.route("/")
def index():
    if get_session_user_id() is not None:
        return redirect("/dashboard")
    auth_url = github_api.auth_url()
    return render_template_string(HTML_LOGIN, auth_url=auth_url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Código de autorização ausente.", 400

    token_data = github_api.exchange_code(code)
    if not token_data or "access_token" not in token_data:
        return "Falha ao autenticar no GitHub.", 400

    token = token_data["access_token"]
    user_data = github_api.get_user_info(token)
    if not user_data or "login" not in user_data:
        return "Não foi possível obter dados do usuário do GitHub.", 400

    user_id = db.add_or_update_user(
        github_id=int(user_data.get("id")),
        github_login=user_data.get("login"),
        github_token=token,
        name=user_data.get("name") or user_data.get("login"),
        avatar=user_data.get("avatar_url") or "",
        email=user_data.get("email") or "",
    )

    session_id = secrets.token_urlsafe(32)
    sessions[session_id] = {"user_id": user_id, "github_login": user_data.get("login")}

    response = redirect("/dashboard")
    response.set_cookie("session", session_id, max_age=60 * 60 * 24 * 7, httponly=True)
    return response


@app.route("/dashboard")
def dashboard():
    user_id = get_session_user_id()
    if user_id is None:
        return redirect("/")

    user = db.get_user_by_id(user_id)
    apps = db.get_user_apps(user_id)
    return render_template_string(HTML_DASHBOARD, user=user, apps=apps)


@app.route("/api/apps")
def api_apps():
    user_id = get_session_user_id()
    if user_id is None:
        return jsonify({"error": "Não autenticado"}), 401

    apps = db.get_user_apps(user_id)
    return jsonify([dict(app) for app in apps])


@app.route("/api/app/<int:app_id>")
def api_app_detail(app_id: int):
    user_id = get_session_user_id()
    if user_id is None:
        return jsonify({"error": "Não autenticado"}), 401

    app = db.get_app(app_id)
    if not app or int(app["user_id"]) != user_id:
        return jsonify({"error": "Aplicação não encontrada"}), 404

    logs = db.get_logs(app_id)
    payload = dict(app)
    payload["logs"] = [dict(l) for l in logs]
    return jsonify(payload)


@app.route("/api/add-app", methods=["POST"])
def add_app():
    user_id = get_session_user_id()
    if user_id is None:
        return jsonify({"error": "Não autenticado"}), 401

    data = request.get_json(silent=True) or {}
    repo_url = (data.get("repo_url") or "").strip()
    app_name = (data.get("app_name") or "").strip()
    branch = (data.get("branch") or "main").strip() or "main"

    if not repo_url or not app_name:
        return jsonify({"error": "repo_url e app_name são obrigatórios"}), 400

    repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    repo_owner = repo_url.rstrip("/").split("/")[-2]
    port = db.get_next_port()
    deploy_key = secrets.token_urlsafe(24)

    app_id = db.add_app(user_id, repo_url, repo_name, repo_owner, app_name, branch, port, deploy_key)
    db.add_log(app_id, "info", f"Nova aplicação criada: {app_name}")

    threading.Thread(target=deploy_application, args=(app_id, repo_url, branch), daemon=True).start()
    return jsonify({"success": True, "app_id": app_id})


@app.route("/api/start-app", methods=["POST"])
def start_app():
    user_id = get_session_user_id()
    if user_id is None:
        return jsonify({"error": "Não autenticado"}), 401

    data = request.get_json(silent=True) or {}
    app_id = int(data.get("app_id"))
    app = db.get_app(app_id)
    if not app or int(app["user_id"]) != user_id:
        return jsonify({"error": "App inválida"}), 404

    ok = app_runner.start_app(app_id)
    return jsonify({"success": ok})


@app.route("/api/stop-app", methods=["POST"])
def stop_app():
    user_id = get_session_user_id()
    if user_id is None:
        return jsonify({"error": "Não autenticado"}), 401

    data = request.get_json(silent=True) or {}
    app_id = int(data.get("app_id"))
    app = db.get_app(app_id)
    if not app or int(app["user_id"]) != user_id:
        return jsonify({"error": "App inválida"}), 404

    ok = app_runner.stop_app(app_id)
    return jsonify({"success": ok})


@app.route("/api/restart-app", methods=["POST"])
def restart_app():
    user_id = get_session_user_id()
    if user_id is None:
        return jsonify({"error": "Não autenticado"}), 401

    data = request.get_json(silent=True) or {}
    app_id = int(data.get("app_id"))
    app = db.get_app(app_id)
    if not app or int(app["user_id"]) != user_id:
        return jsonify({"error": "App inválida"}), 404

    ok = app_runner.restart_app(app_id)
    return jsonify({"success": ok})


@app.route("/api/sync-app", methods=["POST"])
def sync_app():
    user_id = get_session_user_id()
    if user_id is None:
        return jsonify({"error": "Não autenticado"}), 401

    data = request.get_json(silent=True) or {}
    app_id = int(data.get("app_id"))
    app = db.get_app(app_id)
    if not app or int(app["user_id"]) != user_id:
        return jsonify({"error": "App inválida"}), 404

    ok = app_runner.sync_existing_repo(app_id, app["branch"])
    return jsonify({"success": ok})


def deploy_application(app_id: int, repo_url: str, branch: str):
    app = db.get_app(app_id)
    if not app:
        return

    try:
        git_manager.clone_repo(app_id, repo_url, branch)
        app_dir = Path(Config.APPS_DIR) / f"app_{app_id}"
        app_runner.install_requirements(app_dir)
        db.add_log(app_id, "success", "Repositório clonado e dependências verificadas.")
        db.update_app_status(app_id, "ready")
    except Exception as exc:
        db.add_log(app_id, "error", f"Deploy falhou: {str(exc)}")
        db.update_app_status(app_id, "error")


HTML_LOGIN = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GitHub Host Manager</title>
  <style>
    body { font-family: Arial, sans-serif; background: linear-gradient(135deg, #0f172a, #111827); color: #fff; margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
    .card { background: rgba(15, 23, 42, 0.85); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 16px; padding: 40px; width: min(92vw, 450px); box-shadow: 0 30px 60px rgba(0,0,0,.35); }
    h1 { margin-top: 0; font-size: 2rem; }
    p { color: #cbd5e1; line-height: 1.6; }
    a.button { display: inline-block; background: linear-gradient(135deg, #2563eb, #7c3aed); color: white; text-decoration: none; font-weight: bold; padding: 14px 22px; border-radius: 10px; margin-top: 18px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>🚀 GitHub Host Manager</h1>
    <p> Conecte sua conta do GitHub para gerenciar apps, repositórios, deploy e controle local.</p>
    <a class="button" href="{{ auth_url }}">Conectar com GitHub</a>
  </div>
</body>
</html>
"""


HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Painel GitHub Host Manager</title>
  <style>
    body { font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; }
    .topbar { background: #111827; padding: 18px 22px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1f2937; }
    .container { padding: 24px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; }
    .card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 18px; }
    .status { padding: 5px 10px; border-radius: 999px; font-size: 12px; font-weight: bold; display: inline-block; }
    .running { background: #14532d; color: #bbf7d0; }
    .stopped { background: #7f1d1d; color: #fecaca; }
    .ready { background: #1d4ed8; color: #dbeafe; }
    .queued { background: #713f12; color: #fde68a; }
    .error { background: #7f1d1d; color: #fecaca; }
    .starting { background: #78350f; color: #fcd34d; }
    input, select, button { padding: 10px; border-radius: 8px; border: 1px solid #374151; background: #0f172a; color: #e2e8f0; }
    .btn { background: linear-gradient(135deg, #2563eb, #7c3aed); color: white; border: none; cursor: pointer; }
    .danger { background: #dc2626; }
    .success { background: #16a34a; }
    .form { margin-top: 20px; display: grid; grid-template-columns: 1.5fr 1fr 1fr auto; gap: 12px; }
    .logs { max-height: 140px; overflow-y: auto; background: #020817; color: #a7f3d0; padding: 12px; border-radius: 8px; font-size: 12px; }
    .muted { color: #94a3b8; }
  </style>
</head>
<body>
  <div class="topbar">
    <h2>GitHub Host Manager</h2>
    <div class="muted">{{ user["name"] or user["github_login"] }}</div>
  </div>

  <div class="container">
    <div class="card">
      <h3>Adicionar nova aplicação</h3>
      <div class="form">
        <input id="repoUrl" placeholder="https://github.com/usuario/repo.git" />
        <input id="appName" placeholder="Nome da app" />
        <input id="branch" value="main" placeholder="Branch" />
        <button class="btn" onclick="addApp()">Adicionar</button>
      </div>
    </div>

    <div style="margin-top: 24px;">
      <h3>Aplicações</h3>
      <div id="apps" class="grid"></div>
    </div>
  </div>

  <script>
    function renderApps(apps) {
      const container = document.getElementById('apps');
      if (!apps.length) {
        container.innerHTML = '<div class="card"><p>Nenhuma aplicação cadastrada.</p></div>';
        return;
      }

      container.innerHTML = apps.map(app => `
        <div class="card">
          <h3>${app.app_name}</h3>
          <p class="muted">${app.repo_url}</p>
          <p style="margin-top: 12px;">
            <span class="status ${app.status}">${app.status}</span>
          </p>
          <p style="margin-top: 8px;">Porta: ${app.port}</p>
          <p style="margin-top: 4px;">Branch: ${app.branch}</p>
          <div style="margin-top: 14px; display: flex; gap: 8px; flex-wrap: wrap;">
            <button class="btn" onclick="startApp(${app.id})">Iniciar</button>
            <button class="btn success" onclick="restartApp(${app.id})">Reiniciar</button>
            <button class="btn danger" onclick="stopApp(${app.id})">Parar</button>
            <button class="btn" onclick="syncRepo(${app.id})">Sync Git</button>
          </div>
          <div class="logs" style="margin-top: 12px;">
            Carregando logs...
          </div>
        </div>
      `).join('');
    }

    async function fetchApps() {
      const res = await fetch('/api/apps');
      const apps = await res.json();
      renderApps(apps);
      for (const app of apps) {
        const detail = await fetch(`/api/app/${app.id}`);
        const data = await detail.json();
        const card = document.querySelectorAll('.card')[Array.from(document.querySelectorAll('.card')).findIndex(c => c.textContent.includes(app.app_name))];
        if (card) {
          const logs = card.querySelector('.logs');
          logs.innerHTML = (data.logs || []).slice(0, 8).map(log => `<div>[${log.level}] ${log.message}</div>`).join('') || 'Sem logs';
        }
      }
    }

    async function addApp() {
      const repoUrl = document.getElementById('repoUrl').value;
      const appName = document.getElementById('appName').value;
      const branch = document.getElementById('branch').value;
      const res = await fetch('/api/add-app', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_url: repoUrl, app_name: appName, branch })
      });
      const data = await res.json();
      if (data.success) {
        document.getElementById('repoUrl').value = '';
        document.getElementById('appName').value = '';
        fetchApps();
      } else {
        alert(data.error || 'Erro ao adicionar app');
      }
    }

    async function startApp(appId) {
      await fetch('/api/start-app', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ app_id: appId }) });
      fetchApps();
    }

    async function stopApp(appId) {
      await fetch('/api/stop-app', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ app_id: appId }) });
      fetchApps();
    }

    async function restartApp(appId) {
      await fetch('/api/restart-app', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ app_id: appId }) });
      fetchApps();
    }

    async function syncRepo(appId) {
      await fetch('/api/sync-app', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ app_id: appId }) });
      fetchApps();
    }

    fetchApps();
    setInterval(fetchApps, 5000);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=Config.CONTROL_PANEL_PORT, debug=False)


# build_exe.py
# build_exe.bat
# README.md
# .gitignore
