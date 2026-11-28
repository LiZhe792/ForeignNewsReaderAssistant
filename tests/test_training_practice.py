"""Listening and reading integration checks with local media, synthetic AI and a fixed clock."""
import base64
import copy
import json
import struct
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app import create_app
from newsreader.db import CHINA_TZ, get_db
from newsreader.exercises import BANK, TYPES
from newsreader.training import compare, units


def wave():
    payload=b'\0'*1600
    return b'RIFF'+struct.pack('<I',36+len(payload))+b'WAVEfmt '+struct.pack('<IHHIIHH',16,1,1,8000,16000,2,16)+b'data'+struct.pack('<I',len(payload))+payload


class PracticeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=str(Path(self.temp.name)/'practice.sqlite3')
        self.current=datetime(2026,11,20,9,0,tzinfo=CHINA_TZ)
        self.clock=patch('newsreader.exercises.clock',side_effect=lambda:self.current);self.clock.start()
        self.app=create_app({'TESTING':True,'DATABASE':self.path,'AI_API_KEY':'synthetic-only-key','AI_MODEL':'test-model'})
        self.client=self.app.test_client();self.counter=0
        self.addCleanup(self.temp.cleanup);self.addCleanup(self.clock.stop)

    def call(self,path,method='GET',data=None,status=200):
        response=self.client.open('/api'+path,method=method,json=data)
        self.assertEqual(response.status_code,status,response.get_data(as_text=True))
        return response.json

    def serial(self):
        self.counter+=1;return f'practice_request_{self.counter}'

    def source(self,article_id=1,paragraph=0,unit=0):
        manifest=self.call(f'/training/articles/{article_id}?paragraph={paragraph}')
        return {'article_id':article_id,'article_hash':manifest['article']['hash'],'paragraph_index':paragraph,'unit_index':unit}

    def reference(self,**extra):
        return {**self.source(),'mime':'audio/wav','audio_base64':base64.b64encode(wave()).decode(),**extra}

    def make_set(self,article_id=1,source='source',questions=None):
        article=next(a for a in self.call('/reading/catalog')['articles'] if a['id']==article_id)
        return self.call('/reading/sets','POST',{'article_id':article_id,'article_hash':article['hash'],'source':source,**({'questions':questions} if questions else {})},201)['set']

    def start(self,set_ids=None,**extra):
        set_ids=set_ids or [self.make_set()['id']]
        return self.call('/reading/sessions','POST',{'request_id':self.serial(),'kind':'practice','set_ids':set_ids,'time_limit_seconds':600,**extra},201)['session']

    def sql(self,query,args=()):
        with self.app.app_context():
            db=get_db();result=[dict(r) for r in db.execute(query,args).fetchall()];db.commit();return result

    def correct_answers(self,identity):
        row=self.sql('SELECT snapshot_json FROM reading_sessions WHERE id=?',(identity,))[0]
        return {q['id']:q['answer'] for q in json.loads(row['snapshot_json'])['questions']}

    def submit(self,session,answers):
        return self.call(f"/reading/sessions/{session['id']}/answers",'POST',{'version':session['version'],'answers':answers})['session']

    def test_assets_and_practice_navigation(self):
        page=self.client.get('/').get_data(as_text=True)
        self.assertIn('href="#listening"',page);self.assertIn('href="#exercises"',page)
        for asset in ('listening.js','exercises.js','practice.css'):
            with self.client.get('/static/'+asset) as response:self.assertEqual(response.status_code,200)
        self.assertIn("media-src 'self' blob:",self.client.get('/').headers['Content-Security-Policy'])

    def test_manifest_exact_spans_keywords_and_invalid_bounds(self):
        manifest=self.call('/training/articles/1')
        paragraph=self.call('/articles/1')['content'].split('\n\n')[0]
        for unit in manifest['units']:
            self.assertEqual(paragraph[unit['start']:unit['end']],unit['text'])
            self.assertEqual(len(unit['keyword_parts']),unit['gap_count']+1)
        for query in ('-1','bad','999'):
            self.call('/training/articles/1?paragraph='+query,status=400)
        self.call('/training/articles/999',status=404)

    def test_sentence_boundaries_abbreviations_decimals_and_long_units(self):
        text='Dr. Lee paid 3.5 dollars. Then he left!'
        self.assertEqual([u['text'] for u in units(text)],['Dr. Lee paid 3.5 dollars.','Then he left!'])
        long=' '.join(['journey']*400)
        self.assertTrue(all(len(u['text'])<=600 for u in units(long)))
        self.assertEqual(' '.join(u['text'] for u in units(long)),long)

    def test_word_comparison_exposes_real_insertions_deletions_and_case(self):
        result=compare("The reader’s quiet journey.","THE reader's journey today")
        self.assertEqual(result['expected_words'],4);self.assertEqual(result['matched_words'],3)
        self.assertEqual(result['word_errors'],2)
        self.assertIn('delete',[o['kind'] for o in result['operations']]);self.assertIn('insert',[o['kind'] for o in result['operations']])
        self.assertEqual(compare('Hello, world!','hello world')['accuracy'],100)

    def test_dictation_persists_and_retry_is_idempotent(self):
        payload={**self.source(),'mode':'dictation','attempt':'At a station after sunset.','request_id':self.serial(),'duration_ms':12000}
        first=self.call('/training/attempts','POST',payload,201);again=self.call('/training/attempts','POST',payload)
        self.assertTrue(again['duplicate']);self.assertEqual(first['attempt'],again['attempt'])
        self.assertEqual(len(self.call('/training/attempts')['attempts']),1)
        self.call('/training/attempts','POST',{**payload,'attempt':'another answer'},409)
        restarted=create_app({'TESTING':True,'DATABASE':self.path}).test_client()
        self.assertEqual(restarted.get('/api/training/attempts').json['attempts'][0]['attempt'],payload['attempt'])

    def test_keywords_are_derived_from_source_not_client_provided_answer(self):
        manifest=self.call('/training/articles/1');selected=manifest['units'][0]
        import newsreader.training as training
        expected=training.keywords(selected['text'])['answers']
        result=self.call('/training/attempts','POST',{**self.source(),'mode':'keywords','answers':expected,'request_id':self.serial()},201)
        self.assertEqual(result['attempt']['feedback']['accuracy'],100)
        self.call('/training/attempts','POST',{**self.source(),'mode':'keywords','answers':[],'request_id':self.serial()},400)

    def test_recording_can_be_saved_without_recognition_or_fake_pronunciation_score(self):
        payload={**self.reference(),'mode':'readalong','attempt':'','request_id':self.serial(),'duration_ms':1000}
        result=self.call('/training/attempts','POST',payload,201)['attempt']
        self.assertFalse(result['feedback']['available']);self.assertIsNone(result['feedback']['accuracy'])
        self.assertIsNone(result['feedback']['speech_rate']);self.assertNotIn('pronunciation_score',result['feedback'])
        media=self.client.get(f"/api/training/attempts/{result['id']}/audio")
        self.assertEqual(media.data,wave());self.assertEqual(media.mimetype,'audio/wav')
        self.assertEqual(media.headers['X-Content-Type-Options'],'nosniff')

    def test_shadow_feedback_uses_supplied_voice_metrics_transparently(self):
        target=self.call('/training/articles/1')['units'][0]['text']
        result=self.call('/training/attempts','POST',{**self.reference(),'mode':'shadow','attempt':target,'request_id':self.serial(),'duration_ms':10000,'voiced_ms':6000,'pause_count':2,'analysis_available':True},201)['attempt']
        self.assertEqual(result['feedback']['accuracy'],100);self.assertEqual(result['feedback']['pause_ratio'],.4)
        self.assertEqual(result['feedback']['pause_count'],2);self.assertGreater(result['feedback']['speech_rate'],0)

    def test_missing_analysis_is_not_reported_as_silence_and_short_clips_have_no_rate(self):
        no_analysis=self.call('/training/attempts','POST',{**self.reference(),'mode':'readalong','request_id':self.serial(),'duration_ms':5000,'attempt':'A quiet journey.'},201)['attempt']['feedback']
        self.assertIsNone(no_analysis['pause_ratio'])
        short=self.call('/training/attempts','POST',{**self.reference(),'mode':'readalong','request_id':self.serial(),'duration_ms':500,'voiced_ms':400,'analysis_available':True,'attempt':'A quiet journey.'},201)['attempt']['feedback']
        self.assertIsNone(short['speech_rate'])
        self.assertEqual(short['pause_ratio'],.2)

    def test_reference_upload_versioning_and_content_binding(self):
        payload=self.reference();first=self.call('/training/reference','POST',payload,201)['reference']
        self.call('/training/reference','POST',payload,409)
        updated=self.call('/training/reference','POST',{**payload,'version':first['version']},201)['reference']
        self.assertEqual(updated['id'],first['id']);self.assertEqual(updated['version'],2)
        self.assertEqual(self.call('/training/articles/1')['units'][0]['reference']['id'],first['id'])
        self.assertEqual(self.client.get('/api/training/audio/'+str(first['id'])).data,wave())
        self.call('/training/reference','POST',{**payload,'article_hash':'outdated'},409)

    def test_invalid_audio_and_metrics_do_not_create_records(self):
        base={**self.reference(),'mode':'readalong','request_id':self.serial(),'duration_ms':5000}
        for extra in ({'mime':[]},{'mime':'text/html'},{'audio_base64':'invalid!'}, {'audio_base64':base64.b64encode(b'<script>'+b'x'*40).decode()}, {'voiced_ms':5001},{'duration_ms':True},{'pause_count':-1},{'mode':'unknown'},{'unit_index':999}):
            self.call('/training/attempts','POST',{**base,**extra},400)
        self.assertEqual(self.call('/training/attempts')['attempts'],[])
        self.call('/training/attempts','POST',{**self.source(),'mode':'dictation','attempt':'','request_id':self.serial()},400)
        self.call('/training/attempts','POST',{**self.source(),'mode':'shadow','request_id':self.serial()},400)

    def test_media_limit_override_accepts_more_than_default_512kb(self):
        raw=wave()+b'\0'*(600*1024)
        payload={**self.reference(),'audio_base64':base64.b64encode(raw).decode()}
        self.call('/training/reference','POST',payload,201)
        too_big=wave()+b'\0'*(6*1024*1024)
        self.call('/training/reference','POST',{**payload,'audio_base64':base64.b64encode(too_big).decode()},400)

    def test_article_deletion_preserves_attempt_audio_and_receipt(self):
        payload={**self.reference(),'mode':'readalong','request_id':self.serial()}
        result=self.call('/training/attempts','POST',payload,201)['attempt']
        self.call('/articles/1','DELETE',{})
        history=self.call('/training/attempts')['attempts'][0]
        self.assertIsNone(history['article_id']);self.assertEqual(history['source_text'],result['source_text'])
        self.assertEqual(self.client.get(f"/api/training/attempts/{result['id']}/audio").data,wave())
        self.assertTrue(self.call('/training/attempts','POST',payload)['duplicate'])

    def test_all_curated_articles_cover_eight_types_and_cache_sets(self):
        for article in self.call('/reading/catalog')['articles']:
            self.assertTrue(article['curated']);result=self.make_set(article['id'])
            self.assertEqual(set(result['types']),set(TYPES));self.assertEqual(result['count'],8)
            self.assertEqual(self.make_set(article['id'])['id'],result['id'])
        self.assertEqual(len(self.sql('SELECT id FROM reading_sets')),5)

    def test_imported_material_gets_only_truthful_source_tasks_offline(self):
        article=self.call('/articles','POST',{'title':'New material','content':'A meaningful journey begins with quiet observation. The reader makes careful notes about the world. Many useful questions follow.\n\nThe community builds a small reading garden where people share ideas and books. They learn from every visitor. They remember these stories together.'},201)
        result=self.make_set(article['id']);self.assertEqual(result['source'],'offline')
        self.assertEqual(set(result['types']),{'cloze','paragraph','order'})

    def test_active_session_never_reveals_correct_answers_or_explanations(self):
        session=self.start()
        self.assertIsNone(session['result']);self.assertEqual(session['answers'],{})
        for q in session['questions']:
            self.assertNotIn('answer',q);self.assertNotIn('explanation',q);self.assertNotIn('evidence',q)
        self.assertEqual(len(session['questions']),8)
        self.assertTrue(session['materials'][0]['available'])

    def test_sessions_shuffle_options_without_changing_scoring(self):
        session=self.start();expected=self.correct_answers(session['id'])
        finished=self.submit(session,expected)
        self.assertEqual(finished['result']['score'],100);self.assertEqual(finished['result']['correct'],8)
        self.assertEqual(set(finished['result']['by_type']),set(TYPES))
        self.assertTrue(all(q['answer'] for q in finished['questions']))
        self.assertEqual(self.call('/reading/mistakes')['mistakes'],[])

    def test_start_receipt_cannot_be_reused_for_different_settings(self):
        identity=self.make_set()['id'];payload={'request_id':self.serial(),'set_ids':[identity],'time_limit_seconds':600}
        a=self.call('/reading/sessions','POST',payload,201);b=self.call('/reading/sessions','POST',payload)
        self.assertEqual(a['session']['id'],b['session']['id']);self.assertTrue(b['duplicate'])
        self.call('/reading/sessions','POST',{**payload,'time_limit_seconds':10},409)

    def test_drafts_persist_and_conflicting_saves_preserve_server_answers(self):
        session=self.start();answer=self.correct_answers(session['id']);one={next(iter(answer)):next(iter(answer.values()))}
        saved=self.call(f"/reading/sessions/{session['id']}/answers",'PATCH',{'version':session['version'],'answers':one})['session']
        self.assertEqual(saved['answers'],one)
        response=self.call(f"/reading/sessions/{session['id']}/answers",'PATCH',{'version':session['version'],'answers':{}},409)
        self.assertEqual(response['current']['answers'],one)
        restarted=create_app({'TESTING':True,'DATABASE':self.path}).test_client()
        self.assertEqual(restarted.get(f"/api/reading/sessions/{session['id']}").json['session']['answers'],one)

    def test_invalid_answers_are_atomic_and_unrelated_question_ids_rejected(self):
        session=self.start();path=f"/reading/sessions/{session['id']}/answers"
        for answers in ({'other':['0']},{session['questions'][0]['id']:7},{session['questions'][0]['id']:[True]}):
            self.call(path,'PATCH',{'version':session['version'],'answers':answers},400)
        self.assertEqual(self.call('/reading/sessions/'+str(session['id']))['session']['answers'],{})
        order=next(q for q in session['questions'] if q['type']=='order')
        self.call(path,'POST',{'version':session['version'],'answers':{order['id']:['0','0','1']}},400)
        self.assertEqual(self.call('/reading/sessions/'+str(session['id']))['session']['status'],'active')

    def test_submitting_twice_does_not_duplicate_mistakes_or_history(self):
        session=self.start();first=self.submit(session,{})
        second=self.submit(session,self.correct_answers(session['id']))
        self.assertEqual(first,second);self.assertEqual(first['result']['score'],0)
        self.assertEqual(len(self.call('/reading/mistakes')['mistakes']),8)
        self.assertTrue(all(m['wrong_count']==1 for m in self.call('/reading/mistakes')['mistakes']))
        self.assertEqual(len(self.call('/reading/history')['sessions']),1)

    def test_deadline_grades_saved_draft_and_ignores_late_answers(self):
        session=self.start(time_limit_seconds=10);answers=self.correct_answers(session['id']);question=next(iter(answers));partial={question:answers[question]}
        saved=self.call(f"/reading/sessions/{session['id']}/answers",'PATCH',{'version':1,'answers':partial})['session']
        self.current+=timedelta(seconds=10)
        result=self.submit(saved,answers)
        self.assertTrue(result['result']['timed_out']);self.assertEqual(result['result']['correct'],1)
        self.assertEqual(result['result']['elapsed_seconds'],10)
        self.assertEqual(result['answers'],partial)

    def test_history_finalizes_expired_sessions_once(self):
        self.start(time_limit_seconds=1);self.current+=timedelta(seconds=5)
        for _ in range(2):history=self.call('/reading/history')
        self.assertEqual(history['sessions'][0]['status'],'completed')
        self.assertEqual(len(self.call('/reading/mistakes')['mistakes']),8)
        self.assertTrue(all(m['wrong_count']==1 for m in self.call('/reading/mistakes')['mistakes']))
        self.assertTrue(all(m['reason']=='time' for m in self.call('/reading/mistakes')['mistakes']))

    def test_no_time_limit_does_not_expire_when_reopened(self):
        session=self.start(time_limit_seconds=0);self.current+=timedelta(days=3)
        self.assertEqual(self.call('/reading/sessions/'+str(session['id']))['session']['status'],'active')

    def test_all_answers_including_cloze_case_and_punctuation_are_scored(self):
        session=self.start();answers=self.correct_answers(session['id'])
        for q in session['questions']:
            if q['type']=='cloze':answers[q['id']]=[a.upper()+' ' for a in answers[q['id']]]
        result=self.submit(session,answers);self.assertEqual(result['result']['correct'],8)

    def test_mistake_classification_is_versioned_and_atomic(self):
        self.submit(self.start(),{});m=self.call('/reading/mistakes')['mistakes'][0]
        path='/reading/mistakes/'+str(m['id']);payload={'version':m['version'],'reason':'vocabulary','note':'记住搭配','resolved':True}
        result=self.call(path,'PATCH',payload);self.assertEqual(result['version'],m['version']+1)
        self.call(path,'PATCH',payload,409)
        found=next(item for item in self.call('/reading/mistakes')['mistakes'] if item['id']==m['id'])
        self.assertEqual((found['reason'],found['note'],found['resolved']),('vocabulary','记住搭配',1))
        self.call(path,'PATCH',{'version':found['version'],'reason':'detail','resolved':'yes'},400)
        self.assertEqual(next(item for item in self.call('/reading/mistakes')['mistakes'] if item['id']==m['id'])['reason'],'vocabulary')

    def test_mistake_repractice_correct_resolves_original_even_with_new_option_order(self):
        self.submit(self.start(),{});original=self.call('/reading/mistakes')['mistakes'];ids=[m['id'] for m in original]
        reviewed=self.call('/reading/mistakes/practice','POST',{'ids':ids,'request_id':self.serial()},201)['session']
        finished=self.submit(reviewed,self.correct_answers(reviewed['id']))
        self.assertEqual(finished['result']['score'],100)
        self.assertTrue(all(m['resolved'] for m in self.call('/reading/mistakes')['mistakes']))
        self.assertTrue(all(m['attempt_count']==2 for m in self.call('/reading/mistakes')['mistakes']))
        self.assertEqual(len(self.call('/reading/mistakes')['mistakes']),8)

    def test_wrong_repractice_reopens_without_discarding_reason_and_note(self):
        self.submit(self.start(),{});m=self.call('/reading/mistakes')['mistakes'][0]
        self.call('/reading/mistakes/'+str(m['id']),'PATCH',{'version':m['version'],'note':'自己的反思','reason':'grammar','resolved':True})
        reviewed=self.call('/reading/mistakes/practice','POST',{'ids':[m['id']],'request_id':self.serial()},201)['session'];self.submit(reviewed,{})
        found=next(item for item in self.call('/reading/mistakes')['mistakes'] if item['id']==m['id'])
        self.assertEqual((found['resolved'],found['wrong_count'],found['reason'],found['note']),(0,2,'grammar','自己的反思'))

    def test_assessment_combines_articles_and_reports_by_type(self):
        sets=[self.make_set(i)['id'] for i in (1,2)]
        session=self.start(sets,kind='assessment');self.assertEqual(len(session['materials']),2);self.assertEqual(len(session['questions']),16)
        result=self.submit(session,self.correct_answers(session['id']));self.assertEqual(result['result']['score'],100)
        self.assertTrue(all(stats['total']==2 for stats in result['result']['by_type'].values()))
        self.assertEqual(self.call('/reading/history')['sessions'][0]['kind'],'assessment')

    def test_deleted_article_does_not_destroy_scores_mistakes_or_practice_snapshots(self):
        session=self.start();self.submit(session,{})
        self.call('/articles/1','DELETE',{})
        found=self.call('/reading/sessions/'+str(session['id']))['session']
        self.assertFalse(found['materials'][0]['available']);self.assertEqual(found['result']['total'],8)
        ids=[m['id'] for m in self.call('/reading/mistakes')['mistakes']]
        reviewed=self.call('/reading/mistakes/practice','POST',{'ids':ids[:1],'request_id':self.serial()},201)['session']
        self.assertEqual(reviewed['kind'],'mistakes');self.assertFalse(reviewed['materials'][0]['available'])

    def test_new_sessions_reject_changed_or_deleted_question_set_sources(self):
        set_id=self.make_set()['id'];self.sql('UPDATE articles SET content=content || ? WHERE id=1',(' New text.',))
        self.call('/reading/sessions','POST',{'set_ids':[set_id],'request_id':self.serial()},409)
        self.call('/articles/1','DELETE',{})
        self.call('/reading/sessions','POST',{'set_ids':[set_id],'request_id':self.serial()},409)

    def test_manual_questions_validate_evidence_order_and_cloze_restoration(self):
        base=copy.deepcopy(BANK['articles'][0]['questions'])
        result=self.make_set(source='manual',questions=base);self.assertEqual(result['source'],'manual')
        article=next(a for a in self.call('/reading/catalog')['articles'] if a['id']==1)
        for patcher in (lambda qs:qs[0].update(evidence='invented quotation'),lambda qs:qs[-1].update(answer=['2','1','0']),lambda qs:qs[3].update(answer=['wrongword']),lambda qs:qs[0].update(answer=['100'])):
            questions=copy.deepcopy(base);patcher(questions)
            self.call('/reading/sets','POST',{'article_id':1,'article_hash':article['hash'],'source':'manual','questions':questions},400)
        self.assertEqual(len(self.sql('SELECT id FROM reading_sets')),1)

    def test_ai_generation_is_explicit_cached_and_uses_source_only(self):
        with patch('newsreader.ai.request_json',return_value={'questions':BANK['articles'][0]['questions']}) as network:
            result=self.make_set(source='ai');again=self.make_set(source='ai')
        self.assertEqual(result['id'],again['id']);self.assertEqual(network.call_count,1)
        payload=network.call_args.args[0];self.assertFalse(payload['store']);self.assertEqual(payload['text']['format']['name'],'margin_reading_exercises')
        self.assertEqual(json.loads(payload['input'])['paragraphs'],self.call('/articles/1')['content'].split('\n\n'))
        self.assertNotIn('synthetic-only-key',json.dumps(self.call('/reading/catalog')))

    def test_unconfigured_ai_and_invalid_model_output_never_create_fake_tests(self):
        self.app.config['AI_API_KEY']='';article=next(a for a in self.call('/reading/catalog')['articles'] if a['id']==1)
        payload={'article_id':1,'article_hash':article['hash'],'source':'ai'}
        with patch('newsreader.ai.request_json') as network:self.call('/reading/sets','POST',payload,503)
        network.assert_not_called();self.app.config['AI_API_KEY']='synthetic-only-key'
        malformed=copy.deepcopy(BANK['articles'][0]['questions']);malformed[0]['evidence']='not in this article'
        with patch('newsreader.ai.request_json',return_value={'questions':malformed}):self.call('/reading/sets','POST',payload,502)
        self.assertEqual(self.sql('SELECT id FROM reading_sets'),[])

    def test_late_ai_response_cannot_attach_to_changed_source(self):
        article=next(a for a in self.call('/reading/catalog')['articles'] if a['id']==1)
        def delayed(*args):
            self.sql('UPDATE articles SET content=content || ? WHERE id=1',(' Changed.',))
            return {'questions':BANK['articles'][0]['questions']}
        with patch('newsreader.ai.request_json',side_effect=delayed):self.call('/reading/sets','POST',{'article_id':1,'article_hash':article['hash'],'source':'ai'},409)
        self.assertEqual(self.sql('SELECT id FROM reading_sets'),[])

    def test_input_bounds_reject_booleans_duplicates_and_unknown_types(self):
        set_id=self.make_set()['id']
        for extra in ({'time_limit_seconds':True},{'time_limit_seconds':-1},{'set_ids':[set_id,set_id]},{'set_ids':[True]},{'kind':'assessment'},{'kind':'other'}):
            self.call('/reading/sessions','POST',{'set_ids':[set_id],'request_id':self.serial(),**extra},400)
        self.call('/reading/mistakes/practice','POST',{'ids':[True],'request_id':self.serial()},400)
        self.assertEqual(self.sql('SELECT id FROM reading_sessions'),[])

    def test_new_schema_upgrade_is_idempotent_and_keeps_existing_srs(self):
        word=self.call('/wordbook','POST',{'word':'journey','meaning':'旅程'},201)['entry']
        schedule=word['schedule'];self.start()
        for _ in range(2):create_app({'TESTING':True,'DATABASE':self.path})
        self.assertEqual(self.call('/wordbook/'+str(word['id']))['entry']['schedule'],schedule)
        self.assertEqual(len(self.call('/reading/history')['sessions']),1)


if __name__=='__main__':unittest.main()
