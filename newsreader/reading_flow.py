"""Article-scoped deferred lookup and atomic collection into the vocabulary."""

import re

from flask import Blueprint, abort, jsonify, request

from .db import get_db, now
from .dictionary import normalize_term
from .wordbook import ensure_word, attach_context

reading_flow = Blueprint("reading_flow", __name__, url_prefix="/api/articles")


def contains_term(paragraph, term):
    paragraph = re.sub(r"\s+", " ", paragraph.lower().replace("’", "'"))
    return re.search(r"(?<![a-z'-])" + re.escape(term) + r"(?![a-z'-])", paragraph) is not None


def payload():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError("请求内容必须是 JSON 对象。")
    return data


def field(data, key, limit, required=False):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{key} 必须是长度不超过 {limit} 的文本。")
    value = value.strip()
    if required and not value:
        raise ValueError(f"请填写 {key}。")
    return value


def article(article_id):
    row = get_db().execute("SELECT id,title,category,content FROM articles WHERE id=?", (article_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def pending(article_id, pending_id):
    row = get_db().execute(
        "SELECT * FROM pending_words WHERE article_id=? AND id=?", (article_id, pending_id)
    ).fetchone()
    if row is None:
        abort(404)
    return row


@reading_flow.route("/<int:article_id>/pending-words", methods=["GET", "POST"])
def pending_words(article_id):
    source = article(article_id)
    db = get_db()
    if request.method == "GET":
        rows = db.execute(
            """SELECT p.*, v.id AS vocabulary_id, v.mastery FROM pending_words p
            LEFT JOIN vocabulary v ON v.word=p.word
            WHERE p.article_id=? ORDER BY p.paragraph_index, p.id""", (article_id,)
        ).fetchall()
        return jsonify(words=[dict(row) for row in rows])

    data = payload()
    word = normalize_term(field(data, "word", 60, required=True))
    index = data.get("paragraph_index", -1)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n", source["content"]) if p.strip()]
    if type(index) is not int or not -1 <= index < len(paragraphs):
        raise ValueError("段落编号无效。")
    context = ""
    if index >= 0:
        paragraph = paragraphs[index]
        if not contains_term(paragraph, word):
            raise ValueError("该单词或短语不在指定段落中。")
        sentences = re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", paragraph)
        context = next((s.strip() for s in sentences if contains_term(s, word)), paragraph)[:5000]
    cursor = db.execute(
        """INSERT INTO pending_words (article_id, paragraph_index, word, context, created_at)
        VALUES (?, ?, ?, ?, ?) ON CONFLICT(article_id, paragraph_index, word) DO NOTHING""",
        (article_id, index, word, context, now()),
    )
    duplicate = cursor.rowcount == 0
    if index >= 0:
        from .analytics import record_lookup
        record_lookup(db, word, "encounter", source, index)
    row = db.execute(
        "SELECT * FROM pending_words WHERE article_id=? AND paragraph_index=? AND word=?", (article_id, index, word)
    ).fetchone()
    db.commit()
    return jsonify(word=dict(row), duplicate=duplicate), 200 if duplicate else 201


@reading_flow.delete("/<int:article_id>/pending-words/<int:pending_id>")
def remove_pending(article_id, pending_id):
    article(article_id)
    # Idempotent: a second click or another tab may already have handled the item.
    db = get_db()
    db.execute("DELETE FROM pending_words WHERE article_id=? AND id=?", (article_id, pending_id))
    db.commit()
    return jsonify(ok=True)


@reading_flow.post("/<int:article_id>/pending-words/<int:pending_id>/collect")
def collect_pending(article_id, pending_id):
    data = payload()
    meaning = field(data, "meaning", 3000)
    phonetic = field(data, "phonetic", 150)
    part = field(data, "part_of_speech", 100)
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    entry = pending(article_id, pending_id)
    existing = db.execute("SELECT id FROM vocabulary WHERE word=?", (entry["word"],)).fetchone()
    if existing:
        word_id = existing["id"]
    else:
        if not meaning:
            raise ValueError("请先展开查询释义，或手动填写释义。")
        cursor = db.execute(
            """INSERT INTO vocabulary (word, meaning, phonetic, part_of_speech, context, article_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (entry["word"], meaning, phonetic, part, entry["context"], article_id, now()),
        )
        word_id = cursor.lastrowid
    ensure_word(db,word_id)
    attach_context(db,word_id,entry['context'],article_id)
    db.execute('UPDATE word_meta SET version=version+1 WHERE word_id=?',(word_id,))
    db.execute("DELETE FROM pending_words WHERE id=? AND article_id=?", (pending_id, article_id))
    db.commit()
    return jsonify(id=word_id, duplicate=bool(existing), ok=True)
