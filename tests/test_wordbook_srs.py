"""Transactional wordbook and SM-2 regression tests using disposable databases."""
import base64
import csv
import io
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app import create_app
from newsreader.db import CHINA_TZ, get_db
from newsreader.srs import cloze, next_state

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jduEAAAAASUVORK5CYII=')


class WordbookSrsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'test.sqlite3')
        self.time = datetime(2026, 11, 19, 9, 0, tzinfo=CHINA_TZ)
        self.clock = patch('newsreader.srs.clock', side_effect=lambda: self.time)
        self.clock.start()
        self.app = create_app({'TESTING': True, 'DATABASE': self.path})
        self.client = self.app.test_client()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.clock.stop)

    def post(self, path, data, status=200):
        response = self.client.post('/api' + path, json=data)
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        return response.json

    def entry(self, word='journey', **extra):
        return self.post('/wordbook', {'word': word, 'meaning': '旅程', **extra}, 201)['entry']

    def get(self, word_id):
        return self.client.get(f'/api/wordbook/{word_id}').json['entry']

    def grade(self, entry, grade=4, token=None, **extra):
        return self.post('/review/answer', {'word_id': entry['id'], 'version': entry['schedule']['version'],
            'content_version': entry['version'], 'grade': grade, 'request_id': token or f"review_{entry['id']}_{entry['schedule']['version']}", **extra})

    def sql(self, query, args=()):
        with self.app.app_context():
            db = get_db()
            rows = [dict(row) for row in db.execute(query, args).fetchall()]
            db.commit()
            return rows

    def test_assets_and_navigation(self):
        home = self.client.get('/').get_data(as_text=True)
        self.assertIn('href="#review"', home)
        for asset in ('wordbook.js', 'srs.js', 'vocabulary.css'):
            with self.client.get('/static/' + asset) as response:
                self.assertEqual(response.status_code, 200)

    def test_rich_entry_persists_after_restart(self):
        e = self.entry(senses=[{'meaning': '旅程', 'part_of_speech': 'n.', 'example': 'A journey home.'}, {'meaning': '旅行', 'part_of_speech': 'v.'}],
            contexts=[{'text': 'Our journey begins.', 'article_id': 1}, {'text': 'A journey home.', 'translation': '归途'}], notes=[{'title': '联想', 'body': '保留'}])
        restarted = create_app({'TESTING': True, 'DATABASE': self.path}).test_client()
        found = restarted.get(f"/api/wordbook/{e['id']}").json['entry']
        self.assertEqual(len(found['senses']), 2)
        self.assertEqual(len(found['contexts']), 2)
        self.assertEqual(found['notes'][0]['body'], '保留')
        self.assertEqual(found['contexts'][0]['article_title'], self.client.get('/api/articles/1').json['title'])
        self.assertEqual(found['schedule']['state'], 'new')

    def test_legacy_collection_adds_new_context_without_overwriting_notes(self):
        e = self.entry(note='自己的笔记', contexts=[{'text': 'Journey one.'}])
        self.post('/vocabulary', {'word': 'JOURNEY', 'meaning': '另一释义', 'context': 'Journey two.', 'article_id': 2})
        result = self.get(e['id'])
        self.assertEqual([c['text'] for c in result['contexts']], ['Journey one.', 'Journey two.'])
        self.assertEqual(result['meaning'], '旅程')
        self.assertEqual(result['note'], '自己的笔记')
        self.assertGreater(result['version'], e['version'])

    def test_phrase_pattern_and_duplicate_validation(self):
        for word in ('journey', 'well-being', "reader's"):
            self.entry(word)
        self.post('/wordbook', {'word':'reader’s', 'meaning':'读者的'}, 409)
        self.entry('take into account', entry_kind='phrase')
        self.entry('the more + clause, the more + clause', entry_kind='pattern')
        self.post('/wordbook', {'word':'take into account', 'meaning':'考虑'}, 409)
        for word in ('one two three four five six seven', '123', '<script>', 'read-'):
            self.post('/wordbook', {'word':word, 'meaning':'测试'}, 400)
        for extra in ({'word':'<script>'}, {'entry_kind':[]}, {'senses':[]}, {'senses':[None]}, {'contexts':[{'text':'hi', 'article_id':True}]}, {'notes':[{'title':'x', 'body':None}]}, {'mastery':'done'}, {'collection_ids':[True]}):
            self.post('/wordbook', {'word':'test', 'meaning':'测试', **extra}, 400)

    def test_collections_case_insensitive_and_delete_preserves_words(self):
        book = self.post('/wordbook/collections', {'kind':'book','name':'News'}, 201)
        again = self.post('/wordbook/collections', {'kind':'book','name':'NEWS'}, 201)
        self.assertEqual(book['id'], again['id'])
        e = self.entry(collection_ids=[book['id']])
        self.assertEqual(len(e['collections']), 1)
        self.assertEqual(self.client.delete('/api/wordbook/collections/' + str(book['id']), json={}).status_code, 200)
        self.assertEqual(self.get(e['id'])['collections'], [])
        self.post('/wordbook/collections', {'kind':'unknown','name':'test'}, 400)

    def test_optimistic_edit_conflict_and_atomic_validation(self):
        e = self.entry()
        payload = {'version':e['version'], 'senses':[{'meaning':'新释义'}]}
        response = self.client.patch(f"/api/wordbook/{e['id']}", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['entry']['meaning'], '新释义')
        self.assertEqual(self.client.patch(f"/api/wordbook/{e['id']}", json=payload).status_code, 409)
        latest = self.get(e['id'])
        response = self.client.patch(f"/api/wordbook/{e['id']}", json={'version':latest['version'], 'note':'不能部分保存', 'collection_ids':[9999]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.get(e['id'])['note'], '')

    def test_basic_edit_keeps_primary_sense_in_sync(self):
        e = self.entry()
        self.client.patch(f"/api/vocabulary/{e['id']}", json={'meaning':'同步', 'part_of_speech':''})
        found = self.get(e['id'])
        self.assertEqual(found['senses'][0]['meaning'], '同步')
        self.assertEqual(found['senses'][0]['part_of_speech'], found['part_of_speech'])
        self.assertEqual(found['version'], e['version'] + 1)

    def test_article_deletion_preserves_context_snapshot(self):
        e = self.entry(contexts=[{'text':'A journey.', 'article_id':1}])
        self.assertEqual(self.client.delete('/api/articles/1', json={}).status_code, 200)
        context = self.get(e['id'])['contexts'][0]
        self.assertIsNone(context['article_id'])
        self.assertTrue(context['article_title'])

    def test_media_upload_download_duplicate_and_deletion(self):
        e = self.entry()
        payload = {'version':e['version'], 'kind':'image','mime':'image/png','base64':base64.b64encode(PNG).decode(), 'filename':'tiny.png'}
        updated = self.post(f"/wordbook/{e['id']}/cards", payload, 201)['entry']
        card = updated['cards'][0]
        self.assertNotIn('base64', card)
        response = self.client.get('/api/wordbook/media/' + str(card['id']))
        self.assertEqual(response.data, PNG)
        self.assertEqual(response.mimetype, 'image/png')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.post(f"/wordbook/{e['id']}/cards", payload, 409)
        updated = self.post(f"/wordbook/{e['id']}/cards", {**payload, 'version':updated['version']}, 201)['entry']
        self.assertEqual(len(updated['cards']), 1)
        self.assertEqual(self.client.delete(f"/api/wordbook/{e['id']}/cards/{card['id']}", json={'version':updated['version']}).status_code, 200)
        self.assertEqual(self.client.get('/api/wordbook/media/' + str(card['id'])).status_code, 404)

    def test_invalid_media_never_stored(self):
        e = self.entry()
        for extra in ({'mime':[]}, {'mime':'image/svg+xml'}, {'base64':'!!!'}, {'base64':base64.b64encode(b'<script>'+b'x'*20).decode()}, {'kind':'audio','mime':'audio/wav'}):
            self.post(f"/wordbook/{e['id']}/cards", {'version':e['version'], 'kind':'image','mime':'image/png','base64':base64.b64encode(PNG).decode(), **extra}, 400)
        self.assertEqual(self.get(e['id'])['cards'], [])

    def test_example_and_audio_cards(self):
        e = self.entry()
        e = self.post(f"/wordbook/{e['id']}/cards", {'version':e['version'],'kind':'example','front':'A journey home.', 'back':'归途'}, 201)['entry']
        raw = b'RIFF' + b'\x00'*4 + b'WAVEfmt ' + b'\x00'*40
        e = self.post(f"/wordbook/{e['id']}/cards", {'version':e['version'],'kind':'audio','mime':'audio/wav','base64':base64.b64encode(raw).decode()}, 201)['entry']
        self.assertEqual([c['kind'] for c in e['cards']], ['example','audio'])

    def test_merge_inflections_preserves_target_schedule_and_history(self):
        target = self.entry('journey', note='目标笔记', contexts=[{'text':'A journey.'}])
        source = self.entry('journeys', note='来源笔记', contexts=[{'text':'Many journeys.'}], senses=[{'meaning':'多次旅行'}])
        self.grade(source, 0)
        target_schedule = self.get(target['id'])['schedule']
        result = self.post('/wordbook/merge', {'target_id':target['id'],'source_ids':[source['id']], 'versions':{str(target['id']):target['version'],str(source['id']):source['version']}})['entry']
        self.assertEqual(result['schedule'], target_schedule)
        self.assertEqual(result['aliases'], ['journeys'])
        self.assertEqual(len(result['contexts']), 2)
        self.assertEqual(len(result['senses']), 2)
        self.assertEqual(result['notes'][0]['body'], '来源笔记')
        self.assertEqual(self.client.get(f"/api/wordbook/{source['id']}").status_code, 404)
        event = self.sql('SELECT * FROM srs_events')[0]
        self.assertEqual(event['word_id'], target['id'])
        self.assertEqual(event['original_word_id'], source['id'])
        self.assertEqual(self.client.get('/api/review/queue').json['stats']['answers_today'], 1)

    def test_merge_version_conflict_leaves_everything(self):
        a, b = self.entry('run'), self.entry('running')
        self.post('/wordbook/merge', {'target_id':a['id'],'source_ids':[b['id']], 'versions':{str(a['id']):0,str(b['id']):b['version']}}, 409)
        self.assertEqual(len(self.client.get('/api/wordbook').json['entries']), 2)

    def test_capacity_failure_rolls_back_merge_and_import(self):
        a = self.entry('journey', contexts=[{'text':f'context {i}'} for i in range(30)])
        b = self.entry('journeys', contexts=[{'text':'One more.'}])
        self.post('/wordbook/merge', {'target_id':a['id'],'source_ids':[b['id']], 'versions':{str(a['id']):a['version'],str(b['id']):b['version']}}, 400)
        self.assertEqual(len(self.get(a['id'])['contexts']), 30)
        self.assertEqual(self.get(b['id'])['word'], 'journeys')
        self.post('/wordbook/import', {'mode':'merge','entries':[{'word':'journey','meaning':'旅程','contexts':[{'text':'Overflow.'}]}]}, 400)
        self.assertEqual(len(self.get(a['id'])['contexts']), 30)

    def test_json_roundtrip_preserves_rich_data_aliases_and_media(self):
        e = self.entry(contexts=[{'text':'Journey.', 'translation':'旅程'}],notes=[{'title':'联想','body':'保存'}])
        self.sql('INSERT INTO word_aliases VALUES(?,?)', (e['id'],'journeys'))
        self.post(f"/wordbook/{e['id']}/cards", {'version':e['version'],'kind':'image','mime':'image/png','base64':base64.b64encode(PNG).decode()}, 201)
        packed = self.client.get('/api/wordbook/export?media=1').json
        self.client.delete(f"/api/vocabulary/{e['id']}", json={})
        result = self.post('/wordbook/import', packed)
        self.assertEqual(result['added'], 1)
        found = self.client.get('/api/wordbook').json['entries'][0]
        self.assertEqual(found['aliases'], ['journeys'])
        self.assertEqual(found['contexts'][0]['translation'], '旅程')
        self.assertEqual(found['notes'][0]['body'], '保存')
        self.assertEqual(len(found['cards']), 1)
        self.assertEqual(found['schedule']['state'], 'new')

    def test_import_preview_does_not_write(self):
        self.entry()
        payload = {'preview':True,'entries':[{'word':'journey','meaning':'旅程'},{'word':'city','meaning':'城市'}]}
        result = self.post('/wordbook/import', payload)
        self.assertEqual((result['count'], result['duplicates']), (2,1))
        self.assertEqual(len(self.client.get('/api/wordbook').json['entries']), 1)

    def test_import_skip_merge_preserves_mastery_and_srs(self):
        e = self.entry(note='原笔记', mastery='mastered')
        self.grade(e)
        before = self.get(e['id'])['schedule']
        payload = {'entries':[{'word':'journey','meaning':'新的义项','note':'追加笔记','mastery':'new','contexts':[{'text':'Second journey.'}], 'cards':[{'kind':'example','front':'A journey.', 'back':'旅程'}]}]}
        self.assertEqual(self.post('/wordbook/import', payload)['skipped'], 1)
        for _ in range(2): self.post('/wordbook/import', {**payload,'mode':'merge'})
        found = self.get(e['id'])
        self.assertEqual(found['mastery'], 'mastered')
        self.assertEqual(found['note'], '原笔记')
        self.assertEqual(found['schedule'], before)
        self.assertEqual(len(found['senses']), 2)
        self.assertEqual(len(found['notes']), 1)
        self.assertEqual(len(found['cards']), 1)

    def test_invalid_batch_is_atomic(self):
        self.post('/wordbook/import', {'entries':[{'word':'valid','meaning':'有效'}, {'word':'bad','meaning':''}]}, 400)
        self.assertEqual(self.client.get('/api/wordbook').json['entries'], [])
        for rows in ([], [None], [{'word':'ok','meaning':'好','collections':[None]}], [{'word':'ok','meaning':'好','aliases':[False]}]):
            self.post('/wordbook/import', {'entries':rows}, 400)

    def test_csv_multiline_classifications_and_formula_escaping(self):
        raw = 'word,meaning,note,tags,books\njourney,"旅程\n旅行",=SUM(A1),外刊|出行,News\n'
        self.post('/wordbook/import', {'format':'csv','csv':'\ufeff'+raw})
        found = self.client.get('/api/wordbook').json['entries'][0]
        self.assertEqual(found['meaning'], '旅程\n旅行')
        self.assertEqual(len(found['collections']), 3)
        data = self.client.get('/api/wordbook/export?format=csv').data.decode('utf-8-sig')
        row = list(csv.DictReader(io.StringIO(data)))[0]
        self.assertEqual(row['note'], "'=SUM(A1)")
        self.post('/wordbook/import', {'format':'csv','csv':'word,meaning\na,b,c'}, 400)

    def test_export_selection_and_limits(self):
        a = self.entry('journey')
        self.entry('city')
        self.assertEqual(len(self.client.get('/api/wordbook/export?ids='+str(a['id'])).json['entries']), 1)
        self.assertEqual(self.client.get('/api/wordbook/export?ids=1,abc').status_code, 400)
        self.post('/wordbook/import', {'entries':[{'word':'a','meaning':'a'}]*501}, 400)

    def test_sm2_intervals_ease_and_relearning(self):
        e = self.entry()
        first = self.grade(e, 5)['card']
        self.assertEqual((first['interval_days'],first['repetitions'],first['ease']), (1,1,2.6))
        self.time += timedelta(days=1)
        second = self.grade(self.get(e['id']), 4)['card']
        self.assertEqual(second['interval_days'], 6)
        self.time += timedelta(days=6)
        third = self.grade(self.get(e['id']), 3)['card']
        self.assertEqual(third['interval_days'], 16)  # ceil(6 * old ease 2.6)
        self.assertEqual(third['ease'], 2.46)
        self.time += timedelta(days=16)
        forgotten = self.grade(self.get(e['id']), 0)['card']
        self.assertEqual((forgotten['state'],forgotten['interval_days'],forgotten['repetitions']), ('relearning',0,0))
        self.assertEqual(forgotten['due_at'], (self.time+timedelta(minutes=10)).isoformat())
        self.assertEqual(len(self.client.get('/api/review/queue').json['cards']), 0)
        self.time += timedelta(minutes=10)
        recovered = self.grade(self.get(e['id']), 4)['card']
        self.assertEqual(recovered['interval_days'], 1)
        self.assertEqual(recovered['lapses'], 1)

    def test_ease_floor_and_interval_cap(self):
        base = {'ease':1.3,'repetitions':0,'interval_days':0,'lapses':0}
        self.assertEqual(next_state(base,0,self.time)['ease'], 1.3)
        self.assertEqual(next_state({**base,'repetitions':100,'interval_days':36500,'ease':10},5,self.time)['interval_days'], 36500)

    def test_grading_idempotence_and_token_misuse(self):
        e = self.entry()
        payload = {'word_id':e['id'],'version':e['schedule']['version'],'content_version':e['version'],'grade':4,'request_id':'same_request'}
        first = self.post('/review/answer', payload)
        second = self.post('/review/answer', payload)
        self.assertFalse(first['duplicate'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(first['card'], second['card'])
        self.post('/review/answer', {**payload,'grade':5}, 409)
        self.assertEqual(len(self.sql('SELECT * FROM srs_events')), 1)

    def test_stale_content_or_schedule_is_not_graded(self):
        e = self.entry()
        self.client.patch(f"/api/wordbook/{e['id']}",json={'version':e['version'],'note':'updated'})
        for content,version in ((e['version'],1),(e['version']+1,0)):
            self.post('/review/answer', {'word_id':e['id'],'version':version,'content_version':content,'grade':4,'request_id':'stale_request'}, 409)
        self.assertEqual(self.sql('SELECT * FROM srs_events'), [])

    def test_daily_limits_relearning_and_next_day_reset(self):
        a, b = self.entry('journey'), self.entry('city')
        self.client.patch('/api/review/settings', json={'new_limit':1,'review_limit':0})
        self.assertEqual(len(self.client.get('/api/review/queue').json['cards']), 1)
        self.grade(a, 0)
        self.post('/review/answer', {'word_id':b['id'],'version':1,'content_version':b['version'],'grade':4,'request_id':'over_new_limit'}, 409)
        self.time += timedelta(minutes=10)
        self.assertEqual([e['id'] for e in self.client.get('/api/review/queue').json['cards']], [a['id']])
        self.grade(self.get(a['id']),4)
        self.time += timedelta(days=1)
        queue = self.client.get('/api/review/queue').json
        self.assertEqual(queue['stats']['new_today'], 0)
        self.assertEqual(queue['stats']['due'], 1)
        self.assertEqual([e['id'] for e in queue['cards']], [b['id']])
        self.client.patch('/api/review/settings', json={'review_limit':1})
        self.assertEqual([e['id'] for e in self.client.get('/api/review/queue').json['cards']], [a['id'],b['id']])

    def test_review_quota_counts_words_not_relearning_answers(self):
        a, b = self.entry('journey'), self.entry('city')
        self.grade(a);self.grade(b)
        self.time += timedelta(days=1)
        self.client.patch('/api/review/settings',json={'review_limit':1})
        self.grade(self.get(a['id']),0)
        self.time += timedelta(minutes=10)
        self.grade(self.get(a['id']),4)
        q = self.client.get('/api/review/queue').json
        self.assertEqual(q['stats']['review_today'],1)
        self.assertEqual(q['stats']['answers_today'],2)
        self.assertEqual(q['cards'],[])

    def test_beijing_midnight_resets_new_quota(self):
        self.time = datetime(2026,11,19,23,59,tzinfo=CHINA_TZ)
        a, b = self.entry('journey'), self.entry('city')
        self.client.patch('/api/review/settings',json={'new_limit':1})
        self.grade(a)
        self.assertEqual(self.client.get('/api/review/queue').json['cards'], [])
        self.time += timedelta(minutes=2)
        self.assertEqual([e['id'] for e in self.client.get('/api/review/queue').json['cards']], [b['id']])

    def test_pause_resume_and_difficult_management(self):
        e = self.entry()
        response=self.client.patch(f"/api/review/cards/{e['id']}",json={'version':1,'suspended':True,'leech':True})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get('/api/review/queue').json['cards'],[])
        self.post('/review/answer',{'word_id':e['id'],'version':2,'content_version':e['version'],'grade':4,'request_id':'paused_word'},409)
        self.assertEqual(self.client.patch(f"/api/review/cards/{e['id']}",json={'version':1,'suspended':False}).status_code,409)
        self.assertEqual(self.client.patch(f"/api/review/cards/{e['id']}",json={'version':2,'suspended':False}).status_code,200)
        self.assertEqual(self.get(e['id'])['schedule']['due_at'],e['schedule']['due_at'])
        self.assertEqual(len(self.client.get('/api/review/queue').json['cards']),1)

    def test_repeated_lapses_mark_leech_without_auto_pausing(self):
        e = self.entry()
        for _ in range(8):
            self.grade(self.get(e['id']),0)
            self.time += timedelta(minutes=10)
        card = self.get(e['id'])['schedule']
        self.assertEqual((card['leech'],card['suspended'],card['lapses']), (1,0,8))

    def test_manual_mastery_is_independent(self):
        e = self.entry(mastery='mastered')
        self.grade(e,0)
        self.assertEqual(self.get(e['id'])['mastery'],'mastered')
        self.assertEqual(len(self.client.get('/api/review/queue').json['cards']),0)
        self.time += timedelta(minutes=10)
        self.assertEqual(len(self.client.get('/api/review/queue').json['cards']),1)

    def test_deletion_keeps_daily_usage_and_duplicate_receipt(self):
        e = self.entry()
        self.grade(e,token='deleted_receipt')
        self.client.delete(f"/api/vocabulary/{e['id']}", json={})
        self.assertEqual(self.sql('SELECT word_id FROM srs_events')[0]['word_id'],None)
        self.assertEqual(self.client.get('/api/review/queue').json['stats']['new_today'],1)
        self.assertTrue(self.grade(e,token='deleted_receipt')['duplicate'])

    def test_cloze_boundaries_and_aliases(self):
        self.assertEqual(cloze('art',[{'text':'A party is starting.'}],[]),'')
        self.assertEqual(cloze('journey',[{'text':'Journeys and a journey.'}],['journeys']),'____ and a ____.')
        self.assertEqual(cloze("reader's",[{'text':'A reader’s book.'}],[]),'A ____ book.')
        self.assertEqual(cloze('take into account',[{'text':'We TAKE  INTO account cost.'}],[]),'We ____ cost.')

    def test_spelling_alias_and_collection_filter(self):
        collection = self.post('/wordbook/collections',{'kind':'book','name':'学习'},201)
        e = self.entry(collection_ids=[collection['id']])
        self.entry('city')
        self.sql('INSERT INTO word_aliases VALUES(?,?)',(e['id'],'journeys'))
        queue = self.client.get('/api/review/queue?collection='+str(collection['id'])).json
        self.assertEqual([entry['id'] for entry in queue['cards']],[e['id']])
        self.assertTrue(self.grade(e,mode='spelling',attempt=' JOURNEYS ')['correct'])
        self.assertEqual(self.client.get('/api/review/queue?collection=bad').status_code,400)

    def test_settings_atomic_validation_and_bad_answers(self):
        for payload in ({'new_limit':1,'review_limit':True},{'new_limit':-1},{'review_limit':2001},{'other':1}):
            self.assertEqual(self.client.patch('/api/review/settings',json=payload).status_code,400)
        self.assertEqual(self.client.get('/api/review/settings').json,{'new_limit':10,'review_limit':50})
        e = self.entry()
        for extra in ({'grade':True},{'grade':6},{'version':'1'},{'request_id':'bad'},{'mode':'unknown'},{'attempt':[]}):
            self.post('/review/answer',{'word_id':e['id'],'version':1,'content_version':e['version'],'grade':4,'request_id':'valid_request',**extra},400)
        self.assertEqual(self.sql('SELECT * FROM srs_events'),[])

    def test_restart_keeps_schedule_events_and_srs_preferences(self):
        e = self.entry()
        self.grade(e, 5)
        before = self.get(e['id'])['schedule']
        self.client.patch('/api/review/settings', json={'new_limit':0, 'review_limit':100})
        restarted = create_app({'TESTING':True,'DATABASE':self.path}).test_client()
        self.assertEqual(restarted.get(f"/api/wordbook/{e['id']}").json['entry']['schedule'], before)
        self.assertEqual(restarted.get('/api/review/settings').json, {'new_limit':0,'review_limit':100})
        self.assertEqual(restarted.get('/api/review/queue').json['stats']['answers_today'], 1)

    def test_upgrading_existing_vocabulary_backfills_once(self):
        e = self.entry(contexts=[{'text':'An existing journey.', 'article_id':1}], mastery='mastered')
        with self.app.app_context():
            db=get_db()
            for table in ['srs_events','srs_cards','word_cards','word_aliases','word_notes','word_senses','word_contexts','word_memberships','word_collections','word_meta']:
                db.execute('DROP TABLE '+table)
            db.commit()
        for _ in range(2):
            self.client = create_app({'TESTING':True,'DATABASE':self.path}).test_client()
        found=self.get(e['id'])
        self.assertEqual(len(found['senses']),1)
        self.assertEqual(len(found['contexts']),1)
        self.assertEqual(found['mastery'],'mastered')
        self.assertEqual(found['schedule']['state'],'new')

    def test_context_cap_rejects_collection_without_partial_changes(self):
        e=self.entry(contexts=[{'text':f'Journey {i}.'} for i in range(30)])
        self.post('/vocabulary',{'word':'journey','meaning':'变化','context':'One too many.'},400)
        self.assertEqual(len(self.get(e['id'])['contexts']),30)
        self.assertEqual(self.get(e['id'])['version'],e['version'])

    def test_incompatible_pattern_merge_and_kind_change_are_atomic(self):
        a=self.entry()
        b=self.entry('the more + clause',entry_kind='pattern')
        self.post('/wordbook/merge',{'target_id':a['id'],'source_ids':[b['id']],'versions':{str(a['id']):a['version'],str(b['id']):b['version']}},400)
        response=self.client.patch(f"/api/wordbook/{b['id']}",json={'version':b['version'],'entry_kind':'word','note':'未保存'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.get(b['id'])['entry_kind'],'pattern')
        self.assertEqual(self.get(b['id'])['note'],'')

    def test_malformed_csv_returns_validation_error(self):
        for raw in ['word,meaning\na,"unterminated', 'word,meaning\na,'+'x'*140000]:
            self.post('/wordbook/import',{'format':'csv','csv':raw},400)
        self.assertEqual(self.client.get('/api/wordbook').json['entries'],[])

    def test_preview_counts_duplicates_within_the_file(self):
        rows=[{'word':'journey','meaning':'旅程'},{'word':'JOURNEY','meaning':'旅行'}]
        result=self.post('/wordbook/import',{'entries':rows,'preview':True})
        self.assertEqual(result['duplicates'],1)
        result=self.post('/wordbook/import',{'entries':rows})
        self.assertEqual((result['added'],result['skipped']),(1,1))

    def test_media_above_default_request_limit_and_file_size_cap(self):
        import struct
        import zlib
        e=self.entry()
        ancillary=b'tEXt'+b'Comment\0'+b'x'*(600*1024)
        chunk=struct.pack('>I',len(ancillary)-4)+ancillary+struct.pack('>I',zlib.crc32(ancillary))
        raw=PNG[:-12]+chunk+PNG[-12:]
        payload={'version':e['version'],'kind':'image','mime':'image/png','base64':base64.b64encode(raw).decode()}
        updated=self.post(f"/wordbook/{e['id']}/cards",payload,201)['entry']
        self.assertEqual(updated['cards'][0]['size'],len(raw))
        oversized=PNG[:8]+b'x'*(2*1024*1024)+PNG[-12:]
        self.post(f"/wordbook/{e['id']}/cards",{**payload,'version':updated['version'],'base64':base64.b64encode(oversized).decode()},400)
        self.assertEqual(len(self.get(e['id'])['cards']),1)


if __name__ == '__main__':
    unittest.main()
