"""Disposable trigram FTS cache with durable, versioned invalidation receipts."""
import sqlite3
from pathlib import Path
from threading import RLock
from flask import current_app

_lock=RLock()
SCHEMA='''
CREATE TABLE IF NOT EXISTS search_changes(article_id INTEGER PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS articles_page_order ON articles(created_at DESC,id);
'''


def install(db):
    db.executescript(SCHEMA)
    for action,reference in (('INSERT','new'),('UPDATE OF title,subtitle,source,content','new'),('DELETE','old')):
        name=action.split()[0].lower()
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS search_article_{name} AFTER {action} ON articles BEGIN
            INSERT INTO search_changes(article_id,revision) VALUES({reference}.id,random())
            ON CONFLICT(article_id) DO UPDATE SET revision=random(); END''')
    db.commit()


def candidate_ids(db,query):
    """None means FTS is unavailable/too short; callers use Unicode SQL search.

    The cache is never part of authoritative data. Restores change data_state's
    generation. Concurrent edits retain their journal receipt unless its version
    exactly matches the version that was indexed.
    """
    if len(query)<3:return None
    path=Path(current_app.config['DATABASE']).with_suffix('.search-cache.sqlite3')
    try:
        with _lock:
            cache=sqlite3.connect(path,timeout=15)
            try:
                cache.execute("CREATE VIRTUAL TABLE IF NOT EXISTS documents USING fts5(body, tokenize='trigram')")
                cache.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT)')
                source=db.execute('SELECT generation FROM data_state WHERE id=1').fetchone()[0]
                cached=cache.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()
                changes=[tuple(row) for row in db.execute('SELECT article_id,revision FROM search_changes')]
                if not cached or cached[0]!=source:
                    cache.execute('DELETE FROM documents')
                    rows=db.execute('SELECT id,title,subtitle,source,content FROM articles')
                    cache.executemany('INSERT INTO documents(rowid,body) VALUES(?,?)',((r['id'],'\n'.join(r[k] for k in ('title','subtitle','source','content')).casefold()) for r in rows))
                    cache.execute("INSERT OR REPLACE INTO metadata VALUES('generation',?)",(source,))
                else:
                    for identity,revision in changes:
                        cache.execute('DELETE FROM documents WHERE rowid=?',(identity,))
                        row=db.execute('SELECT title,subtitle,source,content FROM articles WHERE id=?',(identity,)).fetchone()
                        if row:cache.execute('INSERT INTO documents(rowid,body) VALUES(?,?)',(identity,'\n'.join(row).casefold()))
                cache.commit()
                # Clear only receipts that were observed before indexing.
                db.executemany('DELETE FROM search_changes WHERE article_id=? AND revision=?',changes)
                db.commit()
                term='"'+query.casefold().replace('"','""')+'"'
                return [r[0] for r in cache.execute('SELECT rowid FROM documents WHERE documents MATCH ?',(term,))]
            finally:cache.close()
    except (sqlite3.Error,OSError):
        # Normal search still works on platforms built without FTS5/trigram.
        return None
