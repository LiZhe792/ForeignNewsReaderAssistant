"""Exercise source grounding, persistence, and honest speech alignment."""
import json
import io
import hashlib
import sqlite3
import zipfile
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from newsreader.db import get_db, now


class LearningLabTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=create_app({'TESTING':True,'DATABASE':str(Path(self.temp.name)/'test.db')})
        self.client=self.app.test_client()
        self.manifest=self.client.get('/api/learning-lab/articles/1').json
        self.source={'article_id':1,'article_hash':self.manifest['article']['hash']}

    def post(self,path,data): return self.client.post('/api/learning-lab/'+path,json=data)

    def sql(self,query,args=()):
        with self.app.app_context():
            db=get_db();result=db.execute(query,args);db.commit();return result.lastrowid

    def test_recall_is_grounded_and_stale_source_is_rejected(self):
        selected=self.manifest['sentences'][:3]
        points=[{'paragraph_index':s['paragraph_index'],'quote':s['text'],'remembered':i==0} for i,s in enumerate(selected)]
        body={**self.source,'answer':'I remember a train.','points':points}
        result=self.post('recall',body)
        self.assertEqual(result.status_code,201);self.assertEqual(result.json['result']['points'],points)
        self.assertEqual(len(self.client.get('/api/learning-lab/records').json['records']),1)
        body['points']=[points[0]]*3;self.assertEqual(self.post('recall',body).status_code,400)
        body['points']=points;body['article_hash']='outdated';self.assertEqual(self.post('recall',body).status_code,409)

    def test_pack_enforces_order_answers_and_concurrent_versions(self):
        r=self.post('packs',{'title':'Cities','article_ids':[1,2,3],'words':['city','train'],'questions':['Why?','How?','What?'],'target_minutes':30})
        self.assertEqual(r.status_code,201);p=r.json['pack']
        update=lambda **kw:self.client.patch('/api/learning-lab/packs/'+str(p['id']),json={'version':1,**kw})
        self.assertEqual(update(completed=[False,True,False],answers=['','Yes','']).status_code,400)
        self.assertEqual(update(completed=[True,False,False],answers=['','','']).status_code,400)
        good=update(completed=[True,False,False],answers=['Yes','',''])
        self.assertEqual(good.status_code,200);self.assertEqual(good.json['pack']['version'],2)
        self.assertEqual(update(summary='stale').status_code,409)

    def test_transfer_requires_real_context_and_does_not_change_mastery(self):
        sentence=self.manifest['sentences'][0]['text'];term='station'
        w=self.sql('INSERT INTO vocabulary(word,meaning,created_at,mastery) VALUES(?,?,?,?)',(term,'车站',now(),'learning'))
        self.sql("UPDATE articles SET content=content || ? WHERE id=2",('\nAnother station was busy.',))
        self.sql('INSERT INTO word_contexts(word_id,text,article_id,article_title) VALUES(?,?,?,?)',(w,'Another station was busy.',2,'Other article'))
        manifest=self.client.get('/api/learning-lab/articles/1').json
        self.assertEqual(manifest['transfer'][0]['new_sentence'],sentence)
        self.assertNotIn('meaning',manifest['transfer'][0])
        self.assertEqual(self.client.get(f'/api/learning-lab/articles/1/transfer/{w}').json['meaning'],'车站')
        response=self.post('transfer',{**self.source,'word_id':w,'answer':'车站','self_assessment':'understood'})
        self.assertEqual(response.status_code,201)
        with self.app.app_context():
            db=get_db();self.assertEqual(db.execute('SELECT mastery FROM vocabulary WHERE id=?',(w,)).fetchone()[0],'learning')
            self.assertEqual(db.execute('SELECT count(*) FROM srs_events').fetchone()[0],0)

    def test_scaffold_requires_actual_sentence_and_diagnosis_shows_insufficient(self):
        s=self.manifest['sentences'][0];body={**self.source,'paragraph_index':s['paragraph_index'],'sentence':s['text'],'before':'before','after':'after','hints':{'structure':'subject and verb','connectors':''}}
        self.assertEqual(self.post('scaffold',body).status_code,201)
        body['sentence']='invented';self.assertEqual(self.post('scaffold',body).status_code,400)
        d=self.client.get('/api/learning-lab/diagnosis').json
        self.assertTrue(all(not row['sufficient'] for row in d['evidence']));self.assertFalse(d['recommendation'])

    def test_speech_requires_real_timestamps_and_keeps_unmatched_unscored(self):
        source=self.client.get('/api/articles/1').json
        attempt=self.sql('''INSERT INTO listening_attempts(request_id,fingerprint,article_id,article_title,article_hash,paragraph_index,unit_index,source_text,mode,attempt,duration_ms,voiced_ms,pause_count,feedback_json,mime,audio,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',('test','f',1,source['title'],self.source['article_hash'],0,0,'At a station','readalong','',1000,0,0,'{}','audio/wav',b'example',now()))
        with patch.dict(os.environ,{'MARGIN_WHISPER_MODEL':str(Path(self.temp.name)/'missing-model')}):
            self.assertEqual(self.post('speech',{'attempt_id':attempt}).status_code,503)
        word=lambda text,start,end,prob:types.SimpleNamespace(word=text,start=start,end=end,probability=prob)
        class Model:
            def __init__(self,*a,**k): pass
            def transcribe(self,*a,**k):
                assert k['word_timestamps'] is True
                return [types.SimpleNamespace(words=[word('At',0,.2,.8),word('station',.4,.8,.6)])],None
        with patch.dict(os.environ,{'MARGIN_WHISPER_MODEL':self.temp.name}),patch.dict(sys.modules,{'faster_whisper':types.SimpleNamespace(WhisperModel=Model)}):
            response=self.post('speech',{'attempt_id':attempt})
        self.assertEqual(response.status_code,201);words=response.json['result']['words']
        self.assertEqual(words[0]['recognized']['start'],0);self.assertIsNone(words[1]['recognized']);self.assertEqual(words[-1]['recognized']['confidence'],.6)

    def test_expression_requires_three_source_entries(self):
        sentence=self.manifest['sentences'][0]['text']
        for index,term in enumerate(['station','train','sunset']):
            word=self.sql('INSERT INTO vocabulary(word,meaning,created_at) VALUES(?,?,?)',(term,'释义',now()))
            self.sql('INSERT INTO word_contexts(word_id,text,article_id,article_title) VALUES(?,?,?,?)',(word,sentence,1,'source'))
        self.sql('UPDATE articles SET progress=20 WHERE id=1')
        ids=[e['word_id'] for e in self.client.get('/api/learning-lab/expressions').json['expressions']]
        response=self.post('expression-transfer',{**self.source,'word_ids':ids,'topic':'A trip','answer':'The station had a train.','self_assessment':{str(i):'uncertain' for i in ids}})
        self.assertEqual(response.status_code,201)
        self.assertEqual(sum(e['appears'] for e in response.json['result']['expressions']),2)
        review_id=response.json['id']
        self.assertEqual(self.post(f'records/{review_id}/review',{'self_assessment':{str(ids[0]):'appropriate'}}).status_code,400)
        review=self.post(f'records/{review_id}/review',{'self_assessment':{str(i):'uncertain' for i in ids}})
        self.assertEqual(review.status_code,200);self.assertEqual(len(review.json['result']['reviews']),1)

    def test_records_and_packs_survive_a_full_backup_restore(self):
        selected=self.manifest['sentences'][:3]
        points=[{'paragraph_index':s['paragraph_index'],'quote':s['text'],'remembered':False} for s in selected]
        self.assertEqual(self.post('recall',{**self.source,'answer':'Memory','points':points}).status_code,201)
        self.assertEqual(self.post('packs',{'title':'Backup topic','article_ids':[1,2,3],'words':['city'],'questions':['One','Two','Three'],'target_minutes':30}).status_code,201)
        backup=self.client.post('/api/data/backups',json={}).json['backup']['id']
        download=self.client.get('/api/data/backups/'+backup+'/download');raw=download.data;download.close()
        self.sql('DELETE FROM lab_records');self.sql('DELETE FROM lab_packs')
        preview=self.client.post('/api/data/restore/preview',data={'file':(io.BytesIO(raw),'backup.zip')},content_type='multipart/form-data')
        self.assertEqual(preview.status_code,201,preview.get_data(as_text=True));p=preview.json['preview']
        result=self.client.post('/api/data/restore/apply',json={'token':p['token'],'generation':p['generation'],'revision':p['revision'],'request_id':'restore_lab_test','replace':True})
        self.assertEqual(result.status_code,200,result.get_data(as_text=True))
        self.assertEqual(len(self.client.get('/api/learning-lab/records').json['records']),1)
        self.assertEqual(self.client.get('/api/learning-lab/packs').json['packs'][0]['title'],'Backup topic')

    def test_backups_from_before_learning_lab_can_be_restored(self):
        backup=self.client.post('/api/data/backups',json={}).json['backup']['id']
        download=self.client.get('/api/data/backups/'+backup+'/download');raw=download.data;download.close()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            manifest=json.loads(archive.read('manifest.json'));database=archive.read('database.sqlite3')
        old=Path(self.temp.name)/'old.db';old.write_bytes(database)
        db=sqlite3.connect(old)
        try:
            db.execute('DROP TABLE lab_records');db.execute('DROP TABLE lab_packs');db.commit()
        finally: db.close()
        database=old.read_bytes();manifest['database_sha256']=hashlib.sha256(database).hexdigest();manifest['database_size']=len(database)
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('database.sqlite3',database);archive.writestr('manifest.json',json.dumps(manifest))
        response=self.client.post('/api/data/restore/preview',data={'file':(io.BytesIO(buffer.getvalue()),'old.zip')},content_type='multipart/form-data')
        self.assertEqual(response.status_code,201,response.get_data(as_text=True))


if __name__=='__main__':unittest.main()
