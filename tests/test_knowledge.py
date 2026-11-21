"""Source integrity, local persistence and concurrent editing of personal knowledge."""

import tempfile
import unittest
import uuid
from pathlib import Path

from app import create_app
from newsreader.db import get_db


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"TESTING": True, "DATABASE": str(Path(self.temp.name) / "knowledge.sqlite3")}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.parts = ["A 🌱 quiet journey. A quiet journey. " + "Readers learn from the world. " * 9,
                      "We can build a thoughtful habit. " * 10]
        self.article = self.client.post("/api/articles", json={"title": "A reading habit", "content": "\n\n".join(self.parts)}).json["id"]

    def tearDown(self):
        self.temp.cleanup()

    def anchor(self, paragraph=0, quote="quiet journey", start=None):
        text = self.parts[paragraph]
        start = text.index(quote) if start is None else start
        return {"paragraph": paragraph, "start": len(text[:start].encode("utf-16-le")) // 2,
                "end": len(text[:start + len(quote)].encode("utf-16-le")) // 2, "quote": quote}

    def body(self, kind="note", **extra):
        data = {"request_id": str(uuid.uuid4()), "kind": kind, "title": "My learning", "content": "My own explanation.",
                "tags": [" Writing ", "写作", "WRITING"], "color": "green"}
        if kind in ("highlight", "bookmark", "annotation", "sentence"):
            data.update(article_id=self.article, anchors=[self.anchor()])
        return {**data, **extra}

    def create(self, kind="note", **extra):
        response = self.client.post("/api/knowledge/items", json=self.body(kind, **extra))
        self.assertIn(response.status_code, (200, 201), response.json)
        return response.json["item"]

    def map_body(self, **extra):
        return {"title": "Reading map", "request_id": str(uuid.uuid4()), "nodes": [
            {"id": "root", "parent": None, "text": "Reading", "item_id": None},
            {"id": "one", "parent": "root", "text": "A branch", "item_id": None}], **extra}

    def test_all_kinds_local_and_assets(self):
        response = self.client.get("/api/knowledge/options").json
        self.assertEqual(len(response["kinds"]), 8)
        self.assertEqual(len(response["colors"]), 4)
        for kind in response["kinds"]:
            item = self.create(kind)
            self.assertEqual(item["kind"], kind)
            self.assertEqual(item["tags"], ["writing", "写作"])
        home = self.client.get("/").text
        for asset in ("/static/knowledge.js", "/static/knowledge.css"):
            self.assertIn(asset, home)
            with self.client.get(asset) as result:
                self.assertEqual(result.status_code, 200)
        self.assertIn('href="#knowledge"', home)

    def test_exact_emoji_offsets_and_repeated_phrases(self):
        first = self.anchor()
        second = self.anchor(start=self.parts[0].index("quiet journey", 15))
        emoji = self.anchor(quote="🌱")
        for anchor in (first, second, emoji):
            item = self.create("highlight", anchors=[anchor])
            self.assertEqual(item["anchors"], [anchor])
            self.assertEqual(item["source_state"], "available")
        self.assertNotEqual(first["start"], second["start"])

    def test_multiselection_and_source_verification(self):
        anchors = [self.anchor(), self.anchor(1, "thoughtful habit")]
        item = self.create("sentence", anchors=anchors)
        self.assertEqual(item["quote"], "quiet journey\n\nthoughtful habit")
        invalid = [list(reversed(anchors)), [anchors[0], anchors[0]],
                   [{**anchors[0], "quote": "forged"}], [{**anchors[0], "paragraph": True}],
                   [{**anchors[0], "end": 99999}], [{**anchors[0], "start": -1}],
                   [{**self.anchor(quote="🌱"), "end": 3}], []]
        for value in invalid:
            with self.subTest(value=value):
                self.assertEqual(self.client.post("/api/knowledge/items", json=self.body("highlight", anchors=value)).status_code, 400)

    def test_anchor_requirement_and_article_validation(self):
        for kind in ("highlight", "bookmark", "annotation", "sentence"):
            self.assertEqual(self.client.post("/api/knowledge/items", json=self.body(kind, article_id=None, anchors=[])).status_code, 400)
        self.assertEqual(self.client.post("/api/knowledge/items", json=self.body("note", anchors=[self.anchor()])).status_code, 400)
        self.assertEqual(self.client.post("/api/knowledge/items", json=self.body("note", article_id=True)).status_code, 400)
        self.assertEqual(self.client.post("/api/knowledge/items", json=self.body("highlight", article_id=999999)).status_code, 404)
        self.assertEqual(self.client.post("/api/knowledge/items", json=self.body("bookmark", anchors=[self.anchor(), self.anchor(1, "thoughtful")])).status_code, 400)

    def test_retry_idempotency_and_source_deduplication(self):
        body = self.body("highlight")
        first = self.client.post("/api/knowledge/items", json=body)
        again = self.client.post("/api/knowledge/items", json=body)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(first.json["item"]["id"], again.json["item"]["id"])
        self.assertTrue(again.json["duplicate"])
        self.assertEqual(self.client.post("/api/knowledge/items", json={**body, "title": "changed"}).status_code, 409)
        duplicate = self.create("highlight", color="pink")
        self.assertEqual(duplicate["id"], first.json["item"]["id"])
        b1 = self.create("bookmark")
        b2 = self.create("bookmark", anchors=[self.anchor(quote="Readers learn")])
        self.assertEqual(b1["id"], b2["id"])

    def test_edit_color_notes_tags_and_conflict(self):
        item = self.create("highlight")
        url = "/api/knowledge/items/" + str(item["id"])
        response = self.client.patch(url, json={"version": 0, "color": "blue", "content": "Updated", "tags": ["Focus"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["item"]["color"], "blue")
        self.assertEqual(response.json["item"]["tags"], ["focus"])
        conflict = self.client.patch(url, json={"version": 0, "content": "Old window"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json["item"]["content"], "Updated")
        self.assertEqual(self.client.delete(url, json={"version": 0}).status_code, 409)
        self.assertEqual(self.client.patch(url, json={"version": 1, "anchors": []}).status_code, 400)
        self.assertEqual(self.client.delete(url, json={"version": 1}).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_invalid_fields_do_not_write_partial_records(self):
        invalid = [{"kind": []}, {"title": ""}, {"title": "x" * 201}, {"content": ""}, {"content": "x" * 10001},
                   {"example": "x" * 3001}, {"color": "red"}, {"tags": "tag"}, {"tags": [None]},
                   {"tags": ["x" * 25]}, {"tags": ["x"] * 13}, {"request_id": "short"}]
        for patch in invalid:
            response = self.client.post("/api/knowledge/items", json=self.body(**patch))
            self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.client.get("/api/knowledge/items").json["items"], [])
        self.assertEqual(self.client.post("/api/knowledge/items", json=[]).status_code, 400)

    def test_filter_literal_search_tags_kind_article_and_pagination(self):
        note = self.create(title="50%_ gain", tags=["environment", "写作"])
        self.create("expression", title="50 percent", tags=["environmental"])
        self.create("sentence")
        for params in ({"q": "%_"}, {"tag": "ENVIRONMENT"}, {"q": "environment", "kind": "note"}):
            found = self.client.get("/api/knowledge/items", query_string=params).json["items"]
            self.assertEqual([i["id"] for i in found], [note["id"]])
        self.assertEqual(len(self.client.get("/api/knowledge/items", query_string={"article_id": self.article}).json["items"]), 1)
        self.assertEqual(self.client.get("/api/knowledge/items", query_string={"q": "paragraph"}).json["items"], [])
        self.assertEqual(self.client.get("/api/knowledge/items", query_string={"q": '"'}).json["items"], [])
        for i in range(53):
            self.create(title=f"Note {i}")
        first = self.client.get("/api/knowledge/items").json
        second = self.client.get("/api/knowledge/items", query_string={"before": first["next_cursor"]}).json
        self.assertEqual(len(first["items"]), 50)
        self.assertEqual(len(second["items"]), 6)
        self.assertEqual(len({i["id"] for i in first["items"] + second["items"]}), 56)
        for params in ({"before": "-1"}, {"before": "٩"}, {"kind": "unknown"}, {"q": "x" * 201}):
            self.assertEqual(self.client.get("/api/knowledge/items", query_string=params).status_code, 400)

    def test_article_deletion_retains_knowledge_and_original_quotes(self):
        items = [self.create(kind) for kind in ("highlight", "annotation", "sentence", "bookmark")]
        self.assertEqual(self.client.delete(f"/api/articles/{self.article}", json={}).status_code, 200)
        for original in items:
            item = self.client.get(f"/api/knowledge/items/{original['id']}").json["item"]
            self.assertIsNone(item["article_id"])
            self.assertEqual(item["source_state"], "deleted")
            self.assertEqual(item["quote"], original["quote"])
            self.assertEqual(item["article_title"], "A reading habit")

    def test_changed_sources_are_never_reported_as_available(self):
        item = self.create("sentence")
        with self.app.app_context():
            get_db().execute("UPDATE articles SET content=? WHERE id=?", ("A changed article.", self.article))
            get_db().commit()
        result = self.client.get(f"/api/knowledge/items/{item['id']}").json["item"]
        self.assertEqual(result["source_state"], "changed")

    def test_records_and_maps_survive_restart(self):
        item = self.create("note")
        saved_map = self.client.post("/api/knowledge/maps", json=self.map_body()).json["map"]
        new_client = create_app(self.config).test_client()
        self.assertEqual(new_client.get(f"/api/knowledge/items/{item['id']}").json["item"], item)
        self.assertEqual(new_client.get(f"/api/knowledge/maps/{saved_map['id']}").json["map"], saved_map)

    def test_map_validation_cycles_detached_nodes_and_links(self):
        base = self.map_body()["nodes"]
        invalid = [[], [{**base[0], "id": "other"}], [base[0], base[0]],
                   [base[0], {**base[1], "parent": "missing"}], [base[0], {**base[1], "parent": None}],
                   [base[0], {**base[1], "parent": "one"}],
                   [base[0], {**base[1], "parent": "two"}, {**base[1], "id": "two", "parent": "one"}],
                   [base[0], {**base[1], "item_id": 999999}], [base[0], {**base[1], "item_id": True}],
                   [base[0], {**base[1], "text": ""}], [base[0], {**base[1], "text": "a" * 201}]]
        for nodes in invalid:
            response = self.client.post("/api/knowledge/maps", json=self.map_body(nodes=nodes))
            self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.client.get("/api/knowledge/maps").json["maps"], [])

    def test_map_roundtrip_deduplication_edit_conflict_and_delete(self):
        body = self.map_body()
        original = self.client.post("/api/knowledge/maps", json=body).json["map"]
        self.assertEqual(self.client.post("/api/knowledge/maps", json=body).json["map"], original)
        self.assertEqual(self.client.post("/api/knowledge/maps", json={**body, "title": "Different"}).status_code, 409)
        url = f"/api/knowledge/maps/{original['id']}"
        saved = self.client.patch(url, json={"version": 0, "title": "New title", "nodes": body["nodes"]})
        self.assertEqual(saved.json["map"]["version"], 1)
        self.assertEqual(self.client.patch(url, json={"version": 0, **body}).status_code, 409)
        self.assertEqual(self.client.delete(url, json={"version": 0}).status_code, 409)
        self.assertEqual(self.client.delete(url, json={"version": 1}).status_code, 200)

    def test_deleting_linked_item_preserves_nodes_and_increments_map_version(self):
        item = self.create("grammar")
        body = self.map_body()
        body["nodes"][1]["item_id"] = item["id"]
        saved = self.client.post("/api/knowledge/maps", json=body).json["map"]
        self.client.delete(f"/api/knowledge/items/{item['id']}", json={"version": 0})
        result = self.client.get(f"/api/knowledge/maps/{saved['id']}").json["map"]
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["nodes"][1]["text"], "A branch")
        self.assertIsNone(result["nodes"][1]["item_id"])
        self.assertEqual(self.client.patch(f"/api/knowledge/maps/{saved['id']}", json={**result, "version": 0}).status_code, 409)

    def test_upgrade_existing_database_and_cross_origin_protection(self):
        with self.app.app_context():
            get_db().executescript("DROP TABLE knowledge_maps; DROP TABLE knowledge_items;")
        client = create_app(self.config).test_client()
        self.assertEqual(client.get(f"/api/articles/{self.article}").status_code, 200)
        self.assertEqual(client.get("/api/knowledge/items").json["items"], [])
        self.assertEqual(client.post("/api/knowledge/items", json=self.body(), headers={"Origin": "https://example.com"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
