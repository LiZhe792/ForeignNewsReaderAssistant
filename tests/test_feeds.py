"""Subscription tests. Network access is mocked; fixtures are deterministic."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from newsreader.feeds import FeedError, parse_feed

BODY = ("The quiet return of the night train shows how slow travel can change the way we see "
        "the world and the people around us. ") * 4


def rss_feed(entries=None):
    entries = entries if entries is not None else [
        {"title": "Night trains return", "link": "https://example.com/night", "guid": "tag:1"},
        {"title": "Urban trees", "link": "https://example.com/trees", "guid": "tag:2"},
    ]
    items = "".join(
        f"<item><title>{item['title']}</title><link>{item['link']}</link>"
        f"<description>&lt;p&gt;{BODY}&lt;/p&gt;</description><guid>{item['guid']}</guid></item>"
        for item in entries
    )
    return (f'<?xml version="1.0"?><rss version="2.0"><channel><title>World News</title>'
            f'<link>https://example.com</link>{items}</channel></rss>').encode()


ATOM = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Daily</title>'
        b'<link href="https://atom.example.com"/><entry><title>Quiet city</title>'
        b'<link rel="alternate" href="https://atom.example.com/1"/><id>urn:1</id>'
        b'<content type="html">&lt;p&gt;City life can be quiet and slow.&lt;/p&gt;</content>'
        b'<updated>2026-11-16T10:00:00Z</updated></entry></feed>')


class FeedParsingTests(unittest.TestCase):
    def test_rss_entries_are_cleaned(self):
        parsed = parse_feed(rss_feed())
        self.assertEqual(parsed["title"], "World News")
        self.assertEqual(parsed["site_url"], "https://example.com")
        self.assertEqual(len(parsed["entries"]), 2)
        self.assertEqual(parsed["entries"][0]["title"], "Night trains return")
        self.assertNotIn("<p>", parsed["entries"][0]["content"])
        self.assertIn("slow travel", parsed["entries"][0]["content"])

    def test_atom_entries_are_supported(self):
        parsed = parse_feed(ATOM)
        self.assertEqual(parsed["title"], "Atom Daily")
        entry = parsed["entries"][0]
        self.assertEqual(entry["link"], "https://atom.example.com/1")
        self.assertEqual(entry["guid"], "urn:1")
        self.assertEqual(entry["content"], "City life can be quiet and slow.")

    def test_invalid_document_is_rejected(self):
        with self.assertRaisesRegex(FeedError, "RSS"):
            parse_feed(b"<html><body>not a feed</body></html>")
        with self.assertRaises(FeedError):
            parse_feed(b"not xml at all")

    def test_empty_feed_is_rejected(self):
        empty = b'<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>'
        with self.assertRaises(FeedError):
            parse_feed(empty)


class SubscriptionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "DATABASE": str(Path(self.temp.name) / "test.sqlite3")})
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def subscribe(self, xml=None, **payload):
        xml = xml or rss_feed()
        with patch("newsreader.feeds.fetch_document",
                   return_value=(200, xml, {"etag": "v1"}, "application/rss+xml", "https://example.com/feed")):
            return self.client.post("/api/feeds", json={"url": "https://example.com/feed", **payload})

    def test_subscription_imports_entries_with_category(self):
        response = self.subscribe(category="生活", keywords="")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["added"], 2)
        articles = [item for item in self.client.get("/api/articles").json["articles"] if not item["is_demo"]]
        self.assertEqual({item["title"] for item in articles}, {"Night trains return", "Urban trees"})
        self.assertTrue(all(item["category"] == "生活" for item in articles))
        self.assertTrue(all(item["source"] == "World News" for item in articles))

        feeds = self.client.get("/api/feeds").json["feeds"]
        self.assertEqual(len(feeds), 1)
        self.assertEqual(feeds[0]["article_count"], 2)
        self.assertEqual(feeds[0]["last_status"], "ok")

        sources = self.client.get("/api/sources").json["sources"]
        self.assertIn("World News", [source["name"] for source in sources])

    def test_keyword_filter_imports_only_matching_entries(self):
        response = self.subscribe(keywords="trees")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["added"], 1)
        titles = [item["title"] for item in self.client.get("/api/articles").json["articles"]]
        self.assertIn("Urban trees", titles)
        self.assertNotIn("Night trains return", titles)

    def test_refresh_is_idempotent_and_handles_not_modified(self):
        self.subscribe()
        with patch("newsreader.feeds.fetch_document",
                   return_value=(304, b"", {}, "", "https://example.com/feed")):
            refreshed = self.client.post("/api/feeds/1/refresh", json={})
        self.assertEqual(refreshed.status_code, 200)
        self.assertTrue(refreshed.json["unchanged"])
        self.assertEqual(len([a for a in self.client.get("/api/articles").json["articles"]
                              if not a["is_demo"]]), 2)

    def test_duplicate_subscription_is_rejected(self):
        self.assertEqual(self.subscribe().status_code, 201)
        self.assertEqual(self.subscribe().status_code, 400)

    def test_invalid_category_is_rejected(self):
        self.assertEqual(self.subscribe(category="八卦").status_code, 400)

    def test_removed_article_is_not_reimported(self):
        self.subscribe()
        article = next(item for item in self.client.get("/api/articles").json["articles"]
                       if item["title"] == "Urban trees")
        self.assertEqual(self.client.delete(f"/api/articles/{article['id']}", json={}).status_code, 200)
        with patch("newsreader.feeds.fetch_document",
                   return_value=(200, rss_feed(), {"etag": "v2"}, "application/rss+xml", "https://example.com/feed")):
            refreshed = self.client.post("/api/feeds/1/refresh", json={})
        self.assertEqual(refreshed.json["added"], 0)
        titles = [item["title"] for item in self.client.get("/api/articles").json["articles"]]
        self.assertNotIn("Urban trees", titles)

    def test_pause_and_remove_keep_articles(self):
        self.subscribe()
        paused = self.client.patch("/api/feeds/1", json={"enabled": False})
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json["enabled"], 0)
        self.assertEqual(self.client.delete("/api/feeds/1", json={}).status_code, 200)
        self.assertEqual(self.client.get("/api/feeds").json["feeds"], [])
        titles = [item["title"] for item in self.client.get("/api/articles").json["articles"]]
        self.assertIn("Night trains return", titles)

    def test_source_rename_updates_articles(self):
        self.subscribe()
        source = self.client.get("/api/sources").json["sources"][-1]
        renamed = self.client.patch(f"/api/sources/{source['id']}", json={"name": "Example Daily"})
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json["name"], "Example Daily")
        titles = {item["source"] for item in self.client.get("/api/articles").json["articles"]}
        self.assertIn("Example Daily", titles)
        self.assertNotIn("World News", titles)


class BatchImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "DATABASE": str(Path(self.temp.name) / "test.sqlite3")})
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def fake_import(self, url):
        return {"title": "Imported " + url.rsplit("/", 1)[-1], "content": BODY,
                "source": "example.com", "source_url": url}

    def test_batch_import_reports_each_link(self):
        with patch("newsreader.articles.import_url", side_effect=self.fake_import):
            response = self.client.post("/api/articles/batch", json={
                "urls": ["https://example.com/a", "https://example.com/b"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["imported"], 2)
        self.assertTrue(all(item["ok"] for item in response.json["results"]))
        self.assertEqual(len([a for a in self.client.get("/api/articles").json["articles"]
                              if not a["is_demo"]]), 2)

    def test_batch_import_isolates_failures(self):
        def sometimes(url):
            if url.endswith("/bad"):
                raise ValueError("未找到足够的英文正文。")
            return self.fake_import(url)

        with patch("newsreader.articles.import_url", side_effect=sometimes):
            response = self.client.post("/api/articles/batch", json={
                "urls": ["https://example.com/a", "https://example.com/bad"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["imported"], 1)
        self.assertFalse(response.json["results"][1]["ok"])
        self.assertIn("正文", response.json["results"][1]["error"])

    def test_batch_limits_and_validation(self):
        self.assertEqual(self.client.post("/api/articles/batch", json={"urls": []}).status_code, 400)
        too_many = [f"https://example.com/{index}" for index in range(11)]
        self.assertEqual(self.client.post("/api/articles/batch", json={"urls": too_many}).status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
