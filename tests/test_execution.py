import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from memorylab.cards import CanonicalCards, EvidenceStore
from memorylab.contracts import canonical, sha256
from memorylab.freeze import (execution_config, implementation_files, live_blockers,
                              prepare, verify as verify_bundle)
from memorylab.ledger import Ledger
from memorylab.registry import Registry
from memorylab.runtime import RuntimeBuilder
from memorylab.trials import TrialJournal
from test_kernel import AT, Fixture


class FreezeTests(Fixture):
    def freeze(self):
        m=self.registry.register_root("memory","baseline")
        e=self.registry.register_root("scenario","first")
        return prepare(self.registry,self.cards,[m],e,"task/chain",[19],Path(self.tmp.name)/"bundles")

    def test_self_contained_content_bundle_and_exact_prepare_cache(self):
        first=self.freeze();again=self.freeze()
        self.assertFalse(first["cache_hit"]);self.assertTrue(again["cache_hit"])
        self.assertEqual(first["fingerprint"],again["fingerprint"])
        payload=verify_bundle(first["path"])
        self.assertFalse(payload["kernel_frozen"])
        self.assertEqual(payload["budget_preview"]["live_model_calls"],0)
        self.assertEqual(len(payload["jobs"]),5)
        with Ledger(Path(first["path"])/"inputs.sqlite") as ledger:
            self.assertEqual(ledger.audit()["objects"],3)

    def test_reports_outside_bundle_and_cwd_change_do_not_invalidate_snapshot(self):
        first=self.freeze()
        fakecwd=Path(self.tmp.name)/"changed-cwd";fakecwd.mkdir();(fakecwd/"PROTOCOL.md").write_text("unrelated changed report")
        result=subprocess.run([sys.executable,"-I","-B",str(Path(first["path"])/"bootstrap.py"),"kernel","--db",str(Path(self.tmp.name)/"replayed.sqlite"),"init"],
            cwd=fakecwd,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)["live_model_calls"],0)
        verify_bundle(first["path"])

    def test_bundle_tamper_extra_nested_manifest_and_symlink_rejected(self):
        for name in ("tamper","extra","symlink"):
            first=self.freeze();dest=Path(self.tmp.name)/name;shutil.copytree(first["path"],dest)
            if name=="tamper":(dest/"memorylab/runtime.py").write_text("tampered")
            if name=="extra":
                (dest/"unused").mkdir();(dest/"unused/manifest.json").write_text("hidden extra")
            if name=="symlink":(dest/"link").symlink_to(Path(self.tmp.name))
            with self.assertRaises(ValueError):verify_bundle(dest)

    def test_raw_source_and_usage_are_fingerprint_inputs(self):
        first=self.freeze();self.source("newSource")
        second=self.freeze()
        self.assertNotEqual(first["fingerprint"],second["fingerprint"])

    def test_restored_snapshot_sources_cards_and_versions_resolve(self):
        self.extract()
        first=self.freeze();db=Path(self.tmp.name)/"restored.sqlite";shutil.copyfile(Path(first["path"])/"inputs.sqlite",db)
        with Ledger(db) as ledger:
            cards=CanonicalCards(EvidenceStore(ledger))
            payload=verify_bundle(first["path"])
            self.assertEqual(cards.corpus("task/chain",payload["cutoff"]),payload["corpus"])

    def test_live_canary_cannot_sneak_in_with_boolean_authorization(self):
        config=execution_config()
        self.assertIn("live_not_authorized",live_blockers(config))
        config.update(mode="live_canary",live_execution_authorized=True,model="configured",resolved_model="configured",cli_version="version")
        config["tokenizer"]["live_qualified"]=True
        self.assertIn("live_attestation_validator_not_accepted",live_blockers(config,{"claims":"safe"}))
        m=self.registry.register_root("memory","baseline");e=self.registry.register_root("scenario","first")
        self.assert_code("live_plan_not_implemented",lambda:prepare(self.registry,self.cards,[m],e,"task/chain",[19],Path(self.tmp.name)/"bundles",config))


