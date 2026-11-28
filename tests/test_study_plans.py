"""Study plans use real learning events and a controlled Beijing clock."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from app import create_app
from newsreader.db import CHINA_TZ, get_db
from newsreader.study_plans import PLAN_TABLES


class StudyPlanTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "learning.sqlite3"
        self.current = datetime(2026, 11, 25, 20, 1, tzinfo=CHINA_TZ)
        for target in ("app.today", "newsreader.study_plans.today"):
            change = patch(target, side_effect=lambda: self.current.date())
            change.start()
            self.addCleanup(change.stop)
        change = patch("newsreader.study_plans.clock", side_effect=lambda: self.current)
        change.start()
        self.addCleanup(change.stop)
        self.app = create_app({"DATABASE": str(self.path), "TESTING": True})
        self.client = self.app.test_client()

    def call(self, method, path, data=None, status=200, headers=None):
        response = self.client.open("/api/" + path, method=method, json=data, headers=headers)
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        return response.json

    def sql(self, query, args=()):
        with self.app.app_context():
            connection = get_db()
            rows = connection.execute(query, args).fetchall()
            connection.commit()
            return rows

    def plan(self, **fields):
        data = {
            "title": "本周精读", "kind": "week", "start_day": "2026-11-23",
            "end_day": "2026-11-29", "reading_minutes": 10, "articles": 3, "new_words": 4,
            "steps": [{"text": "归纳一篇文章的主旨", "done": False}], **fields,
        }
        return self.call("POST", "planning/plans", data, 201)["plan"]

    def test_daily_goals_apply_to_sidebar_and_use_new_entries_not_reviews(self):
        api = self.call("GET", "planning")
        self.assertEqual(api["goals"]["reading_minutes"], 15)
        self.call("PATCH", "planning/goals", {"version": 0, "reading_minutes": 2, "new_words": 3})
        self.call("PATCH", "planning/goals", {"version": 0, "reading_minutes": 8}, 409)
        self.call("PATCH", "articles/1/progress", {"seconds": 60, "progress": 12, "position": 12})
        with patch("app.now", return_value=self.current.isoformat()):
            self.call("POST", "vocabulary", {"word": "journey", "meaning": "旅程"}, 201)
            self.call("POST", "vocabulary", {"word": "journey", "meaning": "行程"})
        self.assertEqual(self.call("GET", "stats")["daily_goals"], {"reading_minutes": 2, "new_words": 3, "words_today": 1})
        self.assertEqual(self.call("GET", "planning")["progress"]["seconds"], 60)

    def test_week_split_preserves_totals_and_caps_at_today(self):
        self.sql("INSERT INTO checkins VALUES('2026-11-25',240,2,0),('2026-11-28',900,7,0)")
        plan = self.plan()
        self.assertEqual(len(plan["days"]), 7)
        for key, value in plan["targets"].items():
            self.assertEqual(sum(day["target"][key] for day in plan["days"]), value)
        self.assertEqual(plan["totals"]["seconds"], 240)
        self.assertEqual(plan["totals"]["articles"], 2)
        self.assertIsNone(plan["days"][-2]["actual"])
        self.assertFalse(plan["completed"])

    def test_topic_plan_uses_category_not_global_daily_totals(self):
        article = self.call("POST", "articles", {"title": "A technical reading", "content": "A city can learn from its trees. " * 10, "category": "科技"}, 201)["id"]
        self.call("PATCH", f"articles/{article}/progress", {"seconds": 60, "progress": 100, "position": 100})
        self.call("PATCH", "articles/1/progress", {"seconds": 30, "progress": 50, "position": 50})
        plan = self.plan(kind="topic", category="科技", new_words=0, reading_minutes=1, articles=1, steps=[])
        self.assertEqual(plan["totals"]["seconds"], 60)
        self.assertTrue(plan["completed"])
        self.call("DELETE", f"articles/{article}", {})
        saved = self.call("GET", f"planning/plans/{plan['id']}")["plan"]
        self.assertEqual(saved["totals"]["articles"], 1)

    def test_steps_and_reflection_survive_reload_and_edit_conflicts(self):
        plan = self.plan(reading_minutes=0, articles=0, new_words=0)
        path = f"planning/plans/{plan['id']}"
        change = self.call("PATCH", path, {"version": 1, "steps": [{"text": plan["steps"][0]["text"], "done": True}]})["plan"]
        self.assertTrue(change["completed"])
        self.call("PATCH", path, {"version": 1, "reflection": "旧笔记"}, 409)
        self.call("PATCH", path, {"version": 2, "reflection": "回顾主旨，练习略读。"})
        client = create_app({"DATABASE": str(self.path), "TESTING": True}).test_client()
        restored = client.get("/api/" + path).json["plan"]
        self.assertEqual(restored["reflection"], "回顾主旨，练习略读。")
        self.assertTrue(restored["steps"][0]["done"])
        self.call("DELETE", path, {"version": 3})
        self.call("GET", path, status=404)

    def test_invalid_fields_do_not_create_or_mutate(self):
        for data in ({"version": 0, "new_words": True}, {"version": 0, "review_reminder": "24:00"}, {"version": 0, "extra": 1}):
            self.call("PATCH", "planning/goals", data, 400)
        base = {"title": "计划", "kind": "week", "start_day": "2026-11-23", "end_day": "2026-11-29", "reading_minutes": 10}
        for change in ({"start_day": "2026-11-24"}, {"end_day": "2026-11-30"}, {"articles": True},
                       {"kind": "topic", "category": "未知"}, {"steps": [{"text":"X","done":1}]}, {"title": " "}):
            self.call("POST", "planning/plans", {**base, **change}, 400)
        self.assertEqual(self.call("GET", "planning")["plans"], [])

    def test_makeup_connects_streak_without_fabricating_activity_or_achievement(self):
        for value in ("2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23", "2026-11-25"):
            self.sql("INSERT INTO checkins(date,manual) VALUES(?,1)", (value,))
        self.call("POST", "planning/makeups", {"day":"2026-11-24", "note":"补记"}, 201)
        self.assertTrue(self.call("POST", "planning/makeups", {"day":"2026-11-24"})["duplicate"])
        result = self.call("GET", "planning")
        self.assertEqual(result["continuity"], {"current":7,"longest":7,"study_days":6,"makeups":1})
        self.assertFalse(result["achievements"][-1]["earned"])
        stats = self.call("GET", "stats?month=2026-11")
        self.assertEqual(stats["streak"],7)
        self.assertEqual(len(stats["days"]),6)
        self.assertEqual(stats["makeups"][0]["note"],"补记")
        self.assertEqual(self.call("GET", "analytics/report?period=month&date=2026-11-25")["totals"]["seconds"],0)

    def test_makeup_date_window_rejects_today_future_and_real_checkins(self):
        self.call("POST", "checkin", {})
        self.sql("INSERT INTO checkins(date,manual) VALUES('2026-11-24',1)")
        self.call("POST", "planning/makeups", {"day":"2026-11-24"}, 409)
        for value in ("2026-11-26","2026-11-25","2026-11-17","2026-02-30"):
            self.call("POST", "planning/makeups", {"day":value}, 400)
        self.call("POST", "planning/makeups", {"day":"2026-11-18"}, 201)

    def test_reminders_trigger_after_local_time_with_goals_and_due_cards(self):
        self.call("PATCH", "planning/goals", {"version":0,"reading_reminder":"20:02","review_reminder":"20:02"})
        with patch("app.now", return_value=self.current.isoformat()):
            word = self.call("POST", "vocabulary", {"word":"journey","meaning":"旅程"}, 201)["id"]
        self.assertFalse(self.call("POST", "planning/reminders/claim", {})["reminders"])
        self.current += timedelta(minutes=2)
        self.sql("UPDATE srs_cards SET state='review',due_at=? WHERE word_id=?", (self.current.isoformat(),word))
        result = self.call("POST", "planning/reminders/claim", {})
        self.assertEqual(result["reminders"], [{"kind":"reading","due":None},{"kind":"review","due":1}])
        self.assertFalse(self.call("POST", "planning/reminders/claim", {})["reminders"])
        self.current += timedelta(days=1)
        self.assertEqual(len(self.call("POST", "planning/reminders/claim", {})["reminders"]),2)

    def test_accomplished_goals_and_suspended_or_new_words_do_not_trigger(self):
        self.call("PATCH", "planning/goals", {"version":0,"reading_minutes":0,"new_words":0,"reading_reminder":"08:00","review_reminder":"08:00"})
        word = self.call("POST", "vocabulary", {"word":"journey","meaning":"旅程"}, 201)["id"]
        self.assertFalse(self.call("POST", "planning/reminders/claim", {})["reminders"])
        self.sql("UPDATE srs_cards SET state='review',suspended=1,due_at=? WHERE word_id=?", (self.current.isoformat(),word))
        self.assertFalse(self.call("POST", "planning/reminders/claim", {})["reminders"])

    def test_two_tabs_claim_only_one_reminder(self):
        self.call("PATCH", "planning/goals", {"version":0,"reading_reminder":"08:00"})
        def request_reminder(_):
            with self.app.test_client() as client:
                return len(client.post("/api/planning/reminders/claim", json={}).json["reminders"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(request_reminder, range(2))), 1)

    def backup(self):
        record = self.call("POST", "data/backups", {}, 201)["backup"]
        with self.client.get("/api/data/backups/"+record["id"]+"/download") as response:
            return response.data

    def restore(self, raw):
        response = self.client.post("/api/data/restore/preview", data={"file":(io.BytesIO(raw),"backup.zip")})
        self.assertEqual(response.status_code, 201, response.json)
        preview = response.json["preview"]
        return self.call("POST", "data/restore/apply", {
            **{k:preview[k] for k in ("token","generation","revision")}, "request_id":"study_restore_123", "replace":True,
        })

    def test_complete_backup_retains_plans_and_makeups(self):
        plan = self.plan(reflection="阶段总结")
        self.call("POST", "planning/makeups", {"day":"2026-11-24"}, 201)
        raw = self.backup()
        self.call("DELETE", f"planning/plans/{plan['id']}", {"version":1})
        restored = self.restore(raw)
        self.assertEqual(restored["restored"]["tables"]["study_plans"],1)
        self.assertEqual(self.call("GET", f"planning/plans/{plan['id']}")["plan"]["reflection"],"阶段总结")
        self.assertEqual(self.call("GET", "planning")["continuity"]["makeups"],1)

    def test_backup_from_previous_feature_version_restores_with_empty_plans(self):
        raw = self.backup()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            path = self.path.with_name("previous.sqlite3")
            path.write_bytes(archive.read("database.sqlite3"))
        connection = sqlite3.connect(path)
        for table in PLAN_TABLES:
            connection.execute(f"DROP TABLE {table}")
        connection.commit()
        connection.close()
        database = path.read_bytes()
        manifest.update(database_size=len(database), database_sha256=hashlib.sha256(database).hexdigest())
        output = io.BytesIO()
        with zipfile.ZipFile(output,"w") as archive:
            archive.writestr("database.sqlite3",database)
            archive.writestr("manifest.json",json.dumps(manifest))
        self.plan()
        restored = self.restore(output.getvalue())
        self.assertEqual(restored["restored"]["tables"]["study_plans"],0)
        self.assertEqual(self.call("GET","planning")["plans"],[])


if __name__ == "__main__":
    unittest.main()
