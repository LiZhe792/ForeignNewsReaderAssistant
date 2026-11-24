"""Integration checks use real SQLite, with controlled dates and no external calls."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app import create_app
from newsreader.db import get_db, CHINA_TZ
from newsreader.personalization import DEFAULTS


class PersonalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'personal.sqlite3')
        self.app = create_app({'TESTING': True, 'DATABASE': self.path})
        self.client = self.app.test_client(); self.serial = 0
        self.current = datetime(2026, 11, 22, 10, tzinfo=CHINA_TZ)
        for target in ('newsreader.personalization.clock', 'newsreader.exercises.clock'):
            mock = patch(target, side_effect=lambda: self.current); mock.start(); self.addCleanup(mock.stop)

    def call(self, path='', method='GET', data=None, status=200):
        response = self.client.open('/api/personalization' + path, method=method, json=data)
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        return response.json

    def sql(self, query, args=()):
        with self.app.app_context():
            db = get_db(); rows = [dict(r) for r in db.execute(query,args).fetchall()]; db.commit(); return rows

    def article(self, content='The city has a small park. ' * 10, **extra):
        response = self.client.post('/api/articles', json={'title':'Personal article', 'content':content, 'category':'生活', **extra})
        self.assertEqual(response.status_code, 201, response.json); return response.json['id']

    def profile(self, **changes):
        return self.call('/profile', 'PATCH', {'version':self.call('/profile')['version'], **changes})

    def feedback(self, identity=1, **changes):
        info = self.call('/articles/' + str(identity))
        return self.call(f'/articles/{identity}/feedback', 'PUT', {'article_hash':info['analysis']['hash'], 'version':info['feedback']['version'], **changes})

    def known(self, word, mastery='mastered'):
        response=self.client.post('/api/vocabulary', json={'word':word,'meaning':'释义'})
        self.assertIn(response.status_code, (200,201)); identity=response.json['id']
        self.client.patch('/api/vocabulary/'+str(identity),json={'mastery':mastery})
        return identity

    def quiz(self, identity=1, correct=True, source='source', completed=True, blank=False):
        self.serial += 1
        row=self.sql('SELECT content FROM articles WHERE id=?',(identity,))[0]
        from newsreader.personalization import digest
        made=self.client.post('/api/reading/sets',json={'article_id':identity,'article_hash':digest(row['content']),'source':'source'})
        self.assertEqual(made.status_code,201,made.json)
        sid=made.json['set']['id']
        if source!='source': self.sql('UPDATE reading_sets SET source=? WHERE id=?',(source,sid))
        started=self.client.post('/api/reading/sessions',json={'set_ids':[sid],'request_id':f'personal_quiz_{self.serial}','kind':'practice','time_limit_seconds':600})
        self.assertEqual(started.status_code,201,started.json); session=started.json['session']
        qs=json.loads(self.sql('SELECT snapshot_json FROM reading_sessions WHERE id=?',(session['id'],))[0]['snapshot_json'])['questions']
        answers={} if blank else {q['id']:q['answer'] if correct else ([] if q['type']=='order' else ['wrong'] if q['type']=='cloze' else [str((int(q['answer'][0])+1)%len(q['options']))]) for q in qs}
        if completed:
            finished=self.client.post(f"/api/reading/sessions/{session['id']}/answers",json={'version':session['version'],'answers':answers})
            self.assertEqual(finished.status_code,200,finished.json)
        return session['id']

    def test_cold_start_is_explicit_and_recommendations_are_local(self):
        profile=self.call('/profile'); self.assertFalse(profile['configured']);self.assertEqual(profile['preferences'],DEFAULTS)
        result=self.call('/recommendations'); self.assertEqual(len(result['articles']),5)
        self.assertEqual(result['evidence']['articles'],0); self.assertEqual(result['evidence']['adjustment'],0)
        ids={r['id'] for r in self.sql('SELECT id FROM articles')}
        self.assertTrue({a['id'] for a in result['articles']} <= ids)
        for article in result['articles']:
            self.assertNotIn('content',article); self.assertTrue(article['reasons'])
            self.assertEqual(article['analysis']['confirmed_mastered_percent'],0)

    def test_profile_round_trip_and_restart(self):
        saved=self.profile(level='C1',interests=['环境','生活'],minutes=8,speed=120,challenge='gentle',adaptive=False,exclude_read=False)
        self.assertEqual(saved['version'],1);self.assertEqual(saved['preferences']['level'],'C1')
        restarted=create_app({'TESTING':True,'DATABASE':self.path}).test_client()
        self.assertEqual(restarted.get('/api/personalization/profile').json,saved)

    def test_profile_rejects_invalid_values_without_partial_write(self):
        for change in ({'level':'A1'},{'level':[]},{'minutes':True},{'minutes':1},{'minutes':91},{'speed':401},{'speed':60.1},{'interests':['科学','科学']},{'interests':[{}]},{'interests':'生活'},{'adaptive':1},{'exclude_read':'true'},{'challenge':'anything'},{'extra':1}):
            self.call('/profile','PATCH',{'version':0,**change},400)
        self.assertEqual(self.call('/profile')['version'],0)

    def test_profile_requires_object_and_version(self):
        for data in ([],None,{}, {'version':True},{'version':-1}):
            self.call('/profile','PATCH',data,400 if data is not None else 415)

    def test_stale_profile_does_not_overwrite_other_page(self):
        self.profile(level='B2')
        self.call('/profile','PATCH',{'version':0,'level':'C2'},409)
        self.assertEqual(self.call('/profile')['preferences']['level'],'B2')

    def test_reset_retains_data_and_monotonic_version(self):
        self.profile(level='C2'); self.feedback(difficulty='hard')
        reset=self.call('/profile','DELETE',{'version':1})
        self.assertEqual(reset['preferences'],DEFAULTS);self.assertEqual(reset['version'],2)
        self.call('/profile','PATCH',{'version':0,'level':'C2'},409)
        self.assertEqual(len(self.call('/feedback')['articles']),1)
        self.assertEqual(len(self.sql('SELECT id FROM articles')),5)

    def test_feedback_is_reversible_and_dimensions_independent(self):
        first=self.feedback(difficulty='hard'); second=self.feedback(interest='less')
        self.assertEqual(second['feedback']['difficulty'],'hard')
        self.assertEqual(second['feedback']['version'],2)
        self.assertNotIn(1,[a['id'] for a in self.call('/recommendations')['articles']])
        self.assertIn(1,[a['id'] for a in self.call('/feedback')['articles']])
        restored=self.feedback(difficulty='',interest='neutral')
        self.assertEqual(restored['feedback']['version'],3)
        self.assertIn(1,[a['id'] for a in self.call('/recommendations')['articles']])
        self.assertEqual(self.call('/feedback')['articles'],[])

    def test_stale_feedback_hash_and_version_are_rejected(self):
        original=self.call('/articles/1'); self.feedback(difficulty='easy')
        data={'article_hash':original['analysis']['hash'],'version':0,'interest':'less'}
        self.call('/articles/1/feedback','PUT',data,409)
        self.sql("UPDATE articles SET content=content||' New content.' WHERE id=1")
        self.call('/articles/1/feedback','PUT',{**data,'version':1},409)
        fresh=self.call('/articles/1');self.assertEqual(fresh['feedback']['version'],0)
        self.assertEqual(self.call('/feedback')['articles'],[])
        self.feedback(interest='more')

    def test_feedback_validation_and_missing_article(self):
        record=self.call('/articles/1')
        for change in ({'difficulty':'bad'},{'interest':False},{'other':'easy'},{}):
            self.call('/articles/1/feedback','PUT',{'article_hash':record['analysis']['hash'],'version':0,**change},400)
        self.call('/articles/999999',status=404)
        self.call('/articles/999999/feedback','PUT',{'version':0,'interest':'more'},404)

    def test_feedback_deleted_with_article(self):
        self.feedback(difficulty='hard')
        self.client.delete('/api/articles/1',json={})
        self.assertEqual(self.call('/feedback')['articles'],[])
        self.assertEqual(self.sql('SELECT * FROM article_feedback'),[])

    def test_filters_and_limits(self):
        result=self.call('/recommendations?category=科技&limit=1')
        self.assertTrue(all(a['category']=='科技' for a in result['articles']));self.assertLessEqual(len(result['articles']),1)
        for query in ('category=不存在','limit=0','limit=51','limit=1.5','include_read=2','include_read=true','max_minutes=-1','extra=1'):
            self.call('/recommendations?'+query,status=400)

    def test_read_articles_are_optional_and_feedback_can_restore_them(self):
        self.sql('UPDATE articles SET progress=100 WHERE id=1')
        self.assertNotIn(1,[a['id'] for a in self.call('/recommendations')['articles']])
        self.assertIn(1,[a['id'] for a in self.call('/recommendations?include_read=1')['articles']])
        self.profile(exclude_read=False)
        self.assertIn(1,[a['id'] for a in self.call('/recommendations')['articles']])

    def test_interest_rank_and_stable_ties(self):
        first=self.article(title='First identical',category='生活'); second=self.article(title='Second identical',category='科技')
        initial=self.call('/recommendations?limit=50')['articles']; ids=[a['id'] for a in initial]
        self.assertLess(ids.index(first),ids.index(second))
        self.profile(interests=['科技'])
        ids=[a['id'] for a in self.call('/recommendations?limit=50')['articles']]
        self.assertLess(ids.index(second),ids.index(first))

    def test_time_budget_speed_and_hard_filter(self):
        identity=self.article('The city has a small park. '*100)
        self.profile(speed=60,minutes=2)
        article=self.call(f'/articles/{identity}'); self.assertEqual(article['analysis']['minutes'],10)
        self.assertNotIn(identity,[a['id'] for a in self.call('/recommendations?max_minutes=5')['articles']])
        self.profile(speed=300)
        self.assertEqual(self.call(f'/articles/{identity}')['analysis']['minutes'],2)

    def test_confirmed_coverage_and_attested_inflections(self):
        identity=self.article('Studies studies study news new saw see. '*6)
        self.known('study'); self.known('new');self.known('see')
        m=self.call(f'/articles/{identity}')['analysis']
        self.assertEqual(m['vocabulary']['mastered'],30) # studies x2, study, new, see
        self.assertEqual(m['word_count'],42)
        self.assertEqual(sum(m['vocabulary'].values()),42)
        self.assertIn('词库未覆盖不等于生词',m['method'])

    def test_saved_new_words_and_phrases_are_not_assumed_mastered(self):
        identity=self.article('Take into account the city and the park. '*6)
        self.known('take into account');self.known('city','new')
        m=self.call(f'/articles/{identity}')['analysis']
        self.assertEqual(m['vocabulary']['mastered'],0);self.assertEqual(m['vocabulary']['learning'],6)

    def test_stable_srs_and_relearning_affect_estimate(self):
        identity=self.article('The city has a small park. '*10); word=self.known('city','learning')
        self.sql("UPDATE srs_cards SET state='review',repetitions=3,interval_days=8 WHERE word_id=?",(word,))
        before=self.call(f'/articles/{identity}')['analysis']
        self.assertEqual(before['vocabulary']['stable_review'],10);self.assertEqual(before['confirmed_mastered_percent'],0)
        self.sql("UPDATE srs_cards SET state='relearning' WHERE word_id=?",(word,))
        after=self.call(f'/articles/{identity}')['analysis']
        self.assertGreater(after['estimated_load_per_100'],before['estimated_load_per_100'])

    def test_function_words_are_not_unlisted(self):
        identity=self.article('We are in the city and cannot leave. '*6)
        m=self.call(f'/articles/{identity}')['analysis']
        self.assertNotIn('are',[w['word'] for w in m['preview']])
        self.assertNotIn('cannot',[w['word'] for w in m['preview']])

    def test_dictionary_misses_remain_uncertain(self):
        identity=self.article('Quuxblargh ' * 40)
        m=self.call(f'/articles/{identity}')['analysis']
        self.assertEqual(m['vocabulary']['unlisted'],40);self.assertEqual(m['vocabulary']['learning'],0)
        self.assertEqual(m['preview'][0]['basis'],'词库未覆盖，待判断')

    def test_three_distinct_feedback_materials_adjust_but_do_not_relabel_profile(self):
        self.feedback(1,difficulty='hard');self.feedback(2,difficulty='hard')
        self.assertEqual(self.call('/profile')['evidence']['adjustment'],0)
        self.feedback(3,difficulty='hard')
        result=self.call('/profile');self.assertEqual(result['evidence']['articles'],3)
        self.assertEqual(result['evidence']['adjustment'],-.6);self.assertEqual(result['preferences']['level'],'B1')
        self.profile(adaptive=False)
        self.assertEqual(self.call('/profile')['evidence']['adjustment'],0)

    def test_duplicate_materials_do_not_inflate_feedback(self):
        content=self.sql('SELECT content FROM articles WHERE id=1')[0]['content']; other=self.article(content)
        self.feedback(1,difficulty='easy');self.feedback(other,difficulty='easy')
        self.assertEqual(self.call('/profile')['evidence']['articles'],1)

    def test_interest_does_not_refresh_old_difficulty_evidence(self):
        self.feedback(difficulty='hard');self.current+=timedelta(days=91)
        self.feedback(interest='more')
        self.assertEqual(self.call('/profile')['evidence']['articles'],0)
        self.feedback(difficulty='hard')
        self.assertEqual(self.call('/profile')['evidence']['articles'],1)

    def test_first_curated_attempt_only_and_no_active_quiz_side_effects(self):
        active=self.quiz(1,completed=False)
        self.call('/recommendations')
        self.assertEqual(self.sql('SELECT status FROM reading_sessions WHERE id=?',(active,))[0]['status'],'active')
        self.quiz(1,correct=False);self.quiz(1,correct=True);self.quiz(2);self.quiz(3)
        evidence=self.call('/profile')['evidence']
        self.assertEqual(evidence['articles'],3);self.assertEqual(evidence['quiz_articles'],3)
        self.assertEqual(evidence['adjustment'],.2)

    def test_offline_blank_or_unverified_quizzes_do_not_adjust(self):
        self.quiz(1,blank=True); self.quiz(2,source='ai'); self.quiz(3,source='manual')
        self.assertEqual(self.call('/profile')['evidence']['articles'],0)

    def test_feedback_and_quiz_same_material_count_once(self):
        self.quiz(1);self.feedback(1,difficulty='hard');self.feedback(2,difficulty='hard');self.feedback(3,difficulty='hard')
        evidence=self.call('/profile')['evidence']
        self.assertEqual(evidence['articles'],3);self.assertEqual(evidence['adjustment'],-.6)

    def test_reading_and_mastery_are_unchanged_by_recommendations(self):
        self.known('city','learning')
        articles=self.sql('SELECT * FROM articles'); vocabulary=self.sql('SELECT * FROM vocabulary'); cards=self.sql('SELECT * FROM srs_cards')
        self.call('/recommendations');self.call('/articles/1');self.feedback(difficulty='easy');self.profile(level='C2')
        self.assertEqual(self.sql('SELECT * FROM articles'),articles)
        self.assertEqual(self.sql('SELECT * FROM vocabulary'),vocabulary)
        self.assertEqual(self.sql('SELECT * FROM srs_cards'),cards)

    def test_explicit_aliases_count_after_word_form_merge(self):
        identity=self.article('The cities have a small park. '*10); word=self.known('city')
        self.sql('INSERT INTO word_aliases(word_id,alias) VALUES(?,?)',(word,'cities'))
        self.assertEqual(self.call(f'/articles/{identity}')['analysis']['vocabulary']['mastered'],10)

    def test_all_read_and_all_hidden_pools_remain_honest(self):
        self.sql('UPDATE articles SET progress=100')
        result=self.call('/recommendations');self.assertEqual(result['articles'],[]);self.assertEqual(result['matched'],0)
        self.assertEqual(len(self.call('/recommendations?include_read=1')['articles']),5)
        for identity in range(1,6): self.feedback(identity,interest='less')
        result=self.call('/recommendations?include_read=1')
        self.assertEqual(result['articles'],[]);self.assertEqual(result['suppressed'],5)
        self.assertEqual(len(self.call('/feedback')['articles']),5)

    def test_expired_active_sessions_are_not_finalized_by_recommendations(self):
        identity=self.quiz(1,completed=False); self.current+=timedelta(hours=1)
        self.call('/recommendations');self.call('/profile')
        self.assertEqual(self.sql('SELECT status FROM reading_sessions WHERE id=?',(identity,))[0]['status'],'active')

    def test_incomplete_semantic_answers_and_mistake_drills_are_excluded(self):
        identity=self.quiz(1,completed=False)
        snapshot=json.loads(self.sql('SELECT snapshot_json FROM reading_sessions WHERE id=?',(identity,))[0]['snapshot_json'])
        qs=[q for q in snapshot['questions'] if q['type'] in ('choice','true_false','meaning','theme','inference')][:3]
        response=self.client.post(f'/api/reading/sessions/{identity}/answers',json={'version':1,'answers':{q['id']:q['answer'] for q in qs}})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.call('/profile')['evidence']['articles'],0)
        self.sql("UPDATE reading_sessions SET kind='mistakes' WHERE id=?",(identity,))
        self.assertEqual(self.call('/profile')['evidence']['articles'],0)

    def test_old_quiz_results_expire_and_future_timestamps_are_not_evidence(self):
        self.quiz(1);self.current+=timedelta(days=91)
        self.assertEqual(self.call('/profile')['evidence']['articles'],0)
        self.feedback(2,difficulty='hard')
        self.sql('UPDATE article_feedback SET difficulty_at=?',((self.current+timedelta(days=1)).isoformat(),))
        self.assertEqual(self.call('/profile')['evidence']['articles'],0)

    def test_profile_level_and_confirmed_vocabulary_change_relative_difficulty(self):
        identity=self.article('The city has a small park. '*10)
        low=self.profile(level='A2'); before=self.call(f'/articles/{identity}')['analysis']
        self.profile(level='C2');after=self.call(f'/articles/{identity}')['analysis']
        self.assertLess(after['relative'],before['relative'])
        for word in ('the','city','has','a','small','park'): self.known(word)
        learned=self.call(f'/articles/{identity}')['analysis']
        self.assertEqual(learned['confirmed_mastered_percent'],100)
        self.assertEqual(learned['estimated_load_per_100'],0)

    def test_json_origin_and_cache_protection(self):
        response=self.client.patch('/api/personalization/profile',json={'version':0,'level':'C2'},headers={'Origin':'https://outside.example'})
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.client.get('/api/personalization/recommendations').headers['Cache-Control'],'no-store')


if __name__ == '__main__':
    unittest.main()
