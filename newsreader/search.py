"""Local article filtering and optional semantic search."""
import hashlib
import json
import re
import os
from pathlib import Path
from threading import Lock

MODEL = "BAAI/bge-small-en-v1.5"
_model = None
_model_lock = Lock()


def get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        cache=Path(os.getenv('MARGIN_MODEL_DIR',Path.home()/'.cache'/'margin-models'))/'semantic'
        cache.mkdir(parents=True,exist_ok=True)
        _model = TextEmbedding(model_name=MODEL, threads=2, cache_dir=str(cache))
    return _model


def embed(texts, query=False):
    try:
        with _model_lock:
            model = get_model()
            vectors = model.query_embed(texts) if query else model.passage_embed(texts)
            return [v.tolist() for v in vectors]
    except Exception as exc:
        raise ValueError("语义模型暂不可用。首次使用需要联网下载英文模型；请检查网络或改用全文搜索。") from exc


def chunks(article):
    # Overlap preserves context near boundaries; every part of the article is indexed.
    text = (article['title'] + '\n' + article['content']).strip()
    size, step = 800, 640
    return [text[i:i + size] for i in range(0, len(text), step)] or ['']


def vectors_for(db, article):
    parts = chunks(article)
    old = {r['chunk_index']: r for r in db.execute('SELECT * FROM article_embeddings WHERE article_id=?', (article['id'],))}
    vectors, missing = [None] * len(parts), []
    for index, part in enumerate(parts):
        digest = hashlib.sha256(part.encode('utf-8')).hexdigest()
        row = old.get(index)
        if row and row['digest'] == digest and row['model'] == MODEL:
            vectors[index] = json.loads(row['vector'])
        else:
            missing.append((index, part, digest))
    if missing:
        fresh = embed([part for _, part, _ in missing])
        for (index, _, digest), vector in zip(missing, fresh):
            vectors[index] = vector
            db.execute('INSERT OR REPLACE INTO article_embeddings VALUES (?, ?, ?, ?, ?)',
                       (article['id'], index, digest, MODEL, json.dumps(vector)))
    db.execute('DELETE FROM article_embeddings WHERE article_id=? AND chunk_index>=?', (article['id'], len(parts)))
    db.commit()
    return vectors


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def snippet(content, query):
    match = re.search(re.escape(query), content, re.IGNORECASE) if query else None
    if not match:
        return ''
    start, end = max(0, match.start() - 65), min(len(content), match.end() + 90)
    return ('…' if start else '') + re.sub(r'\s+', ' ', content[start:end]) + ('…' if end < len(content) else '')


def number(args, key):
    raw = args.get(key, '').strip()
    if not raw:
        return None
    if not raw.isascii() or not raw.isdigit() or len(raw) > 7:
        raise ValueError('词数和时长必须是非负整数。')
    return int(raw)


