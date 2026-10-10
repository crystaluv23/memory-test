import base64
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from memorylab.admission import RoleAdmission,resolve_capture
from memorylab.common import save
from memorylab.contracts import canonical
from memorylab.derived import DerivedStage
from memorylab.kernel_cli import read_json
from memorylab.ledger import Ledger
from memorylab.role_stage import RoleStages
from memorylab.runtime import RuntimeBuilder,UsageStore
from memorylab.scheduling import StageJournal,compile_schedule,schedule_config
from memorylab.stage_inputs import StageInputs
from memorylab.stores import CorpusStore
from test_kernel import AT,Fixture
import test_stage_inputs


class RoleStageTests(Fixture):
    finish=test_stage_inputs.StageInputTests.finish
    outputs=test_stage_inputs.StageInputTests.outputs
    derive=test_stage_inputs.StageInputTests.derive
    run_until=test_stage_inputs.StageInputTests.run_until

    def setup_graph(self,research=False,shadow=False,seeded=True,derived=True):
        store=CorpusStore(self.cards);self.store=store
        config=dict(self.kernel["memory_initial"],**{"loader.global_briefs":2})
        if derived:config.update({"consolidation.type":"single_index","reflection.trigger":"feedback"})
        self.m=self.registry.register_root("memory","taskMemory",config)
        self.alt=self.registry.register_root("memory","alternative",dict(self.kernel["memory_initial"],**{"loader.global_briefs":2}))
        self.meta=self.registry.register_root("memory","researchMemory",config)
        self.e=self.registry.register_root("scenario","focus",dict(self.kernel["scenario_initial"],distractor_count=0))
        research_snapshots={};research_hashes={}
        for ns in ["task/checkpoint"]+(["research/evaluator","research/proposer"] if research else []):
            if seeded:
                for i in range(2):
                    src=self.source("seed-"+ns.replace("/","-")+str(i),ns=ns)
                    self.extract(self.raw_card([src],id="seed"+str(i)),request="seed-extract-"+ns.replace("/","-")+str(i),ns=ns)
            ref=store.capture("initial-"+ns.replace("/","-"),ns,self.ledger.head())
            if ns.startswith("research/"):
                role=ns.split("/")[1];research_snapshots[role]=ref
                research_hashes[role]=store.resolve(ref)["payload"]["corpus"]["sha256"]
            else:self.initial=ref
        self.initial_hash=store.resolve(self.initial)["payload"]["corpus"]["sha256"]
        schedule=schedule_config()
        for limits in schedule["roles"].values():limits["seconds"]=5
        self.g=compile_schedule(self.registry,[self.m],[self.e],[11],self.initial_hash,config=schedule,
            meta_memory_ref=self.meta if research else None,shadow_memory_ref=self.alt if shadow else None,
            research_corpora=research_hashes if research else None)
        self.j=StageJournal(self.ledger,self.g)
        self.inputs=StageInputs(self.cards,self.registry,self.j,self.initial,Path(self.tmp.name)/"scopes",research_snapshots)
        self.nodes={n["id"]:n for n in self.g["slots"]};self.prepared={};self.count=0
        self.roles=RoleStages(self.inputs,Path(self.tmp.name)/"actor-output")

    def target(self,role,scope="main"):
        node=next(n for n in self.g["slots"] if n["role"]==role and n["store_scope"]==scope)
        self.run_until(node["id"]);return node["id"]

    def code(self,output=None,read=True,extra=""):
        # The subprocess receives its own frozen packet; it does not import this
        # fixture's controller, scorer, expected answers, or another arm.
        return '''import json,pathlib,subprocess,sys
p=json.load(sys.stdin);c=p['context']
def emit(v):print(json.dumps(v),flush=True)
emit({'type':'thread.started','thread_id':'explicit-role-fixture'})
emit({'type':'turn.started'})
''' + ('''for source in c.get('sources',c.get('source_catalog',[])):
 r=subprocess.run([sys.executable,'-I','-B','read_source.py',source['id']],capture_output=True,text=True,timeout=3)
 assert r.returncode==0,r.stderr
 assert json.loads(r.stdout).get('text')
''' if read else "") + '''
for brief in c.get('briefs',{}).get('items',[]):
 r=subprocess.run([sys.executable,'-I','-B','open_memory.py',brief['id']],capture_output=True,text=True,timeout=3)
 assert r.returncode==0,r.stderr
 assert 'memory' in json.loads(r.stdout)
''' + extra + ("final="+repr(output)+"\n" if output is not None else '''
if p['role']=='extractor':
 final={'cards':[{'id':'capturedLesson','expected_version':0,'type_hint':'environment/services','brief':'A fixture source was read','detail':'An explicit infrastructure fixture; semantic usefulness remains unverified.','evidence_refs':[c['sources'][0]['ref']],'outcome':'unknown','status':'active','supersedes':[]}]}
elif p['role']=='consolidation':
 final={'indices':[{'type_hint':'environment/services','brief':'A fixture index','detail':'Infrastructure index with explicit lower support; no gain claim.','support_refs':[v['ref'] for v in c['derived_input']['payload']['cards'][:2]]}]}
elif p['role']=='reflector':
 final={'annotations':[{'support':c['derived_input']['payload']['eligible_refs'][0],'text':'Fixture reflection with no claimed task benefit.'}]}
else:
 field='credit.type' if p['role']=='proposer' else 'distractor_count'
 new='outcome_ema' if p['role']=='proposer' else 2
 final={'schema_version':1,'kind':'memory' if p['role']=='proposer' else 'scenario','parent':c['parent'],'mutation':{'field':field,'old':c['parent_config'][field],'new':new},'hypothesis':'Explicit fixture proposal; quality not established.','evidence_refs':[c['history'][0]['ref']]}
''') + '''
emit({'type':'item.completed','item':{'id':'final','type':'agent_message','text':json.dumps(final)}})
emit({'type':'turn.completed','usage':{'input_tokens':90,'cached_input_tokens':20,'output_tokens':10}})
'''

    @staticmethod
    def crash(phase):
        def fault(name):
            if name==phase:raise RuntimeError("explicit fault at "+phase)
        return fault

    def execution(self):return self.ledger.objects("role_stage_execution","control")[0][0]

    def test_extractor_real_source_socket_atomic_canonical_snapshot_consumed_by_next_stage(self):
        self.setup_graph();slot=self.target("extractor")
        result=self.roles.run_fixture(slot,self.code(),AT)
        self.assertEqual(result["role_result"]["outcome"],"materialized",result)
        self.assertEqual(result["actual_model_calls"],0);self.assertFalse(result["gain_evidence"])
        capture=resolve_capture(self.ledger,result["role_result"]["capture"])
        self.assertTrue(capture["source_events"])
        finish=result["stage"]["result"];art=self.ledger.get(finish["artifact"])["artifacts"]
        self.assertIn("snapshot",art)
        ns=self.nodes[slot]["namespace"];self.assertIn("capturedLesson",[c["id"] for r,c in self.cards.active(ns,self.ledger.head())])
        successor=next(n for n in self.g["slots"] if slot in n["depends_on"] and n["role"]=="corpus")
        prepared=self.inputs.resolve(self.inputs.prepare(successor["id"]))["payload"]
        self.assertEqual(prepared["resources"]["snapshot"],art["snapshot"])
        self.assertTrue(self.roles.run_fixture(slot,self.code(),AT)["cache_hit"])
        self.assertEqual(RoleAdmission(self.cards,self.registry).report()["terminal"],1)
        self.ledger.audit()

    def test_derived_roles_use_actual_capture_and_source_ranges_for_runtime_projection(self):
        self.setup_graph()
        first=self.target("consolidation");one=self.roles.run_fixture(first,self.code(),AT)
        second=next(n["id"] for n in self.g["slots"] if n["role"]=="reflector")
        two=self.roles.run_fixture(second,self.code(),AT)
        self.assertEqual(one["role_result"]["outcome"],"materialized")
        self.assertEqual(two["role_result"]["outcome"],"materialized")
        rebuild=next(n for n in self.g["slots"] if n["role"]=="rebuild")
        ref=self.inputs.prepare(rebuild["id"]);p=self.inputs.resolve(ref)["payload"]
        built=RuntimeBuilder(self.cards,self.registry).build(p["namespace"],p["cutoff"],self.m,p["resources"]["derived_refs"])
        self.assertTrue(any(v["level"]==1 for v in built["projection"]["items"]))
        self.assertTrue(built["projection"]["annotations"])
        for role,ref in p["resources"]["derived_refs"].items():
            batch=DerivedStage(self.cards,self.registry).resolve(ref,role,p["namespace"],p["cutoff"],self.m)
            self.assertEqual(batch["invocation"]["mode"],"captured_diagnostic")

    def test_unread_evidence_rejected_no_card_and_failure_blocks_successors(self):
        self.setup_graph();slot=self.target("extractor")
        result=self.roles.run_fixture(slot,self.code(read=False),AT)
        self.assertEqual(result["role_result"]["code"],"unread_extraction_evidence")
        self.assertEqual(result["stage"]["result"]["outcome"],"failed")
        self.assertNotIn("capturedLesson",[c["id"] for r,c in self.cards.active(self.nodes[slot]["namespace"],self.ledger.head())])
        successor=next(n for n in self.g["slots"] if slot in n["depends_on"] and n["role"]=="corpus")
        self.assert_code("stage_dependencies_unavailable",lambda:self.inputs.prepare(successor["id"]))
        self.assertEqual(self.j.report()["planned"],len(self.g["slots"]))

    def test_main_atomic_publication_all_checkpoints_recover_sealed_raw_without_reexecution(self):
        self.setup_graph();slot=self.target("extractor");code=self.code()
        with self.assertRaises(RuntimeError):self.roles.run_fixture(slot,code,AT,self.crash("after_seal"))
        before=self.ledger.audit()
        for phase in ("materialized","publication","stage","receipt"):
            with self.assertRaises(RuntimeError):self.roles.commit(self.execution(),AT,self.crash(phase))
            self.assertEqual(self.ledger.audit(),before,phase)
        with patch("memorylab.role_stage.capture_transport",side_effect=AssertionError("cannot reexecute")):
            result=self.roles.run_fixture(slot,code,AT)
            self.assertTrue(self.roles.run_fixture(slot,code,AT)["cache_hit"])
        self.assertEqual(len(self.ledger.objects("role_capture","control")),1)
        self.assert_code("idempotency_conflict",lambda:self.roles.run_fixture(slot,code+"\n# drift",AT))
        self.assertEqual(result["new_model_calls"],0)

    def test_unsealed_exit_captures_raw_interruption_and_never_materializes_or_retries(self):
        self.setup_graph();slot=self.target("extractor");code=self.code()
        with self.assertRaises(RuntimeError):self.roles.run_fixture(slot,code,AT,self.crash("after_process"))
        with patch("memorylab.role_stage.capture_transport",side_effect=AssertionError("cannot retry")):
            result=self.roles.run_fixture(slot,code,AT)
        self.assertEqual(result["stage"]["result"]["outcome"],"interrupted")
        capture=self.ledger.get(result["role_result"]["capture"])
        raw=base64.b64decode(capture["raw_files"]["events.jsonl"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),capture["files"]["events.jsonl"])
        audit=capture["unverified_audits"]["source_wire.json"];body=base64.b64decode(audit["base64"])
        self.assertTrue(json.loads(body));self.assertEqual(hashlib.sha256(body).hexdigest(),audit["sha256"])
        self.assert_code("failed_role_capture",lambda:resolve_capture(self.ledger,result["role_result"]["capture"]))
        self.assertNotIn("capturedLesson",[c["id"] for r,c in self.cards.active(self.nodes[slot]["namespace"],self.ledger.head())])
        self.assertEqual(RoleAdmission(self.cards,self.registry).report()["terminal"],1)

    def test_pending_handle_preserves_started_no_close_no_retry(self):
        self.setup_graph();slot=self.target("extractor");code=self.code()
        with self.assertRaises(RuntimeError):self.roles.run_fixture(slot,code,AT,self.crash("after_seal"))
        before=self.ledger.audit()
        for status in ("unknown","running"):
            with patch("memorylab.role_stage.process_status",return_value={"status":status}),patch("memorylab.role_stage.capture_transport",side_effect=AssertionError("cannot retry")):
                self.assert_code("role_stage_exit_unverified",lambda:self.roles.run_fixture(slot,code,AT))
            self.assertEqual(self.ledger.audit(),before)

    def test_changed_source_audit_rejected_without_unsupported_card(self):
        self.setup_graph();slot=self.target("extractor");code=self.code()
        with self.assertRaises(RuntimeError):self.roles.run_fixture(slot,code,AT,self.crash("after_seal"))
        path=self.roles._directory(slot)/"seal.json";seal=read_json(path)
        seal["source_events"][0]["response"]["text"]="Invented source";save(path,seal)
        result=self.roles.commit(self.execution(),AT)
        self.assertEqual(result["role_result"]["code"],"role_source_audit_mismatch")
        self.assertEqual(result["stage"]["result"]["outcome"],"failed")
        self.assertIsNone(result["role_result"]["materialization"])

    def test_evaluator_and_proposer_actual_packets_own_memory_outputs_and_postproposal_extraction(self):
        self.setup_graph(research=True)
        for role in ("evaluator","proposer"):
            slot=self.target(role);result=self.roles.run_fixture(slot,self.code(),AT)
            self.assertEqual(result["role_result"]["materialization"]["result"]["status"],"accepted",result)
            self.assertFalse(result["formal_admission"])
            source=self.ledger.get(self.ledger.get(result["stage"]["result"]["artifact"])["artifacts"]["observable_sources"][0])
            self.assertEqual(source["episode"],slot);self.assertEqual(source["namespace"],"research/"+role)
            usage=UsageStore(self.cards).visible("research/"+role,self.ledger.head())
            self.assertTrue(any(v["event_type"]=="open" and v["episode"]==slot for v in usage))
            self.assertFalse(any(v["event_type"]=="behavior_link" for v in usage))
            validation=self.nodes[slot+"-validate"];self.finish(validation)
            packet=self.inputs.resolve(self.inputs.prepare(slot+"-extract"))["payload"]["packet"]
            self.assertIn(source["id"],[row["ref"]["id"] for row in packet["payload"]["context"]["sources"]])
            self.assertNotIn("Explicit fixture proposal",canonical(packet).decode())
            extracted=self.roles.run_fixture(slot+"-extract",self.code(),AT)
            self.assertEqual(extracted["role_result"]["outcome"],"materialized",extracted)
        self.ledger.audit()

    def test_shadow_child_commit_parent_crash_reuses_result_and_never_imports_shadow_state(self):
        self.setup_graph(research=True,shadow=True)
        slot=self.target("shadow","evaluator-shadow-current");code=self.code()
        with self.assertRaises(RuntimeError):self.roles.run_fixture(slot,code,AT,self.crash("child_committed"))
        self.assertEqual(self.ledger.objects("role_stage_publication","control"),[])
        before_sources=self.ledger.objects("source","research/evaluator")
        p=self.inputs.resolve(self.inputs.prepare(slot))["payload"];path=p["resources"]["store_binding"]["path"]
        with Ledger(path) as child:
            self.assertEqual(len(child.objects("role_capture","control")),1)
            self.assertTrue(any(v["episode"]==slot for r,v in child.objects("source","research/evaluator")))
            before=child.audit()
        with patch("memorylab.role_stage.capture_transport",side_effect=AssertionError("cannot execute shadow twice")):
            for phase in ("materialized","publication","stage","receipt"):
                with self.assertRaises(RuntimeError):self.roles.run_fixture(slot,code,AT,self.crash(phase))
            result=self.roles.run_fixture(slot,code,AT)
        with Ledger(path) as child:self.assertEqual(child.audit(),before)
        self.assertEqual(self.ledger.objects("source","research/evaluator"),before_sources)
        self.assertEqual(result["role_result"]["materialization"]["result"]["status"],"accepted")
        self.assertEqual(self.roles.resolve(slot),result)
        with Ledger(path) as child:
            publication=self.ledger.get(result["publication"]);child.db.execute("DELETE FROM receipts WHERE request_id=?",(publication["child_receipt"]["request_id"],))
        self.assert_code("role_stage_child_receipt_drift",lambda:self.roles.resolve(slot))

    def test_shadow_derived_model_capture_consumed_only_in_its_bound_store(self):
        self.setup_graph(research=True,shadow=True)
        slot=self.target("consolidation","evaluator-shadow-current")
        result=self.roles.run_fixture(slot,self.code(),AT)
        self.assertEqual(result["role_result"]["outcome"],"materialized",result)
        self.assertEqual(self.ledger.objects("role_capture","control"),[])
        p=self.inputs.resolve(self.inputs.prepare(slot))["payload"]
        with self.inputs._scope(self.nodes[slot]) as (cards,registry,store,corpus,scope):
            batch=result["role_result"]["materialization"]["result"]["batch"]
            value=DerivedStage(cards,registry).resolve(batch,"consolidation",p["namespace"],p["cutoff"],self.meta)
            self.assertEqual(len(value["items"]),1)
        self.assertTrue(self.roles.run_fixture(slot,self.code(),AT)["cache_hit"])

    def test_noeligible_and_disabled_do_not_need_actor_code_or_create_fake_model_capture(self):
        self.setup_graph(seeded=False)
        slot=self.target("consolidation")
        self.assertEqual(self.inputs.resolve(self.inputs.prepare(slot))["payload"]["action"],"bound_noop")
        with patch("memorylab.role_stage.capture_transport",side_effect=AssertionError("no eligible means no call")):
            with self.assertRaises(RuntimeError):self.roles.run_fixture(slot,None,AT,self.crash("noop_materialized"))
            result=self.roles.run_fixture(slot,None,AT)
        self.assertEqual(result["result"]["outcome"],"completed")
        self.assertEqual(self.ledger.get(result["result"]["attempt"])["reserved_calls"],0)
        self.assertEqual(self.ledger.objects("role_capture","control"),[])

    def test_subject_and_production_children_cannot_read_sibling_controller_ledger(self):
        self.setup_graph();slot=self.target("extractor")
        code=self.code(extra="try:\n pathlib.Path("+repr(str(self.db_path))+ ").read_bytes();raise AssertionError('controller read allowed')\nexcept PermissionError:pass\n")
        result=self.roles.run_fixture(slot,code,AT)
        self.assertEqual(result["role_result"]["outcome"],"materialized",result)

    def test_disabled_role_has_no_start_or_fake_capture(self):
        self.setup_graph(derived=False)
        slot=next(n["id"] for n in self.g["slots"] if n["role"]=="consolidation")
        with patch("memorylab.role_stage.capture_transport",side_effect=AssertionError("disabled means no call")):
            result=self.roles.run_fixture(slot,None,AT)
        self.assertEqual(result["result"]["outcome"],"disabled")
        self.assertEqual(self.ledger.objects("role_stage_execution","control"),[])
        self.assertEqual(self.ledger.objects("role_attempt","control"),[])

    def test_shadow_noeligible_cross_store_crash_reuses_child_batch_before_parent_finish(self):
        self.setup_graph(research=True,shadow=True,seeded=False)
        slot=self.target("consolidation","evaluator-shadow-current")
        p=self.inputs.resolve(self.inputs.prepare(slot))["payload"]
        self.assertEqual(p["action"],"bound_noop")
        with patch("memorylab.role_stage.capture_transport",side_effect=AssertionError("noeligible shadow means no call")):
            with self.assertRaises(RuntimeError):self.roles.run_fixture(slot,None,AT,self.crash("noop_materialized"))
            self.assertNotIn(slot,self.j._finished())
            with Ledger(p["resources"]["store_binding"]["path"]) as child:
                self.assertEqual(len(child.objects("derived_batch",p["namespace"])),1);before=child.audit()
            result=self.roles.run_fixture(slot,None,AT)
        with Ledger(p["resources"]["store_binding"]["path"]) as child:self.assertEqual(child.audit(),before)
        self.assertEqual(result["result"]["outcome"],"completed")
        self.assertEqual(self.ledger.get(result["result"]["attempt"])["reserved_calls"],0)

    def test_research_memory_wire_tamper_cannot_create_usage_or_publish_proposal(self):
        self.setup_graph(research=True);slot=self.target("evaluator");code=self.code()
        with self.assertRaises(RuntimeError):self.roles.run_fixture(slot,code,AT,self.crash("after_seal"))
        path=self.roles._directory(slot)/"seal.json";seal=read_json(path);before=self.ledger.audit()
        seal["memory_events"][0]["tokens"]=0;save(path,seal)
        self.assert_code("role_memory_audit_mismatch",lambda:self.roles.commit(self.execution(),AT))
        self.assertEqual(self.ledger.audit(),before)
        self.assertEqual(self.ledger.objects("role_stage_publication","control"),[])
