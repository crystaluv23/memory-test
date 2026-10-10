import copy

from memorylab.contracts import sha256
from memorylab.freeze import execution_config
from memorylab.scheduling import StageJournal, compile_schedule, schedule_config, validate_graph
from test_kernel import Fixture


class StageFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.m0=self.registry.register_root("memory","baseline")
        self.m1=self.registry.register_root("memory","alternative",dict(self.kernel["memory_initial"], **{"credit.type":"outcome_ema"}))
        self.meta=self.registry.register_root("memory","separateMeta")
        self.e=self.registry.register_root("scenario","focus")

    def graph(self, **kwargs):
        if kwargs.get("meta_memory_ref") and "research_corpora" not in kwargs:
            kwargs["research_corpora"]={role:sha256({"history":role}) for role in ("evaluator","proposer")}
        return compile_schedule(self.registry,[self.m0,self.m1],[self.e],[11],sha256({"corpus":"frozen"}),**kwargs)


class StageGraphTests(StageFixture):
    def test_independent_learning_counts_all_stages_and_keeps_target_cutoff(self):
        g=self.graph(); validate_graph(g)
        roles=g["budget_preview"]["by_role"]
        # Each arm: one learning, two interference, target, counterexample.
        self.assertEqual(roles["solver"]["session_invocations_max"],10)
        self.assertEqual(roles["extractor"]["session_invocations_max"],6)
        self.assertEqual(roles["oracle"]["session_invocations_max"],2)
        self.assertEqual(g["budget_preview"]["session_invocations_max"],18)
        self.assertEqual(g["budget_preview"]["tokens_max"],450000)
        for arm in (0,1):
            targets=[n for n in g["slots"] if n["role"]=="solver" and n["namespace"]=="task/s0-r0-a"+str(arm) and
                     n["bindings"]["episode"]["role"] in ("target","counterexample")]
            self.assertEqual(len(targets),2)
            self.assertEqual(targets[0]["bindings"]["corpus_receipt"],targets[1]["bindings"]["corpus_receipt"])
            self.assertNotIn(targets[0]["id"]+"-grade",targets[1]["depends_on"])
        self.assertEqual(g["live_model_calls"],0);self.assertFalse(g["execution_admitted"])

    def test_shared_corpus_acquired_once_and_never_from_competing_arm(self):
        g=self.graph(mode="component_comparison")
        roles=g["budget_preview"]["by_role"]
        self.assertEqual(roles["acquisition"]["session_invocations_max"],3)
        self.assertEqual(roles["solver"]["session_invocations_max"],4)
        self.assertEqual(roles["extractor"]["session_invocations_max"],3)
        targets=[n for n in g["slots"] if n["role"]=="solver"]
        self.assertEqual({n["bindings"]["corpus_receipt"] for n in targets},{"s0-r0-acq-corpus"})
        self.assertTrue(all(n["bindings"]["memory"] is None for n in g["slots"] if n["role"]=="acquisition"))

    def test_derived_maxima_follow_registered_mechanism_not_free_zero_placeholder(self):
        m=self.registry.register_root("memory","derived",dict(self.kernel["memory_initial"],**{
            "consolidation.type":"single_index","reflection.trigger":"feedback"}))
        g=compile_schedule(self.registry,[m],[self.e],[11],sha256({}))
        roles=g["budget_preview"]["by_role"]
        self.assertEqual(roles["consolidation"]["session_invocations_max"],4)
        self.assertEqual(roles["reflector"]["session_invocations_max"],4)
        self.assertEqual(roles["extractor"]["session_invocations_max"],3)
        self.assertTrue(all(n["condition"]=="eligible_feedback" for n in g["slots"] if n["role"]=="reflector"))

    def test_research_meta_separate_and_shadow_identical_history_both_objectives(self):
        g=self.graph(meta_memory_ref=self.meta,shadow_memory_ref=self.m1)
        self.assertEqual(g["budget_preview"]["session_invocations_max"],28)
        for role in ("evaluator","proposer"):
            shadow=[n for n in g["slots"] if n["role"]=="shadow" and n["bindings"]["research_role"]==role]
            self.assertEqual(len(shadow),2)
            self.assertEqual(shadow[0]["bindings"]["research_view_receipt"],shadow[1]["bindings"]["research_view_receipt"])
            self.assertNotEqual(shadow[0]["store_scope"],shadow[1]["store_scope"])
            self.assertEqual(shadow[0]["namespace"],"research/"+role)
            proposal=next(n for n in g["slots"] if n["id"]==role+"-proposal")
            self.assertEqual(proposal["bindings"]["memory"],self.meta)
        self.assert_code("shadow_requires_meta_memory",lambda:self.graph(shadow_memory_ref=self.m1))
        self.assert_code("shadow_candidate_is_current",lambda:self.graph(meta_memory_ref=self.meta,shadow_memory_ref=self.meta))

    def test_confirmation_cost_over_guard_preserves_entire_denominator_and_batches(self):
        g=compile_schedule(self.registry,[self.m0,self.m1],[self.e],list(range(7)),sha256({}),meta_memory_ref=self.meta,
            research_corpora={role:sha256({"history":role}) for role in ("evaluator","proposer")})
        self.assertEqual(g["budget_preview"]["session_invocations_max"],132)
        self.assertEqual(g["budget_preview"]["tokens_max"],3250000)
        self.assertFalse(g["budget_preview"]["fits_round_guard"])
        self.assertEqual(sum(len(b["slots"]) for b in g["budget_preview"]["batches"]),len(g["slots"]))
        self.assertTrue(all(b["seconds_max"]<=1800 for b in g["budget_preview"]["batches"]))
        self.assertEqual(len([n for n in g["slots"] if n["role"]=="solver"]),70)
        self.assertIsNone(g["budget_preview"]["provider_requests_max"])

    def test_resource_identity_and_unknown_dimensions_fail_closed(self):
        for kwargs in ({"mode":"mixed"},{"seeds":[]}):
            if "seeds" in kwargs:
                self.assert_code("invalid_schedule_seeds",lambda:compile_schedule(self.registry,[self.m0],[self.e],[],sha256({})))
            else:self.assert_code("invalid_learning_mode",lambda:self.graph(**kwargs))
        g=self.graph();g["slots"][0]["budget"]["input_tokens"]=1
        self.assert_code("stage_graph_drift",lambda:validate_graph(g))
        cfg=schedule_config();cfg["roles"]["solver"]["input_tokens"]=120001
        self.assert_code("role_budget_exceeds_execution_guard",lambda:self.graph(config=cfg))
        cfg=schedule_config();cfg["roles"]["rogue"]={}
        self.assert_code("invalid_role_registry",lambda:self.graph(config=cfg))

    def test_rehashed_unknown_role_and_illegal_namespace_are_not_authorized(self):
        for key,value,code in (("role","hidden_free_model","unregistered_stage"),
                               ("namespace","control","stage_role_namespace_mismatch")):
            g=self.graph();node=next(n for n in g["slots"] if n["role"]=="solver")
            node[key]=value;g["sha256"]=sha256({k:v for k,v in g.items() if k!="sha256"})
            self.assert_code(code,lambda:validate_graph(g))

    def test_research_corpus_must_be_explicit_and_task_corpus_is_not_substituted(self):
        self.assert_code("expected_object",lambda:self.graph(meta_memory_ref=self.meta,research_corpora=None))
        g=self.graph(meta_memory_ref=self.meta)
        for role in ("evaluator","proposer"):
            corpus=next(n for n in g["slots"] if n["id"]==role+"-history-corpus")
            self.assertEqual(corpus["bindings"]["initial_corpus_sha256"],g["research_corpora"][role])
            self.assertNotEqual(corpus["bindings"]["initial_corpus_sha256"],g["initial_corpus_sha256"])
            shadow=g["shadow_stages"]
            self.assertEqual(shadow,"not_nominated_no_meta_upgrade")


