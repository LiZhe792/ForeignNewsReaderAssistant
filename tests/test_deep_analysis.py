import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from app import create_app
from newsreader import ai
from newsreader.deep_analysis import ARTICLE_FIELDS, SENTENCE_FIELDS


class DeepAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'DATABASE': str(Path(self.temp.name) / 'test.sqlite3'),
                               'AI_API_KEY': 'test-token', 'AI_MODEL': 'test-model',
                               'AI_BASE_URL': 'https://example.com/v1'})
        self.client = self.app.test_client()
        self.article = self.client.get('/api/articles/1').json
        self.paragraph = self.article['content'].split('\n')[0].strip()
        self.sentence = self.paragraph.split('.')[0] + '.'

    def tearDown(self):
        self.temp.cleanup()

    def result(self, mode, **overrides):
        fields = SENTENCE_FIELDS if mode == 'sentence' else ARTICLE_FIELDS
        result = {key: '结果' for key in fields}
        result['evidence'] = []
        result.update(overrides)
        return result

    def post(self, data, article=1):
        return self.client.post(f'/api/articles/{article}/deep-analysis', json=data)

    def test_sentence_uses_real_selection_and_shared_transport(self):
        result = self.result('sentence', evidence=[{'article_id':1,'quote':self.sentence,'explanation':'原文依据'}])
        with patch('newsreader.ai.request_json', return_value=result) as transport:
            response = self.post({'mode':'sentence','level':'beginner','paragraph_index':0,'selected_text':self.sentence})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['result']['main_clause'], '结果')
        payload, config = transport.call_args.args
        materials = json.loads(payload['input'])['materials']
        self.assertEqual(materials[0]['selected_sentence'], self.sentence)
        self.assertEqual(config['base'], 'https://example.com/v1')
        self.assertFalse(payload['store'])
        self.assertEqual(transport.call_args.kwargs['timeout'], 120)

    def test_full_paragraph_and_multiarticle_comparison(self):
        other = self.client.get('/api/articles/2').json
        result = self.result('article', evidence=[{'article_id':2,'quote':other['content'][:25],'explanation':'对比依据'}])
        with patch('newsreader.ai.request_json', return_value=result) as transport:
            response = self.post({'mode':'article','scope':'full','paragraph_index':0,'compare_ids':[2]})
            self.assertEqual(response.status_code, 200)
            sent = json.loads(transport.call_args.args[0]['input'])['materials']
            self.assertEqual(sent[0]['text'], self.article['content'])
            self.assertEqual(sent[1]['text'], other['content'])
        with patch('newsreader.ai.request_json', return_value=self.result('article')) as transport:
            response = self.post({'mode':'article','scope':'paragraph','paragraph_index':0,'compare_ids':[]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(transport.call_args.args[0]['input'])['materials'][0]['text'], self.paragraph)

    def test_validation_prevents_invalid_selections_and_costly_calls(self):
        cases = [
            {'mode':'sentence','paragraph_index':0,'selected_text':'not in the article'},
            {'mode':'sentence','paragraph_index':-1,'selected_text':self.sentence},
            {'mode':'article','scope':'paragraph','paragraph_index':0,'compare_ids':[2]},
            {'mode':'article','scope':'full','paragraph_index':0,'compare_ids':[1]},
            {'mode':'article','scope':'full','paragraph_index':0,'compare_ids':[2,2]},
            {'mode':'article','scope':'full','paragraph_index':0,'compare_ids':[2,3,4,5]},
            {'mode':'sentence','paragraph_index':0,'selected_text':self.sentence,'level':'invalid'},
        ]
        with patch('newsreader.ai.request_json') as transport:
            for case in cases:
                with self.subTest(case=case): self.assertEqual(self.post(case).status_code, 400)
            self.assertEqual(self.post({'mode':'article','scope':'full','compare_ids':[99999]}).status_code, 404)
            transport.assert_not_called()

    def test_no_credentials_and_provider_failure_are_explicit(self):
        self.app.config['AI_API_KEY'] = ''
        self.assertFalse(self.client.get('/api/deep-analysis/status').json['configured'])
        with patch('newsreader.ai.request_json') as transport:
            self.assertEqual(self.post({'mode':'sentence','paragraph_index':0,'selected_text':self.sentence}).status_code, 503)
            transport.assert_not_called()
        self.app.config['AI_API_KEY'] = 'test-token'
        with patch('newsreader.ai.request_json', side_effect=ai.AIError('模型调用失败', 502)):
            response = self.post({'mode':'sentence','paragraph_index':0,'selected_text':self.sentence})
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('test-token', response.get_data(as_text=True))

    def test_deepseek_uses_bounded_non_thinking_response(self):
        self.app.config['AI_BASE_URL'] = 'https://api.deepseek.com'
        with patch('newsreader.ai.request_json', return_value=self.result('sentence')) as transport:
            self.assertEqual(self.post({'mode':'sentence','paragraph_index':0,'selected_text':self.sentence}).status_code, 200)
            self.assertEqual(transport.call_args.args[0]['reasoning'], {'effort':'none'})

    def test_bad_shapes_and_ungrounded_quotes_are_rejected(self):
        bad = self.result('sentence', evidence=[{'article_id':1,'quote':'not an actual quote','explanation':'wrong'}])
        for result in ({}, bad, self.result('sentence', meaning='')):
            with self.subTest(result=result), patch('newsreader.ai.request_json', return_value=result):
                response = self.post({'mode':'sentence','paragraph_index':0,'selected_text':self.sentence})
                self.assertEqual(response.status_code, 502)

    def test_cross_origin_and_content_type_protections_remain(self):
        self.assertEqual(self.client.post('/api/articles/1/deep-analysis', json={}, headers={'Origin':'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/articles/1/deep-analysis', data='test').status_code, 415)

    def test_shared_transport_handles_responses_and_provider_errors(self):
        payload={'model':'test-model','input':'example'}
        config={'base':'https://api.deepseek.com','key':'secret-value'}
        body={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"ok":true}'}]}]}
        opener=MagicMock()
        opener.open.return_value.__enter__.return_value=io.BytesIO(json.dumps(body).encode())
        with patch('newsreader.ai.build_opener',return_value=opener):
            self.assertEqual(ai.request_json(payload,config,timeout=120),{'ok':True})
            request=opener.open.call_args.args[0]
            self.assertEqual(request.full_url,'https://api.deepseek.com/responses')
            self.assertEqual(opener.open.call_args.kwargs['timeout'],120)
            self.assertEqual(json.loads(request.data),payload)
        opener.open.side_effect=HTTPError('https://example.invalid',429,'bad',None,None)
        with patch('newsreader.ai.build_opener',return_value=opener), self.assertRaises(ai.AIError) as error:
            ai.request_json(payload,config)
        self.assertNotIn('secret-value',str(error.exception))


if __name__ == '__main__': unittest.main()
