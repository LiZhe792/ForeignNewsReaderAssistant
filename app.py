"""Run this file in PyCharm, then open http://127.0.0.1:5000."""

import calendar
import json
import os
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from newsreader.articles import CATEGORIES, ensure_source, import_many, insert_article
from newsreader.content import import_url, lookup_word
from newsreader.db import close_db, get_db, init_db, now, today
from newsreader.feeds import refresh_feed, subscribe
from newsreader.learning import assess_difficulty, words
from newsreader.preferences import READER_DEFAULTS, read_preferences, validate_preferences
from newsreader.writing import writing, expire_pending_writing
from newsreader.reading_flow import reading_flow
from newsreader.dictionary import normalize_term
from newsreader.assistance import assistance
from newsreader.wordbook import wordbook, ensure_word, attach_context
from newsreader.srs import review
from newsreader.training import training
from newsreader.exercises import exercises
from newsreader.personalization import personalization
from newsreader.data_management import management, initialize as initialize_management
from newsreader.study_plans import planning, continuity, read_goals, today_progress
from newsreader.analytics import analytics, record_reading, token as analytics_token, fingerprint as analytics_fingerprint
from newsreader.personalization import digest as article_digest
from newsreader.writing_workspace import default_database as writing_database, install as install_writing

from newsreader.article_qa import article_qa, expire_pending

from newsreader.sentences import article_segments

from newsreader.speech import speech

from newsreader.knowledge import knowledge
from newsreader.learning_lab import lab

from newsreader.exports import exports

from newsreader.deep_analysis import deep_analysis

from newsreader.file_import import extract_file

from newsreader.search import filtered_articles, similar_articles

from newsreader.db import close_db, get_db, init_db, normalize_title, now, today

from newsreader.library import (
    PRIORITIES, STATUSES, ensure_tag, find_duplicate, set_article_tags, tags_for_articles,
)