def filtered_articles(db, args, with_page=False):
    q = args.get('q', '').strip()
    if len(q) > 200:
        raise ValueError('搜索词不能超过 200 个字符。')
    mode = args.get('mode', 'text')
    if mode not in ('text', 'semantic'):
        raise ValueError('搜索模式无效。')
    low_words, high_words = number(args, 'min_words'), number(args, 'max_words')
    low_time, high_time = number(args, 'min_minutes'), number(args, 'max_minutes')
    if (low_words is not None and high_words is not None and low_words > high_words) or (low_time is not None and high_time is not None and low_time > high_time):
        raise ValueError('筛选范围的最小值不能大于最大值。')
    status = args.get('status', '')
    if status not in ('', 'unread', 'reading', 'completed'):
        raise ValueError('阅读状态无效。')
    where, values = [], []
    library_status = args.get('library_status', 'all')
    if library_status not in ('all', 'active', 'archived', 'trashed', 'favorite', 'read_later'):
        raise ValueError('文章库状态无效。')
    if library_status in ('active', 'archived', 'trashed'):
        where.append('a.status=?'); values.append(library_status)
    else:
        where.append("a.status!='trashed'")
    if library_status in ('favorite', 'read_later'):
        where.append('a.' + library_status + '=1')
    priority = args.get('priority', '')
    if priority:
        if priority not in ('0', '1', '2', '3'): raise ValueError('优先级无效。')
        where.append('a.priority=?'); values.append(int(priority))
    for key in ('category', 'level', 'source'):
        if args.get(key): where.append('a.' + key + '=?'); values.append(args[key])
    # A connection-local Unicode fold preserves accented and CJK search behavior.
    db.create_function('casefold', 1, lambda s: str(s or '').casefold(), deterministic=True)
    if args.get('tag'):
        where.append('EXISTS(SELECT 1 FROM article_tags at JOIN tags t ON t.id=at.tag_id WHERE at.article_id=a.id AND casefold(t.name)=?)')
        values.append(args['tag'].casefold())
    for column, operator, value in (('word_count','>=',low_words),('word_count','<=',high_words),('reading_minutes','>=',low_time),('reading_minutes','<=',high_time)):
        if value is not None: where.append('a.'+column+operator+'?'); values.append(value)
    if status == 'unread': where.append("COALESCE(a.last_read_at,'')='' AND a.progress<=0")
    if status == 'reading': where.append("(COALESCE(a.last_read_at,'')!='' OR a.progress>0) AND a.progress<100")
    if status == 'completed': where.append('a.progress>=100')
    if q and mode == 'text':
        from .search_index import candidate_ids
        indexed=candidate_ids(db,q)
        if indexed is not None:
            where.append('(a.id IN (SELECT value FROM json_each(?)) OR a.id IN (SELECT at.article_id FROM article_tags at JOIN tags t ON t.id=at.tag_id WHERE instr(casefold(t.name),?)>0))')
            values.extend([json.dumps(indexed),q.casefold()])
        else:
            where.append("(instr(casefold(a.title||char(10)||a.subtitle||char(10)||a.source||char(10)||a.content),?)>0 OR EXISTS(SELECT 1 FROM article_tags at JOIN tags t ON t.id=at.tag_id WHERE at.article_id=a.id AND instr(casefold(t.name),?)>0))")
            values.extend([q.casefold(), q.casefold()])
    clause = ' AND '.join('(' + condition + ')' for condition in where)
    total = db.execute('SELECT count(*) FROM articles a WHERE ' + clause, values).fetchone()[0]
    limit, offset = number(args, 'limit'), number(args, 'offset') or 0
    if limit is not None and not 1 <= limit <= 200: raise ValueError('每页数量应为1–200。')
    sql = 'SELECT a.* FROM articles a WHERE ' + clause + ' ORDER BY a.created_at DESC,a.id ASC'
    # Semantic ranking must precede pagination; embeddings themselves are incremental.
    if limit is not None and not (q and mode == 'semantic'):
        sql += ' LIMIT ? OFFSET ?'; values.extend([limit, offset])
    rows = [dict(row) for row in db.execute(sql, values)]
    tags = {}
    ids = [row['id'] for row in rows]
    for row in db.execute('SELECT at.article_id, t.name AS tag FROM article_tags at JOIN tags t ON t.id=at.tag_id WHERE at.article_id IN (SELECT value FROM json_each(?)) ORDER BY t.name COLLATE NOCASE', (json.dumps(ids),)):
        tags.setdefault(row['article_id'], []).append(row['tag'])
    output = []
    for a in rows:
        a['tags'] = tags.get(a['id'], [])
        a['snippet'] = snippet(a['content'], q) if mode == 'text' else ''
        output.append(a)
    if q and mode == 'semantic' and output:
        query = embed([q], query=True)[0]
        for a in output:
            a['similarity'] = round(max(dot(query, vector) for vector in vectors_for(db, a)), 4)
        output.sort(key=lambda a: a['similarity'], reverse=True)
        if limit is not None: output = output[offset:offset+limit]
    for a in output:
        a.pop('content', None)
    return {'articles': output, 'total': total, 'offset': offset, 'limit': limit} if with_page else output


def similar_articles(db, article_id):
    rows = [dict(row) for row in db.execute("SELECT id, title, content, source, category, level FROM articles WHERE status!='trashed'")]
    target = next((a for a in rows if a['id'] == article_id), None)
    if target is None:
        return None
    if len(rows) == 1:
        return []
    original = vectors_for(db, target)
    results = []
    for article in rows:
        if article['id'] == article_id: continue
        other = vectors_for(db, article)
        article['similarity'] = round(max(dot(a, b) for a in original for b in other), 4)
        article.pop('content')
        results.append(article)
    return sorted(results, key=lambda a: a['similarity'], reverse=True)[:5]
