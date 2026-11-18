"""Reader preference contracts and compatibility with existing learning data."""

import json
import tempfile
import unittest
from pathlib import Path

from app import create_app
from newsreader.db import get_db


class ReaderPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"TESTING": True, "DATABASE": str(Path(self.temp.name) / "reader.sqlite3")}
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_all_preferences_survive_restart(self):
        preferences = {
            "font_size": 24, "line_height": 2.2, "font_family": "sans",
            "content_width": 1120, "paragraph_spacing": 2.25,
            "theme": "dark", "layout": "double", "paragraph_numbers": True,
            "focus_mode": True, "shortcuts_enabled": False, "gestures_enabled": False,
            "reading_mode": "intensive", "definition_collapsed": False,
            "hint_density": "quiet", "suppress_mastered": False, "reminder_minutes": 20,
            "dictionary_provider": "offline",
        }
        response = self.client.patch("/api/settings", json=preferences)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, preferences)
        restarted = create_app(self.config).test_client()
        self.assertEqual(restarted.get("/api/settings").json, preferences)

    def test_existing_size_and_line_height_gain_new_defaults(self):
        with self.app.app_context():
            db = get_db()
            db.executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)", [("font_size", "26"), ("line_height", "2.1")])
            db.commit()
        result = create_app(self.config).test_client().get("/api/settings").json
        self.assertEqual(result["font_size"], 26)
        self.assertEqual(result["line_height"], 2.1)
        self.assertEqual(result["theme"], "light")
        self.assertEqual(result["layout"], "single")
        self.assertFalse(result["focus_mode"])

    def test_bad_patch_is_atomic(self):
        self.client.patch("/api/settings", json={"theme": "sepia", "content_width": 900})
        before = self.client.get("/api/settings").json
        for bad in (
            {"theme": "dark", "content_width": 0},
            {"theme": "dark", "focus_mode": "false"},
            {"theme": "dark", "fullscreen": True},
        ):
            with self.subTest(bad=bad):
                self.assertEqual(self.client.patch("/api/settings", json=bad).status_code, 400)
                self.assertEqual(self.client.get("/api/settings").json, before)

    def test_types_enums_ranges_and_nonfinite_values(self):
        invalid = {
            "font_family": ["url(https://example.com/font)", None, [], {}],
            "theme": ["system", 1, True], "layout": ["triple", []],
            "content_width": [559, 1201, 800.5, True, "800", 10**500],
            "paragraph_spacing": [.49, 3.01, True, float("nan"), float("inf")],
            "line_height": [float("nan"), float("-inf"), True],
            "paragraph_numbers": [0, 1, "true", None],
            "focus_mode": [[], {}], "shortcuts_enabled": [1], "gestures_enabled": ["false"],
            "reading_mode": ["auto", []], "hint_density": ["high", 1],
            "definition_collapsed": [0, "false"], "suppress_mastered": [1, None],
            "reminder_minutes": [-1, 61, 1.5, True, "10"],
        }
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.client.patch("/api/settings", json={key: value}).status_code, 400)
        for payload in ({"content_width": 560, "paragraph_spacing": .5}, {"content_width": 1200, "paragraph_spacing": 3}):
            self.assertEqual(self.client.patch("/api/settings", json=payload).status_code, 200)

    def test_invalid_stored_values_fall_back_without_losing_valid_values(self):
        with self.app.app_context():
            db = get_db()
            db.executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)", [
                ("theme", '"unexpected"'), ("layout", "not-json"),
                ("font_size", "false"), ("paragraph_spacing", "NaN"),
                ("content_width", "1000"), ("font_family", json.dumps("mono")),
            ])
            db.commit()
        result = self.client.get("/api/settings").json
        self.assertEqual(result["theme"], "light")
        self.assertEqual(result["layout"], "single")
        self.assertEqual(result["font_size"], 20)
        self.assertEqual(result["paragraph_spacing"], 1.45)
        self.assertEqual(result["content_width"], 1000)
        self.assertEqual(result["font_family"], "mono")

    def test_reader_changes_preserve_learning_records(self):
        self.client.post("/api/vocabulary", json={"word": "journey", "meaning": "旅程", "article_id": 1})
        self.client.patch("/api/articles/1/progress", json={"progress": 65, "position": 42, "seconds": 15})
        article = self.client.get("/api/articles/1").json
        words = self.client.get("/api/vocabulary").json
        stats = self.client.get("/api/stats").json
        self.client.patch("/api/settings", json={"theme": "dark", "layout": "double", "focus_mode": True})
        restarted = create_app(self.config).test_client()
        self.assertEqual(restarted.get("/api/articles/1").json, article)
        self.assertEqual(restarted.get("/api/vocabulary").json, words)
        self.assertEqual(restarted.get("/api/stats").json, stats)


if __name__ == "__main__":
    unittest.main()
