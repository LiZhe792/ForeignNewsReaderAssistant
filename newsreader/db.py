"""SQLite persistence. All user data lives in instance/margin.sqlite3."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app, g
from .preferences import READER_DEFAULTS

CHINA_TZ = timezone(timedelta(hours=8))


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
    completed_at TEXT
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
    # A marker prevents deleted demonstration articles from reappearing on restart.
    if not db.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
        demo_path = Path(__file__).resolve().parent.parent / "data" / "articles.json"
        for article in json.loads(demo_path.read_text(encoding="utf-8")):
            metrics = assess_difficulty(article["content"])
            db.execute(
                """INSERT INTO articles (title, subtitle, content, source, category, author,
                level, difficulty_score, word_count, reading_minutes, created_at, is_demo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (article["title"], article["subtitle"], article["content"],
                 "Margin 原创示例", article["category"], "Margin Editorial",
                 metrics["level"], metrics["score"], metrics["word_count"],
                 metrics["reading_minutes"], now()),
            )
        db.execute("INSERT INTO settings (key, value) VALUES ('seeded', 'true')")
        db.commit()

    backfill_sources(db)
