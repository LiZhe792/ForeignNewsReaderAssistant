"""Explicit local exports and opt-in, token-protected read-only integrations."""
import hashlib
import hmac
import io
import json
import re
import secrets
import uuid
from datetime import date, timedelta

from flask import Blueprint, jsonify, request, send_file

from .db import get_db, now, today
from .export_formats import render_export, safe_url

exports = Blueprint('exports', __name__, url_prefix='/api')
MAX_RECORDS = 5000
MAX_TEXT_BYTES = 8 * 1024 * 1024
DATASETS = {'vocabulary': '生词本', 'notes': '笔记与收藏', 'report': '学习报告', 'reading_list': '阅读清单'}
FORMATS = {'csv': 'CSV', 'xlsx': 'Excel', 'json': 'JSON', 'md': 'Markdown', 'pdf': 'PDF',
           'anki': 'Anki 卡片包', 'html': '可分享的网页文件', 'obsidian': 'Obsidian 笔记包', 'notion': 'Notion 导入包'}
SUPPORTED = {
    'vocabulary': ['csv', 'xlsx', 'json', 'md', 'pdf', 'anki', 'obsidian', 'notion'],
    'notes': ['csv', 'xlsx', 'json', 'md', 'pdf', 'anki', 'obsidian', 'notion'],
    'report': ['pdf', 'xlsx', 'csv', 'json', 'md'],
    'reading_list': ['html', 'md', 'csv', 'xlsx', 'json', 'pdf'],
}
MASTERY = {'new': '初次遇见', 'learning': '正在学习', 'mastered': '已掌握'}
FILTER_KEYS = {'query', 'mastery', 'kind', 'tag', 'article_id', 'from', 'to', 'read_status'}
TABLES = {'vocabulary': 'vocabulary', 'notes': 'knowledge_items', 'reading_list': 'articles'}
TOKEN_KEY = 'integration_read_token'
# Read the feature 19 storage contract without requiring its editor or API module.
KINDS = {'highlight': '高亮', 'bookmark': '段落书签', 'annotation': '页边批注', 'sentence': '句子收藏',
         'grammar': '语法点', 'note': '主题笔记', 'expression': '表达素材', 'card': '知识卡片'}


def table_exists(table):
    return get_db().execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def note_json(row):
    keys = ('id', 'kind', 'article_id', 'article_title', 'title', 'content', 'example',
            'color', 'version', 'created_at', 'updated_at')
    value = {key: row[key] for key in keys}
    value['tags'], value['anchors'] = json.loads(row['tags']), json.loads(row['anchors'])
    value['label'] = KINDS[value['kind']]
    value['quote'] = '\n\n'.join(anchor['quote'] for anchor in value['anchors'])
    article = get_db().execute('SELECT source_url,content FROM articles WHERE id=?', (value['article_id'],)).fetchone()
    value['source_url'] = safe_url(article['source_url']) if article else ''
    value['source_state'] = 'none'
    if value['anchors']:
        value['source_state'] = 'deleted' if article is None else 'available'
        if article is not None:
            parts = [part.strip() for part in re.split(r'\n\s*\n|\n', article['content']) if part.strip()]
            for anchor in value['anchors']:
                try:
                    index, start, end = (anchor[key] for key in ('paragraph', 'start', 'end'))
                    if any(type(n) is not int for n in (index, start, end)) or not 0 <= index < len(parts):
                        raise ValueError('Invalid source position')
                    encoded = parts[index].encode('utf-16-le')
                    if not 0 <= start < end <= len(encoded)//2 or encoded[start*2:end*2].decode('utf-16-le') != anchor['quote']:
                        raise ValueError('Source text changed')
                except (KeyError, TypeError, ValueError, UnicodeError):
                    value['source_state'] = 'changed'
                    break
    return value


def body():
    value = request.get_json()
    if not isinstance(value, dict):
        raise ValueError('请提交 JSON 对象。')
    return value


