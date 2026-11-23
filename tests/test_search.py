import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from newsreader.db import get_db
from newsreader.search import chunks


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'DATABASE': str(Path(self.temp.name) / 'test.sqlite3')})
        self.client = self.app.test_client()
        self.article = self.client.post('/api/articles', json={'title':'A hidden topic', 'source':'Test Press', 'category':'科技', 'content':('A regular sentence about people and places. ' * 20) + 'UniqueNeedle appears only in the full text.'}).json['id']

    def tearDown(self):
        self.temp.cleanup()

    def search(self, **kwargs):
        return self.client.get('/api/articles/search', query_string=kwargs)

    def test_full_text_and_combined_filters(self):
        self.client.patch(f'/api/articles/{self.article}/tags', json={'tags':['Space', 'space', 'Study']})
        item = self.client.get(f'/api/articles/{self.article}').json
        response = self.search(q='uniqueneedle', source='Test Press', tag='SPACE', category='科技', level=item['level'], min_words=item['word_count'], max_words=item['word_count'], min_minutes=item['reading_minutes'], max_minutes=item['reading_minutes'], status='unread')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([a['id'] for a in response.json['articles']], [self.article])
        self.assertIn('UniqueNeedle', response.json['articles'][0]['snippet'])
        self.assertNotIn('content', response.json['articles'][0])
        self.assertEqual(self.search(q='uniqueneedle', source='Other').json['articles'], [])
        self.assertEqual(self.search(q='uniqueneedle', min_words=item['word_count']+1).json['articles'], [])
        options = self.client.get('/api/articles/search-options').json
        self.assertIn('Test Press', options['sources'])
        self.assertEqual(sorted(options['tags']), ['Space', 'Study'])

    def test_reading_status_and_boundary_validation(self):
        self.client.patch(f'/api/articles/{self.article}/progress', json={'progress':0, 'position':0, 'seconds':1})
        self.assertEqual([a['id'] for a in self.search(source='Test Press', status='reading').json['articles']], [self.article])
        self.assertEqual(self.search(source='Test Press', status='unread').json['articles'], [])
        self.client.patch(f'/api/articles/{self.article}/progress', json={'progress':100, 'position':100, 'seconds':0})
        self.assertEqual([a['id'] for a in self.search(source='Test Press', status='completed').json['articles']], [self.article])
        for query in ({'min_words':-1}, {'min_minutes':4, 'max_minutes':2}, {'status':'unknown'}, {'q':'x'*201}, {'mode':'unknown'}):
            self.assertEqual(self.search(**query).status_code, 400)

    def test_tags_are_validated_persisted_and_deleted(self):
        self.assertEqual(self.client.patch(f'/api/articles/{self.article}/tags', json={'tags':['x'*21]}).status_code, 400)
        self.client.patch(f'/api/articles/{self.article}/tags', json={'tags':['focus']})
        restarted = create_app({'TESTING': True, 'DATABASE': self.app.config['DATABASE']}).test_client()
        self.assertEqual(restarted.get(f'/api/articles/{self.article}').json['tags'], ['focus'])
        restarted.delete(f'/api/articles/{self.article}', json={})
        self.assertNotIn('focus', restarted.get('/api/articles/search-options').json['tags'])

    def test_semantic_ranking_cache_and_similar_excludes_self(self):
        def fake_embed(texts, query=False):
            return [[1., 0.] if 'train' in text.lower() else [0., 1.] for text in texts]
        with patch('newsreader.search.embed', side_effect=fake_embed) as embed:
            first = self.search(q='train', mode='semantic')
            self.assertEqual(first.status_code, 200)
            self.assertIn('train', first.json['articles'][0]['title'].lower())
            cached_calls = embed.call_count
            self.search(q='train', mode='semantic')
            self.assertEqual(embed.call_count, cached_calls+1)  # query only; document vectors cached
            similar = self.client.get('/api/articles/1/similar')
            self.assertEqual(similar.status_code, 200)
            self.assertNotIn(1, [a['id'] for a in similar.json['articles']])
            with self.app.app_context():
                db = get_db()
                db.execute('UPDATE articles SET content=content || ? WHERE id=?', (' changed', self.article))
                db.commit()
            self.search(q='train', mode='semantic')
            self.assertGreater(embed.call_count, cached_calls+2)
        self.assertEqual(self.client.get('/api/articles/99999/similar').status_code, 404)

    def test_long_document_chunks_cover_end(self):
        content = 'a' * 5000 + 'LAST_MARKER'
        parts = chunks({'title':'Title', 'content':content})
        self.assertIn('LAST_MARKER', parts[-1])


if __name__ == '__main__':
    unittest.main()
