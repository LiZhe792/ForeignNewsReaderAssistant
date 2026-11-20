"""AI transport, source grounding, resume semantics and versioned user edits."""
import copy
import io
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from app import create_app
from newsreader import ai
from newsreader.assistance import MAX_UNIT, digest, paragraphs, spans
from newsreader.db import get_db, init_db
from ai_fixtures import context_result, translation_result


class AssistanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.config = {'TESTING': True, 'DATABASE': str(Path(self.temporary.name) / 'study.sqlite3'),
                       'AI_API_KEY': 'synthetic-test-only-key', 'AI_MODEL': 'test-model'}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        content = ('The bank of the river was quiet. It gave readers a place to rest and think. '
                   'They chose this bank because the path followed the water.\n\n'
                   'The project turned a new page for the community. Its future depended on careful planning and cooperation. '
                   'We take into account the needs of the readers and preserve the original meaning.')
        response = self.client.post('/api/articles', json={'title': 'River reading', 'content': content})
        self.assertEqual(response.status_code, 201)
        self.id = response.json['id']
        self.url = f'/api/articles/{self.id}/assistance'
        self.manifest = self.client.get(self.url).json

    def tearDown(self):
        self.temporary.cleanup()

    def payload(self, kind='context', **patches):
        span = self.manifest['paragraphs'][0]['sentences'][0]
        return {'kind': kind, 'scope': 'sentence', 'paragraph_index': 0, 'article_hash': self.manifest['article_hash'],
                'level': 'intermediate', **span, **patches}

    def create(self, kind='context', **patches):
        data = self.payload(kind, **patches)
        selected = self.manifest['paragraphs'][data['paragraph_index']]['text'][data['start']:data['end']]
        result = context_result(selected) if kind == 'context' else translation_result(selected)
        with patch('newsreader.ai.request_json', return_value=result) as network:
            response = self.client.post(self.url, json=data)
        self.assertIn(response.status_code, (200, 201), response.json)
        return response.json['result'], network

    def test_unconfigured_service_does_not_send_network_requests_or_fake_a_result(self):
        self.app.config['AI_API_KEY'] = ''
        with patch('newsreader.ai.request_json') as network:
            response = self.client.post(self.url, json=self.payload())
        self.assertEqual(response.status_code, 503)
        self.assertIn('尚未配置', response.json['error'])
        network.assert_not_called()
        self.assertEqual(self.client.get(self.url).json['results'], [])

    def test_configuration_status_never_discloses_the_key(self):
        status = self.client.get('/api/assistance/status')
        self.assertTrue(status.json['configured'])
        self.assertNotIn(self.config['AI_API_KEY'], status.get_data(as_text=True))
        for base in ('http://example.com/v1', 'https://name:secret@example.com/v1', 'https://example.com/v1?key=x', 'https://[broken'):
            self.app.config['AI_BASE_URL'] = base
            self.assertFalse(self.client.get('/api/assistance/status').json['configured'])

    def test_source_ranges_and_manifest_match_original_paragraphs(self):
        self.assertEqual(self.manifest['paragraphs'][0]['text'], paragraphs(self.client.get(f'/api/articles/{self.id}').json['content'])[0])
        for part in self.manifest['paragraphs']:
            for span in part['sentences'] + part['units']:
                self.assertTrue(part['text'][span['start']:span['end']].strip())

    def test_context_outputs_cover_meanings_phrases_register_references_and_examples(self):
        row, network = self.create()
        self.assertEqual(row['kind'], 'context')
        self.assertEqual(set(row['payload']), set(ai.CONTEXT_SCHEMA['properties']))
        self.assertTrue(row['payload']['examples'])
        self.assertTrue(row['payload']['references'])
        call = network.call_args.args[0]
        supplied = json.loads(call['input'])
        self.assertEqual(supplied['selected_text'], row['source_text'])
        self.assertIn('river', supplied['neighboring_context'])
        self.assertFalse(call['store'])
        self.assertTrue(call['text']['format']['strict'])
        self.assertEqual(call['text']['format']['type'], 'json_schema')
        self.assertIn('untrusted', call['instructions'])

    def test_each_learning_level_is_in_the_prompt_and_has_a_distinct_cache(self):
        identifiers = []
        for level in ai.LEVELS:
            row, network = self.create(level=level)
            self.assertEqual(json.loads(network.call_args.args[0]['input'])['learning_level'], level)
            identifiers.append(row['id'])
        self.assertEqual(len(set(identifiers)), 3)
        self.assertEqual(self.client.patch('/api/settings', json={'ai_level': 'advanced'}).json['ai_level'], 'advanced')

    def test_same_request_uses_cache_without_regenerating(self):
        first, _ = self.create()
        second, network = self.create()
        self.assertEqual(first['id'], second['id'])
        network.assert_not_called()

    def test_different_occurrences_keep_separate_context_results(self):
        part = self.manifest['paragraphs'][0]['text']
        first, _ = self.create(scope='word', start=part.index('bank'), end=part.index('bank') + 4)
        later = part.rindex('bank')
        second, _ = self.create(scope='word', start=later, end=later + 4)
        self.assertNotEqual(first['id'], second['id'])

    def test_translation_keeps_literal_natural_and_expression_explanations(self):
        row, _ = self.create('translation')
        self.assertTrue(row['payload']['literal'])
        self.assertTrue(row['payload']['natural'])
        self.assertTrue(row['payload']['comparison'])
        self.assertTrue(row['payload']['expressions'])
        self.assertEqual(row['payload'], row['original'])

    def test_favorites_and_user_revisions_survive_restart(self):
        row, _ = self.create('translation')
        response = self.client.patch(self.url + '/' + str(row['id']), json={'version': 1, 'literal': '我的直译', 'natural': '我的意译', 'favorite': True, 'note': '修订依据'})
        self.assertEqual(response.status_code, 200)
        restarted = create_app(self.config).test_client()
        saved = restarted.get(self.url).json['results'][0]
        self.assertEqual((saved['payload']['literal'], saved['payload']['natural'], saved['note']), ('我的直译', '我的意译', '修订依据'))
        self.assertTrue(saved['favorite'])
        self.assertTrue(saved['edited'])
        self.assertEqual(saved['original'], row['payload'])

    def test_regeneration_does_not_overwrite_a_user_revision(self):
        row, _ = self.create('translation')
        self.client.patch(self.url + '/' + str(row['id']), json={'version': 1, 'natural': '保留我的译文'})
        again, network = self.create('translation')
        network.assert_not_called()
        self.assertEqual(again['payload']['natural'], '保留我的译文')

    def test_version_conflict_returns_latest_record_without_overwriting_it(self):
        row, _ = self.create('translation')
        path = self.url + '/' + str(row['id'])
        self.assertEqual(self.client.patch(path, json={'version': 1, 'favorite': True}).status_code, 200)
        stale = self.client.patch(path, json={'version': 1, 'natural': '过期输入'})
        self.assertEqual(stale.status_code, 409)
        self.assertTrue(stale.json['current']['favorite'])
        self.assertNotEqual(stale.json['current']['payload']['natural'], '过期输入')

    def test_invalid_revision_patch_rolls_back_all_fields(self):
        row, _ = self.create('translation')
        path = self.url + '/' + str(row['id'])
        for data in ({'version': 1, 'favorite': 'yes'}, {'version': True, 'natural': 'x'},
                     {'version': 1, 'favorite': True, 'natural': ''}, {'version': 1, 'note': 7},
                     {'version': 1, 'natural': 'a' * 8001}, {'version': 1, 'unknown': True}):
            self.assertEqual(self.client.patch(path, json=data).status_code, 400, data)
        self.assertFalse(self.client.get(self.url).json['results'][0]['favorite'])

    def test_results_cannot_be_edited_through_another_article(self):
        row, _ = self.create('translation')
        # Import time may cross a one-second timestamp boundary; choose by identity.
        other = next(article['id'] for article in self.client.get('/api/articles').json['articles'] if article['id'] != self.id)
        self.assertNotEqual(other, self.id)
        self.assertEqual(self.client.patch(f'/api/articles/{other}/assistance/{row["id"]}', json={'version': 1, 'favorite': True}).status_code, 404)

    def test_context_is_not_misrepresented_as_an_editable_translation(self):
        row, _ = self.create()
        self.assertEqual(self.client.patch(self.url + '/' + str(row['id']), json={'version': 1, 'natural': 'x'}).status_code, 400)

    def test_source_hash_changes_invalidate_cached_context(self):
        self.create()
        with self.app.app_context():
            db = get_db()
            db.execute('UPDATE articles SET content=content || ? WHERE id=?', (' More context.', self.id))
            db.commit()
        self.assertTrue(self.client.get(self.url).json['results'][0]['stale'])
        self.assertEqual(self.client.post(self.url, json=self.payload()).status_code, 409)

    def test_source_changed_during_generation_does_not_save_a_stale_result(self):
        def change(*args):
            db = get_db()
            db.execute('UPDATE articles SET content=content || ? WHERE id=?', (' Changed.', self.id))
            db.commit()
            return translation_result('source')
        with patch('newsreader.ai.generate', side_effect=change):
            response = self.client.post(self.url, json=self.payload('translation'))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get(self.url).json['results'], [])

    def test_source_deleted_during_generation_is_not_resurrected(self):
        def delete(*args):
            db = get_db()
            db.execute('DELETE FROM articles WHERE id=?', (self.id,))
            db.commit()
            return translation_result('source')
        with patch('newsreader.ai.generate', side_effect=delete):
            self.assertEqual(self.client.post(self.url, json=self.payload('translation')).status_code, 404)

    def test_article_deletion_cascades_only_its_learning_results(self):
        self.create('translation')
        self.client.delete(f'/api/articles/{self.id}', json={})
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT count(*) FROM study_results WHERE article_id=?', (self.id,)).fetchone()[0], 0)

    def test_full_article_resume_preserves_completed_units_on_failure(self):
        first, _ = self.create('translation', scope='paragraph', **self.manifest['paragraphs'][0]['units'][0])
        second_unit = self.manifest['paragraphs'][1]['units'][0]
        with patch('newsreader.ai.request_json', side_effect=ai.AIError('network unavailable')):
            failed = self.client.post(self.url, json=self.payload('translation', scope='paragraph', paragraph_index=1, **second_unit))
        self.assertEqual(failed.status_code, 502)
        self.assertEqual([row['id'] for row in self.client.get(self.url).json['results']], [first['id']])
        self.create('translation', scope='paragraph', paragraph_index=1, **second_unit)
        self.assertEqual(len(self.client.get(self.url).json['results']), 2)

    def test_long_paragraph_units_preserve_every_non_whitespace_character(self):
        text = '😀 A long English sentence with a river bank. ' * 170
        units = list(spans(text))
        self.assertGreater(len(units), 1)
        joined = ''.join(text[unit['start']:unit['end']] for unit in units)
        self.assertEqual(re.sub(r'\s', '', joined), re.sub(r'\s', '', text))
        self.assertTrue(all(unit['end'] - unit['start'] <= MAX_UNIT for unit in units))

    def test_invalid_selections_are_rejected_before_network_access(self):
        patches = [{'start': -1}, {'start': True}, {'end': 10**100}, {'start': 7, 'end': 7},
                   {'paragraph_index': -1}, {'paragraph_index': True}, {'scope': 'full'},
                   {'kind': 'arbitrary'}, {'level': 'unknown'}, {'text': 'injected source'}]
        with patch('newsreader.ai.request_json') as network:
            for item in patches:
                self.assertEqual(self.client.post(self.url, json=self.payload(**item)).status_code, 400, item)
        network.assert_not_called()

    def test_unicode_offsets_are_code_points_not_utf16_units(self):
        with self.app.app_context():
            db = get_db()
            db.execute('UPDATE articles SET content=? WHERE id=?', ('😀 bank by the water.', self.id))
            db.commit()
        self.manifest = self.client.get(self.url).json
        row, _ = self.create(scope='word', start=2, end=6)
        self.assertEqual(row['source_text'], 'bank')

    def test_different_model_or_provider_has_a_distinct_cache(self):
        first, _ = self.create('translation')
        self.app.config['AI_MODEL'] = 'another-model'
        second, network = self.create('translation')
        self.assertEqual(network.call_count, 1)
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotEqual(first['profile'], second['profile'])

    def test_saved_result_is_available_with_key_removed(self):
        row, _ = self.create('translation')
        self.app.config['AI_API_KEY'] = ''
        again, network = self.create('translation')
        network.assert_not_called()
        self.assertEqual(again['id'], row['id'])

    def test_duplicate_concurrent_generation_does_not_issue_a_second_model_request(self):
        entered, release = threading.Event(), threading.Event()
        result = []
        def slow(*args):
            entered.set()
            release.wait(5)
            return translation_result('controlled')
        def work():
            result.append(self.app.test_client().post(self.url, json=self.payload('translation')))
        with patch('newsreader.ai.generate', side_effect=slow) as network:
            worker = threading.Thread(target=work)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.client.post(self.url, json=self.payload('translation')).status_code, 409)
            finally:
                release.set()
                worker.join(5)
        self.assertEqual(result[0].status_code, 201)
        self.assertEqual(network.call_count, 1)

    def test_failures_are_not_cached_and_can_be_retried(self):
        with patch('newsreader.ai.request_json', side_effect=ai.AIError('retry')):
            self.assertEqual(self.client.post(self.url, json=self.payload()).status_code, 502)
        self.assertEqual(len(self.client.get(self.url).json['results']), 0)
        self.create()

    def test_malformed_or_unsubstantiated_model_outputs_are_not_saved(self):
        valid = context_result('The bank of the river was quiet.')
        cases = [None, {}, {**valid, 'contextual_meaning': 7}, {**valid, 'examples': []},
                 {**valid, 'evidence': 'This quotation is not in the article.'},
                 {**valid, 'references': [{'expression': 'it', 'referent': 'x', 'evidence': 'invented quote'}]},
                 {**valid, 'register_tone': 'x' * 8001}]
        for result in cases:
            with patch('newsreader.ai.request_json', return_value=result):
                self.assertEqual(self.client.post(self.url, json=self.payload()).status_code, 502)
        self.assertEqual(self.client.get(self.url).json['results'], [])

    def test_http_transport_extracts_completed_structured_output(self):
        result = context_result('bank')
        envelope = {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(result)}]}]}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(envelope).encode()
        opener = MagicMock()
        opener.open.return_value = response
        with self.app.app_context(), patch('newsreader.ai.build_opener', return_value=opener):
            config = ai.configuration()
            self.assertEqual(ai.request_json({'test': True}, config), result)
        request = opener.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith('/v1/responses'))
        self.assertEqual(request.get_method(), 'POST')

    def test_transport_errors_are_sanitized_and_do_not_leak_provider_content(self):
        for code in (401, 403, 404, 429, 500, 302):
            error = HTTPError('https://example.org/?secret=value', code, 'private error', {}, io.BytesIO(b'secret provider body'))
            opener = MagicMock()
            opener.open.side_effect = error
            with self.app.app_context(), patch('newsreader.ai.build_opener', return_value=opener):
                with self.assertRaises(ai.AIError) as caught:
                    ai.request_json({}, ai.configuration())
                self.assertNotIn('secret', str(caught.exception))
                self.assertNotIn(self.config['AI_API_KEY'], str(caught.exception))

    def test_refusal_incomplete_oversized_and_invalid_json_responses_are_handled(self):
        cases = [b'not json', b' ' * 160001, b'{}', b'{"status":"incomplete"}',
                 json.dumps({'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal'}]}]}).encode(),
                 json.dumps({'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'invalid'}]}]}).encode()]
        for raw in cases:
            response, opener = MagicMock(), MagicMock()
            response.__enter__.return_value.read.return_value = raw
            opener.open.return_value = response
            with self.app.app_context(), patch('newsreader.ai.build_opener', return_value=opener):
                with self.assertRaises(ai.AIError):
                    ai.request_json({}, ai.configuration())

    def test_new_schema_upgrade_preserves_original_reading_data(self):
        self.client.post('/api/vocabulary', json={'word': 'bank', 'meaning': '河岸', 'article_id': self.id})
        with self.app.app_context():
            db = get_db()
            db.execute('DROP TABLE study_results')
            db.commit()
            init_db()
        self.assertEqual(self.client.get('/api/vocabulary').json['words'][0]['meaning'], '河岸')
        self.assertEqual(self.client.get(self.url).json['results'], [])
