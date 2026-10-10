import copy
import os
from pathlib import Path

from memorylab.cards import CanonicalCards,EvidenceStore
from memorylab.contracts import canonical,sha256
from memorylab.ledger import Ledger
from memorylab.registry import Registry
from memorylab.runtime import RuntimeBuilder,UsageStore,credit_scores
from memorylab.stores import CorpusStore,portable
from test_kernel import AT,Fixture


class StoreTests(Fixture):
    def setUp(self):
        super().setUp();self.store=CorpusStore(self.cards)

    def corpus(self,ns="task/acq-seed"):
        src=self.source("learn-"+ns.replace("/","-"),ns=ns)
        a=self.extract(self.raw_card([src],id="learned"),"extract-"+ns.replace("/","-"),ns=ns)["result"]["cards"][0]
        return src,a

    def snapshot(self,ns="task/acq-seed",request="snapshot",cutoff=None):
        return self.store.capture(request,ns,self.ledger.head() if cutoff is None else cutoff)

    def test_shared_corpus_relocation_exact_text_versions_and_independent_future(self):
        src,a=self.corpus();ref=self.snapshot();original=self.store.resolve(ref)
        first=self.store.import_snapshot("copyA",self.store,ref,"task/armA","shared_acquisition")
        second=self.store.import_snapshot("copyB",self.store,ref,"task/armB","shared_acquisition")
        for result in (first["result"],second["result"]):
            copied=self.store._payload(result["namespace"],result["cutoff"])
            self.assertEqual(portable(copied),original["portable_sha256"])
            self.assertEqual(copied["corpus"]["cards"][0]["card"]["detail"],self.ledger.get(a)["detail"])
            self.assertEqual(copied["sources"][0]["source"]["text"],self.ledger.get(src)["text"])
            self.assertNotEqual(copied["corpus"]["cards"][0]["ref"],a)
        self.assertEqual(self.store.import_snapshot("copyA",self.store,ref,"task/armA","shared_acquisition"),first)
        self.extract(self.raw_card([self.cards.active("task/armA",self.ledger.head())[0][1]["evidence_refs"][0]],
            id="learned",expected_version=1,brief="An arm's later revision"),"revisionA",ns="task/armA")
        self.assertEqual(self.cards.active("task/armB",self.ledger.head())[0][1]["version"],1)
        self.assertEqual(self.store.resolve(ref),original)
        self.assert_code("import_namespace_not_empty",lambda:self.store.import_snapshot("newCopy",self.store,ref,"task/armA","shared_acquisition"))

    def test_old_versions_withdrawal_and_supersession_survive_copy(self):
        src,a=self.corpus()
        self.extract(self.raw_card([src],id="replacement",supersedes=[a]),"replace",ns="task/acq-seed")
        self.extract(self.raw_card([src],id="replacement",expected_version=1,status="withdrawn"),"withdraw",ns="task/acq-seed")
        ref=self.snapshot();result=self.store.import_snapshot("historyCopy",self.store,ref,"task/armA","shared_acquisition")["result"]
        self.assertEqual(self.cards.active("task/armA",result["cutoff"]),[])
        history=self.cards.corpus("task/armA",result["cutoff"])["cards"]
        self.assertEqual([(r["card"]["id"],r["card"]["version"]) for r in history],[("learned",1),("replacement",1),("replacement",2)])
        self.assertEqual(history[1]["card"]["supersedes"],[history[0]["ref"]])

    def test_future_hidden_and_other_namespace_are_excluded(self):
        src,a=self.corpus();cutoff=self.ledger.head()
        self.source("private",ns="task/acq-seed",text="Never publish",visibility="controller_only")
        self.source("future",ns="task/acq-seed",text="Future target feedback")
        self.corpus("task/other-arm")
        ref=self.snapshot(cutoff=cutoff);payload=self.store.resolve(ref)["payload"]
        self.assertEqual(len(payload["sources"]),1)
        self.assertNotIn("Future target feedback",canonical(payload).decode())
        result=self.store.import_snapshot("beforeFuture",self.store,ref,"task/armA","shared_acquisition")["result"]
        self.assertEqual(len(self.cards.evidence.visible("extractor","task/armA",result["cutoff"])),1)

    def test_usage_credit_order_is_preserved_without_index_to_leaf_assignment(self):
        src,a=self.corpus();ns="task/acq-seed";m=self.registry.register_root("memory","withIndex",dict(self.kernel["memory_initial"],**{"consolidation.type":"single_index"}))
        b=self.extract(self.raw_card([src],id="second"),"secondCard",ns=ns)["result"]["cards"][0]
        projection=RuntimeBuilder(self.cards,self.registry).build(ns,self.ledger.head(),m)
        index=next(i["ref"] for i in projection["projection"]["items"] if i["level"]==1)
        tool=self.evidence.register("tool",ns,"tool_result","actual-use",AT,"A published command returned failure.")
        outcome=self.evidence.register("outcome",ns,"visible_outcome","actual-use",AT,"Verified failure.")
        usage=UsageStore(self.cards)
        for label,ref in (("card",a),("index",index)):
            usage.record("open-"+label,ns,"actual-use","open",ref)
            usage.record("link-"+label,ns,"actual-use","behavior_link",ref,evidence_ref=tool)
        usage.record("independent",ns,"actual-use","independent_outcome",outcome="failure",evidence_ref=outcome)
        snapshot=self.snapshot();original=self.store.resolve(snapshot)
        result=self.store.import_snapshot("usageCopy",self.store,snapshot,"task/armA","shared_acquisition")["result"]
        copied=self.store._payload("task/armA",result["cutoff"])
        self.assertEqual(portable(copied),original["portable_sha256"])
        shell=copied["indices"][0]["index"]
        self.assertTrue(shell["historical_reference_only"]);self.assertNotIn("detail",shell);self.assertNotIn("brief",shell)
        root=self.registry.register_root("memory","newWithoutIndex")
        rebuilt=RuntimeBuilder(self.cards,self.registry).build("task/armA",result["cutoff"],root)
        self.assertTrue(all(i["level"]==0 for i in rebuilt["projection"]["items"]))
        old_scores=credit_scores(original["payload"]["usage"],.2);new_scores=credit_scores(copied["usage"],.2)
        self.assertEqual(sorted(old_scores.values()),sorted(new_scores.values()))
        self.assertEqual(len(new_scores),2)
        self.assertEqual(len(self.cards.active("task/armA",result["cutoff"])),2)

    def test_import_crash_points_are_atomic_and_replay_does_not_duplicate(self):
        self.corpus();ref=self.snapshot()
        for phase in ("sources","cards","indices","usage","receipt"):
            ns="task/"+phase;before=self.ledger.audit()
            def fault(name):
                if name==phase:raise RuntimeError("injected interruption")
            with self.assertRaises(RuntimeError):self.store.import_snapshot("copy-"+phase,self.store,ref,ns,"shared_acquisition",fault)
            self.assertEqual(self.ledger.audit(),before);self.assertEqual(self.ledger.events(ns),[])
            result=self.store.import_snapshot("copy-"+phase,self.store,ref,ns,"shared_acquisition")
            self.assertEqual(self.store.import_snapshot("copy-"+phase,self.store,ref,ns,"shared_acquisition"),result)

    def test_research_shadow_separate_stores_keep_same_cutoff_and_source_readonly(self):
        ns="research/evaluator";src,a=self.corpus(ns)
        # The snapshot owns only the original research role; no other role or
        # future proposal is copied by the fork.
        self.corpus("research/proposer")
        root=self.registry.register_root("memory","metaRoot")
        proposal=self.proposal(root,evidence_refs=[]);child=self.propose(proposal,"metaChild")["result"]["candidate"]
        ref=self.snapshot(ns);before=self.ledger.audit()
        one=Path(self.tmp.name)/"one.sqlite";two=Path(self.tmp.name)/"two.sqlite"
        a_fork=self.store.fork("forkOne",ref,one,ns,self.registry,[child])
        b_fork=self.store.fork("forkTwo",ref,two,ns,self.registry,[child])
        self.assertEqual(self.ledger.audit(),before)
        self.assertEqual(a_fork["import"]["result"]["portable_sha256"],b_fork["import"]["result"]["portable_sha256"])
        with Ledger(one) as ledger:
            reg=Registry(ledger,self.kernel);cards=CanonicalCards(EvidenceStore(ledger,self.kernel))
            self.assertEqual(reg.resolve(child),self.registry.resolve(child));self.assertEqual(reg.resolve(root),self.registry.resolve(root))
            self.assertEqual(ledger.events("research/proposer"),[])
            new_source=cards.evidence.register("shadowOnly",ns,"agent_output","research",AT,"Only this shadow proposal")
        with Ledger(two) as ledger:
            self.assertFalse(any(v["text"]=="Only this shadow proposal" for _,v in ledger.objects("source",ns)))
        self.assertTrue(self.store.fork("forkOne",ref,one,ns,self.registry,[child])["cache_hit"])
        self.assert_code("reference_hash_mismatch",lambda:self.store.fork("differentRecipe",ref,one,ns,self.registry,[child]))

    def test_fork_publication_failure_never_leaves_partial_destination(self):
        ns="research/proposer";self.corpus(ns);ref=self.snapshot(ns);m=self.registry.register_root("memory","meta")
        destination=Path(self.tmp.name)/"failed.sqlite";before=self.ledger.audit()
        def fault(name):
            if name=="before_publish":raise RuntimeError("crash after DB commit before publication")
        with self.assertRaises(RuntimeError):self.store.fork("fork",ref,destination,ns,self.registry,[m],fault=fault)
        self.assertFalse(destination.exists());self.assertEqual(list(Path(self.tmp.name).glob(".store-pending-*")),[])
        self.assertEqual(self.ledger.audit(),before)
        result=self.store.fork("fork",ref,destination,ns,self.registry,[m]);self.assertFalse(result["cache_hit"])

    def test_store_alias_cannot_be_used_as_separate_shadow_database(self):
        ns="research/proposer";self.corpus(ns);ref=self.snapshot(ns);m=self.registry.register_root("memory","meta")
        alias=Path(self.tmp.name)/"alias.sqlite";os.link(self.db_path,alias)
        before=self.ledger.audit()
        self.assert_code("fork_requires_separate_store",lambda:self.store.fork("sameFile",ref,alias,ns,self.registry,[m]))
        self.assertEqual(self.ledger.audit(),before)

    def test_existing_unrelated_database_and_symlink_are_never_overwritten(self):
        ns="research/evaluator";self.corpus(ns);ref=self.snapshot(ns);m=self.registry.register_root("memory","meta")
        unrelated=Path(self.tmp.name)/"unrelated.sqlite"
        with Ledger(unrelated) as ledger:
            Registry(ledger,self.kernel);before=ledger.audit()
        with self.assertRaises(ValueError):self.store.fork("occupied",ref,unrelated,ns,self.registry,[m])
        with Ledger(unrelated) as ledger:self.assertEqual(ledger.audit(),before)
        link=Path(self.tmp.name)/"link.sqlite";link.symlink_to(unrelated)
        self.assert_code("invalid_fork_destination",lambda:self.store.fork("linked",ref,link,ns,self.registry,[m]))

    def test_wrong_purpose_cross_role_snapshot_and_forged_snapshot_are_denied(self):
        self.corpus();ref=self.snapshot()
        self.assert_code("invalid_initial_import",lambda:self.store.import_snapshot("taskToResearch",self.store,ref,"research/proposer","initial_checkpoint"))
        self.assert_code("invalid_research_shadow_import",lambda:self.store.import_snapshot("notResearch",self.store,ref,"task/armA","research_shadow"))
        self.corpus("research/evaluator");research=self.snapshot("research/evaluator",request="research")
        self.assert_code("invalid_research_shadow_import",lambda:self.store.import_snapshot("roleChange",self.store,research,"research/proposer","research_shadow"))
        value=copy.deepcopy(self.store.resolve(ref));value["id"]="fakeSnapshot";value["request_id"]="neverCommitted"
        with self.ledger.transaction():fake=self.ledger.put(value,"control")
        self.assert_code("uncommitted_corpus_snapshot",lambda:self.store.resolve(fake))

    def test_cutoff_inside_registration_transaction_cannot_become_committed_snapshot(self):
        src=self.source(ns="task/acq-seed");sequence=self.ledger.get(src)["sequence"]
        self.assert_code("uncommitted_snapshot_cutoff",lambda:self.snapshot(cutoff=sequence))
        self.assertEqual(self.ledger.objects("corpus_snapshot","control"),[])

    def test_byte_and_count_limits_reject_whole_snapshot_without_truncation(self):
        self.corpus()
        for key,value in (("max_snapshot_bytes",100),("max_sources",1)):
            limited=CorpusStore(self.cards,dict(self.store.config,**{key:value}))
            if key=="max_sources":self.source("extra",ns="task/acq-seed")
            expected="corpus_snapshot_byte_limit" if key=="max_snapshot_bytes" else "corpus_snapshot_count_limit"
            self.assert_code(expected,lambda:limited.capture("limited-"+key,"task/acq-seed",self.ledger.head()))
        self.assertEqual(self.ledger.objects("corpus_snapshot","control"),[])

    def test_empty_snapshot_is_valid_and_secrets_never_export(self):
        ref=self.snapshot("task/empty");result=self.store.import_snapshot("emptyCopy",self.store,ref,"task/armA","initial_checkpoint")
        self.assertEqual(self.cards.corpus("task/armA",result["result"]["cutoff"])["cards"],[])
        self.source("secret",ns="task/secret",text="api_key="+"z"*40)
        self.assert_code("corpus_snapshot_secret_material",lambda:self.snapshot("task/secret",request="unsafe"))
