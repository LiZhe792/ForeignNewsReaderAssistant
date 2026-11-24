"""Writing persistence, immutable feedback and paid-request retry contracts."""

import json
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from app import create_app
from newsreader import ai_client
from newsreader.db import get_db
from newsreader.writing import MODES, differences


class WritingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"TESTING": True, "DATABASE": str(Path(self.temp.name) / "writing.sqlite3"),
                       "DEEPSEEK_API_KEY": "sk-writing-unit-test-key"}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.content = "A quiet journey gives readers time to think. " * 5 + "\n\n" + "The city values public gardens and shared space. " * 4
        self.article = self.client.post("/api/articles", json={"title": "The reading journey", "content": self.content}).json["id"]
        self.url = f"/api/articles/{self.article}/writing"
        self.feedback = {"summary": "观点清楚，需要调整主谓一致。", "strengths": ["有明确主题。"],
                         "corrections": [{"kind": "grammar", "before": "She go", "after": "She goes", "reason": "第三人称单数。"}],
                         "suggestions": ["可以增加一个具体例子。"], "revised": "She goes on a journey."}
        self.patcher = patch("newsreader.writing.ai_client.generate", return_value=self.feedback)
        self.provider = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def create(self, **extra):
        response = self.client.post(self.url, json={"request_id": str(uuid.uuid4()), "mode": "grammar", **extra})
        self.assertIn(response.status_code, (200, 201), response.json)
        return response.json["exercise"]

    def route(self, exercise):
        return f"/api/writing/exercises/{exercise['id']}"

    def save(self, exercise, draft="She go on a journey."):
        response = self.client.patch(self.route(exercise), json={"draft": draft, "version": exercise["version"]})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["exercise"]

    def query(self, exercise, **extra):
        return {"request_id": str(uuid.uuid4()), "draft": exercise["draft"], "version": exercise["version"], **extra}

    def submit(self, exercise, **extra):
        return self.client.post(self.route(exercise) + "/attempts", json=self.query(exercise, **extra))

    def test_reader_assets_and_all_modes_use_distinct_instructions(self):
        home = self.client.get("/").text
        for asset in ("/static/writing.js", "/static/writing.css"):
            self.assertIn(asset, home)
            with self.client.get(asset) as response:
                self.assertEqual(response.status_code, 200)
        self.assertIn('id="writing-dialog"', home)
        self.assertEqual(len(self.client.get("/api/writing/options").json["modes"]), 12)
        for mode, (label, instruction, _) in MODES.items():
            with self.subTest(mode=mode):
                source = "安静的旅行让读者有时间思考。" if mode == "zh_en" else "A quiet journey gives readers time to think."
                exercise = self.save(self.create(mode=mode, source_text=source, target_words=["journey"]))
                response = self.submit(exercise)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json["attempt"]["status"], "complete")
                sent = json.loads(self.provider.call_args.args[0][-1]["content"])
                self.assertEqual(sent["mode"], label)
                self.assertEqual(sent["instruction"], instruction)
                self.assertEqual(sent["answer_language"], "中文" if mode == "en_zh" else "英文")
                if mode == "continuation":
                    self.assertEqual(sent["source"], self.content.strip())

    def test_creating_and_writing_without_a_key_never_calls_ai(self):
        client = create_app({**self.config, "DEEPSEEK_API_KEY": ""}).test_client()
        exercise = self.create()
        saved = client.patch(self.route(exercise), json={"draft": "My first draft.", "version": 0}).json["exercise"]
        result = client.post(self.route(exercise) + "/attempts", json=self.query(saved))
        self.assertEqual(result.status_code, 400)
        self.assertIn("密钥", result.json["error"])
        self.assertEqual(client.get(self.route(exercise)).json["exercise"]["draft"], "My first draft.")
        self.provider.assert_not_called()

    def test_creation_retries_do_not_duplicate_exercises(self):
        token = str(uuid.uuid4())
        first = self.create(request_id=token)
        self.assertEqual(self.create(request_id=token)["id"], first["id"])
        result = self.client.post(self.url, json={"request_id": token, "mode": "expression"})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(len(self.client.get(self.url).json["exercises"]), 1)

    def test_writing_can_configure_the_shared_client_without_article_qa_routes(self):
        client = create_app({**self.config, "DEEPSEEK_API_KEY": ""}).test_client()
        self.assertFalse(client.get("/api/writing/settings").json["configured"])
        response = client.patch("/api/writing/settings", json={"api_key": "sk-writing-config-fixture", "model": "deepseek-flash"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["configured"])
        self.assertNotIn("sk-writing-config-fixture", response.text)
        restarted = create_app({**self.config, "DEEPSEEK_API_KEY": ""})
        with restarted.app_context():
            self.assertEqual(ai_client.credentials()[0], "sk-writing-config-fixture")
        self.assertTrue(client.patch("/api/writing/settings", json={"api_key": "", "model": "deepseek-flash"}).json["configured"])
        self.assertEqual(client.patch("/api/writing/settings", json={"model": "unknown"}).status_code, 400)
        self.assertEqual(client.patch("/api/writing/settings", json={"api_key": "bad\r\nheader"}).status_code, 400)
        self.assertEqual(self.client.patch("/api/writing/settings", json={"api_key": "sk-new-fixture-key"}).status_code, 400)
        self.provider.assert_not_called()

    def test_validation_rejects_empty_invalid_and_forged_material(self):
        invalid = [{"mode": []}, {"mode": "imitation"}, {"mode": "zh_en", "source_text": "Only English"},
                   {"mode": "en_zh", "source_text": "只有中文"}, {"mode": "vocabulary", "target_words": []},
                   {"mode": "vocabulary", "target_words": ["one", "two", "three", "four", "five", "six"]},
                   {"mode": "vocabulary", "target_words": [True]}, {"mode": "vocabulary", "target_words": ["<script>"]},
                   {"mode": "summary", "source_kind": "article", "source_text": "A made-up article quotation."},
                   {"mode": "grammar", "focus": "x" * 501}]
        for data in invalid:
            with self.subTest(data=data):
                response = self.client.post(self.url, json={"request_id": str(uuid.uuid4()), **data})
                self.assertEqual(response.status_code, 400)
        self.provider.assert_not_called()

    def test_selected_material_is_verified_and_preserved(self):
        selected = "A quiet journey gives readers time to think."
        exercise = self.create(mode="imitation", source_kind="article", source_text=selected)
        self.assertEqual(exercise["source_text"], selected)
        self.assertEqual(exercise["source_kind"], "article")

    def test_draft_version_conflict_and_idempotent_saves(self):
        exercise = self.create()
        saved = self.save(exercise, "First tab's version.")
        self.assertEqual(saved["version"], 1)
        same = self.client.patch(self.route(exercise), json={"draft": saved["draft"], "version": 0})
        self.assertEqual(same.status_code, 200)
        stale = self.client.patch(self.route(exercise), json={"draft": "Second tab's version.", "version": 0})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json["exercise"]["draft"], saved["draft"])
        for data in ({"version": True, "draft": "x"}, {"version": 1, "draft": "x" * 4001}, {"version": 1, "draft": [], "mode": "grammar"}):
            self.assertEqual(self.client.patch(self.route(exercise), json=data).status_code, 400)

    def test_original_is_immutable_across_draft_changes_and_restart(self):
        exercise = self.save(self.create())
        first = self.submit(exercise).json["attempt"]
        exercise = self.save(exercise, "Now I have a different opinion.")
        second = self.submit(exercise).json["attempt"]
        restored = create_app(self.config).test_client().get(self.route(exercise)).json
        self.assertEqual(restored["exercise"]["draft"], exercise["draft"])
        self.assertEqual([a["original"] for a in restored["attempts"]], [second["original"], first["original"]])
        self.assertEqual(first["original"], "She go on a journey.")
        self.assertNotIn("request_id", first)
        self.assertNotIn("sk-writing", json.dumps(restored))

    def test_unsaved_and_empty_drafts_do_not_start_paid_requests(self):
        exercise = self.create()
        for changes in ({"draft": "Unsaved draft."}, {"draft": ""}, {"draft": "  "}, {"version": True}):
            self.assertIn(self.submit(exercise, **changes).status_code, (400, 409))
        self.provider.assert_not_called()

    def test_completed_and_failed_requests_are_idempotent(self):
        exercise = self.save(self.create())
        query = self.query(exercise)
        route = self.route(exercise) + "/attempts"
        first = self.client.post(route, json=query)
        repeated = self.client.post(route, json=query)
        self.assertEqual(first.json["attempt"]["id"], repeated.json["attempt"]["id"])
        self.assertEqual(self.provider.call_count, 1)
        self.assertEqual(self.client.post(route, json={**query, "draft": "Other text"}).status_code, 409)
        self.provider.side_effect = ai_client.AIError("模拟余额不足", 402)
        query = self.query(exercise)
        failed = self.client.post(route, json=query).json["attempt"]
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.client.post(route, json=query).json["attempt"]["id"], failed["id"])
        self.assertEqual(self.provider.call_count, 2)

    def test_concurrent_submits_and_deletion_are_serialized(self):
        exercise = self.save(self.create())
        query, entered, release = self.query(exercise), Event(), Event()
        def generate(*args):
            entered.set()
            self.assertTrue(release.wait(10))
            return self.feedback
        self.provider.side_effect = generate
        route = self.route(exercise)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(lambda: self.app.test_client().post(route + "/attempts", json=query))
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.client.post(route + "/attempts", json=query).status_code, 202)
                self.assertEqual(self.submit(exercise).status_code, 409)
                self.assertEqual(self.client.delete(route, json={}).status_code, 409)
                self.assertEqual(self.client.delete(f"/api/articles/{self.article}", json={}).status_code, 409)
                self.save(exercise, "A newer draft written while feedback is pending.")
            finally:
                release.set()
            result = pending.result().json["attempt"]
        self.assertEqual(result["original"], query["draft"])
        self.assertEqual(self.provider.call_count, 1)

    def test_invalid_ai_feedback_keeps_original_and_can_be_retried(self):
        exercise = self.save(self.create())
        for invalid in ({}, {**self.feedback, "revised": []}, {**self.feedback, "corrections": "invalid"}):
            self.provider.return_value = invalid
            result = self.submit(exercise).json["attempt"]
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["original"], exercise["draft"])
        self.provider.return_value = self.feedback
        self.assertEqual(self.submit(exercise).json["attempt"]["status"], "complete")

    def test_correction_quotes_are_checked_against_student_original(self):
        self.provider.return_value = {**self.feedback, "corrections": self.feedback["corrections"] +
            [{"kind": "grammar", "before": "Invented quote", "after": "Invented correction", "reason": "Invalid"}]}
        result = self.submit(self.save(self.create())).json["attempt"]["feedback"]
        self.assertEqual(len(result["corrections"]), 1)
        self.assertIn("隐藏", result["warning"])

    def test_target_usage_handles_whole_words_phrases_and_case(self):
        exercise = self.save(self.create(mode="vocabulary", target_words=["art", "journey", "pay attention to"]),
                             "On this JOURNEY we pay  attention to the garden party.")
        usage = self.submit(exercise).json["attempt"]["feedback"]["target_usage"]
        self.assertEqual([item["used"] for item in usage], [False, True, True])

    def test_comparison_preserves_english_chinese_html_and_whitespace(self):
        for before, after in (("She go.\n", "She goes.\n"), ("城市 很安靜。", "城市很安静。"),
                              ("<img src=x>  🌍", "<b>World</b> 🌎"), ("same", "same")):
            result = differences(before, after)
            self.assertEqual("".join(item["text"] for item in result["before"]), before)
            self.assertEqual("".join(item["text"] for item in result["after"]), after)

    def test_history_is_article_scoped_paginated_and_deletable(self):
        ids = [self.create()["id"] for _ in range(32)]
        first = self.client.get(self.url).json
        self.assertEqual(len(first["exercises"]), 30)
        second = self.client.get(self.url + "?before=" + str(first["next_cursor"])).json
        self.assertEqual([row["id"] for row in second["exercises"]], ids[:2][::-1])
        exercise = self.save(self.create())
        self.submit(exercise)
        other = self.client.get("/api/articles").json["articles"][0]["id"]
        if other == self.article:
            other = self.client.get("/api/articles").json["articles"][1]["id"]
        self.assertEqual(self.client.get(f"/api/articles/{other}/writing").json["exercises"], [])
        self.assertEqual(self.client.delete(self.route(exercise), json={}).status_code, 200)
        self.assertEqual(self.client.get(self.route(exercise)).status_code, 404)
        self.client.delete(f"/api/articles/{self.article}", json={})
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM writing_exercises").fetchone()[0], 0)
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM writing_attempts").fetchone()[0], 0)

    def test_interrupted_request_expires_without_automatic_ai_retry(self):
        exercise = self.save(self.create())
        with self.app.app_context():
            db = get_db()
            db.execute("""INSERT INTO writing_attempts (exercise_id,request_id,original,status,model,created_at,updated_at)
                VALUES (?,?,?,'pending','test','2020-01-01T00:00:00+08:00','2020-01-01T00:00:00+08:00')""",
                (exercise["id"], str(uuid.uuid4()), exercise["draft"]))
            db.commit()
        result = self.client.get(self.route(exercise)).json
        self.assertEqual(result["attempts"][0]["status"], "failed")
        self.provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
