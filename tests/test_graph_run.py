import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from memorylab import sandbox
from memorylab.cards import CanonicalCards,EvidenceStore
from memorylab.common import exclusive_run,save
from memorylab.contracts import canonical
from memorylab.graph_run import GraphRun,initialize
from memorylab.ledger import Ledger
from memorylab.registry import Registry
from memorylab.stores import CorpusStore
from test_kernel import AT,Fixture
import test_role_stage
import test_task_stage


class GraphRunTests(Fixture):
    setup_graph=test_role_stage.RoleStageTests.setup_graph
    finish=test_role_stage.RoleStageTests.finish
    outputs=test_role_stage.RoleStageTests.outputs
    derive=test_role_stage.RoleStageTests.derive
    run_until=test_role_stage.RoleStageTests.run_until
    role_code=test_role_stage.RoleStageTests.code
    task_code=test_task_stage.TaskStageTests.code
    crash=staticmethod(test_role_stage.RoleStageTests.crash)

    def setup_round(self,research=False,shadow=False,derived=False,seeded=False,mode="independent_learning",bad_extract=False):
        self.setup_graph(research=research,shadow=shadow,derived=derived,seeded=seeded)
        self.options={"mode":mode,"memory_refs":self.g["memory_refs"],"scenario_refs":self.g["scenario_refs"],"seeds":self.g["seeds"],
            "meta_memory_ref":self.g["meta_memory_ref"],"shadow_memory_ref":self.g["shadow_memory_ref"],"schedule":self.g["config"],
            "execution":self.g["execution"],"role":self.inputs.role,"scenario":self.inputs.contract}
        if mode!="independent_learning":
            from memorylab.graph_run import _compile
            self.g=_compile(self.registry,self.store,self.initial,self.inputs.research,self.options)
        self.programs={};bad_done=False
        for node in self.g["slots"]:
            if node["role"] not in ("solver","acquisition","oracle","extractor","consolidation","reflector","evaluator","proposer","shadow") or node["condition"]=="disabled":continue
            if node["role"] in ("solver","acquisition","oracle"):
                self.task=node;code=self.task_code()
            elif node["role"]=="extractor":
                if bad_extract and not bad_done:code=self.role_code(read=False);bad_done=True
                else:code=self.role_code(output={"cards":[]})
            else:code=self.role_code()
            self.programs[node["id"]]=code
        self.root=Path(self.tmp.name)/"round"

    def init(self,fault=None,programs=None,options=None,root=None):
        return initialize(self.cards,self.registry,self.initial,self.inputs.research,options or self.options,
            self.programs if programs is None else programs,root or self.root,fault)

    def test_snapshot_only_initialization_preserves_source_and_is_independent_and_self_contained(self):
        self.setup_round(research=True,seeded=True)
        with self.ledger.mutation():
            private=self.ledger.put({"kind":"private_grade","id":"hidden-controller-grade","version":1,"text":"PRIVATE_SCORE_MARKER"},"control")
        before=self.ledger.audit();first=self.init();second=self.init()
        self.assertFalse(first["cache_hit"]);self.assertTrue(second["cache_hit"]);self.assertEqual(self.ledger.audit(),before)
        with GraphRun(self.root) as run:
            self.assertNotEqual(run.inputs.plan,self.g["sha256"])
            self.assertFalse(run.ledger.objects("private_grade","control"));self.assertFalse(run.ledger.objects("stage_artifact","control"))
            self.assertNotIn("PRIVATE_SCORE_MARKER",canonical(run.value).decode())
            self.assertEqual(set(run.value["imports"]),{"task","evaluator","proposer"})
            self.assertEqual(len(run.cards.active("task/checkpoint",run.ledger.head())),2)
            for role in ("evaluator","proposer"):
                self.assertEqual(len(run.cards.active("research/"+role,run.ledger.head())),2)
            report=run.report();self.assertEqual(report["task_report"]["planned"],5);self.assertEqual(report["task_report"]["unavailable"],5)
            self.assertEqual(run.inputs.denied_roots,tuple(sorted([str(self.db_path.parent.resolve()),str(self.root.resolve())])))
        self.ledger.close();self.db_path.rename(self.db_path.with_name("unavailable-origin.sqlite"))
        with GraphRun(self.root) as run:self.assertEqual(run.report()["task_report"]["planned"],5)

    def test_all_initialization_crash_phases_publish_nothing_then_recover_without_manual_cleanup(self):
        self.setup_round(research=True)
        before=self.ledger.audit()
        for phase in ("task_import","research_evaluator","research_proposer","bindings","ready","before_publish"):
            with self.assertRaises(RuntimeError):self.init(self.crash(phase))
            self.assertFalse((self.root/"ledger.sqlite").exists());self.assertEqual(self.ledger.audit(),before)
            self.assertFalse(list(self.root.glob(".round-pending-*")))
        with self.assertRaises(RuntimeError):self.init(self.crash("published"))
        self.assertTrue((self.root/"ledger.sqlite").is_file());self.assertEqual((self.root/"ledger.sqlite").stat().st_nlink,1)
        self.assertTrue(self.init()["cache_hit"]);self.assertEqual(self.ledger.audit(),before)

    def test_recipe_program_namespace_budget_and_path_drift_are_rejected(self):
        self.setup_round(research=True);self.init()
        changed=dict(self.programs);key=next(iter(changed));changed[key]+="\n# changed fixture\n"
        self.assert_code("graph_run_recipe_drift",lambda:self.init(programs=changed))
        missing=dict(self.programs);missing.pop(key)
        self.assert_code("graph_run_program_slots_mismatch",lambda:self.init(programs=missing,root=self.root.parent/"missing"))
        wrong={"evaluator":self.inputs.research["proposer"],"proposer":self.inputs.research["evaluator"]}
        self.assert_code("graph_run_research_namespace",lambda:initialize(self.cards,self.registry,self.initial,wrong,self.options,self.programs,self.root.parent/"wrong"))
        changed=copy.deepcopy(self.options);changed["execution"]["round_calls"]=1
        self.assert_code("graph_run_prospective_budget_exceeded",lambda:self.init(options=changed,root=self.root.parent/"over-budget"))
        self.assertFalse((self.root.parent/"over-budget").exists())
        for config,code in (({},"invalid_execution_config"),({**self.options["execution"],"mode":"formal_comparison","live_execution_authorized":True},"live_graph_run_not_accepted")):
            changed=copy.deepcopy(self.options);changed["execution"]=config
            self.assert_code(code,lambda:self.init(options=changed,root=self.root.parent/"bad-config"))
            self.assertFalse((self.root.parent/"bad-config").exists())
        self.assert_code("graph_run_research_snapshots",lambda:initialize(self.cards,self.registry,self.initial,None,self.options,self.programs,self.root.parent/"bad-research"))
        self.assertFalse((self.root.parent/"bad-research").exists())
        self.assert_code("graph_run_origin_root_alias",lambda:self.init(root=self.db_path.parent))
        alias=self.root.parent/"alias";alias.symlink_to(self.root,target_is_directory=True)
        self.assert_code("invalid_graph_run_root",lambda:GraphRun(alias))

    def test_driver_runs_complete_task_research_and_four_shadows_without_test_dispatch(self):
        self.setup_round(research=True,shadow=True);self.init()
        with GraphRun(self.root) as run:
            result=run.run(AT);self.assertEqual(result["status"],"terminal",result.get("attention"))
            self.assertEqual(result["stage_report"]["terminal"],result["stage_report"]["planned"])
            self.assertEqual(result["task_report"]["full_success"],5)
            self.assertEqual(len([r for r in result["stage_report"]["rows"] if r["role"]=="shadow_grade"]),4)
            before=run.ledger.audit();self.assertEqual(run.run(AT),result);self.assertEqual(run.ledger.audit(),before)
            self.assertFalse(result["gain_evidence"]);self.assertFalse(result["kernel_frozen"])
            # Actual child receipt loss must reject cached success, before any
            # new dispatch. Neither the original denominator nor events vanish.
            slot=next(n["id"] for n in run.inputs.graph["slots"] if n["role"]=="shadow")
            publication=run.ledger.get(run.actors.resolve(slot)["publication"])
            p=run.inputs.resolve(run.inputs.prepare(slot))["payload"]
            with Ledger(p["resources"]["store_binding"]["path"]) as child:
                child.db.execute("DELETE FROM receipts WHERE request_id=?",(publication["child_receipt"]["request_id"],))
            before=run.ledger.audit()
            self.assert_code("role_stage_child_receipt_drift",run.report)
            self.assert_code("role_stage_child_receipt_drift",lambda:run.run(AT))
            self.assertEqual(run.ledger.audit(),before)
            self.assertEqual(run.journal.report()["planned"],result["stage_report"]["planned"])
        self.assertEqual(json.loads((self.root/"report.json").read_text()),result)

    def test_shared_acquisition_and_bound_noeligible_driver_paths_are_reachable(self):
        self.setup_round(mode="component_comparison",derived=True,seeded=False);self.init()
        with GraphRun(self.root) as run:
            result=run.run(AT);self.assertEqual(result["status"],"terminal",result.get("attention"))
            self.assertEqual(result["task_report"]["planned"],5)
            self.assertEqual(result["task_report"]["rows"][0]["agent_role"],"acquisition")
            noops=[r for r in result["stage_report"]["rows"] if r["role"] in ("consolidation","reflector")]
            self.assertTrue(noops)
            for row in noops:
                if row["status"]=="disabled":continue
                self.assertEqual(run.actors.resolve_noop(row["id"])["simulated_usage"]["session_invocations"],0)
            self.assertEqual(run.run(AT),result)

    def test_failed_extractor_keeps_blocked_tasks_and_unknown_grades_and_continues_oracle_and_research(self):
        self.setup_round(research=True,bad_extract=True);self.init()
        with GraphRun(self.root) as run:
            result=run.run(AT);self.assertEqual(result["status"],"terminal",result.get("attention"))
            self.assertEqual(result["task_report"]["planned"],5);self.assertEqual(result["task_report"]["unavailable"],2)
            self.assertEqual(result["task_report"]["full_success"],3)
            self.assertEqual(len([r for r in result["task_report"]["rows"] if r["task_status"]=="blocked"]),2)
            self.assertTrue(any(r["role"]=="proposer" and r["status"]=="completed" for r in result["stage_report"]["rows"]))

    def test_driver_sealed_crash_resume_keeps_frozen_program_and_does_not_repeat_actor(self):
        self.setup_round();self.init()
        with GraphRun(self.root) as run:
            with self.assertRaises(RuntimeError):run.run(AT,fault=self.crash("after_seal"))
            executions=run.ledger.objects("task_execution","control");self.assertEqual(len(executions),1)
            first=executions[0][0];slot=run.inputs.resolve(executions[0][1]["input"])["payload"]["slot"]
            report=run.report();self.assertEqual(report["status"],"partial")
            self.assertEqual(report["task_report"]["planned"],5)
        with GraphRun(self.root) as run:
            result=run.run(AT);self.assertEqual(result["status"],"terminal",result.get("attention"))
            self.assertEqual(run.ledger.get(run.tasks.resolve_task(slot)["capture"])["execution"],first)
            self.assertEqual(len(run.ledger.objects("task_execution","control")),5)

    def test_missing_process_handle_needs_attention_and_single_executor_blocks_reentry(self):
        self.setup_round();self.init()
        with GraphRun(self.root) as run:
            with self.assertRaises(RuntimeError):run.run(AT,fault=self.crash("after_process"))
            execution=run.ledger.objects("task_execution","control")[0][1]
            p=run.inputs.resolve(execution["input"])["payload"]
            handle=run.tasks._directory(p["slot"])/"transport"/"process.json"
            # Actual missing handle, not a guessed terminal state.
            self.assertTrue(handle.exists());handle.unlink()
            result=run.run(AT);self.assertEqual(result["status"],"needs_attention")
            self.assertEqual(result["attention"]["code"],"task_process_exit_unverified")
            self.assertTrue(any(r["status"]=="started" for r in result["stage_report"]["rows"]))
            self.assertEqual(len(run.ledger.objects("task_execution","control")),1)
            with exclusive_run(self.root),self.assertRaises(RuntimeError):run.run(AT)

    def test_limit_preserves_pending_slots_and_report_cli_replays_published_snapshot(self):
        self.setup_round();self.init()
        with GraphRun(self.root) as run:
            result=run.run(AT,limit=1);self.assertEqual(result["status"],"partial")
            self.assertEqual(result["stage_report"]["terminal"],1);self.assertEqual(result["task_report"]["planned"],5)
        proc=subprocess.run([sys.executable,"-m","memorylab","graph-round","report","--run",str(self.root)],
            cwd=Path(__file__).resolve().parent.parent,text=True,capture_output=True,timeout=20)
        self.assertEqual(proc.returncode,0,proc.stderr);self.assertEqual(json.loads(proc.stdout),result)

    def test_mutating_in_memory_program_map_cannot_change_unstarted_frozen_actor(self):
        self.setup_round();self.init()
        with GraphRun(self.root) as run:
            slot=next(iter(run.driver["programs"]));run.driver["programs"][slot]+="\n# drift\n";before=run.ledger.audit()
            self.assert_code("graph_driver_binding_drift",lambda:run.run(AT))
            self.assert_code("graph_driver_binding_drift",run.report)
            self.assertEqual(run.ledger.audit(),before)

    def test_prepare_run_and_report_cli_share_the_same_published_store_and_recipe(self):
        self.setup_round();request=Path(self.tmp.name)/"request.json"
        save(request,{"source_db":str(self.db_path),"initial_snapshot":self.initial,"research_snapshots":self.inputs.research,
            "options":self.options,"programs":self.programs})
        def cli(*args):
            proc=subprocess.run([sys.executable,"-m","memorylab","graph-round",*args],
                cwd=Path(__file__).resolve().parent.parent,text=True,capture_output=True,timeout=30)
            self.assertEqual(proc.returncode,0,proc.stderr);return json.loads(proc.stdout)
        before=self.ledger.audit()
        first=cli("prepare","--request",str(request),"--output",str(self.root));self.assertFalse(first["cache_hit"])
        second=cli("prepare","--request",str(request),"--output",str(self.root));self.assertTrue(second["cache_hit"])
        result=cli("run","--run",str(self.root),"--timestamp",AT,"--limit","1")
        self.assertEqual(result["status"],"partial");self.assertEqual(result["task_report"]["planned"],5)
        self.assertEqual(cli("report","--run",str(self.root)),result);self.assertEqual(self.ledger.audit(),before)

    def test_original_controller_outside_implicit_roots_is_denied_to_task_role_and_production(self):
        self.setup_round()
        cache=Path.home()/".cache";cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="graph-origin-marker-",dir=cache) as temporary:
            origin=Path(temporary).resolve();marker=origin/"public-test-marker.txt";marker.write_text("harmless boundary marker")
            destination=origin/"ledger.sqlite"
            imported=self.store.fork("native-origin",self.initial,destination,"task/checkpoint",self.registry,[self.m,self.e],"initial_checkpoint")
            with Ledger(destination) as ledger:
                cards=CanonicalCards(EvidenceStore(ledger,self.kernel));registry=Registry(ledger,self.kernel);store=CorpusStore(cards)
                initial=store.capture("native-origin-checkpoint","task/checkpoint",imported["import"]["result"]["cutoff"])
                deny="try:\n pathlib.Path("+repr(str(marker))+").read_text()\nexcept PermissionError: pass\nelse: raise AssertionError('original controller was readable')\n"
                programs={}
                for node in self.g["slots"]:
                    if node["id"] not in self.programs:continue
                    if node["role"] in ("solver","acquisition","oracle"):
                        self.task=node
                        # The solved engine repeats the same check inside the
                        # fresh production grader, after subject tools finish.
                        extra=deny+"f=pathlib.Path('engine.py');f.write_text("+repr("import pathlib\n"+deny)+"+f.read_text())\n"
                        programs[node["id"]]=self.task_code(extra=extra)
                    else:programs[node["id"]]=self.role_code(output={"cards":[]},extra=deny)
                initialize(cards,registry,initial,{},self.options,programs,self.root)
            # Without the bound origin, this path is accessible: it is outside
            # Project, global memories and the sibling temporary-worker roots.
            with tempfile.TemporaryDirectory(prefix="graph-boundary-control-",dir="/tmp") as worker:
                proc=subprocess.run(sandbox.command(worker,self.root,[sys.executable,"-I","-B","-c",
                    "import pathlib;print(pathlib.Path("+repr(str(marker))+").read_text())"],True,False),
                    cwd=worker,text=True,capture_output=True,env=sandbox.worker_environment(),timeout=10)
                self.assertEqual(proc.returncode,0,proc.stderr);self.assertIn("harmless boundary marker",proc.stdout)
            with GraphRun(self.root) as run:
                result=run.run(AT);self.assertEqual(result["status"],"terminal",result.get("attention"))
                self.assertEqual(result["task_report"]["full_success"],5)
                self.assertTrue(any(r["role"]=="extractor" and r["status"]=="completed" for r in result["stage_report"]["rows"]))
