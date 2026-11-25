"""SM-2 scheduling with a 10-minute relearning step and idempotent grading."""
import hashlib
import json
import math
import re
from datetime import datetime, timedelta

from flask import Blueprint, abort, jsonify, request
from .db import CHINA_TZ, current_timezone, get_db

review=Blueprint('review',__name__,url_prefix='/api/review')
SCHEMA='''
CREATE TABLE IF NOT EXISTS srs_cards(word_id INTEGER PRIMARY KEY REFERENCES vocabulary(id) ON DELETE CASCADE,
 state TEXT NOT NULL DEFAULT 'new', repetitions INTEGER NOT NULL DEFAULT 0, interval_days INTEGER NOT NULL DEFAULT 0,
 ease REAL NOT NULL DEFAULT 2.5, due_at TEXT NOT NULL, last_review_at TEXT,
 lapses INTEGER NOT NULL DEFAULT 0, suspended INTEGER NOT NULL DEFAULT 0, leech INTEGER NOT NULL DEFAULT 0,
 version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS srs_events(id INTEGER PRIMARY KEY AUTOINCREMENT,
 request_id TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
 word_id INTEGER REFERENCES vocabulary(id) ON DELETE SET NULL, original_word_id INTEGER NOT NULL,
 word_snapshot TEXT NOT NULL, day TEXT NOT NULL, reviewed_at TEXT NOT NULL,
 grade INTEGER NOT NULL, mode TEXT NOT NULL, attempt TEXT NOT NULL DEFAULT '',
 state_before TEXT NOT NULL, response_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS srs_day ON srs_events(day);
CREATE INDEX IF NOT EXISTS srs_due ON srs_cards(suspended,due_at);
'''
DEFAULTS={'new_limit':10,'review_limit':50}


def clock(): return datetime.now(current_timezone()).replace(microsecond=0)


def ensure_schedule(db,word_id):
    db.execute('INSERT OR IGNORE INTO srs_cards(word_id,due_at) VALUES(?,?)',(word_id,clock().isoformat()))


def settings(db):
    values=DEFAULTS.copy()
    for key in values:
        row=db.execute('SELECT value FROM settings WHERE key=?',('srs.'+key,)).fetchone()
        try:
            value=json.loads(row['value']) if row else values[key]
            if type(value) is int and 0<=value<=(200 if key=='new_limit' else 2000): values[key]=value
        except (ValueError,TypeError): pass
    return values


@review.route('/settings',methods=['GET','PATCH'])
def preferences():
    db=get_db()
    if request.method=='PATCH':
        data=request.get_json()
        if not isinstance(data,dict) or set(data)-set(DEFAULTS): raise ValueError('复习设置无效。')
        for key,value in data.items():
            if type(value) is not int or not 0<=value<=(200 if key=='new_limit' else 2000): raise ValueError('新词限额为 0–200，复习限额为 0–2000。')
        for key,value in data.items(): db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('srs.'+key,json.dumps(value)))
        db.commit()
    return jsonify(settings(db))


def usage(db,day):
    events=db.execute('SELECT original_word_id,state_before FROM srs_events WHERE day=?',(day,)).fetchall()
    introduced={r['original_word_id'] for r in events if r['state_before']=='new'}
    reviewed={r['original_word_id'] for r in events if r['state_before']=='review'}-introduced
    return {'new_today':len(introduced),'review_today':len(reviewed),'answers_today':len(events)},introduced,reviewed


def cloze(word,contexts,aliases):
    terms=sorted({word,*aliases},key=len,reverse=True)
    expression=r"(?<![A-Za-z'’-])(?:"+'|'.join(re.escape(term).replace(r'\ ',r'\s+').replace("'","['’]") for term in terms)+r")(?![A-Za-z'’-])"
    for context in contexts:
        if re.search(expression,context['text'],re.I): return re.sub(expression,'____',context['text'],flags=re.I)
    return ''


@review.get('/queue')
def queue():
    from .wordbook import detail
    db=get_db();current=clock();config=settings(db);counts,introduced,reviewed=usage(db,current.date().isoformat())
    rows=db.execute('SELECT * FROM srs_cards WHERE suspended=0 AND due_at<=? ORDER BY due_at,word_id',(current.isoformat(),)).fetchall()
    collection=request.args.get('collection','')
    if collection:
        if not collection.isdigit() or not db.execute('SELECT 1 FROM word_collections WHERE id=?',(int(collection),)).fetchone(): raise ValueError('词书或分组不存在。')
        ids={r[0] for r in db.execute('SELECT word_id FROM word_memberships WHERE collection_id=?',(int(collection),))}
        rows=[row for row in rows if row['word_id'] in ids]
    new=[row for row in rows if row['state']=='new']; due=[row for row in rows if row['state']=='review']; relearn=[row for row in rows if row['state']=='relearning']
    allowance_new=max(0,config['new_limit']-counts['new_today']);allowance_review=max(0,config['review_limit']-counts['review_today'])
    selected=relearn+[row for row in due if row['word_id'] in reviewed or row['word_id'] in introduced][:200]+[row for row in due if row['word_id'] not in reviewed and row['word_id'] not in introduced][:allowance_review]+new[:allowance_new]
    cards=[]
    for schedule in selected[:200]:
        entry=detail(db,schedule['word_id']);entry['cloze']=cloze(entry['word'],entry['contexts'],entry['aliases']);cards.append(entry)
    paused=db.execute('SELECT count(*) FROM srs_cards WHERE suspended=1').fetchone()[0]
    leeches=db.execute('SELECT count(*) FROM srs_cards WHERE leech=1').fetchone()[0]
    return jsonify(cards=cards,now=current.isoformat(),settings=config,stats={**counts,'due':len(due),'new':len(new),'relearning':len(relearn),'paused':paused,'difficult':leeches},
                   collections=[dict(r) for r in db.execute('SELECT * FROM word_collections ORDER BY kind,name')])


