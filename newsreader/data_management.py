"""Local offline controls and verified, transactional data portability."""
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from threading import RLock
import time
import uuid
import zipfile
import zlib

from flask import Blueprint, Response, abort, current_app, g, jsonify, request, send_file
from .db import CHINA_TZ, get_db, now
from .learning import assess_difficulty, words
from .study_plans import PLAN_TABLES, parse_day, validate_plan

management=Blueprint('management',__name__,url_prefix='/api/data')
FORMAT='margin-backup-v1'
ARTICLE_FORMAT='margin-articles-v1'
MAX_ARCHIVE=128*1024*1024
MAX_DATABASE=256*1024*1024
MAX_ARTICLES=20*1024*1024
INTEGRATION_TABLES=('sources','feeds','feed_seen','categories','tags','article_tags','article_embeddings','qa_threads','qa_turns','writing_exercises','writing_attempts','tts_playlist','knowledge_items','knowledge_maps')
LAB_TABLES=('lab_records','lab_packs')
EXTENSION_TABLES=('extension_records','search_changes')
TABLES=('articles','vocabulary','checkins','settings','dictionary_cache','pending_words','study_results',
        'word_meta','word_collections','word_memberships','word_contexts','word_senses','word_notes','word_aliases','word_cards',
        'srs_cards','srs_events','listening_audio','listening_attempts','reading_sets','reading_sessions','reading_mistakes',
        'learner_profile','article_feedback','learning_reading_events','learning_word_events','learning_lookup_events','data_state',*PLAN_TABLES,*INTEGRATION_TABLES,*LAB_TABLES,*EXTENSION_TABLES)
JSON_COLUMNS={'study_plans':{'steps_json':list},'study_results':{'payload_json':dict,'original_json':dict},'srs_events':{'response_json':dict},
 'listening_attempts':{'feedback_json':dict},'reading_sets':{'questions_json':list},
 'reading_sessions':{'snapshot_json':dict,'answers_json':dict,'result_json':dict},
 'reading_mistakes':{'material_json':dict,'question_json':dict,'last_answer_json':list},'learner_profile':{'preferences':dict},
 'lab_records':{'payload_json':dict},'lab_packs':{'article_ids_json':list,'words_json':list,'questions_json':list,'answers_json':list,'completed_json':list}}


def offline():
    row=get_db().execute("SELECT value FROM settings WHERE key='data.offline'").fetchone()
    return bool(row and row[0]=='true')


def flag(key):
    row=get_db().execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return bool(row and row[0]=='true')


def state(db=None):
    return dict((db or get_db()).execute('SELECT generation,revision FROM data_state WHERE id=1').fetchone())


def body():
    data=request.get_json()
    if not isinstance(data,dict):raise ValueError('请提交 JSON 对象。')
    return data


def manager():return getattr(g, 'learning_manager', None) or current_app.extensions['data_manager']


