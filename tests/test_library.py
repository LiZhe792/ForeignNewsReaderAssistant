"""Article library management tests: categories, tags, batch ops and filters."""

import tempfile
import unittest
from pathlib import Path

from app import create_app

BODY = ("The quiet return of the night train shows how slow travel can change the way we "
        "see the world and the people around us. ") * 4


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "DATABASE": str(Path(self.temp.name) / "test.sqlite3")})
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def make(self, title="A quiet journey", category="生活", **extra):
        response = self.client.post("/api/articles", json={"title": title, "content": BODY,
                                                           "category": category, **extra})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json["id"]

    def articles(self, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return self.client.get("/api/articles" + (f"?{query}" if query else "")).json["articles"]

    def test_seeded_categories_are_listed(self):
        names = [item["name"] for item in self.client.get("/api/categories").json["categories"]]
        self.assertEqual(names, ["科技", "文化", "科学", "生活", "环境", "其他"])

    def test_add_and_rename_category(self):
        created = self.client.post("/api/categories", json={"name": "商业"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.post("/api/categories", json={"name": "商业"}).json["duplicate"], True)
        article_id = self.make(category="商业")
        renamed = self.client.patch(f"/api/categories/{created.json['id']}", json={"name": "财经"})
        self.assertEqual(renamed.json["name"], "财经")
        self.assertIn("财经", [a["category"] for a in self.articles() if a["id"] == article_id])

    def test_delete_category_reassigns_articles(self):
        category_id = self.client.post("/api/categories", json={"name": "商业"}).json["id"]
        self.make(category="商业")
        self.assertEqual(self.client.delete(f"/api/categories/{category_id}", json={}).status_code, 200)
        categories = [c["name"] for c in self.client.get("/api/categories").json["categories"]]
        self.assertNotIn("商业", categories)
        self.assertIn("其他", [a["category"] for a in self.articles() if a["category"] != "生活"] or ["其他"])

    def test_last_category_cannot_be_deleted(self):
        for category in self.client.get("/api/categories").json["categories"]:
            response = self.client.delete(f"/api/categories/{category['id']}", json={})
            if response.status_code != 200:
                self.assertEqual(response.status_code, 400)
                return
        self.fail("至少一个分类应始终保留")

    def test_tags_attach_and_filter(self):
        article_id = self.make()
        self.client.patch(f"/api/articles/{article_id}", json={"tags": ["气候", "能源", "气候"]})
        detail = self.client.get(f"/api/articles/{article_id}").json
        self.assertEqual(detail["tags"], ["气候", "能源"])
        tagged = self.articles(tag="气候")
        self.assertIn(article_id, [a["id"] for a in tagged])
        self.assertEqual(self.articles(tag="不存在"), [])

    def test_tag_rename_and_delete(self):
        tag_id = self.client.post("/api/tags", json={"name": "深度报道"}).json["id"]
        article_id = self.make()
        self.client.patch(f"/api/articles/{article_id}", json={"tags": ["深度报道"]})
        self.client.patch(f"/api/tags/{tag_id}", json={"name": "调查报道"})
        self.assertIn("调查报道", self.client.get(f"/api/articles/{article_id}").json["tags"])
        self.client.delete(f"/api/tags/{tag_id}", json={})
        self.assertEqual(self.client.get(f"/api/articles/{article_id}").json["tags"], [])

    def test_article_edit_single_fields(self):
        article_id = self.make()
        self.client.patch(f"/api/articles/{article_id}", json={
            "favorite": True, "read_later": True, "priority": 2,
            "author": "Jane Goodall", "published_at": "2026-11-01"})
        article = self.client.get(f"/api/articles/{article_id}").json
        self.assertTrue(article["favorite"]); self.assertTrue(article["read_later"])
        self.assertEqual(article["priority"], 2)
        self.assertEqual(article["author"], "Jane Goodall")
        self.assertEqual(article["published_at"], "2026-11-01")

    def test_batch_archive_trash_restore_delete(self):
        first, second = self.make("First story"), self.make("Second story")
        self.client.post("/api/articles/batch", json={"ids": [first, second], "op": "archive"})
        archived = self.articles(status="archived")
        self.assertEqual({a["id"] for a in archived}, {first, second})
        self.client.post("/api/articles/batch", json={"ids": [first], "op": "trash"})
        self.assertIn(first, [a["id"] for a in self.articles(status="trashed")])
        self.assertNotIn(first, [a["id"] for a in self.articles()])
        self.client.post("/api/articles/batch", json={"ids": [first], "op": "restore"})
        self.assertIn(first, [a["id"] for a in self.articles(status="active")])
        self.client.post("/api/articles/batch", json={"ids": [second], "op": "delete"})
        self.assertNotIn(second, [a["id"] for a in self.articles(status="all")])

    def test_batch_flags_priority_category_and_tag(self):
        first, second = self.make("First"), self.make("Second")
        self.client.post("/api/articles/batch", json={"ids": [first, second], "op": "favorite"})
        self.assertEqual({a["id"] for a in self.articles(favorite=1)}, {first, second})
        self.client.post("/api/articles/batch", json={"ids": [first, second], "op": "set_priority", "priority": 3})
        self.assertEqual({a["id"] for a in self.articles(priority=3)}, {first, second})
        self.client.post("/api/articles/batch", json={"ids": [first, second], "op": "set_category", "category": "科学"})
        self.assertTrue(all(a["category"] == "科学" for a in self.articles(status="all") if a["id"] in (first, second)))
        self.client.post("/api/articles/batch", json={"ids": [first, second], "op": "add_tag", "tag": "精读"})
        self.assertEqual({a["id"] for a in self.articles(tag="精读")}, {first, second})
        self.client.post("/api/articles/batch", json={"ids": [first], "op": "remove_tag", "tag": "精读"})
        self.assertEqual({a["id"] for a in self.articles(tag="精读")}, {second})

    def test_batch_validation(self):
        self.assertEqual(self.client.post("/api/articles/batch", json={"ids": [], "op": "trash"}).status_code, 400)
        self.assertEqual(self.client.post("/api/articles/batch", json={"ids": [1], "op": "bogus"}).status_code, 400)
        self.assertEqual(self.client.post("/api/articles/batch", json={"ids": [1], "op": "set_priority", "priority": 9}).status_code, 400)

    def test_duplicate_title_is_detected(self):
        self.make("Night Trains Return")
        response = self.client.post("/api/articles", json={"title": "Night Trains Return", "content": BODY, "category": "生活"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["duplicate"])

    def test_read_later_filter(self):
        article_id = self.make()
        self.client.patch(f"/api/articles/{article_id}", json={"read_later": True})
        self.assertIn(article_id, [a["id"] for a in self.articles(read_later=1)])
        self.assertNotIn(article_id, [a["id"] for a in self.articles(status="archived")])


class LibraryMigrationTests(unittest.TestCase):
    def test_legacy_article_columns_are_added(self):
        import sqlite3
        from newsreader.db import migrate_library_columns, normalize_title
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = str(Path(temp.name) / "old.sqlite3")
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE articles (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "content TEXT NOT NULL, source TEXT, source_url TEXT DEFAULT '', category TEXT DEFAULT '其他', "
            "author TEXT DEFAULT '', level TEXT, difficulty_score REAL, word_count INTEGER, "
            "reading_minutes INTEGER, created_at TEXT, is_demo INTEGER DEFAULT 0, "
            "progress REAL DEFAULT 0, position REAL DEFAULT 0, reading_seconds INTEGER DEFAULT 0, "
            "last_read_at TEXT, completed_at TEXT)"
        )
        connection.execute("INSERT INTO articles (title, content) VALUES ('Legacy Story', 'x')")
        connection.commit()
        connection.row_factory = sqlite3.Row
        migrate_library_columns(connection)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(articles)")}
        for name in ("favorite", "read_later", "priority", "status", "published_at", "title_norm"):
            self.assertIn(name, columns)
        self.assertEqual(connection.execute("SELECT title_norm FROM articles").fetchone()[0], normalize_title("Legacy Story"))
        connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
