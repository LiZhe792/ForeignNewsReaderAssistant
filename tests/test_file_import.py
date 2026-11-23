import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import pymupdf

from app import create_app
from newsreader.file_import import extract_file


TEXT = "The world is full of stories and every reader can discover something new. " * 5


def zipped(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for name, content in files.items():
            z.writestr(name, content)
    return out.getvalue()


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = create_app({'TESTING': True, 'DATABASE': str(Path(self.temp.name) / 'test.sqlite3')}).test_client()

    def tearDown(self):
        self.temp.cleanup()

    def preview(self, name, data):
        return self.client.post('/api/import/preview', data={'file': (io.BytesIO(data), name)}, content_type='multipart/form-data')

    def test_text_markdown_html(self):
        for name, raw in [('story.txt', TEXT), ('story.md', '# Heading\n\n' + TEXT), ('story.html', '<html><nav>ignore me</nav><article><p>' + TEXT + '</p></article><script>bad</script></html>')]:
            with self.subTest(name=name):
                response = self.preview(name, raw.encode())
                self.assertEqual(response.status_code, 200)
                self.assertIn('every reader', response.json['content'])
                self.assertNotIn('ignore me', response.json['content'])
                self.assertNotIn('bad', response.json['content'])
        self.assertEqual(len(self.client.get('/api/articles').json['articles']), 5)

    def test_docx_and_epub_spine_order(self):
        docx = zipped({'word/document.xml': '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + TEXT + '</w:t></w:r></w:p></w:body></w:document>'})
        self.assertIn('every reader', self.preview('story.docx', docx).json['content'])
        epub = zipped({
            'META-INF/container.xml': '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>',
            'OPS/book.opf': '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/><item id="b" href="b.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>',
            'OPS/a.xhtml': '<html><body><p>First chapter</p></body></html>',
            'OPS/b.xhtml': '<html><body><p>Second chapter</p></body></html>',
        })
        result = self.preview('book.epub', epub)
        self.assertEqual(result.status_code, 200)
        self.assertLess(result.json['content'].index('Second'), result.json['content'].index('First'))

    def test_pdf_text_and_scanned_page(self):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(40, 40, 500, 500), TEXT, fontsize=12)
        doc.new_page()
        raw = doc.tobytes()
        doc.close()
        with patch('newsreader.file_import.recognize', return_value='Recognized scan text') as ocr:
            result = self.preview('story.pdf', raw)
        self.assertEqual(result.status_code, 200)
        self.assertIn('every reader', result.json['content'])
        self.assertIn('Recognized scan text', result.json['content'])
        self.assertEqual(result.json['ocr_pages'], 1)
        self.assertEqual(ocr.call_count, 1)

    def test_preview_can_be_corrected_before_saving(self):
        result = self.preview('draft.txt', TEXT.encode()).json
        result['title'] = 'Corrected title'
        result['content'] = result['content'].replace('stories', 'articles')
        saved = self.client.post('/api/articles', json=result)
        self.assertEqual(saved.status_code, 201)
        article = self.client.get('/api/articles/' + str(saved.json['id'])).json
        self.assertEqual(article['title'], 'Corrected title')
        self.assertIn('articles', article['content'])

    def test_bad_files_and_cross_origin_are_rejected(self):
        for name, raw in [('bad.docx', b'not a zip'), ('bad.pdf', b'not pdf'), ('bad.exe', b'bad'), ('empty.txt', b'')]:
            with self.subTest(name=name):
                self.assertEqual(self.preview(name, raw).status_code, 400)
        response = self.client.post('/api/import/preview', data={'file': (io.BytesIO(b'text'), 'test.txt')}, headers={'Origin': 'https://example.com'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post('/api/import/preview', json={}).status_code, 415)


if __name__ == '__main__':
    unittest.main()
