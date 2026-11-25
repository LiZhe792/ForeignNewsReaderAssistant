"""Regressions for account boundaries, shared reading, offline merging and imports."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from newsreader.library import content_fingerprint

CONTENT = "A careful reader follows the evidence and asks thoughtful questions about the world. " * 8


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({"TESTING": True, "DATABASE": str(Path(self.temp.name)/"test.sqlite3")})
        self.guest = self.app.test_client()

    def user(self, name):
        client = self.app.test_client()
        response = client.post("/api/writing-workspace/session", json={"username": name, "password": "test-password-123", "register": True})
        self.assertEqual(response.status_code, 200, response.json)
        return client, response.json["account"]["id"]

    def write(self, user, path, data, method="POST"):
        client, identity = user
        return client.open("/api"+path, method=method, json=data, headers={"X-Margin-Account": identity})

    def article(self, user, title="Private reading"):
        response = self.write(user, "/articles", {"title": title, "content": CONTENT})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json["id"]

    def test_account_learning_data_is_isolated_and_old_tabs_cannot_write(self):
        alice, bob = self.user("alice"), self.user("bob")
        identity = self.article(alice)
        self.assertEqual(bob[0].get("/api/articles/"+str(identity)).status_code, 404)
        self.assertEqual(self.guest.get("/api/articles/"+str(identity)).status_code, 404)
        response = alice[0].post("/api/articles", json={"title":"wrong account","content":CONTENT},
                                 headers={"X-Margin-Account": bob[1]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.write(alice, "/vocabulary", {"word":"evidence","meaning":"证据"}).status_code, 201)
        self.assertEqual(len(bob[0].get("/api/vocabulary").json["words"]), 0)

    def test_account_backups_use_the_current_database(self):
        alice, bob = self.user("alice"), self.user("bob")
        self.article(alice)
        response = self.write(alice, "/data/backups", {})
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.json["backup"]["summary"]["articles"], 6)
        self.assertEqual(bob[0].get("/api/data/state").json["summary"]["articles"], 5)

    def test_same_title_different_body_is_not_lost_and_tracking_urls_deduplicate(self):
        a = self.guest.post("/api/articles", json={"title":"Same","content":CONTENT})
        b = self.guest.post("/api/articles", json={"title":"Same","content":CONTENT+" Another sentence."})
        self.assertEqual(a.status_code, 201);self.assertEqual(b.status_code, 201)
        self.assertNotEqual(a.json["id"], b.json["id"])
        article = {"title":"URL","content":CONTENT,"source":"Test","source_url":"https://example.org/news?utm_source=a&id=5"}
        with patch("app.import_url", return_value=article):
            first=self.guest.post("/api/articles",json={"url":article["source_url"]})
        article["source_url"]="https://EXAMPLE.org/news?id=5#section"
        with patch("app.import_url", return_value=article):
            second=self.guest.post("/api/articles",json={"url":article["source_url"]})
        self.assertEqual(first.json["id"],second.json["id"])

    def test_trashed_articles_are_excluded_across_search_and_recommendation(self):
        self.guest.post("/api/articles/batch",json={"ids":[1],"op":"trash"})
        self.assertNotIn(1,[a["id"] for a in self.guest.get("/api/articles/search").json["articles"]])
        self.assertNotIn(1,[a["id"] for a in self.guest.get("/api/personalization/recommendations").json["articles"]])
        with patch("newsreader.search.embed",side_effect=lambda texts,query=False:[[1,0] for _ in texts]):
            self.assertNotIn(1,[a["id"] for a in self.guest.get("/api/articles/2/similar").json["articles"]])

    def test_extraction_retains_heading_list_and_quote_without_duplicates(self):
        from newsreader.content import extract_article
        content=extract_article("<article><h2>Short</h2><p>"+CONTENT+"</p><ul><li>A distinct item</li></ul><blockquote><p>A distinct quote.</p></blockquote></article>","https://example.org")["content"]
        self.assertIn("## Short",content);self.assertIn("- A distinct item",content)
        self.assertEqual(content.count("A distinct quote."),1)

    def test_collections_conflict_and_account_boundaries(self):
        alice,bob=self.user("alice"),self.user("bob")
        created=self.write(alice,"/platform/collections",{"title":"Climate","article_ids":[1,2]}).json["collection"]
        self.assertEqual(bob[0].get("/api/platform/collections").json["collections"],[])
        data={"id":created["id"],"version":created["version"],"title":"New","article_ids":[1]}
        self.assertEqual(self.write(alice,"/platform/collections",data).status_code,200)
        self.assertEqual(self.write(alice,"/platform/collections",data).status_code,409)

    def test_offline_operations_are_idempotent_and_position_conflicts_are_explicit(self):
        alice=self.user("alice");identity=self.article(alice)
        article=alice[0].get("/api/platform/offline-bundle/"+str(identity)).json
        operation={"id":"offline-operation-123","kind":"word","article_id":identity,"content_hash":article["fingerprint"],"word":"evidence","meaning":"证据","context":""}
        for _ in range(2):
            r=self.write(alice,"/platform/sync",{"operations":[operation]});self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(len(alice[0].get("/api/vocabulary").json["words"]),1)
        progress={"id":"offline-progress-123","kind":"progress","article_id":identity,"content_hash":article["fingerprint"],"position":40,"progress":40,"base_read_at":"stale"}
        self.assertEqual(self.write(alice,"/platform/sync",{"operations":[progress]}).json["results"][0]["status"],"conflict")
        progress["resolution"]="local"
        self.assertEqual(self.write(alice,"/platform/sync",{"operations":[progress]}).json["results"][0]["status"],"saved")
        self.assertEqual(alice[0].get("/api/articles/"+str(identity)).json["position"],40)

    def test_group_teacher_permissions_and_private_student_data(self):
        teacher,student,outsider=self.user("teacher"),self.user("student"),self.user("outsider")
        group=self.write(teacher,"/groups",{"title":"Reading group"}).json["group"]
        self.assertEqual(self.write(student,"/groups/join",{"invite":group["invite"]}).status_code,200)
        self.assertEqual(outsider[0].get("/api/groups/"+group["id"]).status_code,404)
        payload={"kind":"assignment","title":"Read and summarize","article_ids":[1],"instructions":"Summarize the main argument."}
        self.assertEqual(self.write(student,"/groups/"+group["id"]+"/readings",payload).status_code,403)
        assignment=self.write(teacher,"/groups/"+group["id"]+"/readings",payload)
        self.assertEqual(assignment.status_code,201,assignment.json)
        aid=assignment.json["id"]
        self.assertEqual(self.write(student,"/groups/readings/"+aid+"/open",{}).status_code,200)
        submission=student[0].get("/api/groups/readings/"+aid+"/submission").json["submission"]
        self.assertEqual(self.write(student,"/groups/readings/"+aid+"/submission",{"answer":"My summary","version":submission["version"]}).status_code,200)
        self.assertEqual(student[0].get("/api/groups/readings/"+aid+"/progress").status_code,403)
        progress=teacher[0].get("/api/groups/readings/"+aid+"/progress")
        self.assertEqual(progress.status_code,200)
        self.assertEqual(next(s for s in progress.json["students"] if s["id"]==student[1])["answer"],"My summary")

    def test_deepseek_configuration_is_reused_by_context_service(self):
        from newsreader import ai
        with self.app.app_context(),patch("newsreader.ai_client.credentials",return_value=("fake-test-key","deepseek-flash","local")):
            config=ai.configuration()
            self.assertEqual(config["transport"],"deepseek")
            with patch("newsreader.ai_client.request_json",return_value={"choices":[{"finish_reason":"stop","message":{"content":'{"answer":"valid"}'}}]}) as transport:
                self.assertEqual(ai.request_json({"input":"{}","instructions":"Test","text":{"format":{"schema":{}}}},config),{"answer":"valid"})
                self.assertEqual(transport.call_count,1)

    def test_shared_service_blocks_anonymous_article_in_writing_page(self):
        self.app.config['LEARNING_SHARED']=True
        self.assertEqual(self.guest.get('/api/articles/1').status_code,401)
        response=self.guest.get('/writing?article=1')
        self.assertEqual(response.status_code,302)
        self.assertTrue(response.location.endswith('/hub#accounts'))

    def test_editor_revocation_invite_rotation_and_shared_edit_conflict(self):
        owner,editor,newcomer=self.user('owner'),self.user('editor'),self.user('newcomer')
        group=self.write(owner,'/groups',{'title':'Editors'}).json['group'];gid=group['id']
        self.write(editor,'/groups/join',{'invite':group['invite']})
        self.write(owner,'/groups/'+gid+'/members',{'account_id':editor[1],'role':'editor'},'PATCH')
        created=self.write(editor,'/groups/'+gid+'/readings',{'title':'Collection','article_ids':[1]})
        self.assertEqual(created.status_code,201,created.json);rid=created.json['id']
        path='/groups/readings/'+rid
        update={'title':'Revised','version':1}
        self.assertEqual(self.write(editor,path,update,'PATCH').status_code,200)
        self.assertEqual(self.write(owner,path,update,'PATCH').status_code,409)
        self.write(owner,'/groups/'+gid+'/members',{'account_id':editor[1],'role':'reader'},'PATCH')
        self.assertEqual(self.write(editor,path,{'title':'No','version':2},'PATCH').status_code,403)
        self.write(owner,'/groups/'+gid+'/rotate-invite',{})
        self.assertEqual(self.write(newcomer,'/groups/join',{'invite':group['invite']}).status_code,400)
        self.write(owner,'/groups/'+gid+'/members',{'account_id':editor[1],'role':'remove'},'PATCH')
        self.assertEqual(editor[0].get('/api'+path+'/submission').status_code,404)

    def test_assessment_does_not_expose_answers_and_submission_is_repeatable(self):
        created=self.guest.post("/api/platform/assessment",json={})
        self.assertEqual(created.status_code,201,created.json)
        self.assertGreaterEqual(len(created.json["questions"]),5)
        self.assertNotIn("answer",created.json["questions"][0])
        answers={q["id"]:"0" for q in created.json["questions"]}
        path="/api/platform/assessment/"+created.json["id"]
        first=self.guest.post(path,json={"answers":answers})
        second=self.guest.post(path,json={"answers":answers})
        self.assertEqual(first.status_code,200,first.json);self.assertEqual(first.json,second.json)