class AtomicTrialTests(Fixture):
    def setUp(self):
        super().setUp()
        self.memory=self.registry.register_root("memory","baseline")
        self.builder=RuntimeBuilder(self.cards,self.registry)
        self.journal=TrialJournal(self.ledger,self.cards,self.builder,execution_config())
        self.job={"id":"job1","namespace":"task/chain","memory":self.memory,"mode":"scripted_acceptance"}
        self.fingerprint=sha256({"job":self.job,"mode":"scripted_acceptance","fixture":"explicit-candidate"})
        self.raw=[{"event_type":"tool_result","episode":"job1","timestamp":AT,"text":"Actual scripted production request executed."}]
        self.grade={"full_success":True,"behavior_success":True,"status":"completed"}
        self.usage={"model_calls":0,"tokens":0,"simulated_invocations":1}
        self.template={"cards":[{k:v for k,v in self.raw_card([{"ignored":"not-validated-here"}]).items() if k!="evidence_refs"}]}
        self.template["cards"][0]["source_indices"]=[0]
        self.template["cards"][0]["id"]="learned"

    def begin(self):
        return self.journal.begin("startTrial","plan1",self.job,self.fingerprint)["result"]["attempt"]

    def complete(self,attempt,fault=None):
        return self.journal.complete("completeTrial",attempt,self.raw,self.grade,self.usage,AT,self.template,fault=fault)

    def test_full_commit_is_one_receipt_and_can_recompute_payoff(self):
        attempt=self.begin();result=self.complete(attempt)
        self.assertTrue(result["result"]["task_success"])
        self.assertFalse(result["result"]["promotion_eligible"])
        self.assertFalse(result["result"]["gain_evidence"])
        self.assertEqual(len(self.cards.active("task/chain",self.ledger.head())),1)
        payoff=self.ledger.get(result["result"]["payoff"])
        self.assertTrue(payoff["trial_success"])
        self.assertEqual(self.journal.balance("plan1")["charged_calls"],0)
        self.ledger.audit()

    def test_all_seven_crash_boundaries_rollback_trial_and_memory(self):
        attempt=self.begin();head=self.ledger.head()
        for phase in ("raw","grade","sources","memory","projection","payoff","receipt"):
            with self.subTest(phase=phase):
                def crash(name):
                    if name==phase:raise RuntimeError("crash at "+phase)
                with self.assertRaises(RuntimeError):self.complete(attempt,crash)
                self.assertEqual(self.ledger.head(),head)
                self.assertEqual(self.ledger.objects("card","task/chain"),[])
                self.assertEqual(self.ledger.objects("source","task/chain"),[])
                self.assertEqual(self.ledger.objects("grade","control"),[])
                self.assertEqual(self.ledger.objects("payoff","control"),[])
                self.assertIsNone(self.ledger.receipt("completeTrial"))
        self.assertTrue(self.complete(attempt)["result"]["task_success"])

    def test_exact_cache_has_no_new_attempt_or_call_and_not_independent_sample(self):
        attempt=self.begin();result=self.complete(attempt)
        cached=self.journal.begin("cacheTrial","plan1",self.job,self.fingerprint)
        self.assertEqual(cached["result"]["status"],"cache_hit")
        self.assertEqual(cached["result"]["new_model_calls"],0)
        self.assertEqual(len(self.ledger.objects("attempt","control")),1)
        report=self.journal.report([self.job],"plan1")
        self.assertEqual(report["planned"],1);self.assertEqual(report["completed"],1)

    def test_changed_content_cannot_overwrite_same_committed_trial(self):
        attempt=self.begin();self.complete(attempt)
        changed=copy.deepcopy(self.template);changed["cards"][0]["brief"]="Different lesson"
        self.assert_code("idempotency_conflict",lambda:self.journal.complete("completeTrial",attempt,self.raw,self.grade,self.usage,AT,changed))

    def test_pending_failed_interrupted_and_missing_stay_in_denominator(self):
        attempt=self.begin()
        report=self.journal.report([self.job,dict(self.job,id="job2")],"plan1")
        self.assertEqual(report["planned"],2);self.assertEqual(report["completed"],0)
        self.journal.recover_interrupted("recoverTrial",attempt,AT)
        report=self.journal.report([self.job,dict(self.job,id="job2")],"plan1")
        self.assertEqual(report["planned"],2);self.assertEqual(report["completed"],1);self.assertEqual(report["successful"],0)
        self.assertEqual(report["rows"][0]["status"],"interrupted")
        self.assertEqual(report["rows"][1]["status"],"pending")

    def test_started_trial_requires_recovery_and_no_implicit_retry(self):
        self.begin()
        self.assert_code("started_trial_requires_recovery",lambda:self.journal.begin("startAgain","plan1",self.job,self.fingerprint))
        self.assert_code("unplanned_job_retry",lambda:self.journal.begin("changed","plan1",self.job,"a"*64))

    def test_hidden_grader_data_does_not_become_observable_source(self):
        attempt=self.begin();self.grade["hidden_inputs"]={"answer":"secret-value"};self.complete(attempt)
        sources=self.cards.evidence.visible("extractor","task/chain",self.ledger.head())
        self.assertNotIn("secret-value"," ".join(v["text"] for _,v in sources))

    def test_invalid_template_source_indices_roll_back_all_artifacts(self):
        attempt=self.begin();self.template["cards"][0]["source_indices"]=[True]
        self.assert_code("invalid_source_indices",lambda:self.complete(attempt))
        self.assertEqual(self.ledger.objects("source","task/chain"),[])
        self.assertEqual(self.ledger.objects("payoff","control"),[])

    def test_memory_batch_rejection_is_retained_as_failed_trial(self):
        attempt=self.begin();self.template["cards"][0]["brief"]=""
        result=self.complete(attempt)["result"]
        self.assertEqual(result["extraction"]["result"]["status"],"rejected")
        self.assertFalse(result["task_success"])
        self.assertEqual(self.ledger.objects("card","task/chain"),[])
        self.assertEqual(len(self.ledger.objects("payoff","control")),1)