def sha_file(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def summary(db):
    counts={table:db.execute('SELECT count(*) FROM "'+table+'"').fetchone()[0] for table in TABLES if table!='data_state'}
    preferences={row[0]:row[1] for row in db.execute("SELECT key,value FROM settings WHERE key IN ('data.offline','data.auto_backup')")}
    return {'offline':preferences.get('data.offline')=='true','auto_backup':preferences.get('data.auto_backup')=='true','articles':counts['articles'],'words':counts['vocabulary'],'reviews':counts['srs_events'],
            'translations':counts['study_results'],'recordings':counts['listening_attempts'],
            'exercises':counts['reading_sessions'],'tables':counts}


def schema(db):
    return [dict(row) for row in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL ORDER BY type,name")]


def rows_into(source,destination, missing=()):
    for table in TABLES:
        if table in missing:continue
        columns=[r[1] for r in destination.execute('PRAGMA table_info("'+table+'")')]
        sql='INSERT INTO "'+table+'"('+','.join('"'+c+'"' for c in columns)+') VALUES('+','.join('?' for _ in columns)+')'
        count=0;cursor=source.execute('SELECT '+','.join('"'+c+'"' for c in columns)+' FROM "'+table+'"')
        while True:
            batch=cursor.fetchmany(100)
            if not batch:break
            count+=len(batch)
            if count>1000000:raise ValueError('备份记录过多，无法在本机还原。')
            for row in batch:
                definitions = {'payload_json': dict} if table == 'extension_records' else JSON_COLUMNS.get(table,{})
                for column,kind in definitions.items():
                    raw=row[columns.index(column)]
                    if len(raw)>2*1024*1024:raise ValueError('备份中的学习记录过大。')
                    try:value=json.loads(raw)
                    except (ValueError,TypeError):raise ValueError('备份中有损坏的学习 JSON 记录。') from None
                    if not isinstance(value,kind):raise ValueError('备份中的学习记录结构无效。')
                if table == 'study_plans':
                    plan = dict(zip(columns,row))
                    plan.pop('id')
                    plan['steps'] = json.loads(plan.pop('steps_json'))
                    validate_plan(plan)
                elif table in ('study_makeups','study_reminder_days'):
                    parse_day(row[columns.index('day')])
            destination.executemany(sql,[tuple(r) for r in batch])
    # Preserve deleted high IDs as well as live IDs, avoiding ID reuse after migration.
    if source.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
        for name,value in source.execute('SELECT name,seq FROM sqlite_sequence'):
            if name in TABLES:
                if type(value) is not int or value<0:raise ValueError('备份中的编号序列无效。')
                destination.execute('UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name=?',(value,name))
                if not destination.execute('SELECT 1 FROM sqlite_sequence WHERE name=?',(name,)).fetchone():destination.execute('INSERT INTO sqlite_sequence VALUES(?,?)',(name,value))


class Manager:
    def __init__(self,app):
        self.database = app.config['DATABASE']
        self.app=app;self.root=Path(app.config.get('DATA_STORAGE_DIR') or (str(Path(app.config['DATABASE']).with_suffix(''))+'-data'))
        self.root=self.root.resolve()
        self.backups=self.root/'backups';self.staging=self.root/'staging'
        for path in (self.backups,self.staging):path.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lock=RLock();self.active=0;self.maintenance=False;self.previews={}
        self.cleanup()

    def cleanup(self):
        with self.lock:
            for token,preview in list(self.previews.items()):
                if preview['expires']<time.monotonic() and preview['lock'].acquire(blocking=False):
                    try:shutil.rmtree(preview['directory'],ignore_errors=True);del self.previews[token]
                    finally:preview['lock'].release()
            for directory in self.staging.iterdir():
                if not directory.is_symlink() and directory.is_dir() and re.fullmatch(r'(?:[a-f0-9]{32}|snapshot-[a-zA-Z0-9_-]{6,})',directory.name) and directory.stat().st_mtime<time.time()-86400:
                    shutil.rmtree(directory,ignore_errors=True)
            for file in self.backups.glob('*.partial'):
                if re.fullmatch(r'[a-f0-9]{32}\.partial',file.name) and file.stat().st_mtime<time.time()-86400:file.unlink(missing_ok=True)

    def preview(self,kind,path,info,directory):
        self.cleanup()
        with self.lock:
            if sum(not item['receipt'] for item in self.previews.values())>=5:raise ValueError('已有 5 份待确认预览，请稍后或重新启动程序后再试。')
            completed=[key for key,item in self.previews.items() if item['receipt']]
            for key in completed[:-50]:del self.previews[key]
            token=uuid.uuid4().hex
            item={'kind':kind,'path':path,'info':info,'directory':directory,'expires':time.monotonic()+900,'state':state(),'receipt':None,'lock':RLock()}
            self.previews[token]=item
        return {'token':token,'expires_in':900,'current':summary(get_db()),'incoming':info,**item['state']}

    def get_preview(self,data,kind):
        token=data.get('token');request_id=data.get('request_id')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',request_id):raise ValueError('操作编号无效。')
        self.cleanup();item=self.previews.get(token) if isinstance(token,str) else None
        if not item or item['kind']!=kind:raise ValueError('预览已过期，请重新选择文件并预览。')
        fingerprint=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
        if item['receipt']:
            if item['receipt']['fingerprint']!=fingerprint:raise ValueError('预览已经用于另一项操作。')
            return item,{**item['receipt']['response'],'duplicate':True},fingerprint
        return item,None,fingerprint

    def backup(self,kind='manual'):
        identity=uuid.uuid4().hex;archive=self.backups/(identity+'.zip');temp=self.backups/(identity+'.partial')
        try:
            with tempfile.TemporaryDirectory(prefix='snapshot-',dir=self.staging) as scratch:
                path=Path(scratch)/'database.sqlite3'
                # A separate connection sees committed WAL pages without blocking active readers.
                with closing(sqlite3.connect(self.database)) as source,closing(sqlite3.connect(path)) as target:
                    source.backup(target);target.commit()
                    target.row_factory=sqlite3.Row;info=summary(target)
                if path.stat().st_size>MAX_DATABASE:raise ValueError('数据库超过 256 MB，请停止程序后手动备份数据库文件。')
                manifest={'format':FORMAT,'created_at':now(),'database_size':path.stat().st_size,'database_sha256':sha_file(path),'summary':info}
                with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED) as output:
                    output.write(path,'database.sqlite3');output.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False))
                if temp.stat().st_size>MAX_ARCHIVE:raise ValueError('备份文件超过 128 MB，请停止程序后手动备份数据库文件。')
            os.replace(temp,archive)
            meta={'id':identity,'kind':kind,'created_at':manifest['created_at'],'size':archive.stat().st_size,'sha256':sha_file(archive),'summary':info}
            (self.backups/(identity+'.json')).write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
            return meta
        except Exception:
            temp.unlink(missing_ok=True);archive.unlink(missing_ok=True);raise

    def list_backups(self):
        result=[]
        for path in self.backups.glob('*.json'):
            if not re.fullmatch(r'[a-f0-9]{32}',path.stem):continue
            try:
                item=json.loads(path.read_text(encoding='utf-8'))
                valid=(isinstance(item,dict) and item.get('id')==path.stem and item.get('kind') in ('manual','auto','rescue') and
                       isinstance(item.get('created_at'),str) and isinstance(item.get('summary'),dict) and
                       type(item.get('size')) is int and item['size']>=0 and isinstance(item.get('sha256'),str) and
                       re.fullmatch(r'[a-f0-9]{64}',item['sha256']) and all(type(item['summary'].get(k)) is int and item['summary'][k]>=0 for k in ('articles','words','reviews','translations','recordings','exercises')))
                if valid:
                    datetime.fromisoformat(item['created_at'])
                    if (self.backups/(path.stem+'.zip')).is_file():result.append(item)
            except (OSError,ValueError,KeyError,TypeError):continue
        return sorted(result,key=lambda item:(item['created_at'],item['id']),reverse=True)

    def backup_path(self,identity):
        if not re.fullmatch(r'[a-f0-9]{32}',identity):abort(404)
        path=self.backups/(identity+'.zip')
        if not path.is_file():abort(404)
        return path


