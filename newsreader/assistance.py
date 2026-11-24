"""Grounded selections, cached AI learning results and versioned translation edits."""
import hashlib
import json
import re
from contextlib import contextmanager
from threading import Lock

from flask import Blueprint, abort, current_app, jsonify, request

from . import ai
from .db import get_db, now

assistance = Blueprint('assistance', __name__)
MAX_UNIT = 2800
_inflight = set()
_inflight_lock = Lock()


@contextmanager
def generation_guard(key):
    identity = (current_app.config['DATABASE'], key)
    with _inflight_lock:
        if identity in _inflight:
            raise ai.AIError('这个片段正在生成，请稍后重试或重新读取已保存结果。', 409)
        _inflight.add(identity)
    try:
        yield
    finally:
        with _inflight_lock:
            _inflight.discard(identity)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def paragraphs(content):
    return [p.strip() for p in re.split(r'\n\s*\n|\n', content) if p.strip()]


def spans(text, maximum=MAX_UNIT):
    start = 0
    while start < len(text):
        end = min(start + maximum, len(text))
        if end < len(text):
            cut = max(text.rfind('. ', start + maximum // 2, end), text.rfind(' ', start + maximum // 2, end))
            if cut > start:
                end = cut + 1
        while start < end and text[start].isspace():
            start += 1
        trimmed_end = end
        while trimmed_end > start and text[trimmed_end - 1].isspace():
            trimmed_end -= 1
        if trimmed_end > start:
            yield {'start': start, 'end': trimmed_end}
        start = end


def sentences(text):
    return [{'start': match.start() + len(match.group()) - len(match.group().lstrip()),
             'end': match.end() - len(match.group()) + len(match.group().rstrip())}
            for match in re.finditer(r'[^.!?]+[.!?]+(?:["\u201d\u2019\']+)?|[^.!?]+$', text)
            if match.group().strip()]


def article(article_id):
    row = get_db().execute('SELECT id, title, content FROM articles WHERE id=?', (article_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def body():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError('请求内容必须是 JSON 对象。')
    return data


def serialize(row, content_hash):
    result = dict(row)
    result['payload'] = json.loads(result.pop('payload_json'))
    result['original'] = json.loads(result.pop('original_json'))
    result['favorite'] = bool(result['favorite'])
    result['edited'] = bool(result['edited'])
    result['stale'] = result['article_hash'] != content_hash
    result.pop('request_key')
    return result


@assistance.errorhandler(ai.AIError)
def ai_error(error):
    return jsonify(error=str(error)), error.status


@assistance.get('/api/assistance/status')
def service_status():
    return jsonify(ai.status())


@assistance.get('/api/articles/<int:article_id>/assistance')
def manifest(article_id):
    source = article(article_id)
    hashed = digest(source['content'])
    parts = paragraphs(source['content'])
    rows = get_db().execute('SELECT * FROM study_results WHERE article_id=? ORDER BY id', (article_id,)).fetchall()
    return jsonify(article_hash=hashed, profile=ai.profile(),
                   paragraphs=[{'text': text, 'sentences': sentences(text), 'units': list(spans(text))} for text in parts],
                   results=[serialize(row, hashed) for row in rows])


@assistance.post('/api/articles/<int:article_id>/assistance')
def create_result(article_id):
    source = article(article_id)
    data = body()
    if set(data) - {'kind', 'scope', 'paragraph_index', 'start', 'end', 'article_hash', 'level'}:
        raise ValueError('学习请求包含不支持的字段。')
    kind, scope = data.get('kind'), data.get('scope')
    if kind not in ('context', 'translation') or scope not in ('word', 'sentence', 'paragraph'):
        raise ValueError('请选择有效的学习任务与原文范围。')
    level = data.get('level', 'intermediate')
    if level not in ai.LEVELS:
        raise ValueError('请选择有效的学习水平。')
    hashed = digest(source['content'])
    if data.get('article_hash') != hashed:
        return jsonify(error='文章内容已变化，请重新打开学习面板。'), 409
    parts = paragraphs(source['content'])
    index, start, end = (data.get(key) for key in ('paragraph_index', 'start', 'end'))
    if type(index) is not int or not 0 <= index < len(parts):
        raise ValueError('段落编号无效。')
    paragraph = parts[index]
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(paragraph):
        raise ValueError('原文范围无效。')
    selected = paragraph[start:end]
    if not selected.strip() or len(selected) > MAX_UNIT or (scope == 'word' and len(selected) > 120):
        raise ValueError('请选择较短的单词、句子或原文片段；长段落请使用分段翻译。')
    config = ai.configuration()
    key = digest(json.dumps([ai.PROMPT_VERSION, article_id, hashed, kind, scope, index, start, end, level,
                             config['base'], config['model']], ensure_ascii=False))
    db = get_db()
    cached = db.execute('SELECT * FROM study_results WHERE request_key=?', (key,)).fetchone()
    if cached:
        return jsonify(result=serialize(cached, hashed), cached=True)
    with generation_guard(key):
        cached = db.execute('SELECT * FROM study_results WHERE request_key=?', (key,)).fetchone()
        if cached:
            return jsonify(result=serialize(cached, hashed), cached=True)
        # A bounded slice centered on the selected occurrence, plus nearby paragraphs.
        nearby = paragraph[max(0, start - 1800):min(len(paragraph), end + 1800)]
        before = parts[index - 1][-1200:] if index else ''
        after = parts[index + 1][:1200] if index + 1 < len(parts) else ''
        context = '\n\n'.join(filter(None, (before, nearby, after)))
        generated = ai.generate(kind, selected, context, source['title'], level)
        # External requests do not hold a write lock. Check the article again before
        # persisting, so a slow response cannot resurrect a deleted or edited source.
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT content FROM articles WHERE id=?', (article_id,)).fetchone()
        if current is None:
            db.rollback()
            abort(404)
        if digest(current['content']) != hashed:
            db.rollback()
            return jsonify(error='文章内容已变化，未保存过期结果。请重新打开面板。'), 409
        packed = json.dumps(generated, ensure_ascii=False)
        db.execute('''INSERT INTO study_results
            (request_key, article_id, article_hash, kind, scope, paragraph_index, start, end,
             source_text, level, model, profile, payload_json, original_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_key) DO NOTHING''',
            (key, article_id, hashed, kind, scope, index, start, end, selected, level, config['model'], ai.profile(), packed, packed, now(), now()))
        db.commit()
        row = db.execute('SELECT * FROM study_results WHERE request_key=?', (key,)).fetchone()
        return jsonify(result=serialize(row, hashed), cached=False), 201

@assistance.patch('/api/articles/<int:article_id>/assistance/<int:result_id>')
def update_result(article_id, result_id):
    source = article(article_id)
    data = body()
    allowed = {'version', 'favorite', 'literal', 'natural', 'note'}
    if set(data) - allowed or not (set(data) - {'version'}):
        raise ValueError('请选择收藏或填写需要修订的译文。')
    if type(data.get('version')) is not int or data['version'] < 1:
        raise ValueError('记录版本无效，请重新打开学习面板。')
    if 'favorite' in data and type(data['favorite']) is not bool:
        raise ValueError('收藏开关必须为 true 或 false。')
    for field in ('literal', 'natural', 'note'):
        if field in data and (not isinstance(data[field], str) or len(data[field]) > (2000 if field == 'note' else 8000)
                              or (field != 'note' and not data[field].strip())):
            raise ValueError('请填写有效译文或不超过 2000 字的修订笔记。')
    db = get_db()
    # Version check and update belong to the same transaction across tabs.
    db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT * FROM study_results WHERE id=? AND article_id=?', (result_id, article_id)).fetchone()
    if row is None:
        db.rollback()
        abort(404)
    hashed = digest(source['content'])
    if row['version'] != data['version']:
        db.rollback()
        return jsonify(error='记录已被另一页面更新。已载入最新版本，你的输入仍保留，请核对后再保存。',
                       current=serialize(row, hashed)), 409
    if row['kind'] != 'translation' and any(field in data for field in ('literal', 'natural', 'note', 'favorite')):
        db.rollback()
        raise ValueError('此操作仅适用于译文。')
    payload, original = json.loads(row['payload_json']), json.loads(row['original_json'])
    for field in ('literal', 'natural'):
        if field in data:
            payload[field] = data[field].strip()
    edited = any(payload[field] != original[field] for field in ('literal', 'natural'))
    db.execute('''UPDATE study_results SET payload_json=?, note=?, favorite=?, edited=?,
        version=version+1, updated_at=? WHERE id=?''',
        (json.dumps(payload, ensure_ascii=False), data.get('note', row['note']).strip(),
         int(data.get('favorite', row['favorite'])), int(edited), now(), result_id))
    db.commit()
    updated = db.execute('SELECT * FROM study_results WHERE id=?', (result_id,)).fetchone()
    return jsonify(result=serialize(updated, hashed))
