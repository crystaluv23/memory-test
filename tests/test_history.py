import copy
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from memorylab.contracts import ContractError, canonical
from memorylab.history import (audit_candidate, audit_catalog, checkpoint, digest,
    project_identity, read_history, source_event, stage, verify_package)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.project=self.root/"project";self.project.mkdir()
        self.git("init","-q");self.git("config","user.name","fixture");self.git("config","user.email","fixture@example.invalid")
        (self.project/"src").mkdir();(self.project/"src/main.py").write_text("def run(): return 1\n")
        (self.project/"memory").mkdir();(self.project/"memory/lesson.md").write_text("future lesson")
        (self.project/"link.py").symlink_to("src/main.py")
        self.git("add",".");self.git("commit","-qm","fixture",env={"GIT_AUTHOR_DATE":"2026-01-01T00:00:00Z","GIT_COMMITTER_DATE":"2026-01-01T00:00:00Z"})
        self.commit=self.git("rev-parse","HEAD").strip();self.config=read_history()
        self.session=self.root/"session.jsonl"
        self.rows=[{"type":"session_meta","payload":{"id":"authentic-fixture","cwd":str(self.project)}},
            {"type":"response_item","timestamp":"2026-01-02T00:00:00Z","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Repair production checkpoint recovery with current constraints."}]}},
            {"type":"event_msg","timestamp":"2026-01-03T00:00:00Z","payload":{"type":"user_message","message":"Apply the workflow to the successor task under the new state."}}]
        self.write_session()
        self.candidate={"id":"chain1","split":"adapter","checkpoint":{"commit":self.commit,"files":["src/main.py"]},
            "learning":self.locator(2),"target":self.locator(3),"run_contract":None,
            "environment":{"python":"3.12","dependencies":[],"external_io":"fixed_replay","replay_manifest_sha256":"a"*64},
            "feedback":{"predicate":"none","production_check_sha256":None},
            "verifier":{"id":"history-verifier","sha256":"b"*64,"coverage":self.config["required_verifier_coverage"],"independent_contract_sha256":"c"*64}}
        self.catalog={"schema_version":1,"category":"infrastructure_fixture","project":str(self.project),"candidates":[self.candidate]}

    def tearDown(self):self.tmp.cleanup()

    def git(self,*args,env=None):
        return subprocess.run(["git","-C",str(self.project),*args],capture_output=True,text=True,
            env={**os.environ,**(env or {})},check=True).stdout

    def write_session(self):self.session.write_bytes(b"".join(canonical(r)+b"\n" for r in self.rows))

    def locator(self,line):
        data=self.session.read_bytes();raw=data.splitlines(keepends=True)[line-1];row=self.rows[line-1]
        text=row["payload"].get("message") or "\n".join(c["text"] for c in row["payload"]["content"])
        return {"path":str(self.session),"file_sha256":digest(data),"line":line,"line_sha256":digest(raw),"text_sha256":digest(text.encode()),"timestamp":row["timestamp"]}

    def code(self,expected,fn):
        with self.assertRaises(ContractError) as caught:fn()
        self.assertEqual(caught.exception.code,expected)

    def audit(self):return audit_candidate(self.candidate,self.project,self.config)[0]

    def test_authentic_inputs_and_checkpoint_are_staged_never_accepted(self):
        before=project_identity(self.project);row=self.audit()
        self.assertEqual(row["status"],"staged_requires_independent_replay")
        self.assertFalse(row["h2_accepted"]);self.assertEqual(row["live_model_calls"],0)
        self.assertEqual(row["checkpoint"]["files"],{"src/main.py":digest((self.project/"src/main.py").read_bytes())})
        self.assertEqual(project_identity(self.project),before)

    def test_real_category_cannot_label_fixture_as_recommandation(self):
        self.catalog["category"]="real_history_candidates"
        self.code("wrong_real_history_project",lambda:audit_catalog(self.catalog))

    def test_denominator_retains_missing_and_rejected_material(self):
        missing=copy.deepcopy(self.candidate);missing.update(id="missing",learning=None,target=None,environment=None,feedback=None,verifier=None)
        rejected=copy.deepcopy(self.candidate);rejected["id"]="bad";rejected["target"]["text_sha256"]="0"*64
        self.catalog["candidates"] += [missing,rejected]
        report=audit_catalog(self.catalog)
        self.assertEqual(report["planned"],3);self.assertEqual(report["h2_accepted"],0)
        self.assertEqual([r["status"] for r in report["rows"]],["staged_requires_independent_replay","incomplete","rejected"])
        self.assertIn("cannot_bind_checkpoint_cutoff",report["rows"][1]["blockers"])

    def test_file_line_text_and_timestamp_each_bind_source(self):
        for field in ("file_sha256","line_sha256","text_sha256","timestamp"):
            loc=self.locator(2);loc[field]="2026-01-04T00:00:00Z" if field=="timestamp" else "0"*64
            with self.subTest(field=field),self.assertRaises(ContractError):source_event(loc,self.project,self.config)

    def test_wrong_project_injected_context_and_model_output_are_not_tasks(self):
        original=copy.deepcopy(self.rows)
        for kind,code in (("project","history_wrong_project"),("context","history_injected_context_not_task"),("role","history_event_not_user_input")):
            self.rows=copy.deepcopy(original)
            if kind=="project":self.rows[0]["payload"]["cwd"]=str(self.root)
            elif kind=="context":self.rows[1]["payload"]["content"][0]["text"]="<environment_context>injected</environment_context>"
            else:self.rows[1]["payload"]["role"]="assistant"
            self.write_session();self.code(code,lambda:source_event(self.locator(2),self.project,self.config))

    def test_future_checkpoint_and_reversed_events_rejected(self):
        self.candidate["learning"]=self.locator(3);self.candidate["target"]=self.locator(2)
        self.code("history_event_order",self.audit)
        self.code("post_learning_checkpoint",lambda:checkpoint(self.project,self.candidate["checkpoint"],"2025-01-01T00:00:00Z",self.config))

    def test_checkpoint_reads_old_git_blob_not_modified_worktree(self):
        (self.project/"src/main.py").write_text("future repaired code and lesson")
        row,files,_=audit_candidate(self.candidate,self.project,self.config)
        self.assertEqual(files["src/main.py"],b"def run(): return 1\n")
        self.assertEqual(row["status"],"staged_requires_independent_replay")

    def test_unsafe_paths_links_and_missing_files_rejected(self):
        for path,code in (("../src/main.py","invalid_checkpoint_path"),("/src/main.py","invalid_checkpoint_path"),
            ("memory/lesson.md","forbidden_checkpoint_path"),(".env.production","forbidden_checkpoint_path"),
            ("link.py","nonregular_checkpoint_file"),("missing.py","checkpoint_path_missing")):
            self.candidate["checkpoint"]["files"]=[path];self.code(code,self.audit)

    def test_secrets_rejected_without_export(self):
        self.rows[1]["payload"]["content"][0]["text"]="api_key="+"x"*32
        self.write_session();self.candidate["learning"]=self.locator(2);self.candidate["target"]=self.locator(3)
        self.code("history_secret_material",self.audit)
        (self.project/"secret.txt").write_text("-----BEGIN PRIVATE KEY-----\nfixture")
        self.git("add","secret.txt");self.git("commit","-qm","secret fixture",env={"GIT_AUTHOR_DATE":"2026-01-01T01:00:00Z","GIT_COMMITTER_DATE":"2026-01-01T01:00:00Z"})
        value={"commit":self.git("rev-parse","HEAD").strip(),"files":["secret.txt"]}
        self.code("history_secret_material",lambda:checkpoint(self.project,value,"2026-01-02T00:00:00Z",self.config))

    def test_feedback_and_verifier_cannot_be_unbound(self):
        self.candidate["feedback"]={"predicate":"production_check_failed","production_check_sha256":None}
        self.code("unbound_history_feedback",self.audit)
        self.candidate["feedback"]={"predicate":"none","production_check_sha256":None}
        self.candidate["verifier"]["coverage"]=["production_entry"]
        self.code("incomplete_history_verifier",self.audit)

    def test_original_contract_revision_and_timestamp_bound(self):
        path=self.root/"contract.json"
        for revision,created,code in (("0"*40,"2026-01-01T12:00:00Z","history_contract_revision_mismatch"),
            (self.commit,"2026-01-04T00:00:00Z","post_learning_run_contract")):
            path.write_bytes(canonical({"contract":{"code_revision":revision,"created_at":created}}))
            self.candidate["run_contract"]={"path":str(path),"sha256":digest(path.read_bytes())};self.code(code,self.audit)

    def test_source_and_event_size_caps(self):
        config=copy.deepcopy(self.config);config["max_source_bytes"]=10
        self.code("history_source_too_large",lambda:source_event(self.locator(2),self.project,config))
        config=copy.deepcopy(self.config);config["max_event_bytes"]=10
        self.code("invalid_history_event_size",lambda:source_event(self.locator(2),self.project,config))

    def test_package_exact_inputs_cache_and_tamper(self):
        first=stage(self.catalog,"chain1",self.root/"bundles")
        self.assertFalse(first["cache_hit"]);self.assertTrue(stage(self.catalog,"chain1",self.root/"bundles")["cache_hit"])
        package=Path(first["path"]);payload=verify_package(package)
        self.assertFalse(payload["h2_accepted"]);self.assertTrue(payload["requires_independent_content_review"])
        self.assertEqual((package/"solver/checkpoint/src/main.py").read_bytes(),b"def run(): return 1\n")
        self.assertFalse((package/"solver/checkpoint/memory").exists())
        (package/"tasks/target.txt").write_text("tampered answer");self.code("history_package_drift",lambda:verify_package(package))

    def test_incomplete_candidate_and_source_output_not_published(self):
        self.candidate["environment"]=None
        self.code("history_candidate_incomplete",lambda:stage(self.catalog,"chain1",self.root/"out"))
        self.assertFalse((self.root/"out").exists())
        self.code("history_output_in_source",lambda:stage(self.catalog,"chain1",self.project/"out"))

    def test_source_and_package_symlink_fail_closed(self):
        link=self.root/"symlink.jsonl";link.symlink_to(self.session)
        loc=self.locator(2);loc["path"]=str(link)
        self.code("invalid_history_source_file",lambda:source_event(loc,self.project,self.config))
        first=stage(self.catalog,"chain1",self.root/"bundles")
        (Path(first["path"])/"escape").symlink_to(self.project)
        self.code("history_package_symlink",lambda:verify_package(first["path"]))


if __name__=="__main__":unittest.main()
