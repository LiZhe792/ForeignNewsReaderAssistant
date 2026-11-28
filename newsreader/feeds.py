"""RSS/Atom subscriptions: parsing, keyword filters and article ingestion.

Feeds are fetched through the same public-URL client as article imports, so
subscriptions inherit the redirect, DNS and download-size boundaries.
"""

import email.utils
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .articles import ensure_source, existing_article, insert_article
from .content import import_url
from .db import get_db, now
from .learning import words
from .network import FetchError, fetch_document

MAX_ENTRIES = 10
MAX_PAGE_FETCHES = 4


class FeedError(ValueError):
    """A subscription that cannot be parsed or completed."""


def keyword_list(value):
    return [item.strip() for item in re.split(r"[,，;；、]", value or "") if item.strip()]


def matches_keywords(entry, keywords):
    if not keywords:
        return True
    haystack = f"{entry.get('title', '')} {entry.get('content', '')}".lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def _strip_namespaces(root):
    for element in root.iter():
        if isinstance(element.tag, str):
            element.tag = element.tag.rsplit("}", 1)[-1]
        element.attrib = {key.rsplit("}", 1)[-1]: value for key, value in element.attrib.items()}


def _text(node, *names):
    for name in names:
        child = node.find(name)
        if child is not None:
            value = "".join(child.itertext()).strip()
            if value:
                return value
    return ""


def _html(node, *names):
    """Return the raw inner markup of the first matching child."""
    for name in names:
        child = node.find(name)
        if child is None:
            continue
        markup = "".join(ET.tostring(item, encoding="unicode") for item in child) or (child.text or "")
        if markup.strip():
            return markup
    return ""


def html_to_text(fragment):
    if not fragment:
        return ""
    soup = BeautifulSoup(fragment, "html.parser")
    for node in soup.select("script, style"):
        node.decompose()
    blocks = [re.sub(r"\s+", " ", item.get_text(" ", strip=True)) for item in soup.find_all(["p", "h2", "h3", "li"])]
    blocks = [block for block in blocks if block]
    if blocks:
        return "\n\n".join(blocks)
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def _rss_date(value):
    if not value:
        return ""
    try:
        return email.utils.parsedate_to_datetime(value).astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, IndexError):
        return ""


def _atom_date(value):
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _entry(title, link, content, guid, published):
    return {"title": title.strip()[:250] or "未命名文章", "link": link,
            "content": html_to_text(content), "guid": guid, "published": published}


def _parse_rss(root, entries):
    channel = root.find("channel")
    if channel is None:
        channel = root
    for item in channel.findall("item"):
        link = _text(item, "link") or _text(item, "guid")
        entries.append(_entry(
            title=_text(item, "title"),
            link=link,
            content=_html(item, "encoded", "description") or _text(item, "encoded", "description"),
            guid=_text(item, "guid") or link,
            published=_rss_date(_text(item, "pubDate", "date")),
        ))
    return {"title": _text(channel, "title") or "未命名订阅源",
            "site_url": _text(channel, "link"), "entries": entries}


def _parse_atom(root, entries):
    site_url = ""
    for link in root.findall("link"):
        if link.get("rel") in (None, "alternate"):
            site_url = link.get("href", "")
            break
    for entry in root.findall("entry"):
        link = ""
        for node in entry.findall("link"):
            if node.get("rel") in (None, "alternate"):
                link = node.get("href", "")
                break
        if not link:
            link = _text(entry, "id")
        entries.append(_entry(
            title=_text(entry, "title"),
            link=link,
            content=_html(entry, "content", "summary") or _text(entry, "content", "summary"),
            guid=_text(entry, "id") or link,
            published=_atom_date(_text(entry, "published", "updated")),
        ))
    return {"title": _text(root, "title") or "未命名订阅源", "site_url": site_url, "entries": entries}


