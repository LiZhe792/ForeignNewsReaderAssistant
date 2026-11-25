"""Small, source-grounded reading exercises using the existing learning data."""
import hashlib
import json
import re
import os
import tempfile
from difflib import SequenceMatcher
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, jsonify, request
from .db import get_db, now
from .training import units
from .assistance import paragraphs

lab = Blueprint('learning_lab', __name__, url_prefix='/api/learning-lab')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS lab_records (
 id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
 article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL,
 article_hash TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lab_records_article ON lab_records(article_id,kind,id);
CREATE TABLE IF NOT EXISTS lab_packs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
 article_ids_json TEXT NOT NULL, words_json TEXT NOT NULL, questions_json TEXT NOT NULL,
 answers_json TEXT NOT NULL, completed_json TEXT NOT NULL, target_minutes INTEGER NOT NULL,
 summary TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
'''


def body():
    value=request.get_json()
    if not isinstance(value,dict): raise ValueError('请求内容必须为 JSON 对象。')
    return value


def text(value, limit, label, required=True):
    if not isinstance(value,str) or len(value)>limit or (required and not value.strip()):
        raise ValueError(f'{label}不能为空或超过 {limit} 字符。')
    return value.strip()


def article(db, identity):
    if type(identity) is not int or identity<1: raise ValueError('文章编号无效。')
    row=db.execute("SELECT id,title,content,progress,last_read_at,status FROM articles WHERE id=?",(identity,)).fetchone()
    if row is None or row['status']!='active': abort(404)
    return dict(row)


def digest(content): return hashlib.sha256(content.encode('utf-8')).hexdigest()


def sentences(content):
    return [{'paragraph_index':i,'text':s['text']} for i,p in enumerate(paragraphs(content)) for s in units(p)]


def save(db, kind, source, payload):
    cur=db.execute('INSERT INTO lab_records(kind,article_id,article_hash,title,payload_json,created_at) VALUES(?,?,?,?,?,?)',
                   (kind,source['id'],digest(source['content']),source['title'],json.dumps(payload,ensure_ascii=False),now()))
    db.commit();return cur.lastrowid


@lab.get('/articles/<int:article_id>')
def manifest(article_id):
    db=get_db();source=article(db,article_id)
    return jsonify(article={'id':source['id'],'title':source['title'],'hash':digest(source['content'])},
                   sentences=sentences(source['content']), transfer=transfer_candidates(db,source))


@lab.get('/articles')
def article_list():
    return jsonify(articles=[dict(row) for row in get_db().execute("SELECT id,title,level,progress,last_read_at FROM articles WHERE status='active' ORDER BY id")])


def source_with_hash(db, data):
    source=article(db,data.get('article_id'))
    if data.get('article_hash')!=digest(source['content']):
        abort(409,'原文已变化，请重新打开练习。')
    return source


@lab.post('/recall')
def recall():
    data=body();db=get_db();source=source_with_hash(db,data)
    answer=text(data.get('answer'),5000,'复述')
    points=data.get('points')
    if not isinstance(points,list) or len(points)!=3: raise ValueError('请选择三个不同的原文要点。')
    candidates={(item['paragraph_index'],item['text']) for item in sentences(source['content'])}
    seen=set(); cleaned=[]
    for point in points:
        if not isinstance(point,dict) or set(point)!={'paragraph_index','quote','remembered'} or type(point['paragraph_index']) is not int or type(point['remembered']) is not bool:
            raise ValueError('要点或自评格式无效。')
        if not isinstance(point['quote'],str): raise ValueError('要点必须是三个不同的原文句子。')
        pair=(point['paragraph_index'],point['quote'])
        if pair not in candidates or pair in seen: raise ValueError('要点必须是三个不同的原文句子。')
        seen.add(pair);cleaned.append(point)
    payload={'answer':answer,'points':cleaned,'evaluation':'用户自评，未自动判定理解正确性'}
    return jsonify(id=save(db,'recall',source,payload),result=payload),201


def matches(term, sentence):
    pattern=r"(?<![A-Za-z'’-])"+re.escape(term).replace(r'\ ',r'\s+').replace("'","['’]")+r"(?![A-Za-z'’-])"
    return bool(re.search(pattern,sentence,re.I))


def transfer_candidates(db, source):
    current=sentences(source['content']);out=[]
    words=db.execute("SELECT id,word,meaning,mastery FROM vocabulary ORDER BY id DESC LIMIT 1000").fetchall()
    for w in words:
        old=db.execute('''SELECT c.text,c.article_id,c.article_title,a.content FROM word_contexts c
            JOIN articles a ON a.id=c.article_id
            WHERE c.word_id=? AND c.article_id!=? AND a.status='active' ORDER BY c.id DESC LIMIT 30''',(w['id'],source['id'])).fetchall()
        if not old:continue
        aliases=[r[0] for r in db.execute('SELECT alias FROM word_aliases WHERE word_id=?',(w['id'],))]
        terms=[w['word'],*aliases]
        for sentence in current:
            if any(matches(term,sentence['text']) for term in terms):
                prior=None;previous=None
                for context in old:
                    if context['text'] not in context['content']:continue
                    previous=next((s for s in sentences(context['content']) if s['text']!=sentence['text'] and s['text'] in context['text'] and any(matches(term,s['text']) for term in terms)),None)
                    if previous:prior=context;break
                if prior and previous:
                    out.append({'word_id':w['id'],'word':w['word'],'paragraph_index':sentence['paragraph_index'],
                                'new_sentence':sentence['text'],'previous_sentence':previous['text'],
                                'previous_paragraph_index':previous['paragraph_index'],
                                'previous_article_id':prior['article_id'],'previous_title':prior['article_title']})
                break
        if len(out)>=50:break
    return out


@lab.get('/articles/<int:article_id>/transfer/<int:word_id>')
def transfer_meaning(article_id, word_id):
    db=get_db(); source=article(db,article_id)
    if word_id not in {c['word_id'] for c in transfer_candidates(db,source)}: abort(404)
    return jsonify(meaning=db.execute('SELECT meaning FROM vocabulary WHERE id=?',(word_id,)).fetchone()['meaning'])


@lab.post('/transfer')
def transfer():
    data=body();db=get_db();source=source_with_hash(db,data)
    identity=data.get('word_id');candidates={c['word_id']:c for c in transfer_candidates(db,source)}
    if type(identity) is not int or identity not in candidates:raise ValueError('该词没有可用的新旧文章语境。')
    answer=text(data.get('answer'),1000,'新语境词义')
    judgment=data.get('self_assessment')
    if judgment not in ('understood','uncertain','incorrect'):raise ValueError('请选择自评结果。')
    word=db.execute('SELECT meaning FROM vocabulary WHERE id=?',(identity,)).fetchone()
    result={**candidates[identity],'answer':answer,'self_assessment':judgment,'saved_meaning':word['meaning'],
            'note':'结果由你自行判断；原有掌握度与 SRS 安排不会自动改变。'}
    return jsonify(id=save(db,'transfer',source,result),result=result),201


@lab.post('/scaffold')
def scaffold():
    data=body();db=get_db();source=source_with_hash(db,data)
    index=data.get('paragraph_index');sentence=data.get('sentence')
    if (type(index) is not int or not isinstance(sentence,str) or
        (index,sentence) not in {(s['paragraph_index'],s['text']) for s in sentences(source['content'])}):
        raise ValueError('请选择原文中的句子。')
    before=text(data.get('before'),2000,'提示前理解');after=text(data.get('after'),2000,'独立理解')
    hints=data.get('hints')
    if not isinstance(hints,dict) or set(hints)!={'structure','connectors'}:raise ValueError('请提供结构和连接词提示。')
    structure=text(hints['structure'],4000,'结构提示');connectors=text(hints['connectors'],2000,'连接词提示',False)
    result={'paragraph_index':index,'sentence':sentence,'before':before,'after':after,'hints':{'structure':structure,'connectors':connectors},
            'note':'前后理解由学习者自行比较，系统不自动评分。'}
    return jsonify(id=save(db,'scaffold',source,result),result=result),201


@lab.get('/diagnosis')
def diagnosis():
    db=get_db();events=db.execute('SELECT grade FROM srs_events ORDER BY id DESC LIMIT 100').fetchall()
    mistakes=db.execute('SELECT reason,question_json FROM reading_mistakes WHERE resolved=0 ORDER BY id DESC LIMIT 200').fetchall()
    counts={'grammar':0,'theme':0,'inference':0,'vocabulary':0}
    for row in mistakes:
        try:q=json.loads(row['question_json'])
        except (ValueError,TypeError):q={}
        kind=q.get('type') if isinstance(q,dict) else None
        if row['reason']=='grammar':counts['grammar']+=1
        elif row['reason']=='vocabulary':counts['vocabulary']+=1
        elif kind=='theme':counts['theme']+=1
        elif kind=='inference' or row['reason']=='inference':counts['inference']+=1
    srs_total=len(events);srs_failed=sum(r['grade']<3 for r in events)
    evidence=[{'area':'词汇','samples':srs_total,'failed':srs_failed,'mistakes':counts['vocabulary'],
               'sufficient':srs_total>=5 or counts['vocabulary']>=3},
              *[{'area':label,'samples':None,'failed':None,'mistakes':counts[key],'sufficient':counts[key]>=3}
                for key,label in [('grammar','句法'),('theme','主旨'),('inference','推断')]]]
    choices=[item for item in evidence if item['sufficient'] and (item['mistakes']>=3 or (item['area']=='词汇' and srs_failed>=3))]
    actions={'词汇':'复习一次失误词，并在新文章中检验词义。','句法':'选择一道语法错题或一条长句，使用渐退提示练习。',
             '主旨':'完成一篇读后复述，再核对原文要点。','推断':'重做一道推断错题并标出原文依据。'}
    strongest=max(choices,key=lambda item:item['mistakes']+(srs_failed if item['area']=='词汇' else 0)) if choices else None
    return jsonify(evidence=evidence,recommendation=actions[strongest['area']] if strongest else '',
                   note='透明规则：最近最多 100 次词汇复习至少 5 次且失误至少 3 次，或某类未解决错题至少 3 条；错题计数不是准确率，也不是能力诊断。')


@lab.get('/expressions')
def expressions():
    return jsonify(expressions=expressions_data(get_db()))


@lab.post('/expression-transfer')
def expression_transfer():
    data=body();db=get_db();source=source_with_hash(db,data)
    ids=data.get('word_ids');answer=text(data.get('answer'),5000,'短文');topic=text(data.get('topic'),200,'新话题')
    if not isinstance(ids,list) or len(ids)!=3 or any(type(i) is not int for i in ids) or len(set(ids))!=3:raise ValueError('请选择三个不同的来源表达。')
    available={e['word_id']:e for e in expressions_data(db)}
    if any(i not in available for i in ids):raise ValueError('部分表达缺少已读文章的来源原句。')
    judgments=data.get('self_assessment')
    if not isinstance(judgments,dict) or set(judgments)!=set(map(str,ids)) or any(v not in ('appropriate','uncertain','incorrect') for v in judgments.values()):raise ValueError('请逐条自评表达用法。')
    items=[{**available[i], 'appears':matches(available[i]['word'],answer),'self_assessment':judgments[str(i)]} for i in ids]
    result={'topic':topic,'answer':answer,'expressions':items,'reviews':[],
            'next_review_at':(datetime.fromisoformat(now())+timedelta(days=7)).isoformat(timespec='seconds'),
            'note':'出现检测只匹配字面形式；用法正确性由学习者自评。'}
    return jsonify(id=save(db,'expression',source,result),result=result),201


def expressions_data(db):
    rows=db.execute('''SELECT c.word_id,v.word,v.meaning,c.text,c.article_id,c.article_title,a.content
        FROM word_contexts c JOIN vocabulary v ON v.id=c.word_id JOIN articles a ON a.id=c.article_id
        WHERE a.status='active' AND (a.progress>0 OR a.last_read_at IS NOT NULL) ORDER BY c.id DESC LIMIT 500''').fetchall()
    result={}
    for row in rows:
        if row['text'] in row['content'] and matches(row['word'],row['text']):
            item=dict(row);item.pop('content');result.setdefault(row['word_id'],item)
    return list(result.values())


@lab.post('/speech')
def speech():
    data=body(); db=get_db(); identity=data.get('attempt_id')
    if type(identity) is not int or identity<1: raise ValueError('录音编号无效。')
    row=db.execute('SELECT * FROM listening_attempts WHERE id=? AND audio IS NOT NULL',(identity,)).fetchone()
    if not row: abort(404,'没有找到录音。')
    source=article(db,row['article_id'])
    if row['article_hash']!=digest(source['content']): abort(409,'原文已改变，请重新录音。')
    model_path=os.environ.get('MARGIN_WHISPER_MODEL','').strip() or os.path.join(os.getenv('MARGIN_DATA_DIR',os.path.join(current_app.root_path,'instance')),'whisper-tiny.en')
    if not model_path or not os.path.isdir(model_path):
        return jsonify(error='尚未配置本地语音识别模型：请按 README 安装可选依赖并下载模型。'),503
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return jsonify(error='尚未安装语音识别依赖，请运行 pip install -r requirements-speech.txt。'),503
    path=None
    try:
        with tempfile.NamedTemporaryFile(delete=False,suffix='.audio') as temp:
            path=temp.name;temp.write(row['audio'])
        model=WhisperModel(model_path,device='cpu',compute_type='int8',local_files_only=True)
        segments,_=model.transcribe(path,language='en',word_timestamps=True,beam_size=5)
        recognized=[]
        for segment in segments:
            for word in segment.words or []:
                if word.start is None or word.end is None or word.start<0 or word.end<=word.start: continue
                recognized.append({'word':word.word.strip(),'start':round(word.start,3),'end':round(word.end,3),
                                   'confidence':round(float(word.probability),3) if word.probability is not None else None})
        if not recognized: return jsonify(error='识别器没有返回有效的词级时间戳，请检查录音。'),422
    except Exception:
        current_app.logger.exception('Local speech recognition failed')
        return jsonify(error='本地语音识别失败，请检查模型目录、依赖和录音格式。'),503
    finally:
        if path and os.path.exists(path): os.unlink(path)
    normalize=lambda value:re.sub(r"[^a-z0-9']",'',value.lower())
    expected=re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?",row['source_text'])
    recognized_tokens=[normalize(item['word']) for item in recognized]
    comparison=SequenceMatcher(None,[normalize(w) for w in expected],recognized_tokens,autojunk=False)
    aligned=[]
    for tag,a,b,c,d in comparison.get_opcodes():
        if tag=='equal':
            for original,heard in zip(expected[a:b],recognized[c:d]): aligned.append({'expected':original,'recognized':heard,'status':'matched'})
        else:
            for original in expected[a:b]: aligned.append({'expected':original,'recognized':None,'status':'not_matched'})
            for heard in recognized[c:d]: aligned.append({'expected':'','recognized':heard,'status':'extra'})
    result={'attempt_id':identity,'source_text':row['source_text'],'paragraph_index':row['paragraph_index'],
            'words':aligned,'note':'词级时间与置信度来自本机语音识别器；置信度反映识别可靠性，不是发音评分。未匹配也可能是识别错误。'}
    return jsonify(id=save(db,'speech',source,result),result=result),201


def read_pack(db, identity):
    row=db.execute('SELECT * FROM lab_packs WHERE id=?',(identity,)).fetchone()
    if not row:abort(404)
    result=dict(row)
    for name in ('article_ids','words','questions','answers','completed'):result[name]=json.loads(result.pop(name+'_json'))
    return result


@lab.route('/packs',methods=['GET','POST'])
def packs():
    db=get_db()
    if request.method=='GET':return jsonify(packs=[read_pack(db,r[0]) for r in db.execute('SELECT id FROM lab_packs ORDER BY id DESC LIMIT 100')])
    data=body();title=text(data.get('title'),100,'专题名称');ids=data.get('article_ids');words=data.get('words');questions=data.get('questions')
    if not isinstance(ids,list) or len(ids)!=3 or any(type(i) is not int or i<1 for i in ids) or len(set(ids))!=3:raise ValueError('请选择三篇不同的文章。')
    for identity in ids:article(db,identity)
    if not isinstance(words,list) or not 1<=len(words)<=20:raise ValueError('请填写 1–20 个目标词或表达。')
    words=[text(w,80,'目标词') for w in words]
    if len({w.casefold() for w in words})!=len(words):raise ValueError('目标词不能重复。')
    if not isinstance(questions,list) or len(questions)!=3:raise ValueError('请为三篇文章各填写一个问题。')
    questions=[text(q,500,'阶段问题') for q in questions]
    target=data.get('target_minutes')
    if type(target) is not int or not 2<=target<=180:raise ValueError('目标时长为 2–180 分钟。')
    stamp=now();ident=db.execute('''INSERT INTO lab_packs(title,article_ids_json,words_json,questions_json,answers_json,completed_json,target_minutes,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?)''',(title,json.dumps(ids),json.dumps(words,ensure_ascii=False),json.dumps(questions,ensure_ascii=False),json.dumps(['']*3),json.dumps([False]*3),target,stamp,stamp)).lastrowid
    db.commit();return jsonify(pack=read_pack(db,ident)),201


@lab.route('/packs/<int:identity>',methods=['GET','PATCH'])
def update_pack(identity):
    db=get_db()
    if request.method=='GET':return jsonify(pack=read_pack(db,identity))
    data=body();db.execute('BEGIN IMMEDIATE');current=read_pack(db,identity)
    if type(data.get('version')) is not int or data['version']!=current['version']:
        db.rollback();return jsonify(error='任务包已更新，请重新载入。'),409
    if set(data)-{'version','completed','answers','summary'}:raise ValueError('包含未知任务包字段。')
    completed=data.get('completed',current['completed']);summary=text(data.get('summary',current['summary']),5000,'总结',False)
    answers=data.get('answers',current['answers'])
    if not isinstance(completed,list) or len(completed)!=3 or any(type(v) is not bool for v in completed):raise ValueError('阶段状态无效。')
    if not isinstance(answers,list) or len(answers)!=3:raise ValueError('阶段回答无效。')
    answers=[text(answer,2000,'阶段回答',False) for answer in answers]
    # Stage N cannot be marked complete until all previous stages are complete.
    if (completed[1] and not completed[0]) or (completed[2] and not completed[1]):raise ValueError('请按顺序完成阅读阶段。')
    if any(done and not answers[i] for i,done in enumerate(completed)):raise ValueError('完成阶段前请填写对应问题的回答。')
    db.execute('UPDATE lab_packs SET completed_json=?,answers_json=?,summary=?,version=version+1,updated_at=? WHERE id=?',(json.dumps(completed),json.dumps(answers,ensure_ascii=False),summary,now(),identity))
    db.commit();return jsonify(pack=read_pack(db,identity))


@lab.get('/records')
def records():
    kind=request.args.get('kind','')
    if kind not in ('','recall','transfer','scaffold','expression','speech'):raise ValueError('记录类型无效。')
    rows=get_db().execute('SELECT * FROM lab_records WHERE (?=\'\' OR kind=?) ORDER BY id DESC LIMIT 100',(kind,kind)).fetchall()
    result=[]
    for row in rows:
        item=dict(row);item['payload']=json.loads(item.pop('payload_json'));result.append(item)
    return jsonify(records=result)


@lab.post('/records/<int:identity>/review')
def review_expression(identity):
    data=body();db=get_db();db.execute('BEGIN IMMEDIATE')
    row=db.execute("SELECT payload_json FROM lab_records WHERE id=? AND kind='expression'",(identity,)).fetchone()
    if not row:abort(404)
    payload=json.loads(row['payload_json']);items=payload['expressions'];ratings=data.get('self_assessment')
    ids=[str(item['word_id']) for item in items]
    if not isinstance(ratings,dict) or set(ratings)!=set(ids) or any(v not in ('appropriate','uncertain','incorrect') for v in ratings.values()):
        raise ValueError('请逐条选择复习自评。')
    reviews=payload.setdefault('reviews',[])
    if len(reviews)>=100:raise ValueError('这条练习的复习记录已达上限。')
    stamp=now();reviews.append({'at':stamp,'self_assessment':ratings})
    payload['next_review_at']=(datetime.fromisoformat(stamp)+timedelta(days=7)).isoformat(timespec='seconds')
    db.execute('UPDATE lab_records SET payload_json=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),identity));db.commit()
    return jsonify(result=payload)