class StageJournalTests(StageFixture):
    def setUp(self):
        super().setUp()
        self.g=self.graph();self.j=StageJournal(self.ledger,self.g)
        self.first_solver=next(n for n in self.g["slots"] if n["role"]=="solver")

    def satisfy_initial_disabled_and_rebuild(self):
        for i,n in enumerate(self.g["slots"]):
            if n["id"]==self.first_solver["id"]:break
            if n["condition"]=="disabled":self.j.close_unrun("disabled"+str(i),n["id"])
            else:
                a=self.j.begin("controlStart"+str(i),n["id"],sha256({"slot":n["id"]}))["result"]["attempt"]
                self.j.complete("controlEnd"+str(i),a,"completed",dict(session_invocations=0,input_tokens=0,output_tokens=0),[],{})

    def start(self):
        self.satisfy_initial_disabled_and_rebuild()
        return self.j.begin("solverStart",self.first_solver["id"],sha256({"input":"exact"}))["result"]["attempt"]

    def test_dependency_and_disabled_skip_authority(self):
        self.assert_code("stage_dependencies_unavailable",lambda:self.j.begin("early",self.first_solver["id"],sha256({})))
        self.assert_code("stage_cannot_be_arbitrarily_skipped",lambda:self.j.close_unrun("skip",self.first_solver["id"]))
        disabled=next(n for n in self.g["slots"] if n["condition"]=="disabled")
        self.assert_code("disabled_stage_cannot_start",lambda:self.j.begin("disabledStart",disabled["id"],sha256({})))

    def test_one_round_reservation_unknown_recovery_no_rerun_all_pending_retained(self):
        a=self.start()
        self.assertEqual(self.j.balance()["charged_calls"],1)
        self.assertEqual(self.j.balance()["charged_tokens"],30000)
        self.assert_code("started_stage_requires_recovery",lambda:self.j.begin("again",self.first_solver["id"],sha256({"input":"exact"})))
        self.j.recover("recover",a)
        self.assertTrue(self.j.balance()["cost_unknown"])
        self.assertEqual(self.j.balance()["charged_tokens"],30000)
        cached=self.j.begin("cached",self.first_solver["id"],sha256({"input":"exact"}))["result"]
        self.assertEqual(cached["status"],"stage_cache_hit");self.assertFalse(cached["independent_sample"])
        self.assert_code("stage_input_drift",lambda:self.j.begin("changed",self.first_solver["id"],sha256({"different":True})))
        report=self.j.report()
        self.assertEqual(report["planned"],len(self.g["slots"]))
        self.assertEqual(next(r for r in report["rows"] if r["id"]==self.first_solver["id"])["status"],"interrupted")
        self.assertTrue(any(r["status"]=="pending" for r in report["rows"]))
        self.assertEqual(report["live_model_calls"],0)

    def test_artifacts_and_receipt_atomic_faults_and_precise_usage_settlement(self):
        a=self.start();before=self.ledger.head()
        usage=dict(session_invocations=1,input_tokens=100,output_tokens=10)
        for phase in ("artifacts","receipt"):
            def fault(p):
                if p==phase:raise RuntimeError("crash")
            with self.assertRaises(RuntimeError):self.j.complete("finish",a,"completed",usage,[{"fixture":"raw"}],{},fault)
            self.assertEqual(self.ledger.head(),before)
            self.assertEqual(self.ledger.objects("stage_artifact","control")[-1][1]["artifacts"],{})
            self.assertIsNone(self.ledger.receipt("finish"))
        r=self.j.complete("finish",a,"completed",usage,[{"fixture":"raw"}],{})
        self.assertTrue(r["result"]["budget_qualified"])
        self.assertFalse(r["result"]["promotion_eligible"])
        self.assertEqual(self.j.balance()["charged_tokens"],110)
        self.assertEqual(self.j.complete("finish",a,"completed",usage,[{"fixture":"raw"}],{}),r)
        self.ledger.audit()

    def test_failed_task_still_grades_and_extracts_actual_failure_but_failed_corpus_blocks(self):
        a=self.start()
        self.j.complete("fail",a,"failed",dict(session_invocations=1,input_tokens=3,output_tokens=4),[],{})
        grade=self.first_solver["id"]+"-grade"
        grade_attempt=self.j.begin("gradeFailure",grade,sha256({"failed":True}))["result"]["attempt"]
        self.j.complete("gradeFailureEnd",grade_attempt,"completed",dict(session_invocations=0,input_tokens=0,output_tokens=0),[],{"full_success":False})
        extract=self.first_solver["id"]+"-extract"
        extract_attempt=self.j.begin("extractFailure",extract,sha256({"public":"actual failure"}))["result"]["attempt"]
        self.j.complete("extractFailureEnd",extract_attempt,"failed",dict(session_invocations=1,input_tokens=4,output_tokens=2),[],{})
        dependent=next(n for n in self.g["slots"] if extract in n["depends_on"] and n["role"]=="rebuild")
        r=self.j.close_unrun("blockedRebuild",dependent["id"])
        self.assertEqual(r["result"]["outcome"],"blocked")
        self.assertFalse(r["result"]["promotion_eligible"])
        self.assertEqual(self.j.report()["planned"],len(self.g["slots"]))

    def test_guard_survives_new_batch_and_overage_cannot_qualify(self):
        # The next unrelated arm has no dependence on this failed arm. A batch
        # boundary must not give it a fresh round allowance.
        ex=execution_config();ex["round_tokens"]=30000;ex["round_calls"]=1
        self.g=self.graph(execution=ex);self.j=StageJournal(self.ledger,self.g)
        self.first_solver=next(n for n in self.g["slots"] if n["role"]=="solver")
        a=self.start();self.j.recover("recoverBudget",a)
        other=next(n for n in self.g["slots"] if n["role"]=="oracle")
        self.assert_code("round_budget_exhausted",lambda:self.j.begin("nextBatch",other["id"],sha256({})))
        self.assertEqual(self.j.balance()["tokens_remaining"],0)

    def test_live_calls_rejected_even_fully_filled_self_report(self):
        self.assert_code("live_stage_not_accepted",lambda:self.j.begin("live",self.first_solver["id"],sha256({}),mode="formal_comparison"))
        self.assertEqual(self.ledger.objects("stage_attempt","control"),[])

    def test_unknown_or_over_budget_success_cannot_release_downstream_dependency(self):
        a=self.start()
        r=self.j.complete("overBudget",a,"completed",dict(session_invocations=1,input_tokens=24001,output_tokens=0),[],{})
        self.assertFalse(r["result"]["budget_qualified"])
        self.assertEqual(r["result"]["outcome"],"failed")
        self.assertEqual(self.j.balance()["charged_tokens"],24001)
        grade=self.j.begin("gradeOver",self.first_solver["id"]+"-grade",sha256({}))["result"]
        self.assertEqual(grade["status"],"stage_started")
