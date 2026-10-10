import copy
import json

from memorylab.contracts import canonical
from memorylab.derived import DerivedStage
from memorylab.runtime import RuntimeBuilder,RuntimeSession,read_runtime
from test_kernel import Fixture


class DerivedTests(Fixture):
    def setUp(self):
        super().setUp()
        self.a=self.extract(self.raw_card(id="a"),"deriveExtractA")["result"]["cards"][0]
        self.b=self.extract(self.raw_card(id="b",outcome="success"),"deriveExtractB")["result"]["cards"][0]
        config=dict(self.kernel["memory_initial"],**{"consolidation.type":"single_index"})
        self.m=self.registry.register_root("memory","indexed",config)
        self.stage=DerivedStage(self.cards,self.registry)
        self.input=self.stage.prepare("consolidation","task/chain",self.ledger.head(),self.m)
        self.index={"type_hint":"environment/services","brief":"Check dependency state before editing integration logic.",
            "detail":"This applies when the declared service state blocks the integration. Retry only when the dependency is healthy. It does not authorize bypassing a current outage.","support_refs":[self.a,self.b]}
        self.response={"indices":[self.index]}

    def accept(self,response=None,request="derive",input_=None,invocation=None):
        input_=input_ or self.input
        invocation=invocation or {"mode":"scripted_acceptance","input_sha256":input_["sha256"],"model":"explicit-offline-fixture",
            "model_calls":0,"input_tokens":None,"output_tokens":None}
        raw=response if type(response) is str else canonical(response if response is not None else self.response).decode()
        return self.stage.accept(request,input_,raw,invocation)["result"]

    def reject(self,code,response):
        result=self.accept(response)
        self.assertEqual(result["status"],"rejected");self.assertEqual(result["code"],code)
        self.assertIsNone(result["batch"]);self.assertEqual(self.ledger.objects("derived_batch","task/chain"),[])
        self.assertEqual(len(self.ledger.objects("derived_raw","control")),1)

    def test_input_only_current_canonical_sources_and_exact_snapshot(self):
        p=self.input["payload"]
        self.assertTrue(p["scheduled"]);self.assertEqual(len(p["cards"]),2)
        self.assertEqual({s["ref"]["namespace"] for s in p["sources"]},{"task/chain"})
        self.assertEqual(p["spec"],self.m);self.assertNotIn("grade",p)
        wrong=copy.deepcopy(self.input);wrong["payload"]["cards"][0]["detail"]="Injected future answer"
        self.assert_code("derived_input_drift",lambda:self.accept(input_=wrong))

    def test_generated_abstract_index_progressive_expansion_and_canonical_unchanged(self):
        corpus=self.cards.corpus("task/chain",self.ledger.head());result=self.accept();ref=result["batch"]
        self.assertEqual(result["status"],"accepted");self.assertEqual(result["semantic_validation"],"not_established")
        # The original cutoff, not the new derived commit's head, binds input.
        cutoff=self.input["payload"]["cutoff"]
        built=RuntimeBuilder(self.cards,self.registry).build("task/chain",cutoff,self.m,{"consolidation":ref})
        index=next(i for i in built["projection"]["items"] if i["level"]==1)
        self.assertEqual(index["detail"],self.index["detail"])
        self.assertEqual(self.cards.corpus("task/chain",cutoff),corpus)
        self.assertEqual(built["maintenance"]["semantic_validation"],"unverified_derived_semantics")
        session=RuntimeSession(self.ledger,built["projection_ref"],"service dependency state")
        session.briefs();detail=json.loads(session.open({"op":"open_memory","id":index["id"]}))["memory"]
        self.assertEqual(set(detail["support_ids"]),{"a","b"});self.assertNotIn("generator_batch",detail)

    def test_cached_response_no_duplicate_batch_and_same_content_new_provenance(self):
        first=self.accept();self.assertEqual(self.accept(),first)
        self.assertEqual(len(self.ledger.objects("derived_batch","task/chain")),1)
        second=self.accept(request="independentRecordedGeneration")
        self.assertNotEqual(first["batch"],second["batch"])
        for ref in (first["batch"],second["batch"]):
            RuntimeBuilder(self.cards,self.registry).build("task/chain",self.input["payload"]["cutoff"],self.m,{"consolidation":ref})

    def test_recursion_duplicate_unknown_and_cross_namespace_support_rejected(self):
        variants=[]
        item=copy.deepcopy(self.index);item["support_refs"][1]=dict(self.b,kind="index");variants.append(("invalid_reference_kind",item))
        item=copy.deepcopy(self.index);item["support_refs"]=[self.a,self.a];variants.append(("duplicate_reference",item))
        item=copy.deepcopy(self.index);item["support_refs"][1]=dict(self.b,namespace="task/other");variants.append(("cross_namespace_index_support",item))
        item=copy.deepcopy(self.index);item["support_refs"][1]=dict(self.b,sha256="0"*64);variants.append(("inactive_index_support",item))
        for n,(code,item) in enumerate(variants):
            result=self.accept({"indices":[item]},request="bad"+str(n));self.assertEqual(result["code"],code);self.assertIsNone(result["batch"])
        self.assertEqual(self.ledger.objects("derived_batch","task/chain"),[])

    def test_wrong_ancestor_and_output_fields_preserve_rejection_raw(self):
        self.index["type_hint"]="workflow/repair"
        self.reject("invalid_index_ancestor",self.response)

    def test_fixed_tree_rejects_cross_group_abstract_root(self):
        other=self.extract(self.raw_card(id="other",type_hint="workflow/repair"),"otherCategory")["result"]["cards"][0]
        root=self.registry.register_root("memory","tree",dict(self.kernel["memory_initial"],**{"organizer.type":"fixed_tree","consolidation.type":"single_index"}))
        prepared=self.stage.prepare("consolidation","task/chain",self.ledger.head(),root)
        item=copy.deepcopy(self.index);item.update(type_hint="root",support_refs=[self.a,other])
        result=self.accept({"indices":[item]},input_=prepared)
        self.assertEqual(result["code"],"cross_group_tree_index")
        # The same canonical supports have a valid synthetic root in flat mode.
        flat=self.stage.prepare("consolidation","task/chain",prepared["payload"]["cutoff"],self.m)
        accepted=self.accept({"indices":[item]},request="flatRoot",input_=flat)
        self.assertEqual(accepted["status"],"accepted")

    def test_input_overflow_rejects_whole_input_and_single_leaf_skips_call(self):
        config=copy.deepcopy(self.stage.config);config["max_input_bytes"]=10
        stage=DerivedStage(self.cards,self.registry,config=config)
        self.assert_code("derived_input_overflow",lambda:stage.prepare("consolidation","task/chain",self.ledger.head(),self.m))
        self.extract(self.raw_card(id="b",expected_version=1,status="withdrawn"),"withdrawn")
        prepared=self.stage.prepare("consolidation","task/chain",self.ledger.head(),self.m)
        self.assertFalse(prepared["payload"]["scheduled"])

    def test_two_invalid_items_cannot_commit_partial_batch(self):
        invalid=copy.deepcopy(self.index);invalid["type_hint"]="workflow"
        self.reject("invalid_index_ancestor",{"indices":[self.index,invalid]})

    def test_malformed_json_and_duplicate_keys_are_rejected(self):
        for n,(text,code) in enumerate((("{","invalid_derived_json"),('{"indices":[],"indices":[]}',"duplicate_json_key"),
            ('{"indices":[],"gold":"answer"}',"invalid_derived_output_fields"))):
            result=self.accept(text,request="parse"+str(n));self.assertEqual(result["code"],code);self.assertIsNone(result["batch"])

    def test_noop_retained_and_missing_generation_not_silently_accepted(self):
        noop=self.accept({"indices":[]});self.assertEqual(noop["status"],"noop")
        builder=RuntimeBuilder(self.cards,self.registry);cutoff=self.input["payload"]["cutoff"]
        built=builder.build("task/chain",cutoff,self.m,{"consolidation":noop["batch"]})
        self.assertFalse(any(i["level"]==1 for i in built["projection"]["items"]))
        self.assert_code("missing_scheduled_derived_batch",lambda:builder.build("task/chain",cutoff,self.m,{}))

    def test_none_and_off_disable_stages(self):
        root=self.registry.register_root("memory","none")
        prepared=self.stage.prepare("consolidation","task/chain",self.ledger.head(),root)
        self.assertFalse(prepared["payload"]["scheduled"])
        result=self.accept(input_=prepared);self.assertEqual(result["code"],"derived_stage_disabled")
        reflection=self.stage.prepare("reflector","task/chain",self.ledger.head(),root)
        self.assertFalse(reflection["payload"]["scheduled"])

    def test_stale_support_or_different_spec_cannot_load_old_batch(self):
        ref=self.accept()["batch"]
        self.extract(self.raw_card(id="a",expected_version=1,brief="Revised current rule"),"revision")
        self.assert_code("stale_derived_batch",lambda:RuntimeBuilder(self.cards,self.registry).build("task/chain",self.ledger.head(),self.m,{"consolidation":ref}))
        newconfig=dict(self.kernel["memory_initial"],**{"consolidation.type":"single_index","organizer.type":"fixed_tree"})
        m=self.registry.register_root("memory","other",newconfig)
        self.assert_code("stale_derived_batch",lambda:self.stage.resolve(ref,"consolidation","task/chain",self.input["payload"]["cutoff"],m))

    def test_live_usage_claim_cannot_self_certify(self):
        invocation={"mode":"live","input_sha256":self.input["sha256"],"model":"configured","model_calls":1,"input_tokens":100,"output_tokens":50}
        result=self.accept(invocation=invocation)
        self.assertEqual(result["code"],"unvalidated_live_derived_invocation");self.assertIsNone(result["batch"])

    def test_reflection_failure_only_eligible_current_leaf_l2(self):
        self.extract(self.raw_card(id="a",expected_version=1,outcome="failure"),"failureRevision")
        root=self.registry.register_root("memory","reflect",dict(self.kernel["memory_initial"],**{"reflection.trigger":"failure"}))
        prepared=self.stage.prepare("reflector","task/chain",self.ledger.head(),root);support=prepared["payload"]["eligible_refs"][0]
        result=self.accept({"annotations":[{"support":support,"text":"The failure alone cannot identify the fix; check the current service facts."}]},input_=prepared)
        built=RuntimeBuilder(self.cards,self.registry).build("task/chain",prepared["payload"]["cutoff"],root,{"reflector":result["batch"]})
        self.assertEqual(len(built["projection"]["annotations"]),1)
        self.assertIn("Derived reflection:",next(i for i in built["projection"]["items"] if i["id"]=="a")["detail"])
        self.assertNotIn("Derived reflection:",self.ledger.get(support)["detail"])
        invalid=self.accept({"annotations":[{"support":self.b,"text":"Unsupported correction"}]},request="invalidReflection",input_=prepared)
        self.assertEqual(invalid["code"],"ineligible_reflection_support")
