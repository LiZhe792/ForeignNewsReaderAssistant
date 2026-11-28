"""Article library management: categories, tags, dedup and batch operations."""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .db import now, normalize_title

PRIORITIES = (0, 1, 2, 3)
STATUSES = ("active", "archived", "trashed")


def canonical_url(value):
    """Ignore tracking parameters without discarding meaningful article IDs."""
    if not value:
        return ''
    parsed = urlsplit(value.strip())
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('原文链接必须是 HTTP / HTTPS 地址。')
    port = parsed.port
    host = parsed.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    if port and (parsed.scheme, port) not in (('http', 80), ('https', 443)):
        host += ':' + str(port)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or '/', urlencode(sorted(query)), ''))


def content_fingerprint(content):
    return hashlib.sha256(re.sub(r'\s+', ' ', content).strip().encode('utf-8')).hexdigest()


def find_duplicate(db, title, source_url="", content=None):
    """Only reuse an identical source or identical title AND body, never title alone."""
    if source_url:
        url = canonical_url(source_url)
        for row in db.execute("SELECT id,source_url FROM articles WHERE source_url!=''"):
            try:
                if canonical_url(row['source_url']) == url:
                    return row['id']
            except ValueError:
                continue
    norm = normalize_title(title)
    if norm and content is not None:
        digest = content_fingerprint(content)
        for row in db.execute("SELECT id,content FROM articles WHERE title_norm=?", (norm,)):
            if content_fingerprint(row['content']) == digest:
                return row['id']
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
