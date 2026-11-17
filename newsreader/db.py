"""SQLite persistence. All user data lives in instance/margin.sqlite3."""

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app, g

CHINA_TZ = timezone(timedelta(hours=8))

DEFAULT_CATEGORIES = ("科技", "文化", "科学", "生活", "环境", "其他")


def normalize_title(title):
    """Lowercase alphanumeric title used for duplicate detection."""
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def today():
    return datetime.now(CHINA_TZ).date()


def now():
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def get_db():
    if "db" not in g:
        db = sqlite3.connect(current_app.config["DATABASE"], timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        g.db = db
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


VOCABULARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS vocabulary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phonetic TEXT NOT NULL DEFAULT '', meaning TEXT NOT NULL,
    part_of_speech TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL,
    mastery TEXT NOT NULL DEFAULT 'new' CHECK(mastery IN ('new','learning','mastered')),
    created_at TEXT NOT NULL
);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, subtitle TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL, source TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '其他', author TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL, difficulty_score REAL NOT NULL,
    word_count INTEGER NOT NULL, reading_minutes INTEGER NOT NULL,
    created_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0,
    progress REAL NOT NULL DEFAULT 0, position REAL NOT NULL DEFAULT 0,
    reading_seconds INTEGER NOT NULL DEFAULT 0, last_read_at TEXT,
    completed_at TEXT,
    favorite INTEGER NOT NULL DEFAULT 0, read_later INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
    published_at TEXT NOT NULL DEFAULT '', title_norm TEXT NOT NULL DEFAULT ''
);
""" + VOCABULARY_SCHEMA + """
CREATE TABLE IF NOT EXISTS checkins (
    date TEXT PRIMARY KEY, reading_seconds INTEGER NOT NULL DEFAULT 0,
    articles_completed INTEGER NOT NULL DEFAULT 0, manual INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dictionary_cache (
    word TEXT PRIMARY KEY, result TEXT NOT NULL, cached_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS article_tags (
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (article_id, tag_id)
);
"""


def migrate_to_basic_schema(db):
    """Retain basic learning data when upgrading a database from the old build."""
    columns = {row["name"] for row in db.execute("PRAGMA table_info(vocabulary)")}
    has_legacy_table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_logs'"
    ).fetchone()
    if "due_date" not in columns and not has_legacy_table:
        return

    # Snapshot the original file before removing the obsolete schema.
    source_path = Path(current_app.config["DATABASE"])
    backup_path = source_path.with_name(source_path.stem + "-before-basic.sqlite3")
    if not backup_path.exists():
        with closing(sqlite3.connect(backup_path)) as backup:
            db.backup(backup)

    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("DROP TABLE IF EXISTS review_logs")
        if "due_date" in columns:
            db.execute(VOCABULARY_SCHEMA.replace("vocabulary (", "vocabulary_basic ("))
            retained = "id, word, phonetic, meaning, part_of_speech, context, note, article_id, mastery, created_at"
            db.execute(f"INSERT INTO vocabulary_basic ({retained}) SELECT {retained} FROM vocabulary")
            db.execute("DROP TABLE vocabulary")
            db.execute("ALTER TABLE vocabulary_basic RENAME TO vocabulary")
        db.execute("DELETE FROM settings WHERE key NOT IN ('seeded', 'font_size', 'line_height')")
        db.commit()
    except Exception:
        db.rollback()
        raise


def migrate_library_columns(db):
    """Add library-management columns to article tables from earlier builds."""
    columns = {row["name"] for row in db.execute("PRAGMA table_info(articles)")}
    additions = {
        "favorite": "INTEGER NOT NULL DEFAULT 0",
        "read_later": "INTEGER NOT NULL DEFAULT 0",
        "priority": "INTEGER NOT NULL DEFAULT 0",
        "status": "TEXT NOT NULL DEFAULT 'active'",
        "published_at": "TEXT NOT NULL DEFAULT ''",
        "title_norm": "TEXT NOT NULL DEFAULT ''",
    }
    for name, ddl in additions.items():
        if name not in columns:
            db.execute(f"ALTER TABLE articles ADD COLUMN {name} {ddl}")
    db.execute("CREATE INDEX IF NOT EXISTS idx_articles_title_norm ON articles(title_norm)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category)")
    for row in db.execute("SELECT id, title FROM articles WHERE title_norm=''"):
        db.execute("UPDATE articles SET title_norm=? WHERE id=?", (normalize_title(row["title"]), row["id"]))
    db.commit()


def seed_categories(db):
    existing = {row["name"] for row in db.execute("SELECT name FROM categories")}
    for name in DEFAULT_CATEGORIES:
        if name not in existing:
            db.execute("INSERT INTO categories (name, created_at) VALUES (?, ?)", (name, now()))
    db.commit()


def init_db():
    from .learning import assess_difficulty

    db = get_db()
    db.executescript(SCHEMA)
    migrate_to_basic_schema(db)
    migrate_library_columns(db)
    seed_categories(db)
    # A marker prevents deleted demonstration articles from reappearing on restart.
    if not db.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
        demo_path = Path(__file__).resolve().parent.parent / "data" / "articles.json"
        for article in json.loads(demo_path.read_text(encoding="utf-8")):
            metrics = assess_difficulty(article["content"])
            db.execute(
                """INSERT INTO articles (title, subtitle, content, source, category, author,
                level, difficulty_score, word_count, reading_minutes, created_at, is_demo, title_norm)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (article["title"], article["subtitle"], article["content"],
                 "Margin 原创示例", article["category"], "Margin Editorial",
                 metrics["level"], metrics["score"], metrics["word_count"],
                 metrics["reading_minutes"], now(), normalize_title(article["title"])),
            )
        db.execute("INSERT INTO settings (key, value) VALUES ('seeded', 'true')")
        db.commit()
