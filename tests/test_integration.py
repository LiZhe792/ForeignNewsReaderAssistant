"""Cross-feature contracts for the local all-branches integration."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import create_app
from newsreader import ai_client, tts_audio
from newsreader.data_management import TABLES
from newsreader.db import get_db

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({'TESTING': True, 'DATABASE': str(Path(self.temp.name) / 'test.db')})
        self.client = self.app.test_client()

    def test_every_learning_table_is_covered_by_backup(self):
        with self.app.app_context():
            tables = {r[0] for r in get_db().execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            self.assertEqual(tables, set(TABLES))
        made = self.client.post('/api/data/backups', json={})
        self.assertEqual(made.status_code, 201)
        identity = made.json['backup']['id']
        archive = self.client.get(f'/api/data/backups/{identity}/download')
        preview = self.client.post('/api/data/restore/preview', data={'file': (io.BytesIO(archive.data), 'backup.zip')})
        self.assertEqual(preview.status_code, 201, preview.json)

    def test_batch_import_and_library_operations_are_both_reachable(self):
        result = self.client.post('/api/articles/batch', json={'ids': [1], 'op': 'favorite'})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertTrue(self.client.get('/api/articles/1').json['favorite'])
        with patch('newsreader.articles.import_url', side_effect=ValueError('unavailable')):
            result = self.client.post('/api/articles/batch', json={'urls': ['https://example.com/article']})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(len(result.json['results']), 1)
        self.assertFalse(result.json['results'][0]['ok'])

    def test_tags_are_shared_by_library_and_search(self):
        saved = self.client.patch('/api/articles/1', json={'tags': ['shared-tag']})
        self.assertEqual(saved.status_code, 200, saved.json)
        found = self.client.get('/api/articles/search?tag=shared-tag').json['articles']
        self.assertEqual([a['id'] for a in found], [1])
        self.assertIn('tts_segments', self.client.get('/api/articles/1').json)

    def test_offline_stops_newly_integrated_network_clients(self):
        self.assertEqual(self.client.patch('/api/data/preferences', json={'offline': True}).status_code, 200)
        with self.app.app_context(), patch('newsreader.ai_client.urllib.request.build_opener') as transport:
            with self.assertRaises(ai_client.AIError):
                ai_client.request_json('/chat/completions', 'fixture-key', {})
            transport.assert_not_called()
        with self.app.app_context(), patch('newsreader.tts_audio.synthesize') as synthesize:
            with self.assertRaises(tts_audio.SpeechError):
                tts_audio.get_audio('uncached audio', 'en-US-AriaNeural')
            synthesize.assert_not_called()
