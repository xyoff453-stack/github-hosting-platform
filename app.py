#!/usr/bin/env python3
"""
GitHub Hosting Platform - App único em Python
Hospeda apps do GitHub, integração com Git, painel local, banco de dados SQLite
Host 24/7 com suporte a múltiplas aplicações
"""

import os
import sys
import json
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import base64
import hashlib
import secrets

# ============================================================================
# CONFIGURAÇÕES
# ============================================================================

GITHUB_CLIENT_ID = os.getenv('GITHUB_CLIENT_ID', 'seu_client_id')
GITHUB_CLIENT_SECRET = os.getenv('GITHUB_CLIENT_SECRET', 'seu_client_secret')
GITHUB_REDIRECT_URI = os.getenv('GITHUB_REDIRECT_URI', 'http://localhost:8000/callback')
HOSTING_PORT = int(os.getenv('HOSTING_PORT', 9000))
CONTROL_PANEL_PORT = int(os.getenv('CONTROL_PANEL_PORT', 8000))
APPS_DIR = Path('hosted_apps')
DB_PATH = 'platform.db'

# ============================================================================
# BANCO DE DADOS
# ============================================================================

class Database:
    """Gerenciador de banco de dados SQLite"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()
    
    def get_connection(self):
        """Retorna conexão com banco de dados"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_db(self):
        """Inicializa tabelas do banco"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Tabela de usuários
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                github_id INTEGER UNIQUE,
                github_login TEXT UNIQUE,
                github_token TEXT,
                name TEXT,
                avatar TEXT,
                email TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Tabela de aplicações
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS apps (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                repo_url TEXT UNIQUE,
                repo_name TEXT,
                repo_owner TEXT,
                branch TEXT DEFAULT 'main',
                app_name TEXT,
                status TEXT DEFAULT 'stopped',
                port INTEGER UNIQUE,
                deploy_key TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        
        # Tabela de logs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY,
                app_id INTEGER NOT NULL,
                log_type TEXT,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (app_id) REFERENCES apps(id)
            )
        ''')
        
        # Tabela de deployments
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS deployments (
                id INTEGER PRIMARY KEY,
                app_id INTEGER NOT NULL,
                commit_hash TEXT,
                status TEXT DEFAULT 'pending',
                output TEXT,
                deployed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (app_id) REFERENCES apps(id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_user(self, github_id: int, github_login: str, github_token: str, 
                 name: str, avatar: str, email: str):
        """Adiciona ou atualiza usuário"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO users 
            (github_id, github_login, github_token, name, avatar, email)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (github_id, github_login, github_token, name, avatar, email))
        
        conn.commit()
        user_id = cursor.lastrowid
        conn.close()
        return user_id
    
    def get_user_by_github_id(self, github_id: int):
        """Busca usuário por GitHub ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE github_id = ?', (github_id,))
        user = cursor.fetchone()
        conn.close()
        return user
    
    def add_app(self, user_id: int, repo_url: str, repo_name: str, repo_owner: str,
                app_name: str, branch: str = 'main'):
        """Adiciona nova aplicação"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Gera porta disponível
        port = self._get_available_port(9100, 9999)
        deploy_key = secrets.token_urlsafe(32)
        
        cursor.execute('''
            INSERT INTO apps 
            (user_id, repo_url, repo_name, repo_owner, app_name, branch, port, deploy_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, repo_url, repo_name, repo_owner, app_name, branch, port, deploy_key))
        
        conn.commit()
        app_id = cursor.lastrowid
        conn.close()
        return app_id
    
    def get_app(self, app_id: int):
        """Busca app por ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM apps WHERE id = ?', (app_id,))
        app = cursor.fetchone()
        conn.close()
        return app
    
    def get_user_apps(self, user_id: int):
        """Lista apps do usuário"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM apps WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
        apps = cursor.fetchall()
        conn.close()
        return apps
    
    def update_app_status(self, app_id: int, status: str):
        """Atualiza status da app"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE apps SET status = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (status, app_id))
        conn.commit()
        conn.close()
    
    def add_log(self, app_id: int, log_type: str, message: str):
        """Adiciona log"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO logs (app_id, log_type, message)
            VALUES (?, ?, ?)
        ''', (app_id, log_type, message))
        conn.commit()
        conn.close()
    
    def get_app_logs(self, app_id: int, limit: int = 100):
        """Busca logs da app"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM logs WHERE app_id = ? 
            ORDER BY created_at DESC LIMIT ?
        ''', (app_id, limit))
        logs = cursor.fetchall()
        conn.close()
        return logs
    
    def _get_available_port(self, start: int, end: int) -> int:
        """Encontra uma porta disponível"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        for port in range(start, end):
            cursor.execute('SELECT id FROM apps WHERE port = ?', (port,))
            if not cursor.fetchone():
                conn.close()
                return port
        
        conn.close()
        return start

