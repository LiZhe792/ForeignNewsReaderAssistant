"""Small reading groups with explicit membership roles and assignment snapshots."""
import json
import secrets
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request
from .accounts import account
from .db import get_db, now
from .platform import body, text
from .writing_workspace import db as accounts_db

collaboration = Blueprint("reading_groups", __name__, url_prefix="/api/groups")
SCHEMA = """
CREATE TABLE IF NOT EXISTS reading_groups(
 id TEXT PRIMARY KEY,title TEXT NOT NULL,owner TEXT NOT NULL REFERENCES accounts(id),
 invite TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reading_members(
 group_id TEXT REFERENCES reading_groups(id) ON DELETE CASCADE,
 account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE,role TEXT NOT NULL,
 PRIMARY KEY(group_id,account_id));
CREATE TABLE IF NOT EXISTS shared_readings(
 id TEXT PRIMARY KEY,group_id TEXT NOT NULL REFERENCES reading_groups(id) ON DELETE CASCADE,
 creator TEXT NOT NULL REFERENCES accounts(id),kind TEXT NOT NULL,title TEXT NOT NULL,
 payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reading_submissions(
 reading_id TEXT REFERENCES shared_readings(id) ON DELETE CASCADE,
 account_id TEXT REFERENCES accounts(id),article_id INTEGER,
 answer TEXT NOT NULL DEFAULT '',feedback TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,
 submitted_at TEXT,PRIMARY KEY(reading_id,account_id));
"""


def install(app):
    app.register_blueprint(collaboration)
    with app.app_context():
        accounts_db().executescript(SCHEMA)


@collaboration.before_request
def authenticated():
    if not account():
        return jsonify(error="请登录后使用小组协作。"), 401


def member(group_id, roles=None):
    row = accounts_db().execute("SELECT * FROM reading_members WHERE group_id=? AND account_id=?",
                               (group_id, account()["id"])).fetchone()
    if not row:
        abort(404)
    if roles and row["role"] not in roles:
        abort(403)
    return row


def group_json(row):
    value = dict(row)
    own = member(row["id"])
    value["role"] = own["role"]
    if own["role"] not in ("owner", "teacher"):
        value.pop("invite", None)
    return value


@collaboration.route("", methods=["GET", "POST"])
def groups():
    db = accounts_db()
    if request.method == "GET":
        rows = db.execute("SELECT g.* FROM reading_groups g JOIN reading_members m ON m.group_id=g.id WHERE m.account_id=? ORDER BY g.created_at DESC", (account()["id"],)).fetchall()
        return jsonify(groups=[group_json(r) for r in rows])
    title = text(body().get("title"), 100)
    identity = uuid.uuid4().hex
    with db:
        db.execute("INSERT INTO reading_groups VALUES(?,?,?,?,?)", (identity, title, account()["id"], secrets.token_urlsafe(18), now()))
        db.execute("INSERT INTO reading_members VALUES(?,?,?)", (identity, account()["id"], "owner"))
    return jsonify(group=group_json(db.execute("SELECT * FROM reading_groups WHERE id=?", (identity,)).fetchone())), 201


@collaboration.post("/join")
def join():
    db = accounts_db()
    code = text(body().get("invite"), 100)
    group = db.execute("SELECT * FROM reading_groups WHERE invite=?", (code,)).fetchone()
    if not group:
        raise ValueError("邀请代码无效或已更新。")
    with db:
        db.execute("INSERT OR IGNORE INTO reading_members VALUES(?,?,?)", (group["id"], account()["id"], "reader"))
    return jsonify(group=group_json(group))


@collaboration.get("/<identity>")
def detail(identity):
    member(identity)
    db = accounts_db()
    group = db.execute("SELECT * FROM reading_groups WHERE id=?", (identity,)).fetchone()
    members = [dict(r) for r in db.execute("SELECT a.id,a.username,m.role FROM reading_members m JOIN accounts a ON a.id=m.account_id WHERE m.group_id=?", (identity,))]
    items = []
    for row in db.execute("SELECT * FROM shared_readings WHERE group_id=? ORDER BY created_at DESC", (identity,)):
        value = dict(row); value["payload"] = json.loads(value["payload"])
        items.append(value)
    return jsonify(group=group_json(group), members=members, readings=items)


