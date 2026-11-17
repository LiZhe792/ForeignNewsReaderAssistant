"""Article library management: categories, tags, dedup and batch operations."""

from .db import DEFAULT_CATEGORIES, get_db, now, normalize_title

PRIORITIES = (0, 1, 2, 3)
PRIORITY_LABELS = {0: "无优先级", 1: "低", 2: "中", 3: "高"}
STATUSES = ("active", "archived", "trashed")
STATUS_LABELS = {"active": "未归档", "archived": "已归档", "trashed": "回收站"}


def find_duplicate(db, title, source_url=""):
    """Return an existing article id that matches the URL or normalized title."""
    if source_url:
        row = db.execute("SELECT id FROM articles WHERE source_url=?", (source_url,)).fetchone()
        if row:
            return row["id"]
    norm = normalize_title(title)
    if norm:
        row = db.execute("SELECT id FROM articles WHERE title_norm=? LIMIT 1", (norm,)).fetchone()
        if row:
            return row["id"]
    return None


def ensure_tag(db, name):
    name = (name or "").strip()
    if not name:
        raise ValueError("标签不能为空。")
    if len(name) > 30:
        raise ValueError("标签长度不能超过 30 个字符。")
    row = db.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    cursor = db.execute("INSERT INTO tags (name, created_at) VALUES (?, ?)", (name, now()))
    return cursor.lastrowid


def set_article_tags(db, article_id, names):
    """Replace an article's tag set, creating tags on demand."""
    cleaned = list(dict.fromkeys((name or "").strip() for name in names if (name or "").strip()))
    db.execute("DELETE FROM article_tags WHERE article_id=?", (article_id,))
    for name in cleaned:
        db.execute("INSERT OR IGNORE INTO article_tags (article_id, tag_id) VALUES (?, ?)",
                   (article_id, ensure_tag(db, name)))
    return cleaned


def tags_for_articles(db):
    """Return a mapping of article_id -> sorted tag names."""
    rows = db.execute(
        "SELECT at.article_id, t.name FROM article_tags at JOIN tags t ON t.id=at.tag_id ORDER BY t.name"
    ).fetchall()
    result = {}
    for row in rows:
        result.setdefault(row["article_id"], []).append(row["name"])
    return result
