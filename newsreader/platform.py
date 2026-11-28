"""Portable collections, locale settings, import previews and an offline sync inbox."""
import json
import hashlib
import math
import sqlite3
import re
import uuid
from difflib import SequenceMatcher

from flask import Blueprint, abort, current_app, g, jsonify, request
from .accounts import account, account_id
from .db import get_db, now
from .library import content_fingerprint

platform = Blueprint("platform", __name__, url_prefix="/api/platform")
TABLES = ("extension_records",)
SCHEMA = """
CREATE TABLE IF NOT EXISTS extension_records(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS extension_kind ON extension_records(kind,updated_at);
"""


def body():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError("请提交 JSON 对象。")
    return data


def text(value, limit=1000, required=True):
    if not isinstance(value, str) or len(value) > limit or required and not value.strip():
        raise ValueError("内容为空或超过长度限制。")
    return value.strip()


def record(row):
    return {**json.loads(row["payload_json"]), "id": row["id"], "version": row["version"],
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def store(kind, payload, identity=None, version=None):
    db = get_db()
    identity = identity or uuid.uuid4().hex
    packed = json.dumps(payload, ensure_ascii=False)
    if len(packed.encode()) > 256000:
        raise ValueError("记录过大，请拆分保存。")
    existing = db.execute("SELECT * FROM extension_records WHERE id=? AND kind=?", (identity, kind)).fetchone()
    if existing:
        if type(version) is not int or version != existing["version"]:
            return None
        changed = db.execute("UPDATE extension_records SET payload_json=?,version=version+1,updated_at=? WHERE id=? AND version=?",
                             (packed, now(), identity, version))
        if not changed.rowcount:
            return None
    else:
        db.execute("INSERT INTO extension_records VALUES(?,?,?,1,?,?)", (identity, kind, packed, now(), now()))
    return record(db.execute("SELECT * FROM extension_records WHERE id=?", (identity,)).fetchone())


def preferences():
    defaults = {"timezone": "Asia/Shanghai", "date_format": "locale", "article_language": "en"}
    for row in get_db().execute("SELECT key,value FROM settings WHERE key LIKE 'locale.%'"):
        try:
            defaults[row["key"].split(".", 1)[1]] = json.loads(row["value"])
        except ValueError:
            pass
    return defaults


@platform.get("/identity")
def identity():
    return jsonify(account={"id": account()["id"], "username": account()["username"]} if account() else None,
                   scope=account_id(), shared=bool(current_app.config.get("LEARNING_SHARED")),
                   preferences=preferences(), storage="account" if account() else "local")


@platform.route("/preferences", methods=["GET", "PATCH"])
def locale_preferences():
    if request.method == "GET":
        return jsonify(preferences())
    data = body()
    if not data or set(data) - {"timezone", "date_format", "article_language"}:
        raise ValueError("不支持的语言和日期设置。")
    if "timezone" in data:
        from .db import timezone_for
        timezone_for(text(data["timezone"], 80))
    if "date_format" in data and data["date_format"] not in ("locale", "iso", "dmy", "mdy"):
        raise ValueError("请选择日期格式。")
    if "article_language" in data and data["article_language"] not in ("en", "fr", "de", "es", "ja", "ko", "zh", "auto"):
        raise ValueError("请选择支持的文章语言。")
    db = get_db()
    for key, value in data.items():
        db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", ("locale." + key, json.dumps(value)))
    db.commit()
    g.pop("reading_timezone", None)
    return jsonify(preferences())


@platform.get("/catalog")
def catalog():
    rows = get_db().execute("SELECT id,title,level,category,word_count,reading_minutes FROM articles WHERE status!='trashed' ORDER BY id DESC LIMIT 1000")
    return jsonify(articles=[dict(r) for r in rows])


@platform.post("/import-preview")
def import_preview():
    from .content import import_url
    from .learning import words
    data = body()
    if data.get("url"):
        article = import_url(text(data["url"], 2048))
    else:
        article = {"title": text(data.get("title"), 250), "content": text(data.get("content"), 100000),
                   "source": text(data.get("source", "个人导入"), 100), "source_url": ""}
    if len(words(article["content"])) < 40:
        raise ValueError("正文不足40个词或文字单位，请检查提取结果。")
    digest = content_fingerprint(article["content"])
    candidates = []
    for row in get_db().execute("SELECT id,title,content,source_url,status FROM articles"):
        exact = content_fingerprint(row["content"]) == digest
        same_title = row["title"].casefold() == article["title"].casefold()
        if exact or same_title:
            candidates.append({"id": row["id"], "title": row["title"], "status": row["status"],
                               "reason": "相同正文" if exact else "同名但正文不同",
                               "similarity": round(SequenceMatcher(None, article["content"][:10000], row["content"][:10000]).ratio(), 3)})
    return jsonify(article=article, candidates=candidates[:20], fingerprint=digest)


@platform.route("/collections", methods=["GET", "POST"])
def collections():
    db = get_db()
    if request.method == "GET":
        return jsonify(collections=[record(r) for r in db.execute("SELECT * FROM extension_records WHERE kind='collection' ORDER BY updated_at DESC")])
    data = body()
    title = text(data.get("title"), 100)
    ids = data.get("article_ids", [])
    if not isinstance(ids, list) or len(ids) > 500 or any(type(i) is not int for i in ids):
        raise ValueError("合集最多500篇文章。")
    ids = list(dict.fromkeys(ids))
    for identity in ids:
        if not db.execute("SELECT 1 FROM articles WHERE id=? AND status!='trashed'", (identity,)).fetchone():
            raise ValueError("合集中的文章不存在或已移入回收站。")
    identity = data.get("id")
    if identity and not db.execute("SELECT 1 FROM extension_records WHERE id=? AND kind='collection'", (identity,)).fetchone():
        abort(404)
    saved = store("collection", {"title": title, "article_ids": ids, "description": text(data.get("description", ""), 2000, False)},
                  identity, data.get("version"))
    if saved is None:
        return jsonify(error="合集已在另一设备修改，请重新加载。"), 409
    db.commit()
    return jsonify(collection=saved), 200 if identity else 201


@platform.delete("/collections/<identity>")
def delete_collection(identity):
    data = body()
    cursor = get_db().execute("DELETE FROM extension_records WHERE kind='collection' AND id=? AND version=?", (identity, data.get("version")))
    if not cursor.rowcount:
        return jsonify(error="合集已变化，请刷新后重试。"), 409
    get_db().commit()
    return jsonify(ok=True)


@platform.get("/offline-bundle/<int:identity>")
def offline_bundle(identity):
    db = get_db()
    article = db.execute("SELECT * FROM articles WHERE id=? AND status!='trashed'", (identity,)).fetchone()
    if not article:
        abort(404)
    words = [dict(r) for r in db.execute("SELECT word,meaning,phonetic,mastery FROM vocabulary")]
    return jsonify(article=dict(article), vocabulary=words, scope=account_id(), downloaded_at=now(),
                   fingerprint=content_fingerprint(article['content']), language=preferences()['article_language'])


@platform.post("/sync")
def sync():
    """Replay one user's explicit offline operations, with receipts and conflicts."""
    from .dictionary import normalize_term
    from .wordbook import ensure_word, attach_context
    data = body()
    operations = data.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        raise ValueError("每次同步1–100条记录。")
    db = get_db()
    results = []
    for operation in operations:
        if not isinstance(operation, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", str(operation.get("id", ""))):
            raise ValueError("同步记录编号无效。")
        receipt = "sync-" + operation["id"]
        operation_hash = hashlib.sha256(json.dumps(operation,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        old = db.execute("SELECT * FROM extension_records WHERE id=? AND kind='receipt'", (receipt,)).fetchone()
        if old:
            if json.loads(old['payload_json']).get('digest', operation_hash) != operation_hash:
                results.append({'id':operation['id'],'status':'conflict','reason':'同步编号已用于不同内容，请导出保留本机记录。'})
                continue
            results.append({"id": operation["id"], "status": "saved", "duplicate": True})
            continue
        try:
            db.execute("SAVEPOINT sync_item")
            kind = operation.get("kind")
            article_id = operation.get("article_id")
            source = db.execute("SELECT * FROM articles WHERE id=? AND status!='trashed'", (article_id,)).fetchone()
            if not source or operation.get("content_hash") != content_fingerprint(source["content"]):
                results.append({"id": operation["id"], "status": "conflict", "reason": "原文已更改或删除，请保留本机记录并重新选择原文。"})
                db.execute("RELEASE sync_item")
                continue
            if kind == "progress":
                position = operation.get("position")
                progress = operation.get("progress")
                if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100 for v in (position, progress)):
                    raise ValueError("同步进度无效。")
                # Progress merges monotonically; position conflicts require an explicit choice.
                changed = operation.get("base_read_at") != source["last_read_at"]
                if changed and operation.get("resolution") not in ("server", "local"):
                    results.append({"id": operation["id"], "status": "conflict", "reason": "另一设备已更新阅读位置。", "server_position": source["position"]})
                    db.execute("RELEASE sync_item")
                    continue
                selected = source["position"] if changed and operation.get("resolution") == "server" else position
                db.execute("UPDATE articles SET progress=max(progress,?),position=?,last_read_at=? WHERE id=?", (progress, selected, now(), article_id))
            elif kind == "word":
                word = normalize_term(operation.get("word", ""))
                meaning = text(operation.get("meaning"), 2000)
                context = text(operation.get("context", ""), 2000, False)
                if context and context not in source["content"]:
                    raise ValueError("原句与文章不符。")
                existing = db.execute("SELECT id,meaning FROM vocabulary WHERE word=? COLLATE NOCASE", (word,)).fetchone()
                if existing:
                    identity = existing["id"]
                    attach_context(db, identity, context, article_id)
                    if existing["meaning"] != meaning:
                        db.execute("INSERT INTO word_notes(word_id,title,body) VALUES(?,?,?)", (identity, "离线补充释义", meaning))
                else:
                    cursor = db.execute("INSERT INTO vocabulary(word,meaning,context,article_id,created_at) VALUES(?,?,?,?,?)", (word, meaning, context, article_id, now()))
                    ensure_word(db, cursor.lastrowid)
            elif kind == "note":
                note = text(operation.get("note"), 6000)
                quote=text(operation.get('quote',''),2000,False)
                if quote and quote not in source['content']: raise ValueError('笔记引文与文章不符。')
                store("offline-note", {"article_id": article_id, "article_title": source["title"], "note": note, "quote": quote})
            else:
                raise ValueError("不支持的离线操作。")
            store("receipt", {"operation": operation["id"], 'digest':operation_hash}, receipt)
            db.execute("RELEASE sync_item")
            db.commit()
            results.append({"id": operation["id"], "status": "saved"})
        except (ValueError, sqlite3.Error):
            db.execute("ROLLBACK TO sync_item")
            db.execute("RELEASE sync_item")
            raise
    return jsonify(results=results)


@platform.get("/reading-summary/<int:identity>")
def reading_summary(identity):
    db = get_db()
    article = db.execute("SELECT id,title FROM articles WHERE id=? AND status!='trashed'", (identity,)).fetchone()
    if not article:
        abort(404)
    return jsonify(article=dict(article),
                   pending=[dict(r) for r in db.execute("SELECT * FROM pending_words WHERE article_id=?", (identity,))],
                   notes=[dict(r) for r in db.execute("SELECT id,kind,title,content FROM knowledge_items WHERE article_id=?", (identity,))],
                   words=[dict(r) for r in db.execute("SELECT id,word,meaning,mastery FROM vocabulary WHERE article_id=?", (identity,))],
                   offline_notes=[record(r) for r in db.execute("SELECT * FROM extension_records WHERE kind='offline-note'") if json.loads(r["payload_json"]).get("article_id") == identity])


@platform.post("/review-words")
def review_words():
    from .srs import ensure_schedule
    data = body()
    ids = data.get("word_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(type(i) is not int for i in ids):
        raise ValueError("请选择1–100个生词。")
    db = get_db()
    for identity in set(ids):
        if not db.execute("SELECT 1 FROM vocabulary WHERE id=?", (identity,)).fetchone():
            raise ValueError("生词已删除。")
        ensure_schedule(db, identity)
        db.execute("UPDATE srs_cards SET due_at=?,suspended=0,version=version+1 WHERE word_id=?", (now(), identity))
    db.commit()
    return jsonify(ok=True, count=len(set(ids)))


@platform.post("/assessment")
def start_assessment():
    from pathlib import Path
    import random
    data_root = Path(current_app.root_path) / "data"
    bank = json.loads((data_root / "reading-exercises.json").read_text(encoding="utf-8"))
    articles = {a["title"]: a for a in json.loads((data_root / "articles.json").read_text(encoding="utf-8"))}
    questions = []
    rng = random.SystemRandom()
    for article in bank["articles"]:
        material = articles[article["article_title"]]["content"]
        candidates = [q for q in article["questions"] if q["type"] in ("choice", "true_false", "main_idea", "inference", "vocabulary") and len(q["answer"]) == 1]
        for q in candidates[:2]:
            options = list(enumerate(q["options"])); rng.shuffle(options)
            answer = str(next(i for i, (old, _) in enumerate(options) if str(old) == q["answer"][0]))
            questions.append({**q, "id": "q"+str(len(questions)), "material": material,
                              "options": [v for _, v in options], "answer": answer})
    saved = store("assessment", {"questions": questions, "completed": False})
    get_db().commit()
    return jsonify(id=saved["id"], questions=[{k:q[k] for k in ("id", "prompt", "options", "material")} for q in questions]), 201


@platform.post("/assessment/<identity>")
def complete_assessment(identity):
    row = get_db().execute("SELECT * FROM extension_records WHERE id=? AND kind='assessment'", (identity,)).fetchone()
    if not row:
        abort(404)
    saved = record(row)
    if saved["completed"]:
        return jsonify(saved["result"])
    answers = body().get("answers")
    if not isinstance(answers, dict):
        raise ValueError("请先完成自测。")
    questions = saved["questions"]
    if set(answers) != {q["id"] for q in questions} or any(not isinstance(v,str) for v in answers.values()):
        raise ValueError("请完成所有题目。")
    score = sum(answers.get(q["id"]) == q["answer"] for q in questions)
    percent = round(score / max(1, len(questions)) * 100)
    level = "A2" if percent < 30 else "B1" if percent < 60 else "B2" if percent < 85 else "C1"
    result = {"percent": percent, "level": level,
              "note": "这是固定阅读样本的参考自测，分数不证明CEFR等级；重复作答的熟悉效应会提高分数。可在阅读偏好中确认是否采用建议。",
              "items": [{"prompt": q["prompt"], "correct": answers[q["id"]] == q["answer"], "explanation": q["explanation"], "evidence": q["evidence"]} for q in questions]}
    saved.pop("id"); saved.pop("version"); saved.pop("created_at"); saved.pop("updated_at")
    saved.update(completed=True, result=result)
    if store("assessment", saved, identity, row["version"]) is None:
        return jsonify(error="自测已在另一设备提交，请刷新。"), 409
    get_db().commit()
    return jsonify(result)


@platform.get("/reading-path")
def reading_path():
    from .personalization import context, analyze
    category = request.args.get("category", "")
    try:
        budget = int(request.args.get("minutes", 15))
    except ValueError:
        raise ValueError("阅读时间无效。") from None
    if not 1 <= budget <= 120:
        raise ValueError("每篇阅读时间范围1–120分钟。")
    db = get_db(); profile, evidence, known = context(db)
    rows = db.execute("SELECT * FROM articles WHERE status!='trashed' AND progress<100 AND (?='' OR category=?)", (category, category)).fetchall()
    pool = []
    for row in rows:
        analysis = analyze(dict(row), profile["preferences"], evidence, known)
        if analysis["minutes"] <= budget:
            pool.append({k: row[k] for k in ("id", "title", "level", "reading_minutes", "category")} | {"load": analysis["relative"]})
    target = min(pool, key=lambda a: abs(a["load"]))["load"] if pool else 0
    pool.sort(key=lambda a: (a["load"], a["id"]))
    candidates = [a for a in pool if a["load"] >= target - .7]
    return jsonify(articles=candidates[:5], method="同主题、未读文章按个人负荷从低到高排列；以最接近当前水平的位置开始。可调整主题与用时，无匹配时不虚构文章。")


@platform.get("/dimensions/<int:identity>")
def difficulty_dimensions(identity):
    from .personalization import context, analyze
    db = get_db()
    row = db.execute("SELECT * FROM articles WHERE id=? AND status!='trashed'", (identity,)).fetchone()
    if not row:
        abort(404)
    profile, evidence, known = context(db)
    value = analyze(dict(row), profile["preferences"], evidence, known)
    saved = db.execute("SELECT payload_json FROM extension_records WHERE id=?", ("background-"+str(identity),)).fetchone()
    background = json.loads(saved[0]) if saved else {"familiarity": "unknown"}
    return jsonify(vocabulary={"load_per_100":value["estimated_load_per_100"],"method":"已记词汇与词频假设"},
                   syntax={"average_sentence_length":value["sentence_length"],"level":"较高" if value["sentence_length"]>25 else "中等" if value["sentence_length"]>16 else "较低","method":"句长估计；不冒充完整句法理解能力"},
                   background=background, article_id=identity)


@platform.post("/dimensions/<int:identity>")
def background_rating(identity):
    if not get_db().execute("SELECT 1 FROM articles WHERE id=?", (identity,)).fetchone():
        abort(404)
    data = body()
    if data.get("familiarity") not in ("familiar", "some", "unfamiliar"):
        raise ValueError("请选择主题背景熟悉程度。")
    key = "background-"+str(identity)
    row = get_db().execute("SELECT version FROM extension_records WHERE id=?", (key,)).fetchone()
    value = store("background", {"familiarity":data["familiarity"],"note":text(data.get("note",""),1000,False),"method":"学习者自评，避免从词汇量推断背景知识"}, key, row[0] if row else None)
    get_db().commit()
    return jsonify(value)