def next_state(card,grade,current):
    ease=max(1.3,card['ease']+.1-(5-grade)*(.08+(5-grade)*.02))
    if grade<3:
        return {'state':'relearning','repetitions':0,'interval_days':0,'ease':round(ease,4),
                'due_at':(current+timedelta(minutes=10)).isoformat(),'lapses':card['lapses']+1}
    repetitions=card['repetitions']+1
    interval=1 if repetitions==1 else 6 if repetitions==2 else min(36500,math.ceil(card['interval_days']*card['ease']))
    return {'state':'review','repetitions':repetitions,'interval_days':interval,'ease':round(ease,4),
            'due_at':(current+timedelta(days=interval)).isoformat(),'lapses':card['lapses']}


@review.post('/answer')
def answer():
    data=request.get_json()
    if not isinstance(data,dict): raise ValueError('请求应为 JSON 对象。')
    required=('word_id','version','content_version','grade')
    if any(type(data.get(key)) is not int for key in required) or not 0<=data['grade']<=5: raise ValueError('评分或卡片版本无效。')
    token=data.get('request_id');mode=data.get('mode','front');attempt=data.get('attempt','')
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',token): raise ValueError('评分请求编号无效。')
    if mode not in ('front','cloze','listen','spelling') or not isinstance(attempt,str) or len(attempt)>500: raise ValueError('复习模式或答案无效。')
    canonical={key:data[key] for key in required};canonical.update(mode=mode,attempt=attempt)
    fingerprint=hashlib.sha256(json.dumps(canonical,sort_keys=True).encode()).hexdigest()
    db=get_db();db.execute('BEGIN IMMEDIATE')
    old=db.execute('SELECT fingerprint,response_json FROM srs_events WHERE request_id=?',(token,)).fetchone()
    if old:
        db.rollback()
        if old['fingerprint']!=fingerprint: return jsonify(error='同一请求编号不能用于不同评分。'),409
        return jsonify(**json.loads(old['response_json']),duplicate=True)
    row=db.execute('SELECT s.*,v.word,m.version AS content_version FROM srs_cards s JOIN vocabulary v ON v.id=s.word_id JOIN word_meta m ON m.word_id=s.word_id WHERE s.word_id=?',(data['word_id'],)).fetchone()
    if row is None: db.rollback();abort(404)
    current=clock();counts,introduced,reviewed=usage(db,current.date().isoformat());config=settings(db)
    if row['version']!=data['version'] or row['content_version']!=data['content_version']:
        db.rollback();return jsonify(error='卡片或词条已更新，请刷新到期列表后重试。'),409
    if row['suspended'] or row['due_at']>current.isoformat(): db.rollback();return jsonify(error='这张卡片已暂停或尚未到期。'),409
    if ((row['state']=='new' and counts['new_today']>=config['new_limit']) or
        (row['state']=='review' and row['word_id'] not in introduced|reviewed and counts['review_today']>=config['review_limit'])):
        db.rollback();return jsonify(error='今日学习限额已用完，可在设置中调整或明日继续。'),409
    scheduled=next_state(row,data['grade'],current)
    db.execute('''UPDATE srs_cards SET state=:state,repetitions=:repetitions,interval_days=:interval_days,ease=:ease,
        due_at=:due_at,lapses=:lapses,last_review_at=:last_review_at,leech=CASE WHEN :lapses>=8 THEN 1 ELSE leech END,version=version+1 WHERE word_id=:word_id''',
        {**scheduled,'last_review_at':current.isoformat(),'word_id':row['word_id']})
    response={'card':dict(db.execute('SELECT * FROM srs_cards WHERE word_id=?',(row['word_id'],)).fetchone())}
    aliases=[r[0] for r in db.execute('SELECT alias FROM word_aliases WHERE word_id=?',(row['word_id'],))]
    normalize=lambda value: re.sub(r'\s+',' ',value.strip().lower().replace('’',"'"))
    response['correct']=normalize(attempt) in {normalize(value) for value in [row['word'],*aliases]} if attempt else None
    db.execute('''INSERT INTO srs_events(request_id,fingerprint,word_id,original_word_id,word_snapshot,day,reviewed_at,grade,mode,attempt,state_before,response_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(token,fingerprint,row['word_id'],row['word_id'],row['word'],current.date().isoformat(),current.isoformat(),data['grade'],mode,attempt,row['state'],json.dumps(response)))
    db.commit();return jsonify(**response,duplicate=False)


@review.patch('/cards/<int:word_id>')
def manage(word_id):
    data=request.get_json()
    if not isinstance(data,dict) or set(data)-{'version','suspended','leech'} or type(data.get('version')) is not int or not set(data)&{'suspended','leech'}: raise ValueError('复习管理请求无效。')
    for key in ('suspended','leech'):
        if key in data and type(data[key]) is not bool: raise ValueError('暂停与困难标记应为布尔值。')
    db=get_db();db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM srs_cards WHERE word_id=?',(word_id,)).fetchone()
    if row is None: db.rollback();abort(404)
    if row['version']!=data['version']: db.rollback();return jsonify(error='复习状态已更新，请刷新。'),409
    db.execute('UPDATE srs_cards SET suspended=?,leech=?,version=version+1 WHERE word_id=?',(int(data.get('suspended',row['suspended'])),int(data.get('leech',row['leech'])),word_id))
    db.commit();return jsonify(card=dict(db.execute('SELECT * FROM srs_cards WHERE word_id=?',(word_id,)).fetchone()))
