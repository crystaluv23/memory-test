import base64
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from memorylab.common import save
from memorylab.contracts import canonical
from memorylab.kernel_cli import read_json
from memorylab.runtime import UsageStore
from memorylab.scheduling import StageJournal,compile_schedule,schedule_config
from memorylab.stage_inputs import StageInputs
from memorylab.stores import CorpusStore
from memorylab.task_stage import TaskStages
from test_kernel import AT,Fixture
from test_scenario import CANDIDATES
import test_stage_inputs


class TaskStageTests(Fixture):
    # Reuse setup/producer helpers without inheriting their full test suite.
    def setUp(self):
        super().setUp();self.store=CorpusStore(self.cards)
        self.m=self.registry.register_root("memory","taskMemory")
        self.alt=self.registry.register_root("memory","alternative",dict(self.kernel["memory_initial"],**{"credit.type":"outcome_ema"}))
        self.meta=self.registry.register_root("memory","researchMemory")
        self.e=self.registry.register_root("scenario","focus")
        self.initial=self.store.capture("initial","task/checkpoint",self.ledger.head())
        self.initial_hash=self.store.resolve(self.initial)["payload"]["corpus"]["sha256"]
    finish=test_stage_inputs.StageInputTests.finish
    outputs=test_stage_inputs.StageInputTests.outputs
    derive=test_stage_inputs.StageInputTests.derive
    run_until=test_stage_inputs.StageInputTests.run_until

    def setup_task(self,mode="independent_learning",seeded=False,contract=None):
        if seeded:
            src=self.source("checkpointSource",ns="task/checkpoint")
            self.extract(self.raw_card([src],id="checkpointCard"),ns="task/checkpoint")
            self.initial=self.store.capture("seededInitial","task/checkpoint",self.ledger.head())
            self.initial_hash=self.store.resolve(self.initial)["payload"]["corpus"]["sha256"]
        schedule=schedule_config()
        for role in schedule["roles"].values():role["seconds"]=5
        self.g=compile_schedule(self.registry,[self.m],[self.e],[11],self.initial_hash,mode=mode,config=schedule,scenario_contract=contract)
        self.j=StageJournal(self.ledger,self.g)
        self.inputs=StageInputs(self.cards,self.registry,self.j,self.initial,Path(self.tmp.name)/"scopes",scenario=contract)
        self.nodes={n["id"]:n for n in self.g["slots"]};self.prepared={};self.count=0
        self.task=next(n for n in self.g["slots"] if n["role"] in ("solver","acquisition"))
        self.run_until(self.task["id"])
        self.stages=TaskStages(self.inputs,Path(self.tmp.name)/"task-output")
        return self.task["id"]

    def code(self,good=True,open_memory=True,extra="",tools=True):
        engine=CANDIDATES[self.task["bindings"]["episode"]["backend"]] if good else None
        return '''import json,pathlib,subprocess,sys
p=json.load(sys.stdin)
def emit(event):print(json.dumps(event),flush=True)
emit({'type':'thread.started','thread_id':'explicit-fixture-model-zero'})
emit({'type':'turn.started'})
emit({'type':'item.completed','item':{'id':'private','type':'reasoning','text':'private-reasoning-marker'}})
claims=[item['id'] for item in p['context']['briefs']['items']]
''' + ("pathlib.Path('engine.py').write_text("+repr(engine)+")\n" if engine else "") + '''
def tool(script,arg):
 r=subprocess.run([sys.executable,'-I','-B',script,arg],capture_output=True,text=True,timeout=3)
 assert r.returncode==0,r.stderr
 emit({'type':'item.completed','item':{'id':script+arg,'type':'command_execution','command':script+' '+arg,'aggregated_output':r.stdout,'exit_code':r.returncode,'status':'completed'}})
''' + ("for id_ in claims:tool('open_memory.py',id_)\ntool('open_memory.py','nonexistentCard')\n" if open_memory else "") + ("tool('scenario.py','inspect')\ntool('scenario.py','check')\n" if tools else "") + extra + '''
final={'summary':'Explicit independent transport fixture','completion_claim':'complete','memory_uses':claims+['unknownClaim']}
emit({'type':'item.completed','item':{'id':'final','type':'agent_message','text':json.dumps(final)}})
emit({'type':'turn.completed','usage':{'input_tokens':90,'cached_input_tokens':20,'output_tokens':10}})
'''

    def crash(self,phase):
        def fault(name):
            if name==phase:raise RuntimeError("explicit interruption at "+phase)
        return fault

    def execute_ref(self):return self.ledger.objects("task_execution","control")[0][0]

    def test_actual_task_grade_extractor_join_same_episode_and_keep_private_reasoning_and_grade_out(self):
        slot=self.setup_task(seeded=True)
        result=self.stages.run_fixture(slot,self.code(),AT)
        capture=self.ledger.get(result["capture"])
        self.assertTrue(capture["transport"]["transport_success"],capture["transport"])
        self.assertEqual(result["actual_model_calls"],0);self.assertFalse(result["gain_evidence"])
        self.assertEqual(result["transport_usage"]["input_tokens"],90)
        grade=self.stages.grade(slot+"-grade",AT)
        private=self.ledger.get(grade["grade"])["value"]
        self.assertTrue(private["full_success"],private)
        packet=self.inputs.resolve(self.inputs.prepare(slot+"-extract"))["payload"]
        source_refs=packet["resources"]["source_refs"]
        public=[self.ledger.get(ref) for ref in source_refs]
        self.assertTrue(all(s["episode"]==slot for s in public))
        text=canonical(public).decode()
        self.assertNotIn("private-reasoning-marker",text)
        self.assertNotIn('"groups"',text);self.assertNotIn('"expected"',text)
        raw=base64.b64decode(capture["raw_files"]["events.jsonl"])
        self.assertIn(b"private-reasoning-marker",raw)
        usage=UsageStore(self.cards).visible(self.task["namespace"],self.ledger.head())
        events=[v for v in usage if v["episode"]==slot]
        operations=[v["event_type"] for v in events]
        self.assertIn("open",operations);self.assertIn("claimed_use",operations);self.assertIn("independent_outcome",operations)
        self.assertNotIn("behavior_link",operations)
        self.assertEqual([v["outcome"] for v in events if v["event_type"]=="independent_outcome"],["success"])
        self.assertEqual(len([v for v in events if v["event_type"]=="open"]),1)
        self.assertTrue(self.stages.grade(slot+"-grade",AT)["cache_hit"])
        self.ledger.audit()

    def test_false_completion_is_independent_failure_and_stays_in_denominator(self):
        slot=self.setup_task()
        task=self.stages.run_fixture(slot,self.code(good=False),AT)
        self.assertTrue(self.ledger.get(task["capture"])["transport"]["transport_success"])
        grade=self.ledger.get(self.stages.grade(slot+"-grade",AT)["grade"])["value"]
        self.assertFalse(grade["full_success"]);self.assertTrue(grade["false_completion"])
        self.assertEqual(self.j.report()["planned"],len(self.g["slots"]))
        self.assertFalse(self.j.report()["promotion_eligible"])
        self.assertTrue(self.inputs.prepare(slot+"-extract"))

    def test_unread_claims_remain_claims_and_never_become_open_or_behavior_links(self):
        slot=self.setup_task(seeded=True)
        task=self.stages.run_fixture(slot,self.code(open_memory=False),AT)
        artifact=self.ledger.get(task["stage"]["result"]["artifact"])["artifacts"]
        self.assertIn("checkpointCard",artifact["claimed_without_open"])
        usage=UsageStore(self.cards).visible(self.task["namespace"],self.ledger.head())
        self.assertIn("claimed_use",[v["event_type"] for v in usage])
        self.assertNotIn("open",[v["event_type"] for v in usage])
        self.assertNotIn("behavior_link",[v["event_type"] for v in usage])

    def test_task_commit_all_checkpoints_rollback_then_reuse_seal_without_reexecution(self):
        slot=self.setup_task();code=self.code()
        with self.assertRaises(RuntimeError):self.stages.run_fixture(slot,code,AT,self.crash("after_seal"))
        execution=self.execute_ref();before=self.ledger.audit()
        for phase in ("capture","sources","usage","stage","receipt"):
            with self.assertRaises(RuntimeError):self.stages.commit_task(execution,AT,self.crash(phase))
            self.assertEqual(self.ledger.audit(),before,phase)
            self.assertEqual(self.ledger.objects("task_capture","control"),[])
        with patch("memorylab.task_stage.capture_transport",side_effect=AssertionError("must not execute twice")):
            result=self.stages.run_fixture(slot,code,AT)
            self.assertTrue(self.stages.run_fixture(slot,code,AT)["cache_hit"])
        self.assertEqual(len(self.ledger.objects("task_capture","control")),1)
        self.assert_code("idempotency_conflict",lambda:self.stages.run_fixture(slot,code+"\n# different",AT))
        self.assertEqual(result["new_model_calls"],0)

    def test_grade_commit_all_checkpoints_rollback_then_reuse_independent_grade(self):
        slot=self.setup_task();self.stages.run_fixture(slot,self.code(),AT)
        with self.assertRaises(RuntimeError):self.stages.grade(slot+"-grade",AT,self.crash("after_grade"))
        before=self.ledger.audit()
        with patch("memorylab.task_stage.verify",side_effect=AssertionError("must reuse exact sealed grade")):
            for phase in ("grade","outcome","usage","stage","receipt"):
                with self.assertRaises(RuntimeError):self.stages.grade(slot+"-grade",AT,self.crash(phase))
                self.assertEqual(self.ledger.audit(),before,phase)
            result=self.stages.grade(slot+"-grade",AT)
        self.assertTrue(self.ledger.get(result["grade"])["value"]["full_success"])
        self.assertEqual(len(self.ledger.objects("task_grade","control")),1)

    def test_exited_without_seal_preserves_raw_and_commits_interruption_not_retry(self):
        slot=self.setup_task();code=self.code()
        with self.assertRaises(RuntimeError):self.stages.run_fixture(slot,code,AT,self.crash("after_process"))
        with patch("memorylab.task_stage.capture_transport",side_effect=AssertionError("must not retry")):
            result=self.stages.run_fixture(slot,code,AT)
        capture=self.ledger.get(result["capture"])
        self.assertIsNone(capture["seal"])
        self.assertEqual(result["stage"]["result"]["outcome"],"interrupted")
        raw=base64.b64decode(capture["raw_files"]["events.jsonl"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),capture["raw_sha256"]["events.jsonl"])
        grade=self.ledger.get(self.stages.grade(slot+"-grade",AT)["grade"])["value"]
        self.assertEqual(grade["verification"],"unavailable");self.assertFalse(grade["full_success"])

    def test_missing_or_live_process_handle_cannot_close_or_restart_task(self):
        slot=self.setup_task();code=self.code()
        with self.assertRaises(RuntimeError):self.stages.run_fixture(slot,code,AT,self.crash("after_seal"))
        before=self.ledger.audit()
        for status in ({"status":"unknown"},{"status":"running"}):
            with patch("memorylab.task_stage.process_status",return_value=status),patch("memorylab.task_stage.capture_transport",side_effect=AssertionError("cannot restart")):
                self.assert_code("task_process_exit_unverified",lambda:self.stages.run_fixture(slot,code,AT))
            self.assertEqual(self.ledger.audit(),before)

    def test_resigned_raw_usage_or_memory_wire_is_not_accepted(self):
        slot=self.setup_task(seeded=True);code=self.code()
        with self.assertRaises(RuntimeError):self.stages.run_fixture(slot,code,AT,self.crash("after_seal"))
        directory=self.stages._directory(slot);seal=read_json(directory/"seal.json");before=self.ledger.audit()
        changed=copy.deepcopy(seal);changed["memory_events"][1]["tokens"]=0;save(directory/"seal.json",changed)
        self.assert_code("task_memory_audit_mismatch",lambda:self.stages.commit_task(self.execute_ref(),AT))
        save(directory/"seal.json",seal)
        path=directory/"transport/transport.json";raw=path.read_bytes();transport=read_json(path)
        transport["usage"]["input_tokens"]=0;save(path,transport)
        changed=copy.deepcopy(seal);changed["files"]["transport.json"]=hashlib.sha256(path.read_bytes()).hexdigest();save(directory/"seal.json",changed)
        self.assert_code("role_capture_result_mismatch",lambda:self.stages.commit_task(self.execute_ref(),AT))
        self.assertEqual(self.ledger.audit(),before)
        path.write_bytes(raw);save(directory/"seal.json",seal)
        self.assertTrue(self.stages.commit_task(self.execute_ref(),AT))

    def test_grade_seal_bound_to_exact_input_and_capture(self):
        slot=self.setup_task();self.stages.run_fixture(slot,self.code(),AT)
        with self.assertRaises(RuntimeError):self.stages.grade(slot+"-grade",AT,self.crash("after_grade"))
        path=self.stages.output/("grade-"+slot+"-grade")/"seal.json";seal=read_json(path)
        changed=copy.deepcopy(seal);changed["capture"]["sha256"]="0"*64;save(path,changed)
        self.assert_code("task_grade_seal_mismatch",lambda:self.stages.grade(slot+"-grade",AT))
        self.assertEqual(self.ledger.objects("task_grade","control"),[])

    def test_unsafe_symlink_and_non_utf8_candidate_are_recorded_without_parent_read_or_decode_crash(self):
        slot=self.setup_task()
        code=self.code(extra="pathlib.Path('engine.py').write_bytes(b'\\xff\\xfe')\n",tools=False)
        result=self.stages.run_fixture(slot,code,AT)
        private=self.ledger.get(self.stages.grade(slot+"-grade",AT)["grade"])["value"]
        self.assertFalse(private["full_success"])
        sources=self.inputs._dependency(slot)["artifacts"]["observable_sources"]
        diff=next(self.ledger.get(r) for r in sources if self.ledger.get(r)["event_type"]=="file_diff")
        self.assertEqual(json.loads(diff["text"])["unavailable"]["reason"],"non_utf8_task_source")
        self.assertTrue(self.stages.run_fixture(slot,code,AT)["cache_hit"])

    def test_symlink_workspace_fails_collection_and_independent_grade_remains_unavailable(self):
        slot=self.setup_task()
        code=self.code(extra="pathlib.Path('engine.py').unlink();pathlib.Path('engine.py').symlink_to('/does-not-exist-task-fixture')\n",tools=False)
        result=self.stages.run_fixture(slot,code,AT);capture=self.ledger.get(result["capture"])
        self.assertEqual(capture["seal"]["workspace_boundary_error"],"unsafe_workspace_file")
        self.assertEqual(result["stage"]["result"]["outcome"],"failed")
        grade=self.ledger.get(self.stages.grade(slot+"-grade",AT)["grade"])["value"]
        self.assertEqual(grade["verification"],"unavailable")
        self.assertTrue(self.inputs.prepare(slot+"-extract"))

    def test_acquisition_has_no_checkpoint_memory_and_oracle_has_same_off_policy(self):
        slot=self.setup_task(mode="component_comparison",seeded=True)
        code=self.code(extra="assert not claims\n",tools=False)
        result=self.stages.run_fixture(slot,code,AT);capture=self.ledger.get(result["capture"])
        self.assertTrue(capture["transport"]["transport_success"])
        self.assertEqual(capture["seal"]["memory_events"][0]["memory_refs"],[])
        oracle=next(n for n in self.g["slots"] if n["role"]=="oracle")
        self.run_until(oracle["id"])
        self.task=oracle
        result=self.stages.run_fixture(oracle["id"],self.code(extra="assert not claims\n",tools=False),AT)
        self.assertTrue(self.ledger.get(result["capture"])["transport"]["transport_success"])

    def test_bound_production_limit_and_private_oversized_tool_data_do_not_leak(self):
        from memorylab.scenario import read_scenario
        contract=read_scenario();contract["production_stdout_bytes"]=128;contract["max_response_bytes"]=128
        slot=self.setup_task(contract=contract)
        code=self.code(good=False,extra="",tools=False)
        code=code.replace("claims=[item", "pathlib.Path('engine.py').write_text(\"def process(c):\\n print('x'*5000,flush=True)\\n return {}\\n\")\nclaims=[item")
        code=code.replace("final={'summary'", "tool('scenario.py','check')\nfinal={'summary'")
        result=self.stages.run_fixture(slot,code,AT)
        capture=self.ledger.get(result["capture"]);event=capture["seal"]["scenario_events"][0]
        self.assertEqual(event["response"],{"error":"scenario_response_limit"})
        self.assertIn("private_oversized_response",event)
        artifact=self.ledger.get(result["stage"]["result"]["artifact"])["artifacts"]
        sources=[self.ledger.get(r) for r in artifact["observable_sources"]]
        tools=[s["text"] for s in sources if s["event_type"]=="tool_result"]
        self.assertNotIn("private_oversized_response",canonical(tools).decode())
        self.assertNotIn("x"*128,canonical(tools).decode())
        grade=self.ledger.get(self.stages.grade(slot+"-grade",AT)["grade"])["value"]
        self.assertEqual(grade["production"]["error"],"production_stdout_overflow")
        self.assertEqual(grade["production"]["raw_streams"]["stdout"]["bytes"],128)
        self.assertFalse(grade["full_success"])

    def test_task_and_grade_children_do_not_inherit_fake_secret_or_controller_environment(self):
        slot=self.setup_task()
        names=["OPENROUTER_API_KEY","ANTHROPIC_AUTH_TOKEN","HTTPS_PROXY","CODEX_APP_TOOLS_PIPE_PATH","CODEX_THREAD_ID"]
        extra="import os\nassert not set("+repr(names)+") & set(os.environ)\n"
        engine=CANDIDATES[self.task["bindings"]["episode"]["backend"]]+extra
        code=self.code(extra=extra,tools=False).replace(repr(CANDIDATES[self.task["bindings"]["episode"]["backend"]]),repr(engine))
        with patch.dict("os.environ",{key:"harmless-environment-marker" for key in names}):
            result=self.stages.run_fixture(slot,code,AT)
            grade=self.ledger.get(self.stages.grade(slot+"-grade",AT)["grade"])["value"]
        self.assertTrue(self.ledger.get(result["capture"])["transport"]["transport_success"])
        self.assertTrue(grade["full_success"],grade)

    def test_production_created_link_is_rejected_by_independent_post_execution_check(self):
        slot=self.setup_task();base=CANDIDATES[self.task["bindings"]["episode"]["backend"]]
        engine="import os\nos.symlink('SPEC.md','created-link')\n"+base
        result=self.stages.run_fixture(slot,self.code(tools=False).replace(repr(base),repr(engine)),AT)
        self.assertEqual(result["stage"]["result"]["outcome"],"completed")
        grade=self.ledger.get(self.stages.grade(slot+"-grade",AT)["grade"])["value"]
        self.assertFalse(grade["full_success"])
        self.assertTrue(any(v.startswith("workspace_boundary:") for v in grade["protected_violations"]))

    def test_whole_source_overflow_has_explicit_unavailable_identity_not_truncated_rule(self):
        slot=self.setup_task();text="unavailable-fixture-"+"x"*self.kernel["limits"]["source_text_bytes"]
        ref=self.stages._source(self.task["namespace"],slot,0,"tool_result",text,AT,{"explicit_fixture":True})
        value=json.loads(self.ledger.get(ref)["text"])
        self.assertEqual(value["status"],"unavailable")
        self.assertEqual(value["original_bytes"],len(text.encode()))
        self.assertEqual(value["original_sha256"],hashlib.sha256(text.encode()).hexdigest())

    def test_task_and_independent_production_cannot_read_sibling_controller_ledger(self):
        slot=self.setup_task()
        checks="import os\ntry:\n open("+repr(str(self.db_path))+",'rb').read(1);raise AssertionError('controller read allowed')\nexcept PermissionError:pass\n"
        checks+="try:\n os.listdir("+repr(str(self.inputs.output))+");raise AssertionError('scope read allowed')\nexcept PermissionError:pass\n"
        base=CANDIDATES[self.task["bindings"]["episode"]["backend"]]
        engine=base+checks
        code=self.code(extra=checks,tools=False).replace(repr(base),repr(engine))
        result=self.stages.run_fixture(slot,code,AT)
        self.assertTrue(self.ledger.get(result["capture"])["transport"]["transport_success"])
        grade=self.ledger.get(self.stages.grade(slot+"-grade",AT)["grade"])["value"]
        self.assertTrue(grade["full_success"],grade)