def initialize_schema():
    db=get_db();db.execute('CREATE TABLE IF NOT EXISTS data_state(id INTEGER PRIMARY KEY CHECK(id=1),generation TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,restored INTEGER NOT NULL DEFAULT 0)')
    db.execute('INSERT OR IGNORE INTO data_state(id,generation,revision) VALUES(1,?,0)',(uuid.uuid4().hex,))
    for table in TABLES:
        if table in ('data_state','search_changes'):continue
        for event in ('INSERT','UPDATE','DELETE'):
            db.execute(f'CREATE TRIGGER IF NOT EXISTS data_revision_{table}_{event.lower()} AFTER {event} ON "{table}" BEGIN UPDATE data_state SET revision=revision+1 WHERE id=1; END')
    db.commit()


def initialize(app):
    initialize_schema()
    app.extensions['data_manager']=Manager(app)

    @app.before_request
    def data_boundary():
        if request.blueprint == 'workspace' or not request.path.startswith('/api/'):return
        control=manager();exclusive=request.endpoint=='management.restore_apply'
        with control.lock:
            if control.maintenance or (exclusive and control.active):return jsonify(error='当前有其他操作正在进行，请结束阅读、录音或 AI 生成后重试还原。'),409
            control.active+=1;control.maintenance=exclusive;g.data_admitted=True
        supplied=request.headers.get('X-Margin-Generation')
        restored=get_db().execute('SELECT restored FROM data_state WHERE id=1').fetchone()[0]
        if restored and request.method in ('POST','PATCH','DELETE','PUT') and not supplied and request.endpoint!='management.restore_apply':
            return jsonify(error='数据已还原，请重新载入页面后再修改。',code='DATA_CHANGED'),409
        if supplied and supplied!=state()['generation'] and request.endpoint not in ('management.restore_apply','management.get_state'):
            return jsonify(error='学习数据已在其他页面还原，请重新载入。',code='DATA_CHANGED'),409

    @app.after_request
    def data_headers(response):
        if request.blueprint != 'workspace' and request.path.startswith('/api/'):
            response.headers['X-Margin-Generation']=state()['generation']
        return response

    @app.teardown_request
    def release(_error):
        if g.pop('data_admitted',False):
            control=manager()
            with control.lock:control.active-=1;control.maintenance=False

    if not app.config.get('TESTING') and flag('data.auto_backup'):
        try:
            control=app.extensions['data_manager'];old=[item for item in control.list_backups() if item['kind']=='auto']
            if not any(item['created_at'][:10]==now()[:10] for item in old):control.backup('auto')
            for item in [i for i in control.list_backups() if i['kind']=='auto'][7:]:
                control.backup_path(item['id']).unlink(missing_ok=True);(control.backups/(item['id']+'.json')).unlink(missing_ok=True)
        except (OSError,sqlite3.Error,ValueError):app.logger.warning('Automatic local backup failed; learning data was retained.')


