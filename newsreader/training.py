"""Sentence listening, local recording storage and transparent practice feedback."""
import base64
import binascii
import hashlib
import json
import re
from difflib import SequenceMatcher

from flask import Blueprint, Response, abort, jsonify, request
from .assistance import paragraphs
from .db import get_db, now

training = Blueprint('training', __name__, url_prefix='/api/training')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS listening_audio (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL,
 article_hash TEXT NOT NULL, paragraph_index INTEGER NOT NULL, unit_index INTEGER NOT NULL,
 source_text TEXT NOT NULL, mime TEXT NOT NULL, data BLOB NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(article_id,article_hash,paragraph_index,unit_index));
CREATE TABLE IF NOT EXISTS listening_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
 article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL, article_title TEXT NOT NULL,
 article_hash TEXT NOT NULL, paragraph_index INTEGER NOT NULL, unit_index INTEGER NOT NULL,
 source_text TEXT NOT NULL, mode TEXT NOT NULL, attempt TEXT NOT NULL,
 duration_ms INTEGER NOT NULL, voiced_ms INTEGER NOT NULL, pause_count INTEGER NOT NULL,
 feedback_json TEXT NOT NULL, mime TEXT NOT NULL DEFAULT '', audio BLOB,
 created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS listening_article ON listening_attempts(article_id,id);
