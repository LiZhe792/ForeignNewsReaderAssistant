"""Local learning reports. Missing observations stay missing; snapshots survive deletion."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import calendar
import csv
import hashlib
import io
import json
import re
import uuid

from flask import Blueprint, Response, abort, jsonify, request
from .db import CHINA_TZ, current_timezone, get_db, now
from .dictionary import normalize_term
from .personalization import digest, lexicon, knowledge
from .reading_flow import contains_term

analytics = Blueprint('analytics', __name__, url_prefix='/api/analytics')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS learning_reading_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE, fingerprint TEXT NOT NULL DEFAULT '',
 article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL, original_article_id INTEGER NOT NULL,
 article_hash TEXT NOT NULL, title TEXT NOT NULL, category TEXT NOT NULL,
 day TEXT NOT NULL, occurred_at TEXT NOT NULL, seconds INTEGER NOT NULL DEFAULT 0,
 advanced_words REAL NOT NULL DEFAULT 0, speed_seconds INTEGER NOT NULL DEFAULT 0,
 completed_words INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
 response_json TEXT NOT NULL DEFAULT '{}', legacy INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS learning_reading_day ON learning_reading_events(day);
CREATE TABLE IF NOT EXISTS learning_word_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, original_word_id INTEGER NOT NULL,
 word TEXT NOT NULL, kind TEXT NOT NULL, occurred_at TEXT NOT NULL, day TEXT NOT NULL,
 UNIQUE(original_word_id,kind)
);
CREATE INDEX IF NOT EXISTS learning_word_day ON learning_word_events(day);
CREATE TABLE IF NOT EXISTS learning_lookup_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
 word TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('lookup','encounter')),
 article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL, original_article_id INTEGER,
 article_hash TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
 paragraph_index INTEGER NOT NULL DEFAULT -1, occurred_at TEXT NOT NULL, day TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS learning_lookup_day ON learning_lookup_events(day);
CREATE TRIGGER IF NOT EXISTS learning_word_add AFTER INSERT ON vocabulary BEGIN
 INSERT OR IGNORE INTO learning_word_events(original_word_id,word,kind,occurred_at,day)
 VALUES(NEW.id,NEW.word,'added',NEW.created_at,substr(NEW.created_at,1,10));
END;
CREATE TRIGGER IF NOT EXISTS learning_word_remove AFTER DELETE ON vocabulary BEGIN
 INSERT OR IGNORE INTO learning_word_events(original_word_id,word,kind,occurred_at,day)
 VALUES(OLD.id,OLD.word,'removed',margin_now(),substr(margin_now(),1,10));
END;
'''


def clock():
    return datetime.now(current_timezone())


def initialize(db):
    db.executescript(SCHEMA)
    if not db.execute("SELECT 1 FROM settings WHERE key='analytics.started_at'").fetchone():
        stamp = now()
        db.execute("INSERT INTO settings VALUES('analytics.started_at',?)", (stamp,))
        db.execute("INSERT OR IGNORE INTO learning_word_events(original_word_id,word,kind,occurred_at,day) SELECT id,word,'added',created_at,substr(created_at,1,10) FROM vocabulary")
        # A completion date can be recovered, but its day's reading duration cannot.
        for row in db.execute('SELECT * FROM articles WHERE completed_at IS NOT NULL'):
            db.execute('''INSERT INTO learning_reading_events(article_id,original_article_id,article_hash,title,category,day,occurred_at,completed_words,completed,legacy)
                VALUES(?,?,?,?,?,?,?,?,1,1)''', (row['id'],row['id'],digest(row['content']),row['title'],row['category'],local_day(row['completed_at']),row['completed_at'],row['word_count']))
        db.commit()


def local_day(stamp):
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return parsed.astimezone(current_timezone()).date().isoformat() if parsed.tzinfo else parsed.date().isoformat()


def token(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',value):
        raise ValueError('请提供有效的记录编号。')
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def record_reading(db, article, data, response, day=None):
    seconds = data.get('seconds',0)
    advanced = max(0,data.get('progress',0) - article['progress']) * article['word_count'] / 100
    complete = bool(response['completed'] and not article['completed_at'])
    speed_seconds = seconds if seconds >= 10 and advanced > 0 and not complete else 0
    stamp = now()
    db.execute('''INSERT INTO learning_reading_events(request_id,fingerprint,article_id,original_article_id,article_hash,title,category,day,occurred_at,
        seconds,advanced_words,speed_seconds,completed_words,completed,response_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (data.get('request_id'),fingerprint([article['id'],data.get('progress',0),data.get('position',0),seconds]),article['id'],article['id'],digest(article['content']),article['title'],article['category'],
         day or local_day(stamp),stamp,seconds,advanced if speed_seconds else 0,speed_seconds,article['word_count'] if complete else 0,int(complete),json.dumps(response)))


def record_lookup(db, term, kind, article=None, paragraph=-1, request_id=None, occurred_at=None):
    stamp=occurred_at or now(); hashed=digest(article['content']) if article else ''
    if kind=='encounter':
        # Reopening the same word in one paragraph on the same day isn't a new encounter.
        request_id=fingerprint([kind,article['id'],hashed,paragraph,term,local_day(stamp)])
    request_id=request_id or uuid.uuid4().hex
    canonical=[term,kind,article['id'] if article else None,hashed,paragraph]
    db.execute('''INSERT OR IGNORE INTO learning_lookup_events(request_id,fingerprint,word,kind,article_id,original_article_id,article_hash,title,category,paragraph_index,occurred_at,day)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(request_id,fingerprint(canonical),term,kind,article['id'] if article else None,article['id'] if article else None,hashed,
        article['title'] if article else '',article['category'] if article else '',paragraph,stamp,local_day(stamp)))


@analytics.post('/lookup')
def observe_lookup():
    data=request.get_json()
    if not isinstance(data,dict) or set(data)-{'request_id','word','article_id','article_hash','paragraph_index','occurred_at'}:
        raise ValueError('查词记录格式无效。')
    term=normalize_term(data.get('word')); identity=data.get('article_id'); paragraph=data.get('paragraph_index',-1); request_id=token(data.get('request_id'))
    if identity is not None and (type(identity) is not int or not 1<=identity<=2**53-1): raise ValueError('文章编号无效。')
    if type(paragraph) is not int or paragraph < -1: raise ValueError('段落编号无效。')
    stamp=data.get('occurred_at',now())
    if not isinstance(stamp,str) or len(stamp)>60:raise ValueError('查词时间格式无效。')
    try:
        parsed=datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None or not clock()-timedelta(days=7)<=parsed<=clock()+timedelta(minutes=2):raise ValueError()
        stamp=parsed.astimezone(current_timezone()).isoformat()
    except (ValueError,TypeError):raise ValueError('查词时间无效或超过 7 天保存范围。') from None
    db=get_db(); db.execute('BEGIN IMMEDIATE'); article=None
    if identity is not None:
        row=db.execute('SELECT * FROM articles WHERE id=?',(identity,)).fetchone()
        if row is None: db.rollback();abort(404)
        article=dict(row)
        if data.get('article_hash')!=digest(article['content']): db.rollback();return jsonify(error='文章已变化，未记录过期查词。'),409
    elif paragraph != -1: raise ValueError('无文章时不能指定段落。')
    canonical=[term,'lookup',identity,digest(article['content']) if article else '',paragraph]
    old=db.execute('SELECT fingerprint FROM learning_lookup_events WHERE request_id=?',(request_id,)).fetchone()
    if old:
        db.rollback()
        if old['fingerprint']!=fingerprint(canonical):return jsonify(error='记录编号已用于其他查词。'),409
        return jsonify(ok=True,duplicate=True)
    if article and paragraph>=0:
        parts=[p.strip() for p in re.split(r'\n\s*\n|\n',article['content']) if p.strip()]
        if paragraph>=len(parts) or not contains_term(parts[paragraph],term):raise ValueError('单词不在指定段落中。')
    record_lookup(db,term,'lookup',article,paragraph,request_id,stamp)
    if article and paragraph>=0:record_lookup(db,term,'encounter',article,paragraph,occurred_at=stamp)
    db.commit();return jsonify(ok=True,duplicate=False),201


def period_bounds(args):
    period=args.get('period','week'); anchor=args.get('date',clock().date().isoformat())
    try:
        if not isinstance(anchor,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',anchor): raise ValueError()
        selected=date.fromisoformat(anchor)
        if not 1901<=selected.year<=9998:raise ValueError()
        if period=='week':start=selected-timedelta(days=selected.weekday());end=start+timedelta(days=6)
        elif period=='month':start=selected.replace(day=1);end=selected.replace(day=calendar.monthrange(selected.year,selected.month)[1])
        elif period=='year':start=selected.replace(month=1,day=1);end=selected.replace(month=12,day=31)
        else:raise ValueError()
    except (ValueError,OverflowError):raise ValueError('请选择 week / month / year 与有效日期 YYYY-MM-DD（1901–9998 年）。') from None
    return period,start,end


def percentage(numerator,denominator):
    return round(numerator/denominator*100,1) if denominator else None


def report(args):
    if set(args)-{'period','date'}:raise ValueError('包含不支持的统计筛选。')
    period,start,end=period_bounds(args); first,last=start.isoformat(),end.isoformat(); db=get_db()
    daily={}
    cursor=start
    while cursor<=end:
        key=cursor.isoformat();daily[key]={'date':key,'seconds':0,'completed':0,'completed_words':0,'vocabulary_added':0,'vocabulary_removed':0,'lookups':0,'encounters':0,'reviews':0,'remembered':0,'forgotten':0,'advanced_words':0,'speed_seconds':0};cursor+=timedelta(days=1)
    for row in db.execute('SELECT * FROM checkins WHERE date BETWEEN ? AND ?',(first,last)):
        daily[row['date']].update(seconds=row['reading_seconds'],completed=row['articles_completed'])
    topics=defaultdict(lambda:{'seconds':0,'completed':0,'lookups':0}); materials={}
    def material(row):
        key=(row['original_article_id'],row['article_hash'])
        if key not in materials:materials[key]={'id':row['article_id'],'original_id':row['original_article_id'],'hash':row['article_hash'],'title':row['title'],'category':row['category'],'seconds':0,'lookups':0,'completed':0}
        return materials[key]
    for row in db.execute('SELECT * FROM learning_reading_events WHERE day BETWEEN ? AND ?',(first,last)):
        d=daily[row['day']]
        for key in ('completed_words','advanced_words','speed_seconds'):d[key]+=row[key]
        if row['seconds'] or row['completed']:
            t=topics[row['category']];t['seconds']+=row['seconds'];t['completed']+=row['completed']
        m=material(row);m['seconds']+=row['seconds'];m['completed']+=row['completed']
    baseline=db.execute("SELECT COUNT(*) FROM learning_word_events WHERE day<? AND kind='added'",(first,)).fetchone()[0]-db.execute("SELECT COUNT(*) FROM learning_word_events WHERE day<? AND kind='removed'",(first,)).fetchone()[0]
    for row in db.execute('SELECT day,kind,COUNT(*) AS count FROM learning_word_events WHERE day BETWEEN ? AND ? GROUP BY day,kind',(first,last)):
        daily[row['day']]['vocabulary_'+row['kind']]+=row['count']
    queried=Counter(); encounter_words=defaultdict(lambda:{'count':0,'articles':set(),'days':set()})
    for row in db.execute('SELECT * FROM learning_lookup_events WHERE day BETWEEN ? AND ? ORDER BY id',(first,last)):
        if row['kind']=='lookup':
            daily[row['day']]['lookups']+=1;queried[row['word']]+=1
            if row['original_article_id']:
                topics[row['category']]['lookups']+=1;material(row)['lookups']+=1
        else:
            daily[row['day']]['encounters']+=1;item=encounter_words[row['word']];item['count']+=1;item['articles'].add((row['original_article_id'],row['article_hash']));item['days'].add(row['day'])
    spelling_correct=spelling_total=first_misses=0; forgotten_words=Counter();reviewed_words=set()
    for row in db.execute('SELECT * FROM srs_events WHERE day BETWEEN ? AND ?',(first,last)):
        d=daily[row['day']];d['reviews']+=1;d['remembered']+=int(row['grade']>=3);reviewed_words.add(row['original_word_id'])
        if row['grade']<3:
            if row['state_before']=='new':first_misses+=1
            else:d['forgotten']+=1;forgotten_words[row['word_snapshot']]+=1
        response=json.loads(row['response_json'])
        if row['mode']=='spelling' and type(response.get('correct')) is bool:
            spelling_total+=1;spelling_correct+=int(response['correct'])
    running=baseline
    for d in daily.values():
        running+=d['vocabulary_added']-d['vocabulary_removed'];d['vocabulary_total']=running
        d['speed_wpm']=round(d['advanced_words']*60/d['speed_seconds'],1) if d['speed_seconds'] else None
        d['remembered_rate']=percentage(d['remembered'],d['reviews'])
    buckets=[]
    for d in daily.values():
        label=d['date'][:7] if period=='year' else d['date']
        if not buckets or buckets[-1]['label']!=label:buckets.append({'label':label,**{k:0 for k in ('seconds','completed','completed_words','vocabulary_added','vocabulary_removed','lookups','encounters','reviews','remembered','forgotten','advanced_words','speed_seconds')}})
        b=buckets[-1]
        for key in tuple(b):
            if key not in ('label','vocabulary_total'):b[key]+=d[key]
        b['vocabulary_total']=d['vocabulary_total']
    for b in buckets:
        b['speed_wpm']=round(b['advanced_words']*60/b['speed_seconds'],1) if b['speed_seconds'] else None
        b['remembered_rate']=percentage(b['remembered'],b['reviews'])
    # The above counters are historical; distributions and article rates are current snapshots.
    mastery={key:0 for key in ('new','learning','mastered')}
    for row in db.execute('SELECT mastery,COUNT(*) AS count FROM vocabulary GROUP BY mastery'):mastery[row['mastery']]=row['count']
    live={r['id']:dict(r) for r in db.execute('SELECT * FROM articles')}; known=knowledge(db)
    for row in live.values():
        key=(row['id'],digest(row['content']))
        if key not in materials:materials[key]={'id':row['id'],'original_id':row['id'],'hash':key[1],'title':row['title'],'category':row['category'],'seconds':0,'lookups':0,'completed':0}
    ordered=sorted(materials.values(),key=lambda m:(-m['seconds'],-m['lookups'],m['original_id'],m['hash']))
    for m in ordered[:50]:
        article=live.get(m['original_id']);m['current']=bool(article and digest(article['content'])==m['hash'])
        m['unmastered_rate']=None;m['recorded_words']=0;m['word_count']=None
        if m['current']:
            entries,base=lexicon(article['content']);count=sum(n for term,lemma,n,rank in entries if (known.get(term) or known.get(lemma)) in ('learning','stable_review'))
            m.update(unmastered_rate=percentage(count,base['word_count']),recorded_words=count,word_count=base['word_count'])
    totals={key:sum(d[key] for d in daily.values()) for key in ('seconds','completed','completed_words','vocabulary_added','vocabulary_removed','lookups','encounters','reviews','remembered','forgotten','advanced_words','speed_seconds')}
    totals.update(active_reading_days=sum(d['seconds']>0 or d['completed']>0 for d in daily.values()),vocabulary_net=totals['vocabulary_added']-totals['vocabulary_removed'],
        remembered_rate=percentage(totals['remembered'],totals['reviews']),speed_wpm=round(totals['advanced_words']*60/totals['speed_seconds'],1) if totals['speed_seconds'] else None,
        first_learning_misses=first_misses,spelling_total=spelling_total,spelling_correct=spelling_correct,spelling_rate=percentage(spelling_correct,spelling_total),reviewed_words=len(reviewed_words))
    tracking=db.execute("SELECT value FROM settings WHERE key='analytics.started_at'").fetchone()[0]
    tracking_day=local_day(tracking); today=clock().date().isoformat()
    for d in daily.values():
        d['lookup_observed']=(tracking_day<=d['date']<=today) or d['lookups']>0
    for b in buckets:
        relevant=[d for d in daily.values() if d['date'].startswith(b['label'])]
        b['lookup_observed']=any(d['lookup_observed'] for d in relevant)
    coverage='none' if not any(d['lookup_observed'] for d in daily.values()) else 'full' if all(d['lookup_observed'] for d in daily.values()) else 'partial'
    repeated=[{'word':word,'count':v['count'],'articles':len(v['articles']),'days':len(v['days'])} for word,v in encounter_words.items() if v['count']>=2]
    repeated.sort(key=lambda v:(-v['count'],v['word']))
    return {'period':period,'start':first,'end':last,'today':clock().date().isoformat(),'tracking_since':tracking,'lookup_coverage':coverage,'totals':totals,'series':buckets,'daily':list(daily.values()),
        'mastery':mastery,'vocabulary_current':sum(mastery.values()),'vocabulary_at_start':baseline,
        'topics':[{'category':cat,**v} for cat,v in sorted(topics.items(),key=lambda item:(-item[1]['seconds'],-item[1]['completed'],item[0]))],
        'top_lookups':[{'word':w,'count':c} for w,c in sorted(queried.items(),key=lambda item:(-item[1],item[0]))[:20]],
        'repeated_words':repeated[:20],'forgotten_words':[{'word':w,'count':c} for w,c in forgotten_words.most_common(20)],
        'articles':ordered[:50],'article_count':len(ordered),
        'current_review':dict(db.execute('SELECT COALESCE(SUM(suspended),0) AS suspended,COALESCE(SUM(leech),0) AS difficult,COALESCE(SUM(lapses),0) AS lifetime_lapses FROM srs_cards').fetchone()),
        'notes':['时间与完成篇数沿用每日学习记录；阅读时长按保存成功的北京时间归档，单次最多 60 秒；仅点击打卡不增加阅读量。',
                 '阅读速度按首读进度推进词数 / 对应活跃计时估算，仅采用至少 10 秒且有进度推进的保存段；手动标记完成不参与。滚动、跳段与重读会影响估计，不代表正式能力测评。',
                 '完成文章词数仅含能追溯到正文的记录，删除前未采集的历史正文无法补回。',
                 '查词和重复遇见从升级后采集；失败的查词统计在本标签页暂存，恢复连接后重试，最多 500 条、7 天，原文已变动、删除或记录过期后不补计；一次段落、同词、同一天只计一次遇见，不代表自动检测每次阅读。',
                 '升级前词汇增长仅能追溯仍在词库的词条，已删除旧词无法补回。词汇增长是已保存词条的增减（含短语和句型、导入与合并），不是实际词汇量；掌握度和文章生词率展示当前快照。',
                 '文章已记生词率 = 当前词库未掌握词在文章中的词次 / 正文英文词次；未收藏、仅查过或词库缺失不直接视为生词。',
                 '复习记住率来自 SM-2 自评 grade ≥ 3；拼写核对正确率仅统计有实际核对结果的拼写作答；遗忘不含首次学习未记住。']}


@analytics.get('/report')
def get_report():
    return jsonify(report(request.args))


@analytics.get('/export')
def export_report():
    data=report(request.args);output=io.StringIO();writer=csv.writer(output)
    writer.writerow(['日期','活跃阅读秒数','完成篇数','可追溯完成词数','新增词条','移除词条','词库累计','主动查词','遇见记录','复习次数','自评记住次数','遗忘次数','推进估算词/分钟'])
    for row in data['daily']:
        writer.writerow([row['date'],row['seconds'],row['completed'],row['completed_words'],row['vocabulary_added'],row['vocabulary_removed'],row['vocabulary_total'],row['lookups'] if row['lookup_observed'] else '',row['encounters'] if row['lookup_observed'] else '',row['reviews'],row['remembered'],row['forgotten'],row['speed_wpm'] if row['speed_wpm'] is not None else ''])
    writer.writerow([])
    for note in data['notes']:writer.writerow(['口径说明',note])
    return Response('\ufeff'+output.getvalue(),content_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="margin-{data["period"]}-{data["start"]}.csv"'})
