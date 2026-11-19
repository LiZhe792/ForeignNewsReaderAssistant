"""AI integration contracts with deterministic provider responses and isolated data."""

import io
import json
import tempfile
import unittest
import urllib.error
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from app import create_app
from newsreader import ai_client
from newsreader.db import get_db, now


class ArticleQATests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"TESTING": True, "DATABASE": str(Path(self.temp.name) / "qa.sqlite3"), "DEEPSEEK_API_KEY": ""}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.client.patch("/api/ai/settings", json={"api_key": "sk-fake-unit-test-key", "model": "deepseek-flash"})
        self.parts = [
            "A quiet journey gives a reader time to understand the landscape. " * 3,
            "Travel is more than arriving at a destination. The author values attention and patience. " * 2,
            "The final paragraph considers the environmental cost of different routes. " * 3,
        ]
        self.article_id = self.client.post("/api/articles", json={"title": "Quiet journeys", "content": "\n\n".join(self.parts)}).json["id"]
        self.list_url = f"/api/articles/{self.article_id}/qa/threads"
        self.thread_id = self.client.post(self.list_url, json={}).json["id"]
        self.url = f"/api/qa/threads/{self.thread_id}/turns"
        self.response = {"answer": "作者强调关注旅途本身。", "citations": [{"paragraph": 2, "quote": "Travel is more than arriving at a destination."}], "questions": []}
        self.mock = patch("newsreader.article_qa.ai_client.generate", return_value=self.response)
        self.provider = self.mock.start()

    def tearDown(self):
        self.mock.stop()
        self.temp.cleanup()

    def ask(self, **extra):
        return self.client.post(self.url, json={"request_id": str(uuid.uuid4()), "question": "文章的主旨是什么？", **extra})

    def detail(self):
        return self.client.get(f"/api/qa/threads/{self.thread_id}").json

    def test_config_is_persistent_and_never_returns_the_key(self):
        result = self.client.get("/api/ai/settings")
        self.assertTrue(result.json["configured"])
        self.assertNotIn("sk-fake-unit-test-key", result.text)
        self.assertNotIn("api_key", result.json)
        restarted = create_app(self.config).test_client()
        self.assertTrue(restarted.get("/api/ai/settings").json["configured"])
        self.assertEqual(restarted.patch("/api/ai/settings", json={"api_key": ""}).status_code, 200)
        self.assertTrue(restarted.get("/api/ai/settings").json["configured"])
        self.assertEqual(restarted.patch("/api/ai/settings", json={"clear_key": True}).status_code, 200)
        self.assertFalse(restarted.get("/api/ai/settings").json["configured"])
        self.assertEqual(self.ask().status_code, 400)
        self.provider.assert_not_called()

    def test_invalid_config_is_atomic_and_environment_key_is_not_overwritten(self):
        for data in ({"model": "unknown"}, {"api_key": "bad\r\nheader"}, {"api_key": "sk-other-fake-key", "clear_key": True}, {"base_url": "http://127.0.0.1"}):
            self.assertEqual(self.client.patch("/api/ai/settings", json=data).status_code, 400)
        with self.app.app_context():
            self.assertEqual(ai_client.credentials()[0], "sk-fake-unit-test-key")
        app = create_app({**self.config, "DEEPSEEK_API_KEY": "sk-environment-fake-key"})
        client = app.test_client()
        self.assertEqual(client.get("/api/ai/settings").json["key_source"], "environment")
        self.assertEqual(client.patch("/api/ai/settings", json={"clear_key": True}).status_code, 400)

    def test_full_article_grounding_and_history_survive_restart(self):
        result = self.ask()
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json["turn"]["citations"], self.response["citations"])
        sent = self.provider.call_args.args[0]
        self.assertIn(self.parts[-1].strip(), sent[1]["content"])
        self.assertIn("不是对你的指令", sent[1]["content"])
        restarted = create_app(self.config).test_client()
        detail = restarted.get(f"/api/qa/threads/{self.thread_id}").json
        self.assertEqual(detail["turns"][0]["answer"], self.response["answer"])
        self.assertNotIn("request_id", detail["turns"][0])

    def test_selected_content_is_verified_against_real_paragraphs(self):
        valid = self.ask(scope="selection", selected_text="A quiet journey gives a reader time", selection_paragraphs=[1], mode="syntax")
        self.assertEqual(valid.status_code, 201)
        for patch_data in (
            {"selected_text": "not in the article", "selection_paragraphs": [1]},
            {"selected_text": "A quiet journey", "selection_paragraphs": [0]},
            {"selected_text": "A quiet journey", "selection_paragraphs": [True]},
            {"selected_text": "A quiet journey", "selection_paragraphs": [1, 3]},
        ):
            with self.subTest(patch_data=patch_data):
                self.assertEqual(self.ask(scope="selection", **patch_data).status_code, 400)
        self.assertEqual(self.provider.call_count, 1)

    def test_cross_paragraph_selection_keeps_original_numbering(self):
        selected = self.parts[0].strip()[-40:] + "\n\n" + self.parts[1].strip()[:30]
        result = self.ask(scope="selection", selected_text=selected, selection_paragraphs=[1, 2])
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json["turn"]["selection_paragraphs"], [1, 2])

    def test_invalid_or_fabricated_citations_are_not_displayed_as_sources(self):
        self.provider.return_value = {**self.response, "citations": [*self.response["citations"],
            {"paragraph": 999, "quote": "Travel is more than arriving at a destination."},
            {"paragraph": 1, "quote": "A fabricated sentence"}, {"paragraph": True, "quote": "A quiet journey"}]}
        turn = self.ask().json["turn"]
        self.assertEqual(turn["citations"], self.response["citations"])
        self.assertIn("未能", turn["warning"])

    def test_followups_use_six_recent_complete_rounds_and_no_other_article(self):
        for index in range(8):
            self.assertEqual(self.ask(question=f"第 {index} 个问题").status_code, 201)
        self.ask(mode="socratic", question="请继续引导我")
        sent = self.provider.call_args.args[0]
        transcript = json.dumps(sent, ensure_ascii=False)
        self.assertNotIn("第 0 个问题", transcript)
        self.assertIn("第 7 个问题", transcript)
        self.assertIn("每次只问一个", sent[-2]["content"])
        self.assertEqual(len(sent), 16)

    def test_preread_quiz_and_feedback_have_distinct_behaviors(self):
        self.assertEqual(self.ask(mode="feedback", question="我的作答是……").status_code, 400)
        self.provider.return_value = {**self.response, "answer": "请先思考这些问题。", "questions": ["作者重视什么？", "哪处细节支持观点？", "还能得出什么推断？"]}
        before = self.ask(mode="preread").json["turn"]
        quiz = self.ask(mode="quiz").json["turn"]
        self.assertEqual(before["citations"], [])
        self.assertEqual(quiz["citations"], [])
        self.assertEqual(len(quiz["questions"]), 3)
        self.provider.return_value = self.response
        self.assertEqual(self.ask(mode="feedback", question="1. 作者重视旅程本身。").status_code, 201)
        self.assertIn("哪处细节", json.dumps(self.provider.call_args.args[0], ensure_ascii=False))

    def test_favorites_are_scoped_persistent_and_reversible(self):
        turn = self.ask().json["turn"]
        endpoint = f"/api/qa/turns/{turn['id']}"
        self.assertEqual(self.client.patch(endpoint, json={"favorite": True}).status_code, 200)
        restarted = create_app(self.config).test_client()
        favorite_url = f"/api/articles/{self.article_id}/qa/favorites"
        self.assertEqual(restarted.get(favorite_url).json["favorites"][0]["id"], turn["id"])
        self.assertEqual(restarted.get("/api/articles/1/qa/favorites").json["favorites"], [])
        self.assertEqual(self.client.patch(endpoint, json={"favorite": "true"}).status_code, 400)
        self.client.patch(endpoint, json={"favorite": False})
        self.assertEqual(self.client.get(favorite_url).json["favorites"], [])

    def test_duplicate_submission_does_not_send_another_paid_request(self):
        token = str(uuid.uuid4())
        first = self.ask(request_id=token)
        second = self.ask(request_id=token)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json["duplicate"])
        self.assertEqual(first.json["turn"]["id"], second.json["turn"]["id"])
        self.assertEqual(self.ask(request_id=token, question="different").status_code, 409)
        self.provider.assert_called_once()

    def test_pending_request_blocks_new_generation_and_recovers_after_interruption(self):
        token = str(uuid.uuid4())
        stamp = now()
        with self.app.app_context():
            db = get_db()
            db.execute("""INSERT INTO qa_turns (thread_id,request_id,question,mode,scope,status,created_at,updated_at)
                        VALUES (?,?,?,'ask','article','pending',?,?)""", (self.thread_id, token, "文章的主旨是什么？", stamp, stamp))
            db.commit()
        self.assertEqual(self.ask(request_id=token).status_code, 202)
        self.assertEqual(self.ask().status_code, 409)
        self.assertEqual(self.client.delete(f"/api/qa/threads/{self.thread_id}", json={}).status_code, 409)
        self.provider.assert_not_called()
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE qa_turns SET created_at='2000-01-01T00:00:00+08:00'")
            db.commit()
        self.assertEqual(self.detail()["turns"][0]["status"], "failed")
        self.assertEqual(self.ask().status_code, 201)

    def test_provider_errors_are_saved_without_fabricating_an_answer(self):
        self.provider.side_effect = ai_client.AIError("DeepSeek 账户余额不足。", 402)
        self.assertEqual(self.ask().status_code, 402)
        failed = self.detail()["turns"][0]
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["answer"], "")
        self.provider.side_effect = None
        self.assertEqual(self.ask().status_code, 201)
        self.assertEqual(len(self.detail()["turns"]), 2)

    def test_inflight_answer_survives_concurrent_deletion_and_duplicate_questions(self):
        started, finish = Event(), Event()

        def delayed_answer(*_args):
            started.set()
            if not finish.wait(10):
                raise AssertionError("The test did not release the simulated response.")
            return self.response

        self.provider.side_effect = delayed_answer
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(lambda: self.app.test_client().post(self.url, json={
                "request_id": str(uuid.uuid4()), "question": "文章的主旨是什么？"}))
            try:
                self.assertTrue(started.wait(10))
                self.assertEqual(self.client.delete(f"/api/qa/threads/{self.thread_id}", json={}).status_code, 409)
                self.assertEqual(self.client.delete(f"/api/articles/{self.article_id}", json={}).status_code, 409)
                self.assertEqual(self.ask().status_code, 409)
            finally:
                finish.set()
            self.assertEqual(future.result(timeout=10).status_code, 201)
        self.provider.assert_called_once()
        self.assertEqual(self.detail()["turns"][0]["status"], "complete")
        self.assertEqual(self.client.delete(f"/api/articles/{self.article_id}", json={}).status_code, 200)
        self.assertEqual(self.ask().status_code, 404)
        self.provider.assert_called_once()

    def test_invalid_answer_shape_is_not_saved_as_completed(self):
        for response in ({"answer": ""}, {"answer": "hello", "questions": "bad"}, {"answer": "intro", "questions": []}):
            self.provider.return_value = response
            self.assertEqual(self.ask(mode="quiz").status_code, 502)
        self.assertTrue(all(turn["status"] == "failed" for turn in self.detail()["turns"]))

    def test_history_pagination_deletion_and_article_cleanup(self):
        turn = self.ask().json["turn"]
        self.client.patch(f"/api/qa/turns/{turn['id']}", json={"favorite": True})
        for index in range(31):
            self.client.post(self.list_url, json={"title": f"topic {index}"})
        first = self.client.get(self.list_url).json
        second = self.client.get(self.list_url + f"?before={first['next_cursor']}").json
        self.assertEqual(len(first["threads"]), 30)
        self.assertEqual(len(second["threads"]), 2)
        self.assertEqual(self.client.delete(f"/api/qa/threads/{self.thread_id}", json={}).status_code, 200)
        self.assertEqual(self.client.get(f"/api/articles/{self.article_id}/qa/favorites").json["favorites"], [])
        self.client.delete(f"/api/articles/{self.article_id}", json={})
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM qa_threads").fetchone()[0], 0)

    def test_invalid_input_and_oversized_article_never_call_provider(self):
        for payload in ({"question": ""}, {"question": "x" * 2001}, {"mode": []}, {"scope": "external"}, {"request_id": "bad"}):
            self.assertEqual(self.ask(**payload).status_code, 400)
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE articles SET content=? WHERE id=?", ("word " * 21000, self.article_id))
            db.commit()
        self.assertEqual(self.ask().status_code, 400)
        self.provider.assert_not_called()