@management.before_request
def upload_limit():
    if request.endpoint=='management.restore_preview':request.max_content_length=MAX_ARCHIVE+1024*1024
    elif request.endpoint=='management.articles_preview':request.max_content_length=MAX_ARTICLES+1024*1024


@management.get('/state')
def get_state():
    db=get_db();cache=db.execute('SELECT count(*),coalesce(sum(length(CAST(result AS BLOB))),0) FROM dictionary_cache').fetchone();path=Path(current_app.config['DATABASE'])
    size=sum(p.stat().st_size for p in (path,Path(str(path)+'-wal'),Path(str(path)+'-shm')) if p.exists())
    media=sum(db.execute('SELECT coalesce(sum(length('+column+')),0) FROM '+table).fetchone()[0] for table,column in (('word_cards','data'),('listening_audio','data'),('listening_attempts','audio')))
    return jsonify(**state(),offline=offline(),auto_backup=flag('data.auto_backup'),summary=summary(db),storage={'database_bytes':size,'media_bytes':media,'cache_entries':cache[0],'cache_bytes':cache[1]},backups=manager().list_backups(),limits={'archive_bytes':MAX_ARCHIVE,'database_bytes':MAX_DATABASE,'articles_bytes':MAX_ARTICLES})


@management.patch('/preferences')
def preferences():
    data=body()
    if set(data)-{'offline','auto_backup'} or not data:raise ValueError('请选择离线或自动备份设置。')
    if any(type(value) is not bool for value in data.values()):raise ValueError('设置应为布尔值。')
    db=get_db()
    for key,value in data.items():db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('data.'+key,json.dumps(value)))
    db.commit();return get_state()


@management.post('/backups')
def create_backup():
    if body():raise ValueError('备份请求不应包含其他参数。')
    return jsonify(backup=manager().backup()),201