@collaboration.patch("/<identity>/members")
def set_role(identity):
    member(identity, ("owner",))
    data = body(); target = data.get("account_id")
    if target == account()["id"]:
        raise ValueError("不能移除或修改小组所有者。")
    if data.get("role") not in ("reader", "editor", "teacher", "remove"):
        raise ValueError("请选择只读、编辑、教师或移除权限。")
    db = accounts_db()
    with db:
        if data["role"] == "remove":
            db.execute("DELETE FROM reading_members WHERE group_id=? AND account_id=?", (identity, target))
        else:
            db.execute("UPDATE reading_members SET role=? WHERE group_id=? AND account_id=?", (data["role"], identity, target))
    return jsonify(ok=True)


@collaboration.post("/<identity>/rotate-invite")
def rotate_invite(identity):
    member(identity, ("owner",))
    db = accounts_db()
    with db:
        db.execute("UPDATE reading_groups SET invite=? WHERE id=?", (secrets.token_urlsafe(18), identity))
    return jsonify(group=group_json(db.execute("SELECT * FROM reading_groups WHERE id=?", (identity,)).fetchone()))


@collaboration.post("/<identity>/readings")
def share(identity):
    data = body(); kind = data.get("kind", "collection")
    member(identity, ("owner", "teacher") if kind == "assignment" else ("owner", "teacher", "editor"))
    if kind not in ("collection", "assignment"):
        raise ValueError("分享类型无效。")
    ids = data.get("article_ids", [])
    if not isinstance(ids, list) or not 1 <= len(ids) <= 20 or any(type(i) is not int for i in ids):
        raise ValueError("请选择1–20篇文章。")
    if kind == 'assignment' and len(ids) != 1:
        raise ValueError('每项任务选择一篇文章；多篇连续任务可分别发布。')
    articles = []
    for article_id in dict.fromkeys(ids):
        row = get_db().execute("SELECT title,content,source,source_url,category FROM articles WHERE id=? AND status!='trashed'", (article_id,)).fetchone()
        if not row:
            raise ValueError("文章已删除或移入回收站。")
        articles.append(dict(row))
    payload = {"articles": articles, "instructions": text(data.get("instructions", ""), 3000, False),
               "due_date": text(data.get("due_date", ""), 10, False)}
    if payload["due_date"]:
        from datetime import date
        date.fromisoformat(payload["due_date"])
    packed = json.dumps(payload, ensure_ascii=False)
    if len(packed.encode()) > 2 * 1024 * 1024:
        raise ValueError("分享内容超过2MB，请减少文章数量。")
    db = accounts_db(); reading_id = uuid.uuid4().hex
    with db:
        db.execute("INSERT INTO shared_readings VALUES(?,?,?,?,?,?,1,?)", (reading_id, identity, account()["id"], kind, text(data.get("title"), 150), packed, now()))
    return jsonify(id=reading_id), 201


def reading(identity):
    row = accounts_db().execute("SELECT * FROM shared_readings WHERE id=?", (identity,)).fetchone()
    if not row:
        abort(404)
    member(row["group_id"])
    return row


@collaboration.delete("/readings/<identity>")
def unshare(identity):
    row = reading(identity)
    role = member(row["group_id"])["role"]
    if role not in ("owner", "teacher") and row["creator"] != account()["id"]:
        abort(403)
    db = accounts_db()
    with db:
        db.execute("DELETE FROM shared_readings WHERE id=?", (identity,))
    return jsonify(ok=True)