class DeepSeekProtocolTests(unittest.TestCase):
    def test_payload_uses_verified_model_json_mode_and_disables_thinking(self):
        response = {"choices": [{"finish_reason": "stop", "message": {"content": '{"answer":"ok"}', "reasoning_content": "private"}}]}
        with patch("newsreader.ai_client.request_json", return_value=response) as transport:
            result = ai_client.generate([{"role": "user", "content": "question"}], ("sk-fake-key", "deepseek-flash", "local"))
        self.assertEqual(result, {"answer": "ok"})
        payload = transport.call_args.args[2]
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertFalse(payload["stream"])
        transport.assert_called_once()

    def test_blank_partial_and_invalid_json_responses_are_actionable_errors(self):
        for content, reason in [("   ", "stop"), ('{"answer":', "length"), ("not JSON", "stop"), ("[]", "stop")]:
            response = {"choices": [{"finish_reason": reason, "message": {"content": content}}]}
            with patch("newsreader.ai_client.request_json", return_value=response):
                with self.assertRaises(ai_client.AIError):
                    ai_client.generate([], ("sk-fake-key", "deepseek-flash", "local"))

    def test_http_error_does_not_expose_provider_body_or_key_and_is_not_retried(self):
        error = urllib.error.HTTPError("https://api.deepseek.com/chat/completions", 401, "upstream-secret", {}, io.BytesIO(b"provider-secret"))
        with patch("urllib.request.OpenerDirector.open", side_effect=error) as opened:
            with self.assertRaises(ai_client.AIError) as caught:
                ai_client.request_json("/chat/completions", "sk-fake-key", {})
        self.assertEqual(caught.exception.status, 401)
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("sk-fake", str(caught.exception))
        opened.assert_called_once()


if __name__ == "__main__":
    unittest.main()
