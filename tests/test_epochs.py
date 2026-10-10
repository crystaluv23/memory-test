import copy

from memorylab.epochs import ControlledEpochs, epoch_config
from memorylab.statistics import cp_lower, cp_upper, paired_decision
from test_kernel import Fixture


def pairs(prefix="unit",kind="better",n=10):
    return [{"unit":prefix+str(i),"baseline_success":kind!="better","candidate_success":kind!="worse",
        "baseline_cost":1.0,"candidate_cost":0.7,"severe_violation":False} for i in range(n)]


class StatisticalTests(Fixture):
    def test_exact_superiority_harm_and_screening_do_not_confuse(self):
        config=epoch_config()
        better=paired_decision(pairs(),config)
        self.assertEqual(better["verdict"],"better")
        self.assertAlmostEqual(better["p_better"],1/1024)
        self.assertEqual(paired_decision(pairs(kind="worse"),config)["verdict"],"worse")
        self.assertEqual(paired_decision(pairs(),config,"screening")["verdict"],"confirmation_required")

    def test_nonsignificance_is_not_noninferiority_even_with_cost_saving(self):
        result=paired_decision(pairs(kind="tie",n=14),epoch_config())
        self.assertEqual(result["verdict"],"indistinguishable")
        self.assertLess(result["difference_lower_bound"],-.1)
        self.assertAlmostEqual(result["cost_saving_fraction"],.3)

    def test_missing_cost_short_sample_severe_and_duplicates(self):
        config=epoch_config()
        rows=pairs();rows[0]["candidate_cost"]=None
        self.assertEqual(paired_decision(rows,config)["code"],"unknown_cost")
        self.assertEqual(paired_decision(pairs(n=3),config)["code"],"too_few_independent_units")
        rows=pairs();rows[0]["severe_violation"]=True
        self.assertEqual(paired_decision(rows,config)["verdict"],"unusable")
        self.assert_code("duplicate_independent_unit",lambda:paired_decision(pairs()+[pairs()[0]],config))
        self.assert_code("undeclared_extra_confirmation",lambda:paired_decision(pairs(n=15),config))

    def test_binomial_bounds_known_endpoints(self):
        self.assertEqual(cp_lower(0,14,.01),0)
        self.assertEqual(cp_upper(14,14,.01),1)
        self.assertAlmostEqual(cp_lower(14,14,.01),.01**(1/14))
        self.assertAlmostEqual(cp_upper(0,14,.01),1-.01**(1/14))


class EpochHarness(Fixture):
    def setUp(self):
        super().setUp()
        self.ctl=ControlledEpochs(self.ledger,self.registry,self.cards)
        self.m=self.registry.register_root("memory","baseline")
        self.e=self.registry.register_root("scenario","first")
        self.state_ref=self.ctl.initialize("init","experiment",[self.m],self.e)["result"]["state"]
        self.counter=0

    def state(self):return self.ledger.get(self.state_ref)

    def act(self,action,args):
        self.counter+=1
        result=self.ctl.transition("transition"+str(self.counter),self.state_ref,action,args)
        self.state_ref=result["result"]["state"]
        return result["result"]["result"]

    def proof(self,operation,data):
        self.counter+=1
        return self.ctl.fixture("fixture"+str(self.counter),operation,data)

    def propose(self,field="organizer.type",value="fixed_tree"):
        state=self.state()
        parent=self.ctl.parent(state) if state["phase"]=="memory" else state["focus"]
        proposal=self.proposal(parent,field,value)
        return self.act("propose",{"proposal":proposal,"cutoff":self.ledger.head()})

    def memory(self,candidate,kind="better",prefix="paired",**changes):
        data={"pairs":pairs(prefix,kind),"regression_safe":True,"budget_qualified":True,"completed":True,"infrastructure_failure":False}
        data.update(changes)
        plan=self.plan("memory_confirmation",candidate,{"task":[r["unit"] for r in data["pairs"]]})
        data["plan"]=plan
        proof=self.proof("memory_confirmation",data)
        return self.act("memory_confirmation",{"candidate":candidate,"proof":proof,"plan":plan})

    def plan(self,operation,candidate,units,history=None):
        baseline=self.registry.resolve(candidate)["parent"] if operation=="memory_confirmation" else self.state()["meta_champion"]
        return self.act("plan_confirmation",{"operation":operation,"candidate":candidate,"baseline":baseline,
            "history_sha256":history,"units":units})["plan"]

    def mastery(self,prefix):
        state=self.state()
        proof=self.proof("mastery_confirmation",{"cohort":state["elite"],"units":[prefix+str(i) for i in range(7)],
            "successes":{r["id"]:[True]*7 for r in state["elite"]},"budget_qualified":True})
        return self.act("mastery_confirmation",{"proof":proof})

    def to_evaluator(self):
        candidate=self.propose()["candidate"]
        self.memory(candidate)
        self.mastery("masteryA");self.mastery("masteryB")
        return candidate

    def reward(self,rate=.5,**changes):
        value={"validity":True,"oracle_success":1.0,"elite_rates":{r["id"]:rate for r in self.state()["elite"]},
            "disagreement":0.0,"coverage":["feedback_retention"],"learning_progress":0.0,"cost":1.0,"budget_qualified":True}
        value.update(changes)
        return self.proof("challenge_confirmation",value)


