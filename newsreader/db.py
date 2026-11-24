"""SQLite persistence. All user data lives in instance/margin.sqlite3."""

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app, g
from .preferences import READER_DEFAULTS

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
        db.create_function("margin_now", 0, lambda: now())
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
CREATE TABLE IF NOT EXISTS writing_exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL, source_kind TEXT NOT NULL, source_text TEXT NOT NULL,
    target_words TEXT NOT NULL DEFAULT '[]', focus TEXT NOT NULL DEFAULT '',
    draft TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS writing_exercises_article ON writing_exercises(article_id, id DESC);
CREATE TABLE IF NOT EXISTS writing_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exercise_id INTEGER NOT NULL REFERENCES writing_exercises(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL UNIQUE, original TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','complete','failed')),
    feedback TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS writing_attempts_exercise ON writing_attempts(exercise_id, id);
CREATE TABLE IF NOT EXISTS dictionary_cache (
    word TEXT PRIMARY KEY, result TEXT NOT NULL, cached_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tts_playlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT, item_key TEXT NOT NULL UNIQUE,
    article_id INTEGER REFERENCES articles(id) ON DELETE CASCADE,
    scope TEXT NOT NULL CHECK(scope IN ('word','sentence','paragraph','article')),
    unit_index INTEGER NOT NULL DEFAULT 0, word TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL, position INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    paragraph_index INTEGER NOT NULL CHECK(paragraph_index >= -1),
    word TEXT NOT NULL COLLATE NOCASE,
    context TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
    UNIQUE(article_id, paragraph_index, word)
);
CREATE TABLE IF NOT EXISTS study_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_key TEXT NOT NULL UNIQUE,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    article_hash TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('context','translation')),
    scope TEXT NOT NULL, paragraph_index INTEGER NOT NULL,
    start INTEGER NOT NULL, end INTEGER NOT NULL, source_text TEXT NOT NULL,
    level TEXT NOT NULL, model TEXT NOT NULL, profile TEXT NOT NULL,
    payload_json TEXT NOT NULL, original_json TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', favorite INTEGER NOT NULL DEFAULT 0,
    edited INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS study_results_article ON study_results(article_id, kind);
"""


SCHEMA += """
CREATE TABLE IF NOT EXISTS qa_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS qa_threads_article ON qa_threads(article_id, id DESC);
CREATE TABLE IF NOT EXISTS qa_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL REFERENCES qa_threads(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL UNIQUE,
    question TEXT NOT NULL, mode TEXT NOT NULL, scope TEXT NOT NULL,
    selected_text TEXT NOT NULL DEFAULT '', selection_paragraphs TEXT NOT NULL DEFAULT '[]',
    answer TEXT NOT NULL DEFAULT '', citations TEXT NOT NULL DEFAULT '[]',
    questions TEXT NOT NULL DEFAULT '[]', warning TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('pending','complete','failed')),
    error TEXT NOT NULL DEFAULT '', favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0,1)),
    model TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS qa_turns_thread ON qa_turns(thread_id, id);
CREATE TABLE IF NOT EXISTS knowledge_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE,
    source_key TEXT UNIQUE, kind TEXT NOT NULL,
    article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL,
    article_title TEXT NOT NULL DEFAULT '', anchors TEXT NOT NULL DEFAULT '[]',
    title TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', example TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT 'yellow', tags TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_items_article ON knowledge_items(article_id, id);
CREATE INDEX IF NOT EXISTS knowledge_items_kind ON knowledge_items(kind, id DESC);
CREATE TABLE IF NOT EXISTS knowledge_maps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL, nodes TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS article_embeddings (
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL, digest TEXT NOT NULL, model TEXT NOT NULL,
    vector TEXT NOT NULL, PRIMARY KEY(article_id, chunk_index)
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    domain TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_seen (
    feed_id INTEGER NOT NULL, guid TEXT NOT NULL, first_seen TEXT NOT NULL,
    PRIMARY KEY (feed_id, guid)
);
CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
    title TEXT NOT NULL, url TEXT NOT NULL UNIQUE, site_url TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '其他', keywords TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    etag TEXT NOT NULL DEFAULT '', last_modified TEXT NOT NULL DEFAULT '',
    last_fetched_at TEXT, last_status TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
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
CREATE INDEX IF NOT EXISTS article_tags_tag ON article_tags(tag_id);
"""


def migrate_article_columns(db):
    """Add feed bookkeeping columns to article tables created by earlier builds."""
    columns = {row["name"] for row in db.execute("PRAGMA table_info(articles)")}
    if "feed_id" not in columns:
        db.execute("ALTER TABLE articles ADD COLUMN feed_id INTEGER")
    if "guid" not in columns:
        db.execute("ALTER TABLE articles ADD COLUMN guid TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_articles_source_url ON articles(source_url)")
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_feed_guid ON articles(feed_id, guid) "
        "WHERE guid IS NOT NULL AND guid != ''"
    )
    db.commit()


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
        retained_settings = ["seeded", *READER_DEFAULTS, "srs.new_limit", "srs.review_limit"]
        placeholders = ",".join("?" for _ in retained_settings)
        db.execute(f"DELETE FROM settings WHERE key NOT IN ({placeholders})", retained_settings)
        db.commit()
    except Exception:
        db.rollback()
        raise


def backfill_sources(db):
    """Register every media source that already owns articles."""
    db.execute(
        """INSERT OR IGNORE INTO sources (name, domain, created_at)
        SELECT DISTINCT source, '', ? FROM articles WHERE source != ''""",
        (now(),),
    )
    db.commit()


def init_db():
    from .learning import assess_difficulty

    db = get_db()
    db.executescript(SCHEMA)
    migrate_to_basic_schema(db)
    migrate_library_columns(db)
    migrate_article_columns(db)
    seed_categories(db)
    from .wordbook import initialize
    initialize(db)
    from .training import SCHEMA as TRAINING_SCHEMA
    from .exercises import SCHEMA as EXERCISE_SCHEMA
    from .personalization import SCHEMA as PERSONALIZATION_SCHEMA
    db.executescript(TRAINING_SCHEMA + EXERCISE_SCHEMA + PERSONALIZATION_SCHEMA)
    from .analytics import initialize as initialize_analytics
    initialize_analytics(db)
    from .study_plans import SCHEMA as PLAN_SCHEMA
    db.executescript(PLAN_SCHEMA)
    from .learning_lab import SCHEMA as LAB_SCHEMA
    db.executescript(LAB_SCHEMA)
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

    backfill_sources(db)


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
