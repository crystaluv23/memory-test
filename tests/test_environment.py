import copy
import json
import os
import py_compile
import socket
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memorylab.common import digest, encode, load, save, tree_hash, exclusive_run
from memorylab.grade import equal_json, grade
from memorylab.memory import CLIENT, DisclosureServer, ReferenceMemory
from memorylab.runner import ROOT, codex_command, execute, parse_events, prepare, verify_manifest, invoke
from memorylab.report import summarize
from memorylab.__main__ import close_interrupted
from memorylab.sandbox import command, probe
from memorylab.tasks import FAMILIES, cases, materialize, oracle

# Independently authored reference candidates; never copied to live workspaces.
GOOD_RECOVERY = '''import hashlib, json
def process(c):
    pointer=c['state'][c['marker_key']]
    fail={'resume_from':pointer,'action':'blocked','outcome':'integrity_failure','benefit':False}
    if pointer is None:
        return dict(resume_from=None,action='start',outcome='not_started',benefit=False)
    if type(pointer)!=int or pointer<0:
        fail['resume_from']=None
        return fail
    found=[]
    for r in c['receipts']:
        h=hashlib.sha256(json.dumps(r['payload'],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        if r['id']==pointer and r['committed'] is True and r['hash']==h: found.append(r)
    if len(found)!=1: return fail
    why=c['interruption']; p=found[0]['payload']
    benefit=all(p.get(k) is True for k in ('promoted','used','independent_gain_confirmed'))
    if c['contract']=='strict': benefit=benefit and p.get('cohort_fixed') is True
    action='pause' if why in ('paused','provider_failure') else 'fallback' if why=='unsupported' else 'resume'
    outcome='technical_failure' if why=='provider_failure' else 'capability_gap' if why=='unsupported' else 'engineering_complete'
    return dict(resume_from=pointer,action=action,outcome=outcome,benefit=benefit)
'''
GOOD_REPAIR = '''import copy,hashlib,json,re
def process(c):
    original=c['state']; out=copy.deepcopy(original)
    reject={'accepted':False,'state':original}
    if hashlib.sha256(json.dumps(original,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()!=c['snapshot']:return reject
    visited=set()
    try:
        for e in c['edits']:
            op,path=e['op'],e['path']
            if path in visited or not path.startswith('/') or op not in ('add','replace'):return reject
            visited.add(path)
            if {'op':op,'path':path} not in c['grants']:return reject
            raw=path[1:].split('/')
            if any(re.search(r'~(?![01])',x) for x in raw):return reject
            parts=[x.replace('~1','/').replace('~0','~') for x in raw]
            parent=out
            for key in parts[:-1]:
                if isinstance(parent,list):
                    if not key.isdigit() or str(int(key))!=key:return reject
                    parent=parent[int(key)]
                else:parent=parent[key]
            key=parts[-1]
            if isinstance(parent,dict):
                if op=='add' and key in parent:return reject
                if op=='replace' and key not in parent:return reject
                parent[key]=e['value']
            elif isinstance(parent,list) and op=='replace':
                if not key.isdigit() or str(int(key))!=key:return reject
                parent[int(key)]=e['value']
            else:return reject
    except (KeyError,IndexError,TypeError,ValueError):return reject
    return {'accepted':True,'state':out}
'''
GOOD_EVIDENCE = '''import math
def process(c):
    pool=[]; absent=dict(status='unavailable',value=None,source=None)
    for f in c['facts']:
        if f['metric']!=c['metric'] or f['period']!=c['period'] or f['scope']!='total':continue
        if f['published_at']>c['cutoff'] or type(f['value']) not in (int,float) or not math.isfinite(f['value']):continue
        if c['contract']=='observed' and (not f.get('observed_at') or f['observed_at']>c['cutoff']):continue
        pool.append(f)
    if not pool:return absent
    pool.sort(key=lambda f:(f['published_at'],f['id']))
    newest=pool[-1]['published_at']; top=[f for f in pool if f['published_at']==newest]
    if any(f['value']!=top[0]['value'] for f in top):return absent
    f=min(top,key=lambda f:f['id'])
    return dict(status='available',value=f['value'],source=f['id'])
'''
GOOD = dict(recovery=GOOD_RECOVERY, repair=GOOD_REPAIR, evidence=GOOD_EVIDENCE)
FINAL = dict(summary="Validated synthetic entry", completion_claim="complete", memory_uses=[])


