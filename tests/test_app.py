"""Deterministic integration tests. Network providers are mocked, not required."""

import json
import sqlite3
from contextlib import closing
import socket
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import create_app
from newsreader.content import extract_article
from newsreader.db import get_db
from newsreader.learning import assess_difficulty
from newsreader.network import FetchError, fetch_public, public_address
from newsreader.preferences import READER_DEFAULTS


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "test.sqlite3")
        self.app = create_app({"TESTING": True, "DATABASE": self.path})
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def add_word(self, word="journey", **extra):
        response = self.client.post("/api/vocabulary", json={"word": word, "meaning": "旅程", **extra})
        self.assertIn(response.status_code, (200, 201))
        return response.json["id"]

    def test_home_and_assets(self):
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Margin 知刊", home.get_data(as_text=True))
        self.assertIn("frame-ancestors 'none'", home.headers["Content-Security-Policy"])
        for asset in ("/static/app.js", "/static/style.css", "/static/favicon.svg", "/static/reader.js", "/static/reader.css", "/static/reading-flow.js", "/static/reading-flow.css"):
            with self.client.get(asset) as response:
                self.assertEqual(response.status_code, 200)

    def test_samples_and_difficulty(self):
        articles = self.client.get("/api/articles").json["articles"]
        self.assertGreaterEqual(len(articles), 5)
        article = self.client.get(f"/api/articles/{articles[0]['id']}").json
        self.assertTrue(article["is_demo"])
        self.assertGreater(article["word_count"], 100)
        self.assertIn(article["level"], ["A2", "B1", "B2", "C1", "C2"])
        self.assertIn("仅供", article["difficulty"]["method"])

    def test_paste_import_and_persistence(self):
        payload = {"title": "My reading", "content": "The city has a small park. " * 12, "category": "生活"}
        response = self.client.post("/api/articles", json=payload)
        self.assertEqual(response.status_code, 201)
        article_id = response.json["id"]
        restarted = create_app({"TESTING": True, "DATABASE": self.path}).test_client()
        article = restarted.get(f"/api/articles/{article_id}").json
        self.assertEqual(article["title"], payload["title"])
        self.assertFalse(article["is_demo"])

    def test_invalid_import(self):
        for payload in ({"title": "Short", "content": "hello"}, {"title": [], "content": "hello " * 50},
                        {"title": "test", "content": "hello " * 50, "category": "unknown"}):
            self.assertEqual(self.client.post("/api/articles", json=payload).status_code, 400)

    def test_url_import_duplicate(self):
        imported = {"title": "Public article", "content": "A useful word in a small article. " * 10,
                    "source": "example.com", "source_url": "https://example.com/read"}
        with patch("app.import_url", return_value=imported):
            first = self.client.post("/api/articles", json={"url": imported["source_url"]})
            second = self.client.post("/api/articles", json={"url": imported["source_url"]})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.json["id"], first.json["id"])
        self.assertTrue(second.json["duplicate"])

    def test_progress_never_decreases_but_position_does(self):
        self.client.patch("/api/articles/1/progress", json={"progress": 75, "position": 75, "seconds": 30})
        self.client.patch("/api/articles/1/progress", json={"progress": 20, "position": 20, "seconds": 12})
        article = self.client.get("/api/articles/1").json
        self.assertEqual(article["progress"], 75)
        self.assertEqual(article["position"], 20)
        self.assertEqual(article["reading_seconds"], 42)
        self.assertEqual(self.client.get("/api/stats").json["today_seconds"], 42)

    def test_completion_and_checkin_are_idempotent(self):
        for _ in range(3):
            self.client.patch("/api/articles/1/progress", json={"progress": 100, "position": 100, "seconds": 0})
            self.client.post("/api/checkin", json={})
        stats = self.client.get("/api/stats").json
        self.assertEqual(stats["completed_articles"], 1)
        self.assertEqual(stats["active_days"], 1)
        self.assertEqual(stats["streak"], 1)
        self.assertEqual(stats["today_seconds"], 0)

    def test_progress_validation(self):
        for payload in ({"seconds": -1}, {"seconds": 61}, {"seconds": True}, {"progress": 101},
                        {"progress": "30"}, {"position": -5}, {"position": None}):
            self.assertEqual(self.client.patch("/api/articles/1/progress", json=payload).status_code, 400)

    def test_dictionary_offline_and_inflection(self):
        with patch("newsreader.content.fetch_public") as network:
            result = self.client.get("/api/dictionary?word=journeys").json
        self.assertTrue(result["found"])
        self.assertEqual(result["lemma"], "journey")
        self.assertIn("旅程", result["meaning"])
        network.assert_not_called()

    def test_dictionary_network_cache(self):
        data = [{"word": "ephemeral", "phonetics": [{"text": "/test/"}], "meanings": [{"partOfSpeech": "adjective", "definitions": [{"definition": "Lasting for a short time.", "example": "An ephemeral moment."}]}]}]
        with patch("newsreader.content.fetch_public", return_value=(json.dumps(data).encode(), "application/json", "")) as network:
            first = self.client.get("/api/dictionary?word=ephemeral").json
            second = self.client.get("/api/dictionary?word=ephemeral").json
        self.assertEqual(first["phonetic"], "/test/")
        self.assertTrue(second["cached"])
        self.assertEqual(network.call_count, 1)

    def test_dictionary_network_failure_is_honest(self):
        with patch("newsreader.content.fetch_public", side_effect=FetchError("offline")):
            result = self.client.get("/api/dictionary?word=unlistedword").json
        self.assertFalse(result["found"])
        self.assertEqual(result["meaning"], "")

    def test_dictionary_validation(self):
        for word in ("hello world", "<script>", "a" * 61, "", "123"):
            response = self.client.get("/api/dictionary", query_string={"word": word})
            self.assertEqual(response.status_code, 400)

    def test_vocabulary_duplicate_preserves_entry(self):
        word_id = self.add_word(context="The journey begins.", article_id=1)
        self.client.patch(f"/api/vocabulary/{word_id}", json={"mastery": "mastered"})
        self.assertEqual(self.add_word("JOURNEY"), word_id)
        cards = self.client.get("/api/vocabulary").json["words"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["mastery"], "mastered")
        self.assertEqual(cards[0]["context"], "The journey begins.")

    def test_word_edit_and_mastery(self):
        word_id = self.add_word()
        response = self.client.patch(f"/api/vocabulary/{word_id}", json={"mastery": "mastered", "note": "A long journey", "meaning": "旅行"})
        self.assertEqual(response.json["mastery"], "mastered")
        self.assertEqual(response.json["note"], "A long journey")
        self.assertEqual(response.json["meaning"], "旅行")
        self.assertNotIn("due_date", response.json)
        self.assertEqual(self.client.get("/api/stats").json["mastered_words"], 1)
        self.client.patch(f"/api/vocabulary/{word_id}", json={"mastery": "learning"})
        self.assertEqual(self.client.get("/api/stats").json["mastered_words"], 0)

    def test_word_validation_and_edit_rollback(self):
        word_id = self.add_word()
        self.assertEqual(self.client.patch(f"/api/vocabulary/{word_id}", json={"note": "should rollback", "mastery": "invalid"}).status_code, 400)
        self.assertEqual(self.client.get("/api/vocabulary").json["words"][0]["note"], "")
        for payload in ({"word": "two words", "meaning": "invalid"}, {"word": "read", "meaning": ""},
                        {"word": "read", "meaning": "阅读", "article_id": 999}, {"word": "read", "meaning": "阅读", "article_id": True}):
            self.assertIn(self.client.post("/api/vocabulary", json=payload).status_code, (400, 404))


    def test_deleting_article_preserves_vocabulary_and_history(self):
        self.add_word(article_id=1)
        self.client.patch("/api/articles/1/progress", json={"progress": 100, "seconds": 30})
        self.client.delete("/api/articles/1", json={})
        self.assertIsNone(self.client.get("/api/vocabulary").json["words"][0]["article_id"])
        self.assertEqual(self.client.get("/api/stats").json["completed_articles"], 1)
        self.assertEqual(self.client.get("/api/stats").json["total_seconds"], 30)


    def test_seeding_does_not_restore_deleted_samples(self):
        self.client.delete("/api/articles/1", json={})
        restarted = create_app({"TESTING": True, "DATABASE": self.path}).test_client()
        self.assertEqual(restarted.get("/api/articles/1").status_code, 404)

    def test_streak_month_boundary_and_calendar(self):
        with self.app.app_context():
            db = get_db()
            for day in ("2026-10-30", "2026-10-31", "2026-11-01"):
                db.execute("INSERT INTO checkins (date, manual) VALUES (?, 1)", (day,))
            db.commit()
        with patch("app.today", return_value=date(2026, 11, 2)):
            stats = self.client.get("/api/stats?month=2026-10").json
        self.assertEqual(stats["streak"], 3)
        self.assertEqual(len(stats["days"]), 2)
        self.assertFalse(stats["checked_in"])
        self.assertEqual(self.client.get("/api/stats?month=not-a-month").status_code, 400)

    def test_settings_persist_and_validate(self):
        response = self.client.patch("/api/settings", json={"font_size": 25, "line_height": 2.2})
        self.assertEqual(response.json, {**READER_DEFAULTS, "font_size": 25, "line_height": 2.2})
        self.assertEqual(self.client.get("/api/settings").json, response.json)
        for payload in ({"font_size": 100}, {"font_size": True}, {"theme": "unknown"}, {"line_height": 0}, {"AI_API_KEY": "secret"}):
            self.assertEqual(self.client.patch("/api/settings", json=payload).status_code, 400)


    def test_mutation_origin_and_json_required(self):
        self.assertEqual(self.client.post("/api/checkin", data="date=today").status_code, 415)
        self.assertEqual(self.client.post("/api/checkin", json={}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/checkin", json={}, headers={"Origin": "http://localhost"}).status_code, 200)
        self.assertEqual(self.client.get("/api/stats", headers={"Host": "evil.example"}).status_code, 400)

    def test_json_shape_and_missing_record(self):
        self.assertEqual(self.client.post("/api/articles", json=[]).status_code, 400)
        self.assertEqual(self.client.get("/api/articles/99999").status_code, 404)


    def test_delete_word_keeps_article_and_checkin(self):
        word_id = self.add_word(article_id=1)
        self.client.post("/api/checkin", json={})
        response = self.client.delete(f"/api/vocabulary/{word_id}", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/vocabulary").json["words"], [])
        self.assertEqual(self.client.get("/api/articles/1").status_code, 200)
        self.assertEqual(self.client.get("/api/stats").json["active_days"], 1)

    def test_advanced_routes_are_removed(self):
        self.assertEqual(self.client.get("/api/reviews").status_code, 404)
        for route in ("/api/reviews/1", "/api/analyze", "/api/context"):
            self.assertEqual(self.client.post(route, json={}).status_code, 404)
        stats = self.client.get("/api/stats").json
        for field in ("due_words", "total_reviews", "today_reviews"):
            self.assertNotIn(field, stats)

    def test_legacy_database_migration_preserves_basic_data(self):
        with self.app.app_context():
            db = get_db()
            db.executescript("""
                DROP TABLE vocabulary;
                CREATE TABLE vocabulary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    word TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    phonetic TEXT NOT NULL DEFAULT '', meaning TEXT NOT NULL,
                    part_of_speech TEXT NOT NULL DEFAULT '',
                    context TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
                    article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL,
                    mastery TEXT NOT NULL DEFAULT 'new',
                    repetitions INTEGER NOT NULL DEFAULT 0, interval_days INTEGER NOT NULL DEFAULT 0,
                    ease_factor REAL NOT NULL DEFAULT 2.5, due_date TEXT NOT NULL,
                    created_at TEXT NOT NULL, last_reviewed_at TEXT
                );
                CREATE TABLE review_logs (
                    id INTEGER PRIMARY KEY, vocabulary_id INTEGER REFERENCES vocabulary(id),
                    grade INTEGER, reviewed_at TEXT, review_date TEXT, interval_days INTEGER
                );
                INSERT INTO vocabulary (id, word, meaning, context, note, article_id, mastery, due_date, created_at)
                VALUES (7, 'journey', 'travel', 'The journey begins.', 'keep this note', 1, 'mastered', '2026-12-01', '2026-11-15');
                INSERT INTO review_logs VALUES (1, 7, 3, '2026-11-15', '2026-11-15', 16);
                INSERT INTO checkins VALUES ('2026-11-15', 120, 0, 1);
                UPDATE articles SET progress=65, position=55, reading_seconds=120 WHERE id=1;
                INSERT OR REPLACE INTO settings VALUES ('font_size', '25'), ('line_height', '2.2'), ('theme', '"dark"');
            """)
        restarted = create_app({"TESTING": True, "DATABASE": self.path})
        client = restarted.test_client()
        word = client.get("/api/vocabulary").json["words"][0]
        self.assertEqual(word["id"], 7)
        self.assertEqual(word["context"], "The journey begins.")
        self.assertEqual(word["note"], "keep this note")
        self.assertEqual(word["mastery"], "mastered")
        self.assertNotIn("due_date", word)
        self.assertNotIn("repetitions", word)
        self.assertEqual(client.get("/api/articles/1").json["progress"], 65)
        self.assertEqual(client.get("/api/articles/1").json["position"], 55)
        self.assertEqual(client.get("/api/stats").json["total_seconds"], 120)
        self.assertEqual(client.get("/api/settings").json, {**READER_DEFAULTS, "font_size": 25, "line_height": 2.2, "theme": "dark"})
        self.assertEqual(client.post("/api/vocabulary", json={"word": "city", "meaning": "城市"}).status_code, 201)
        with restarted.app_context():
            db = get_db()
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='review_logs'").fetchone())
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        backup = Path(self.path).with_name("test-before-basic.sqlite3")
        self.assertTrue(backup.exists())
        with closing(sqlite3.connect(backup)) as old:
            self.assertEqual(old.execute("SELECT COUNT(*) FROM review_logs").fetchone()[0], 1)
        again = create_app({"TESTING": True, "DATABASE": self.path}).test_client()
        self.assertEqual(len(again.get("/api/vocabulary").json["words"]), 2)
        self.assertEqual(len(again.get("/api/articles").json["articles"]), 5)
        again.delete("/api/articles/1", json={})
        self.assertIsNone(next(w for w in again.get("/api/vocabulary").json["words"] if w["id"] == 7)["article_id"])


class DifficultyTests(unittest.TestCase):
    def test_difficulty_is_bounded_and_longer_sentences_are_harder(self):
        easy = assess_difficulty("The cat is on the mat. The sun is out. We can go home.")
        hard = assess_difficulty("The institutionalization of interdisciplinary methodologies necessitates comprehensive reconsideration of conventional epistemological assumptions.")
        self.assertGreater(easy["score"], hard["score"])
        self.assertTrue(0 <= easy["score"] <= 100)
        self.assertEqual(easy["word_count"], 14)
        self.assertEqual(assess_difficulty("")["word_count"], 0)


class NetworkAndContentTests(unittest.TestCase):
    def test_extract_removes_navigation_and_scripts(self):
        sentence = "The library gives every reader a quiet place to learn and think. "
        html = f'<html><head><title>Library story</title></head><body><nav>Ignore me</nav><article><h1>Library story</h1><p>{sentence * 5}</p><script>alert(1)</script></article><footer>Footer words</footer></body></html>'
        result = extract_article(html, "https://example.com/read")
        self.assertEqual(result["title"], "Library story")
        self.assertNotIn("Ignore", result["content"])
        self.assertNotIn("alert", result["content"])
        self.assertEqual(result["source"], "example.com")

    def test_extract_empty_paywall_has_actionable_error(self):
        with self.assertRaisesRegex(FetchError, "粘贴"):
            extract_article("<html><p>Sign in to read more.</p></html>", "https://example.com")

    def test_url_scheme_and_port_validation(self):
        for url in ("file:///etc/passwd", "ftp://example.com", "http://user:pass@example.com", "http://example.com:8080"):
            with self.subTest(url=url), self.assertRaises(FetchError):
                fetch_public(url)

    def test_private_dns_addresses_rejected(self):
        for address in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "::1", "fc00::1", "224.0.0.1"):
            with self.subTest(address=address), patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80))]), self.assertRaises(FetchError):
                public_address("example.com", 80)

    def test_public_dns_pinned(self):
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]):
            self.assertEqual(public_address("example.com", 443), "93.184.216.34")

    def test_redirect_target_revalidated(self):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 302
        response.getheader.return_value = "http://127.0.0.1/private"
        with patch("newsreader.network.public_address", side_effect=["93.184.216.34", FetchError("blocked")]) as dns, patch("newsreader.network.PinnedHTTPS", return_value=connection), self.assertRaises(FetchError):
            fetch_public("https://example.com")
        self.assertEqual(dns.call_count, 2)
        connection.close.assert_called_once()

    def test_maximum_download_size(self):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheader.return_value = None
        response.read1.side_effect = [b"a" * 11, b""]
        with patch("newsreader.network.public_address", return_value="93.184.216.34"), patch("newsreader.network.PinnedHTTPS", return_value=connection), self.assertRaises(FetchError):
            fetch_public("https://example.com", max_bytes=10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