@management.get('/backups/<identity>/download')
def download_backup(identity):
    return send_file(manager().backup_path(identity),mimetype='application/zip',as_attachment=True,download_name='margin-backup-'+identity[:8]+'.zip')


@management.delete('/backups/<identity>')
def delete_backup(identity):
    if body().get('confirm') is not True:raise ValueError('请确认删除选中的备份文件。')
    manager().backup_path(identity).unlink();(manager().backups/(identity+'.json')).unlink(missing_ok=True)
    return jsonify(ok=True)


@management.post('/cache/clear')
def clear_cache():
    if body().get('confirm') is not True:raise ValueError('请确认清理可重新查询的词典缓存。')
    db=get_db();count=db.execute('SELECT count(*) FROM dictionary_cache').fetchone()[0];db.execute('DELETE FROM dictionary_cache');db.commit()
    return jsonify(removed=count)


def uploaded_file(maximum,directory):
    file=request.files.get('file')
    if file is None or len(request.files)!=1:raise ValueError('请选择一份文件。')
    path=directory/'upload';size=0
    with path.open('wb') as output:
        while True:
            block=file.stream.read(1024*1024)
            if not block:break
            size+=len(block)
            if size>maximum:raise ValueError('所选文件超过大小限制。')
            output.write(block)
    return path


@management.post('/restore/preview')
def restore_preview():
    control=manager();directory=control.staging/uuid.uuid4().hex;directory.mkdir(mode=0o700)
    try:
        uploaded=uploaded_file(MAX_ARCHIVE,directory);raw=directory/'untrusted.sqlite3';clean=directory/'verified.sqlite3'
        try:
            with zipfile.ZipFile(uploaded) as archive:
                names=archive.namelist()
                if sorted(names)!=['database.sqlite3','manifest.json']:raise ValueError('备份必须且只能包含数据库与说明文件。')
                if any(item.flag_bits&1 or item.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED) for item in archive.infolist()):raise ValueError('不支持加密或特殊压缩的备份。')
                if archive.getinfo('manifest.json').file_size>65536 or archive.getinfo('database.sqlite3').file_size>MAX_DATABASE:raise ValueError('备份解压后过大。')
                manifest=json.loads(archive.read('manifest.json'))
                if not isinstance(manifest,dict) or manifest.get('format')!=FORMAT:raise ValueError('不是受支持的 Margin 完整备份。')
                with archive.open('database.sqlite3') as source,raw.open('wb') as target:
                    size=0
                    while True:
                        block=source.read(1024*1024)
                        if not block:break
                        size+=len(block)
                        if size>MAX_DATABASE:raise ValueError('数据库超过 256 MB 限制。')
                        target.write(block)
            if manifest.get('database_size')!=raw.stat().st_size or manifest.get('database_sha256')!=sha_file(raw):raise ValueError('备份校验和不匹配，文件可能损坏。')
        except (zipfile.BadZipFile,KeyError,UnicodeError,json.JSONDecodeError,RuntimeError,NotImplementedError,zlib.error,EOFError):raise ValueError('备份文件不完整或格式无效。') from None
        trusted=schema(get_db())
        with closing(sqlite3.connect(raw.as_uri()+'?mode=ro&immutable=1',uri=True)) as source,closing(sqlite3.connect(clean)) as target:
            source.row_factory=sqlite3.Row;target.row_factory=sqlite3.Row;source.execute('PRAGMA trusted_schema=OFF');source.execute('PRAGMA query_only=ON')
            objects=source.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            present={r['name'] for r in objects if r['type']=='table'}
            missing=set(TABLES)-present
            if (missing-set(PLAN_TABLES)-set(INTEGRATION_TABLES)-set(LAB_TABLES)-set(EXTENSION_TABLES)) or present-set(TABLES) or any(r['type']=='view' or re.search(r'\bVIRTUAL\b',r['sql'] or '',re.I) for r in objects):raise ValueError('备份包含不支持的数据结构。')
            for table in TABLES:
                if table in missing:continue
                if [tuple(r)[1:] for r in source.execute('PRAGMA table_info("'+table+'")')]!=[tuple(r)[1:] for r in get_db().execute('PRAGMA table_info("'+table+'")')]:raise ValueError('备份结构与当前版本不兼容。')
            if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('数据库完整性检查未通过。')
            for item in trusted:
                if item['type']=='table':target.execute(item['sql'])
            rows_into(source,target,missing)
            for item in trusted:
                if item['type']=='index':target.execute(item['sql'])
            if target.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('备份存在无效的数据关联。')
            restored_state=state(target)
            if not re.fullmatch(r'[a-f0-9]{32}',restored_state['generation']) or type(restored_state['revision']) is not int or restored_state['revision']<0:raise ValueError('备份中的数据版本无效。')
            target.commit();info=summary(target)
        if clean.stat().st_size>MAX_DATABASE:raise ValueError('校验后的数据库超过 256 MB 限制。')
        raw.unlink();uploaded.unlink()
        return jsonify(preview=control.preview('restore',clean,info,directory)),201
    except (sqlite3.Error,TypeError,OverflowError) as error:
        shutil.rmtree(directory,ignore_errors=True);raise ValueError('备份数据库或学习记录无效，未修改当前数据。') from error
    except Exception:
        shutil.rmtree(directory,ignore_errors=True);raise


