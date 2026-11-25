import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from app import create_app
from newsreader.db import get_db, init_db


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'study.db'
        self.app=create_app({'TESTING':True,'DATABASE':str(self.path),'AI_API_KEY':'test-only','AI_MODEL':'fixture'})
        self.client=self.app.test_client()

    def test_recall_grounding_cache_and_followup(self):
        meta=self.client.get('/api/learning-coach/articles/1').json
        answer='I recall a local community and its future.'
        result={'points':[{'source_quote':p['text'],'answer_quote':'','status':'missing','explanation':'待补充','exercise':'解释这个要点。'} for p in meta['points']]}
        payload={'article_id':1,'article_hash':meta['article_hash'],'answer':answer}
        with patch('newsreader.learning_coach.generate',return_value=result) as generate:
            response=self.client.post('/api/learning-coach/recall',json=payload)
            self.assertEqual(response.status_code,201,response.json)
            saved=response.json['result']
            self.assertTrue(self.client.post('/api/learning-coach/recall',json=payload).json['cached'])
            self.assertEqual(generate.call_count,1)
        follow={'version':saved['version'],'answers':{'0':'补充一','1':'补充二','2':'补充三'}}
        r=self.client.post('/api/learning-coach/recall/'+saved['id']+'/followup',json=follow)
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(self.client.post('/api/learning-coach/recall/'+saved['id']+'/followup',json=follow).status_code,409)
        result['points'][0]['source_quote']='invented evidence'
        payload['answer']='A different answer'
        with patch('newsreader.learning_coach.generate',return_value=result):
            self.assertEqual(self.client.post('/api/learning-coach/recall',json=payload).status_code,502)

    def test_deletion_undo_restores_schedule_and_cards_once(self):
        response=self.client.post('/api/wordbook',json={'word':'evidence','meaning':'证据','contexts':[{'text':'source context','article_id':1}]})
        identity=response.json['entry']['id']
        card={'kind':'example','front':'Evidence matters.','back':'证据重要','version':1}
        self.assertEqual(self.client.post(f'/api/wordbook/{identity}/cards',json=card).status_code,201)
        before=self.client.get(f'/api/wordbook/{identity}').json['entry']
        token=self.client.delete('/api/vocabulary/'+str(identity),json={}).json['undo_token']
        self.assertEqual(self.client.get('/api/wordbook/'+str(identity)).status_code,404)
        self.assertEqual(self.client.post('/api/undo/'+token,json={}).status_code,200)
        restored=self.client.get('/api/wordbook/'+str(identity)).json['entry']
        self.assertEqual(restored['schedule'],before['schedule'])
        self.assertEqual(restored['cards'],before['cards'])
        self.assertEqual(self.client.post('/api/undo/'+token,json={}).status_code,404)

    def test_pagination_matches_combined_filters_and_trash(self):
        all_rows=self.client.get('/api/articles/search?limit=2').json
        self.assertEqual(all_rows['total'],5);self.assertEqual(len(all_rows['articles']),2)
        second=self.client.get('/api/articles/search?limit=2&offset=2').json
        self.assertFalse(set(a['id'] for a in second['articles'])&set(a['id'] for a in all_rows['articles']))
        self.client.post('/api/articles/batch',json={'ids':[1],'op':'trash'})
        self.assertEqual([a['id'] for a in self.client.get('/api/articles/search?library_status=trashed&limit=2').json['articles']],[1])
        for word in ('apple','garden','future'):
            self.client.post('/api/wordbook',json={'word':word,'meaning':'word '+word})
        result=self.client.get('/api/wordbook?limit=1&query=garden').json
        self.assertEqual(result['total'],1);self.assertEqual(result['entries'][0]['word'],'garden')
        compact=self.client.get('/api/vocabulary?compact=1').json['words']
        self.assertEqual(len(compact),3)
        self.assertEqual(set(compact[0]),{'id','word','mastery'})

    def test_timezone_multilingual_and_offline_dictionary(self):
        response=self.client.patch('/api/platform/preferences',json={'timezone':'America/New_York','article_language':'fr'})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.client.post('/api/vocabulary',json={'word':'écologie','meaning':'生态学'}).status_code,201)
        self.assertEqual(self.client.post('/api/articles',json={'title':'日本語','content':'自然を守ることは社会の未来にとって重要です。'*8}).status_code,201)
        dictionary=self.client.get('/offline-dictionary.json')
        self.assertEqual(dictionary.status_code,200)
        self.assertTrue(dictionary.json.get('garden'))

    def test_migration_failure_restores_last_database(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE settings SET value='0' WHERE key='schema.version'")
            db.commit()
        def fail():
            db=get_db();db.execute("DELETE FROM articles");db.commit();raise RuntimeError('fixture failure')
        with self.app.app_context(),patch('newsreader.db._initialize_database',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'fixture failure'):init_db()
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM articles').fetchone()[0],5)
        self.assertTrue(list((self.path.parent/'migration-backups').glob('*.sqlite3')))

    def test_sync_same_receipt_cannot_apply_different_content(self):
        bundle=self.client.get('/api/platform/offline-bundle/1').json
        op={'id':'operation-123','kind':'word','article_id':1,'content_hash':bundle['fingerprint'],'word':'garden','meaning':'花园'}
        self.assertEqual(self.client.post('/api/platform/sync',json={'operations':[op]}).json['results'][0]['status'],'saved')
        op['meaning']='different'
        self.assertEqual(self.client.post('/api/platform/sync',json={'operations':[op]}).json['results'][0]['status'],'conflict')

    def test_expression_rejects_invented_quotes_and_deleted_exercise(self):
        from newsreader.learning_lab import digest
        with self.app.app_context():
            db=get_db();article=db.execute('SELECT * FROM articles WHERE id=1').fetchone()
            payload={'expressions':[{'word_id':i,'text':'The garden matters.'} for i in (1,2,3)],'answer':'A garden matters.','topic':'Community'}
            identity=db.execute("INSERT INTO lab_records(kind,article_id,article_hash,title,payload_json,created_at) VALUES('expression',1,?,'test',?,'2026-11-25')",(digest(article['content']),json.dumps(payload))).lastrowid;db.commit()
        items=[{'word_id':i,'source_quote':'The garden','answer_quote':'A garden','judgment':'appropriate','explanation':'Matches context','suggestion':''} for i in (1,2,3)]
        path='/api/learning-coach/expression/'+str(identity)
        invalid=json.loads(json.dumps(items));invalid[0]['source_quote']='invented'
        with patch('newsreader.learning_coach.generate',return_value={'expressions':invalid}):
            self.assertEqual(self.client.post(path,json={}).status_code,502)
        def delete_during_generation(*args):
            with closing(sqlite3.connect(self.path)) as db:
                db.execute('DELETE FROM lab_records WHERE id=?',(identity,));db.commit()
            return {'expressions':items}
        with patch('newsreader.learning_coach.generate',side_effect=delete_during_generation):
            self.assertEqual(self.client.post(path,json={}).status_code,409)
        with self.app.app_context():self.assertEqual(get_db().execute("SELECT count(*) FROM extension_records WHERE kind='coach-expression'").fetchone()[0],0)

    def test_fts_tracks_edits_deletes_and_rebuilds_after_cache_loss(self):
        self.assertEqual(self.client.get('/api/articles/search?q=uniqueneedle').json['articles'],[])
        with self.app.app_context():
            db=get_db();db.execute("UPDATE articles SET content=content||' uniqueneedle' WHERE id=1");db.commit()
        self.assertEqual([a['id'] for a in self.client.get('/api/articles/search?q=uniqueneedle').json['articles']],[1])
        cache=self.path.with_suffix('.search-cache.sqlite3');self.assertTrue(cache.exists());cache.unlink()
        self.assertEqual(len(self.client.get('/api/articles/search?q=uniqueneedle').json['articles']),1)
        with self.app.app_context():
            db=get_db();db.execute('DELETE FROM articles WHERE id=1');db.commit()
        self.assertEqual(self.client.get('/api/articles/search?q=uniqueneedle').json['articles'],[])

    def test_full_instance_backup_includes_accounts_and_verifies_before_restoring(self):
        from backup import create_backup,restore_backup
        source=Path(self.tmp.name)/'source';source.mkdir()
        (source/'writing.json').write_text('{"draft":"my writing"}',encoding='utf-8')
        with closing(sqlite3.connect(source/'learning.db')) as db:
            db.execute('CREATE TABLE study(value TEXT)');db.execute("INSERT INTO study VALUES('retained')");db.commit()
        archive=Path(self.tmp.name)/'complete.zip';create_backup(source,archive)
        target=Path(self.tmp.name)/'restored';self.assertEqual(restore_backup(archive)['files'],2)
        restore_backup(archive,target)
        self.assertEqual((target/'writing.json').read_text(),'{"draft":"my writing"}')
        with closing(sqlite3.connect(target/'learning.db')) as db:self.assertEqual(db.execute('SELECT value FROM study').fetchone()[0],'retained')
        with self.assertRaises(ValueError):restore_backup(archive,target)


if __name__=='__main__':unittest.main()