# ============================================================================
# GERENCIADOR DE APPS
# ============================================================================

class AppManager:
    """Gerencia deploy, execução e parada de apps"""
    
    def __init__(self, db: Database):
        self.db = db
        self.running_apps: Dict[int, subprocess.Popen] = {}
        self.apps_dir = APPS_DIR
        self.apps_dir.mkdir(exist_ok=True)
    
    def clone_repo(self, app_id: int, repo_url: str, branch: str) -> bool:
        """Clona repositório do GitHub"""
        try:
            app_path = self.apps_dir / f"app_{app_id}"
            
            self.db.add_log(app_id, 'info', f'Clonando repositório: {repo_url}')
            
            if app_path.exists():
                subprocess.run(['git', 'clone', repo_url, str(app_path)], 
                             check=True, capture_output=True)
            else:
                subprocess.run(['git', 'clone', '-b', branch, repo_url, str(app_path)],
                             check=True, capture_output=True)
            
            self.db.add_log(app_id, 'success', f'Repositório clonado em {app_path}')
            return True
            
        except subprocess.CalledProcessError as e:
            self.db.add_log(app_id, 'error', f'Erro ao clonar: {str(e)}')
            return False
    
    def pull_updates(self, app_id: int) -> bool:
        """Atualiza repositório via git pull"""
        try:
            app_path = self.apps_dir / f"app_{app_id}"
            
            self.db.add_log(app_id, 'info', 'Puxando atualizações...')
            
            result = subprocess.run(
                ['git', 'pull', 'origin', 'main'],
                cwd=str(app_path),
                capture_output=True,
                text=True,
                check=True
            )
            
            self.db.add_log(app_id, 'success', f'Atualizações puxadas\n{result.stdout}')
            return True
            
        except subprocess.CalledProcessError as e:
            self.db.add_log(app_id, 'error', f'Erro ao puxar atualizações: {e.stderr}')
            return False
    
    def install_dependencies(self, app_id: int) -> bool:
        """Instala dependências (pip, npm, etc)"""
        try:
            app_path = self.apps_dir / f"app_{app_id}"
            
            self.db.add_log(app_id, 'info', 'Instalando dependências...')
            
            # Verifica e instala dependências Python
            if (app_path / 'requirements.txt').exists():
                subprocess.run(
                    ['pip', 'install', '-r', 'requirements.txt'],
                    cwd=str(app_path),
                    check=True,
                    capture_output=True
                )
                self.db.add_log(app_id, 'success', 'Dependências Python instaladas')
            
            # Verifica e instala dependências Node.js
            if (app_path / 'package.json').exists():
                subprocess.run(
                    ['npm', 'install'],
                    cwd=str(app_path),
                    check=True,
                    capture_output=True
                )
                self.db.add_log(app_id, 'success', 'Dependências Node.js instaladas')
            
            return True
            
        except subprocess.CalledProcessError as e:
            self.db.add_log(app_id, 'error', f'Erro ao instalar dependências: {str(e)}')
            return False
    
    def start_app(self, app_id: int) -> bool:
        """Inicia aplicação"""
        try:
            if app_id in self.running_apps:
                self.db.add_log(app_id, 'warning', 'App já está em execução')
                return False
            
            app_info = self.db.get_app(app_id)
            if not app_info:
                return False
            
            app_path = self.apps_dir / f"app_{app_id}"
            port = app_info['port']
            
            self.db.add_log(app_id, 'info', f'Iniciando app na porta {port}...')
            self.db.update_app_status(app_id, 'starting')
            
            # Detecta tipo de app e comando para executar
            cmd = self._detect_start_command(app_path, port)
            
            if not cmd:
                self.db.add_log(app_id, 'error', 'Não foi possível detectar como executar a app')
                return False
            
            # Inicia processo
            process = subprocess.Popen(
                cmd,
                cwd=str(app_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            self.running_apps[app_id] = process
            self.db.update_app_status(app_id, 'running')
            self.db.add_log(app_id, 'success', f'App iniciada com PID {process.pid}')
            
            # Thread para capturar output
            threading.Thread(target=self._capture_output, args=(app_id, process), daemon=True).start()
            
            return True
            
        except Exception as e:
            self.db.add_log(app_id, 'error', f'Erro ao iniciar app: {str(e)}')
            self.db.update_app_status(app_id, 'error')
            return False
    
    def stop_app(self, app_id: int) -> bool:
        """Para aplicação"""
        try:
            if app_id not in self.running_apps:
                self.db.add_log(app_id, 'warning', 'App não está em execução')
                return False
            
            process = self.running_apps[app_id]
            process.terminate()
            
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            
            del self.running_apps[app_id]
            self.db.update_app_status(app_id, 'stopped')
            self.db.add_log(app_id, 'success', 'App parada')
            
            return True
            
        except Exception as e:
            self.db.add_log(app_id, 'error', f'Erro ao parar app: {str(e)}')
            return False
    
    def restart_app(self, app_id: int) -> bool:
        """Reinicia aplicação"""
        self.stop_app(app_id)
        time.sleep(1)
        return self.start_app(app_id)
    
    def _detect_start_command(self, app_path: Path, port: int) -> Optional[List[str]]:
        """Detecta comando para executar a app"""
        
        # Verifica Python
        if (app_path / 'main.py').exists():
            return ['python', 'main.py']
        if (app_path / 'app.py').exists():
            return ['python', 'app.py']
        if (app_path / 'server.py').exists():
            return ['python', 'server.py']
        
        # Verifica Node.js
        if (app_path / 'package.json').exists():
            return ['npm', 'start']
        
        # Verifica Procfile
        procfile = app_path / 'Procfile'
        if procfile.exists():
            with open(procfile) as f:
                content = f.read().strip()
                if content.startswith('web:'):
                    cmd = content.replace('web:', '').strip()
                    return cmd.split()
        
        return None
    
    def _capture_output(self, app_id: int, process: subprocess.Popen):
        """Captura output da app em tempo real"""
        try:
            while True:
                output = process.stdout.readline()
                if not output and process.poll() is not None:
                    break
                if output:
                    self.db.add_log(app_id, 'output', output.strip())
        except:
            pass
        finally:
            if app_id in self.running_apps:
                del self.running_apps[app_id]
            self.db.update_app_status(app_id, 'stopped')

# ============================================================================
# AUTENTICAÇÃO GITHUB
# ============================================================================

class GitHubAuth:
    """Gerencia autenticação com GitHub OAuth"""
    
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
    
    def get_auth_url(self) -> str:
        """Gera URL de autenticação GitHub"""
        return (
            f'https://github.com/login/oauth/authorize'
            f'?client_id={self.client_id}'
            f'&redirect_uri={self.redirect_uri}'
            f'&scope=repo,user'
        )
    
    def exchange_code(self, code: str) -> Optional[Dict]:
        """Troca código por token"""
        try:
            response = requests.post(
                'https://github.com/login/oauth/access_token',
                data={
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'code': code
                },
                headers={'Accept': 'application/json'}
            )
            return response.json()
        except:
            return None
    
    def get_user_info(self, token: str) -> Optional[Dict]:
        """Busca informações do usuário no GitHub"""
        try:
            response = requests.get(
                'https://api.github.com/user',
                headers={'Authorization': f'token {token}'}
            )
            return response.json()
        except:
            return None

# ============================================================================
# PAINEL DE CONTROLE (Interface Web)
# ============================================================================

class ControlPanelHandler(BaseHTTPRequestHandler):
    """Handler HTTP para o painel de controle"""
    
    db: Database = None
    app_manager: AppManager = None
    github_auth: GitHubAuth = None
    sessions: Dict[str, Dict] = {}
    
    def do_GET(self):
        """Processa requisições GET"""
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        
        # Callback OAuth GitHub
        if path == '/callback':
            code = query.get('code', [None])[0]
            if not code:
                self.send_error(400, 'Código não fornecido')
                return
            
            # Troca código por token
            token_data = self.github_auth.exchange_code(code)
            if not token_data or 'access_token' not in token_data:
                self.send_error(400, 'Falha na autenticação')
                return
            
            token = token_data['access_token']
            user_data = self.github_auth.get_user_info(token)
            
            if not user_data:
                self.send_error(400, 'Falha ao buscar dados do usuário')
                return
            
            # Salva usuário no banco
            user_id = self.db.add_user(
                github_id=user_data['id'],
                github_login=user_data['login'],
                github_token=token,
                name=user_data.get('name', user_data['login']),
                avatar=user_data.get('avatar_url', ''),
                email=user_data.get('email', '')
            )
            
            # Cria sessão
            session_id = secrets.token_urlsafe(32)
            self.sessions[session_id] = {
                'user_id': user_id,
                'github_login': user_data['login'],
                'created_at': datetime.now()
            }
            
            # Redireciona para dashboard
            self.send_response(302)
            self.send_header('Location', f'/dashboard?session={session_id}')
            self.send_header('Set-Cookie', f'session={session_id}; Path=/; HttpOnly')
            self.end_headers()
            return
        
        # Login
        if path == '/':
            self._send_html(self._html_login())
            return
        
        # Dashboard
        if path == '/dashboard':
            session_id = query.get('session', [None])[0]
            session = self.sessions.get(session_id)
            
            if not session:
                self.send_error(401, 'Não autenticado')
                return
            
            self._send_html(self._html_dashboard(session['user_id']))
            return
        
        # API - Listar apps
        if path == '/api/apps':
            session_id = self._get_session_id()
            session = self.sessions.get(session_id)
            
            if not session:
                self.send_error(401, 'Não autenticado')
                return
            
            apps = self.db.get_user_apps(session['user_id'])
            self._send_json([dict(app) for app in apps])
            return
        
        # API - Detalhes da app
        if path.startswith('/api/app/'):
            app_id = int(path.split('/')[-1])
            app = self.db.get_app(app_id)
            
            if not app:
                self.send_error(404, 'App não encontrada')
                return
            
            app_dict = dict(app)
            app_dict['logs'] = [dict(log) for log in self.db.get_app_logs(app_id)]
            self._send_json(app_dict)
            return
        
        self.send_error(404)
    
    def do_POST(self):
        """Processa requisições POST"""
        path = urlparse(self.path).path
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        
        try:
            data = json.loads(body) if body else {}
        except:
            data = {}
        
        session_id = self._get_session_id()
        session = self.sessions.get(session_id)
        
        if not session:
            self.send_error(401, 'Não autenticado')
            return
        
        user_id = session['user_id']
        
        # API - Criar nova app
        if path == '/api/add-app':
            repo_url = data.get('repo_url')
            app_name = data.get('app_name')
            branch = data.get('branch', 'main')
            
            if not repo_url or not app_name:
                self.send_error(400, 'Parâmetros inválidos')
                return
            
            # Extrai owner e repo do URL
            repo_name = repo_url.split('/')[-1].replace('.git', '')
            repo_owner = repo_url.split('/')[-2]
            
            try:
                app_id = self.db.add_app(user_id, repo_url, repo_name, repo_owner, app_name, branch)
                
                # Clone em thread separada
                threading.Thread(
                    target=self._deploy_app,
                    args=(app_id, repo_url, branch),
                    daemon=True
                ).start()
                
                self._send_json({'success': True, 'app_id': app_id})
            except Exception as e:
                self.send_error(500, str(e))
            
            return
        
        # API - Iniciar app
        if path == '/api/start-app':
            app_id = data.get('app_id')
            if self.app_manager.start_app(app_id):
                self._send_json({'success': True})
            else:
                self.send_error(500, 'Erro ao iniciar app')
            return
        
        # API - Parar app
        if path == '/api/stop-app':
            app_id = data.get('app_id')
            if self.app_manager.stop_app(app_id):
                self._send_json({'success': True})
            else:
                self.send_error(500, 'Erro ao parar app')
            return
        
        # API - Reiniciar app
        if path == '/api/restart-app':
            app_id = data.get('app_id')
            if self.app_manager.restart_app(app_id):
                self._send_json({'success': True})
            else:
                self.send_error(500, 'Erro ao reiniciar app')
            return
        
        # API - Atualizar app
        if path == '/api/update-app':
            app_id = data.get('app_id')
            if self.app_manager.pull_updates(app_id):
                self._send_json({'success': True})
            else:
                self.send_error(500, 'Erro ao atualizar app')
            return
        
        self.send_error(404)
    
    def _deploy_app(self, app_id: int, repo_url: str, branch: str):
        """Deploy de app em thread separada"""
        try:
            self.app_manager.clone_repo(app_id, repo_url, branch)
            self.app_manager.install_dependencies(app_id)
            self.app_manager.start_app(app_id)
        except Exception as e:
            self.db.add_log(app_id, 'error', f'Erro no deploy: {str(e)}')
            self.db.update_app_status(app_id, 'error')
    
    def _send_json(self, data: Dict):
        """Envia resposta JSON"""
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def _send_html(self, html: str):
        """Envia resposta HTML"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode())
    
    def _get_session_id(self) -> str:
        """Extrai session ID do cookie ou query"""
        cookies = self.headers.get('Cookie', '')
        for cookie in cookies.split(';'):
            if 'session=' in cookie:
                return cookie.split('session=')[1].strip()
        
        query = parse_qs(urlparse(self.path).query)
        return query.get('session', [None])[0]
    
    def log_message(self, format, *args):
        """Suprime logs padrão"""
        pass
    
    def _html_login(self) -> str:
        """HTML da página de login"""
        auth_url = self.github_auth.get_auth_url()
        return f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Hosting Platform</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .container {{
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            text-align: center;
            max-width: 400px;
            width: 100%;
        }}
        h1 {{ color: #333; margin-bottom: 10px; }}
        p {{ color: #666; margin-bottom: 30px; }}
        .btn {{
            display: inline-block;
            padding: 12px 30px;
            background: #333;
            color: white;
            text-decoration: none;
            border-radius: 5px;
            font-weight: bold;
            transition: background 0.3s;
        }}
        .btn:hover {{ background: #555; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 GitHub Hosting Platform</h1>
        <p>Hospede suas aplicações GitHub 24/7 com um painel de controle completo</p>
        <a href="{auth_url}" class="btn">Conectar com GitHub</a>
    </div>
</body>
</html>'''
    
    def _html_dashboard(self, user_id: int) -> str:
        """HTML do painel de controle"""
        return '''<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Painel de Controle</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto;
            background: #f5f5f5;
        }}
        header {{
            background: #333;
            color: white;
            padding: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
        h1 {{ font-size: 28px; }}
        .section {{ margin: 30px 0; }}
        .card {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            margin-bottom: 15px;
        }}
        .btn {{
            padding: 10px 20px;
            background: #667eea;
            color: white;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-weight: bold;
            transition: background 0.3s;
        }}
        .btn:hover {{ background: #764ba2; }}
        .btn-danger {{ background: #e74c3c; }}
        .btn-danger:hover {{ background: #c0392b; }}
        .btn-success {{ background: #27ae60; }}
        .btn-success:hover {{ background: #229954; }}
        .status {{
            display: inline-block;
            padding: 5px 10px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
        }}
        .status.running {{ background: #d4edda; color: #155724; }}
        .status.stopped {{ background: #f8d7da; color: #721c24; }}
        .status.starting {{ background: #fff3cd; color: #856404; }}
        .modal {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }}
        .modal.active {{ display: flex; }}
        .modal-content {{
            background: white;
            padding: 30px;
            border-radius: 8px;
            max-width: 500px;
            width: 90%;
        }}
        input, textarea {{
            width: 100%;
            padding: 10px;
            margin: 10px 0;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-family: inherit;
        }}
        .logs {{
            background: #1e1e1e;
            color: #0f0;
            padding: 15px;
            border-radius: 5px;
            max-height: 300px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            line-height: 1.4;
        }}
        .log-entry {{ margin: 3px 0; }}
        .log-info {{ color: #0f0; }}
        .log-error {{ color: #f00; }}
        .log-success {{ color: #0f0; }}
        .log-warning {{ color: #ff0; }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
        }}
    </style>
</head>
<body>
    <header>
        <div class="container">
            <h1>🚀 Painel de Controle - GitHub Hosting</h1>
        </div>
    </header>
    
    <div class="container">
        <div class="section">
            <h2>Suas Aplicações</h2>
            <button class="btn" onclick="showAddAppModal()">+ Adicionar Nova Aplicação</button>
            <div class="grid" id="apps-container" style="margin-top: 20px;">
                <p>Carregando...</p>
            </div>
        </div>
    </div>
    
    <div class="modal" id="addAppModal">
        <div class="modal-content">
            <h2>Adicionar Nova Aplicação</h2>
            <input type="text" id="repoUrl" placeholder="URL do repositório (https://github.com/usuario/repo)">
            <input type="text" id="appName" placeholder="Nome da aplicação">
            <input type="text" id="branch" placeholder="Branch (padrão: main)" value="main">
            <button class="btn" onclick="addApp()" style="width: 100%; margin-top: 10px;">Adicionar</button>
            <button class="btn" style="width: 100%; margin-top: 10px; background: #999;" onclick="closeAddAppModal()">Cancelar</button>
        </div>
    </div>
    
    <div class="modal" id="appDetailsModal">
        <div class="modal-content">
            <h2 id="appTitle"></h2>
            <p><strong>Status:</strong> <span id="appStatus" class="status"></span></p>
            <p><strong>Repositório:</strong> <span id="appRepo"></span></p>
            <p><strong>Porta:</strong> <span id="appPort"></span></p>
            <p><strong>Deploy Key:</strong> <code id="deployKey" style="background: #f0f0f0; padding: 5px; border-radius: 3px; font-size: 11px;"></code></p>
            
            <h3 style="margin-top: 20px;">Controles</h3>
            <button class="btn btn-success" onclick="startApp(currentAppId)" style="width: 100%; margin-top: 10px;">Iniciar</button>
            <button class="btn btn-danger" onclick="stopApp(currentAppId)" style="width: 100%; margin-top: 10px;">Parar</button>
            <button class="btn" onclick="restartApp(currentAppId)" style="width: 100%; margin-top: 10px;">Reiniciar</button>
            <button class="btn" onclick="updateApp(currentAppId)" style="width: 100%; margin-top: 10px;">Atualizar (git pull)</button>
            
            <h3 style="margin-top: 20px;">Logs</h3>
            <div class="logs" id="appLogs">Carregando logs...</div>
            
            <button class="btn" style="width: 100%; margin-top: 20px; background: #999;" onclick="closeAppDetailsModal()">Fechar</button>
        </div>
    </div>
    
    <script>
        let currentAppId = null;
        
        function showAddAppModal() {
            document.getElementById('addAppModal').classList.add('active');
        }
        
        function closeAddAppModal() {
            document.getElementById('addAppModal').classList.remove('active');
        }
        
        function closeAppDetailsModal() {
            document.getElementById('appDetailsModal').classList.remove('active');
        }
        
        async function loadApps() {
            try {
                const response = await fetch('/api/apps');
                const apps = await response.json();
                
                const container = document.getElementById('apps-container');
                if (apps.length === 0) {
                    container.innerHTML = '<p>Nenhuma aplicação ainda. Clique em "Adicionar Nova Aplicação" para começar.</p>';
                    return;
                }
                
                container.innerHTML = apps.map(app => `
                    <div class="card">
                        <h3>${app.app_name}</h3>
                        <p style="color: #666; font-size: 14px; margin: 5px 0;">${app.repo_url}</p>
                        <p style="margin: 10px 0;">
                            Status: <span class="status ${app.status}">${app.status}</span>
                        </p>
                        <p style="color: #999; font-size: 12px;">Porta: ${app.port}</p>
                        <button class="btn" onclick="showAppDetails(${app.id})" style="width: 100%; margin-top: 10px;">Ver Detalhes</button>
                    </div>
                `).join('');
            } catch (error) {
                console.error('Erro ao carregar apps:', error);
            }
        }
        
        async function showAppDetails(appId) {
            currentAppId = appId;
            try {
                const response = await fetch(`/api/app/${appId}`);
                const app = await response.json();
                
                document.getElementById('appTitle').textContent = app.app_name;
                document.getElementById('appStatus').textContent = app.status;
                document.getElementById('appStatus').className = `status ${app.status}`;
                document.getElementById('appRepo').textContent = app.repo_url;
                document.getElementById('appPort').textContent = `http://localhost:${app.port}`;
                document.getElementById('deployKey').textContent = app.deploy_key;
                
                const logsHtml = app.logs
                    .map(log => {
                        let className = 'log-' + log.log_type;
                        return `<div class="log-entry ${className}">[${log.log_type}] ${log.message}</div>`;
                    })
                    .join('');
                
                document.getElementById('appLogs').innerHTML = logsHtml || 'Sem logs';
                document.getElementById('appDetailsModal').classList.add('active');
            } catch (error) {
                alert('Erro ao carregar detalhes da app');
            }
        }
        
        async function addApp() {
            const repoUrl = document.getElementById('repoUrl').value;
            const appName = document.getElementById('appName').value;
            const branch = document.getElementById('branch').value;
            
            if (!repoUrl || !appName) {
                alert('Preencha todos os campos');
                return;
            }
            
            try {
                const response = await fetch('/api/add-app', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ repo_url: repoUrl, app_name: appName, branch })
                });
                
                if (response.ok) {
                    closeAddAppModal();
                    document.getElementById('repoUrl').value = '';
                    document.getElementById('appName').value = '';
                    document.getElementById('branch').value = 'main';
                    alert('Aplicação adicionada! O deploy está em andamento...');
                    setTimeout(loadApps, 2000);
                } else {
                    alert('Erro ao adicionar aplicação');
                }
            } catch (error) {
                alert('Erro: ' + error.message);
            }
        }
        
        async function startApp(appId) {
            try {
                const response = await fetch('/api/start-app', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ app_id: appId })
                });
                
                if (response.ok) {
                    alert('App iniciada!');
                    setTimeout(() => showAppDetails(appId), 1000);
                }
            } catch (error) {
                alert('Erro: ' + error.message);
            }
        }
        
        async function stopApp(appId) {
            try {
                const response = await fetch('/api/stop-app', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ app_id: appId })
                });
                
                if (response.ok) {
                    alert('App parada!');
                    setTimeout(() => showAppDetails(appId), 1000);
                }
            } catch (error) {
                alert('Erro: ' + error.message);
            }
        }
        
        async function restartApp(appId) {
            try {
                const response = await fetch('/api/restart-app', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ app_id: appId })
                });
                
                if (response.ok) {
                    alert('App reiniciada!');
                    setTimeout(() => showAppDetails(appId), 2000);
                }
            } catch (error) {
                alert('Erro: ' + error.message);
            }
        }
        
        async function updateApp(appId) {
            try {
                const response = await fetch('/api/update-app', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ app_id: appId })
                });
                
                if (response.ok) {
                    alert('App atualizada!');
                    setTimeout(() => showAppDetails(appId), 2000);
                }
            } catch (error) {
                alert('Erro: ' + error.message);
            }
        }
        
        // Carrega apps ao iniciar
        loadApps();
        
        // Recarrega apps a cada 5 segundos
        setInterval(loadApps, 5000);
    </script>
</body>
</html>'''

# ============================================================================
# SERVIDOR DE HOSTING (Proxy reverso)
# ============================================================================

class HostingProxyHandler(BaseHTTPRequestHandler):
    """Handler HTTP para proxy reverso das apps hospedadas"""
    
    app_manager: AppManager = None
    db: Database = None
    
    def do_GET(self):
        """Proxy GET requests"""
        self._proxy_request('GET')
    
    def do_POST(self):
        """Proxy POST requests"""
        self._proxy_request('POST')
    
    def do_PUT(self):
        """Proxy PUT requests"""
        self._proxy_request('PUT')
    
    def do_DELETE(self):
        """Proxy DELETE requests"""
        self._proxy_request('DELETE')
    
    def _proxy_request(self, method):
        """Faz proxy da requisição para a app hospedada"""
        try:
            # Extrai porta do header Host
            host_header = self.headers.get('Host', 'localhost')
            port_str = host_header.split(':')[1] if ':' in host_header else '9000'
            port = int(port_str)
            
            # Monta URL da app
            path = self.path
            url = f'http://localhost:{port}{path}'
            
            # Faz proxy da requisição
            headers = dict(self.headers)
            headers.pop('Host', None)
            
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            elif method == 'POST':
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                response = requests.post(url, headers=headers, data=body, timeout=10)
            elif method == 'PUT':
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                response = requests.put(url, headers=headers, data=body, timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=10)
            
            # Envia resposta
            self.send_response(response.status_code)
            for header, value in response.headers.items():
                if header.lower() not in ['content-encoding']:
                    self.send_header(header, value)
            self.end_headers()
            self.wfile.write(response.content)
            
        except requests.exceptions.ConnectionError:
            self.send_error(503, 'Aplicação indisponível')
        except Exception as e:
            self.send_error(500, str(e))
    
    def log_message(self, format, *args):
        """Suprime logs padrão"""
        pass

# ============================================================================
# INICIALIZAÇÃO
# ============================================================================

def main():
    """Função principal"""
    print('🚀 GitHub Hosting Platform - Iniciando...')
    
    # Inicializa banco de dados
    db = Database(DB_PATH)
    print(f'✓ Banco de dados inicializado: {DB_PATH}')
    
    # Inicializa gerenciador de apps
    app_manager = AppManager(db)
    print(f'✓ Gerenciador de apps inicializado')
    
    # Inicializa autenticação GitHub
    github_auth = GitHubAuth(GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_REDIRECT_URI)
    print(f'✓ Autenticação GitHub configurada')
    
    # Configura handlers HTTP
    ControlPanelHandler.db = db
    ControlPanelHandler.app_manager = app_manager
    ControlPanelHandler.github_auth = github_auth
    
    HostingProxyHandler.app_manager = app_manager
    HostingProxyHandler.db = db
    
    # Inicia servidor do painel de controle
    print(f'\n📊 Painel de Controle: http://localhost:{CONTROL_PANEL_PORT}')
    control_panel_server = HTTPServer(('0.0.0.0', CONTROL_PANEL_PORT), ControlPanelHandler)
    control_panel_thread = threading.Thread(target=control_panel_server.serve_forever, daemon=True)
    control_panel_thread.start()
    
    # Inicia servidor de hosting
    print(f'🌐 Servidor de Hosting: http://localhost:{HOSTING_PORT}')
    print(f'\n⚠️  IMPORTANTE:')
    print(f'   1. Configure GITHUB_CLIENT_ID e GITHUB_CLIENT_SECRET')
    print(f'   2. Acesse http://localhost:{CONTROL_PANEL_PORT}')
    print(f'   3. Conecte com GitHub e adicione seus repositórios')
    print(f'   4. As apps rodarão em http://localhost:PORT')
    print(f'\n💡 Dica: Use ngrok ou similar para expor na internet')
    print(f'   ngrok http {HOSTING_PORT}\n')
    
    hosting_server = HTTPServer(('0.0.0.0', HOSTING_PORT), HostingProxyHandler)
    
    try:
        hosting_server.serve_forever()
    except KeyboardInterrupt:
        print('\n\n🛑 Encerrando...')
        hosting_server.shutdown()
        control_panel_server.shutdown()
        sys.exit(0)

if __name__ == '__main__':
    main()