def integer(value, label, minimum=1, maximum=2**53-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{label}不正确。')
    return value


def parse_date(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError(f'{label}应为 YYYY-MM-DD。')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f'{label}无效。') from None


def normalize(data):
    if set(data) - {'dataset', 'format', 'filters', 'ids', 'after', 'deck_name'}:
        raise ValueError('包含不支持的导出参数。')
    dataset, fmt = data.get('dataset'), data.get('format', 'json')
    if not isinstance(dataset, str) or dataset not in DATASETS:
        raise ValueError('请选择有效的导出内容。')
    if not isinstance(fmt, str) or fmt not in SUPPORTED[dataset]:
        raise ValueError('当前内容不支持此导出格式。')
    filters = data.get('filters', {})
    if not isinstance(filters, dict) or set(filters) - FILTER_KEYS:
        raise ValueError('筛选参数无效。')
    filters = dict(filters)
    for key, limit in [('query', 200), ('tag', 24)]:
        value = filters.get(key, '')
        if not isinstance(value, str) or len(value) > limit or '\x00' in value:
            raise ValueError('搜索内容或标签过长、格式不正确。')
        filters[key] = value.strip()
    filters['tag'] = filters['tag'].casefold()
    for key, choices in [('mastery', MASTERY), ('kind', KINDS), ('read_status', ('unread', 'reading', 'completed'))]:
        value = filters.get(key, '')
        if not isinstance(value, str) or value and value not in choices:
            raise ValueError('筛选选项无效。')
        filters[key] = value
    if filters.get('article_id') not in (None, ''):
        filters['article_id'] = integer(filters['article_id'], '文章编号')
    else:
        filters['article_id'] = None
    applicable = {'vocabulary': {'query', 'mastery', 'article_id', 'from', 'to'},
                  'notes': {'query', 'kind', 'tag', 'article_id', 'from', 'to'},
                  'reading_list': {'query', 'read_status', 'from', 'to'}, 'report': {'from', 'to'}}[dataset]
    if any(value not in ('', None) and key not in applicable for key, value in filters.items()):
        raise ValueError('筛选条件不适用于当前导出内容。')
    for key in ('from', 'to'):
        if not isinstance(filters.get(key, ''), str):
            raise ValueError('起止日期应为 YYYY-MM-DD。')
        if filters.get(key):
            filters[key] = parse_date(filters[key], '起止日期').isoformat()
        else:
            filters[key] = ''
    if dataset == 'report':
        filters['from'] = filters['from'] or (today()-timedelta(days=29)).isoformat()
        filters['to'] = filters['to'] or today().isoformat()
        if (date.fromisoformat(filters['to'])-date.fromisoformat(filters['from'])).days > 365:
            raise ValueError('一次学习报告最多覆盖 366 天。')
    if filters['from'] and filters['to'] and filters['from'] > filters['to']:
        raise ValueError('开始日期不能晚于结束日期。')
    if dataset == 'notes' and fmt == 'anki':
        if filters['kind'] and filters['kind'] != 'card':
            raise ValueError('Anki 笔记导出请选择「知识卡片」；其他笔记可先制成卡片。')
        filters['kind'] = 'card'
    ids = data.get('ids')
    if ids is not None:
        if dataset == 'report' or not isinstance(ids, list) or not 1 <= len(ids) <= MAX_RECORDS:
            raise ValueError('请至少勾选一条记录，单次最多 5000 条。')
        ids = sorted({integer(value, '记录编号') for value in ids})
    after = integer(data.get('after', 0), '分页位置', 0)
    deck_name = data.get('deck_name', 'Margin 知刊::'+DATASETS[dataset])
    if not isinstance(deck_name, str) or not deck_name.strip() or len(deck_name) > 100 or any(ord(c) < 32 for c in deck_name):
        raise ValueError('牌组名称应为 1–100 字符，不能包含换行。')
    return {'dataset': dataset, 'format': fmt, 'filters': filters, 'ids': ids,
            'after': after, 'deck_name': deck_name.strip()}


def selection(spec):
    dataset, filters = spec['dataset'], spec['filters']
    conditions, params = [], []
    if spec['ids'] is not None:
        conditions.append('id IN (SELECT value FROM json_each(?))')
        params.append(json.dumps(spec['ids']))
    if filters['query']:
        pattern = '%'+filters['query'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')+'%'
        columns = {'vocabulary': ['word', 'meaning', 'context', 'note'],
                   'notes': ['title', 'content', 'example'], 'reading_list': ['title', 'source', 'category']}[dataset]
        clauses = [column+" LIKE ? ESCAPE '\\'" for column in columns]
        params.extend([pattern]*len(columns))
        if dataset == 'notes':
            clauses.append("EXISTS (SELECT 1 FROM json_each(anchors) WHERE json_extract(value,'$.quote') LIKE ? ESCAPE '\\')")
            params.append(pattern)
        conditions.append('('+' OR '.join(clauses)+')')
    for key in ('mastery', 'kind', 'article_id'):
        if filters[key]:
            conditions.append(key+'=?')
            params.append(filters[key])
    if filters['tag']:
        conditions.append('EXISTS (SELECT 1 FROM json_each(tags) WHERE value=?)')
        params.append(filters['tag'])
    if filters['read_status']:
        conditions.append({'unread': 'progress=0', 'reading': '(progress>0 AND progress<100)', 'completed': 'progress>=100'}[filters['read_status']])
    if filters['from']:
        conditions.append('substr(created_at,1,10)>=?')
        params.append(filters['from'])
    if filters['to']:
        conditions.append('substr(created_at,1,10)<=?')
        params.append(filters['to'])
    return ' AND '.join(conditions) or '1=1', params


def serialize(row, dataset):
    row = dict(row)
    if dataset == 'notes':
        return note_json(row)
    if dataset == 'vocabulary':
        article = get_db().execute('SELECT title,source_url FROM articles WHERE id=?', (row['article_id'],)).fetchone()
        row['article_title'] = article['title'] if article else ''
        row['source_url'] = safe_url(article['source_url']) if article else ''
        row['mastery_label'] = MASTERY[row['mastery']]
        return row
    # Shared reading lists deliberately contain bibliographic data, not personal reading history.
    return {**{key: row[key] for key in ('id', 'title', 'subtitle', 'source', 'category', 'level', 'word_count', 'reading_minutes')},
            'source_url': safe_url(row['source_url'])}


def records(spec, limit, after=0):
    where, params = selection(spec)
    db, table = get_db(), TABLES[spec['dataset']]
    if not table_exists(table):
        return 0, [], None
    count = db.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}', params).fetchone()[0]
    rows = db.execute(f'SELECT * FROM {table} WHERE ({where}) AND id>? ORDER BY id LIMIT ?', [*params, after, limit+1]).fetchall()
    return count, [serialize(row, spec['dataset']) for row in rows[:limit]], rows[limit-1]['id'] if len(rows) > limit else None


def report(filters):
    start, end = filters['from'], filters['to']
    db = get_db()
    rows = {r['date']: dict(r) for r in db.execute('SELECT * FROM checkins WHERE date BETWEEN ? AND ?', (start, end))}
    daily, cursor, current_streak, longest_streak = [], date.fromisoformat(start), 0, 0
    while cursor <= date.fromisoformat(end):
        key = cursor.isoformat()
        row = rows.get(key, {'reading_seconds': 0, 'articles_completed': 0, 'manual': 0})
        checked = key in rows
        current_streak = current_streak+1 if checked else 0
        longest_streak = max(longest_streak, current_streak)
        daily.append({'date': key, 'reading_seconds': row['reading_seconds'], 'reading_minutes': round(row['reading_seconds']/60, 2),
                      'completed_count': row['articles_completed'], 'checked_in': checked, 'manual': bool(row['manual'])})
        if key == end:
            break
        cursor += timedelta(days=1)
    mastery = {key: 0 for key in MASTERY}
    for row in db.execute('SELECT mastery,COUNT(*) AS n FROM vocabulary GROUP BY mastery'):
        mastery[row['mastery']] = row['n']
    created = lambda table: db.execute(f'SELECT COUNT(*) FROM {table} WHERE substr(created_at,1,10) BETWEEN ? AND ?', (start, end)).fetchone()[0] if table_exists(table) else 0
    return {'period': {'from': start, 'to': end, 'timezone': 'Asia/Shanghai'},
            'metrics': {'reading_seconds': sum(r['reading_seconds'] for r in daily), 'checked_days': len(rows),
                        'completed_count': sum(r['completed_count'] for r in daily), 'longest_streak': longest_streak,
                        'added_vocabulary': created('vocabulary'), 'added_notes': created('knowledge_items'),
                        'current_vocabulary': sum(mastery.values())},
            'mastery': mastery, 'daily': daily,
            'definitions': '阅读时长、打卡与完成次数按所选区间统计；新增生词和笔记统计区间内创建且当前仍保留的记录。掌握分布为导出时的现状，不代表历史日期的掌握程度。'}


def bundle(spec, preview=False, api_limit=50):
    db = get_db()
    db.execute('BEGIN')
    try:
        result = {'schema_version': 1, 'dataset': spec['dataset'], 'title': DATASETS[spec['dataset']], 'generated_at': now(), 'filters': spec['filters']}
        if spec['dataset'] == 'report':
            result['report'] = report(spec['filters'])
            result['count'] = len(result['report']['daily'])
        else:
            count, items, cursor = records(spec, api_limit if preview else MAX_RECORDS, spec['after'] if preview else 0)
            if spec['ids'] is not None and count != len(spec['ids']):
                raise ValueError('部分勾选记录已删除或不再符合筛选条件，请刷新预览后重选。')
            if not preview and count > MAX_RECORDS:
                raise ValueError('单次最多导出 5000 条，请缩小筛选范围或分批导出。')
            result.update(count=count, items=items, next_cursor=cursor)
            if not preview and not count:
                raise ValueError('没有符合条件的记录，请调整筛选条件。')
        if not preview and len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_TEXT_BYTES:
            raise ValueError('本次文本超过 8 MB，请缩小筛选范围或分批导出。')
        return result
    finally:
        db.rollback()  # A consistent read snapshot; no exported records are modified.


def namespace():
    db = get_db()
    db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)', ('export_namespace', json.dumps(str(uuid.uuid4()))))
    db.commit()
    return json.loads(db.execute('SELECT value FROM settings WHERE key=?', ('export_namespace',)).fetchone()[0])


