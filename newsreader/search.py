"""Local article filtering and optional semantic search."""
import hashlib
import json
import re
from threading import Lock

MODEL = "BAAI/bge-small-en-v1.5"
_model = None
_model_lock = Lock()


def get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=MODEL, threads=2)
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


def filtered_articles(db, args):
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
    rows = [dict(row) for row in db.execute('SELECT * FROM articles ORDER BY created_at DESC, id ASC')]
    tags = {}
    for row in db.execute('SELECT at.article_id, t.name AS tag FROM article_tags at JOIN tags t ON t.id=at.tag_id ORDER BY t.name COLLATE NOCASE'):
        tags.setdefault(row['article_id'], []).append(row['tag'])
    output = []
    for a in rows:
        a['tags'] = tags.get(a['id'], [])
        if args.get('category') and a['category'] != args['category']: continue
        if args.get('level') and a['level'] != args['level']: continue
        if args.get('source') and a['source'] != args['source']: continue
        if args.get('tag') and not any(t.casefold() == args['tag'].casefold() for t in a['tags']): continue
        if low_words is not None and a['word_count'] < low_words: continue
        if high_words is not None and a['word_count'] > high_words: continue
        if low_time is not None and a['reading_minutes'] < low_time: continue
        if high_time is not None and a['reading_minutes'] > high_time: continue
        if status == 'unread' and (a['last_read_at'] or a['progress'] > 0): continue
        if status == 'reading' and (not a['last_read_at'] and a['progress'] <= 0 or a['progress'] >= 100): continue
        if status == 'completed' and a['progress'] < 100: continue
        if q and mode == 'text' and not any(q.casefold() in str(a[field]).casefold() for field in ('title', 'subtitle', 'source', 'content')) and not any(q.casefold() in tag.casefold() for tag in a['tags']): continue
        a['snippet'] = snippet(a['content'], q) if mode == 'text' else ''
        output.append(a)
    if q and mode == 'semantic' and output:
        query = embed([q], query=True)[0]
        for a in output:
            a['similarity'] = round(max(dot(query, vector) for vector in vectors_for(db, a)), 4)
        output.sort(key=lambda a: a['similarity'], reverse=True)
    for a in output:
        a.pop('content', None)
    return output


def similar_articles(db, article_id):
    rows = [dict(row) for row in db.execute('SELECT id, title, content, source, category, level FROM articles')]
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