'''
MODES = ('dictation', 'keywords', 'readalong', 'shadow')
MAX_AUDIO = 6 * 1024 * 1024


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def body():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError('请求内容必须为 JSON 对象。')
    return data


def text(data, key, maximum, required=False):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError('请检查文本是否为空或超过长度限制。')
    return value.strip()


def integer(data, key, minimum, maximum, default=None):
    value = data.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('编号、时长或数量超出允许范围。')
    return value


def token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', value):
        raise ValueError('请求编号无效，请重新打开练习。')
    return value


def normalize(value):
    return re.sub(r'\s+', ' ', value.strip().lower().replace('’', "'"))


def words(value):
    return re.findall(r"[A-Za-z]+(?:['’\-][A-Za-z]+)*|\d+(?:\.\d+)?", value)


def units(paragraph):
    """Bounded, exact source slices; common abbreviations do not create fragments."""
    boundaries = []
    for match in re.finditer(r'[.!?]+["”’\']*(?=\s|$)', paragraph):
        prior = re.search(r'([A-Za-z.]+)[.!?]+["”’\']*$', paragraph[:match.end()])
        previous = prior.group(1).rstrip('.') if prior else ''
        if previous.lower() in ('mr', 'mrs', 'ms', 'dr', 'prof', 'e.g', 'i.e', 'u.s', 'u.k') or len(previous) == 1:
            continue
        boundaries.append(match.end())
    boundaries.append(len(paragraph))
    result, start = [], 0
    for end in sorted(set(boundaries)):
        while start < end and paragraph[start].isspace():
            start += 1
        while start < end:
            stop = min(end, start + 600)
            if stop < end:
                gap = paragraph.rfind(' ', start + 300, stop)
                if gap > start:
                    stop = gap
            fragment = paragraph[start:stop].strip()
            if fragment:
                result.append({'text': fragment, 'start': start, 'end': start + len(fragment)})
            start = stop
            while start < end and paragraph[start].isspace():
                start += 1
    return result


def keywords(value):
    candidates = list(re.finditer(r"[A-Za-z]+(?:['’\-][A-Za-z]+)*", value))
    excluded = {'about', 'there', 'their', 'these', 'those', 'which', 'would', 'could', 'should', 'every', 'after', 'before', 'because', 'while', 'other', 'between'}
    chosen = sorted([m for m in candidates if len(m.group()) >= 5 and m.group().lower() not in excluded], key=lambda m: (-len(m.group()), m.start()))[:3]
    if not chosen:
        chosen = candidates[:1]
    chosen.sort(key=lambda m: m.start())
    parts, end = [], 0
    for match in chosen:
        parts.append(value[end:match.start()]); end = match.end()
    parts.append(value[end:])
    return {'parts': parts, 'answers': [m.group() for m in chosen]}


def source(data):
    article_id = integer(data, 'article_id', 1, 2**53 - 1)
    row = get_db().execute('SELECT id,title,content FROM articles WHERE id=?', (article_id,)).fetchone()
    if row is None:
        abort(404)
    if data.get('article_hash') != digest(row['content']):
        abort(409, '文章内容已变化，请重新载入练习。')
    parts = paragraphs(row['content'])
    paragraph_index = integer(data, 'paragraph_index', 0, len(parts) - 1)
    sentences = units(parts[paragraph_index])
    unit_index = integer(data, 'unit_index', 0, len(sentences) - 1)
    return row, sentences[unit_index]['text'], paragraph_index, unit_index


def audio_payload(data, optional=False):
    encoded = data.get('audio_base64', '')
    if optional and not encoded:
        return '', None
    if not isinstance(encoded, str) or len(encoded) > (MAX_AUDIO + 2) // 3 * 4:
        raise ValueError('音频文件最多 6 MB。')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('音频编码无效。') from None
    if not 32 <= len(raw) <= MAX_AUDIO:
        raise ValueError('音频为空、损坏或超过 6 MB。')
    mime = data.get('mime')
    valid = {'audio/webm': raw.startswith(b'\x1aE\xdf\xa3'), 'audio/ogg': raw.startswith(b'OggS'),
             'audio/wav': raw[:4] == b'RIFF' and raw[8:12] == b'WAVE',
             'audio/mpeg': raw.startswith(b'ID3') or raw[:2] in (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'),
             'audio/mp4': raw[4:8] == b'ftyp'}
    if not isinstance(mime, str) or not valid.get(mime):
        raise ValueError('请选择有效的 WebM、OGG、WAV、MP3 或 M4A 音频。')
    return mime, raw


def compare(expected, actual):
    left, right = words(expected), words(actual)
    if len(left) > 500 or len(right) > 500:
        raise ValueError('单次练习最多比较 500 个词，请缩小原文范围。')
    operations, errors, matched = [], 0, 0
    for kind, a, b, c, d in SequenceMatcher(None, [normalize(w) for w in left], [normalize(w) for w in right], autojunk=False).get_opcodes():
        operations.append({'kind': kind, 'expected': ' '.join(left[a:b]), 'actual': ' '.join(right[c:d])})
        if kind == 'equal':
            matched += b - a
        else:
            errors += max(b - a, d - c)
    return {'operations': operations, 'expected_words': len(left), 'recognized_words': len(right), 'matched_words': matched,
            'word_errors': errors, 'accuracy': round(100 * max(0, 1 - errors / max(1, len(left))), 1)}


def feedback(target, attempt, mode, duration_ms, voiced_ms, pause_count, analysis_available):
    result = compare(target, attempt)
    result['available'] = bool(attempt.strip())
    if not result['available']:
        result['accuracy'] = None
    result['analysis_available'] = analysis_available
    result['speech_rate'] = round(len(words(attempt)) * 60000 / voiced_ms, 1) if analysis_available and duration_ms >= 2000 and voiced_ms >= 1000 and attempt.strip() else None
    result['pause_ratio'] = round(1 - voiced_ms / duration_ms, 3) if analysis_available and duration_ms else None
    result['pause_count'] = pause_count
    result['notes'] = (['识别文本与原文的差异仅供核对漏读、替换和识别误差，不是声学发音评分。',
                        '语速与停顿由本机录音能量估算；设备噪声和耳机使用情况会影响结果。'] if mode in ('readalong', 'shadow') else
                       ['比较忽略大小写与标点，保留单词顺序；请结合原音复听差异。'])
    if mode in ('readalong', 'shadow'):
        if not analysis_available:
            result['notes'].append('本次没有本机能量分析数据，语速和停顿估计不可用。')
        elif not voiced_ms:
            result['notes'].append('未检测到明显发声，请回放录音或检查麦克风。')
        elif duration_ms < 2000 or voiced_ms < 1000:
            result['notes'].append('有效录音过短，本次不估算语速；可录制更完整的句子后再核对。')
    return result


@training.before_request
def upload_limit():
    if request.endpoint in ('training.add_attempt', 'training.add_reference'):
        request.max_content_length = 9 * 1024 * 1024


@training.get('/articles/<int:article_id>')
def manifest(article_id):
    db = get_db()
    row = db.execute('SELECT id,title,content FROM articles WHERE id=?', (article_id,)).fetchone()
    if row is None:
        abort(404)
    parts = paragraphs(row['content'])
    raw = request.args.get('paragraph', '0')
    if not raw.isdigit() or not 0 <= int(raw) < len(parts):
        raise ValueError('段落编号无效。')
    index = int(raw); hashed = digest(row['content'])
    references = {r['unit_index']: dict(r) for r in db.execute('SELECT id,unit_index,mime,version,length(data) AS size FROM listening_audio WHERE article_id=? AND article_hash=? AND paragraph_index=?', (article_id, hashed, index))}
    sentences = []
    for i, unit in enumerate(units(parts[index])):
        blanks = keywords(unit['text'])
        sentences.append({**unit, 'index': i, 'keyword_parts': blanks['parts'], 'gap_count': len(blanks['answers']), 'reference': references.get(i)})
    return jsonify(article={'id': article_id, 'title': row['title'], 'hash': hashed}, paragraph_index=index,
                   paragraphs=[{'index': i, 'preview': part[:100]} for i, part in enumerate(parts)], units=sentences)


@training.post('/reference')
def add_reference():
    data = body(); mime, raw = audio_payload(data); db = get_db(); db.execute('BEGIN IMMEDIATE')
    row, selected, paragraph_index, unit_index = source(data)
    current = db.execute('SELECT id,version FROM listening_audio WHERE article_id=? AND article_hash=? AND paragraph_index=? AND unit_index=?', (row['id'], data['article_hash'], paragraph_index, unit_index)).fetchone()
    version = integer(data, 'version', 0, 2**31 - 1, 0)
    if (current['version'] if current else 0) != version:
        db.rollback(); return jsonify(error='原句音频已更新，请重新读取后再保存。'), 409
    if current:
        db.execute('UPDATE listening_audio SET mime=?,data=?,version=version+1 WHERE id=?', (mime, raw, current['id']))
        identity = current['id']
    else:
        identity = db.execute('INSERT INTO listening_audio(article_id,article_hash,paragraph_index,unit_index,source_text,mime,data) VALUES(?,?,?,?,?,?,?)', (row['id'], data['article_hash'], paragraph_index, unit_index, selected, mime, raw)).lastrowid
    db.commit()
    return jsonify(reference={'id': identity, 'version': version + 1, 'mime': mime, 'size': len(raw)}), 201


@training.get('/audio/<int:audio_id>')
def reference_audio(audio_id):
    row = get_db().execute('SELECT mime,data FROM listening_audio WHERE id=?', (audio_id,)).fetchone()
    if row is None:
        abort(404)
    return Response(row['data'], mimetype=row['mime'], headers={'Content-Disposition': 'inline', 'Cache-Control': 'no-store'})


@training.post('/attempts')
def add_attempt():
    data = body(); request_id = token(data.get('request_id')); mode = data.get('mode')
    if mode not in MODES:
        raise ValueError('请选择有效的练习模式。')
    attempt = text(data, 'attempt', 4000)
    duration = integer(data, 'duration_ms', 0, 3600000, 0)
    voiced = integer(data, 'voiced_ms', 0, min(duration, 120000), 0)
    pauses = integer(data, 'pause_count', 0, 1200, 0)
    analysis_available = data.get('analysis_available', False)
    if type(analysis_available) is not bool or (not analysis_available and (voiced or pauses)):
        raise ValueError('音频分析状态与估算数据不一致。')
    if mode in ('dictation', 'keywords') and analysis_available:
        raise ValueError('听写模式无需提交录音分析。')
    mime, audio = audio_payload(data, optional=True)
    if mode in ('readalong', 'shadow') and not audio:
        raise ValueError('请先完成录音或上传本地跟读音频。')
    if mode in ('dictation', 'keywords') and audio:
        raise ValueError('听写模式无需上传录音。')
    fingerprint = digest(json.dumps({k: v for k, v in data.items() if k != 'audio_base64'}, sort_keys=True) + hashlib.sha256(audio or b'').hexdigest())
    db = get_db(); db.execute('BEGIN IMMEDIATE')
    existing = db.execute('SELECT id,fingerprint FROM listening_attempts WHERE request_id=?', (request_id,)).fetchone()
    if existing:
        db.rollback()
        if existing['fingerprint'] != fingerprint:
            return jsonify(error='同一请求不能用于不同练习记录。'), 409
        return jsonify(attempt=attempt_detail(db, existing['id']), duplicate=True)
    row, selected, paragraph_index, unit_index = source(data)
    target = selected
    if mode == 'keywords':
        gaps = keywords(selected)['answers']; answers = data.get('answers')
        if not isinstance(answers, list) or len(answers) != len(gaps) or any(not isinstance(a, str) or len(a) > 100 for a in answers):
            raise ValueError('请按空格数量填写关键词。')
        target, attempt = ' '.join(gaps), ' '.join(a.strip() for a in answers)
    if mode in ('dictation', 'keywords') and not attempt.strip():
        raise ValueError('请先填写听写或关键词答案。')
    result = feedback(target, attempt, mode, duration, voiced, pauses, analysis_available)
    identity = db.execute('''INSERT INTO listening_attempts(request_id,fingerprint,article_id,article_title,article_hash,
        paragraph_index,unit_index,source_text,mode,attempt,duration_ms,voiced_ms,pause_count,feedback_json,mime,audio,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (request_id, fingerprint, row['id'], row['title'], data['article_hash'], paragraph_index,
        unit_index, selected, mode, attempt, duration, voiced, pauses, json.dumps(result, ensure_ascii=False), mime, audio, now())).lastrowid
    db.commit()
    return jsonify(attempt=attempt_detail(db, identity), duplicate=False), 201


