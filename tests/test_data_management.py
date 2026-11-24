"""Backups use disposable databases and archives. No personal database is touched."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from app import create_app
from newsreader import ai
from newsreader.db import get_db
from newsreader.data_management import TABLES, FORMAT, ARTICLE_FORMAT


class DataManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=str(Path(self.temp.name)/'learning.sqlite3')
        self.app=create_app({'TESTING':True,'DATABASE':self.path,'AI_API_KEY':'synthetic-key','AI_MODEL':'test-model'})
        self.client=self.app.test_client();self.serial=0

    def call(self,path,method='GET',data=None,status=200,headers=None):
        if headers is None and method not in ('GET','HEAD'):
            current=self.client.get('/api/data/state').json
            headers={'X-Margin-Generation':current['generation']} if 'generation' in current else {}
        result=self.client.open('/api'+path,method=method,json=data,headers=headers)
        self.assertEqual(result.status_code,status,result.get_data(as_text=True));return result.json

    def sql(self,query,args=()):
        with self.app.app_context():
            db=get_db();rows=[dict(row) for row in db.execute(query,args).fetchall()];db.commit();return rows

    def word(self,word='journey'):
        return self.call('/vocabulary','POST',{'word':word,'meaning':'旅程'},201)['id']

    def backup(self):
        record=self.call('/data/backups','POST',{},201)['backup']
        result=self.client.get('/api/data/backups/'+record['id']+'/download');self.assertEqual(result.status_code,200)
        data=result.data;result.close();return record,data

    def preview(self,data,path='/data/restore/preview',status=201):
        response=self.client.post('/api'+path,data={'file':(io.BytesIO(data),'backup.zip')},content_type='multipart/form-data',headers={'X-Margin-Generation':self.client.get('/api/data/state').json['generation']})
        self.assertEqual(response.status_code,status,response.get_data(as_text=True));return response.json

    def apply(self,preview,**extra):
        return {'token':preview['token'],'generation':preview['generation'],'revision':preview['revision'],'request_id':'restore_receipt_1','replace':True,**extra}

    def snapshot(self):
        return {table:self.sql('SELECT * FROM "'+table+'"') for table in TABLES if table!='data_state'}

    def edit_archive(self,data,modify=None,manifest_change=None,extra=None):
        with zipfile.ZipFile(io.BytesIO(data)) as source:
            raw=source.read('database.sqlite3');manifest=json.loads(source.read('manifest.json'))
        if modify:
            target=Path(self.temp.name)/'tampered.sqlite3';target.write_bytes(raw)
            with closing(sqlite3.connect(target)) as db:
                db.create_function('margin_now',0,lambda:'2026-11-22T10:00:00+08:00');modify(db);db.commit()
            raw=target.read_bytes();manifest['database_sha256']=hashlib.sha256(raw).hexdigest();manifest['database_size']=len(raw)
        if manifest_change:manifest_change(manifest)
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('database.sqlite3',raw);archive.writestr('manifest.json',json.dumps(manifest))
            if extra:archive.writestr(extra,b'untrusted')
        return output.getvalue()

    def test_state_describes_local_data_and_limits(self):
        info=self.call('/data/state');self.assertFalse(info['offline']);self.assertFalse(info['auto_backup'])
        self.assertEqual(info['summary']['articles'],5);self.assertEqual(info['summary']['words'],0)
        self.assertEqual(len(info['generation']),32);self.assertEqual(info['revision'],0)
        self.assertEqual(info['backups'],[]);self.assertGreater(info['storage']['database_bytes'],0)

    def test_revision_tracks_writes_and_read_only_status_does_not_change_it(self):
        before=self.call('/data/state');self.word();after=self.call('/data/state')
        self.assertGreater(after['revision'],before['revision']);self.assertEqual(after['generation'],before['generation'])
        self.call('/data/state');self.call('/analytics/report');self.assertEqual(self.call('/data/state')['revision'],after['revision'])

    def test_offline_preference_round_trip_and_invalid_values(self):
        saved=self.call('/data/preferences','PATCH',{'offline':True,'auto_backup':True});self.assertTrue(saved['offline']);self.assertTrue(saved['auto_backup'])
        restart=create_app({'TESTING':True,'DATABASE':self.path}).test_client();self.assertTrue(restart.get('/api/data/state').json['offline'])
        for body in ({},{'offline':1},{'offline':'true'},{'anything':True},[]):self.call('/data/preferences','PATCH',body,400)

    def test_offline_blocks_url_import_before_dns(self):
        self.call('/data/preferences','PATCH',{'offline':True})
        with patch('newsreader.network.socket.getaddrinfo') as dns:
            self.call('/articles','POST',{'url':'https://example.com/article'},400);dns.assert_not_called()
        self.call('/articles','POST',{'title':'Offline pasted','content':'The city has a small park. '*10},201)

    def test_offline_dictionary_keeps_local_entries_without_network(self):
        self.call('/data/preferences','PATCH',{'offline':True})
        with patch('newsreader.content.fetch_public') as fetch:
            self.assertTrue(self.call('/dictionary?word=journey&provider=offline')['found'])
            missing=self.call('/dictionary?word=quuxblargh&provider=auto');self.assertFalse(missing['found']);self.assertIn('离线',missing['message']);fetch.assert_not_called()

    def test_offline_online_provider_can_reuse_real_cached_result(self):
        fixture=[{'word':'quux','meanings':[{'partOfSpeech':'noun','definitions':[{'definition':'A controlled example.'}]}]}]
        with patch('newsreader.content.fetch_public',return_value=(json.dumps(fixture).encode(),'application/json','https://api.dictionaryapi.dev')):
            self.assertTrue(self.call('/dictionary?word=quux&provider=free')['found'])
        self.call('/data/preferences','PATCH',{'offline':True})
        with patch('newsreader.content.fetch_public') as fetch:
            result=self.call('/dictionary?word=quux&provider=free');self.assertTrue(result['found']);self.assertTrue(result['cached']);fetch.assert_not_called()

    def test_offline_blocks_ai_transport_and_status_is_clear(self):
        self.call('/data/preferences','PATCH',{'offline':True})
        with self.app.app_context(),patch('newsreader.ai.build_opener') as opener:
            self.assertTrue(ai.status()['offline']);self.assertFalse(ai.status()['configured'])
            with self.assertRaises(ai.AIError) as raised:ai.request_json({},ai.configuration())
            self.assertEqual(raised.exception.status,409);opener.assert_not_called()
        self.call('/data/preferences','PATCH',{'offline':False})
        with self.app.app_context():self.assertTrue(ai.status()['configured'])

    def test_archive_manifest_and_wal_contents_are_complete(self):
        word=self.word();record,raw=self.backup()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),record['sha256'])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertEqual(set(archive.namelist()),{'database.sqlite3','manifest.json'})
            manifest=json.loads(archive.read('manifest.json'));content=archive.read('database.sqlite3')
            self.assertEqual(manifest['format'],FORMAT);self.assertEqual(manifest['database_sha256'],hashlib.sha256(content).hexdigest())
        path=Path(self.temp.name)/'restored-copy.sqlite3';path.write_bytes(content)
        with closing(sqlite3.connect(path)) as db:self.assertEqual(db.execute('SELECT word FROM vocabulary WHERE id=?',(word,)).fetchone()[0],'journey')

    def test_backup_does_not_modify_learning_data(self):
        self.word();before=self.snapshot();generation=self.call('/data/state');self.backup()
        self.assertEqual(self.snapshot(),before);self.assertEqual(self.call('/data/state')['revision'],generation['revision'])

    def test_restore_replaces_all_data_keeps_media_and_creates_rescue(self):
        word=self.word();self.sql("UPDATE vocabulary SET note='Original note',mastery='mastered' WHERE id=?",(word,))
        self.sql("INSERT INTO word_cards(word_id,kind,filename,mime,data) VALUES(?, 'audio','sample.wav','audio/wav',?)",(word,b'RIFFsample'))
        self.call('/personalization/profile','PATCH',{'version':0,'level':'B2'})
        self.call('/articles/1/progress','PATCH',{'seconds':30,'progress':30,'position':30,'request_id':'before_backup_1'})
        expected=self.snapshot();old=self.call('/data/state');_,raw=self.backup()
        self.word('different');self.call('/vocabulary/'+str(word),'PATCH',{'note':'Changed'})
        preview=self.preview(raw)['preview'];result=self.call('/data/restore/apply','POST',self.apply(preview))
        self.assertNotEqual(result['generation'],old['generation']);self.assertEqual(self.snapshot(),expected)
        self.assertEqual(self.sql('PRAGMA foreign_key_check'),[])
        self.assertEqual(result['rescue_backup']['kind'],'rescue')
        backup=result['rescue_backup']
        with self.client.get('/api/data/backups/'+backup['id']+'/download') as download:rescue=download.data
        self.assertEqual(self.preview(rescue)['preview']['incoming']['words'],2)

    def test_restore_receipt_retries_do_not_replace_new_writes(self):
        self.word();_,raw=self.backup();preview=self.preview(raw)['preview'];payload=self.apply(preview)
        first=self.call('/data/restore/apply','POST',payload);self.word('later')
        again=self.call('/data/restore/apply','POST',payload);self.assertTrue(again['duplicate']);self.assertEqual(first['generation'],again['generation'])
        self.assertEqual(self.call('/data/state')['summary']['words'],2)
        self.call('/data/restore/apply','POST',{**payload,'request_id':'different_request'},400)

    def test_restore_requires_confirmation_and_current_preview_revision(self):
        _,raw=self.backup();preview=self.preview(raw)['preview'];payload=self.apply(preview)
        self.call('/data/restore/apply','POST',{**payload,'replace':False},400)
        self.word();self.call('/data/restore/apply','POST',payload,409)
        self.assertEqual(self.call('/data/state')['summary']['words'],1)

    def test_old_tab_cannot_write_after_restore_but_old_apply_receipt_can_retry(self):
        old=self.call('/data/state')['generation'];_,raw=self.backup();preview=self.preview(raw)['preview'];payload=self.apply(preview)
        self.call('/data/restore/apply','POST',payload,headers={'X-Margin-Generation':old})
        response=self.client.post('/api/vocabulary',json={'word':'wrong','meaning':'旧页面'},headers={'X-Margin-Generation':old})
        self.assertEqual(response.status_code,409);self.assertEqual(response.json['code'],'DATA_CHANGED')
        self.assertTrue(self.call('/data/restore/apply','POST',payload,headers={'X-Margin-Generation':old})['duplicate'])
        self.assertEqual(self.call('/data/state')['summary']['words'],0)

    def test_active_request_prevents_restore_without_changing_data(self):
        _,raw=self.backup();preview=self.preview(raw)['preview'];control=self.app.extensions['data_manager']
        with control.lock:control.active+=1
        try:self.call('/data/restore/apply','POST',self.apply(preview),409)
        finally:
            with control.lock:control.active-=1
        self.assertEqual(self.call('/data/state')['generation'],preview['generation'])

    def test_new_request_is_blocked_during_maintenance(self):
        control=self.app.extensions['data_manager'];control.maintenance=True
        try:self.call('/vocabulary','POST',{'word':'wrong','meaning':'测试'},409)
        finally:control.maintenance=False
        self.assertEqual(self.call('/data/state')['summary']['words'],0)

    def test_failed_rescue_backup_leaves_current_data_intact(self):
        _,raw=self.backup();self.word();preview=self.preview(raw)['preview'];before=self.snapshot()
        with patch.object(self.app.extensions['data_manager'],'backup',side_effect=ValueError('无法创建还原前备份')):
            self.call('/data/restore/apply','POST',self.apply(preview),400)
        self.assertEqual(self.snapshot(),before);self.assertEqual(self.sql('PRAGMA foreign_keys')[0]['foreign_keys'],1)

    def test_apply_failure_rolls_back_deletes_and_triggers(self):
        _,raw=self.backup();self.word();preview=self.preview(raw)['preview'];before=self.snapshot()
        with patch('newsreader.data_management.rows_into',side_effect=ValueError('Controlled invalid row')):
            self.call('/data/restore/apply','POST',self.apply(preview),400)
        self.assertEqual(self.snapshot(),before);self.word('another')
        self.assertEqual(self.call('/data/state')['summary']['words'],2)
        self.assertGreater(self.call('/data/state')['revision'],preview['revision'])

    def test_malformed_archives_are_rejected_before_writes(self):
        _,raw=self.backup();before=self.snapshot()
        bads=[b'not a zip',self.edit_archive(raw,manifest_change=lambda m:m.update(format='other')),
              self.edit_archive(raw,manifest_change=lambda m:m.update(database_sha256='0'*64)),
              self.edit_archive(raw,extra='../unsafe')]
        for data in bads:self.preview(data,status=400)
        self.assertEqual(self.snapshot(),before)

    def test_extra_tables_views_and_invalid_relations_are_rejected(self):
        _,raw=self.backup()
        for modify in (lambda db:db.execute('CREATE TABLE unknown(x)'),lambda db:db.execute('CREATE VIEW unknown AS SELECT 1'),
                       lambda db:db.execute("INSERT INTO word_meta(word_id) VALUES(999999)")):
            self.preview(self.edit_archive(raw,modify),status=400)
        self.assertEqual(self.call('/data/state')['summary']['words'],0)

    def test_corrupt_json_and_unsupported_columns_are_rejected(self):
        self.word();_,raw=self.backup()
        for modify in (lambda db:db.execute("INSERT INTO learner_profile VALUES(1,'[',1,'2026-11-22')"),lambda db:db.execute('ALTER TABLE articles ADD COLUMN surprise TEXT')):
            self.preview(self.edit_archive(raw,modify),status=400)

    def test_uploaded_triggers_are_not_executed_or_installed(self):
        _,raw=self.backup()
        poisoned=self.edit_archive(raw,lambda db:db.execute('CREATE TRIGGER untrusted AFTER INSERT ON vocabulary BEGIN DELETE FROM articles; END'))
        preview=self.preview(poisoned)['preview'];self.call('/data/restore/apply','POST',self.apply(preview))
        self.word();self.assertEqual(self.call('/data/state')['summary']['articles'],5)
        self.assertEqual(self.sql("SELECT name FROM sqlite_master WHERE name='untrusted'"),[])

    def test_expanded_size_limit_and_upload_media_type(self):
        _,raw=self.backup()
        with patch('newsreader.data_management.MAX_DATABASE',1024):self.preview(raw,status=400)
        self.call('/data/restore/preview','POST',{},400)
        self.assertEqual(self.client.post('/api/data/restore/preview',data=b'bad').status_code,415)

    def test_preview_cancel_expiry_and_source_filename_are_safe(self):
        _,raw=self.backup();preview=self.preview(raw)['preview']
        self.call('/data/previews/'+preview['token'],'DELETE',{})
        self.call('/data/restore/apply','POST',self.apply(preview),400)
        preview=self.preview(raw)['preview'];self.app.extensions['data_manager'].previews[preview['token']]['expires']=0
        self.call('/data/restore/apply','POST',self.apply(preview),400)
        self.call('/data/backups/bad-path/download',status=404)

    def test_cache_cleanup_preserves_learning_and_requires_confirmation(self):
        self.word();self.sql("INSERT INTO dictionary_cache VALUES('test','{}','2026-11-22')")
        self.call('/data/cache/clear','POST',{},400)
        before=self.snapshot();result=self.call('/data/cache/clear','POST',{'confirm':True})
        self.assertEqual(result['removed'],1);after=self.snapshot()
        for table in TABLES:
            if table not in ('dictionary_cache','data_state'):self.assertEqual(before[table],after[table])

    def test_backup_delete_only_removes_selected_file(self):
        first,_=self.backup();second,_=self.backup();before=self.snapshot()
        self.call('/data/backups/'+first['id'],'DELETE',{},400)
        self.call('/data/backups/'+first['id'],'DELETE',{'confirm':True})
        self.assertEqual([b['id'] for b in self.call('/data/state')['backups']],[second['id']]);self.assertEqual(self.snapshot(),before)

    def test_article_pack_export_import_dedup_and_receipt(self):
        exported=self.client.get('/api/data/articles/export');pack=json.loads(exported.data);self.assertEqual(pack['format'],ARTICLE_FORMAT)
        self.assertNotIn('progress',pack['articles'][0]);self.assertNotIn('id',pack['articles'][0])
        pack['articles'].append({'title':'New offline article','content':'The city has a small park. '*10,'category':'生活'})
        preview=self.preview(json.dumps(pack).encode(),'/data/articles/preview')['preview'];self.assertEqual(preview['incoming']['new_articles'],1)
        payload={'token':preview['token'],'request_id':'articles_request_1'}
        result=self.call('/data/articles/apply','POST',payload);self.assertEqual(result['added'],1);self.assertEqual(result['skipped'],5)
        self.assertTrue(self.call('/data/articles/apply','POST',payload)['duplicate']);self.assertEqual(self.call('/data/state')['summary']['articles'],6)

    def test_article_import_rechecks_duplicates_after_preview(self):
        row={'title':'New','content':'The city has a small park. '*10,'category':'生活'}
        pack={'format':ARTICLE_FORMAT,'articles':[row]};preview=self.preview(json.dumps(pack).encode(),'/data/articles/preview')['preview']
        self.call('/articles','POST',row,201)
        result=self.call('/data/articles/apply','POST',{'token':preview['token'],'request_id':'article_race_123'})
        self.assertEqual(result['added'],0);self.assertEqual(result['skipped'],1)

    def test_article_validation_is_atomic_and_does_not_fetch_source_urls(self):
        good={'title':'New','content':'The city has a small park. '*10,'source_url':'https://example.com/article'}
        for extra in ({'content':'short'},{'category':'bad'},{'source_url':'javascript:alert(1)'},{'source_url':'https://user:pass@example.com'},{'progress':100},{'title':['bad']}):
            pack={'format':ARTICLE_FORMAT,'articles':[good,{**good,**extra}]}
            self.preview(json.dumps(pack).encode(),'/data/articles/preview',400)
        self.assertEqual(self.call('/data/state')['summary']['articles'],5)
        self.call('/data/preferences','PATCH',{'offline':True})
        with patch('newsreader.network.socket.getaddrinfo') as dns:
            preview=self.preview(json.dumps({'format':ARTICLE_FORMAT,'articles':[good]}).encode(),'/data/articles/preview')['preview']
            self.call('/data/articles/apply','POST',{'token':preview['token'],'request_id':'offline_articles_1'});dns.assert_not_called()

    def test_cross_origin_upload_and_writes_are_rejected(self):
        response=self.client.post('/api/data/restore/preview',data={'file':(io.BytesIO(b'zip'),'x.zip')},headers={'Origin':'https://outside.example'})
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.call('/data/state')['summary']['articles'],5)

    def test_restore_holds_an_exclusive_boundary_during_real_concurrent_request(self):
        _,raw=self.backup();preview=self.preview(raw)['preview'];entered=threading.Event();release=threading.Event()
        control=self.app.extensions['data_manager'];real_backup=control.backup
        def blocked_backup(kind):
            entered.set();release.wait(5);return real_backup(kind)
        def restore():
            with self.app.test_client() as client:return client.post('/api/data/restore/apply',json=self.apply(preview)).status_code
        with patch.object(control,'backup',side_effect=blocked_backup),ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(restore);self.assertTrue(entered.wait(5))
            try:self.call('/vocabulary','POST',{'word':'overlap','meaning':'不应写入'},409)
            finally:release.set()
            self.assertEqual(future.result(5),200)
        self.assertEqual(self.call('/data/state')['summary']['words'],0)

    def test_concurrent_article_confirmation_returns_one_consistent_receipt(self):
        pack={'format':ARTICLE_FORMAT,'articles':[{'title':'New','content':'The city has a small park. '*10}]}
        preview=self.preview(json.dumps(pack).encode(),'/data/articles/preview')['preview'];payload={'token':preview['token'],'request_id':'concurrent_import_1'}
        def submit():
            with self.app.test_client() as client:
                response=client.post('/api/data/articles/apply',json=payload);self.assertEqual(response.status_code,200);return response.json
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:submit(),range(2)))
        self.assertEqual([row['added'] for row in results],[1,1]);self.assertEqual(sum(bool(row['duplicate']) for row in results),1)
        self.assertEqual(self.call('/data/state')['summary']['articles'],6)

    def test_restore_invalidates_unapplied_article_previews(self):
        _,raw=self.backup();pack={'format':ARTICLE_FORMAT,'articles':[{'title':'New','content':'The city has a small park. '*10}]}
        article_preview=self.preview(json.dumps(pack).encode(),'/data/articles/preview')['preview']
        restored=self.preview(raw)['preview'];self.call('/data/restore/apply','POST',self.apply(restored))
        self.call('/data/articles/apply','POST',{'token':article_preview['token'],'request_id':'stale_article_123','generation':self.call('/data/state')['generation']},409)
        self.assertEqual(self.call('/data/state')['summary']['articles'],5)

    def test_corrupt_backup_metadata_does_not_break_startup_state(self):
        record,_=self.backup();control=self.app.extensions['data_manager']
        target=control.backups/(record['id']+'.json')
        for bad in ({'id':record['id'],'created_at':None},[],{'id':record['id'],'kind':'manual','size':1,'summary':{},'created_at':'bad','sha256':'0'*64}):
            target.write_text(json.dumps(bad),encoding='utf-8')
            info=self.call('/data/state');self.assertEqual(info['backups'],[]);self.assertEqual(info['summary']['articles'],5)

    def test_cache_usage_counts_utf8_payload_bytes(self):
        payload=json.dumps({'meaning':'中文释义'},ensure_ascii=False)
        self.sql('INSERT INTO dictionary_cache VALUES(?,?,?)',('test',payload,'2026-11-22'))
        self.assertEqual(self.call('/data/state')['storage']['cache_bytes'],len(payload.encode('utf-8')))

    def test_finished_previews_release_uploaded_files_but_keep_receipts(self):
        pack={'format':ARTICLE_FORMAT,'articles':[{'title':'New','content':'The city has a small park. '*10}]}
        payload=None
        for index in range(6):
            preview=self.preview(json.dumps(pack).encode(),'/data/articles/preview')['preview']
            item=self.app.extensions['data_manager'].previews[preview['token']]
            payload={'token':preview['token'],'request_id':'completed_import_'+str(index)}
            self.call('/data/articles/apply','POST',payload)
            self.assertFalse(item['directory'].exists())
        self.assertTrue(self.call('/data/articles/apply','POST',payload)['duplicate'])

    def test_pre_upgrade_tabs_without_generation_cannot_write_after_restore_or_restart(self):
        _,raw=self.backup();preview=self.preview(raw)['preview'];self.call('/data/restore/apply','POST',self.apply(preview))
        for client in (self.client,create_app({'TESTING':True,'DATABASE':self.path}).test_client()):
            response=client.post('/api/vocabulary',json={'word':'oldclient','meaning':'不应写入'})
            self.assertEqual(response.status_code,409);self.assertEqual(response.json['code'],'DATA_CHANGED')
        self.assertEqual(self.call('/data/state')['summary']['words'],0)

    def test_auto_backup_is_once_per_day_and_keeps_seven_only_auto(self):
        self.call('/data/preferences','PATCH',{'auto_backup':True})
        manual,_=self.backup();self.app.config['TESTING']=False
        # Exercise the production startup path with a controlled safe temporary database.
        for i in range(9):
            with patch('newsreader.data_management.now',return_value=f'2026-11-{10+i:02d}T10:00:00+08:00'):
                create_app({'DATABASE':self.path})
        app=create_app({'TESTING':True,'DATABASE':self.path});items=app.test_client().get('/api/data/state').json['backups']
        self.assertEqual(sum(b['kind']=='auto' for b in items),7);self.assertIn(manual['id'],[b['id'] for b in items])
        with patch('newsreader.data_management.now',return_value='2026-11-18T11:00:00+08:00'):create_app({'DATABASE':self.path})
        self.assertEqual(len(app.test_client().get('/api/data/state').json['backups']),8)


if __name__=='__main__':unittest.main()