@exports.get('/exports/options')
def options():
    counts = {key: get_db().execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] if table_exists(table) else 0 for key, table in TABLES.items()}
    return jsonify(datasets=DATASETS, formats=FORMATS, supported=SUPPORTED, kinds=KINDS, mastery=MASTERY, counts=counts,
                   max_records=MAX_RECORDS, today=today().isoformat(), default_from=(today()-timedelta(days=29)).isoformat())


@exports.post('/exports/preview')
def preview():
    return jsonify(bundle(normalize(body()), preview=True))


@exports.post('/exports/file')
def download():
    spec = normalize(body())
    identity = namespace() if spec['format'] == 'anki' else ''
    data = bundle(spec)
    result, extension, mime = render_export(data, spec['format'], deck_name=spec['deck_name'], namespace=identity)
    response = send_file(io.BytesIO(result), mimetype=mime, as_attachment=True,
                         download_name=f"margin-{spec['dataset']}-{today().isoformat()}.{extension}", max_age=0)
    response.headers['X-Export-Count'] = str(data['count'])
    response.headers['Cache-Control'] = 'no-store'
    return response


def token_config():
    row = get_db().execute('SELECT value FROM settings WHERE key=?', (TOKEN_KEY,)).fetchone()
    return json.loads(row['value']) if row else None


