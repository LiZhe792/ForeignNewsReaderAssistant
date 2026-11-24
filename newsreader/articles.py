"""Article creation shared by imports, subscriptions and batch ingestion."""

from flask import current_app

from .content import import_url
from .db import get_db, now, normalize_title
from .learning import assess_difficulty, words

CATEGORIES = ("科技", "文化", "科学", "生活", "环境", "其他")
MIN_WORDS = 40
MAX_BATCH = 10


def ensure_source(db, name, domain=""):
    """Return the id of a managed media source, creating it when absent."""
    name = (name or "").strip() or domain or "未知来源"
    row = db.execute("SELECT id, domain FROM sources WHERE name=?", (name,)).fetchone()
    if row:
        if domain and not row["domain"]:
            db.execute("UPDATE sources SET domain=? WHERE id=?", (domain, row["id"]))
        return row["id"]
    cursor = db.execute(
        "INSERT INTO sources (name, domain, created_at) VALUES (?, ?, ?)",
        (name, domain or "", now()),
    )
    return cursor.lastrowid


def insert_article(db, *, title, content, source, source_url="", category="其他",
                   subtitle="", author="", feed_id=None, guid=""):
    """Insert one article and return its id. Caller owns the transaction."""
    metrics = assess_difficulty(content)
    cursor = db.execute(
        """INSERT INTO articles (title, subtitle, content, source, source_url, category, author,
        level, difficulty_score, word_count, reading_minutes, created_at, feed_id, guid, title_norm)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, subtitle, content, source, source_url, category, author,
         metrics["level"], metrics["score"], metrics["word_count"],
         metrics["reading_minutes"], now(), feed_id, guid, normalize_title(title)),
    )
    return cursor.lastrowid


def existing_article(db, source_url):
    return db.execute("SELECT id FROM articles WHERE source_url=?", (source_url,)).fetchone()


def import_many(urls, category="其他", limit=MAX_BATCH):
    """Import a list of article links, reporting a result per link."""
    db = get_db()
    results, seen = [], set()
    for raw in urls[:limit]:
        url = (raw or "").strip()
        if not url:
            continue
        if url in seen:
            results.append({"url": url, "ok": True, "duplicate": True, "message": "本次列表里重复，已跳过。"})
            continue
        seen.add(url)
        try:
            article = import_url(url)
        except ValueError as exc:  # includes FetchError; one bad link must not stop the batch
            results.append({"url": url, "ok": False, "error": str(exc)})
            continue
        duplicate = existing_article(db, article["source_url"])
        if duplicate:
            results.append({"url": url, "ok": True, "id": duplicate["id"], "duplicate": True,
                            "message": "文章已在库中。"})
            continue
        if len(words(article["content"])) < MIN_WORDS:
            results.append({"url": url, "ok": False, "error": "正文不足 40 个英文单词。"})
            continue
        article_id = insert_article(
            db, title=article["title"], content=article["content"], source=article["source"],
            source_url=article["source_url"], category=category,
        )
        ensure_source(db, article["source"])
        results.append({"url": url, "ok": True, "id": article_id, "duplicate": False,
                        "message": "已导入。"})
    if any(item.get("ok") and not item.get("duplicate") for item in results):
        db.commit()
    else:
        db.rollback()
    current_app.logger.info("batch import finished: %s links", len(results))
    return results
