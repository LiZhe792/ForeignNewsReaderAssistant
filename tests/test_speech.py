"""Speech persistence and cache contracts, without calling an external speech service."""
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from app import create_app
from newsreader import tts_audio
from newsreader.db import get_db
from newsreader.sentences import article_segments, split_sentences

AUDIO = b'\xff\xfb' + b'\0' * 510


class SegmentationTests(unittest.TestCase):
    def test_abbreviations_decimals_quotes_and_acronyms(self):
        source = 'Dr. Smith visited the U.S. market at 3.14 p.m. "Is it ready?" She said yes. 🌍 Read on!'
        parts = split_sentences(source)
        self.assertEqual(''.join(parts), source)
        self.assertEqual(len(parts), 4)
        self.assertTrue(parts[0].startswith('Dr. Smith'))
        self.assertIn('3.14 p.m.', parts[0])

    def test_text_and_paragraph_mapping_are_preserved(self):
        source = ' First sentence.  Second sentence!\n\nThird paragraph? Yes.\nLast without punctuation '
        segments = article_segments(source)
        self.assertEqual([s['index'] for s in segments], list(range(len(segments))))
        self.assertEqual(''.join(s['text'] for s in segments if s['paragraph'] == 0), 'First sentence.  Second sentence!')
        self.assertEqual(segments[-1]['paragraph'], 2)
        self.assertEqual(segments[-1]['text'], 'Last without punctuation')

    def test_long_unpunctuated_text_is_playable_without_loss(self):
        source = 'very long text ' * 400
        parts = split_sentences(source)
        self.assertEqual(''.join(parts), source)
        self.assertTrue(all(len(part) <= 1800 for part in parts))
        self.assertGreater(len(parts), 1)


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.config = {'TESTING': True, 'DATABASE': str(self.folder / 'test.sqlite3')}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.parts = ['Dr. Smith reads slowly. He notices the journey, not just the destination. ' * 3,
                      'The city offers a quiet place to read. Every sentence can reveal a new idea. ' * 3]
        self.article = self.client.post('/api/articles', json={'title': 'Listening test', 'content': '\n\n'.join(self.parts)}).json['id']
        self.patch = patch('newsreader.tts_audio.synthesize', return_value=AUDIO)
        self.provider = self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def audio(self, **extra):
        return self.client.post('/api/tts/audio', json={'article_id': self.article, 'sentence_index': 0, **extra})

    def add(self, **extra):
        return self.client.post('/api/tts/playlist', json={'article_id': self.article, 'scope': 'article', **extra})

    def test_reader_loads_player_assets(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('id="speech-player-template"', html)
        for asset in ('/static/speech.js', '/static/speech.css'):
            self.assertIn(asset, html)
            with self.client.get(asset) as response:
                self.assertEqual(response.status_code, 200)

    def test_article_manifest_uses_exact_reading_text_without_generating_audio(self):
        detail = self.client.get(f'/api/articles/{self.article}').json
        for index, part in enumerate(self.parts):
            self.assertEqual(''.join(s['text'] for s in detail['tts_segments'] if s['paragraph'] == index), part.strip())
        self.provider.assert_not_called()

    def test_preferences_survive_restart_without_touching_reader_preferences(self):
        old = self.client.get('/api/settings').json
        values = {'voice': 'en-GB-SoniaNeural', 'rate': 0.7, 'loop': 'paragraph', 'follow': False, 'click_to_play': True}
        self.assertEqual(self.client.patch('/api/tts/settings', json=values).status_code, 200)
        restarted = create_app(self.config).test_client()
        self.assertEqual(restarted.get('/api/tts/settings').json['settings'], values)
        self.assertEqual(restarted.get('/api/settings').json, old)
        self.provider.assert_not_called()

    def test_invalid_settings_are_atomic_and_corrupt_settings_fall_back(self):
        for data in ({'voice': 'custom-url'}, {'rate': 10**400}, {'rate': True}, {'follow': 1}, {'loop': []}, {'extra': 'value'}, {'rate': .2, 'follow': False}):
            self.assertEqual(self.client.patch('/api/tts/settings', json=data).status_code, 400)
        self.assertTrue(self.client.get('/api/tts/settings').json['settings']['follow'])
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('tts_preferences',?)", (json.dumps({'voice': 'bad', 'rate': .8}),))
            db.commit()
        settings = self.client.get('/api/tts/settings').json['settings']
        self.assertEqual(settings['voice'], 'en-US-AriaNeural')
        self.assertEqual(settings['rate'], .8)

    def test_same_text_and_voice_reuse_disk_cache_after_restart_and_offline(self):
        first = self.audio().json
        self.assertFalse(first['cached'])
        self.provider.side_effect = tts_audio.SpeechError('Offline')
        restarted = create_app(self.config).test_client()
        second = restarted.post('/api/tts/audio', json={'article_id': self.article, 'sentence_index': 0}).json
        self.assertTrue(second['cached'])
        self.assertEqual(first['key'], second['key'])
        self.assertEqual(self.client.get('/api/tts/cache').json['files'], 1)
        self.provider.assert_called_once()

    def test_voice_changes_use_a_new_cache_and_rate_does_not(self):
        first = self.audio().json
        self.client.patch('/api/tts/settings', json={'rate': 1.6})
        self.assertEqual(self.audio().json['key'], first['key'])
        british = self.audio(voice='en-GB-RyanNeural').json
        self.assertNotEqual(british['key'], first['key'])
        self.assertEqual(self.provider.call_count, 2)

    def test_audio_bytes_ranges_and_local_only_media_policy(self):
        result = self.audio().json
        with self.client.get(result['url']) as media:
            self.assertEqual(media.data, AUDIO)
            self.assertEqual(media.mimetype, 'audio/mpeg')
        with self.client.get(result['url'], headers={'Range': 'bytes=0-19'}) as partial:
            self.assertEqual(partial.status_code, 206)
            self.assertEqual(partial.data, AUDIO[:20])
        self.assertIn("media-src 'self'", self.client.get('/').headers['Content-Security-Policy'])

    def test_word_audio_is_validated_and_preserves_compound_words(self):
        self.assertEqual(self.audio(scope='word', word="reader's").status_code, 200)
        self.assertEqual(self.provider.call_args.args[0], "reader's")
        for value in ('two words', '<script>', '', 'a'*61, None, True):
            self.assertEqual(self.audio(scope='word', word=value).status_code, 400)
        self.assertEqual(self.provider.call_count, 1)

    def test_bad_article_index_voice_and_cross_origin_never_generate(self):
        for data in ({'sentence_index': -1}, {'sentence_index': True}, {'sentence_index': 9999}, {'article_id': True}, {'voice': 'https://example.com'}):
            self.assertEqual(self.audio(**data).status_code, 400)
        self.assertEqual(self.audio(article_id=9999).status_code, 404)
        self.assertEqual(self.client.post('/api/tts/audio', json={'scope': 'word', 'word': 'hello'}, headers={'Origin':'https://example.com'}).status_code, 403)
        self.assertEqual(self.client.get('/api/tts/audio/not-a-hash').status_code, 400)
        self.provider.assert_not_called()

    def test_failed_generation_leaves_no_partial_cache(self):
        self.provider.side_effect = tts_audio.SpeechError('Offline')
        self.assertEqual(self.audio().status_code, 503)
        self.assertEqual(self.client.get('/api/tts/cache').json['files'], 0)
        self.assertFalse(list(self.folder.rglob('*.part')))
        self.provider.side_effect = None
        self.assertEqual(self.audio().status_code, 200)

    def test_cache_budget_evicts_least_recently_used_audio(self):
        self.app.config['TTS_CACHE_MAX_BYTES'] = len(AUDIO)*2
        oldest = self.audio(scope='word', word='first').json['key']
        path = self.folder / 'tts_cache' / (oldest + '.mp3')
        os.utime(path, (100, 100))
        self.audio(scope='word', word='second')
        self.audio(scope='word', word='third')
        stats = self.client.get('/api/tts/cache').json
        self.assertEqual(stats['files'], 2)
        self.assertLessEqual(stats['bytes'], stats['limit_bytes'])
        self.assertFalse(path.exists())

    def test_clear_cache_only_removes_managed_audio_and_keeps_playlist(self):
        self.audio()
        self.add()
        unrelated = self.folder / 'tts_cache' / 'keep.txt'
        unrelated.write_text('unrelated', encoding='utf-8')
        result = self.client.delete('/api/tts/cache', json={})
        self.assertEqual(result.json['files'], 0)
        self.assertTrue(unrelated.exists())
        self.assertEqual(len(self.client.get('/api/tts/playlist').json['items']), 1)

    def test_concurrent_same_sentence_is_generated_only_once(self):
        started, finish = Event(), Event()
        def delayed(*_args):
            started.set()
            if not finish.wait(10):
                raise AssertionError('Simulated generator timed out')
            return AUDIO
        self.provider.side_effect = delayed
        def request():
            return self.app.test_client().post('/api/tts/audio', json={'article_id': self.article, 'sentence_index': 0})
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(request)
            try:
                self.assertTrue(started.wait(10))
                second = pool.submit(request)
            finally:
                finish.set()
            responses = [first.result(timeout=10), second.result(timeout=10)]
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertEqual(sorted(r.json['cached'] for r in responses), [False, True])
        self.provider.assert_called_once()

    def test_clear_during_generation_does_not_repopulate_cache(self):
        started, finish = Event(), Event()
        def delayed(*_args):
            started.set()
            if not finish.wait(10):
                raise AssertionError('Simulated generator timed out')
            return AUDIO
        self.provider.side_effect = delayed
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(lambda: self.app.test_client().post('/api/tts/audio', json={'scope':'word','word':'journey'}))
            try:
                self.assertTrue(started.wait(10))
                self.assertEqual(self.client.delete('/api/tts/cache', json={}).json['files'], 0)
            finally:
                finish.set()
            self.assertEqual(future.result(timeout=10).status_code, 409)
        self.assertEqual(self.client.get('/api/tts/cache').json['files'], 0)

    def test_playlist_supports_all_scopes_deduplication_order_and_restart(self):
        ids = [self.add().json['id'], self.add(scope='paragraph', unit_index=1).json['id'],
               self.add(scope='sentence', unit_index=1).json['id'], self.add(scope='word', word='journey').json['id']]
        self.assertTrue(self.add().json['duplicate'])
        reverse = list(reversed(ids))
        result = self.client.patch('/api/tts/playlist/order', json={'ids': reverse})
        self.assertEqual(result.status_code, 200)
        restarted = create_app(self.config).test_client()
        self.assertEqual([i['id'] for i in restarted.get('/api/tts/playlist').json['items']], reverse)
        self.assertEqual(self.client.patch('/api/tts/playlist/order', json={'ids': ids[:-1]}).status_code, 409)
        self.assertEqual(self.client.patch('/api/tts/playlist/order', json={'ids': [ids[0]]*2}).status_code, 400)
        self.provider.assert_not_called()

    def test_playlist_rejects_invalid_items_and_cleans_article_entries(self):
        for data in ({'scope': 'remote'}, {'scope': 'paragraph', 'unit_index': 999}, {'scope': 'sentence', 'unit_index': True}):
            self.assertEqual(self.add(**data).status_code, 400)
        article_item = self.add().json['id']
        word_item = self.add(scope='word', word='journey').json['id']
        self.client.delete(f'/api/articles/{self.article}', json={})
        items = self.client.get('/api/tts/playlist').json['items']
        self.assertEqual([i['id'] for i in items], [word_item])
        self.assertNotIn(article_item, [i['id'] for i in items])
        self.client.delete(f'/api/tts/playlist/{word_item}', json={})
        self.assertEqual(self.client.get('/api/tts/playlist').json['items'], [])


if __name__ == '__main__':
    unittest.main()