class EpochTests(EpochHarness):
    def test_controlled_complete_e0_memory_mastery_e1_memory_sequence(self):
        first=self.to_evaluator()
        state=self.state()
        self.assertEqual(state["phase"],"evaluator")
        self.assertEqual(state["meta_champion"],self.m)
        challenge=self.propose("distractor_count",4)["candidate"]
        result=self.act("challenge_confirmation",{"candidate":challenge,"proof":self.reward()})
        self.assertEqual(result["status"],"frontier")
        self.assertEqual(self.state()["phase"],"memory")
        self.assertEqual(self.state()["focus"],challenge)
        field="consolidation.type"
        second=self.propose(field,"single_index")["candidate"]
        result=self.memory(second,prefix="newFocus")
        self.assertEqual(result["status"],"simulated_promoted")
        self.assertNotEqual(self.state()["task_champion"],first)
        self.assertEqual(self.state()["meta_champion"],self.m)
        self.assertTrue(any(t["ref"]==self.e and t["status"]=="regression" for t in self.state()["challenges"]))
        self.ledger.audit()

    def test_opposite_side_and_stale_state_rejected_without_partial_changes(self):
        original=self.state_ref
        wrong=self.proposal(self.e,"distractor_count",4)
        self.assert_code("opposite_side_frozen",lambda:self.act("propose",{"proposal":wrong,"cutoff":self.ledger.head()}))
        self.assertEqual(self.state_ref,original)
        self.propose()
        self.assert_code("stale_epoch_snapshot",lambda:self.ctl.transition("stale",original,"exhaustion",{"reason":"proposal_budget_exhausted"}))

    def test_noop_reject_budget_and_exhaustion_are_not_plateau(self):
        for _ in range(3):self.assertEqual(self.propose("organizer.type","flat")["status"],"noop")
        self.assertEqual(self.state()["attempts"],[])
        self.assertEqual(self.act("exhaustion",{"reason":"proposal_budget_exhausted"})["status"],"proposal_budget_exhausted")
        self.assertEqual(self.state()["phase"],"memory")
        self.assert_code("proposal_budget_exhausted",lambda:self.propose())

    def test_three_completed_nonimprovements_trigger_plateau(self):
        for i,(field,value) in enumerate((("organizer.type","fixed_tree"),("loader.global_briefs",2),("consolidation.type","single_index"))):
            candidate=self.propose(field,value)["candidate"]
            self.memory(candidate,"tie","attempt"+str(i))
        self.assertEqual(self.state()["phase"],"evaluator")
        self.assertEqual(self.state()["switch_reason"],"plateau")
        self.assertEqual(self.state()["challenges"][0]["status"],"archive")

    def test_infrastructure_failure_unknown_cost_and_regression_do_not_promote(self):
        candidate=self.propose()["candidate"]
        result=self.memory(candidate,regression_safe=False)
        self.assertEqual(result["status"],"confirmation_rejected")
        self.assertEqual(self.state()["task_champion"],self.m)
        second=self.propose("loader.global_briefs",2)["candidate"]
        self.memory(second,prefix="infra",infrastructure_failure=True)
        self.assertFalse(self.state()["attempts"][-1]["valid"])

    def test_mastery_two_cached_or_training_units_cannot_count_twice(self):
        candidate=self.propose()["candidate"];self.memory(candidate)
        self.mastery("one")
        self.assert_code("reused_mastery_units",lambda:self.mastery("one"))
        self.assert_code("reused_mastery_units",lambda:self.mastery("paired"))
        self.assertEqual(self.state()["phase"],"memory")
        self.mastery("two");self.assertEqual(self.state()["phase"],"evaluator")

    def test_frontier_upper_boundary_is_regression_not_frontier_and_too_hard_rejected(self):
        self.to_evaluator()
        candidate=self.propose("distractor_count",4)["candidate"]
        result=self.act("challenge_confirmation",{"candidate":candidate,"proof":self.reward(.8)})
        self.assertEqual(result["status"],"regression")
        self.assertEqual(self.state()["phase"],"evaluator")
        candidate=self.propose("transfer_distance","far")["candidate"]
        result=self.act("challenge_confirmation",{"candidate":candidate,"proof":self.reward(.1)})
        self.assertEqual(result["code"],"stepping_stone_required")

    def test_unsolvable_and_duplicate_challenges_do_not_open_frontier(self):
        self.to_evaluator()
        candidate=self.propose("distractor_count",4)["candidate"]
        result=self.act("challenge_confirmation",{"candidate":candidate,"proof":self.reward(oracle_success=.5)})
        self.assertEqual(result["code"],"invalid_or_unsolvable")
        candidate=self.propose("distractor_count",4)["candidate"]
        result=self.act("challenge_confirmation",{"candidate":candidate,"proof":self.reward()})
        self.assertEqual(result["code"],"duplicate_challenge")

    def test_pending_old_context_candidates_are_archived_on_epoch_switch(self):
        candidate=self.propose()["candidate"];self.memory(candidate)
        parent=self.ctl.parent(self.state());spec=self.registry.resolve(parent)
        pending=self.propose("loader.global_briefs",2)["candidate"]
        self.mastery("firstRound");self.mastery("secondRound")
        self.assertEqual(self.state()["pending"],[])
        self.assertTrue(any(a["ref"]==pending and a["reason"]=="untested_epoch_closed" for a in self.state()["archive"]))

    def test_shadow_same_history_rejects_then_confirms_two_research_objectives(self):
        candidate=self.to_evaluator()
        history="a"*64
        plan=self.plan("shadow_confirmation",candidate,{"evaluator":[r["unit"] for r in pairs("eval")],
            "proposer":[r["unit"] for r in pairs("proposer")]},history)
        proof=self.proof("shadow_confirmation",{"plan":plan,"history_sha256":history,"evaluator_pairs":pairs("eval","tie"),
            "proposer_pairs":pairs("proposer"),"budget_qualified":True})
        result=self.act("shadow_confirmation",{"candidate":candidate,"proof":proof,"history_sha256":history,"plan":plan})
        self.assertEqual(result["status"],"shadow_rejected")
        self.assertEqual(self.state()["meta_champion"],self.m)
        plan=self.plan("shadow_confirmation",candidate,{"evaluator":[r["unit"] for r in pairs("eval2")],
            "proposer":[r["unit"] for r in pairs("proposer2")]},history)
        proof=self.proof("shadow_confirmation",{"plan":plan,"history_sha256":history,"evaluator_pairs":pairs("eval2"),
            "proposer_pairs":pairs("proposer2"),"budget_qualified":True})
        self.assert_code("shadow_history_drift",lambda:self.act("shadow_confirmation",{"candidate":candidate,"proof":proof,"history_sha256":"b"*64,"plan":plan}))
        result=self.act("shadow_confirmation",{"candidate":candidate,"proof":proof,"history_sha256":history,"plan":plan})
        self.assertEqual(result["status"],"simulated_meta_promoted")
        self.assertEqual(self.state()["meta_champion"],candidate)

    def test_public_research_views_are_separate_and_exclude_private_units(self):
        candidate=self.propose()["candidate"];self.memory(candidate)
        for role in ("evaluator","proposer"):
            visible=self.cards.evidence.visible(role,"research/"+role,self.ledger.head())
            self.assertTrue(visible)
            self.assertNotIn("paired0"," ".join(s["text"] for _,s in visible))
        evaluator=self.cards.evidence.visible("evaluator","research/evaluator",self.ledger.head())[0][0]
        self.assert_code("cross_namespace_source",lambda:self.cards.proposal_evidence(evaluator,"proposer","research/proposer",self.ledger.head()))