def attempt_detail(db, identity):
    row = db.execute('''SELECT id,article_id,article_title,paragraph_index,unit_index,source_text,mode,attempt,
        duration_ms,voiced_ms,pause_count,feedback_json,mime,length(audio) AS audio_size,created_at FROM listening_attempts WHERE id=?''', (identity,)).fetchone()
    if row is None:
        abort(404)
    item = dict(row); item['feedback'] = json.loads(item.pop('feedback_json'))
    return item


@training.get('/attempts')
def attempts():
    db = get_db(); article = request.args.get('article', '')
    if article and not article.isdigit():
        raise ValueError('文章编号无效。')
    rows = db.execute('SELECT id FROM listening_attempts' + (' WHERE article_id=?' if article else '') + ' ORDER BY id DESC LIMIT 100', (int(article),) if article else ()).fetchall()
    return jsonify(attempts=[attempt_detail(db, row['id']) for row in rows], total=db.execute('SELECT count(*) FROM listening_attempts').fetchone()[0])


@training.get('/attempts/<int:identity>/audio')
def recorded_audio(identity):
    row = get_db().execute('SELECT mime,audio FROM listening_attempts WHERE id=? AND audio IS NOT NULL', (identity,)).fetchone()
    if row is None:
        abort(404)
    return Response(row['audio'], mimetype=row['mime'], headers={'Content-Disposition': 'inline', 'Cache-Control': 'no-store'})
