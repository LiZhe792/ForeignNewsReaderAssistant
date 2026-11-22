"""Real SQLite aggregation, migration, receipts, dates, and sampling boundaries."""
import csv
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from newsreader.db import get_db, CHINA_TZ


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=str(Path(self.temp.name)/'analytics.sqlite3');self.current=datetime(2026,11,22,10,tzinfo=CHINA_TZ);self.serial=0
        for target in ('newsreader.analytics.clock','newsreader.srs.clock'):
            mocked=patch(target,side_effect=lambda:self.current);mocked.start();self.addCleanup(mocked.stop)
        for target in ('newsreader.analytics.now','newsreader.db.now','app.now','newsreader.wordbook.now','newsreader.reading_flow.now'):
            mocked=patch(target,side_effect=lambda:self.current.isoformat());mocked.start();self.addCleanup(mocked.stop)
        mocked=patch('app.today',side_effect=lambda:self.current.date());mocked.start();self.addCleanup(mocked.stop)
        self.app=create_app({'TESTING':True,'DATABASE':self.path});self.client=self.app.test_client()

    def sql(self,query,args=()):
        with self.app.app_context():
            db=get_db();rows=[dict(r) for r in db.execute(query,args).fetchall()];db.commit();return rows

    def call(self,path,method='GET',data=None,status=200):
        response=self.client.open('/api'+path,method=method,json=data)
        self.assertEqual(response.status_code,status,response.get_data(as_text=True));return response.json

    def report(self,period='month',day=None):
        return self.call('/analytics/report?period='+period+'&date='+(day or self.current.date().isoformat()))

    def serial_id(self):self.serial+=1;return f'analytics_receipt_{self.serial}'

    def article(self,content='The city has a small park. '*100,**extra):
        return self.call('/articles','POST',{'title':'Reading sample','content':content,'category':'生活',**extra},201)['id']

    def progress(self,identity,seconds=30,progress=20,**extra):
        return self.call(f'/articles/{identity}/progress','PATCH',{'seconds':seconds,'progress':progress,'position':progress,'request_id':self.serial_id(),**extra})

    def lookup(self,identity=None,word='city',paragraph=-1,**extra):
        article=self.call('/articles/'+str(identity)) if identity else None
        return self.call('/analytics/lookup','POST',{'word':word,'request_id':self.serial_id(),'article_id':identity,'article_hash':article['content_hash'] if article else '', 'paragraph_index':paragraph,**extra},201)

    def word(self,word='city'):
        return self.call('/vocabulary','POST',{'word':word,'meaning':'测试释义'},201)['id']

    def answer(self,identity,grade=4,mode='front',attempt='',**extra):
        row=self.sql('SELECT s.version,m.version AS content_version FROM srs_cards s JOIN word_meta m ON m.word_id=s.word_id WHERE s.word_id=?',(identity,))[0]
        return self.call('/review/answer','POST',{'word_id':identity,**row,'grade':grade,'mode':mode,'attempt':attempt,'request_id':self.serial_id(),**extra})

    def test_empty_period_has_zero_counts_and_missing_rates(self):
        report=self.report();self.assertEqual(len(report['series']),30)
        self.assertEqual(report['totals']['seconds'],0);self.assertEqual(report['totals']['lookups'],0)
        for name in ('speed_wpm','remembered_rate','spelling_rate'):self.assertIsNone(report['totals'][name])
        self.assertTrue(all(day['speed_wpm'] is None for day in report['daily']))
        self.assertEqual(report['mastery'],{'new':0,'learning':0,'mastered':0})

    def test_period_boundaries_leap_year_and_monday_week(self):
        for period,anchor,start,end,count in [('week','2026-11-22','2026-11-16','2026-11-22',7),('month','2024-02-10','2024-02-01','2024-02-29',29),('year','2024-05-20','2024-01-01','2024-12-31',12),('week','2027-01-01','2026-12-28','2027-01-03',7)]:
            result=self.report(period,anchor);self.assertEqual((result['start'],result['end']),(start,end));self.assertEqual(len(result['series']),count)

    def test_invalid_period_dates_and_unknown_filters(self):
        for query in ('period=day','period=week&date=2026-2-01','date=2026-02-30','date=0001-01-01','date=9999-12-31','extra=1','date=2026-11-22T00:00:00'):
            self.call('/analytics/report?'+query,status=400)
        self.assertEqual(self.call('/analytics/report?period=week&date=1901-01-01')['start'],'1900-12-31')

    def test_speed_is_weighted_by_sample_time(self):
        identity=self.article();self.progress(identity,30,20);self.progress(identity,60,40)
        report=self.report();self.assertEqual(report['totals']['speed_wpm'],160)
        self.assertEqual(report['totals']['seconds'],90);self.assertEqual(report['totals']['completed'],0)
        self.assertEqual(report['topics'][0]['seconds'],90)

    def test_manual_completion_and_short_saves_do_not_create_speed(self):
        identity=self.article();self.progress(identity,5,20);self.progress(identity,20,100)
        result=self.report();self.assertIsNone(result['totals']['speed_wpm'])
        self.assertEqual(result['totals']['completed'],1);self.assertEqual(result['totals']['completed_words'],600)
        self.progress(identity,20,100)
        result=self.report();self.assertEqual(result['totals']['completed'],1);self.assertEqual(result['totals']['seconds'],45)

    def test_scroll_back_and_idle_saves_do_not_make_negative_or_zero_speed_samples(self):
        identity=self.article();self.progress(identity,30,40);self.progress(identity,30,20);self.progress(identity,20,40)
        result=self.report();self.assertEqual(result['totals']['speed_seconds'],30);self.assertEqual(result['totals']['speed_wpm'],480)
        self.assertEqual(self.call(f'/articles/{identity}')['progress'],40)

    def test_reading_receipt_retries_only_count_once_even_after_article_delete(self):
        identity=self.article();receipt=self.serial_id();data={'request_id':receipt,'seconds':30,'progress':20,'position':20}
        first=self.call(f'/articles/{identity}/progress','PATCH',data)
        again=self.call(f'/articles/{identity}/progress','PATCH',data);self.assertTrue(again['duplicate'])
        self.assertEqual(self.report()['totals']['seconds'],30)
        self.call('/articles/'+str(identity),'DELETE',{})
        self.assertTrue(self.call(f'/articles/{identity}/progress','PATCH',data)['duplicate'])
        self.assertEqual(self.report()['totals']['seconds'],30)

    def test_receipt_conflict_rolls_back(self):
        identity=self.article();receipt=self.serial_id();self.progress(identity,request_id=receipt)
        self.call(f'/articles/{identity}/progress','PATCH',{'request_id':receipt,'seconds':50,'progress':80,'position':80},409)
        self.assertEqual(self.report()['totals']['seconds'],30)
        for receipt in (True,[], 'short'):
            self.call(f'/articles/{identity}/progress','PATCH',{'request_id':receipt,'seconds':30},400)

    def test_lookup_receipt_is_idempotent_without_client_timestamp(self):
        receipt=self.serial_id();data={'request_id':receipt,'word':'city'}
        self.call('/analytics/lookup','POST',data,201);self.current+=timedelta(minutes=5)
        self.assertTrue(self.call('/analytics/lookup','POST',data)['duplicate'])
        self.call('/analytics/lookup','POST',{**data,'word':'park'},409)
        self.assertEqual(self.report()['totals']['lookups'],1)

    def test_lookup_china_day_uses_original_observation_not_retry_date(self):
        self.current=datetime(2026,11,23,10,tzinfo=CHINA_TZ)
        self.lookup(occurred_at='2026-11-22T16:01:00+00:00')
        result=self.report();days={d['date']:d for d in result['daily']}
        self.assertEqual(days['2026-11-23']['lookups'],1);self.assertEqual(days['2026-11-22']['lookups'],0)
        self.lookup(occurred_at='2026-11-21T02:00:00+00:00')
        self.assertEqual({d['date']:d for d in self.report()['daily']}['2026-11-21']['lookups'],1)

    def test_lookup_validation_timestamp_origin_and_no_partial_inserts(self):
        base={'request_id':self.serial_id(),'word':'city'}
        for extra in ({'word':'<script>'},{'word':'x'*61},{'article_id':True},{'article_id':2**80},{'paragraph_index':True},{'paragraph_index':0},{'extra':1},{'occurred_at':'bad'},{'occurred_at':True},{'occurred_at':[]} ,{'occurred_at':'2026-11-22T10:00:00'},{'occurred_at':(self.current-timedelta(days=8)).isoformat()},{'occurred_at':(self.current+timedelta(days=1)).isoformat()}):
            self.call('/analytics/lookup','POST',{**base,**extra},400)
        response=self.client.post('/api/analytics/lookup',json=base,headers={'Origin':'https://other.example'});self.assertEqual(response.status_code,403)
        self.assertEqual(self.sql('SELECT * FROM learning_lookup_events'),[])

    def test_invalid_json_and_content_type(self):
        self.call('/analytics/lookup','POST',[],400)
        response=self.client.post('/api/analytics/lookup',data='word=city');self.assertEqual(response.status_code,415)

    def test_pending_and_dictionary_lookup_share_encounter_deduplication(self):
        identity=self.article();body={'word':'city','paragraph_index':0}
        self.call(f'/articles/{identity}/pending-words','POST',body,201)
        self.call(f'/articles/{identity}/pending-words','POST',body)
        self.lookup(identity,paragraph=0);self.lookup(identity,paragraph=0)
        totals=self.report()['totals'];self.assertEqual(totals['encounters'],1);self.assertEqual(totals['lookups'],2)
        self.assertEqual(self.report()['repeated_words'],[])

    def test_repeated_encounters_across_paragraphs_days_and_articles(self):
        content=('The city has a small park. '*10+'\n\n')*2;identity=self.article(content)
        self.lookup(identity,paragraph=0);self.lookup(identity,paragraph=1)
        self.current+=timedelta(days=1);self.lookup(identity,paragraph=0)
        other=self.article(content);self.lookup(other,paragraph=0)
        row=self.report()['repeated_words'][0];self.assertEqual((row['word'],row['count'],row['articles'],row['days']),('city',4,2,2))

    def test_manual_lookup_is_not_an_observed_encounter(self):
        identity=self.article();self.lookup(identity,word='perspective')
        result=self.report();self.assertEqual(result['totals']['lookups'],1);self.assertEqual(result['totals']['encounters'],0)
        self.lookup(identity,word='perspective',paragraph=-1)
        self.assertEqual(self.report()['top_lookups'][0],{'word':'perspective','count':2})

    def test_stale_or_invalid_lookup_source_is_not_recorded(self):
        identity=self.article();article=self.call(f'/articles/{identity}');data={'request_id':self.serial_id(),'word':'city','article_id':identity,'article_hash':article['content_hash']}
        self.sql("UPDATE articles SET content=content||' Changed.' WHERE id=?",(identity,))
        self.call('/analytics/lookup','POST',data,409)
        self.call('/analytics/lookup','POST',{**data,'article_id':999999},404)
        fresh=self.call(f'/articles/{identity}')
        self.call('/analytics/lookup','POST',{**data,'article_hash':fresh['content_hash'],'paragraph_index':999},400)
        self.call('/analytics/lookup','POST',{**data,'article_hash':fresh['content_hash'],'paragraph_index':0,'word':'perspective'},400)
        self.assertEqual(self.report()['totals']['lookups'],0)

    def test_dictionary_reads_do_not_fabricate_learning_events(self):
        self.call('/dictionary?word=city&provider=offline');self.call('/dictionary?word=city&provider=offline')
        self.assertEqual(self.report()['totals']['lookups'],0)

    def test_vocabulary_growth_removal_and_current_mastery(self):
        first=self.word();self.word('take into account')
        self.call('/vocabulary/'+str(first),'PATCH',{'mastery':'mastered'})
        result=self.report();self.assertEqual(result['totals']['vocabulary_added'],2);self.assertEqual(result['mastery']['mastered'],1)
        self.call('/vocabulary/'+str(first),'DELETE',{})
        result=self.report();self.assertEqual(result['totals']['vocabulary_added'],2);self.assertEqual(result['totals']['vocabulary_removed'],1);self.assertEqual(result['totals']['vocabulary_net'],1)
        self.assertEqual(result['vocabulary_current'],1);self.assertEqual(result['daily'][-1]['vocabulary_total'],1)

    def test_duplicate_add_and_rolled_back_import_do_not_inflate_growth(self):
        self.word();self.call('/vocabulary','POST',{'word':'city','meaning':'重复'})
        with self.app.app_context():
            db=get_db();db.execute("INSERT INTO vocabulary(word,meaning,created_at) VALUES('temporary','释义',?)",(self.current.isoformat(),));db.rollback()
        self.assertEqual(self.report()['totals']['vocabulary_added'],1)

    def test_growth_baseline_and_zero_days(self):
        self.current=datetime(2026,10,15,10,tzinfo=CHINA_TZ);word=self.word()
        self.current=datetime(2026,11,22,10,tzinfo=CHINA_TZ);self.call('/vocabulary/'+str(word),'DELETE',{})
        report=self.report();self.assertEqual(report['vocabulary_at_start'],1)
        self.assertEqual(report['daily'][0]['vocabulary_total'],1);self.assertEqual(report['daily'][-1]['vocabulary_total'],0)
        self.assertEqual(report['totals']['vocabulary_net'],-1)

    def test_mastery_snapshot_is_current_even_for_past_report(self):
        word=self.word();self.call('/vocabulary/'+str(word),'PATCH',{'mastery':'mastered'})
        result=self.report('month','2020-01-10');self.assertEqual(result['totals']['vocabulary_added'],0);self.assertEqual(result['mastery']['mastered'],1)

    def test_review_self_report_and_actual_spelling_are_separate(self):
        word=self.word();self.answer(word,4,'spelling','wrong')
        t=self.report()['totals'];self.assertEqual(t['remembered_rate'],100);self.assertEqual(t['spelling_rate'],0);self.assertEqual(t['forgotten'],0)

    def test_first_miss_is_not_forgetting_and_later_lapse_is(self):
        word=self.word();self.answer(word,0)
        self.current+=timedelta(minutes=11);self.answer(word,0)
        t=self.report()['totals'];self.assertEqual(t['first_learning_misses'],1);self.assertEqual(t['forgotten'],1);self.assertEqual(t['remembered_rate'],0)
        self.assertEqual(self.report()['forgotten_words'][0],{'word':'city','count':1})

    def test_review_confirmation_retries_and_deletion_preserve_history(self):
        word=self.word();receipt=self.serial_id();row=self.sql('SELECT s.version,m.version AS content_version FROM srs_cards s JOIN word_meta m ON m.word_id=s.word_id WHERE s.word_id=?',(word,))[0]
        data={'word_id':word,**row,'grade':4,'mode':'spelling','attempt':'city','request_id':receipt}
        self.call('/review/answer','POST',data);self.call('/review/answer','POST',data)
        self.call(f'/vocabulary/{word}','DELETE',{})
        t=self.report()['totals'];self.assertEqual(t['reviews'],1);self.assertEqual(t['spelling_rate'],100)

    def test_review_rate_weights_attempt_counts_not_daily_percentages(self):
        for name in ('city','park','read'):
            self.answer(self.word(name),4 if name=='city' else 0)
        self.assertEqual(self.report()['totals']['remembered_rate'],33.3)

    def test_lookup_is_not_automatically_a_new_word(self):
        identity=self.article();self.lookup(identity,paragraph=0)
        row=next(a for a in self.report()['articles'] if a['id']==identity);self.assertEqual(row['unmastered_rate'],0)
        word=self.word();row=next(a for a in self.report()['articles'] if a['id']==identity)
        self.assertEqual(row['unmastered_rate'],16.7);self.assertEqual(row['recorded_words'],100)
        self.call(f'/vocabulary/{word}','PATCH',{'mastery':'mastered'})
        self.assertEqual(next(a for a in self.report()['articles'] if a['id']==identity)['unmastered_rate'],0)

    def test_phrase_words_and_dictionary_misses_do_not_inflate_word_rate(self):
        identity=self.article('Quuxblargh take into account. '*10);self.word('take into account')
        row=next(a for a in self.report()['articles'] if a['id']==identity)
        self.assertEqual(row['unmastered_rate'],0)

    def test_deleted_and_changed_articles_keep_snapshots_and_do_not_invent_current_rates(self):
        identity=self.article();self.progress(identity);self.lookup(identity)
        self.sql("UPDATE articles SET content=content||' Change.' WHERE id=?",(identity,))
        rows=[a for a in self.report()['articles'] if a['original_id']==identity]
        self.assertEqual(len(rows),2);self.assertIsNone(next(a for a in rows if not a['current'])['unmastered_rate'])
        self.call(f'/articles/{identity}','DELETE',{})
        report=self.report();self.assertEqual(report['totals']['seconds'],30);self.assertEqual(report['totals']['lookups'],1)
        self.assertFalse(next(a for a in report['articles'] if a['original_id']==identity)['current'])
        self.assertEqual(self.sql('PRAGMA foreign_key_check'),[])

    def test_manual_checkins_do_not_create_reading_volume(self):
        self.call('/checkin','POST',{});t=self.report()['totals']
        self.assertEqual(t['seconds'],0);self.assertEqual(t['completed'],0);self.assertEqual(t['active_reading_days'],0)

    def test_old_aggregate_data_does_not_get_fabricated_topics_or_words(self):
        self.sql('INSERT INTO checkins(date,reading_seconds,articles_completed) VALUES(?,?,?)',(self.current.date().isoformat(),900,3))
        r=self.report();self.assertEqual(r['totals']['seconds'],900);self.assertEqual(r['totals']['completed'],3)
        self.assertEqual(r['topics'],[]);self.assertEqual(r['totals']['completed_words'],0);self.assertIsNone(r['totals']['speed_wpm'])

    def test_upgrade_backfills_known_completion_and_vocabulary_once(self):
        identity=self.article();word=self.word();self.progress(identity,30,100)
        with self.app.app_context():
            db=get_db();db.execute('DROP TRIGGER learning_word_add');db.execute('DROP TRIGGER learning_word_remove')
            for name in ('learning_reading_events','learning_lookup_events','learning_word_events'):db.execute('DROP TABLE '+name)
            db.execute("DELETE FROM settings WHERE key='analytics.started_at'");db.commit()
        for _ in range(2):create_app({'TESTING':True,'DATABASE':self.path})
        r=self.report();self.assertEqual(r['totals']['vocabulary_added'],1);self.assertEqual(r['totals']['completed_words'],600);self.assertEqual(r['totals']['seconds'],30)
        self.assertIsNone(r['totals']['speed_wpm']);self.assertEqual(r['topics'][0]['seconds'],0)
        self.assertEqual(len(self.sql('SELECT * FROM learning_reading_events')),1)

    def test_year_months_are_weighted_and_include_empty_months(self):
        self.current=datetime(2026,1,10,10,tzinfo=CHINA_TZ);identity=self.article();self.progress(identity,30,20)
        self.current=datetime(2026,11,22,10,tzinfo=CHINA_TZ);self.progress(identity,60,40)
        r=self.report('year');self.assertEqual(len(r['series']),12)
        self.assertEqual(r['series'][0]['speed_wpm'],240);self.assertIsNone(r['series'][1]['speed_wpm']);self.assertEqual(r['series'][10]['speed_wpm'],120)
        self.assertEqual(r['totals']['speed_wpm'],160)

    def test_csv_export_has_china_dates_utf8_bom_and_notes(self):
        identity=self.article();self.progress(identity)
        response=self.client.get('/api/analytics/export?period=week&date=2026-11-22')
        self.assertEqual(response.status_code,200);self.assertTrue(response.data.startswith(b'\xef\xbb\xbf'))
        rows=list(csv.reader(io.StringIO(response.data.decode('utf-8-sig'))))
        self.assertEqual(rows[1][0],'2026-11-16');self.assertEqual(rows[7][0],'2026-11-22')
        self.assertIn('margin-week-2026-11-16.csv',response.headers['Content-Disposition'])
        self.assertTrue(any(row and row[0]=='口径说明' for row in rows))

    def test_unobserved_lookup_dates_are_missing_in_charts_and_csv(self):
        report=self.report('week','2020-01-01');self.assertEqual(report['lookup_coverage'],'none')
        self.assertTrue(all(not row['lookup_observed'] for row in report['series']))
        response=self.client.get('/api/analytics/export?period=week&date=2020-01-01')
        rows=list(csv.reader(io.StringIO(response.data.decode('utf-8-sig'))))
        self.assertEqual(rows[1][7:9],['',''])
        self.lookup();self.assertEqual(self.report('week')['lookup_coverage'],'partial')
        self.assertTrue(self.report('week')['daily'][-1]['lookup_observed'])

    def test_zero_activity_saves_are_not_a_topic_preference(self):
        identity=self.article();self.progress(identity,0,0)
        self.assertEqual(self.report()['topics'],[])

    def test_browser_utc_z_timestamps_are_normalized_to_china(self):
        self.lookup(occurred_at='2026-11-22T02:00:00.000Z')
        row=self.sql('SELECT * FROM learning_lookup_events')[0]
        self.assertEqual(row['day'],'2026-11-22');self.assertTrue(row['occurred_at'].endswith('+08:00'))

    def test_reports_are_read_only_and_cache_disabled(self):
        self.word();identity=self.article();self.progress(identity)
        tables=('articles','vocabulary','srs_cards','learning_reading_events','learning_word_events','learning_lookup_events','checkins')
        before={table:self.sql('SELECT * FROM '+table) for table in tables}
        self.report();self.client.get('/api/analytics/export')
        self.assertEqual(before,{table:self.sql('SELECT * FROM '+table) for table in tables})
        response=self.client.get('/api/analytics/report');self.assertEqual(response.headers['Cache-Control'],'no-store')


if __name__=='__main__':unittest.main()