class ConfirmationPlanTests(EpochHarness):
    def confirmation_data(self,plan,prefix="audit",kind="better"):
        return {"plan":plan,"pairs":pairs(prefix,kind),"regression_safe":True,"budget_qualified":True,
            "completed":True,"infrastructure_failure":False}

    def test_plan_units_order_and_complete_denominator_rejected_atomically(self):
        candidate=self.propose()["candidate"]
        plan=self.plan("memory_confirmation",candidate,{"task":[r["unit"] for r in pairs("audit")]})
        original=self.state_ref
        for change in (lambda rows:rows[:-1],lambda rows:list(reversed(rows)),lambda rows:rows+pairs("extra",n=1)):
            data=self.confirmation_data(plan);data["pairs"]=change(data["pairs"])
            proof=self.proof("memory_confirmation",data)
            self.assert_code("confirmation_units_drift",lambda:self.act("memory_confirmation",{"candidate":candidate,"proof":proof,"plan":plan}))
            self.assertEqual(self.state_ref,original)
        proof=self.proof("memory_confirmation",self.confirmation_data(plan))
        self.assertEqual(self.act("memory_confirmation",{"candidate":candidate,"proof":proof,"plan":plan})["status"],"simulated_promoted")

    def test_unpublished_or_preexisting_proof_does_not_authorize_confirmation(self):
        from memorylab.contracts import sha256
        candidate=self.propose()["candidate"]
        request_id="transition"+str(self.counter+1)
        # Predicting a future ref is not sufficient: the publication must be later.
        args={"operation":"memory_confirmation","candidate":candidate,"baseline":self.m,"history_sha256":None,
            "units":{"task":[r["unit"] for r in pairs("audit")]}}
        predicted={"kind":"comparison_plan","id":"comparison-"+sha256({"request_id":request_id}),"version":1,
            "request_id":request_id,"state":self.state_ref,"memory_epoch":1,"focus":self.e,
            "config_sha256":sha256(self.ctl.config),"mode":"scripted_acceptance",**args}
        ref={k:predicted[k] for k in ("kind","id","version")};ref.update(namespace="control",sha256=sha256(predicted))
        early=self.ctl.fixture("earlyProof","memory_confirmation",self.confirmation_data(ref))
        plan=self.act("plan_confirmation",args)["plan"];self.assertEqual(ref,plan)
        self.assert_code("confirmation_proof_not_after_plan",lambda:self.act("memory_confirmation",{"candidate":candidate,"proof":early,"plan":plan}))
        data=self.confirmation_data(plan)
        request={"operation":"memory_confirmation","data":data,"category":"scripted_fixture"}
        with self.ledger.mutation():
            orphan=self.ledger.put({"kind":"acceptance_proof","id":"orphan-proof","version":1,**request},"control")
        self.assert_code("confirmation_proof_not_after_plan",lambda:self.act("memory_confirmation",{"candidate":candidate,"proof":orphan,"plan":plan}))

    def test_exact_cached_shadow_is_original_receipt_new_request_cannot_repeat(self):
        candidate=self.to_evaluator();history="a"*64
        plan=self.plan("shadow_confirmation",candidate,{"evaluator":[r["unit"] for r in pairs("ev")],
            "proposer":[r["unit"] for r in pairs("pr")]},history)
        proof=self.proof("shadow_confirmation",{"plan":plan,"history_sha256":history,"evaluator_pairs":pairs("ev"),
            "proposer_pairs":pairs("pr"),"budget_qualified":True})
        args={"candidate":candidate,"proof":proof,"plan":plan,"history_sha256":history}
        before=self.state_ref
        result=self.ctl.transition("shadowOnce",before,"shadow_confirmation",args)
        self.state_ref=result["result"]["state"];head=self.ledger.head()
        self.assertEqual(self.ctl.transition("shadowOnce",before,"shadow_confirmation",args),result)
        self.assertEqual(self.ledger.head(),head)
        self.assert_code("confirmation_plan_consumed_or_unknown",lambda:self.act("shadow_confirmation",args))
        self.assertEqual(self.ledger.head(),head)
        self.assert_code("shadow_not_new_task_champion",lambda:self.plan("shadow_confirmation",candidate,
            {"evaluator":["x"+str(i) for i in range(10)],"proposer":["y"+str(i) for i in range(10)]},history))

    def test_reserved_units_and_mastery_remain_consumed_across_focus_switch(self):
        self.to_evaluator()
        challenge=self.propose("distractor_count",4)["candidate"]
        self.act("challenge_confirmation",{"candidate":challenge,"proof":self.reward()})
        self.assertEqual(self.state()["memory_epoch"],2)
        candidate=self.propose("consolidation.type","single_index")["candidate"]
        for prefix,n in (("paired",10),("masteryA",7)):
            self.assert_code("reused_confirmation_units",lambda:self.plan("memory_confirmation",candidate,
                {"task":[prefix+str(i) for i in range(n)]}))
        self.memory(candidate,prefix="fresh")
        self.assert_code("reused_mastery_units",lambda:self.mastery("masteryB"))

    def test_abandoned_plan_reserves_units_and_wrong_baseline_is_not_accepted(self):
        candidate=self.propose()["candidate"]
        plan=self.plan("memory_confirmation",candidate,{"task":[r["unit"] for r in pairs("audit")]})
        other=self.propose("loader.global_briefs",2)["candidate"]
        self.assert_code("reused_confirmation_units",lambda:self.plan("memory_confirmation",other,
            {"task":[r["unit"] for r in pairs("audit")]}))
        self.assert_code("confirmation_baseline_drift",lambda:self.act("plan_confirmation",{"operation":"memory_confirmation",
            "candidate":other,"baseline":candidate,"history_sha256":None,"units":{"task":[r["unit"] for r in pairs("new")]}}))
        self.assertEqual(self.state()["comparison_plans"][0]["ref"],plan)

    def test_shadow_attempt_budget_not_reset_by_failure_or_new_request(self):
        candidate=self.to_evaluator();history="a"*64
        for i in range(3):
            ev=pairs("ev"+str(i),"tie");pr=pairs("pr"+str(i))
            plan=self.plan("shadow_confirmation",candidate,{"evaluator":[r["unit"] for r in ev],"proposer":[r["unit"] for r in pr]},history)
            proof=self.proof("shadow_confirmation",{"plan":plan,"history_sha256":history,"evaluator_pairs":ev,"proposer_pairs":pr,"budget_qualified":True})
            self.assertEqual(self.act("shadow_confirmation",{"candidate":candidate,"proof":proof,"plan":plan,"history_sha256":history})["status"],"shadow_rejected")
        self.assert_code("confirmation_plan_budget_exhausted",lambda:self.plan("shadow_confirmation",candidate,
            {"evaluator":[r["unit"] for r in pairs("evFinal")],"proposer":[r["unit"] for r in pairs("prFinal")]},history))
        self.ledger.audit()

    def test_all_failure_rows_consume_plan_and_never_promote(self):
        candidate=self.propose()["candidate"]
        plan=self.plan("memory_confirmation",candidate,{"task":[r["unit"] for r in pairs("audit")]})
        data=self.confirmation_data(plan,kind="worse");data["completed"]=False;data["infrastructure_failure"]=True
        proof=self.proof("memory_confirmation",data)
        result=self.act("memory_confirmation",{"candidate":candidate,"proof":proof,"plan":plan})
        self.assertEqual(result["status"],"confirmation_rejected")
        self.assertEqual(result["decision"]["units"],10)
        self.assertEqual(self.state()["comparison_plans"][0]["status"],"consumed")
        self.assertEqual(self.state()["phase"],"memory")

    def test_reopen_preserves_reservation_and_consumption(self):
        from memorylab.ledger import Ledger
        candidate=self.to_evaluator();history="a"*64
        plan=self.plan("shadow_confirmation",candidate,{"evaluator":[r["unit"] for r in pairs("ev")],
            "proposer":[r["unit"] for r in pairs("pr")]},history)
        proof=self.proof("shadow_confirmation",{"plan":plan,"history_sha256":history,"evaluator_pairs":pairs("ev","tie"),
            "proposer_pairs":pairs("pr"),"budget_qualified":True})
        args={"candidate":candidate,"proof":proof,"plan":plan,"history_sha256":history}
        self.act("shadow_confirmation",args)
        path=self.ledger.path;self.ledger.close();self.ledger=Ledger(path)
        self.registry.ledger=self.ledger;self.evidence.ledger=self.ledger
        self.ctl=ControlledEpochs(self.ledger,self.registry,self.cards)
        self.assert_code("confirmation_plan_consumed_or_unknown",lambda:self.act("shadow_confirmation",args))
        self.assert_code("reused_confirmation_units",lambda:self.plan("shadow_confirmation",candidate,
            {"evaluator":[r["unit"] for r in pairs("ev")],"proposer":[r["unit"] for r in pairs("different")]},history))
        self.ledger.audit()

    def test_proof_cannot_be_attached_to_another_candidate_plan(self):
        first=self.propose()["candidate"]
        first_plan=self.plan("memory_confirmation",first,{"task":[r["unit"] for r in pairs("audit")]})
        second=self.propose("loader.global_briefs",2)["candidate"]
        second_plan=self.plan("memory_confirmation",second,{"task":[r["unit"] for r in pairs("other")]})
        data=self.confirmation_data(first_plan,prefix="other")
        proof=self.proof("memory_confirmation",data)
        self.assert_code("confirmation_proof_plan_drift",lambda:self.act("memory_confirmation",{"candidate":second,"proof":proof,"plan":second_plan}))
        self.assertTrue(all(p["status"]=="planned" for p in self.state()["comparison_plans"]))

    def test_shadow_roles_cannot_share_units_and_expired_plan_never_reopens(self):
        candidate=self.to_evaluator();history="a"*64
        units=[r["unit"] for r in pairs("ev")]
        self.assert_code("reused_confirmation_units",lambda:self.plan("shadow_confirmation",candidate,
            {"evaluator":units,"proposer":units},history))
        plan=self.plan("shadow_confirmation",candidate,{"evaluator":units,"proposer":[r["unit"] for r in pairs("pr")]},history)
        challenge=self.propose("distractor_count",4)["candidate"]
        self.act("challenge_confirmation",{"candidate":challenge,"proof":self.reward()})
        proof=self.proof("shadow_confirmation",{"plan":plan,"history_sha256":history,"evaluator_pairs":pairs("ev"),
            "proposer_pairs":pairs("pr"),"budget_qualified":True})
        self.assert_code("confirmation_plan_consumed_or_unknown",lambda:self.act("shadow_confirmation",{"candidate":candidate,"proof":proof,"plan":plan,"history_sha256":history}))
        self.assertEqual(next(p for p in self.state()["comparison_plans"] if p["ref"]==plan)["status"],"expired")



