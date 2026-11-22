"""Local, explainable selection. Heuristic reading loads are not proficiency tests."""
from collections import Counter
from datetime import datetime, timedelta
from functools import lru_cache
import hashlib
import json
import math

from flask import Blueprint, abort, jsonify, request

from .db import CHINA_TZ, get_db, now
from .dictionary import EXTRA, offline_entry
from .learning import assess_difficulty, words

personalization = Blueprint('personalization', __name__, url_prefix='/api/personalization')
LEVELS = ('A2', 'B1', 'B2', 'C1', 'C2')
CATEGORIES = ('科技', '文化', '科学', '生活', '环境', '其他')
DEFAULTS = {'level': 'B1', 'interests': [], 'minutes': 15, 'speed': 180,
            'challenge': 'balanced', 'adaptive': True, 'exclude_read': True}
# Frequency cutoffs are transparent selection heuristics, not CEFR word lists.
RANK_CUTOFFS = (1500, 3000, 6000, 10000, 16000)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS learner_profile (
 id INTEGER PRIMARY KEY CHECK(id=1), preferences TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS article_feedback (
 article_id INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
 article_hash TEXT NOT NULL, difficulty TEXT NOT NULL DEFAULT '',
 interest TEXT NOT NULL DEFAULT 'neutral', version INTEGER NOT NULL,
 updated_at TEXT NOT NULL, difficulty_at TEXT
);
'''


def digest(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def clock():
    return datetime.now(CHINA_TZ)


def body():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError('请提交 JSON 对象。')
    return data


def version(data):
    value = data.get('version')
    if type(value) is not int or value < 0:
        raise ValueError('请提供有效的记录版本。')
    return value


def read_profile(db):
    row = db.execute('SELECT * FROM learner_profile WHERE id=1').fetchone()
    return {'preferences': {**DEFAULTS, **(json.loads(row['preferences']) if row else {})},
            'version': row['version'] if row else 0, 'configured': bool(row)}


def validate(data):
    if set(data) - {*DEFAULTS, 'version'}:
        raise ValueError('学习偏好包含不支持的选项。')
    result = {key: value for key, value in data.items() if key != 'version'}
    for key, choices in (('level', LEVELS), ('challenge', ('gentle', 'balanced', 'stretch'))):
        if key in result and result[key] not in choices:
            raise ValueError('请选择有效的阅读水平与挑战程度。')
    for key, low, high in (('minutes', 2, 90), ('speed', 60, 400)):
        if key in result and (type(result[key]) is not int or not low <= result[key] <= high):
            raise ValueError(f'{key} 应为 {low}–{high} 之间的整数。')
    for key in ('adaptive', 'exclude_read'):
        if key in result and type(result[key]) is not bool:
            raise ValueError('自动调整与已读筛选应为布尔值。')
    if 'interests' in result:
        items = result['interests']
        if not isinstance(items, list) or len(items) > len(CATEGORIES) or any(not isinstance(i, str) or i not in CATEGORIES for i in items) or len(set(items)) != len(items):
            raise ValueError('请选择不重复的有效兴趣主题。')
    return result


def empty_feedback():
    return {'difficulty': '', 'interest': 'neutral', 'version': 0, 'difficulty_at': None}


def current_feedback(db, article):
    row = db.execute('SELECT * FROM article_feedback WHERE article_id=?', (article['id'],)).fetchone()
    return {key: row[key] for key in ('difficulty', 'interest', 'version', 'difficulty_at')} if row and row['article_hash'] == digest(article['content']) else empty_feedback()


def article_record(db, identity):
    row = db.execute('SELECT * FROM articles WHERE id=?', (identity,)).fetchone()
    if row is None:
        abort(404)
    return dict(row)


def recent(stamp):
    try:
        parsed = datetime.fromisoformat(stamp)
        return parsed.tzinfo is not None and clock() - timedelta(days=90) <= parsed <= clock() + timedelta(minutes=1)
    except (TypeError, ValueError):
        return False


def learning_evidence(db, profile):
    # Only completed, first eligible attempts on curated semantic questions count.
    # Repeating mistakes, copying source-location answers, or scrolling isn't evidence.
    signals, quiz_count, feedback_count = {}, 0, 0
    rows = db.execute("SELECT snapshot_json,answers_json,result_json,completed_at FROM reading_sessions WHERE status='completed' AND kind IN ('practice','assessment') ORDER BY completed_at,id").fetchall()
    for row in rows:
        if not recent(row['completed_at']):
            continue
        snapshot, answers, result = (json.loads(row[key]) for key in ('snapshot_json', 'answers_json', 'result_json'))
        outcomes = {q['id']: q['correct'] for q in result['questions']}
        for index, material in enumerate(snapshot['materials']):
            key = material['hash']
            if material['source'] != 'curated' or key in signals:
                continue
            eligible = [q for q in snapshot['questions'] if q['material_index'] == index and q['type'] in ('choice', 'true_false', 'meaning', 'theme', 'inference')]
            answered = [q for q in eligible if answers.get(q['id']) and any(str(a).strip() for a in answers[q['id']])]
            if len(answered) < max(3, math.ceil(len(eligible) * .8)):
                continue
            score = sum(bool(outcomes.get(q['id'])) for q in answered) / len(answered)
            signals[key] = 1 if score >= .85 else -1 if score <= .5 else 0
            quiz_count += 1
    for row in db.execute('SELECT f.*,a.content FROM article_feedback f JOIN articles a ON a.id=f.article_id ORDER BY f.updated_at,f.article_id'):
        if row['difficulty'] and recent(row['difficulty_at']) and row['article_hash'] == digest(row['content']):
            signals[row['article_hash']] = {'easy': 1, 'fit': 0, 'hard': -1}[row['difficulty']]
            feedback_count += 1
    count = len(signals)
    adjustment = round(max(-.6, min(.6, sum(signals.values()) / count * .6)), 2) if count >= 3 and profile['adaptive'] else 0
    return {'articles': count, 'quiz_articles': quiz_count, 'feedback_articles': feedback_count,
            'adjustment': adjustment, 'active': profile['adaptive'],
            'confidence': '充足' if count >= 8 else '初步' if count >= 3 else '待积累',
            'note': '近 90 天至少 3 篇独立材料才小幅调整；重复作答不累加，手动水平保持不变。'}


def knowledge(db):
    result = {}
    for row in db.execute('''SELECT v.word,v.mastery,m.entry_kind,s.state,s.repetitions,s.interval_days
        FROM vocabulary v JOIN word_meta m ON m.word_id=v.id LEFT JOIN srs_cards s ON s.word_id=v.id'''):
        if row['entry_kind'] != 'word' or ' ' in row['word']:
            continue
        term = row['word'].lower().replace('’', "'")
        result[term] = ('mastered' if row['mastery'] == 'mastered' else
                        'stable_review' if row['state'] == 'review' and row['repetitions'] >= 3 and row['interval_days'] >= 6 else 'learning')
    # Explicit user-approved aliases only; don't split phrases into known words.
    for row in db.execute("SELECT a.alias,v.word FROM word_aliases a JOIN vocabulary v ON a.word_id=v.id JOIN word_meta m ON m.word_id=v.id WHERE m.entry_kind='word'"):
        alias = row['alias'].lower().replace('’', "'")
        if ' ' not in alias and alias not in result:
            result[alias] = result.get(row['word'].lower(), 'learning')
    return result


@lru_cache(maxsize=128)
def lexicon(content):
    counts = Counter(term.lower().replace('’', "'") for term in words(content))
    entries = []
    for term, count in counts.items():
        lemma, entry = offline_entry(term)
        if entry is None:
            lemma = {'am': 'be', 'is': 'be', 'are': 'be', 'was': 'be', 'were': 'be', 'been': 'be',
                     'has': 'have', 'does': 'do', 'did': 'do', 'cannot': 'can'}.get(term, lemma)
        ranks = [int(EXTRA.get(lemma, {}).get(k) or 0) for k in ('bnc', 'frq')]
        rank = min((r for r in ranks if r > 0), default=None)
        entries.append((term, lemma, count, rank))
    return entries, assess_difficulty(content)


def analyze(article, profile, evidence, known):
    entries, base = lexicon(article['content'])
    total = base['word_count']
    groups = dict.fromkeys(('mastered', 'stable_review', 'learning', 'assumed_familiar', 'to_explore', 'unlisted'), 0)
    burden, preview = 0, []
    cutoff = RANK_CUTOFFS[LEVELS.index(profile['level'])]
    for term, lemma, count, rank in entries:
        group = known.get(term) or known.get(lemma)
        if group is None:
            group = 'unlisted' if rank is None else 'assumed_familiar' if rank <= cutoff else 'to_explore'
        groups[group] += count
        burden += count * {'mastered': 0, 'stable_review': .1, 'learning': .75, 'assumed_familiar': .06, 'to_explore': .55, 'unlisted': .4}[group]
        if group in ('learning', 'to_explore', 'unlisted'):
            preview.append({'word': term, 'count': count, 'basis': '已记生词' if group == 'learning' else '词库未覆盖，待判断' if group == 'unlisted' else '超出当前词频假设'})
    load = burden / max(total, 1)
    target = min(4, max(0, LEVELS.index(profile['level']) + evidence['adjustment']))
    relative = .65 * (LEVELS.index(base['level']) - target) + .75 * max(-1, min(2, (load - .12) / .15))
    minutes = max(1, math.ceil(total / profile['speed']))
    label = '轻松' if relative < -.65 else '适合' if relative <= .55 else '挑战'
    return {'hash': digest(article['content']), 'label': label, 'relative': round(relative, 3),
            'baseline_level': base['level'], 'ease': base['score'], 'sentence_length': base['average_sentence_length'],
            'word_count': total, 'minutes': minutes, 'vocabulary': groups,
            'confirmed_mastered_percent': round(groups['mastered'] / max(total, 1) * 100, 1),
            'estimated_load_per_100': round(load * 100, 1),
            'preview': sorted(preview, key=lambda w: (-w['count'], w['word']))[:6],
            'method': '按词次计算。已掌握来自手动标记；稳定复习来自 SRS。其余按词频作假设，词库未覆盖不等于生词。难度与用时均为选文估算。'}


def context(db):
    profile = read_profile(db)
    evidence = learning_evidence(db, profile['preferences'])
    return profile, evidence, knowledge(db)


@personalization.get('/profile')
def get_profile():
    db = get_db(); profile = read_profile(db)
    return jsonify(**profile, evidence=learning_evidence(db, profile['preferences']), categories=CATEGORIES)


@personalization.patch('/profile')
def save_profile():
    data = body(); expected = version(data); changes = validate(data); db = get_db()
    db.execute('BEGIN IMMEDIATE'); current = read_profile(db)
    if expected != current['version']:
        db.rollback(); return jsonify(error='学习偏好已在其他页面修改，请重新载入。'), 409
    values = {**current['preferences'], **changes}
    db.execute('INSERT INTO learner_profile(id,preferences,version,updated_at) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET preferences=excluded.preferences,version=excluded.version,updated_at=excluded.updated_at',
               (json.dumps(values, ensure_ascii=False), expected + 1, now()))
    db.commit()
    return get_profile()


@personalization.delete('/profile')
def reset_profile():
    expected = version(body()); db = get_db(); db.execute('BEGIN IMMEDIATE'); current = read_profile(db)
    if expected != current['version']:
        db.rollback(); return jsonify(error='学习偏好已变化，请重新载入。'), 409
    # Keep a monotonic version (prevents stale tabs from recreating old preferences).
    db.execute('INSERT INTO learner_profile(id,preferences,version,updated_at) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET preferences=excluded.preferences,version=excluded.version,updated_at=excluded.updated_at',
               (json.dumps(DEFAULTS), expected + 1, now()))
    db.commit()
    return get_profile()


@personalization.get('/articles/<int:identity>')
def article_analysis(identity):
    db = get_db(); article = article_record(db, identity); profile, evidence, known = context(db)
    return jsonify(article={'id': identity, 'title': article['title']}, analysis=analyze(article, profile['preferences'], evidence, known),
                   feedback=current_feedback(db, article), profile=profile, evidence=evidence)


@personalization.put('/articles/<int:identity>/feedback')
def save_feedback(identity):
    data = body(); expected = version(data)
    if set(data) - {'version', 'article_hash', 'difficulty', 'interest'} or not {'difficulty', 'interest'} & set(data):
        raise ValueError('请选择难度或兴趣反馈。')
    if 'difficulty' in data and data['difficulty'] not in ('', 'easy', 'fit', 'hard'):
        raise ValueError('难度反馈无效。')
    if 'interest' in data and data['interest'] not in ('neutral', 'more', 'less'):
        raise ValueError('兴趣反馈无效。')
    db = get_db(); db.execute('BEGIN IMMEDIATE'); article = article_record(db, identity)
    hashed = digest(article['content']); current = current_feedback(db, article)
    if data.get('article_hash') != hashed or expected != current['version']:
        db.rollback(); return jsonify(error='文章或反馈已变化，请重新载入后操作。'), 409
    values = {**current, **{k: data[k] for k in ('difficulty', 'interest') if k in data}}
    db.execute('''INSERT INTO article_feedback(article_id,article_hash,difficulty,interest,version,updated_at,difficulty_at) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(article_id) DO UPDATE SET article_hash=excluded.article_hash,difficulty=excluded.difficulty,
        interest=excluded.interest,version=excluded.version,updated_at=excluded.updated_at,difficulty_at=excluded.difficulty_at''',
        (identity, hashed, values['difficulty'], values['interest'], expected + 1, clock().isoformat(),
         (clock().isoformat() if values['difficulty'] else None) if 'difficulty' in data else current['difficulty_at']))
    db.commit()
    return article_analysis(identity)


def query_int(name, default, low, high):
    raw = request.args.get(name)
    if raw is None: return default
    if not raw.isascii() or not raw.isdigit() or not low <= int(raw) <= high:
        raise ValueError(f'{name} 筛选范围无效。')
    return int(raw)


@personalization.get('/recommendations')
def recommend():
    if set(request.args) - {'category', 'max_minutes', 'include_read', 'limit'}:
        raise ValueError('包含不支持的推荐筛选。')
    db = get_db(); profile, evidence, known = context(db); prefs = profile['preferences']
    category = request.args.get('category', '')
    if category and category not in CATEGORIES: raise ValueError('请选择有效主题。')
    budget = query_int('max_minutes', None, 1, 120)
    include_read = bool(query_int('include_read', int(not prefs['exclude_read']), 0, 1))
    limit = query_int('limit', 12, 1, 50)
    pool, suppressed = [], 0
    for row in db.execute('SELECT * FROM articles ORDER BY id'):
        article = dict(row); feedback = current_feedback(db, article)
        if (not include_read and article['progress'] >= 100) or (category and article['category'] != category): continue
        if feedback['interest'] == 'less': suppressed += 1; continue
        analysis = analyze(article, prefs, evidence, known)
        if budget and analysis['minutes'] > budget: continue
        distance = abs(analysis['relative'] - {'gentle': -.65, 'balanced': 0, 'stretch': .65}[prefs['challenge']])
        score = 70 - distance * 24
        reasons = [f"个人难度：{analysis['label']}"]
        if article['category'] in prefs['interests']:
            score += 12; reasons.append('符合你的兴趣主题')
        if analysis['minutes'] <= prefs['minutes']:
            score += 10; reasons.append(f"约 {analysis['minutes']} 分钟，适合本次时间")
        else:
            score -= min(30, (analysis['minutes'] - prefs['minutes']) * 2)
            reasons.append('篇幅较长，建议分段阅读')
        if feedback['interest'] == 'more': score += 8; reasons.append('你希望多读这篇')
        if article['progress'] >= 100: reasons.append('已读文章，可用于重读')
        elif article['progress'] > 0: reasons.append('上次阅读尚未完成')
        summary = {k: article[k] for k in ('id', 'title', 'subtitle', 'source', 'category', 'progress', 'word_count')}
        pool.append({**summary, 'score': round(max(0, min(100, score)), 1), 'reasons': reasons, 'analysis': analysis, 'feedback': feedback})
    pool.sort(key=lambda a: (-a['score'], a['id']))
    return jsonify(articles=pool[:limit], matched=len(pool), suppressed=suppressed, profile=profile, evidence=evidence,
                   note='从本地文章库选择；匹配分只用于排序，不代表测评成绩。无记录时使用自评与词频假设。')


@personalization.get('/feedback')
def feedback_history():
    db = get_db(); profile, evidence, known = context(db); items = []
    for row in db.execute('SELECT a.* FROM article_feedback f JOIN articles a ON a.id=f.article_id ORDER BY f.updated_at DESC,a.id'):
        article = dict(row); feedback = current_feedback(db, article)
        if not feedback['version'] or (not feedback['difficulty'] and feedback['interest'] == 'neutral'): continue
        items.append({**{k: article[k] for k in ('id', 'title', 'subtitle', 'source', 'category', 'progress', 'word_count')},
                      'feedback': feedback, 'analysis': analyze(article, profile['preferences'], evidence, known),
                      'reasons': ['已保存的反馈，可随时撤销']})
    return jsonify(articles=items, profile=profile, evidence=evidence)