ROOT = Path(__file__).resolve().parent


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=str(ROOT / "instance" / "margin.sqlite3"),
        MAX_CONTENT_LENGTH=21 * 1024 * 1024,
        JSON_AS_ASCII=False,
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"],
        AI_API_KEY=os.getenv("MARGIN_AI_API_KEY", ""),
        AI_MODEL=os.getenv("MARGIN_AI_MODEL", ""),
        AI_BASE_URL=os.getenv("MARGIN_AI_BASE_URL", "https://api.openai.com/v1"),
    )
    if test_config:
        app.config.update(test_config)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    app.json.ensure_ascii = False
    app.teardown_appcontext(close_db)
    app.register_blueprint(writing)
    app.register_blueprint(reading_flow)
    app.register_blueprint(assistance)
    app.register_blueprint(wordbook)
    app.register_blueprint(review)
    app.register_blueprint(training)
    app.register_blueprint(exercises)
    app.register_blueprint(personalization)
    app.register_blueprint(analytics)
    app.register_blueprint(management)
    app.register_blueprint(planning)
    if not app.config.get("WRITING_DATABASE"):
        app.config["WRITING_DATABASE"] = os.getenv("MARGIN_WRITING_DATABASE") or writing_database(app.config["DATABASE"])
    install_writing(app)
    app.register_blueprint(article_qa)
    app.register_blueprint(speech)
    app.register_blueprint(knowledge)
    app.register_blueprint(lab)
    app.register_blueprint(exports)
    app.register_blueprint(deep_analysis)
    with app.app_context():
        init_db()
        initialize_management(app)

    @app.before_request
    def protect_local_writes():
        if request.blueprint == "workspace":
            return
        if request.method in ("POST", "PATCH", "DELETE", "PUT"):
            origin = request.headers.get("Origin")
            if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
                return jsonify(error="不允许跨站修改本地学习数据。"), 403
            multipart = request.endpoint in ('management.restore_preview','management.articles_preview','preview_file') and request.mimetype == 'multipart/form-data'
            if not request.is_json and not multipart:
                return jsonify(error="请使用 JSON 格式提交数据。"), 415

    @app.after_request
    def security_headers(response):
        if request.blueprint == "workspace":
            return response
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(Exception)
    def handle_error(error):
        if isinstance(error, HTTPException):
            messages = {400: "请求格式不正确。", 404: "记录不存在。", 405: "不支持此操作。", 413: "提交内容过大。"}
            return jsonify(error=messages.get(error.code, error.description)), error.code
        if isinstance(error, ValueError):
            return jsonify(error=str(error)), 400
        if isinstance(error, sqlite3.IntegrityError):
            return jsonify(error="数据关联无效，请刷新后重试。"), 400
        app.logger.exception("Request failed")
        return jsonify(error="服务暂时遇到问题，请稍后重试。"), 500

    def body():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("请求内容必须是 JSON 对象。")
        return data

    def text(data, key, maximum, default="", required=False):
        value = data.get(key, default)
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f"{key} 必须是长度不超过 {maximum} 的文本。")
        value = value.strip()
        if required and not value:
            raise ValueError(f"请填写 {key}。")
        return value

    def record(table, record_id):
        # table is an internal constant, never user input.
        row = get_db().execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        if row is None:
            from flask import abort
            abort(404)
        return dict(row)

    def category_exists(name):
        return get_db().execute("SELECT 1 FROM categories WHERE name=?", (name,)).fetchone() is not None

    @app.get("/")
    def index():
        return render_template("index.html", reader_defaults=READER_DEFAULTS)

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", application="Margin 外刊学习助手")

    @app.get("/api/articles")
    def articles():
        db = get_db()
        status = request.args.get("status", "all")
        where, params = [], []
        if status in STATUSES:
            where.append("status=?"); params.append(status)
        elif status == "all":
            where.append("status!='trashed'")
        if request.args.get("favorite") == "1":
            where.append("favorite=1")
        if request.args.get("read_later") == "1":
            where.append("read_later=1")
        priority = request.args.get("priority", "")
        if priority.isdigit() and int(priority) in PRIORITIES:
            where.append("priority=?"); params.append(int(priority))
        tag = request.args.get("tag", "")
        if tag:
            where.append("id IN (SELECT at.article_id FROM article_tags at JOIN tags t ON t.id=at.tag_id WHERE t.name=?)")
            params.append(tag)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = db.execute(
            f"""SELECT id, title, subtitle, source, category, level, difficulty_score,
            word_count, reading_minutes, progress, reading_seconds, last_read_at,
            created_at, is_demo, favorite, read_later, priority, status, author, published_at
            FROM articles {clause} ORDER BY created_at DESC, id ASC""", params
        ).fetchall()
        tag_map = tags_for_articles(db)
        result = []
        for row in rows:
            item = dict(row)
            item["tags"] = tag_map.get(item["id"], [])
            result.append(item)
        return jsonify(articles=result)

    @app.post("/api/import/preview")
    def preview_file():
        if request.mimetype != "multipart/form-data":
            return jsonify(error="请使用文件上传表单。"), 415
        upload = request.files.get("file")
        if not upload or not upload.filename:
            raise ValueError("请选择一个文件。")
        data = upload.stream.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("文件不能超过 20 MB。")
        return jsonify(extract_file(upload.filename, data))

    @app.get("/api/articles/<int:article_id>")
    def article_detail(article_id):
        article = record("articles", article_id)
        article["difficulty"] = assess_difficulty(article["content"])
        article["content_hash"] = article_digest(article["content"])
        article["tts_segments"] = article_segments(article["content"])
        article["tags"] = tags_for_articles(get_db()).get(article_id, [])
        return jsonify(article)

    @app.get("/api/articles/search")
    def search_articles():
        return jsonify(articles=filtered_articles(get_db(), request.args))

    @app.get("/api/articles/search-options")
    def search_options():
        db = get_db()
        return jsonify(sources=[r[0] for r in db.execute("SELECT DISTINCT source FROM articles ORDER BY source COLLATE NOCASE")],
                       tags=[r[0] for r in db.execute("SELECT DISTINCT t.name FROM tags t JOIN article_tags at ON at.tag_id=t.id ORDER BY t.name COLLATE NOCASE")])

    @app.patch("/api/articles/<int:article_id>/tags")
    def update_article_tags(article_id):
        record("articles", article_id)
        data = body()
        tags = data.get("tags")
        if not isinstance(tags, list) or len(tags) > 10 or any(not isinstance(tag, str) or not tag.strip() or len(tag.strip()) > 20 for tag in tags):
            raise ValueError("标签最多 10 个，每个标签为 1–20 个字符。")
        unique = {}
        for tag in tags:
            cleaned = tag.strip()
            unique.setdefault(cleaned.casefold(), cleaned)
        db = get_db()
        set_article_tags(db, article_id, list(unique.values()))
        db.commit()
        return jsonify(tags=list(unique.values()))

    @app.get("/api/articles/<int:article_id>/similar")
    def similar(article_id):
        result = similar_articles(get_db(), article_id)
        if result is None:
            from flask import abort
            abort(404)
        return jsonify(articles=result)

    @app.post("/api/articles")
    def add_article():
        data = body()
        url = text(data, "url", 2048)
        if url:
            article = import_url(url)
        else:
            article = {"title": text(data, "title", 250, required=True),
                       "content": text(data, "content", 100000, required=True),
                       "source": text(data, "source", 100, default="个人导入") or "个人导入", "source_url": ""}
        if len(words(article["content"])) < 40:
            raise ValueError("请至少提供 40 个英文单词的正文。")
        category = text(data, "category", 20, default="其他")
        if not category_exists(category):
            raise ValueError("请选择有效的文章分类。")
        db = get_db()
        duplicate = db.execute("SELECT id FROM articles WHERE title_norm=? OR (source_url!='' AND source_url=?)", (normalize_title(article["title"]), article["source_url"])).fetchone()
        if duplicate:
            return jsonify(id=duplicate["id"], duplicate=True)
        article_id = insert_article(
            db, title=article["title"], content=article["content"], source=article["source"],
            source_url=article["source_url"], category=category,
        )
        ensure_source(db, article["source"])
        db.commit()
        return jsonify(id=article_id, duplicate=False), 201

    @app.delete("/api/articles/<int:article_id>")
    def delete_article(article_id):
        expire_pending()
        expire_pending_writing()
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        record("articles", article_id)
        pending = db.execute("""SELECT 1 FROM qa_turns q JOIN qa_threads t ON q.thread_id=t.id
                                WHERE t.article_id=? AND q.status='pending' LIMIT 1""", (article_id,)).fetchone()
        pending_writing = db.execute("""SELECT 1 FROM writing_attempts a JOIN writing_exercises e ON a.exercise_id=e.id
                                        WHERE e.article_id=? AND a.status='pending' LIMIT 1""", (article_id,)).fetchone()
        if pending or pending_writing:
            db.rollback()
            return jsonify(error="这篇文章的 AI 回答仍在生成，请等返回后再删除文章。"), 409
        db.execute("DELETE FROM articles WHERE id=?", (article_id,))
        db.commit()
        return jsonify(ok=True)

    @app.patch("/api/articles/<int:article_id>/progress")
    def update_progress(article_id):
        data = body()
        progress, position, seconds = data.get("progress", 0), data.get("position", 0), data.get("seconds", 0)
        for value in (progress, position):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
                raise ValueError("阅读进度必须介于 0 与 100 之间。")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 0 <= seconds <= 60:
            raise ValueError("每次阅读计时必须为 0–60 秒。")
        request_id = data.get('request_id')
        if request_id is not None: analytics_token(request_id)
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        if request_id:
            previous = db.execute('SELECT fingerprint,response_json FROM learning_reading_events WHERE request_id=?',(request_id,)).fetchone()
            if previous:
                db.rollback()
                if previous['fingerprint'] != analytics_fingerprint([article_id,progress,position,seconds]):
                    return jsonify(error='阅读记录编号已用于其他内容。'),409
                return jsonify(**json.loads(previous['response_json']),duplicate=True)
        article = record("articles", article_id)
        progress = max(article["progress"], progress)
        activity_day = today().isoformat()
        newly_completed = progress >= 100 and not article["completed_at"]
        completed_at = article["completed_at"] or (now() if newly_completed else None)
        db.execute(
            "UPDATE articles SET progress=?, position=?, reading_seconds=reading_seconds+?, last_read_at=?, completed_at=? WHERE id=?",
            (progress, position, seconds, now(), completed_at, article_id),
        )
        if seconds or newly_completed:
            db.execute(
                """INSERT INTO checkins (date, reading_seconds, articles_completed) VALUES (?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET reading_seconds=reading_seconds+excluded.reading_seconds,
                articles_completed=articles_completed+excluded.articles_completed""",
                (activity_day, seconds, int(newly_completed)),
            )
        response = {'progress':progress,'completed':bool(completed_at)}
        record_reading(db,article,data,response,activity_day)
        db.commit()
        return jsonify(**response)

    @app.get("/api/dictionary")
    def dictionary():
        provider = request.args.get("provider", read_preferences(get_db())["dictionary_provider"])
        return jsonify(lookup_word(request.args.get("word", ""), provider))

    @app.get("/api/vocabulary")
    def vocabulary():
        rows = get_db().execute(
            "SELECT v.*, a.title AS article_title FROM vocabulary v LEFT JOIN articles a ON a.id=v.article_id ORDER BY v.created_at DESC, v.id DESC"
        ).fetchall()
        return jsonify(words=[dict(row) for row in rows])

    @app.post("/api/vocabulary")
    def add_word():
        data = body()
        word = normalize_term(text(data, "word", 60, required=True))
        meaning = text(data, "meaning", 3000, required=True)
        phonetic, part = text(data, "phonetic", 150), text(data, "part_of_speech", 100)
        context, note = text(data, "context", 5000), text(data, "note", 2000)
        article_id = data.get("article_id")
        if article_id is not None:
            if isinstance(article_id, bool) or not isinstance(article_id, int):
                raise ValueError("文章编号无效。")
            record("articles", article_id)
        db = get_db()
        existing = db.execute("SELECT id FROM vocabulary WHERE word=?", (word,)).fetchone()
        if existing:
            ensure_word(db,existing['id'])
            attach_context(db,existing['id'],context,article_id)
            db.execute('UPDATE word_meta SET version=version+1 WHERE word_id=?',(existing['id'],))
            db.commit()
            return jsonify(id=existing["id"], duplicate=True), 200
        cursor = db.execute(
            """INSERT INTO vocabulary (word, phonetic, meaning, part_of_speech, context, note, article_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (word, phonetic, meaning, part, context, note, article_id, now()),
        )
        ensure_word(db,cursor.lastrowid)
        db.commit()
        return jsonify(id=cursor.lastrowid, duplicate=False), 201

    @app.patch("/api/vocabulary/<int:word_id>")
    def update_word(word_id):
        record("vocabulary", word_id)
        data = body()
        db = get_db()
        for field in ("note", "meaning"):
            if field in data:
                value = text(data, field, 3000 if field == "meaning" else 2000, required=field == "meaning")
                db.execute(f"UPDATE vocabulary SET {field}=? WHERE id=?", (value, word_id))
        if "mastery" in data:
            mastery = data["mastery"]
            if mastery not in ("new", "learning", "mastered"):
                raise ValueError("掌握度无效。")
            db.execute(
                "UPDATE vocabulary SET mastery=? WHERE id=?", (mastery, word_id),
            )
        ensure_word(db,word_id)
        base=record('vocabulary',word_id)
        db.execute('UPDATE word_senses SET meaning=?,part_of_speech=? WHERE word_id=? AND is_primary=1',(base['meaning'],base['part_of_speech'],word_id))
        db.execute('UPDATE word_meta SET version=version+1 WHERE word_id=?',(word_id,))
        db.commit()
        return jsonify(base)

    @app.delete("/api/vocabulary/<int:word_id>")
    def delete_word(word_id):
        record("vocabulary", word_id)
        db = get_db()
        db.execute("DELETE FROM vocabulary WHERE id=?", (word_id,))
        db.commit()
        return jsonify(ok=True)

    @app.post("/api/checkin")
    def checkin():
        body()
        db = get_db()
        db.execute("INSERT INTO checkins (date, manual) VALUES (?, 1) ON CONFLICT(date) DO UPDATE SET manual=1", (today().isoformat(),))
        db.commit()
        return jsonify(date=today().isoformat(), ok=True)

    @app.get("/api/stats")
    def stats():
        db = get_db()
        current = today()
        month = request.args.get("month", current.strftime("%Y-%m"))
        try:
            first = date.fromisoformat(month + "-01")
        except ValueError as exc:
            raise ValueError("月份格式应为 YYYY-MM。") from exc
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        days = [dict(r) for r in db.execute("SELECT * FROM checkins WHERE date BETWEEN ? AND ?", (first.isoformat(), last.isoformat()))]
        activity_dates = {r[0] for r in db.execute("SELECT date FROM checkins WHERE date<=?", (current.isoformat(),))}
        makeups = [dict(row) for row in db.execute(
            "SELECT * FROM study_makeups WHERE day BETWEEN ? AND ? ORDER BY day",
            (first.isoformat(), last.isoformat()),
        )]
        learned = today_progress(db, current)
        goals = read_goals(db)
        today_record = db.execute("SELECT * FROM checkins WHERE date=?", (current.isoformat(),)).fetchone()
        return jsonify(
            today=current.isoformat(), month=month, days=days, makeups=makeups, streak=continuity(db,current)["current"],
            daily_goals={"reading_minutes":goals["reading_minutes"],"new_words":goals["new_words"],"words_today":learned["words"]},
            checked_in=current.isoformat() in activity_dates,
            today_seconds=today_record["reading_seconds"] if today_record else 0,
            total_seconds=db.execute("SELECT COALESCE(SUM(reading_seconds),0) FROM checkins").fetchone()[0],
            completed_articles=db.execute("SELECT COALESCE(SUM(articles_completed),0) FROM checkins").fetchone()[0],
            total_words=db.execute("SELECT COUNT(*) FROM vocabulary").fetchone()[0],
            mastered_words=db.execute("SELECT COUNT(*) FROM vocabulary WHERE mastery='mastered'").fetchone()[0],
            active_days=len(activity_dates),
        )

    @app.route("/api/settings", methods=["GET", "PATCH"])
    def settings():
        db = get_db()
        if request.method == "PATCH":
            data = body()
            validate_preferences(data)
            for key, value in data.items():
                db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))
            db.commit()
        return jsonify(read_preferences(db))


    def batch_articles():
        data = body()
        urls = data.get("urls")
        if isinstance(urls, str):
            urls = urls.splitlines()
        if not isinstance(urls, list):
            raise ValueError("请提供要导入的链接列表。")
        links = [item.strip() for item in urls if isinstance(item, str) and item.strip()]
        if not links:
            raise ValueError("请至少提供一个文章链接。")
        if len(links) > 10:
            raise ValueError("一次最多导入 10 个链接。")
        category = text(data, "category", 20, default="其他")
        if category not in CATEGORIES:
            raise ValueError("请选择有效的文章分类。")
        results = import_many(links, category=category)
        return jsonify(results=results, imported=sum(
            1 for item in results if item.get("ok") and not item.get("duplicate")))

    @app.get("/api/feeds")
    def feeds():
        rows = get_db().execute(
            """SELECT f.*, s.name AS source_name, s.domain,
            (SELECT COUNT(*) FROM articles a WHERE a.feed_id=f.id) AS article_count
            FROM feeds f LEFT JOIN sources s ON s.id=f.source_id
            ORDER BY f.created_at DESC, f.id DESC"""
        ).fetchall()
        return jsonify(feeds=[dict(row) for row in rows])

    @app.post("/api/feeds")
    def add_feed():
        data = body()
        url = text(data, "url", 2048, required=True)
        category = text(data, "category", 20, default="其他")
        if category not in CATEGORIES:
            raise ValueError("请选择有效的栏目分类。")
        result = subscribe(
            url, category=category,
            source_name=text(data, "source", 100),
            keywords=text(data, "keywords", 300),
            title=text(data, "title", 250),
        )
        return jsonify(result), 201

    @app.post("/api/feeds/refresh")
    def refresh_feeds():
        db = get_db()
        rows = db.execute(
            """SELECT * FROM feeds WHERE enabled=1
            ORDER BY CASE WHEN last_fetched_at IS NULL THEN 0 ELSE 1 END, last_fetched_at
            LIMIT 12"""
        ).fetchall()
        results = []
        for row in rows:
            try:
                results.append({"id": row["id"], "title": row["title"], "ok": True, **refresh_feed(row)})
            except ValueError as exc:
                results.append({"id": row["id"], "title": row["title"], "ok": False, "error": str(exc)})
        return jsonify(results=results, updated=sum(item.get("added", 0) for item in results))

    @app.post("/api/feeds/<int:feed_id>/refresh")
    def refresh_one_feed(feed_id):
        feed = record("feeds", feed_id)
        return jsonify(id=feed_id, **refresh_feed(feed))

    @app.patch("/api/feeds/<int:feed_id>")
    def update_feed(feed_id):
        feed = record("feeds", feed_id)
        data = body()
        db = get_db()
        if "enabled" in data:
            if not isinstance(data["enabled"], bool):
                raise ValueError("启用状态必须是布尔值。")
            db.execute("UPDATE feeds SET enabled=? WHERE id=?", (int(data["enabled"]), feed_id))
        if "category" in data:
            category = text(data, "category", 20)
            if category not in CATEGORIES:
                raise ValueError("请选择有效的栏目分类。")
            db.execute("UPDATE feeds SET category=? WHERE id=?", (category, feed_id))
        if "keywords" in data:
            db.execute("UPDATE feeds SET keywords=? WHERE id=?", (text(data, "keywords", 300), feed_id))
        if "title" in data:
            db.execute("UPDATE feeds SET title=? WHERE id=?", (text(data, "title", 250, required=True), feed_id))
        db.commit()
        return jsonify(record("feeds", feed_id))

    @app.delete("/api/feeds/<int:feed_id>")
    def delete_feed(feed_id):
        record("feeds", feed_id)
        db = get_db()
        db.execute("DELETE FROM feed_seen WHERE feed_id=?", (feed_id,))
        db.execute("UPDATE articles SET feed_id=NULL WHERE feed_id=?", (feed_id,))
        db.execute("DELETE FROM feeds WHERE id=?", (feed_id,))
        db.commit()
        return jsonify(ok=True)

    @app.get("/api/sources")
    def sources():
        rows = get_db().execute(
            """SELECT s.*, (SELECT COUNT(*) FROM articles a WHERE a.source=s.name) AS article_count,
            (SELECT COUNT(*) FROM feeds f WHERE f.source_id=s.id) AS feed_count
            FROM sources s ORDER BY article_count DESC, s.name ASC"""
        ).fetchall()
        return jsonify(sources=[dict(row) for row in rows])

    @app.patch("/api/sources/<int:source_id>")
    def update_source(source_id):
        source = record("sources", source_id)
        data = body()
        db = get_db()
        if "name" in data:
            name = text(data, "name", 100, required=True)
            duplicate = db.execute("SELECT id FROM sources WHERE name=? AND id!=?", (name, source_id)).fetchone()
            if duplicate:
                raise ValueError("已有同名的媒体来源。")
            db.execute("UPDATE sources SET name=? WHERE id=?", (name, source_id))
            db.execute("UPDATE articles SET source=? WHERE source=?", (name, source["name"]))
        if "note" in data:
            db.execute("UPDATE sources SET note=? WHERE id=?", (text(data, "note", 500), source_id))
        db.commit()
        return jsonify(record("sources", source_id))

    @app.delete("/api/sources/<int:source_id>")
    def delete_source(source_id):
        record("sources", source_id)
        db = get_db()
        db.execute("DELETE FROM sources WHERE id=?", (source_id,))
        db.commit()
        return jsonify(ok=True)


    @app.patch("/api/articles/<int:article_id>")
    def edit_article(article_id):
        record("articles", article_id)
        data = body()
        db = get_db()
        sets, params = [], []
        if "favorite" in data:
            if not isinstance(data["favorite"], bool):
                raise ValueError("收藏标记必须是布尔值。")
            sets.append("favorite=?"); params.append(int(data["favorite"]))
        if "read_later" in data:
            if not isinstance(data["read_later"], bool):
                raise ValueError("待读标记必须是布尔值。")
            sets.append("read_later=?"); params.append(int(data["read_later"]))
        if "priority" in data:
            priority = data["priority"]
            if isinstance(priority, bool) or not isinstance(priority, int) or priority not in PRIORITIES:
                raise ValueError("阅读优先级无效。")
            sets.append("priority=?"); params.append(priority)
        if "status" in data:
            status = data["status"]
            if status not in STATUSES:
                raise ValueError("文章状态无效。")
            sets.append("status=?"); params.append(status)
        if "category" in data:
            category = text(data, "category", 20, required=True)
            if not category_exists(category):
                raise ValueError("请选择有效的文章分类。")
            sets.append("category=?"); params.append(category)
        if "author" in data:
            sets.append("author=?"); params.append(text(data, "author", 100))
        if "published_at" in data:
            sets.append("published_at=?"); params.append(text(data, "published_at", 40))
        if "tags" in data:
            if not isinstance(data["tags"], list):
                raise ValueError("标签必须是字符串列表。")
            set_article_tags(db, article_id, [item for item in data["tags"] if isinstance(item, str)])
        if sets:
            db.execute(f"UPDATE articles SET {', '.join(sets)} WHERE id=?", (*params, article_id))
        db.commit()
        result = record("articles", article_id)
        result["tags"] = tags_for_articles(db).get(article_id, [])
        return jsonify(result)

    @app.post("/api/articles/batch")
    def manage_articles():
        data = body()
        if "urls" in data:
            return batch_articles()
        ids = data.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 200:
            raise ValueError("请选择 1 到 200 篇文章。")
        if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in ids):
            raise ValueError("文章编号无效。")
        op = data.get("op")
        db = get_db()
        placeholders = ",".join("?" * len(ids))
        if op in ("trash", "archive", "restore"):
            status = {"trash": "trashed", "archive": "archived", "restore": "active"}[op]
            db.execute(f"UPDATE articles SET status=? WHERE id IN ({placeholders})", (status, *ids))
        elif op == "delete":
            db.execute(f"DELETE FROM articles WHERE id IN ({placeholders})", ids)
        elif op in ("favorite", "unfavorite"):
            db.execute(f"UPDATE articles SET favorite=? WHERE id IN ({placeholders})",
                       (1 if op == "favorite" else 0, *ids))
        elif op in ("read_later", "unread_later"):
            db.execute(f"UPDATE articles SET read_later=? WHERE id IN ({placeholders})",
                       (1 if op == "read_later" else 0, *ids))
        elif op == "set_priority":
            priority = data.get("priority")
            if isinstance(priority, bool) or not isinstance(priority, int) or priority not in PRIORITIES:
                raise ValueError("阅读优先级无效。")
            db.execute(f"UPDATE articles SET priority=? WHERE id IN ({placeholders})", (priority, *ids))
        elif op == "set_category":
            category = text(data, "category", 20, required=True)
            if not category_exists(category):
                raise ValueError("请选择有效的文章分类。")
            db.execute(f"UPDATE articles SET category=? WHERE id IN ({placeholders})", (category, *ids))
        elif op in ("add_tag", "remove_tag"):
            name = text(data, "tag", 30, required=True)
            if op == "add_tag":
                tag_id = ensure_tag(db, name)
                for article_id in ids:
                    db.execute("INSERT OR IGNORE INTO article_tags (article_id, tag_id) VALUES (?, ?)",
                               (article_id, tag_id))
            else:
                db.execute(
                    "DELETE FROM article_tags WHERE tag_id=(SELECT id FROM tags WHERE name=?) "
                    f"AND article_id IN ({placeholders})", (name, *ids))
        else:
            raise ValueError("不支持的操作。")
        db.commit()
        return jsonify(ok=True, count=len(ids))

    @app.get("/api/categories")
    def categories():
        rows = get_db().execute(
            """SELECT c.*, (SELECT COUNT(*) FROM articles a WHERE a.category=c.name AND a.status!='trashed') AS article_count
            FROM categories c ORDER BY c.id"""
        ).fetchall()
        return jsonify(categories=[dict(row) for row in rows])

    @app.post("/api/categories")
    def add_category():
        data = body()
        name = text(data, "name", 20, required=True)
        db = get_db()
        existing = db.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()
        if existing:
            return jsonify(id=existing["id"], duplicate=True), 200
        cursor = db.execute("INSERT INTO categories (name, created_at) VALUES (?, ?)", (name, now()))
        db.commit()
        return jsonify(id=cursor.lastrowid, name=name), 201

    @app.patch("/api/categories/<int:category_id>")
    def rename_category(category_id):
        category = record("categories", category_id)
        data = body()
        name = text(data, "name", 20, required=True)
        db = get_db()
        if db.execute("SELECT 1 FROM categories WHERE name=? AND id!=?", (name, category_id)).fetchone():
            raise ValueError("已有同名分类。")
        db.execute("UPDATE categories SET name=? WHERE id=?", (name, category_id))
        db.execute("UPDATE articles SET category=? WHERE category=?", (name, category["name"]))
        db.commit()
        return jsonify(record("categories", category_id))

    @app.delete("/api/categories/<int:category_id>")
    def delete_category(category_id):
        category = record("categories", category_id)
        db = get_db()
        if db.execute("SELECT COUNT(*) FROM categories").fetchone()[0] <= 1:
            raise ValueError("至少需要保留一个分类。")
        target = "其他" if category["name"] != "其他" and category_exists("其他") else None
        if target is None:
            target = db.execute("SELECT name FROM categories WHERE id!=? ORDER BY id LIMIT 1", (category_id,)).fetchone()["name"]
        db.execute("UPDATE articles SET category=? WHERE category=?", (target, category["name"]))
        db.execute("DELETE FROM categories WHERE id=?", (category_id,))
        db.commit()
        return jsonify(ok=True)

    @app.get("/api/tags")
    def tags():
        rows = get_db().execute(
            """SELECT t.*, (SELECT COUNT(*) FROM article_tags at WHERE at.tag_id=t.id) AS article_count
            FROM tags t ORDER BY t.name"""
        ).fetchall()
        return jsonify(tags=[dict(row) for row in rows])

    @app.post("/api/tags")
    def add_tag():
        data = body()
        tag_id = ensure_tag(get_db(), text(data, "name", 30, required=True))
        get_db().commit()
        return jsonify(id=tag_id), 201

    @app.patch("/api/tags/<int:tag_id>")
    def rename_tag(tag_id):
        record("tags", tag_id)
        data = body()
        name = text(data, "name", 30, required=True)
        db = get_db()
        if db.execute("SELECT 1 FROM tags WHERE name=? AND id!=?", (name, tag_id)).fetchone():
            raise ValueError("已有同名标签。")
        db.execute("UPDATE tags SET name=? WHERE id=?", (name, tag_id))
        db.commit()
        return jsonify(record("tags", tag_id))

    @app.delete("/api/tags/<int:tag_id>")
    def delete_tag(tag_id):
        record("tags", tag_id)
        db = get_db()
        db.execute("DELETE FROM tags WHERE id=?", (tag_id,))
        db.commit()
        return jsonify(ok=True)


    return app


if __name__ == "__main__":
    application = create_app()
    port = int(os.getenv("PORT", "5000"))
    print(f"\nMargin reader: http://127.0.0.1:{port}\nPress Ctrl+C to stop.\n", flush=True)
    application.run(host="127.0.0.1", port=port, debug=False, threaded=True)