class DecisionFamilyTests(Fixture):
    def test_direction_family_null_error_is_bounded_over_all_allowed_discordance(self):
        import math
        from memorylab.statistics import tail
        config=epoch_config();alpha=config["alpha"]/(9*3)
        for n in range(1,15):
            error=sum(math.comb(n,k)*.5**n for k in range(n+1)
                if tail(k,n,.5)<=alpha or tail(n-k,n,.5)<=alpha)
            self.assertLessEqual(error,2*alpha+1e-12)
            self.assertLessEqual(9*error,config["alpha"])
        self.assertAlmostEqual(paired_decision(pairs(),config)["alpha_adjusted"],alpha)
        self.assertEqual(paired_decision(pairs(n=7),config)["verdict"],"indistinguishable")
        result=paired_decision(pairs(n=9),config)
        self.assertGreater(result["p_better"],alpha)
        # Strong wins can establish NI before the stricter superiority threshold.
        self.assertEqual(result["verdict"],"cheaper_noninferior")
        self.assertEqual(paired_decision(pairs(n=10),config)["verdict"],"better")

    def test_family_configuration_cannot_silently_drop_claims_or_shadow(self):
        import json
        from memorylab.contracts import ContractError
        config=epoch_config()
        for field,value in (("decision_claims",["better"]),("confirmation_comparisons_max",3),
            ("error_scope","unbounded_bootstrap"),("confirmation_policy","optional_stopping")):
            bad=copy.deepcopy(config);bad[field]=value
            path=self.ledger.path.parent/"bad-config.json";path.write_text(json.dumps(bad))
            with self.assertRaises(ContractError) as caught:epoch_config(path)
            self.assertIn(caught.exception.code,("unsupported_decision_family","unsupported_confirmation_policy"))
