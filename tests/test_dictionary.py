"""Dictionary selection, offline data integrity, phrases and failure boundaries."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from newsreader.db import get_db, now
from newsreader.dictionary import EXTRA, NOTES, normalize_term, offline_entry
from newsreader.network import FetchError


class DictionaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.config = {'TESTING': True, 'DATABASE': str(Path(self.temporary.name) / 'dictionary.sqlite3')}
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temporary.cleanup()

    def lookup(self, word, provider='offline'):
        response = self.client.get('/api/dictionary', query_string={'word': word, 'provider': provider})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def remote(self):
        return [{'word': 'journey', 'phonetics': [
            {'text': '/UK/', 'audio': 'https://example.org/journey-uk.mp3'},
            {'text': '/US/', 'audio': 'https://example.org/journey-us.mp3'}],
            'meanings': [{'partOfSpeech': 'noun', 'synonyms': ['trip'], 'antonyms': [],
                          'definitions': [{'definition': 'Travel from one place to another.',
                                           'example': 'A journey starts here.', 'synonyms': ['voyage']}]}]}]

    def test_expanded_offline_dictionary_never_uses_network(self):
        with patch('newsreader.content.fetch_public') as network:
            result = self.lookup('democracy')
            unknown = self.lookup('unlistedword')
        network.assert_not_called()
        self.assertTrue(result['found'])
        self.assertIn('民主', result['meaning'])
        self.assertFalse(unknown['found'])
        self.assertEqual(unknown['meaning'], '')

    def test_bundled_dataset_is_queryable_without_recursion_or_invalid_headwords(self):
        self.assertGreaterEqual(len(EXTRA), 12000)
        for word in EXTRA:
            self.assertEqual(normalize_term(word), word)
            lemma, entry = offline_entry(word)
            self.assertTrue(entry['meaning'], word)
            self.assertLessEqual(len(entry['meaning']), 3000, lemma)

    def test_attested_irregular_and_regular_forms(self):
        for word, lemma in [('went', 'go'), ('taken', 'take'), ('running', 'run'),
                            ('children', 'child'), ('cities', 'city'), ('better', 'good'), ("reader’s", 'reader')]:
            with self.subTest(word=word):
                self.assertEqual(self.lookup(word)['lemma'], lemma)

    def test_exact_word_senses_are_not_erased_by_stemming(self):
        news = self.lookup('news')
        self.assertEqual(news['lemma'], 'news')
        self.assertIn('新闻', news['meaning'])
        self.assertIn('锯', self.lookup('saw')['meaning'])

    def test_all_original_phrases_work_offline(self):
        with patch('newsreader.content.fetch_public') as network:
            for phrase in NOTES['phrases']:
                self.assertTrue(self.lookup(phrase)['found'], phrase)
        network.assert_not_called()

    def test_phrase_inflections_and_whitespace_are_normalized(self):
        result = self.lookup('  Took   into\taccount  ')
        self.assertEqual(result['word'], 'took into account')
        self.assertEqual(result['lemma'], 'take into account')
        self.assertIn('考虑', result['meaning'])
        self.assertEqual(self.lookup('making a difference')['lemma'], 'make a difference')

    def test_online_provider_is_used_even_for_a_known_offline_word(self):
        with patch('newsreader.content.fetch_public', return_value=(json.dumps(self.remote()).encode(), '', '')) as fetch:
            result = self.lookup('journey', 'free')
        self.assertEqual(fetch.call_count, 1)
        self.assertIn('Free Dictionary', result['source'])
        self.assertEqual(result['phonetics']['uk'], '/UK/')
        self.assertEqual(result['phonetics']['us'], '/US/')
        self.assertIn('trip', result['knowledge']['synonyms'])

    def test_online_cache_is_separate_from_offline_dictionary_and_persists(self):
        with patch('newsreader.content.fetch_public', return_value=(json.dumps(self.remote()).encode(), '', '')) as fetch:
            first = self.lookup('journey', 'free')
            restarted = create_app(self.config).test_client()
            second = restarted.get('/api/dictionary', query_string={'word': 'journey', 'provider': 'free'}).json
            offline = self.lookup('journey')
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(second['cached'])
        self.assertEqual(first['meaning'], second['meaning'])
        self.assertIn('旅程', offline['meaning'])
        self.assertNotIn('cached', offline)

    def test_preferences_are_validated_saved_and_used_by_default(self):
        response = self.client.patch('/api/settings', json={'dictionary_provider': 'offline'})
        self.assertEqual(response.status_code, 200)
        with patch('newsreader.content.fetch_public') as network:
            self.assertFalse(self.client.get('/api/dictionary?word=unlistedword').json['found'])
        network.assert_not_called()
        restarted = create_app(self.config).test_client()
        self.assertEqual(restarted.get('/api/settings').json['dictionary_provider'], 'offline')
        self.assertEqual(self.client.patch('/api/settings', json={'dictionary_provider': 'invalid', 'theme': 'dark'}).status_code, 400)
        self.assertEqual(self.client.get('/api/settings').json['theme'], 'light')
        self.assertEqual(self.client.get('/api/dictionary?word=learn&provider=invalid').status_code, 400)

    def test_missing_region_is_not_presented_as_a_british_or_american_pronunciation(self):
        result = self.lookup('democracy')
        self.assertTrue(result['phonetics']['other'])
        self.assertEqual(result['phonetics']['uk'], '')
        self.assertEqual(result['phonetics']['us'], '')
        result = self.lookup('schedule')
        self.assertNotEqual(result['phonetics']['uk'], result['phonetics']['us'])

    def test_learning_knowledge_has_real_data_and_missing_data_is_explicit(self):
        info = self.lookup('sustainable')['knowledge']
        self.assertIn('sustainable development', info['collocations'])
        self.assertIn('sustainability', info['family'])
        self.assertTrue(info['roots'])
        self.assertGreater(info['frequency']['bnc'], 0)
        self.assertTrue(self.lookup('affect')['knowledge']['confusables'])
        self.assertIsNone(self.lookup('unlistedword')['knowledge']['frequency']['bnc'])

    def test_online_outage_is_honest_and_not_cached(self):
        with patch('newsreader.content.fetch_public', side_effect=FetchError('offline')) as fetch:
            for _ in range(2):
                result = self.lookup('journey', 'free')
                self.assertFalse(result['found'])
                self.assertEqual(result['meaning'], '')
        self.assertEqual(fetch.call_count, 2)

    def test_malformed_online_responses_remain_editable(self):
        malformed = [None, {}, [None], [{'meanings': {}}], [{'meanings': [{'definitions': [None, {}]}]}],
                     [{'phonetics': [None, 'x'], 'meanings': [{'definitions': [{'definition': 7}]}]}]]
        for data in malformed:
            with self.subTest(data=data), patch('newsreader.content.fetch_public', return_value=(json.dumps(data).encode(), '', '')):
                self.assertFalse(self.lookup('unlistedword', 'free')['found'])

    def test_corrupt_cached_json_does_not_break_lookup(self):
        for bad in ['not json', 'null', '{"found":true,"meaning":"old"}',
                    '{"found":true,"meaning":"old","phonetics":{},"synonyms":[],"antonyms":[]}']:
            with self.app.app_context():
                db = get_db()
                db.execute('INSERT OR REPLACE INTO dictionary_cache VALUES (?, ?, ?)', ('v7:free:journey', bad, now()))
                db.commit()
            with patch('newsreader.content.fetch_public', return_value=(json.dumps(self.remote()).encode(), '', '')) as fetch:
                self.assertTrue(self.lookup('journey', 'free')['found'])
                self.assertEqual(fetch.call_count, 1)

    def test_large_remote_fields_fit_vocabulary_limits(self):
        data = self.remote()
        data[0]['phonetics'][0]['text'] = 'p' * 200
        data[0]['meanings'][0]['partOfSpeech'] = 'n' * 150
        data[0]['meanings'][0]['definitions'] = [{'definition': 'x' * 4000 + str(i)} for i in range(5)]
        with patch('newsreader.content.fetch_public', return_value=(json.dumps(data).encode(), '', '')):
            result = self.lookup('journey', 'free')
        response = self.client.post('/api/vocabulary', json={k: result[k] for k in ('word', 'meaning', 'phonetic', 'part_of_speech')})
        self.assertEqual(response.status_code, 201, response.json)

    def test_phrase_collection_deduplicates_and_preserves_mastery(self):
        first = self.client.post('/api/vocabulary', json={'word': 'Take into account', 'meaning': '考虑'})
        self.assertEqual(first.status_code, 201)
        self.client.patch('/api/vocabulary/' + str(first.json['id']), json={'mastery': 'mastered', 'note': 'my note'})
        second = self.client.post('/api/vocabulary', json={'word': 'TAKE  INTO ACCOUNT', 'meaning': 'replace'})
        self.assertTrue(second.json['duplicate'])
        row = self.client.get('/api/vocabulary').json['words'][0]
        self.assertEqual((row['meaning'], row['note'], row['mastery']), ('考虑', 'my note', 'mastered'))

    def test_phrase_pending_requires_contiguous_words_in_the_same_paragraph(self):
        article = self.client.post('/api/articles', json={'title': 'Phrase test', 'content':
            'We take into account the needs of every reader. We never mistake art for part.\n\n'
            'Another paragraph discusses the city and its residents. ' +
            'Readers learn about the world through careful research and clear evidence. ' * 4}).json
        endpoint = f"/api/articles/{article['id']}/pending-words"
        response = self.client.post(endpoint, json={'word': 'take into account', 'paragraph_index': 0})
        self.assertEqual(response.status_code, 201)
        for phrase in ['take the needs', 'reader another', 'art for par']:
            self.assertEqual(self.client.post(endpoint, json={'word': phrase, 'paragraph_index': 0}).status_code, 400)
        entry = response.json['word']
        self.assertIn('take into account', entry['context'])
        saved = self.client.post(endpoint + f"/{entry['id']}/collect", json={'meaning': '考虑'})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.client.get(endpoint).json['words'], [])

    def test_invalid_terms_and_oversized_sentences_are_rejected(self):
        for word in ['', 'a' * 61, 'one two three four five six seven', '<img>', 'hello!', '123', '--word']:
            self.assertEqual(self.client.get('/api/dictionary', query_string={'word': word}).status_code, 400, word)