@management.post('/restore/apply')
def restore_apply():
    data=body()
    if data.get('replace') is not True:raise ValueError('请确认以备份替换当前学习数据。')
    control=manager();item,done,fingerprint=control.get_preview(data,'restore')
    if done:return jsonify(**done)
    db=get_db();current=state()
    if current!=item['state'] or data.get('generation')!=current['generation'] or data.get('revision')!=current['revision']:
        return jsonify(error='预览后学习数据发生变化，请重新预览再还原。'),409
    ddl=[r for r in schema(db) if r['type']=='trigger'];rescue=None;new_generation=uuid.uuid4().hex
    try:
        db.execute('PRAGMA foreign_keys=OFF');db.execute('BEGIN IMMEDIATE')
        if state()!=current:raise ValueError('数据已变化，请重新预览。')
        rescue=control.backup('rescue')
        for trigger in ddl:db.execute('DROP TRIGGER "'+trigger['name']+'"')
        for table in TABLES:db.execute('DELETE FROM "'+table+'"')
        db.execute('DELETE FROM sqlite_sequence')
        with closing(sqlite3.connect(item['path'])) as source:rows_into(source,db)
        db.execute('UPDATE data_state SET generation=?,revision=0,restored=1 WHERE id=1',(new_generation,))
        for trigger in ddl:db.execute(trigger['sql'])
        if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('备份数据关联检查失败。')
        db.commit()
    except Exception:
        db.rollback();raise
    finally:db.execute('PRAGMA foreign_keys=ON')
    result={'ok':True,'generation':new_generation,'rescue_backup':rescue,'restored':item['info']}
    item['receipt']={'fingerprint':fingerprint,'response':result}
    shutil.rmtree(item['directory'],ignore_errors=True)
    return jsonify(**result,duplicate=False)


@management.get('/articles/export')
def articles_export():
    articles=[dict(row) for row in get_db().execute('SELECT title,subtitle,content,source,source_url,category,author FROM articles ORDER BY id')]
    if not articles:raise ValueError('当前没有可导出的文章。')
    if len(articles)>500:raise ValueError('离线文章包最多 500 篇。')
    packed=json.dumps({'format':ARTICLE_FORMAT,'articles':articles},ensure_ascii=False)
    if len(packed.encode('utf-8'))>MAX_ARTICLES:raise ValueError('文章包超过 20 MB。')
    return Response(packed,mimetype='application/json',headers={'Content-Disposition':'attachment; filename="margin-articles.json"'})


