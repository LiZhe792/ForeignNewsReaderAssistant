import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from app import create_app as reader_app
from newsreader.writing_workspace import API, COOKIE
from writing_server import create_app


class WritingWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / "writing.sqlite3")
        self.app = create_app({
            "TESTING": True, "WRITING_DATABASE": self.database,
            "TRUSTED_HOSTS": ["writer.example"], "WRITING_SIGNUP_CODE": "sample-code",
        })
        self.url = "https://writer.example"

    def call(self, client, path, method="GET", data=None, account=None, **kwargs):
        headers = kwargs.pop("headers", {})
        if account:
            headers["X-Margin-Writing-Account"] = account["id"]
        return client.open(API + path, base_url=self.url, method=method,
                           json=data, headers=headers, **kwargs)

    def login(self, client, username="reader.one", register=False):
        response = self.call(client, "/session", "POST", {
            "username": username, "password": "test passphrase", "register": register,
            "invite": "sample-code",
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIn("Secure", response.headers["Set-Cookie"])
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", response.headers["Set-Cookie"])
        return response.json["account"]

    def save(self, client, account, item_id, content, version=0):
        return self.call(client, "/drafts/" + item_id, "PUT",
                         {"title": "  Reader's draft  ", "content": content, "version": version},
                         account=account)

    def test_same_account_across_devices_and_restart(self):
        first = self.app.test_client()
        account = self.login(first, register=True)
        item_id = uuid4().hex
        self.assertEqual(self.save(first, account, item_id, "First edit").json["draft"]["version"], 1)
        self.assertEqual(self.save(first, account, item_id, "First edit").json["draft"]["version"], 1)

        second_app = create_app(dict(self.app.config))
        second = second_app.test_client()
        self.assertEqual(self.login(second, "Reader.One"), account)
        listed = self.call(second, "/drafts", account=account).json["drafts"]
        self.assertEqual([(item["id"], item["title"]) for item in listed], [(item_id, "Reader's draft")])
        self.assertEqual(self.save(second, account, item_id, "Second edit", 1).json["draft"]["version"], 2)
        stale = self.save(first, account, item_id, "Stale edit", 1)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json["draft"]["content"], "Second edit")

    def test_simultaneous_edits_cannot_overwrite_each_other(self):
        first, second = self.app.test_client(), self.app.test_client()
        account = self.login(first, register=True)
        self.login(second)
        item_id = uuid4().hex
        self.save(first, account, item_id, "Starting draft")
        with ThreadPoolExecutor(max_workers=2) as pool:
            edits = [
                pool.submit(self.save, client, account, item_id, content, 1)
                for client, content in [(first, "Edit A"), (second, "Edit B")]
            ]
        responses = [edit.result() for edit in edits]
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        self.assertEqual(responses[0].json["draft"], responses[1].json["draft"])

    def test_account_isolation_and_stale_tab_guard(self):
        first, second = self.app.test_client(), self.app.test_client()
        one = self.login(first, register=True)
        two = self.login(second, "reader.two", register=True)
        item_id = uuid4().hex
        self.save(first, one, item_id, "Private")
        self.assertEqual(self.call(second, "/drafts", account=two).json["drafts"], [])
        self.assertEqual(self.call(second, "/drafts/" + item_id, account=two).status_code, 404)
        self.assertEqual(self.call(second, "/drafts", account=one).json["code"], "account_changed")
        self.assertEqual(self.save(second, one, uuid4().hex, "Wrong account").status_code, 409)
        self.assertEqual(self.call(self.app.test_client(), "/drafts", account=one).status_code, 401)

    def test_session_revocation_and_password_storage(self):
        first, second = self.app.test_client(), self.app.test_client()
        account = self.login(first, register=True)
        token = first.get_cookie(COOKIE, domain="writer.example", path=API).value
        self.login(second)
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertTrue(db.execute("SELECT password_hash FROM accounts").fetchone()[0].startswith("scrypt:"))
            self.assertNotIn(token, [row[0] for row in db.execute("SELECT token_hash FROM sessions")])
        self.assertEqual(self.call(first, "/session", "DELETE", {}, account=account).status_code, 200)
        self.assertIsNone(self.call(first, "/session").json["account"])
        self.assertEqual(self.call(second, "/session").json["account"], account)
        self.login(first)
        self.call(first, "/session", "DELETE", {"all_devices": True}, account=account)
        self.assertIsNone(self.call(second, "/session").json["account"])

    def test_delete_is_revision_checked_and_cannot_be_undone_by_stale_save(self):
        client = self.app.test_client()
        account = self.login(client, register=True)
        item_id = uuid4().hex
        self.save(client, account, item_id, "one")
        self.save(client, account, item_id, "two", 1)
        endpoint = "/drafts/" + item_id
        self.assertEqual(self.call(client, endpoint, "DELETE", {"version": 1}, account=account).status_code, 409)
        self.assertEqual(self.call(client, endpoint, "DELETE", {"version": 2}, account=account).status_code, 200)
        self.assertEqual(self.save(client, account, item_id, "stale", 2).status_code, 404)
        self.assertEqual(self.save(client, account, item_id, "retry creation", 0).status_code, 404)

    def test_remote_server_requires_https_and_does_not_expose_library(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/writing", base_url="http://writer.example").status_code, 403)
        self.assertEqual(client.get("/writing", base_url="https://other.example").status_code, 400)
        response = client.get("/writing", base_url=self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(client.get("/api/articles", base_url=self.url).status_code, 404)
        self.assertEqual(client.get("/api/data/state", base_url=self.url).status_code, 404)

    def test_registration_invite_and_same_origin_guard(self):
        client = self.app.test_client()
        credentials = {"register": True, "username": "reader.one", "password": "test passphrase"}
        self.assertTrue(self.call(client, "/session").json["invite_required"])
        response = self.call(client, "/session", "POST", credentials, headers={"Origin": "https://other.example"})
        self.assertEqual(response.json["code"], "invalid_origin")
        self.assertEqual(self.call(client, "/session", "POST", credentials).json["code"], "invalid_invite")
        self.app.config["WRITING_SIGNUP_CODE"] = ""
        self.assertFalse(self.call(client, "/session").json["registration"])
        self.assertEqual(self.call(client, "/session", "POST", credentials).json["code"], "registration_closed")

    def test_local_writing_storage_is_independent_of_library_restore(self):
        library = reader_app({"TESTING": True, "DATABASE": str(Path(self.directory.name) / "reader.sqlite3"),
                              "WRITING_DATABASE": self.database})
        client = library.test_client()
        with closing(sqlite3.connect(library.config["DATABASE"])) as connection, connection:
            connection.execute("UPDATE data_state SET restored=1")
        response = client.post(API + "/session", json={
            "register": True, "username": "local-writer", "password": "test passphrase",
        })
        self.assertEqual(response.status_code, 200)
        account = response.json["account"]
        row = client.get("/api/articles").json["articles"][0]
        self.assertIn(row["title"], client.get("/writing?article=" + str(row["id"])).text)
        self.assertEqual(client.post("/api/checkin", json={}).status_code, 409)
        remote = self.app.test_client()
        self.assertEqual(self.login(remote, "local-writer"), account)

    def test_invalid_unicode_and_bounds_leave_draft_unchanged(self):
        client = self.app.test_client()
        account = self.login(client, register=True)
        item_id = uuid4().hex
        self.assertEqual(self.save(client, account, item_id, "x" * 20001).status_code, 400)
        response = self.call(
            client, "/drafts/" + item_id, "PUT", account=account,
            data=json.dumps({"title": "Draft", "content": "\ud800", "version": 0}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.call(client, "/drafts", account=account).json["drafts"], [])


if __name__ == "__main__":
    unittest.main()
