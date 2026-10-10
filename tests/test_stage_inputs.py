import copy
import json
from pathlib import Path
from unittest.mock import patch

from memorylab.contracts import canonical,sha256
from memorylab.derived import DerivedStage
from memorylab.ledger import Ledger
from memorylab.runtime import RuntimeBuilder,UsageStore
from memorylab.scheduling import StageJournal,compile_schedule
from memorylab.stage_inputs import StageInputs
from memorylab.stores import CorpusStore
from test_kernel import AT,Fixture


ZERO=dict(session_invocations=0,input_tokens=0,output_tokens=0)


class StageInputTests(Fixture):
    def setUp(self):
        super().setUp();self.store=CorpusStore(self.cards)
        self.m=self.registry.register_root("memory","taskMemory")
        self.alt=self.registry.register_root("memory","alternative",dict(self.kernel["memory_initial"],**{"credit.type":"outcome_ema"}))
        self.meta=self.registry.register_root("memory","researchMemory")
        self.e=self.registry.register_root("scenario","focus")
        self.initial=self.store.capture("initial","task/checkpoint",self.ledger.head())
        self.initial_hash=self.store.resolve(self.initial)["payload"]["corpus"]["sha256"]

    def setup_graph(self,mode="independent_learning",research=False,shadow=False,memory=None,seeds=None):
        snapshots={};hashes={}
        if research:
            for role in ("evaluator","proposer"):
                snapshots[role]=self.store.capture("initial-"+role,"research/"+role,self.ledger.head())
                hashes[role]=self.store.resolve(snapshots[role])["payload"]["corpus"]["sha256"]
        self.g=compile_schedule(self.registry,memory or [self.m,self.alt],[self.e],seeds or [11],self.initial_hash,mode=mode,
            meta_memory_ref=self.meta if research else None,shadow_memory_ref=self.alt if shadow else None,
            research_corpora=hashes if research else None)
        self.j=StageJournal(self.ledger,self.g)
        self.inputs=StageInputs(self.cards,self.registry,self.j,self.initial,Path(self.tmp.name)/"scopes",snapshots)
        self.nodes={n["id"]:n for n in self.g["slots"]};self.prepared={};self.count=0

    def finish(self,node,override=None,outcome="completed"):
        """Typed production APIs, explicit controller fixtures, zero models."""
        slot=node["id"];self.count+=1
        ref=self.inputs.prepare(slot);value=self.inputs.resolve(ref);p=value["payload"];self.prepared[slot]=value
        started=self.j.begin_bound("start-"+slot,ref,self.inputs)
        if started["result"]["status"]=="stage_finished":return started
        attempt=started["result"]["attempt"];resources=p["resources"];ns=node["namespace"];role=node["role"]
        artifacts={}
        # Shadow objects live in the actual fork DB; stage input/finish receipts
        # remain in the main controller journal.
        if node["store_scope"]=="main":
            artifacts=self.outputs(node,p)
        else:
            from memorylab.cards import CanonicalCards,EvidenceStore
            from memorylab.registry import Registry
            with Ledger(resources["store_binding"]["path"]) as child:
                cards=CanonicalCards(EvidenceStore(child,self.kernel));registry=Registry(child,self.kernel)
                if role in ("consolidation","reflector"):
                    artifacts=self.derive(cards,registry,p)
                elif role=="rebuild":
                    built=RuntimeBuilder(cards,registry).build(ns,p["cutoff"],resources["memory"],resources["derived_refs"])
                    artifacts=dict(snapshot=resources["snapshot"],cutoff=p["cutoff"],projection=built["projection_ref"],derived_refs=resources["derived_refs"])
                elif role=="shadow":artifacts={"fixture_proposal":p["packet"]["payload"]["context"]["parent"]}
        if override is not None:artifacts=override(artifacts,p,attempt)
        return self.j.complete("finish-"+slot,attempt,outcome,ZERO,[{"explicit_fixture":True}],artifacts)

    def derive(self,cards,registry,p):
        role=p["role"];input_=p["packet"]["payload"]["context"]["derived_input"]
        stage=DerivedStage(cards,registry)
        result=stage.accept("derived-"+p["slot"],input_,canonical({"indices" if role=="consolidation" else "annotations":[]}).decode(),
            dict(mode="scripted_acceptance",input_sha256=input_["sha256"],model="explicit-offline-fixture",model_calls=0,input_tokens=None,output_tokens=None))
        return {"batch":result["result"]["batch"]}

    def outputs(self,node,p):
        ns=node["namespace"];role=node["role"];slot=node["id"];r=p["resources"]
        if role in ("solver","acquisition","oracle","evaluator","proposer"):
            source=self.evidence.register("source-"+slot,ns,"agent_output",slot,AT,"Explicit infrastructure fixture output; no task gain claimed.")
            return {"observable_sources":[source]}
        if role in ("grade","proposal_validate"):
            producing=node["bindings"].get("solver_receipt") or node["bindings"]["proposal_receipt"]
            source=self.evidence.register("public-"+slot,self.nodes[producing]["namespace"],"visible_outcome" if role=="grade" else "explicit_feedback",
                producing,AT,json.dumps({"full_success":False,"category":"infrastructure_fixture","code":"no_live_quality_claim"}))
            return {"observable_sources":[source],"private_marker":"hidden verifier fixture must stay private"}
        if role=="extractor":
            if r["source_refs"]:
                card=self.raw_card(r["source_refs"],id="lesson"+str(self.count),brief="A bounded infrastructure lesson",outcome="unknown")
                result=self.cards.extract("canonical-"+slot,{"cards":[card]},ns,p["cutoff"],AT)
                self.assertEqual(result["result"]["status"],"extracted",result)
            return {"snapshot":self.store.capture("snapshot-"+slot,ns,self.ledger.head())}
        if role=="corpus":return {"snapshot":r["snapshot"]}
        if role in ("consolidation","reflector"):return self.derive(self.cards,self.registry,p)
        if role=="rebuild":
            built=RuntimeBuilder(self.cards,self.registry).build(ns,p["cutoff"],r["memory"],r["derived_refs"])
            return dict(snapshot=r["snapshot"],cutoff=p["cutoff"],projection=built["projection_ref"],derived_refs=r["derived_refs"])
        if role=="research_view":
            source=self.evidence.register("view-"+slot,ns,"agent_output",slot,AT,"Public bounded research history: explicit fixture, all failures retained.")
            return {"observable_sources":[source]}
        return {"fixture_control":True}

    def run_until(self,slot):
        for node in self.g["slots"]:
            if node["id"]==slot:return
            if node["id"] not in self.j._finished():self.finish(node)

    def test_full_independent_graph_consumes_actual_sources_snapshots_runtime_and_keeps_private_grade_out(self):
        self.setup_graph()
        for node in self.g["slots"]:self.finish(node)
        report=self.j.report();self.assertEqual(report["terminal"],report["planned"])
        self.assertEqual(report["live_model_calls"],0);self.assertFalse(report["promotion_eligible"])
        extracts=[v for v in self.prepared.values() if v["payload"]["role"]=="extractor"]
        self.assertEqual(len(extracts),6)
        for v in extracts:
            p=v["payload"];self.assertEqual(len(p["resources"]["source_refs"]),2)
            self.assertNotIn("hidden verifier fixture",canonical(p["packet"]).decode())
            self.assertNotEqual(p["cutoff"],0)
        self.ledger.audit()

    def test_shared_acquisition_copies_same_actual_history_without_cross_arm_source_refs(self):
        self.setup_graph(mode="component_comparison")
        for node in self.g["slots"]:self.finish(node)
        learned=[]
        for arm in (0,1):
            target=next(v["payload"] for v in self.prepared.values() if v["payload"]["role"]=="solver" and
                v["payload"]["namespace"]=="task/s0-r0-a"+str(arm))
            snapshot=self.store.resolve(target["resources"]["snapshot"])
            learned.append(snapshot["portable_sha256"])
            self.assertTrue(all(row["ref"]["namespace"]==target["namespace"] for row in snapshot["payload"]["sources"]))
        self.assertEqual(learned[0],learned[1]);self.assertEqual(self.j.report()["terminal"],self.j.report()["planned"])

    def test_target_and_counterexample_reuse_frozen_learning_input_after_future_feedback_and_usage(self):
        self.setup_graph();targets=[n for n in self.g["slots"] if n["role"]=="solver" and n["namespace"]=="task/s0-r0-a0" and
            n["bindings"]["episode"]["role"] in ("target","counterexample")]
        self.run_until(targets[0]["id"])
        one=self.inputs.prepare(targets[0]["id"]);frozen=self.inputs.resolve(one)
        self.finish(targets[0]);self.finish(self.nodes[targets[0]["id"]+"-grade"])
        usage=UsageStore(self.cards);ns=targets[0]["namespace"]
        projection=self.ledger.get(frozen["payload"]["resources"]["projection"]);card=projection["items"][0]["ref"]
        tool=self.evidence.register("futureTool",ns,"tool_result",targets[0]["id"],AT,"Actual explicit fixture tool failure")
        usage.record("futureOpen",ns,targets[0]["id"],"open",card)
        usage.record("futureLink",ns,targets[0]["id"],"behavior_link",card,evidence_ref=tool)
        self.evidence.register("futureExtra",targets[0]["namespace"],"explicit_feedback","future",AT,"Future target feedback must not be loaded")
        two=self.inputs.prepare(targets[1]["id"]);second=self.inputs.resolve(two)
        self.assertEqual(frozen["payload"]["cutoff"],second["payload"]["cutoff"])
        self.assertEqual(frozen["payload"]["resources"]["projection"],second["payload"]["resources"]["projection"])
        self.assertEqual(self.inputs.resolve(one),frozen)
        self.assertNotIn("Future target feedback",canonical(second["payload"]["packet"]).decode())

    def test_research_and_four_shadow_scopes_use_preproposal_history_and_own_role(self):
        self.setup_graph(research=True,shadow=True)
        for node in self.g["slots"]:self.finish(node)
        for role in ("evaluator","proposer"):
            main=self.prepared[role+"-proposal"]["payload"]
            pair=[self.prepared[role+"-shadow-"+label+"-call"]["payload"] for label in ("current","candidate")]
            self.assertEqual(pair[0]["packet"]["payload"]["role"],role)
            self.assertNotEqual(pair[0]["resources"]["store_binding"]["path"],pair[1]["resources"]["store_binding"]["path"])
            for p in pair:
                context=p["packet"]["payload"]["context"]
                self.assertEqual([v["source"]["text"] for v in context["history"]],[v["source"]["text"] for v in main["packet"]["payload"]["context"]["history"]])
                self.assertEqual(context["parent"],self.e if role=="evaluator" else self.m)
                with Ledger(p["resources"]["store_binding"]["path"]) as ledger:
                    sources=ledger.objects("source","research/"+role)
                    self.assertFalse(any(v["episode"]==role+"-proposal" for _,v in sources))
                    self.assertEqual(ledger.events("research/"+("proposer" if role=="evaluator" else "evaluator")),[])
            self.assertNotEqual(pair[0]["resources"]["source_refs"],main["resources"]["source_refs"])
        self.assertEqual(self.j.report()["terminal"],self.j.report()["planned"])

    def test_rehashed_graph_with_changed_bindings_cannot_gain_authority(self):
        self.setup_graph();changed=copy.deepcopy(self.g)
        node=next(n for n in changed["slots"] if n["role"]=="solver")
        node["bindings"]["visibility"]="include_private_grades"
        changed["sha256"]=sha256({k:v for k,v in changed.items() if k!="sha256"})
        journal=StageJournal(self.ledger,changed)
        self.assert_code("stage_bindings_not_compiled",lambda:StageInputs(self.cards,self.registry,journal,self.initial,Path(self.tmp.name)/"bad"))

    def test_wrong_initial_research_corpus_and_binding_root_drift_are_rejected(self):
        self.setup_graph(research=True)
        self.assert_code("research_stage_corpus_mismatch",lambda:StageInputs(self.cards,self.registry,self.j,self.initial,
            Path(self.tmp.name)/"another",{"evaluator":self.initial,"proposer":self.inputs.research["proposer"]}))
        self.assert_code("immutable_object_conflict",lambda:StageInputs(self.cards,self.registry,self.j,self.initial,
            Path(self.tmp.name)/"another",self.inputs.research))

    def test_disabled_and_empty_eligible_stages_do_not_reserve_model_calls(self):
        m=self.registry.register_root("memory","withDerived",dict(self.kernel["memory_initial"],**{"consolidation.type":"single_index","reflection.trigger":"feedback"}))
        self.setup_graph(memory=[m]);first=self.g["slots"][0]
        ref=self.inputs.prepare(first["id"]);value=self.inputs.resolve(ref)
        self.assertEqual(value["payload"]["action"],"bound_noop")
        a=self.j.begin_bound("emptyEligible",ref,self.inputs)["result"]["attempt"]
        self.assertEqual(self.ledger.get(a)["reserved_calls"],0);self.assertEqual(self.ledger.get(a)["reserved_tokens"],0)
        self.assertEqual(self.j.balance()["charged_calls"],0)

    def test_prepare_crash_rolls_back_import_input_and_receipt_then_replays_exactly(self):
        self.setup_graph();slot=next(n["id"] for n in self.g["slots"] if n["role"]=="rebuild")
        for node in self.g["slots"]:
            if node["id"]==slot:break
            self.finish(node)
        before=self.ledger.audit()
        for phase in ("input","receipt"):
            def crash(name):
                if name==phase:raise RuntimeError("interrupt input publication")
            with self.assertRaises(RuntimeError):self.inputs.prepare(slot,crash)
            self.assertEqual(self.ledger.audit(),before)
        ref=self.inputs.prepare(slot);self.assertEqual(self.inputs.prepare(slot),ref)
        self.assertEqual(self.ledger.get(ref),self.inputs.resolve(ref))

    def test_generic_uncommitted_input_and_legacy_unbound_attempt_cannot_be_dependencies(self):
        self.setup_graph(mode="component_comparison");first=self.g["slots"][0]
        legitimate=self.inputs.prepare(first["id"]);fake=copy.deepcopy(self.inputs.resolve(legitimate));fake["id"]="forged";fake["request_id"]="forged"
        with self.ledger.transaction():ref=self.ledger.put(fake,"control")
        self.assert_code("uncommitted_stage_input",lambda:self.inputs.resolve(ref))
        attempt=self.j.begin("legacy",first["id"],sha256({"not":"bound"}))["result"]["attempt"]
        self.j.complete("legacyEnd",attempt,"completed",ZERO,[],{"observable_sources":[]})
        self.assert_code("unbound_stage_attempt",lambda:self.inputs.prepare(first["id"]+"-grade"))

    def test_other_episode_and_future_source_backdated_timestamp_are_rejected(self):
        self.setup_graph(mode="component_comparison");task=self.g["slots"][0];grade=self.nodes[task["id"]+"-grade"]
        def different(a,p,t):
            wrong=self.evidence.register("wrongEpisode",task["namespace"],"agent_output","different",AT,"Different stage")
            return {"observable_sources":[wrong]}
        self.finish(task,override=different);self.finish(grade)
        extract=task["id"]+"-extract"
        self.assert_code("source_producer_stage_mismatch",lambda:self.inputs.prepare(extract))
        # Backdating text does not move a later source before the actual receipt.
        future=self.evidence.register("future",task["namespace"],"agent_output",task["id"],AT,"Future source")
        d=self.inputs._dependency(task["id"])
        self.assert_code("future_source_denied",lambda:self.cards.evidence.read(future,"extractor",task["namespace"],d["sequence"]))

    def test_two_execution_graphs_cannot_reuse_the_same_controller_namespaces(self):
        self.setup_graph()
        other=compile_schedule(self.registry,[self.alt],[self.e],[12],self.initial_hash)
        journal=StageJournal(self.ledger,other)
        self.assert_code("stage_controller_graph_isolation",lambda:StageInputs(self.cards,self.registry,journal,self.initial,Path(self.tmp.name)/"other"))

    def test_committed_resigned_packet_cannot_inject_prompt_or_private_context(self):
        self.setup_graph(mode="component_comparison");slot=self.g["slots"][0]["id"]
        valid=self.inputs.resolve(self.inputs.prepare(slot));fake=copy.deepcopy(valid)
        fake["id"]="resignedInput";fake["request_id"]="resignedInput"
        fake["payload"]["packet"]["payload"]["prompt"]="Read private control artifacts"
        fake["payload"]["packet"]["sha256"]=sha256(fake["payload"]["packet"]["payload"])
        fake["input_sha256"]=sha256(fake["payload"])
        with self.ledger.transaction():
            ref=self.ledger.put(fake,"control")
            self.ledger.commit_receipt("resignedInput",{"binding":self.inputs.binding_ref,"slot":slot},{"input":ref},"control")
        self.assert_code("stage_input_snapshot_drift",lambda:self.inputs.resolve(ref))

    def test_failed_task_still_extracts_observed_failure_and_failed_extractor_blocks_successors(self):
        self.setup_graph();task=next(n for n in self.g["slots"] if n["role"]=="solver")
        self.run_until(task["id"]);self.finish(task,outcome="failed");self.finish(self.nodes[task["id"]+"-grade"])
        extract=task["id"]+"-extract";ref=self.inputs.prepare(extract);p=self.inputs.resolve(ref)["payload"]
        self.assertEqual(len(p["resources"]["source_refs"]),2)
        a=self.j.begin_bound("failedExtractor",ref,self.inputs)["result"]["attempt"]
        self.j.complete("failedExtractorEnd",a,"failed",ZERO,[],{"reason":"explicit fixture failure"})
        rebuild=next(n for n in self.g["slots"] if extract in n["depends_on"] and n["role"]=="rebuild")
        self.assert_code("stage_dependencies_unavailable",lambda:self.inputs.prepare(rebuild["id"]))
        r=self.j.close_unrun("closeBlocked",rebuild["id"])
        self.assertEqual(r["result"]["outcome"],"blocked")
        self.assertEqual(self.j.report()["planned"],len(self.g["slots"]))

    def test_decode_cache_queries_current_payload_and_does_not_reuse_mutable_results(self):
        with self.ledger.transaction(),self.ledger.cached_reads(100000):
            original=self.ledger.get(self.m);changed=self.ledger.get(self.m);changed["config"]["credit.type"]="altered"
            self.assertEqual(self.ledger.get(self.m),original)
            self.ledger.db.execute("UPDATE objects SET payload=? WHERE kind='memory' AND id='taskMemory'",('{"bad":"alteration"}',))
            self.assert_code("corrupt_ledger_hash",lambda:self.ledger.get(self.m))

    def test_environment_drift_blocks_fresh_cached_disabled_and_resolved_inputs_without_mutation(self):
        self.setup_graph();slot=self.g["slots"][0]["id"];ref=self.inputs.prepare(slot);before=self.ledger.audit()
        for field in ("python","system","native_sandbox","filtered_worker_environment_sha256"):
            changed=copy.deepcopy(self.inputs.binding["environment"])
            if type(changed[field]) is dict:changed[field][next(iter(changed[field]))]="changed-fixture-identity"
            else:changed[field]="0"*64
            with self.subTest(field=field),patch("memorylab.stage_inputs.stage_environment_identity",return_value=changed):
                self.assert_code("stage_environment_drift",lambda:self.inputs.prepare(slot))
                self.assert_code("stage_environment_drift",lambda:self.inputs.resolve(ref))
            self.assertEqual(self.ledger.audit(),before)

    def test_control_and_cold_input_source_drift_fails_before_import_or_new_input(self):
        self.setup_graph();before=self.ledger.audit()
        from memorylab.freeze import implementation_files
        changed=implementation_files();changed["memorylab/stage_inputs.py"]+=b"\n# changed fixture\n"
        with patch("memorylab.stage_inputs.implementation_files",return_value=changed):
            for slot in (self.g["slots"][0]["id"],next(n["id"] for n in self.g["slots"] if n["role"]=="rebuild")):
                self.assert_code("stage_implementation_drift",lambda:self.inputs.prepare(slot))
        self.assertEqual(self.ledger.audit(),before)

    def test_excluded_credentials_do_not_enter_environment_identity_and_allowed_environment_drift_is_rejected(self):
        self.setup_graph();slot=self.g["slots"][0]["id"];ref=self.inputs.prepare(slot)
        with patch.dict("os.environ",{"OPENAI_API_KEY":"explicit-fake-secret","CODEX_CONTROLLER_TOKEN":"explicit-fake-token"}):
            self.assertEqual(self.inputs.prepare(slot),ref)
        self.assertNotIn("explicit-fake",canonical(self.inputs.binding).decode())
        with patch.dict("os.environ",{"LANG":"explicit-fixture-different-locale"}):
            self.assert_code("stage_environment_drift",lambda:self.inputs.prepare(slot))

    def test_mutable_in_memory_binding_cannot_authorize_changed_role_configuration(self):
        self.setup_graph();slot=self.g["slots"][0]["id"];before=self.ledger.audit()
        self.inputs.role["version"]="changed-fixture"
        self.assert_code("stage_binding_drift",lambda:self.inputs.prepare(slot))
        self.assertEqual(self.ledger.audit(),before)

    def test_valid_same_corpus_batch_cannot_replace_the_registered_derived_producer(self):
        self.setup_graph();rebuild=next(n for n in self.g["slots"] if n["role"]=="rebuild")
        self.run_until(rebuild["id"])
        def replacement(art,p,attempt):
            stage=DerivedStage(self.cards,self.registry)
            input_=stage.prepare("reflector",rebuild["namespace"],p["cutoff"],self.m)
            extra=stage.accept("unplannedDerived",input_,json.dumps({"annotations":[]}),
                dict(mode="scripted_acceptance",input_sha256=input_["sha256"],model="explicit-offline-fixture",model_calls=0,input_tokens=None,output_tokens=None))
            derived={"reflector":extra["result"]["batch"]}
            # RuntimeBuilder accepts this structurally valid same-input batch;
            # the stage binder must still reject its unregistered substitution.
            built=RuntimeBuilder(self.cards,self.registry).build(rebuild["namespace"],p["cutoff"],self.m,derived)
            return dict(art,projection=built["projection_ref"],derived_refs=derived)
        self.finish(rebuild,override=replacement)
        solver=next(n for n in self.g["slots"] if n["role"]=="solver")
        self.assert_code("runtime_derived_producer_mismatch",lambda:self.inputs.prepare(solver["id"]))
