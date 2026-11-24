"""Export fidelity, safe sharing, Anki identity and opt-in read-only access."""
import csv
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from app import create_app
from newsreader.db import get_db
from newsreader.exports import SUPPORTED
from newsreader.export_formats import safe_url


def create_note_fixture_schema(db):
    """Feature 19 fixture; the exporter never creates or changes this optional table."""
    db.executescript('''CREATE TABLE IF NOT EXISTS knowledge_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE,
        source_key TEXT UNIQUE, kind TEXT NOT NULL,
        article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL,
        article_title TEXT NOT NULL DEFAULT '', anchors TEXT NOT NULL DEFAULT '[]',
        title TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', example TEXT NOT NULL DEFAULT '',
        color TEXT NOT NULL DEFAULT 'yellow', tags TEXT NOT NULL DEFAULT '[]',
        version INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );''')


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='margin-exports-')
        self.app = create_app({'TESTING': True, 'DATABASE': str(Path(self.temp.name)/'exports.sqlite3')})
        self.client = self.app.test_client()
        self.quote = 'A 🌱 quiet journey.'
        self.note = '中文笔记，保留换行\n<script>alert("test")</script>\n/ˈdʒɜːni/ & 学习'
        with self.app.app_context():
            db = get_db()
            create_note_fixture_schema(db)
            db.execute('DELETE FROM articles')
            db.execute('''INSERT INTO articles(id,title,content,source,source_url,category,level,
                difficulty_score,word_count,reading_minutes,created_at,progress,reading_seconds)
                VALUES(101,?,?,?,?,?,?,?,?,?,?,?,?)''',
                ('A city <script>bad()</script>', self.quote+' '+('We learn together. '*30),
                 '示例来源', 'https://example.com/article?q=(reading)&lang=en', '文化', 'B2', 60, 94, 2,
                 '2026-11-01T10:00:00+08:00', 55, 99999))
            for index, mastery in enumerate(('new', 'learning', 'mastered'), 1):
                db.execute('''INSERT INTO vocabulary(id,word,phonetic,meaning,part_of_speech,context,note,article_id,mastery,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''', (index, ['journey', 'garden', 'thoughtful'][index-1], '/ˈdʒɜːni/',
                    '=HYPERLINK("https://example.com","释义")' if index == 1 else '释义 '+str(index),
                    'n.', self.quote, self.note, 101, mastery, f'2026-11-0{index}T08:00:00+08:00'))
            for index, kind in enumerate(('note', 'card', 'highlight', 'expression'), 1):
                anchor = {'paragraph': 0, 'start': 0, 'end': len(self.quote.encode('utf-16-le'))//2, 'quote': self.quote}
                db.execute('''INSERT INTO knowledge_items(id,request_id,kind,article_id,article_title,anchors,
                    title,content,example,tags,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (index, 'test-'+str(index), kind, 101, 'A city <script>bad()</script>', json.dumps([anchor]),
                     kind+' 学习 <img src=x>', self.note, 'A useful example.', '["写作", "focus"]',
                     f'2026-11-0{index}T09:00:00+08:00', '2026-11-05T15:00:00+08:00'))
            db.executemany('INSERT INTO checkins(date,reading_seconds,articles_completed,manual) VALUES(?,?,?,?)',
                           [('2026-10-31', 3000, 7, 0), ('2026-11-01', 60, 1, 0),
                            ('2026-11-02', 120, 0, 1), ('2026-11-04', 30, 1, 0)])
            db.execute('INSERT INTO settings(key,value) VALUES(?,?)', ('private_ai_config', '"test-secret-never-export"'))
            db.commit()

    def tearDown(self):
        self.temp.cleanup()

    def export(self, dataset='vocabulary', fmt='json', **extra):
        return self.client.post('/api/exports/file', json={'dataset': dataset, 'format': fmt, **extra})

    def preview(self, dataset='vocabulary', **extra):
        return self.client.post('/api/exports/preview', json={'dataset': dataset, **extra})

    def assert_ok(self, response):
        self.assertEqual(response.status_code, 200, response.json if response.is_json else response.data[:200])
        return response

    def anki_notes(self, raw, filename):
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            self.assertIn('collection.anki2', package.namelist())
            path = Path(self.temp.name)/filename
            path.write_bytes(package.read('collection.anki2'))
        with closing(sqlite3.connect(path)) as db:
            rows = db.execute('SELECT guid,flds,tags FROM notes ORDER BY guid').fetchall()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cards').fetchone()[0], len(rows))
            return rows

    def test_all_supported_formats_are_downloadable(self):
        for dataset, formats in SUPPORTED.items():
            for fmt in formats:
                with self.subTest(dataset=dataset, format=fmt):
                    response = self.assert_ok(self.export(dataset, fmt))
                    self.assertGreater(len(response.data), 50)
                    self.assertIn('attachment;', response.headers['Content-Disposition'])
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    self.assertIn('X-Export-Count', response.headers)
                    if fmt == 'pdf':
                        self.assertTrue(response.data.startswith(b'%PDF-'))
                    elif fmt in ('xlsx', 'anki', 'obsidian', 'notion'):
                        self.assertTrue(zipfile.is_zipfile(io.BytesIO(response.data)))

    def test_options_navigation_and_assets(self):
        options = self.assert_ok(self.client.get('/api/exports/options')).json
        self.assertEqual(options['counts'], {'notes': 4, 'vocabulary': 3, 'reading_list': 1})
        home = self.client.get('/').text
        self.assertIn('href="#exports"', home)
        for asset in ('exports.js', 'exports.css'):
            self.assertIn('/static/'+asset, home)
            with self.client.get('/static/'+asset) as response:
                self.assertEqual(response.status_code, 200)

    def test_json_preserves_content_and_excludes_private_settings(self):
        token = self.client.post('/api/integrations/token', json={}).json['token']
        for dataset in SUPPORTED:
            response = self.assert_ok(self.export(dataset))
            self.assertNotIn('test-secret-never-export', response.text)
            self.assertNotIn(token, response.text)
            self.assertNotIn('integration_read_token', response.text)
            self.assertEqual(response.json['schema_version'], 1)
        note = self.export('notes').json['items'][0]
        self.assertEqual(note['content'], self.note)
        self.assertEqual(note['quote'], self.quote)
        self.assertEqual(note['anchors'][0]['end'], 19)
        self.assertNotIn('request_id', note)

    def test_filters_and_selected_ids_are_enforced(self):
        result = self.assert_ok(self.preview(filters={'mastery': 'learning'})).json
        self.assertEqual([i['id'] for i in result['items']], [2])
        notes = self.preview('notes', filters={'tag': 'FOCUS', 'kind': 'card', 'article_id': 101}).json
        self.assertEqual([i['id'] for i in notes['items']], [2])
        result = self.export(ids=[3, 1, 1]).json
        self.assertEqual([i['id'] for i in result['items']], [1, 3])
        for extra in ({'ids': []}, {'ids': [999]}, {'ids': [1, 2], 'filters': {'mastery': 'new'}}):
            self.assertEqual(self.export(**extra).status_code, 400)
        result = self.preview(filters={'from': '2026-11-02', 'to': '2026-11-02'}).json
        self.assertEqual([i['id'] for i in result['items']], [2])
        self.assertEqual(self.preview('reading_list', filters={'read_status': 'reading'}).json['count'], 1)
        self.assertEqual(self.preview('reading_list', filters={'read_status': 'completed'}).json['count'], 0)

    def test_search_uses_literals_and_actual_quote_text(self):
        self.assertEqual(self.preview(filters={'query': '%'}).json['count'], 0)
        self.assertEqual(self.preview(filters={'query': "' OR 1=1 --"}).json['count'], 0)
        self.assertEqual(self.preview('notes', filters={'query': self.quote}).json['count'], 4)
        self.assertEqual(self.preview('notes', filters={'query': 'paragraph'}).json['count'], 0)

    def test_pagination_has_no_gaps_or_duplicates(self):
        with self.app.app_context():
            db = get_db()
            db.executemany('INSERT INTO vocabulary(word,meaning,created_at) VALUES(?,?,?)',
                           [(f'word-{n}', '释义', '2026-11-01') for n in range(65)])
            db.commit()
        first = self.preview().json
        self.assertEqual(first['count'], 68)
        self.assertEqual(len(first['items']), 50)
        second = self.preview(after=first['next_cursor']).json
        ids = [i['id'] for i in first['items']+second['items']]
        self.assertEqual(len(set(ids)), 68)
        self.assertEqual(ids, sorted(ids))
        self.assertIsNone(second['next_cursor'])
        self.assertEqual(self.preview(after=9999).json['items'], [])

    def test_empty_and_bounded_exports_fail_without_partial_files(self):
        self.assertEqual(self.preview(filters={'query': 'nonexistent'}).json['count'], 0)
        self.assertEqual(self.export(filters={'query': 'nonexistent'}).status_code, 400)
        with patch('newsreader.exports.MAX_RECORDS', 2):
            response = self.export()
            self.assertEqual(response.status_code, 400)
            self.assertIn('5000', response.json['error'])
        with patch('newsreader.exports.MAX_TEXT_BYTES', 100):
            self.assertEqual(self.export().status_code, 400)

    def test_invalid_requests_are_rejected(self):
        invalid = [{'dataset': []}, {'format': []}, {'format': 'html'}, {'ids': [True]}, {'ids': [0]},
                   {'ids': '1'}, {'after': -1}, {'after': True}, {'filters': []}, {'extra': 1},
                   {'filters': {'query': []}}, {'filters': {'article_id': True}},
                   {'filters': {'from': False}}, {'filters': {'to': '2026-02-30'}},
                   {'filters': {'from': '2026-11-02', 'to': '2026-11-01'}},
                   {'filters': {'kind': 'card'}}, {'filters': {'unknown': 'value'}}, {'deck_name': 'a\nb'}]
        for extra in invalid:
            with self.subTest(extra=extra):
                self.assertEqual(self.client.post('/api/exports/file', json={'dataset': 'vocabulary', **extra}).status_code, 400)
        self.assertEqual(self.client.post('/api/exports/file', json=[]).status_code, 400)
        self.assertEqual(self.client.post('/api/exports/file', data='{}').status_code, 415)
        self.assertEqual(self.client.post('/api/integrations/token', json={}, headers={'Origin': 'https://other.example'}).status_code, 403)

    def test_csv_is_utf8_and_formula_safe_with_embedded_newlines(self):
        response = self.export(fmt='csv')
        self.assertTrue(response.data.startswith(b'\xef\xbb\xbf'))
        rows = list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig'), newline='')))
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0]['释义'].startswith("'=HYPERLINK"))
        self.assertEqual(rows[0]['学习笔记'], self.note)
        with self.app.app_context():
            db = get_db()
            db.execute('UPDATE vocabulary SET note=? WHERE id=1', (' \t@SUM(1,2)',))
            db.commit()
        rows = list(csv.DictReader(io.StringIO(self.export(fmt='csv').data.decode('utf-8-sig'))))
        self.assertEqual(rows[0]['学习笔记'], "' \t@SUM(1,2)")

    def test_excel_keeps_literal_text_dates_and_complete_notes(self):
        book = load_workbook(io.BytesIO(self.export(fmt='xlsx').data))
        sheet = book.active
        self.assertEqual(sheet['D4'].value, '=HYPERLINK("https://example.com","释义")')
        self.assertEqual(sheet['D4'].data_type, 's')
        self.assertEqual(sheet['G4'].value, self.note)
        self.assertIsInstance(sheet['J4'].value, datetime)
        self.assertEqual(sheet.freeze_panes, 'A4')
        self.assertFalse(sheet.sheet_view.showGridLines)
        self.assertGreater(sheet.row_dimensions[4].height, 28)
        self.assertTrue(all(c.data_type != 'f' for row in sheet for c in row))
        book.close()
        report = load_workbook(io.BytesIO(self.export('report', 'xlsx').data))
        self.assertEqual(report.sheetnames, ['报告概要', '每日学习'])
        self.assertIsInstance(report['每日学习']['A4'].value, datetime)
        report.close()

    def test_markdown_and_note_packages_preserve_links_and_escape_markup(self):
        markdown = self.export('notes', 'md').text
        self.assertIn('&lt;script&gt;', markdown)
        self.assertNotIn('<script>', markdown)
        self.assertIn('A 🌱 quiet journey.', markdown)
        for target in ('obsidian', 'notion'):
            with self.subTest(target=target), zipfile.ZipFile(io.BytesIO(self.export('notes', target).data)) as package:
                names = package.namelist()
                self.assertIn('index.md', names)
                self.assertIn('sources/article-101.md', names)
                self.assertTrue(all(not n.startswith(('/', '\\')) and '..' not in n.split('/') for n in names))
                note = package.read('notes/1.md').decode('utf-8')
                source = package.read('sources/article-101.md').decode('utf-8')
                self.assertIn('../sources/article-101.md', note)
                self.assertIn('../notes/1.md', source)
                self.assertIn('https://example.com/article', source)
                self.assertIn('A 🌱 quiet journey.', source)
                body = note.split('---\n', 2)[-1] if target == 'obsidian' else note
                self.assertNotIn('<img src=x>', body)
                if target == 'obsidian':
                    self.assertTrue(note.startswith('---\ntitle: '))
                    self.assertIn('#^note-1-p-1-1', note)
                    self.assertIn('\n^note-1-p-1-1', source)
                else:
                    rows = list(csv.DictReader(io.StringIO(package.read('records.csv').decode('utf-8-sig'))))
                    self.assertEqual(len(rows), 4)
                    self.assertEqual(rows[0]['正文 / 卡片答案'], self.note)

    def test_deleted_sources_still_export_saved_excerpts(self):
        self.assertEqual(self.client.delete('/api/articles/101', json={}).status_code, 200)
        note = self.export('notes').json['items'][0]
        self.assertEqual(note['source_state'], 'deleted')
        self.assertEqual(note['quote'], self.quote)
        self.assertIsNone(note['article_id'])
        self.assertEqual(note['source_url'], '')
        self.assertIn('原文已删除', self.export('notes', 'md').text)
        self.assertEqual(self.export().json['count'], 3)

    def test_changed_sources_preserve_excerpts_and_detect_utf16_positions(self):
        self.assertEqual(self.export('notes').json['items'][0]['source_state'], 'available')
        with self.app.app_context():
            db = get_db()
            db.execute('UPDATE articles SET content=? WHERE id=101', ('The text has changed.',))
            db.commit()
        note = self.export('notes').json['items'][0]
        self.assertEqual(note['source_state'], 'changed')
        self.assertEqual(note['quote'], self.quote)

    def test_base_install_without_knowledge_module_has_empty_note_exports(self):
        with self.app.app_context():
            db = get_db()
            db.execute('DROP TABLE knowledge_items')
            db.commit()
        self.assertEqual(self.client.get('/api/exports/options').json['counts']['notes'], 0)
        self.assertEqual(self.preview('notes').json['items'], [])
        self.assertEqual(self.export('notes').status_code, 400)
        self.assertEqual(self.export('report').json['report']['metrics']['added_notes'], 0)
        self.assertEqual(self.export().json['count'], 3)
        token = self.client.post('/api/integrations/token', json={}).json['token']
        response = self.client.get('/api/integrations/v1/notes', headers={'Authorization': 'Bearer '+token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['items'], [])

    def test_anki_stable_identity_and_card_filter(self):
        first = self.anki_notes(self.export(fmt='anki').data, 'first.sqlite3')
        self.assertEqual(len(first), 3)
        self.assertTrue(any('&lt;script&gt;' in row[1] for row in first))
        self.assertTrue(all(len(row[1].split('\x1f')) == 3 for row in first))
        with self.app.app_context():
            db = get_db()
            db.execute('UPDATE vocabulary SET meaning=? WHERE id=1', ('新释义',))
            db.commit()
        second = self.anki_notes(self.export(fmt='anki').data, 'second.sqlite3')
        self.assertEqual([r[0] for r in first], [r[0] for r in second])
        self.assertNotEqual([r[1] for r in first], [r[1] for r in second])
        cards = self.anki_notes(self.export('notes', 'anki').data, 'cards.sqlite3')
        self.assertEqual(len(cards), 1)
        self.assertIn('card 学习', cards[0][1])
        self.assertIn('写作', cards[0][2])
        self.assertNotIn(cards[0][0], [r[0] for r in first])
        self.assertEqual(self.export('notes', 'anki', filters={'kind': 'note'}).status_code, 400)
        self.assertEqual(self.export('notes', 'anki', ids=[1, 2]).status_code, 400)

    def test_reading_list_contains_only_shareable_metadata(self):
        item = self.export('reading_list').json['items'][0]
        self.assertEqual(set(item), {'id', 'title', 'subtitle', 'source', 'category', 'level', 'word_count', 'reading_minutes', 'source_url'})
        raw = self.export('reading_list', 'html').text
        self.assertIn('&lt;script&gt;', raw)
        self.assertNotIn('<script>', raw)
        self.assertNotIn(self.quote, raw)
        self.assertNotIn('99999', raw)
        self.assertIn('rel="noopener noreferrer"', raw)
        self.assertIn('Content-Security-Policy', raw)
        with self.app.app_context():
            db = get_db()
            db.execute('UPDATE articles SET source_url=? WHERE id=101', ('javascript:alert(1)',))
            db.commit()
        self.assertEqual(self.export('reading_list').json['items'][0]['source_url'], '')
        self.assertNotIn('href=', self.export('reading_list', 'html').text)

    def test_url_validation_removes_unsafe_schemes_credentials_and_controls(self):
        for url in ('javascript:alert(1)', 'file:///tmp/key', '//example.com',
                    'https://user:password@example.com', 'https://example.com/\nnext', 'https:\\example.com', 'https://[bad'):
            self.assertEqual(safe_url(url), '')
        self.assertEqual(safe_url('https://example.com/a?q=1&b=2'), 'https://example.com/a?q=1&b=2')

    def test_report_uses_period_activity_and_labels_current_mastery(self):
        response = self.export('report', filters={'from': '2026-11-01', 'to': '2026-11-04'})
        r = self.assert_ok(response).json['report']
        self.assertEqual(r['metrics'], {'reading_seconds': 210, 'checked_days': 3, 'completed_count': 2,
                                       'longest_streak': 2, 'added_vocabulary': 3, 'added_notes': 4, 'current_vocabulary': 3})
        self.assertEqual(r['daily'][2]['reading_seconds'], 0)
        self.assertFalse(r['daily'][2]['checked_in'])
        self.assertTrue(r['daily'][1]['manual'])
        self.assertEqual(r['mastery'], {'new': 1, 'learning': 1, 'mastered': 1})
        self.assertIn('导出时的现状', r['definitions'])
        self.assertEqual(r['period']['timezone'], 'Asia/Shanghai')
        self.assertEqual(self.preview('report', filters={'from': '2020-01-01', 'to': '2022-01-01'}).status_code, 400)
        self.assertEqual(self.preview('report', filters={'from': '2024-01-01', 'to': '2024-12-31'}).json['count'], 366)
        self.assertEqual(self.preview('report', filters={'from': '9999-12-31', 'to': '9999-12-31'}).json['count'], 1)
        self.assertEqual(self.preview('report', ids=[1]).status_code, 400)

    def test_api_is_opt_in_token_is_hashed_and_rotation_revokes_old_token(self):
        url = '/api/integrations/v1/vocabulary'
        self.assertFalse(self.client.get('/api/integrations/token').json['enabled'])
        self.assertEqual(self.client.get(url).status_code, 401)
        first = self.client.post('/api/integrations/token', json={}).json['token']
        headers = {'Authorization': 'Bearer '+first}
        self.assertEqual(self.client.get(url, headers=headers).status_code, 200)
        self.assertNotIn('token', self.client.get('/api/integrations/token').json)
        with self.app.app_context():
            value = get_db().execute("SELECT value FROM settings WHERE key='integration_read_token'").fetchone()[0]
            self.assertNotIn(first, value)
            self.assertEqual(len(json.loads(value)['hash']), 64)
        second = self.client.post('/api/integrations/token', json={}).json['token']
        self.assertNotEqual(first, second)
        self.assertEqual(self.client.get(url, headers=headers).status_code, 401)
        self.assertEqual(self.client.get(url, headers={'Authorization': 'Bearer '+second}).status_code, 200)
        self.client.delete('/api/integrations/token', json={})
        self.assertEqual(self.client.get(url, headers={'Authorization': 'Bearer '+second}).status_code, 401)
        self.assertFalse(self.client.get('/api/integrations/token').json['enabled'])

    def test_api_filters_pagination_schema_and_read_only_methods(self):
        token = self.client.post('/api/integrations/token', json={}).json['token']
        headers = {'Authorization': 'Bearer '+token, 'Origin': 'https://other.example'}
        path = '/api/integrations/v1/'
        first = self.client.get(path+'vocabulary?limit=2', headers=headers)
        self.assert_ok(first)
        self.assertNotIn('Access-Control-Allow-Origin', first.headers)
        self.assertEqual(first.headers['Cache-Control'], 'no-store')
        self.assertEqual(len(first.json['items']), 2)
        second = self.client.get(path+'vocabulary?limit=2&after='+str(first.json['next_cursor']), headers=headers).json
        self.assertEqual([i['id'] for i in second['items']], [3])
        self.assertIsNone(second['next_cursor'])
        self.assertEqual(self.client.get(path+'notes?kind=card&article_id=101', headers=headers).json['count'], 1)
        self.assertEqual(self.client.get(path+'reading-list?read_status=reading', headers=headers).json['count'], 1)
        self.assertEqual(self.client.get(path+'report?from=2026-11-01&to=2026-11-04', headers=headers).json['count'], 4)
        schema = self.client.get(path+'schema', headers=headers).json
        self.assertTrue(schema['read_only'])
        self.assertEqual(schema['max_page_size'], 200)
        self.assertEqual(self.client.get(path+'settings', headers=headers).status_code, 404)
        for query in ('limit=0', 'limit=201', 'after=-1', 'after=true', 'limit=abc', 'article_id=x', 'secret=1', 'mastery=bad'):
            self.assertEqual(self.client.get(path+'vocabulary?'+query, headers=headers).status_code, 400)
        for method in ('post', 'patch', 'delete', 'put'):
            self.assertEqual(getattr(self.client, method)(path+'vocabulary', json={}, headers={'Authorization': 'Bearer '+token}).status_code, 405)


if __name__ == '__main__':
    unittest.main()