def validate_articles(data):
    from urllib.parse import urlsplit
    if not isinstance(data,dict) or set(data)!={'format','articles'} or data['format']!=ARTICLE_FORMAT or not isinstance(data['articles'],list) or not 1<=len(data['articles'])<=500:raise ValueError('请选择包含 1–500 篇文章的 Margin 文章包。')
    result=[];seen=set()
    for row in data['articles']:
        if not isinstance(row,dict) or set(row)-{'title','subtitle','content','source','source_url','category','author'}:raise ValueError('文章包含不支持的字段。')
        clean={}
        for key,limit in (('title',250),('subtitle',1000),('content',100000),('source',100),('source_url',2048),('category',20),('author',100)):
            value=row.get(key,'其他' if key=='category' else '个人导入' if key=='source' else '')
            if not isinstance(value,str) or len(value)>limit:raise ValueError('文章内容过长或字段格式无效。')
            clean[key]=value.strip()
        if not clean['title'] or len(words(clean['content']))<40:raise ValueError('文章需填写标题且正文至少有 40 个英文单词。')
        if clean['category'] not in ('科技','文化','科学','生活','环境','其他'):raise ValueError('文章分类无效。')
        if clean['source_url']:
            try:
                parsed=urlsplit(clean['source_url'])
                if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:raise ValueError()
                parsed.port
            except ValueError:raise ValueError('原文链接必须是有效 HTTP / HTTPS 链接。') from None
        key=hashlib.sha256(clean['content'].encode()).hexdigest()
        if key not in seen:result.append(clean);seen.add(key)
    return result


@management.post('/articles/preview')
def articles_preview():
    control=manager();directory=control.staging/uuid.uuid4().hex;directory.mkdir(mode=0o700)
    try:
        file=uploaded_file(MAX_ARTICLES,directory)
        try:rows=validate_articles(json.loads(file.read_text(encoding='utf-8-sig')))
        except (ValueError,UnicodeError) as error:raise ValueError('文章包无效：'+str(error)) from None
        file.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
        known={hashlib.sha256(row[0].encode()).hexdigest() for row in get_db().execute('SELECT content FROM articles')}
        info={'articles':len(rows),'new_articles':sum(hashlib.sha256(row['content'].encode()).hexdigest() not in known for row in rows),'titles':[row['title'] for row in rows]}
        return jsonify(preview=control.preview('articles',file,info,directory)),201
    except Exception:shutil.rmtree(directory,ignore_errors=True);raise


@management.post('/articles/apply')
def articles_apply():
    data=body();control=manager();item,done,fingerprint=control.get_preview(data,'articles')
    with item['lock']:
        item,done,fingerprint=control.get_preview(data,'articles')
        if done:return jsonify(**done)
        if item['state']['generation']!=state()['generation']:return jsonify(error='学习数据已还原，请重新预览文章包。'),409
        db=get_db();db.execute('BEGIN IMMEDIATE')
        known={hashlib.sha256(row[0].encode()).hexdigest() for row in db.execute('SELECT content FROM articles')};ids=[]
        for row in json.loads(item['path'].read_text(encoding='utf-8')):
            key=hashlib.sha256(row['content'].encode()).hexdigest()
            if key in known:continue
            m=assess_difficulty(row['content']);known.add(key)
            ids.append(db.execute('INSERT INTO articles(title,subtitle,content,source,source_url,category,author,level,difficulty_score,word_count,reading_minutes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                tuple(row[k] for k in ('title','subtitle','content','source','source_url','category','author'))+(m['level'],m['score'],m['word_count'],m['reading_minutes'],now())).lastrowid)
        db.commit();result={'ok':True,'added':len(ids),'skipped':item['info']['articles']-len(ids),'ids':ids}
        item['receipt']={'fingerprint':fingerprint,'response':result}
        shutil.rmtree(item['directory'],ignore_errors=True)
        return jsonify(**result,duplicate=False)


@management.delete('/previews/<token>')
def cancel_preview(token):
    body();control=manager()
    with control.lock:item=control.previews.get(token)
    if item:
        with item['lock']:
            with control.lock:control.previews.pop(token,None)
            shutil.rmtree(item['directory'],ignore_errors=True)
    return jsonify(ok=True)