def note(memory, identifier="", version=0, category="workflow/state", level=0, supports=None):
    handle = memory.register_source("observed:1", "Actual fixture feedback: staged files did not prove commitment.")
    return dict(id=identifier, expected_version=version, category=category, level=level,
                brief="Verify committed state before recovering.", content="In the observed fixture the staged file was newer than the committed receipt. Check the authoritative marker and verified receipt.",
                conditions="Recovery using a committed marker.", limits="Does not imply research gain or authorize extra writes.",
                source_refs=[handle], support_ids=supports or [], links=[], supersedes=[])


class TaskGradingTests(unittest.TestCase):
    def setUp(self):
        self.w = tempfile.TemporaryDirectory(prefix="memorylab-worker-")
        self.c = tempfile.TemporaryDirectory(prefix="memorylab-control-")
        self.ws, self.control = Path(self.w.name), Path(self.c.name)

    def tearDown(self):
        self.w.cleanup(); self.c.cleanup()

    def test_original_defects_fail_every_family_and_episode(self):
        for family in FAMILIES:
            for episode in range(5):
                with self.subTest(family=family, episode=episode):
                    materialize(self.ws, family, 19, episode)
                    result = grade(self.ws, self.control, family, 19, episode, {}, FINAL)
                    self.assertFalse(result["full_success"])
                    self.assertIsNone(result["entry_error"])
                    self.assertTrue(result["false_completion"])

    def test_independent_correct_candidates_pass_real_entry(self):
        for family in FAMILIES:
            for episode in range(5):
                with self.subTest(family=family, episode=episode):
                    materialize(self.ws, family, 432, episode)
                    protected = {k: v for k, v in tree_hash(self.ws).items() if k != "engine.py"}
                    (self.ws / "engine.py").write_text(GOOD[family])
                    result = grade(self.ws, self.control, family, 432, episode, protected, FINAL)
                    self.assertTrue(result["full_success"], result)

    def test_same_solution_does_not_pass_rule_change_mutation(self):
        materialize(self.ws, "evidence", 200, 4)
        (self.ws / "engine.py").write_text(GOOD_EVIDENCE.replace("c['contract']=='observed'", "False"))
        result = grade(self.ws, self.control, "evidence", 200, 4, {}, FINAL)
        self.assertFalse(result["full_success"])
        self.assertLess(result["groups"]["observed_drift"]["passed"], result["groups"]["observed_drift"]["total"])

    def test_add_only_overgeneralization_rejected(self):
        materialize(self.ws, "repair", 200, 4)
        (self.ws / "engine.py").write_text(GOOD_REPAIR.replace("('add','replace')", "('add',)"))
        result = grade(self.ws, self.control, "repair", 200, 4, {}, FINAL)
        self.assertFalse(result["full_success"])
        self.assertEqual(result["groups"]["authorized_replace"]["passed"], 0)

    def test_correct_output_cannot_hide_protected_file_edit(self):
        materialize(self.ws, "recovery", 200, 0)
        protected = {"run.py": tree_hash(self.ws)["run.py"]}
        (self.ws / "engine.py").write_text(GOOD_RECOVERY)
        with (self.ws / "run.py").open("a") as stream:
            stream.write("\n# unapproved change\n")
        result = grade(self.ws, self.control, "recovery", 200, 0, protected, FINAL)
        self.assertFalse(result["full_success"])
        self.assertEqual(result["protected_violations"], ["run.py"])

    def test_worker_cannot_read_control_or_home_memory(self):
        materialize(self.ws, "recovery", 200, 0)
        secret = self.control / "answer.json"; secret.write_text("private")
        script = "from pathlib import Path; Path("+repr(str(secret))+").read_text()"
        process = subprocess.run(command(self.ws, self.control, [sys.executable, "-c", script], True, False), capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("Operation not permitted", process.stderr)

    def test_offline_isolation_probe(self):
        materialize(self.ws, "recovery", 3, 0)
        self.assertTrue(all(probe(self.ws, self.control).values()))

    def test_generator_deterministic_with_seed_variation(self):
        self.assertEqual(cases("repair", 3, 2), cases("repair", 3, 2))
        self.assertNotEqual(cases("repair", 3, 2), cases("repair", 4, 2))

    def test_bool_is_not_numeric_success(self):
        self.assertFalse(equal_json({"value": True}, {"value": 1}))
        self.assertTrue(equal_json({"value": 1.0}, {"value": 1}))

    def test_grader_never_uses_stale_verified_bytecode(self):
        materialize(self.ws,"recovery",200,4)
        engine=self.ws/"engine.py"; engine.write_text(GOOD_RECOVERY)
        original_time=engine.stat().st_mtime
        cache=self.ws/"__pycache__"; cache.mkdir(exist_ok=True)
        py_compile.compile(str(engine),cfile=str(cache/("engine."+sys.implementation.cache_tag+".pyc")),doraise=True)
        subprocess.run([sys.executable,"-I","-c","import os,sys,runpy;sys.path.insert(0,os.getcwd());runpy.run_path('run.py',run_name='__main__')"],cwd=self.ws,input=encode([cases("recovery",200,4)[0]["input"]]),text=True,capture_output=True,check=True)
        self.assertTrue((self.ws/"__pycache__").is_dir())
        changed=GOOD_RECOVERY.replace("c['contract']=='strict'","c['contract']=='legacy'")
        self.assertEqual(len(changed),len(GOOD_RECOVERY))
        engine.write_text(changed); os.utime(engine,(original_time,original_time))
        self.assertFalse(grade(self.ws,self.control,"recovery",200,4,{},FINAL)["full_success"])

    def test_unauthorized_mixed_edit_is_atomic(self):
        original = {"keep": 9}
        c = dict(state=original, snapshot=digest(original), edits=[dict(op="add", path="/ok", value=3), dict(op="add", path="/bad", value=4)], grants=[dict(op="add", path="/ok")])
        self.assertEqual(oracle("repair", c), {"accepted": False, "state": original})


class MemoryTests(unittest.TestCase):
    def test_roundtrip_sources_and_versions(self):
        memory = ReferenceMemory()
        raw = note(memory)
        self.assertEqual(memory.commit({"notes": [raw]}, "episode1")["accepted"], 1)
        restored = ReferenceMemory(memory.snapshot())
        revised = note(restored, "episode1:n1", 1); revised["brief"] = "Revised scoped lesson."
        self.assertEqual(restored.commit({"notes": [revised]}, "episode2")["accepted"], 1)
        self.assertEqual(len(restored.records), 2)
        self.assertEqual(restored.active()[0]["version"], 2)

    def test_invalid_batch_is_atomic(self):
        memory = ReferenceMemory(); good = note(memory)
        bad = copy.deepcopy(good); bad["source_refs"][0]["sha256"] = "wrong"
        self.assertEqual(memory.commit({"notes": [good, bad]}, "episode1")["accepted"], 0)
        self.assertEqual(memory.records, [])

    def test_stale_revision_rejected(self):
        memory = ReferenceMemory(); memory.commit({"notes": [note(memory)]}, "e1")
        self.assertEqual(memory.commit({"notes": [note(memory, "e1:n1", 0)]}, "e2")["accepted"], 0)

    def test_unresolved_and_self_support_rejected(self):
        for reference in ("absent", "e1:n1"):
            memory = ReferenceMemory(); n = note(memory, level=1, supports=[reference])
            self.assertEqual(memory.commit({"notes": [n]}, "e1")["accepted"], 0)

    def test_ancestor_abstraction_and_archived_support(self):
        memory = ReferenceMemory()
        memory.commit({"notes": [note(memory), note(memory, category="workflow/authority")]}, "e1")
        higher = note(memory, category="workflow", level=1, supports=["e1:n1", "e1:n2"])
        self.assertEqual(memory.commit({"notes": [higher]}, "e2")["accepted"], 1)
        revision = note(memory, "e1:n1", 1, category="other/category")
        self.assertEqual(memory.commit({"notes": [revision]}, "e3")["accepted"], 1)
        abstraction = next(n for n in memory.active() if n["id"] == "e2:n1")
        self.assertEqual(abstraction["support_versions"]["e1:n1"], 1)

    def test_conflicting_child_category_rejected(self):
        memory = ReferenceMemory()
        memory.commit({"notes": [note(memory), note(memory, category="data/visibility")]}, "e1")
        higher = note(memory, category="workflow", level=1, supports=["e1:n1", "e1:n2"])
        self.assertEqual(memory.commit({"notes": [higher]}, "e2")["accepted"], 0)

    def test_source_identity_immutable(self):
        memory = ReferenceMemory(); memory.register_source("id", "first")
        with self.assertRaises(ValueError):
            memory.register_source("id", "different")

    def test_budget_does_not_clip_briefs(self):
        memory = ReferenceMemory(); memory.commit({"notes": [note(memory)]}, "e1")
        selected, overflow = memory.select_briefs(5)
        self.assertEqual(selected, []); self.assertEqual(overflow, ["e1:n1"])

    def test_supersession_preserves_archive(self):
        memory = ReferenceMemory(); memory.commit({"notes": [note(memory)]}, "e1")
        new = note(memory); new["supersedes"] = ["e1:n1"]
        memory.commit({"notes": [new]}, "e2")
        self.assertEqual([r["id"] for r in memory.active()], ["e2:n1"])
        self.assertEqual(len(memory.records), 2)

    def test_socket_budget_and_logs_are_controller_owned(self):
        with tempfile.TemporaryDirectory(prefix="memorylab-worker-") as w, tempfile.TemporaryDirectory(prefix="memorylab-control-") as c:
            workspace = Path(w); log = Path(c) / "reads.json"
            memory = ReferenceMemory(); memory.commit({"notes": [note(memory)]}, "e1")
            with DisclosureServer(workspace, memory, 500, log):
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.connect(str(workspace / ".memory.sock")); sock.sendall(b'{"op":"read","id":"e1:n1"}\n')
                    response = json.loads(sock.recv(8192))
                self.assertIn("error", response)
            events = load(log)
            self.assertFalse(events[0]["success"])
            self.assertLessEqual(sum(e["bytes"] for e in events), 500)

    def test_socket_client_works_inside_sandbox(self):
        with tempfile.TemporaryDirectory(prefix="memorylab-worker-") as w, tempfile.TemporaryDirectory(prefix="memorylab-control-") as c:
            workspace = Path(w); (workspace / "memory_tool.py").write_text(CLIENT)
            memory = ReferenceMemory(); memory.commit({"notes": [note(memory)]}, "e1")
            with DisclosureServer(workspace, memory, 10000, Path(c)/"reads.json"):
                proc = subprocess.run(command(workspace, c, [sys.executable, "memory_tool.py", "read", "--id", "e1:n1"], True, False), cwd=workspace, capture_output=True, text=True, timeout=5)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(json.loads(proc.stdout)["record"]["id"], "e1:n1")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memorylab-control-")
        self.root = Path(self.tmp.name)
        self.config = load(ROOT / "configs" / "environment.json")
        self.config.update(model="test-only-not-a-real-model", episodes=2, families=["recovery"])
        self.config_path = self.root / "config.json"
        save(self.config_path, self.config)
        self.run = self.root / "run"

    def tearDown(self):
        self.tmp.cleanup()

    def test_prepare_never_calls_model_and_counts_calls(self):
        with patch("subprocess.Popen", side_effect=AssertionError("model must not run")):
            result = prepare(self.config_path, self.run)
            self.assertEqual(result["solve_calls"], 2)
            self.assertEqual(result["reflection_calls_max"], 0)
            self.assertEqual(load(self.run / "state.json")["status"], "prepared")

    def test_changed_manifest_rejected(self):
        prepare(self.config_path, self.run)
        manifest = load(self.run / "manifest.json"); manifest["config"]["seed"] += 1
        save(self.run / "manifest.json", manifest)
        with self.assertRaises(ValueError):verify_manifest(self.run)

    def test_changed_implementation_rejected(self):
        prepare(self.config_path, self.run)
        with patch("memorylab.runner.source_hashes", return_value={"different": "hash"}):
            with self.assertRaises(ValueError):verify_manifest(self.run)

    def test_refuses_to_overwrite_prior_plan(self):
        prepare(self.config_path, self.run)
        with self.assertRaises(ValueError):prepare(self.config_path, self.run)

    def test_missing_and_interrupted_stay_in_denominator(self):
        prepare(self.config_path, self.run)
        report = summarize(self.run)
        self.assertEqual(report["arms"]["baseline"]["missing"], 2)
        self.assertIsNone(report["arms"]["baseline"]["finished_success_fraction"])
        state = load(self.run / "state.json")
        tid = next(iter(state["trials"])); state["trials"][tid] = "running"; save(self.run/"state.json", state)
        with self.assertRaises(ValueError):execute(self.run)
        close_interrupted(self.run, tid, "test interruption")
        report = summarize(self.run)
        self.assertEqual(report["arms"]["baseline"]["planned"], 2)
        self.assertEqual(report["arms"]["baseline"]["finished"], 1)
        self.assertEqual(report["arms"]["baseline"]["full_successes"], 0)
        self.assertIsNone(report["arms"]["baseline"]["total_tokens"])

    def test_report_preserves_manual_review(self):
        prepare(self.config_path, self.run); summarize(self.run)
        review = load(self.run / "blind_review.json"); review[0]["notes"] = "Human review"
        save(self.run / "blind_review.json", review); summarize(self.run)
        self.assertEqual(load(self.run / "blind_review.json")[0]["notes"], "Human review")
        state=load(self.run/"state.json"); tid=next(iter(state["trials"])); state["trials"][tid]="completed"
        save(self.run/"state.json",state)
        save(self.run/"trials"/tid/"grade.json",{"full_success":True,"groups":{}})
        summarize(self.run)
        self.assertTrue(load(self.run/"blind_review.json")[0]["full_success"])
        self.assertEqual(load(self.run/"blind_review.json")[0]["notes"],"Human review")

    def test_cli_configuration_disables_shared_context(self):
        with tempfile.TemporaryDirectory(prefix="memorylab-worker-") as w:
            args = codex_command(Path(w), self.root, self.config, "solve")
            self.assertIn("--ephemeral", args)
            self.assertIn("--ignore-user-config", args)
            self.assertIn("--ignore-rules", args)
            self.assertIn("memories", args)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)

    def test_usage_does_not_double_count_cached_tokens(self):
        events = [{"type":"turn.completed", "usage":{"input_tokens":100,"cached_input_tokens":80,"output_tokens":20}}]
        file = self.root / "events.jsonl"; file.write_text("\n".join(encode(x) for x in events))
        self.assertEqual(parse_events(file)["total_tokens"], 120)

    def test_failed_events_do_not_get_invented_usage(self):
        file = self.root / "events.jsonl"; file.write_text('{"type":"turn.failed"}\nnot-json\n')
        result = parse_events(file)
        self.assertIsNone(result["usage"]); self.assertTrue(result["turn_failed"])
        self.assertEqual(result["malformed_events"], 1)

    def test_non_object_events_fail_closed(self):
        file=self.root/"events.jsonl"; file.write_text('[]\nnull\n')
        self.assertEqual(parse_events(file)["malformed_events"],2)

    def test_single_executor_lock(self):
        prepare(self.config_path,self.run)
        with exclusive_run(self.run):
            with self.assertRaises(RuntimeError):execute(self.run)

    def test_interruption_restores_pretrial_memory(self):
        prepare(self.config_path,self.run)
        state=load(self.run/"state.json"); tid=next(iter(state["trials"])); state["trials"][tid]="running"
        save(self.run/"state.json",state)
        before={"records":[],"sources":{}}
        save(self.run/"trials"/tid/"memory_before.json",before)
        memory_file=self.run/"memory"/"r0-recovery-baseline.json"
        save(memory_file,{"records":["uncommitted"],"sources":{}})
        close_interrupted(self.run,tid,"process exited and audited")
        self.assertEqual(load(memory_file),before)

    def test_invocation_captures_events_and_actual_token_usage(self):
        with tempfile.TemporaryDirectory(prefix="memorylab-worker-") as w:
            ws=Path(w); script=ws/"fake_cli.py"
            script.write_text("import sys,json,pathlib\nsys.stdin.read()\n"+
                "pathlib.Path('solve.final.json').write_text("+repr(encode(FINAL))+")\n"+
                "print("+repr(encode({"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":50,"output_tokens":20}}))+")\n")
            with patch("memorylab.runner.codex_command",return_value=[sys.executable,str(script)]):
                metrics,final=invoke(ws,self.root,self.config,"Synthetic test input",self.root/"logs","solve")
            self.assertEqual(metrics["status"],"ok"); self.assertEqual(metrics["total_tokens"],120)
            self.assertEqual(final,FINAL)

    def test_invocation_command_cap_retains_failure(self):
        with tempfile.TemporaryDirectory(prefix="memorylab-worker-") as w:
            ws=Path(w); script=ws/"fake_cli.py"
            commands=[{"type":"item.started","item":{"id":str(i),"type":"command_execution","command":"noop"}} for i in range(3)]
            script.write_text("import sys,time\nsys.stdin.read()\n"+
                "print("+repr("\n".join(encode(e) for e in commands))+",flush=True)\ntime.sleep(10)\n")
            config=dict(self.config,command_limit=1)
            with patch("memorylab.runner.codex_command",return_value=[sys.executable,str(script)]):
                metrics,final=invoke(ws,self.root,config,"Synthetic test input",self.root/"logs","solve")
            self.assertEqual(metrics["status"],"failed"); self.assertEqual(metrics["stop_reason"],"command_limit")
            self.assertIsNone(metrics["total_tokens"])

    def test_invocation_timeout_retains_failure(self):
        with tempfile.TemporaryDirectory(prefix="memorylab-worker-") as w:
            ws=Path(w); script=ws/"fake_cli.py"
            script.write_text("import sys,time\nsys.stdin.read()\ntime.sleep(10)\n")
            config=dict(self.config,timeout_seconds=1)
            with patch("memorylab.runner.codex_command",return_value=[sys.executable,str(script)]):
                metrics,final=invoke(ws,self.root,config,"Synthetic test input",self.root/"logs","solve")
            self.assertEqual(metrics["status"],"failed"); self.assertEqual(metrics["stop_reason"],"timeout")

    def test_scripted_full_lifecycle_uses_actual_independent_grader(self):
        # Emulates only the model turn; real materialization, sandbox, grading, commit, resume and report.
        self.config["arms"] = [{"id":"reference_plumbing", "adapter":"reference", "reflect":True}]
        save(self.config_path, self.config); prepare(self.config_path, self.run)
        generated_workspaces = []
        def scripted_invoke(workspace, control, config, prompt, directory, phase):
            generated_workspaces.append(workspace)
            directory.mkdir(parents=True, exist_ok=True)
            (directory/"prompt.txt").write_text(prompt)
            (directory/"events.jsonl").write_text('{"type":"turn.completed"}\n')
            metrics = dict(status="ok", wall_seconds=.01, command_count=1,repeated_commands=0, usage=None)
            if phase == "solve":
                (workspace/"engine.py").write_text(GOOD_RECOVERY)
                final = FINAL
            else:
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.connect(str(workspace/".memory.sock"))
                    marker = "Sources: "
                    handles = json.loads(prompt[prompt.index(marker)+len(marker):])
                    sock.sendall((encode({"op":"source","id":handles[0]["id"]})+"\n").encode())
                    sock.recv(8192)
                n = note(ReferenceMemory()); n["source_refs"] = [handles[0]]
                final = {"notes":[n]}
            save(directory/"metrics.json", metrics); save(directory/"final.json", final)
            return metrics, final
        try:
            with patch("memorylab.runner.invoke", side_effect=scripted_invoke):
                first=execute(self.run,limit=1)
                self.assertEqual(first["status"],"partial")
                second=execute(self.run)
                self.assertEqual(second["status"],"completed")
                no_more=execute(self.run)
                self.assertEqual(no_more["tasks_executed_this_call"],0)
            report=summarize(self.run)
            self.assertEqual(report["arms"]["reference_plumbing"]["full_successes"],2)
            self.assertEqual(report["arms"]["reference_plumbing"]["accepted_notes"],2)
            later=load(self.run/"trials"/"r0-recovery-reference_plumbing-e2"/"automatic_briefs.json")
            self.assertEqual(len(later["items"]),1)
            self.assertIsNone(report["arms"]["reference_plumbing"]["total_tokens"])
        finally:
            import shutil
            for workspace in set(generated_workspaces):shutil.rmtree(workspace,ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
