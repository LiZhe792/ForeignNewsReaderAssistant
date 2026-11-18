"""Deferred words survive interruptions without corrupting vocabulary or progress."""

import json
import tempfile
import unittest
from pathlib import Path

from app import create_app
from newsreader.db import get_db


class ReadingFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"TESTING": True, "DATABASE": str(Path(self.temp.name) / "flow.sqlite3")}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.content = ("A journey through a quiet city gives every reader time to discover new ideas. " * 3 +
                        "\n\nThe journey continues into a garden where people learn together and enjoy the afternoon. " * 2)
        self.article_id = self.client.post("/api/articles", json={"title": "Reading flow", "content": self.content}).json["id"]
        self.url = f"/api/articles/{self.article_id}/pending-words"

    def tearDown(self):
        self.temp.cleanup()

    def defer(self, word="journey", index=0):
        response = self.client.post(self.url, json={"word": word, "paragraph_index": index})
        self.assertIn(response.status_code, (200, 201))
        return response.json["word"]

    def test_pending_persists_with_trusted_context_and_no_vocabulary_side_effect(self):
        before = self.client.get("/api/vocabulary").json
        entry = self.defer()
        self.assertEqual(entry["article_id"], self.article_id)
        self.assertIn("journey", entry["context"])
        restarted = create_app(self.config).test_client()
        stored = restarted.get(self.url).json["words"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["id"], entry["id"])
        self.assertEqual(restarted.get("/api/vocabulary").json, before)

    def test_duplicate_is_case_insensitive_and_scoped_to_paragraph(self):
        first = self.defer("Journey")
        duplicate = self.client.post(self.url, json={"word": "JOURNEY", "paragraph_index": 0, "context": "untrusted"})
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.json["duplicate"])
        self.assertEqual(duplicate.json["word"]["id"], first["id"])
        self.assertNotEqual(duplicate.json["word"]["context"], "untrusted")
        second = self.defer(index=1)
        self.assertNotEqual(first["id"], second["id"])

    def test_manual_words_have_no_invented_context(self):
        entry = self.defer("unknown", -1)
        self.assertEqual(entry["context"], "")
        self.assertEqual(self.defer("Unknown", -1)["id"], entry["id"])

    def test_invalid_words_and_paragraphs_cannot_be_saved(self):
        cases = [[], None, {"word": "one two three four five six seven"}, {"word": "<script>"}, {"word": True},
                 {"word": "x" * 61}, {"word": "missing", "paragraph_index": 0}]
        cases.extend({"word": "journey", "paragraph_index": index} for index in [True, -2, 99, "0", .5])
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(self.client.post(self.url, data=json.dumps(case), content_type="application/json").status_code, 400)
        self.assertEqual(self.client.get(self.url).json["words"], [])

    def test_collection_is_atomic_and_preserves_original_context(self):
        entry = self.defer()
        endpoint = f"{self.url}/{entry['id']}/collect"
        self.assertEqual(self.client.post(endpoint, json={"meaning": ""}).status_code, 400)
        self.assertEqual(len(self.client.get(self.url).json["words"]), 1)
        response = self.client.post(endpoint, json={"meaning": "旅程", "phonetic": "/ˈdʒɜːni/"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["duplicate"])
        words = self.client.get("/api/vocabulary").json["words"]
        saved = next(word for word in words if word["id"] == response.json["id"])
        self.assertEqual(saved["context"], entry["context"])
        self.assertEqual(saved["article_id"], self.article_id)
        self.assertEqual(saved["meaning"], "旅程")
        self.assertEqual(self.client.get(self.url).json["words"], [])

    def test_collecting_existing_mastered_word_keeps_meaning_note_and_mastery(self):
        word_id = self.client.post("/api/vocabulary", json={"word": "journey", "meaning": "原释义", "note": "我的笔记"}).json["id"]
        self.client.patch(f"/api/vocabulary/{word_id}", json={"mastery": "mastered"})
        before = self.client.get("/api/vocabulary").json
        entry = self.defer()
        self.assertEqual(self.client.get(self.url).json["words"][0]["mastery"], "mastered")
        response = self.client.post(f"{self.url}/{entry['id']}/collect", json={"meaning": "替换文本"})
        self.assertTrue(response.json["duplicate"])
        self.assertEqual(self.client.get("/api/vocabulary").json, before)
        self.assertEqual(self.client.get(self.url).json["words"], [])

    def test_other_article_cannot_collect_or_remove_this_articles_pending_word(self):
        entry = self.defer()
        wrong = f"/api/articles/1/pending-words/{entry['id']}"
        self.assertEqual(self.client.post(wrong + "/collect", json={"meaning": "旅程"}).status_code, 404)
        self.assertEqual(self.client.delete(wrong, json={}).status_code, 200)
        self.assertEqual(len(self.client.get(self.url).json["words"]), 1)
        self.assertEqual(self.client.get("/api/articles/99999/pending-words").status_code, 404)

    def test_removal_does_not_remove_vocabulary_or_reading_progress(self):
        self.client.patch(f"/api/articles/{self.article_id}/progress", json={"progress": 70, "position": 45, "seconds": 12})
        self.client.post("/api/vocabulary", json={"word": "journey", "meaning": "旅程"})
        before = self.client.get(f"/api/articles/{self.article_id}").json
        entry = self.defer()
        for _ in range(2):
            self.assertEqual(self.client.delete(f"{self.url}/{entry['id']}", json={}).status_code, 200)
        self.assertEqual(self.client.get(f"/api/articles/{self.article_id}").json, before)
        self.assertTrue(any(word["word"] == "journey" for word in self.client.get("/api/vocabulary").json["words"]))

    def test_article_deletion_cleans_pending_but_keeps_collected_vocabulary(self):
        self.defer()
        self.client.post("/api/vocabulary", json={"word": "journey", "meaning": "旅程", "article_id": self.article_id})
        self.client.delete(f"/api/articles/{self.article_id}", json={})
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM pending_words WHERE article_id=?", (self.article_id,)).fetchone()[0], 0)
        saved = next(word for word in self.client.get("/api/vocabulary").json["words"] if word["word"] == "journey")
        self.assertIsNone(saved["article_id"])

    def test_upgrading_reader_05_adds_queue_without_changing_learning_records(self):
        self.client.patch("/api/settings", json={"theme": "dark", "font_size": 24})
        before = self.client.get(f"/api/articles/{self.article_id}").json
        with self.app.app_context():
            db = get_db()
            db.execute("DROP TABLE pending_words")
            db.commit()
        client = create_app(self.config).test_client()
        self.assertEqual(client.get(self.url).json["words"], [])
        self.assertEqual(client.get(f"/api/articles/{self.article_id}").json, before)
        preferences = client.get("/api/settings").json
        self.assertEqual(preferences["font_size"], 24)
        self.assertEqual(preferences["theme"], "dark")
        self.assertEqual(preferences["reading_mode"], "fluent")
        self.assertTrue(preferences["definition_collapsed"])


if __name__ == "__main__":
    unittest.main()
