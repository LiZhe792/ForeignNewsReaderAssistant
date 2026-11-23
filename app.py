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

from newsreader.content import import_url, lookup_word
from newsreader.db import close_db, get_db, init_db, now, today
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
            multipart = request.endpoint in ('management.restore_preview','management.articles_preview') and request.mimetype == 'multipart/form-data'
            if not request.is_json and not multipart:
                return jsonify(error="请使用 JSON 格式提交数据。"), 415
            elif request.content_length and request.content_length > 512 * 1024:
                return jsonify(error="提交内容过大。"), 413

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

    @app.get("/")
    def index():
        return render_template("index.html", reader_defaults=READER_DEFAULTS)

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", application="Margin 外刊学习助手")

    @app.get("/api/articles")
    def articles():
        rows = get_db().execute(
            """SELECT id, title, subtitle, source, category, level, difficulty_score,
            word_count, reading_minutes, progress, reading_seconds, last_read_at,
            created_at, is_demo FROM articles ORDER BY created_at DESC, id ASC"""
        ).fetchall()
        return jsonify(articles=[dict(row) for row in rows])

    @app.post("/api/import/preview")
    def preview_file():
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
        return jsonify(article)

    @app.post("/api/articles")
    def add_article():
        data = body()
        url = text(data, "url", 2048)
        if url:
            article = import_url(url)
            duplicate = get_db().execute("SELECT id FROM articles WHERE source_url=?", (article["source_url"],)).fetchone()
            if duplicate:
                return jsonify(id=duplicate["id"], duplicate=True), 200
        else:
            article = {"title": text(data, "title", 250, required=True),
                       "content": text(data, "content", 100000, required=True),
                       "source": text(data, "source", 100, default="个人导入") or "个人导入", "source_url": ""}
        if len(words(article["content"])) < 40:
            raise ValueError("请至少提供 40 个英文单词的正文。")
        category = text(data, "category", 20, default="其他")
        if category not in ("科技", "文化", "科学", "生活", "环境", "其他"):
            raise ValueError("请选择有效的文章分类。")
        metrics = assess_difficulty(article["content"])
        db = get_db()
        cursor = db.execute(
            """INSERT INTO articles (title, content, source, source_url, category, level,
            difficulty_score, word_count, reading_minutes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (article["title"], article["content"], article["source"], article["source_url"], category,
             metrics["level"], metrics["score"], metrics["word_count"], metrics["reading_minutes"], now()),
        )
        db.commit()
        return jsonify(id=cursor.lastrowid, duplicate=False), 201

    @app.delete("/api/articles/<int:article_id>")
    def delete_article(article_id):
        expire_pending()
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        record("articles", article_id)
        pending = db.execute("""SELECT 1 FROM qa_turns q JOIN qa_threads t ON q.thread_id=t.id
                                WHERE t.article_id=? AND q.status='pending' LIMIT 1""", (article_id,)).fetchone()
        if pending:
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

    return app


if __name__ == "__main__":
    application = create_app()
    port = int(os.getenv("PORT", "5000"))
    print(f"\nMargin reader: http://127.0.0.1:{port}\nPress Ctrl+C to stop.\n", flush=True)
    application.run(host="127.0.0.1", port=port, debug=False, threaded=True)