def parse_feed(raw):
    """Parse RSS 2.0, RSS 1.0 (RDF) or Atom into a plain dictionary."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise FeedError("订阅源不是有效的 RSS 或 Atom 内容，请检查链接。") from exc
    _strip_namespaces(root)
    entries = []
    if root.tag == "feed":
        parsed = _parse_atom(root, entries)
    elif root.tag in ("rss", "RDF") or root.find("channel") is not None:
        parsed = _parse_rss(root, entries)
    else:
        raise FeedError("无法识别该订阅源格式，请使用 RSS 或 Atom 链接。")
    parsed["entries"] = [entry for entry in parsed["entries"] if entry["link"]][:MAX_ENTRIES]
    if not parsed["entries"]:
        raise FeedError("订阅源里没有找到文章，可能已停止更新。")
    return parsed


def _mark_seen(db, feed_id, guid):
    if guid:
        db.execute("INSERT OR IGNORE INTO feed_seen (feed_id, guid, first_seen) VALUES (?, ?, ?)",
                   (feed_id, guid, now()))


def _update_feed(db, feed_id, *, status, error="", etag=None, last_modified=None, fetched=False):
    db.execute(
        """UPDATE feeds SET last_status=?, last_error=?, etag=COALESCE(?, etag),
        last_modified=COALESCE(?, last_modified),
        last_fetched_at=CASE WHEN ? THEN ? ELSE last_fetched_at END WHERE id=?""",
        (status, error, etag, last_modified, 1 if fetched else 0, now(), feed_id),
    )
    db.commit()


def refresh_feed(feed, limit=MAX_ENTRIES):
    """Fetch one feed and import new entries. Returns a small result summary."""
    db = get_db()
    conditional = {}
    if feed["etag"]:
        conditional["If-None-Match"] = feed["etag"]
    if feed["last_modified"]:
        conditional["If-Modified-Since"] = feed["last_modified"]
    try:
        status, raw, headers, _content_type, _final_url = fetch_document(
            feed["url"], max_bytes=2_000_000, extra_headers=conditional, allow_not_modified=True)
    except FetchError as exc:
        _update_feed(db, feed["id"], status="error", error=str(exc), fetched=True)
        raise
    if status == 304:
        _update_feed(db, feed["id"], status="ok", error="", fetched=True)
        return {"added": 0, "skipped": 0, "unchanged": True}
    try:
        parsed = parse_feed(raw)
    except FeedError as exc:
        _update_feed(db, feed["id"], status="error", error=str(exc), fetched=True)
        raise

    source_row = db.execute("SELECT name FROM sources WHERE id=?", (feed["source_id"],)).fetchone()
    source_name = source_row["name"] if source_row else (urlsplit(feed["url"]).hostname or "订阅源")
    keywords = keyword_list(feed["keywords"])
    added = skipped = pages = 0
    for entry in parsed["entries"][:limit]:
        guid = entry["guid"]
        if guid and db.execute("SELECT 1 FROM feed_seen WHERE feed_id=? AND guid=?",
                               (feed["id"], guid)).fetchone():
            skipped += 1
            continue
        if existing_article(db, entry["link"]):
            skipped += 1
            _mark_seen(db, feed["id"], guid)
            continue
        if not matches_keywords(entry, keywords):
            skipped += 1
            _mark_seen(db, feed["id"], guid)
            continue
        content = entry["content"]
        if len(words(content)) < 40 and pages < MAX_PAGE_FETCHES:
            pages += 1
            try:
                content = import_url(entry["link"])["content"]
            except (FetchError, ValueError):
                content = ""
        if len(words(content)) < 40:
            skipped += 1
            _mark_seen(db, feed["id"], guid)
            continue
        insert_article(
            db, title=entry["title"], content=content, source=source_name,
            source_url=entry["link"], category=feed["category"],
            feed_id=feed["id"], guid=guid,
        )
        _mark_seen(db, feed["id"], guid)
        added += 1
    db.commit()
    _update_feed(db, feed["id"], status="ok", error="",
                 etag=headers.get("etag"), last_modified=headers.get("last-modified"), fetched=True)
    return {"added": added, "skipped": skipped, "unchanged": False}


def subscribe(url, *, category="其他", source_name="", keywords="", title=""):
    """Validate a feed, register its media source and import its latest entries."""
    db = get_db()
    url = (url or "").strip()
    if db.execute("SELECT id FROM feeds WHERE url=?", (url,)).fetchone():
        raise FeedError("这个订阅源已经添加过了。")
    try:
        _status, raw, headers, _content_type, _final_url = fetch_document(url, max_bytes=2_000_000)
    except FetchError as exc:
        raise FeedError(str(exc)) from exc
    parsed = parse_feed(raw)
    host = urlsplit(url).hostname or ""
    media = (source_name or "").strip() or parsed["title"][:100] or host
    source_id = ensure_source(db, media, host)
    cursor = db.execute(
        """INSERT INTO feeds (source_id, title, url, site_url, category, keywords,
        etag, last_modified, last_fetched_at, last_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?)""",
        (source_id, (title or "").strip()[:250] or parsed["title"][:250], url,
         parsed["site_url"] or host, category, ",".join(keyword_list(keywords)),
         headers.get("etag", ""), headers.get("last-modified", ""), now(), now()),
    )
    db.commit()
    feed = db.execute("SELECT * FROM feeds WHERE id=?", (cursor.lastrowid,)).fetchone()
    result = refresh_feed(feed)
    return {"id": cursor.lastrowid, "title": feed["title"], "source": media, **result}
