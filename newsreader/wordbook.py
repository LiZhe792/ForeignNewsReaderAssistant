"""Rich vocabulary, portable imports and local example/image/audio cards."""
import base64
import binascii
import csv
import hashlib
import io
import json
import re

from flask import Blueprint, abort, jsonify, request, Response
from .db import get_db, now
from .dictionary import normalize_term, offline_entry

wordbook = Blueprint('wordbook', __name__, url_prefix='/api/wordbook')
TEMPLATES = {'reading': ['核心义', '原文语境', '常见搭配', '自己的例句'],
             'contrast': ['易混词', '区别与限制', '对比例句'], 'pattern': ['句型结构', '使用场景', '替换练习']}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS word_meta(word_id INTEGER PRIMARY KEY REFERENCES vocabulary(id) ON DELETE CASCADE,
 entry_kind TEXT NOT NULL DEFAULT 'word', version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS word_collections(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
 name TEXT NOT NULL COLLATE NOCASE, UNIQUE(kind,name));
CREATE TABLE IF NOT EXISTS word_memberships(word_id INTEGER REFERENCES vocabulary(id) ON DELETE CASCADE,
 collection_id INTEGER REFERENCES word_collections(id) ON DELETE CASCADE, PRIMARY KEY(word_id,collection_id));
CREATE TABLE IF NOT EXISTS word_contexts(id INTEGER PRIMARY KEY AUTOINCREMENT,
 word_id INTEGER NOT NULL REFERENCES vocabulary(id) ON DELETE CASCADE,
 text TEXT NOT NULL, translation TEXT NOT NULL DEFAULT '', article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL,
 article_title TEXT NOT NULL DEFAULT '', UNIQUE(word_id,text));
CREATE TABLE IF NOT EXISTS word_senses(id INTEGER PRIMARY KEY AUTOINCREMENT,
 word_id INTEGER NOT NULL REFERENCES vocabulary(id) ON DELETE CASCADE, meaning TEXT NOT NULL,
 part_of_speech TEXT NOT NULL DEFAULT '', example TEXT NOT NULL DEFAULT '', is_primary INTEGER NOT NULL DEFAULT 0,
 UNIQUE(word_id,meaning,part_of_speech,example));
CREATE UNIQUE INDEX IF NOT EXISTS word_primary_sense ON word_senses(word_id) WHERE is_primary=1;
CREATE TABLE IF NOT EXISTS word_notes(id INTEGER PRIMARY KEY AUTOINCREMENT,
 word_id INTEGER NOT NULL REFERENCES vocabulary(id) ON DELETE CASCADE, title TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS word_aliases(word_id INTEGER REFERENCES vocabulary(id) ON DELETE CASCADE,
 alias TEXT NOT NULL, PRIMARY KEY(word_id,alias));
CREATE TABLE IF NOT EXISTS word_cards(id INTEGER PRIMARY KEY AUTOINCREMENT,
 word_id INTEGER NOT NULL REFERENCES vocabulary(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, front TEXT NOT NULL DEFAULT '', back TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL DEFAULT '',
 mime TEXT NOT NULL DEFAULT '', data BLOB, digest TEXT NOT NULL DEFAULT '');
'''


def body():
    value = request.get_json()
    if not isinstance(value, dict): raise ValueError('请求内容必须为 JSON 对象。')
    return value


def text(data, key, limit, required=False):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > limit: raise ValueError(f'{key} 超过长度限制或不是文本。')
    value = value.strip()
    if required and not value: raise ValueError(f'请填写 {key}。')
    return value


def entry_word(value, kind):
    if kind not in ('word','phrase','pattern'): raise ValueError('词条类型无效。')
    if kind != 'pattern': return normalize_term(value)
    if (not isinstance(value,str) or not 1 <= len(value) <= 120 or not re.search('[A-Za-z]',value)
        or not re.fullmatch(r"[A-Za-z0-9\s.,!?:;'’\-()_/+…]+",value)):
        raise ValueError('句型限 120 字符，可使用英文、空格、标点和 + … 表示结构。')
    return re.sub(r'\s+',' ',value.strip().lower().replace('’',"'"))


def record(db, word_id):
    row = db.execute('SELECT * FROM vocabulary WHERE id=?',(word_id,)).fetchone()
    if row is None: abort(404)
    return row


def attach_context(db, word_id, context, article_id=None, translation='', title=''):
    if not context.strip(): return
    if db.execute('SELECT 1 FROM word_contexts WHERE word_id=? AND text=?',(word_id,context.strip())).fetchone(): return
    if db.execute('SELECT count(*) FROM word_contexts WHERE word_id=?',(word_id,)).fetchone()[0]>=30: raise ValueError('每个词条最多保留 30 个原句，请先整理已有原句。')
    if article_id is not None:
        row=db.execute('SELECT title FROM articles WHERE id=?',(article_id,)).fetchone()
        if row is None: raise ValueError('关联文章不存在。')
        title=row['title']
    db.execute('INSERT OR IGNORE INTO word_contexts(word_id,text,translation,article_id,article_title) VALUES(?,?,?,?,?)',
               (word_id,context.strip(),translation,article_id,title))


def ensure_word(db, word_id):
    row=record(db,word_id)
    db.execute('INSERT OR IGNORE INTO word_meta(word_id,entry_kind) VALUES(?,?)',(word_id,'phrase' if ' ' in row['word'] else 'word'))
    if not db.execute('SELECT 1 FROM word_senses WHERE word_id=? AND is_primary=1',(word_id,)).fetchone():
        db.execute('INSERT OR IGNORE INTO word_senses(word_id,meaning,part_of_speech,is_primary) VALUES(?,?,?,1)',(word_id,row['meaning'],row['part_of_speech']))
    attach_context(db,word_id,row['context'],row['article_id'])
    from .srs import ensure_schedule
    ensure_schedule(db,word_id)


def initialize(db):
    from .srs import SCHEMA as REVIEW_SCHEMA
    db.executescript(SCHEMA+REVIEW_SCHEMA)
    for row in db.execute('SELECT id FROM vocabulary').fetchall(): ensure_word(db,row['id'])
    db.commit()


def detail(db, word_id, media=False):
    row=dict(record(db,word_id)); meta=db.execute('SELECT * FROM word_meta WHERE word_id=?',(word_id,)).fetchone()
    row.update(dict(meta) if meta else {'entry_kind':'word','version':1})
    row['collections']=[dict(r) for r in db.execute('SELECT c.* FROM word_collections c JOIN word_memberships m ON m.collection_id=c.id WHERE m.word_id=? ORDER BY c.kind,c.name',(word_id,))]
    row['contexts']=[dict(r) for r in db.execute('SELECT * FROM word_contexts WHERE word_id=? ORDER BY id',(word_id,))]
    row['senses']=[dict(r) for r in db.execute('SELECT * FROM word_senses WHERE word_id=? ORDER BY is_primary DESC,id',(word_id,))]
    row['notes']=[dict(r) for r in db.execute('SELECT * FROM word_notes WHERE word_id=? ORDER BY id',(word_id,))]
    row['aliases']=[r['alias'] for r in db.execute('SELECT alias FROM word_aliases WHERE word_id=?',(word_id,))]
    row['cards']=[]
    columns='*' if media else 'id,word_id,kind,front,back,filename,mime,length(data) AS size'
    for card in db.execute(f'SELECT {columns} FROM word_cards WHERE word_id=? ORDER BY id',(word_id,)):
        card=dict(card); raw=card.pop('data',None)
        if media: card['size']=len(raw or b'')
        card.pop('digest',None)
        if media and raw: card['base64']=base64.b64encode(raw).decode()
        row['cards'].append(card)
    schedule=db.execute('SELECT * FROM srs_cards WHERE word_id=?',(word_id,)).fetchone()
    row['schedule']=dict(schedule) if schedule else None
    from .dictionary import exchange
    lemma=offline_entry(row['word'])[0];row['suggested_lemma']=(exchange(lemma).get('0') or [lemma])[0]
    return row


def validate(data):
    result={}
    for key,limit in [('meaning',3000),('phonetic',150),('part_of_speech',100),('note',2000)]:
        if key in data: result[key]=text(data,key,limit,key=='meaning')
    if 'entry_kind' in data:
        if data['entry_kind'] not in ('word','phrase','pattern'): raise ValueError('词条类型无效。')
        result['entry_kind']=data['entry_kind']
    for key,limit,fields in [('senses',30,{'meaning':3000,'part_of_speech':100,'example':3000}),
                             ('contexts',30,{'text':5000,'translation':5000,'article_title':250}),
                             ('notes',30,{'title':100,'body':2000})]:
        if key not in data: continue
        if not isinstance(data[key],list) or len(data[key])>limit or (key=='senses' and not data[key]): raise ValueError('资料列表格式或数量无效。')
        result[key]=[]
        for item in data[key]:
            if not isinstance(item,dict): raise ValueError('资料必须为对象。')
            cleaned={field:text(item,field,maximum,field in ('meaning','text','title')) for field,maximum in fields.items()}
            if key=='contexts':
                aid=item.get('article_id')
                if aid is not None and (type(aid) is not int or aid<1): raise ValueError('原句文章编号无效。')
                cleaned['article_id']=aid
            result[key].append(cleaned)
    if 'collection_ids' in data:
        ids=data['collection_ids']
        if not isinstance(ids,list) or len(ids)>100 or any(type(i) is not int or i<1 for i in ids): raise ValueError('标签、分组或词书编号无效。')
        result['collection_ids']=list(dict.fromkeys(ids))
    if 'mastery' in data:
        if data['mastery'] not in ('new','learning','mastered'): raise ValueError('掌握度无效。')
        result['mastery']=data['mastery']
    return result


def apply_fields(db,word_id,clean):
    for key in ('meaning','phonetic','part_of_speech','note','mastery'):
        if key in clean: db.execute(f'UPDATE vocabulary SET {key}=? WHERE id=?',(clean[key],word_id))
    if 'entry_kind' in clean: db.execute('UPDATE word_meta SET entry_kind=? WHERE word_id=?',(clean['entry_kind'],word_id))
    if 'senses' in clean:
        db.execute('DELETE FROM word_senses WHERE word_id=?',(word_id,))
        for index,sense in enumerate(clean['senses']):
            db.execute('INSERT OR IGNORE INTO word_senses(word_id,meaning,part_of_speech,example,is_primary) VALUES(?,?,?,?,?)',
                       (word_id,sense['meaning'],sense['part_of_speech'],sense['example'],int(index==0)))
        primary=clean['senses'][0]
        db.execute('UPDATE vocabulary SET meaning=?,part_of_speech=? WHERE id=?',(primary['meaning'],primary['part_of_speech'],word_id))
    elif 'meaning' in clean or 'part_of_speech' in clean:
        base=record(db,word_id)
        db.execute('UPDATE word_senses SET meaning=?,part_of_speech=? WHERE word_id=? AND is_primary=1',(base['meaning'],base['part_of_speech'],word_id))
    if 'contexts' in clean:
        db.execute('DELETE FROM word_contexts WHERE word_id=?',(word_id,))
        for context in clean['contexts']: attach_context(db,word_id,context['text'],context['article_id'],context['translation'],context['article_title'])
        first=clean['contexts'][0] if clean['contexts'] else {'text':'','article_id':None}
        db.execute('UPDATE vocabulary SET context=?,article_id=? WHERE id=?',(first['text'],first['article_id'],word_id))
    if 'notes' in clean:
        db.execute('DELETE FROM word_notes WHERE word_id=?',(word_id,))
        db.executemany('INSERT INTO word_notes(word_id,title,body) VALUES(?,?,?)',[(word_id,item['title'],item['body']) for item in clean['notes']])
    if 'collection_ids' in clean:
        ids=clean['collection_ids']
        if any(not db.execute('SELECT 1 FROM word_collections WHERE id=?',(i,)).fetchone() for i in ids): raise ValueError('分类已不存在，请刷新。')
        db.execute('DELETE FROM word_memberships WHERE word_id=?',(word_id,))
        db.executemany('INSERT INTO word_memberships VALUES(?,?)',[(word_id,i) for i in ids])


@wordbook.before_request
def upload_limits():
    if request.endpoint in ('wordbook.add_card','wordbook.import_entries'): request.max_content_length=24*1024*1024 if request.endpoint.endswith('import_entries') else 4*1024*1024


@wordbook.get('')
def catalog():
    db=get_db()
    from .search import number
    limit = number(request.args, 'limit')
    offset = number(request.args, 'offset') or 0
    if limit is not None and not 1 <= limit <= 100: raise ValueError('每页数量应为1–100。')
    where, params = ['1=1'], []
    query = request.args.get('query', '').strip()
    if len(query)>200: raise ValueError('搜索词不能超过200字符。')
    db.create_function('casefold',1,lambda s:str(s or '').casefold(),deterministic=True)
    if query:
        where.append('''(instr(casefold(v.word||' '||v.meaning||' '||v.note),?)>0
          OR EXISTS(SELECT 1 FROM word_aliases a WHERE a.word_id=v.id AND instr(casefold(a.alias),?)>0)
          OR EXISTS(SELECT 1 FROM word_memberships m JOIN word_collections c ON m.collection_id=c.id WHERE m.word_id=v.id AND instr(casefold(c.name),?)>0))''')
        params.extend([query.casefold()]*3)
    collection = number(request.args,'collection')
    if collection: where.append('EXISTS(SELECT 1 FROM word_memberships WHERE word_id=v.id AND collection_id=?)'); params.append(collection)
    kind = request.args.get('kind','')
    if kind:
        if kind not in ('word','phrase','pattern'): raise ValueError('词条类型无效。')
        where.append('EXISTS(SELECT 1 FROM word_meta WHERE word_id=v.id AND entry_kind=?)');params.append(kind)
    status = request.args.get('status','')
    if status in ('new','learning','mastered'): where.append('v.mastery=?');params.append(status)
    elif status in ('paused','difficult'): where.append('EXISTS(SELECT 1 FROM srs_cards WHERE word_id=v.id AND '+('suspended' if status=='paused' else 'leech')+'=1)')
    elif status: raise ValueError('学习状态无效。')
    clause=' AND '.join(where)
    total=db.execute('SELECT count(*) FROM vocabulary v WHERE '+clause,params).fetchone()[0]
    sql='SELECT v.id FROM vocabulary v WHERE '+clause+' ORDER BY v.id DESC'
    if limit is not None: sql+=' LIMIT ? OFFSET ?';params.extend([limit,offset])
    entries=[detail(db,row['id']) for row in db.execute(sql,params).fetchall()]
    return jsonify(entries=entries,total=total,offset=offset,
                   counts={'entries':db.execute('SELECT count(*) FROM vocabulary').fetchone()[0],
                           'contexts':db.execute('SELECT count(*) FROM word_contexts').fetchone()[0],
                           'difficult':db.execute('SELECT count(*) FROM srs_cards WHERE leech=1').fetchone()[0]},
                   collections=[dict(r) for r in db.execute('SELECT * FROM word_collections ORDER BY kind,name')], templates=TEMPLATES)


@wordbook.route('/collections',methods=['POST'])
def add_collection():
    data=body();kind=data.get('kind');name=text(data,'name',60,True)
    if kind not in ('tag','group','book'): raise ValueError('分类类型无效。')
    db=get_db();db.execute('INSERT OR IGNORE INTO word_collections(kind,name) VALUES(?,?)',(kind,name));db.commit()
    return jsonify(dict(db.execute('SELECT * FROM word_collections WHERE kind=? AND name=? COLLATE NOCASE',(kind,name)).fetchone())),201


@wordbook.route('/collections/<int:collection_id>',methods=['PATCH','DELETE'])
def change_collection(collection_id):
    db=get_db()
    if not db.execute('SELECT 1 FROM word_collections WHERE id=?',(collection_id,)).fetchone(): abort(404)
    if request.method=='DELETE': db.execute('DELETE FROM word_collections WHERE id=?',(collection_id,))
    else: db.execute('UPDATE word_collections SET name=? WHERE id=?',(text(body(),'name',60,True),collection_id))
    db.commit();return jsonify(ok=True)


@wordbook.post('')
def create_entry():
    data=body();clean=validate(data);kind=clean.get('entry_kind','word');word=entry_word(data.get('word'),kind)
    meaning=clean.get('meaning') or (clean.get('senses') or [{}])[0].get('meaning')
    if not meaning: raise ValueError('请填写释义。')
    db=get_db();db.execute('BEGIN IMMEDIATE')
    old=db.execute('SELECT id FROM vocabulary WHERE word=? COLLATE NOCASE',(word,)).fetchone()
    if old: db.rollback();return jsonify(error='该词条已存在，可编辑或合并资料。',id=old['id']),409
    cursor=db.execute('INSERT INTO vocabulary(word,meaning,phonetic,part_of_speech,note,created_at) VALUES(?,?,?,?,?,?)',
                     (word,meaning,clean.get('phonetic',''),clean.get('part_of_speech',''),clean.get('note',''),now()))
    word_id=cursor.lastrowid;ensure_word(db,word_id);apply_fields(db,word_id,clean);db.commit()
    return jsonify(entry=detail(db,word_id)),201


@wordbook.route('/<int:word_id>',methods=['GET','PATCH'])
def edit_entry(word_id):
    db=get_db();record(db,word_id)
    if request.method=='GET': return jsonify(entry=detail(db,word_id))
    data=body();clean=validate(data)
    if type(data.get('version')) is not int: raise ValueError('请提供有效词条版本。')
    db.execute('BEGIN IMMEDIATE')
    current=detail(db,word_id)
    if current['version']!=data['version']: db.rollback();return jsonify(error='词条已被其他操作更新；输入已保留，请重新载入后核对。',current=current),409
    kind=clean.get('entry_kind',current['entry_kind'])
    entry_word(current['word'],kind)
    for alias in current['aliases']: entry_word(alias,kind)
    apply_fields(db,word_id,clean);db.execute('UPDATE word_meta SET version=version+1 WHERE word_id=?',(word_id,));db.commit()
    return jsonify(entry=detail(db,word_id))


def media_data(data):
    kind=data.get('kind')
    if kind=='example': return kind,'',None
    if kind not in ('image','audio'): raise ValueError('卡片类型无效。')
    value=data.get('base64')
    if not isinstance(value,str) or len(value)>2800000: raise ValueError('媒体文件限 2 MB。')
    try: raw=base64.b64decode(value,validate=True)
    except (ValueError,binascii.Error): raise ValueError('文件编码无效。') from None
    if not 16<=len(raw)<=2*1024*1024: raise ValueError('文件为空、损坏或超过 2 MB。')
    mime=data.get('mime')
    signatures={'image/png':raw.startswith(b'\x89PNG\r\n\x1a\n') and raw.endswith(b'IEND\xaeB`\x82'),
                'image/jpeg':raw.startswith(b'\xff\xd8\xff') and raw.endswith(b'\xff\xd9'),
                'image/webp':raw[:4]==b'RIFF' and raw[8:12]==b'WEBP',
                'audio/wav':raw[:4]==b'RIFF' and raw[8:12]==b'WAVE',
                'audio/ogg':raw.startswith(b'OggS'),'audio/mpeg':raw.startswith(b'ID3') or raw[:2] in (b'\xff\xfb',b'\xff\xf3',b'\xff\xf2')}
    if not isinstance(mime,str) or not signatures.get(mime) or not mime.startswith(kind+'/'): raise ValueError('仅支持有效的 PNG/JPEG/WebP 图片或 MP3/WAV/OGG 音频。')
    return kind,mime,raw


def insert_card(db,word_id,data):
    kind,mime,raw=media_data(data);front=text(data,'front',3000);back=text(data,'back',3000)
    if kind=='example' and not front: raise ValueError('请填写例句正面。')
    filename=text(data,'filename',120)
    hashed=hashlib.sha256(raw).hexdigest() if raw else ''
    if raw and db.execute('SELECT 1 FROM word_cards WHERE word_id=? AND digest=?',(word_id,hashed)).fetchone(): return
    if kind=='example' and db.execute('SELECT 1 FROM word_cards WHERE word_id=? AND kind=? AND front=? AND back=?',(word_id,kind,front,back)).fetchone(): return
    if db.execute('SELECT count(*) FROM word_cards WHERE word_id=?',(word_id,)).fetchone()[0]>=20: raise ValueError('每个词条最多 20 张附加卡片。')
    db.execute('INSERT INTO word_cards(word_id,kind,front,back,filename,mime,data,digest) VALUES(?,?,?,?,?,?,?,?)',(word_id,kind,front,back,filename,mime,raw,hashed))


@wordbook.post('/<int:word_id>/cards')
def add_card(word_id):
    data=body();db=get_db();db.execute('BEGIN IMMEDIATE');record(db,word_id)
    current=detail(db,word_id)
    if type(data.get('version')) is not int or data['version']!=current['version']: db.rollback();return jsonify(error='词条已更新，请核对最新版本。',current=current),409
    insert_card(db,word_id,data)
    db.execute('UPDATE word_meta SET version=version+1 WHERE word_id=?',(word_id,));db.commit();return jsonify(entry=detail(db,word_id)),201


@wordbook.delete('/<int:word_id>/cards/<int:card_id>')
def remove_card(word_id,card_id):
    data=body();db=get_db();db.execute('BEGIN IMMEDIATE');record(db,word_id);current=detail(db,word_id)
    if type(data.get('version')) is not int or data['version']!=current['version']: db.rollback();return jsonify(error='词条已更新，请核对最新版本。',current=current),409
    db.execute('DELETE FROM word_cards WHERE id=? AND word_id=?',(card_id,word_id))
    db.execute('UPDATE word_meta SET version=version+1 WHERE word_id=?',(word_id,));db.commit();return jsonify(entry=detail(db,word_id))


@wordbook.get('/media/<int:card_id>')
def media(card_id):
    row=get_db().execute('SELECT mime,data FROM word_cards WHERE id=? AND kind IN (\'image\',\'audio\')',(card_id,)).fetchone()
    if row is None: abort(404)
    return Response(row['data'],mimetype=row['mime'],headers={'Cache-Control':'no-store','Content-Disposition':'inline'})


@wordbook.post('/merge')
def merge():
    data=body();target=data.get('target_id');sources=data.get('source_ids');versions=data.get('versions')
    if type(target) is not int or not isinstance(sources,list) or not 1<=len(sources)<=30 or any(type(i) is not int or i==target for i in sources) or len(set(sources))!=len(sources) or not isinstance(versions,dict): raise ValueError('请选择一个目标词条和至少一个其他词形。')
    db=get_db();db.execute('BEGIN IMMEDIATE');target_kind=detail(db,target)['entry_kind']
    for i in [target,*sources]:
        item=detail(db,i)
        if type(versions.get(str(i))) is not int or item['version']!=versions[str(i)]: db.rollback();return jsonify(error='词条已变化，请刷新后重新选择合并。'),409
        if (item['entry_kind']=='pattern') != (target_kind=='pattern'): raise ValueError('句型仅可与句型合并，请分别整理单词、短语和句型。')
    for source in sources:
        item=detail(db,source)
        for context in item['contexts']: attach_context(db,target,context['text'],context['article_id'],context['translation'],context['article_title'])
        for sense in item['senses']:
            db.execute('INSERT OR IGNORE INTO word_senses(word_id,meaning,part_of_speech,example) VALUES(?,?,?,?)',(target,sense['meaning'],sense['part_of_speech'],sense['example']))
        for note in item['notes']+([{'title':'来自 '+item['word'],'body':item['note']}] if item['note'] else []):
            db.execute('INSERT INTO word_notes(word_id,title,body) VALUES(?,?,?)',(target,note['title'][:100],note['body']))
        db.execute('INSERT OR IGNORE INTO word_aliases VALUES(?,?)',(target,item['word']))
        db.execute('INSERT OR IGNORE INTO word_aliases SELECT ?,alias FROM word_aliases WHERE word_id=?',(target,source))
        db.execute('INSERT OR IGNORE INTO word_memberships SELECT ?,collection_id FROM word_memberships WHERE word_id=?',(target,source))
        db.execute('UPDATE word_cards SET word_id=? WHERE word_id=?',(target,source))
        db.execute('UPDATE srs_events SET word_id=? WHERE word_id=?',(target,source))
        db.execute('DELETE FROM vocabulary WHERE id=?',(source,))
    for table,limit in [('word_senses',30),('word_contexts',30),('word_notes',30),('word_cards',20),('word_memberships',100),('word_aliases',100)]:
        if db.execute(f'SELECT count(*) FROM {table} WHERE word_id=?',(target,)).fetchone()[0]>limit:
            raise ValueError('合并后的资料超过单词条容量，请先整理资料后再合并；原词条未改变。')
    db.execute('UPDATE word_meta SET version=version+1 WHERE word_id=?',(target,));db.commit()
    return jsonify(entry=detail(db,target))


@wordbook.get('/export')
def export():
    db=get_db();ids=request.args.get('ids','');selected=None
    if ids:
        if not re.fullmatch(r'\d+(?:,\d+){0,499}',ids): raise ValueError('导出编号无效，单次最多 500 条。')
        selected={int(i) for i in ids.split(',')}
    rows=db.execute('SELECT id FROM vocabulary ORDER BY id').fetchall()
    rows=[row for row in rows if selected is None or row['id'] in selected]
    if len(rows)>500: raise ValueError('单次导出最多 500 条，请筛选词书或勾选词条分批导出。')
    include=request.args.get('media')=='1' and request.args.get('format','json')!='csv'
    if include:
        chosen=[row['id'] for row in rows]
        if chosen:
            total=db.execute('SELECT coalesce(sum(length(data)),0) FROM word_cards WHERE word_id IN ('+','.join('?' for _ in chosen)+')',chosen).fetchone()[0]
            if total>14*1024*1024: raise ValueError('含媒体的导出过大，请减少词条或关闭媒体导出。')
    entries=[detail(db,row['id'],include) for row in rows]
    if request.args.get('format','json')=='csv':
        stream=io.StringIO();writer=csv.writer(stream);writer.writerow(['word','meaning','phonetic','part_of_speech','note','entry_kind','tags','groups','books','context'])
        for row in entries:
            values=[row.get(k,'') for k in ('word','meaning','phonetic','part_of_speech','note','entry_kind')]
            values += ['|'.join(c['name'] for c in row['collections'] if c['kind']==kind) for kind in ('tag','group','book')]+[row['context']]
            writer.writerow(["'"+v if v.startswith(('=','+','-','@','\t','\r')) else v for v in values])
        return Response('\ufeff'+stream.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="margin-words.csv"'})
    packed=json.dumps({'format':'margin-wordbook-v1','entries':entries},ensure_ascii=False)
    if len(packed.encode())>20*1024*1024: raise ValueError('含媒体的导出过大，请减少词条或关闭媒体导出。')
    return Response(packed,mimetype='application/json',headers={'Content-Disposition':'attachment; filename="margin-words.json"'})


@wordbook.post('/import')
def import_entries():
    data=body();mode=data.get('mode','skip')
    if mode not in ('skip','merge'): raise ValueError('导入模式无效。')
    rows=data.get('entries')
    if data.get('format')=='csv':
        raw=text(data,'csv',2*1024*1024,True).lstrip('\ufeff')
        try: rows=list(csv.DictReader(io.StringIO(raw),strict=True))
        except csv.Error: raise ValueError('CSV 格式无效，请检查引号、字段长度与列数。') from None
        for row in rows:
            if None in row: raise ValueError('CSV 列数不一致，含逗号的字段需要引号包围。')
            row['collections']=[{'kind':kind,'name':name.strip()} for key,kind in [('tags','tag'),('groups','group'),('books','book')] for name in (row.get(key) or '').split('|') if name.strip()]
    if not isinstance(rows,list) or not 1<=len(rows)<=500: raise ValueError('请选择包含 1–500 条词条的 JSON 或 CSV。')
    prepared=[]
    for index,row in enumerate(rows):
        try:
            if not isinstance(row,dict): raise ValueError('词条不是对象。')
            kind=row.get('entry_kind') or ('phrase' if ' ' in str(row.get('word','')) else 'word')
            word=entry_word(row.get('word'),kind);clean=validate({**row,'entry_kind':kind})
            meaning=clean.get('meaning') or (clean.get('senses') or [{}])[0].get('meaning')
            if not meaning: raise ValueError('释义为空。')
            collections=row.get('collections',[])
            if not isinstance(collections,list) or len(collections)>100: raise ValueError('分类列表无效。')
            groups=[]
            for group in collections:
                if not isinstance(group,dict) or group.get('kind') not in ('tag','group','book'): raise ValueError('分类类型无效。')
                groups.append((group['kind'],text(group,'name',60,True)))
            cards=row.get('cards',[])
            if not isinstance(cards,list) or len(cards)>20: raise ValueError('卡片数量无效。')
            for card in cards:
                if not isinstance(card,dict): raise ValueError('卡片无效。')
                if card.get('kind')!='example' and 'base64' not in card: continue
                media_data(card);text(card,'front',3000);text(card,'back',3000);text(card,'filename',120)
            aliases=row.get('aliases',[])
            if not isinstance(aliases,list) or len(aliases)>100: raise ValueError('词形列表无效。')
            aliases=[entry_word(alias,kind) for alias in aliases]
            for context in clean.get('contexts',[]): context['article_id']=None
            if row.get('context') and 'contexts' not in clean:
                clean['contexts']=[{'text':text(row,'context',5000),'translation':'','article_id':None,'article_title':''}]
            prepared.append((word,kind,meaning,clean,groups,cards,aliases))
        except ValueError as error: raise ValueError(f'第 {index+1} 条：{error}') from None
    db=get_db()
    if data.get('preview') is True:
        seen={row[0].lower() for row in db.execute('SELECT word FROM vocabulary')};duplicates=0
        for item in prepared:
            if item[0] in seen: duplicates+=1
            seen.add(item[0])
        return jsonify(count=len(prepared),duplicates=duplicates,examples=[item[0] for item in prepared[:8]])
    db.execute('BEGIN IMMEDIATE');added=merged=skipped=0
    for word,kind,meaning,clean,groups,cards,aliases in prepared:
        existing=db.execute('SELECT id FROM vocabulary WHERE word=? COLLATE NOCASE',(word,)).fetchone()
        if existing and mode=='skip': skipped+=1;continue
        if existing:
            word_id=existing['id'];merged+=1
            for sense in clean.get('senses',[{'meaning':meaning,'part_of_speech':clean.get('part_of_speech',''),'example':''}]):
                db.execute('INSERT OR IGNORE INTO word_senses(word_id,meaning,part_of_speech,example) VALUES(?,?,?,?)',(word_id,sense['meaning'],sense['part_of_speech'],sense['example']))
            for context in clean.get('contexts',[]): attach_context(db,word_id,context['text'],None,context['translation'],context['article_title'])
            for note in clean.get('notes',[])+([{'title':'导入笔记','body':clean['note']}] if clean.get('note') else []):
                if not db.execute('SELECT 1 FROM word_notes WHERE word_id=? AND title=? AND body=?',(word_id,note['title'],note['body'])).fetchone():
                    db.execute('INSERT INTO word_notes(word_id,title,body) VALUES(?,?,?)',(word_id,note['title'],note['body']))
        else:
            cursor=db.execute('INSERT INTO vocabulary(word,meaning,phonetic,part_of_speech,note,created_at) VALUES(?,?,?,?,?,?)',(word,meaning,clean.get('phonetic',''),clean.get('part_of_speech',''),clean.get('note',''),now()))
            word_id=cursor.lastrowid;ensure_word(db,word_id);apply_fields(db,word_id,{k:v for k,v in clean.items() if k!='collection_ids'});added+=1
        for kind,name in groups:
            db.execute('INSERT OR IGNORE INTO word_collections(kind,name) VALUES(?,?)',(kind,name))
            cid=db.execute('SELECT id FROM word_collections WHERE kind=? AND name=? COLLATE NOCASE',(kind,name)).fetchone()['id']
            db.execute('INSERT OR IGNORE INTO word_memberships VALUES(?,?)',(word_id,cid))
        for card in cards:
            if card.get('kind')=='example' or 'base64' in card: insert_card(db,word_id,card)
        db.executemany('INSERT OR IGNORE INTO word_aliases VALUES(?,?)',[(word_id,alias) for alias in aliases if alias!=word])
        for table,limit in [('word_senses',30),('word_contexts',30),('word_notes',30),('word_aliases',100),('word_memberships',100)]:
            if db.execute(f'SELECT count(*) FROM {table} WHERE word_id=?',(word_id,)).fetchone()[0]>limit: raise ValueError('导入后词条资料超出容量，整批未写入，请拆分整理。')
        db.execute('UPDATE word_meta SET version=version+1 WHERE word_id=?',(word_id,))
    db.commit();return jsonify(added=added,merged=merged,skipped=skipped)
