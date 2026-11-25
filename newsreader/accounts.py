"""Request-scoped learning databases, sharing the existing secure account service."""
import hashlib
import sqlite3
import time
from pathlib import Path
from threading import RLock

from flask import Config, current_app, g, has_request_context, jsonify, redirect, request
from .db import close_db, get_db, init_db

COOKIE = "margin_learning_session"
SAFE_PATHS = ("/api/writing-workspace/session", "/api/platform/identity")


class ScopedConfig(Config):
    def __getitem__(self, key):
        if has_request_context():
            overrides = getattr(g, "learning_paths", {})
            if key in overrides:
                return overrides[key]
        return super().__getitem__(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def account():
    return getattr(g, "learning_account", None)


def account_id():
    value = account()
    return value["id"] if value else "guest"


def install(app):
    app.config = ScopedConfig(app.root_path, dict(app.config))
    root_database = Path(app.config["DATABASE"]).resolve()
    app.config["LOCAL_DATABASE"] = str(root_database)
    app.config["ACCOUNT_DATABASE"] = app.config["WRITING_DATABASE"]
    app.config["ACCOUNT_DATA_ROOT"] = str(root_database.parent / (root_database.stem + "-accounts"))
    app.extensions["account_managers"] = {}
    lock = RLock()

    @app.before_request
    def choose_learning_database():
        from .writing_workspace import db as account_db
        from .data_management import Manager, initialize_schema

        g.learning_account = None
        token = request.cookies.get(COOKIE, "")
        if token and len(token) <= 128:
            row = account_db().execute(
                """SELECT a.id,a.username,s.token_hash FROM accounts a
                JOIN sessions s ON s.account_id=a.id WHERE s.token_hash=? AND s.expires>?""",
                (hashlib.sha256(token.encode()).hexdigest(), int(time.time())),
            ).fetchone()
            if row:
                g.learning_account = dict(row)
        is_api = request.path.startswith("/api/")
        is_identity = request.path in SAFE_PATHS
        if request.path == '/writing' and app.config.get('LEARNING_SHARED') and not account():
            return redirect('/hub#accounts')
        if is_api and not is_identity:
            if token and not account():
                return jsonify(error="登录已失效，请重新登录。", code="SIGN_IN_REQUIRED"), 401
            if app.config.get("LEARNING_SHARED") and not account():
                return jsonify(error="请先登录学习账户。", code="SIGN_IN_REQUIRED"), 401
            expected = request.headers.get("X-Margin-Account")
            if request.blueprint != "workspace" and expected and expected != account_id():
                return jsonify(error="另一页面已切换账户，请刷新后继续。", code="ACCOUNT_CHANGED"), 409
            if request.method not in ("GET", "HEAD", "OPTIONS") and request.blueprint != "workspace" and account() and not expected:
                return jsonify(error="请刷新页面确认当前账户。", code="ACCOUNT_CHANGED"), 409
        if not account() or not is_api and request.path != "/writing":
            return
        identity = account()["id"]
        directory = Path(app.config["ACCOUNT_DATA_ROOT"]) / identity
        g.learning_paths = {
            "DATABASE": str(directory / "learning.sqlite3"),
            "DATA_STORAGE_DIR": str(directory / "learning-data"),
            "AI_SETTINGS_FILE": str(directory / "deepseek.json"),
            "TTS_CACHE_DIR": str(directory / "tts_cache"),
        }
        # Existing get_db users always resolve within this request. No process-global
        # DATABASE mutation occurs, so concurrent users cannot cross databases.
        with lock:
            control = app.extensions["account_managers"].get(identity)
            if control is None:
                directory.mkdir(parents=True, exist_ok=True)
                close_db()
                init_db()
                initialize_schema()
                control = Manager(app)
                app.extensions["account_managers"][identity] = control
            g.learning_manager = control

    @app.after_request
    def identity_header(response):
        if request.path.startswith("/api/"):
            response.headers["X-Margin-Account"] = account_id()
            response.vary.add('Cookie')
        return response
