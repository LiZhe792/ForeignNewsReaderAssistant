"""Account-scoped writing drafts shared by the local reader and writing-only server."""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, current_app, g, jsonify, render_template, request
from werkzeug.security import check_password_hash, generate_password_hash

workspace = Blueprint("workspace", __name__)
API = "/api/writing-workspace"
COOKIE = "margin_writing_session"
SESSION_SECONDS = 30 * 24 * 60 * 60
PASSWORD_METHOD = "scrypt"
# A dummy hash with the same cost as a real account, for unknown usernames.
DUMMY_HASH = "scrypt:32768:8:1$marginWritingSalt$" + "0" * 128
USERNAME = re.compile(r"[a-zA-Z0-9_.-]{3,32}", re.ASCII)
IDENTIFIER = re.compile(r"[0-9a-f]{32}", re.ASCII)
SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    expires INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_account ON sessions(account_id);
CREATE TABLE IF NOT EXISTS auth_attempts (
    client TEXT NOT NULL, window INTEGER NOT NULL, attempts INTEGER NOT NULL,
    PRIMARY KEY(client, window)
);
CREATE TABLE IF NOT EXISTS drafts (
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    id TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1), updated_at TEXT NOT NULL,
    PRIMARY KEY(account_id, id)
);
CREATE INDEX IF NOT EXISTS drafts_updated ON drafts(account_id, updated_at DESC, id);
CREATE TABLE IF NOT EXISTS deleted_drafts (
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    id TEXT NOT NULL, PRIMARY KEY(account_id, id)
);
"""


def db():
    if "writing_db" not in g:
        connection = sqlite3.connect(current_app.config["WRITING_DATABASE"], timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        g.writing_db = connection
    return g.writing_db


def close_db(_error):
    connection = g.pop("writing_db", None)
    if connection is not None:
        connection.close()


def install(app):
    path = Path(app.config["WRITING_DATABASE"])
    path.parent.mkdir(parents=True, exist_ok=True)
    app.teardown_appcontext(close_db)
    app.register_blueprint(workspace)
    with app.app_context():
        db().execute("PRAGMA journal_mode=WAL")
        db().executescript(SCHEMA)


def default_database(database):
    return str(Path(database).with_suffix(".writing.sqlite3"))


def error(code, status, **extra):
    return jsonify(code=code, **extra), status


def body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def has_surrogate(value):
    return any(0xD800 <= ord(char) <= 0xDFFF for char in value)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@workspace.before_request
def protect():
    if current_app.config.get("WRITING_SHARED") and not request.is_secure:
        return error("secure_required", 403)
    if request.method in {"POST", "PUT", "DELETE"}:
        origin = request.headers.get("Origin")
        if origin and origin != request.host_url.rstrip("/"):
            return error("invalid_origin", 403)
        if not request.is_json:
            return error("invalid_input", 415)

    token = request.cookies.get(COOKIE, "")
    g.writing_account = None
    if 0 < len(token) <= 128:
        g.writing_account = db().execute(
            """SELECT a.id, a.username, s.token_hash FROM accounts a
               JOIN sessions s ON s.account_id=a.id
               WHERE s.token_hash=? AND s.expires>?""",
            (digest(token), int(time.time())),
        ).fetchone()


@workspace.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Cookie"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    return response


def account_json(account):
    return {"id": account["id"], "username": account["username"]} if account else None


def allow_auth_attempt():
    """Reserve a slot before hashing, including across concurrent workers."""
    connection = db()
    window = int(time.time()) // 300
    client = request.remote_addr or "local"
    with connection:
        connection.execute("DELETE FROM auth_attempts WHERE window<?", (window,))
        result = connection.execute(
            """INSERT INTO auth_attempts(client, window, attempts) VALUES(?, ?, 1)
               ON CONFLICT(client, window) DO UPDATE SET attempts=attempts+1
               WHERE attempts<20""", (client, window)
        )
    return result.rowcount == 1


@workspace.get("/writing")
def writing_page():
    title = ""
    article_id = request.args.get("article", "")
    if not current_app.config.get("WRITING_ONLY") and re.fullmatch(r"\d{1,18}", article_id, re.ASCII):
        from newsreader.db import get_db
        article = get_db().execute("SELECT title FROM articles WHERE id=?", (int(article_id),)).fetchone()
        title = article["title"] if article else ""
    return render_template("writing_workspace.html", article_title=title,
                           writing_only=current_app.config.get("WRITING_ONLY", False))


@workspace.get(API + "/session")
def session_info():
    shared = current_app.config.get("WRITING_SHARED", False)
    signup_code = bool(current_app.config.get("WRITING_SIGNUP_CODE"))
    return jsonify(account=account_json(g.writing_account),
                   registration=not shared or signup_code, invite_required=signup_code)


@workspace.post(API + "/session")
def authenticate():
    data = body()
    username, password = data.get("username"), data.get("password")
    registering = data.get("register", False)
    if (
        not isinstance(username, str) or not USERNAME.fullmatch(username)
        or not isinstance(password, str) or not 8 <= len(password) <= 128
        or has_surrogate(password) or type(registering) is not bool
    ):
        return error("invalid_credentials_format", 400)
    if not allow_auth_attempt():
        return error("rate_limited", 429)

    connection = db()
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + SESSION_SECONDS
    if registering:
        invite_code = current_app.config.get("WRITING_SIGNUP_CODE", "")
        if current_app.config.get("WRITING_SHARED") and not invite_code:
            return error("registration_closed", 403)
        if invite_code:
            invite = data.get("invite")
            if (not isinstance(invite, str) or len(invite) > 128 or has_surrogate(invite)
                    or not hmac.compare_digest(invite.encode(), invite_code.encode())):
                return error("invalid_invite", 403)
        account = {"id": uuid4().hex, "username": username}
        password_hash = generate_password_hash(password, method=PASSWORD_METHOD)
    else:
        account = connection.execute(
            "SELECT * FROM accounts WHERE username=?", (username,)
        ).fetchone()
        if not check_password_hash(account["password_hash"] if account else DUMMY_HASH, password) or not account:
            return error("invalid_credentials", 401)

    try:
        with connection:
            if registering:
                connection.execute("INSERT INTO accounts VALUES (?, ?, ?)",
                                   (account["id"], username, password_hash))
            connection.execute("DELETE FROM sessions WHERE expires<=?", (int(time.time()),))
            if g.writing_account:
                connection.execute("DELETE FROM sessions WHERE token_hash=?", (g.writing_account["token_hash"],))
            connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (digest(token), account["id"], expires))
    except sqlite3.IntegrityError:
        return error("username_taken", 409)
    response = jsonify(account=account_json(account))
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                        samesite="Strict", secure=request.is_secure, path=API)
    return response


def authorized(require_account=False):
    account = g.writing_account
    if not account:
        return error("sign_in_required", 401)
    if require_account and request.headers.get("X-Margin-Writing-Account") != account["id"]:
        return error("account_changed", 409)
    return None


@workspace.delete(API + "/session")
def sign_out():
    if failure := authorized(require_account=True):
        return failure
    account = g.writing_account
    with db():
        if body().get("all_devices") is True:
            db().execute("DELETE FROM sessions WHERE account_id=?", (account["id"],))
        else:
            db().execute("DELETE FROM sessions WHERE token_hash=?", (account["token_hash"],))
    response = jsonify(ok=True)
    response.delete_cookie(COOKIE, path=API, secure=request.is_secure, httponly=True, samesite="Strict")
    return response


@workspace.get(API + "/drafts")
def drafts():
    if failure := authorized(require_account=True):
        return failure
    rows = db().execute(
        """SELECT id, title, version, updated_at FROM drafts
           WHERE account_id=? ORDER BY updated_at DESC, id""", (g.writing_account["id"],)
    )
    return jsonify(drafts=[dict(row) for row in rows])


@workspace.route(API + "/drafts/<draft_id>", methods=["GET", "PUT", "DELETE"])
def draft(draft_id):
    if failure := authorized(require_account=True):
        return failure
    if not IDENTIFIER.fullmatch(draft_id):
        return error("draft_missing", 404)
    key = (g.writing_account["id"], draft_id)
    connection = db()
    select = "SELECT id, title, content, version, updated_at FROM drafts WHERE account_id=? AND id=?"
    if request.method == "GET":
        row = connection.execute(select, key).fetchone()
        return jsonify(draft=dict(row)) if row else error("draft_missing", 404)

    data = body()
    version = data.get("version")
    title, content = data.get("title"), data.get("content")
    if type(version) is not int or version < 0:
        return error("invalid_input", 400)
    if request.method == "PUT" and (
        not isinstance(title, str) or not 1 <= len(title.strip()) <= 140
        or not isinstance(content, str) or len(content) > 20000
        or has_surrogate(title + content)
    ):
        return error("invalid_draft", 400)
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(select, key).fetchone()
        if request.method == "DELETE":
            if not row:
                return jsonify(ok=True)
            if row["version"] != version:
                return error("conflict", 409, draft=dict(row))
            connection.execute("INSERT INTO deleted_drafts(account_id, id) VALUES (?, ?)", key)
            connection.execute("DELETE FROM drafts WHERE account_id=? AND id=?", key)
            return jsonify(ok=True)
        title = title.strip()
        if not row and (version or connection.execute(
            "SELECT 1 FROM deleted_drafts WHERE account_id=? AND id=?", key
        ).fetchone()):
            return error("draft_missing", 404)
        # An acknowledged or retried identical save has no new revision.
        if row and row["title"] == title and row["content"] == content:
            return jsonify(draft=dict(row))
        if row and row["version"] != version:
            return error("conflict", 409, draft=dict(row))
        updated_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        if row:
            connection.execute(
                """UPDATE drafts SET title=?, content=?, version=version+1, updated_at=?
                   WHERE account_id=? AND id=?""", (title, content, updated_at, *key)
            )
        else:
            connection.execute("INSERT INTO drafts VALUES (?, ?, ?, ?, 1, ?)",
                               (*key, title, content, updated_at))
        return jsonify(draft=dict(connection.execute(select, key).fetchone()))