@collaboration.patch('/readings/<identity>')
def edit_reading(identity):
    row=reading(identity)
    role=member(row['group_id'])['role']
    if role not in ('owner','teacher') and (row['kind']=='assignment' or role!='editor'):
        abort(403)
    data=body();payload=json.loads(row['payload'])
    title=text(data.get('title',row['title']),150)
    payload['instructions']=text(data.get('instructions',payload['instructions']),3000,False)
    deadline=text(data.get('due_date',payload['due_date']),10,False)
    if deadline:
        from datetime import date
        date.fromisoformat(deadline)
    payload['due_date']=deadline
    with accounts_db():
        cursor=accounts_db().execute('UPDATE shared_readings SET title=?,payload=?,version=version+1 WHERE id=? AND version=?',
                                     (title,json.dumps(payload,ensure_ascii=False),identity,data.get('version')))
    if not cursor.rowcount:return jsonify(error='分享已被其他成员更新，请重新加载后再编辑。'),409
    return jsonify(ok=True,version=row['version']+1)


@collaboration.post("/readings/<identity>/open")
def open_reading(identity):
    from .articles import insert_article
    from .library import find_duplicate
    row = reading(identity); payload = json.loads(row["payload"])
    db = get_db(); ids = []
    for article in payload["articles"]:
        existing = find_duplicate(db, article["title"], article["source_url"], article["content"])
        ids.append(existing or insert_article(db, **article))
    db.commit()
    central = accounts_db()
    with central:
        central.execute("INSERT OR IGNORE INTO reading_submissions(reading_id,account_id,article_id) VALUES(?,?,?)", (identity, account()["id"], ids[0]))
    return jsonify(article_ids=ids)


@collaboration.route("/readings/<identity>/submission", methods=["GET", "POST"])
def submission(identity):
    reading(identity); db = accounts_db()
    old = db.execute("SELECT * FROM reading_submissions WHERE reading_id=? AND account_id=?", (identity, account()["id"])).fetchone()
    if request.method == "GET":
        return jsonify(submission=dict(old) if old else None)
    if not old:
        raise ValueError("请先将任务文章打开到自己的学习库。")
    data = body()
    with db:
        changed = db.execute("UPDATE reading_submissions SET answer=?,submitted_at=?,version=version+1 WHERE reading_id=? AND account_id=? AND version=?",
                             (text(data.get("answer"), 10000), now(), identity, account()["id"], data.get("version")))
    if not changed.rowcount:
        return jsonify(error="另一设备已更新作答，请重新加载。"), 409
    return jsonify(ok=True, version=old["version"]+1)


@collaboration.get("/readings/<identity>/progress")
def student_progress(identity):
    row = reading(identity)
    member(row["group_id"], ("owner", "teacher"))
    central = accounts_db()
    rows = central.execute("""SELECT a.id,a.username,s.article_id,s.answer,s.feedback,s.version,s.submitted_at
         FROM reading_members m JOIN accounts a ON a.id=m.account_id
         LEFT JOIN reading_submissions s ON s.account_id=a.id AND s.reading_id=?
         WHERE m.group_id=?""", (identity, row["group_id"])).fetchall()
    result = []
    for row in rows:
        value = dict(row); value.update(progress=0, reading_seconds=0)
        path = Path(current_app.config["ACCOUNT_DATA_ROOT"]) / row["id"] / "learning.sqlite3"
        if row["article_id"] and path.is_file():
            with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)) as db:
                progress = db.execute("SELECT progress,reading_seconds FROM articles WHERE id=?", (row["article_id"],)).fetchone()
                if progress:
                    value["progress"], value["reading_seconds"] = progress
        result.append(value)
    return jsonify(students=result)


@collaboration.patch("/readings/<identity>/feedback")
def feedback(identity):
    row = reading(identity)
    member(row["group_id"], ("owner", "teacher"))
    data = body(); db = accounts_db()
    with db:
        changed = db.execute("UPDATE reading_submissions SET feedback=?,version=version+1 WHERE reading_id=? AND account_id=? AND version=?",
                             (text(data.get("feedback"), 4000), identity, data.get("account_id"), data.get("version")))
    if not changed.rowcount:
        return jsonify(error="作答已更新，请刷新后再反馈。"), 409
    return jsonify(ok=True)