@exports.route('/integrations/token', methods=['GET', 'POST', 'DELETE'])
def manage_token():
    if request.method == 'GET':
        config = token_config()
        return jsonify(enabled=bool(config), suffix=config['suffix'] if config else '', created_at=config['created_at'] if config else None)
    data = body()
    if data:
        raise ValueError('此操作不接受额外参数。')
    db = get_db()
    if request.method == 'DELETE':
        db.execute('DELETE FROM settings WHERE key=?', (TOKEN_KEY,))
        db.commit()
        return jsonify(enabled=False)
    token = 'margin_'+secrets.token_urlsafe(32)
    config = {'hash': hashlib.sha256(token.encode()).hexdigest(), 'suffix': token[-4:], 'created_at': now()}
    db.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (TOKEN_KEY, json.dumps(config)))
    db.commit()
    return jsonify(token=token, suffix=config['suffix'], created_at=config['created_at'], enabled=True)


def authorized():
    value = request.headers.get('Authorization', '')
    config = token_config()
    if not config or not value.startswith('Bearer ') or len(value) > 200:
        return False
    return hmac.compare_digest(config['hash'], hashlib.sha256(value[7:].encode()).hexdigest())


@exports.get('/integrations/v1/<resource>')
def data_api(resource):
    if not authorized():
        response = jsonify(error='请先在导出中心启用数据接口，并提供有效的 Bearer 令牌。')
        response.status_code = 401
        response.headers['WWW-Authenticate'] = 'Bearer realm="Margin read-only data"'
        return response
    if resource == 'schema':
        return jsonify(schema_version=1, read_only=True, timezone='Asia/Shanghai', max_page_size=200,
                       resources=['vocabulary', 'notes', 'reading-list', 'report'], pagination='after + limit; id ascending',
                       filters={'vocabulary': ['query', 'mastery', 'article_id', 'from', 'to'],
                                'notes': ['query', 'kind', 'tag', 'article_id', 'from', 'to'],
                                'reading-list': ['query', 'read_status', 'from', 'to'], 'report': ['from', 'to']})
    dataset = {'vocabulary': 'vocabulary', 'notes': 'notes', 'reading-list': 'reading_list', 'report': 'report'}.get(resource)
    if not dataset:
        return jsonify(error='接口不存在。'), 404
    args = request.args.to_dict()
    after, limit = args.pop('after', '0'), args.pop('limit', '100')
    if not after.isascii() or not after.isdigit() or len(after) > 16 or not limit.isascii() or not limit.isdigit() or len(limit) > 3:
        raise ValueError('分页参数无效。')
    limit = integer(int(limit), '每页条数', 1, 200)
    if 'article_id' in args:
        value = args['article_id']
        if not value.isascii() or not value.isdigit() or len(value) > 16:
            raise ValueError('文章编号无效。')
        args['article_id'] = int(value)
    spec = normalize({'dataset': dataset, 'filters': args, 'after': int(after)})
    return jsonify(bundle(spec, preview=True, api_limit=limit))
