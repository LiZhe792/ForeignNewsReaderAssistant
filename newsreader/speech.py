"""Reader speech preferences, audio and an ordered persistent listening playlist."""
import hashlib
import json
import math
import re

from flask import Blueprint, abort, jsonify, request, send_file

from . import tts_audio
from .db import get_db, now
from .sentences import article_segments

speech = Blueprint("speech", __name__, url_prefix="/api/tts")
DEFAULTS = {"voice": "en-US-AriaNeural", "rate": 1.0, "follow": True, "click_to_play": False, "loop": "none"}


def body():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError("请求内容必须是 JSON 对象。")
    return data


def validate_settings(data):
    for key, value in data.items():
        if key == "voice":
            valid = value in [voice["id"] for voice in tts_audio.VOICES]
        elif key == "loop":
            valid = value in ("none", "sentence", "paragraph")
        elif key == "rate":
            valid = type(value) in (int, float) and 0.5 <= value <= 2 and math.isfinite(value)
        elif key in ("follow", "click_to_play"):
            valid = type(value) is bool
        else:
            valid = False
        if not valid:
            raise ValueError("朗读设置无效：音色需为列表中的选项，语速为 0.5–2 倍。")


def read_settings():
    result = dict(DEFAULTS)
    row = get_db().execute("SELECT value FROM settings WHERE key='tts_preferences'").fetchone()
    if row:
        try:
            stored = json.loads(row[0])
            if isinstance(stored, dict):
                for key, value in stored.items():
                    try:
                        validate_settings({key: value})
                        result[key] = value
                    except (ValueError, TypeError):
                        pass
        except (ValueError, TypeError):
            pass
    return result


@speech.errorhandler(tts_audio.SpeechError)
def speech_error(error):
    return jsonify(error=str(error)), error.status


@speech.route("/settings", methods=["GET", "PATCH"])
def settings():
    db = get_db()
    if request.method == "PATCH":
        patch = body()
        validate_settings(patch)
        db.execute("BEGIN IMMEDIATE")
        value = {**read_settings(), **patch}
        db.execute("INSERT INTO settings (key,value) VALUES ('tts_preferences',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (json.dumps(value),))
        db.commit()
    return jsonify(settings=read_settings(), voices=tts_audio.VOICES)


def article(article_id):
    if type(article_id) is not int or article_id < 1:
        raise ValueError("文章编号无效。")
    row = get_db().execute("SELECT id,title,content FROM articles WHERE id=?", (article_id,)).fetchone()
    if not row:
        abort(404)
    return row


def word(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", value) or len(value) > 60:
        raise ValueError("请选择或输入一个英文单词，最多 60 个字符。")
    return value


def selected(data):
    scope = data.get("scope", "article")
    if scope == "word":
        value = word(data.get("word"))
        return {"scope": scope, "article_id": None, "unit_index": -1, "word": value,
                "label": value, "article_title": "单词朗读"}
    if scope not in ("article", "paragraph", "sentence"):
        raise ValueError("请选择单词、句子、段落或全文。")
    source = article(data.get("article_id"))
    index = data.get("unit_index", 0)
    if type(index) is not int or index < 0:
        raise ValueError("句段编号无效。")
    segments = article_segments(source["content"])
    if scope == "article":
        index, label = 0, "全文"
    elif scope == "sentence":
        if index >= len(segments):
            raise ValueError("句子不存在。")
        label = "第 {} 段 · {}".format(segments[index]["paragraph"] + 1, segments[index]["text"].strip()[:90])
    else:
        matches = [s for s in segments if s["paragraph"] == index]
        if not matches:
            raise ValueError("段落不存在。")
        label = f"第 {index + 1} 段"
    return {"scope": scope, "article_id": source["id"], "unit_index": index, "word": "",
            "label": label, "article_title": source["title"]}


@speech.post("/audio")
def generate_audio():
    data = body()
    voice = data.get("voice", read_settings()["voice"])
    validate_settings({"voice": voice})
    if data.get("scope") == "word":
        text = word(data.get("word"))
    else:
        source = article(data.get("article_id"))
        segments = article_segments(source["content"])
        index = data.get("sentence_index")
        if type(index) is not int or not 0 <= index < len(segments):
            raise ValueError("请选择文章中的有效句子。")
        text = segments[index]["text"]
    result = tts_audio.get_audio(text, voice)
    return jsonify(**result, url="/api/tts/audio/" + result["key"], voice=voice)


@speech.get("/audio/<key>")
def audio_file(key):
    path = tts_audio.audio_path(key)
    if not path.is_file() or path.is_symlink():
        abort(404)
    return send_file(path, mimetype="audio/mpeg", conditional=True)


@speech.route("/cache", methods=["GET", "DELETE"])
def cache():
    if request.method == "DELETE":
        body()
        return jsonify(tts_audio.clear_cache())
    return jsonify(tts_audio.cache_stats())


def playlist_rows():
    rows = get_db().execute("""SELECT p.*, a.title AS article_title FROM tts_playlist p
        LEFT JOIN articles a ON p.article_id=a.id ORDER BY position,id""").fetchall()
    return [{key: row[key] for key in row.keys() if key != "item_key"} for row in rows]


@speech.route("/playlist", methods=["GET", "POST", "DELETE"])
def playlist():
    db = get_db()
    if request.method == "POST":
        data = body()
        db.execute("BEGIN IMMEDIATE")
        item = selected(data)
        key = hashlib.sha256(json.dumps([item[k] for k in ("scope", "article_id", "unit_index", "word")]).encode()).hexdigest()
        exists = db.execute("SELECT id FROM tts_playlist WHERE item_key=?", (key,)).fetchone()
        if exists:
            db.commit()
            return jsonify(items=playlist_rows(), duplicate=True, id=exists[0])
        if db.execute("SELECT COUNT(*) FROM tts_playlist").fetchone()[0] >= 100:
            raise ValueError("播放列表最多 100 项，请先移除不需要的内容。")
        position = db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM tts_playlist").fetchone()[0]
        entry = db.execute("""INSERT INTO tts_playlist
            (item_key,article_id,scope,unit_index,word,label,position,created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (key, item["article_id"], item["scope"], item["unit_index"], item["word"], item["label"], position, now()))
        db.commit()
        return jsonify(items=playlist_rows(), duplicate=False, id=entry.lastrowid), 201
    if request.method == "DELETE":
        body()
        db.execute("DELETE FROM tts_playlist")
        db.commit()
    return jsonify(items=playlist_rows())


@speech.delete("/playlist/<int:item_id>")
def remove_item(item_id):
    body()
    db = get_db()
    db.execute("DELETE FROM tts_playlist WHERE id=?", (item_id,))
    db.commit()
    return jsonify(items=playlist_rows())


@speech.patch("/playlist/order")
def reorder():
    ids = body().get("ids")
    if not isinstance(ids, list) or any(type(i) is not int for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("播放顺序无效。")
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    current = {row[0] for row in db.execute("SELECT id FROM tts_playlist")}
    if set(ids) != current:
        raise tts_audio.SpeechError("播放列表已变化，请刷新列表后重试排序。", 409)
    db.executemany("UPDATE tts_playlist SET position=? WHERE id=?", enumerate(ids))
    db.commit()
    return jsonify(items=playlist_rows())
