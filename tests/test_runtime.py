import copy
import json
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from memorylab.common import digest
from memorylab.legacy import adapt_leaf
from memorylab.runtime import (CLIENT, RuntimeBuilder, RuntimeSession, RuntimeSocket,
                               UsageStore, credit_scores, read_runtime, terms, validate_runtime)
from test_kernel import AT, Fixture


class RuntimeTests(Fixture):
    def root(self, **changes):
        config = dict(self.kernel["memory_initial"], **changes)
        return self.registry.register_root("memory", "m"+str(len(self.ledger.objects("memory", "control"))), config)

    def add(self, id_, **changes):
        return self.extract(self.raw_card(id=id_, **changes), "extract"+id_)["result"]["cards"][0]

    def build(self, root=None, config=None):
        root = root or self.root()
        return RuntimeBuilder(self.cards, self.registry, config).build("task/chain", self.ledger.head(), root)

    def session(self, built=None, task="repair service health", config=None, **kwargs):
        built = built or self.build(config=config)
        return RuntimeSession(self.ledger, built["projection_ref"], task, **kwargs)

    def test_empty_and_no_relevant_memory_are_allowed(self):
        s = self.session()
        self.assertEqual(json.loads(s.briefs()), {"items": []})
        self.add("service")
        s = self.session(task="draw polygon geometry")
        self.assertEqual(json.loads(s.briefs()), {"items": []})
        self.assertGreater(s.events[0]["tokens"], 0)
        self.assertFalse(s.events[0]["tokenizer"]["live_qualified"])

    def test_research_exact_refs_are_metered_and_can_be_used_in_typed_proposal(self):
        ns = "research/proposer"
        source = self.source("research-reference-source", ns=ns, text="Memory proposer research observation")
        ref = self.extract(self.raw_card(refs=[source], id="research-card", brief="memory proposer research", detail="Observed research only; not a benefit claim."), "research-reference-card", ns=ns)["result"]["cards"][0]
        root = self.root(); built = RuntimeBuilder(self.cards, self.registry).build(ns, self.ledger.head(), root)
        s = RuntimeSession(self.ledger, built["projection_ref"], "memory proposer research")
        wire = s.briefs(); self.assertEqual(json.loads(wire)["items"][0]["evidence_ref"], ref)
        self.assertEqual(s.events[-1]["bytes"], len(wire))
        wire = s.open({"op": "open_memory", "id": ref["id"]})
        self.assertEqual(json.loads(wire)["memory"]["evidence_ref"], ref)
        self.assertEqual(s.events[-1]["bytes"], len(wire))
        self.assertEqual(s.events[-1]["tokens"], len(wire))
        proposal = self.proposal(root, mutation=None, evidence_refs=[ref])
        result = self.propose(proposal, "research-ref-noop")["result"]
        self.assertEqual(result["status"], "noop")
        normal = self.session(); self.add("normal-task-card")
        normal = self.session(); self.assertNotIn("evidence_ref", json.loads(normal.briefs())["items"][0])
        self.assertNotIn("evidence_ref", json.loads(normal.open({"op": "open_memory", "id": "normal-task-card"}))["memory"])

    def test_research_reference_metadata_cannot_bypass_brief_budget(self):
        ns = "research/proposer"
        source = self.source("research-budget-source", ns=ns, text="Research observation")
        ref = self.extract(self.raw_card(refs=[source], id="research-budget-card", brief="memory proposer", detail="Research context"), "research-budget-card", ns=ns)["result"]["cards"][0]
        root = self.root(); config = read_runtime()
        built = RuntimeBuilder(self.cards, self.registry).build(ns, self.ledger.head(), root)
        item = built["projection"]["items"][0]
        from memorylab.contracts import canonical
        bare = {"items": [{k: item[k] for k in ("id", "version", "type_hint", "brief", "level")}]}
        config["brief_tokens"] = len(canonical(bare)) + 1
        built = RuntimeBuilder(self.cards, self.registry, config).build(ns, self.ledger.head(), root)
        s = RuntimeSession(self.ledger, built["projection_ref"], "memory proposer")
        self.assertEqual(json.loads(s.briefs()), {"items": []})
        self.assertEqual(s.events[0]["memory_refs"], [])
        self.assertEqual(s.events[0]["omissions"][0]["reason"], "whole_brief_exceeds_remaining_budget")
        self.assertEqual(json.loads(s.open({"op": "open_memory", "id": ref["id"]}))["error"], "undisclosed_memory_denied")

    def test_unicode_terms_are_deterministic(self):
        self.assertIn("service", terms("ＳＥＲＶＩＣＥ"))
        self.assertIn("服务", terms("服务故障"))
        self.add("cjk", brief="服务故障先检查网络", detail="此规则只适用于依赖网络服务的集成。")
        s = self.session(task="服务调用失败")
        self.assertEqual(json.loads(s.briefs())["items"][0]["id"], "cjk")

    def test_progressive_id_only_and_no_sources_or_versions(self):
        self.add("service")
        s = self.session(); s.briefs()
        payload = json.loads(s.open({"op": "open_memory", "id": "service"}))["memory"]
        self.assertNotIn("evidence_refs", payload)
        self.assertNotIn("extractor", payload)
        for req in ({"op": "source", "id": "service"}, {"op": "search", "id": "service"},
                    {"op": "open_memory", "id": "service", "version": 1}, {"op": "open_memory", "id": "unknown"},
                    {"op": "open_memory", "id": "../service"}):
            self.assertIn("error", json.loads(s.open(req)))

    def test_open_requires_briefs_first(self):
        s = self.session()
        self.assert_code("brief_not_disclosed", lambda: s.open({"op": "open_memory", "id": "x"}))

    def test_indices_have_exact_current_leaf_support_and_rebuild_invalidates(self):
        a = self.add("serviceA")
        b = self.add("serviceB")
        root = self.root(**{"organizer.type": "fixed_tree", "consolidation.type": "single_index"})
        first = self.build(root)
        old_indices = [i for i in first["projection"]["items"] if i["level"] == 1]
        self.assertEqual(len(old_indices), 1)
        self.assertEqual(old_indices[0]["type_hint"], "environment/services")
        self.assertEqual({r["id"] for r in old_indices[0]["support_refs"]}, {"serviceA", "serviceB"})
        self.extract(self.raw_card(id="serviceA", expected_version=1, brief="Revised service diagnosis."), "revision")
        second = self.build(root)
        index = next(i for i in second["projection"]["items"] if i["level"] == 1)
        self.assertNotEqual(index["id"], old_indices[0]["id"])
        self.assertEqual(next(r["version"] for r in index["support_refs"] if r["id"] == "serviceA"), 2)
        self.assertEqual(self.ledger.get(first["projection_ref"]), first["projection"])

    def test_explicit_supersession_and_none_differ_only_in_stale_leaf_view(self):
        old = self.add("old")
        self.add("replacement", supersedes=[old])
        self.add("other")
        normal = self.build(self.root(**{"consolidation.type": "single_index"}))
        stale = self.build(self.root(**{"consolidation.type": "single_index", "invalidation.type": "none"}))
        self.assertNotIn("old", [i["id"] for i in normal["projection"]["items"]])
        self.assertIn("old", [i["id"] for i in stale["projection"]["items"]])
        self.assertFalse(any(r["id"] == "old" for i in stale["projection"]["items"] if i["level"] == 1 for r in i["support_refs"]))

    def test_no_single_or_withdrawn_index_support(self):
        self.add("one")
        root = self.root(**{"consolidation.type": "single_index"})
        self.assertFalse(any(i["level"] == 1 for i in self.build(root)["projection"]["items"]))
        self.add("two", status="withdrawn")
        self.assertFalse(any(i["level"] == 1 for i in self.build(root)["projection"]["items"]))

    def test_index_open_discloses_only_navigation_and_bounded_child_handles(self):
        self.add("a"); self.add("b")
        config = read_runtime(); config.update(brief_tokens=16000, detail_tokens=48000)
        built = self.build(self.root(**{"consolidation.type": "single_index"}), config)
        s = self.session(built)
        briefs = json.loads(s.briefs())["items"]
        index = next(i for i in briefs if i["level"] == 1)
        detail = json.loads(s.open({"op": "open_memory", "id": index["id"]}))["memory"]
        self.assertEqual(set(detail["support_ids"]), {"a", "b"})
        self.assertNotIn("other failures require separate diagnosis", detail["detail"])
        self.assertEqual(json.loads(s.open({"op": "open_memory", "id": "a"}))["memory"]["level"], 0)

    def test_wrong_category_tree_and_lexical_fallback(self):
        self.add("misplaced", type_hint="evidence/time", brief="Diagnose service health before repairing the integration.")
        tree = self.build(self.root(**{"organizer.type": "fixed_tree", "retrieval.type": "tree"}))
        hybrid = self.build(self.root(**{"organizer.type": "fixed_tree", "retrieval.type": "hybrid_tree_lexical"}))
        self.assertEqual(json.loads(self.session(tree).briefs())["items"], [])
        self.assertEqual(json.loads(self.session(hybrid).briefs())["items"][0]["id"], "misplaced")

    def test_whole_brief_detail_envelopes_and_repeated_reads_consume_budget(self):
        self.add("service")
        config = read_runtime(); config.update(brief_tokens=2000, detail_tokens=450)
        s = self.session(config=config)
        wire = s.briefs()
        self.assertEqual(s.events[-1]["bytes"], len(wire))
        first = s.open({"op": "open_memory", "id": "service"})
        self.assertIn("memory", json.loads(first))
        second = s.open({"op": "open_memory", "id": "service"})
        self.assertIn("error", json.loads(second))
        self.assertEqual(sum(e["tokens"] for e in s.events if e["operation"] == "open")+s.remaining["detail"]["tokens"], 450)
        self.assertTrue(all(e["remaining"]["detail"]["tokens"] >= 0 for e in s.events))

    def test_oversized_unicode_card_is_omitted_without_clipping(self):
        self.add("big", brief="服务"*230)
        config = read_runtime(); config.update(brief_tokens=200)
        s = self.session(task="服务", config=config)
        self.assertEqual(json.loads(s.briefs())["items"], [])
        self.assertEqual(s.events[0]["omissions"][0]["id"], "big")

    def test_global_and_local_deduplicate(self):
        self.add("service")
        s = self.session(self.build(self.root(**{"loader.global_briefs": 2})))
        self.assertEqual(len(json.loads(s.briefs())["items"]), 1)

    def test_disabled_arm_cannot_expand_guessed_id(self):
        self.add("service")
        s = self.session(enabled=False)
        self.assertEqual(json.loads(s.briefs())["items"], [])
        self.assertEqual(json.loads(s.open({"op": "open_memory", "id": "service"}))["error"], "undisclosed_memory_denied")

    def test_reflection_annotation_does_not_mutate_canonical_card(self):
        ref = self.add("service")
        original = self.ledger.get(ref)
        off = self.build()
        on = self.build(self.root(**{"reflection.trigger": "failure"}))
        self.assertEqual(off["projection"]["annotations"], [])
        self.assertEqual(on["projection"]["annotations"][0]["support"], ref)
        self.assertEqual(self.ledger.get(ref), original)
        self.assertNotEqual(on["projection"]["items"][0]["detail"], original["detail"])

    def test_credit_requires_open_behavior_and_independent_outcome(self):
        ref = self.add("service"); store = UsageStore(self.cards)
        result = self.evidence.register("result", "task/chain", "visible_outcome", "target", AT, "Independent verifier: success.")
        action = self.evidence.register("action", "task/chain", "tool_result", "target", AT, "Service health check executed successfully.")
        store.record("loaded", "task/chain", "target", "auto_load", ref)
        store.record("claimed", "task/chain", "target", "claimed_use", ref)
        store.record("outcome1", "task/chain", "target", "independent_outcome", outcome="success", evidence_ref=result)
        root = self.root(**{"credit.type": "outcome_ema"})
        self.assertEqual(self.build(root)["projection"]["credit_scores"], {})
        store.record("opened", "task/chain", "target2", "open", ref)
        action2 = self.evidence.register("action2", "task/chain", "tool_result", "target2", AT, "Service health check executed successfully.")
        result2 = self.evidence.register("result2", "task/chain", "visible_outcome", "target2", AT, "Independent verifier: success.")
        store.record("linked", "task/chain", "target2", "behavior_link", ref, evidence_ref=action2)
        store.record("outcome2", "task/chain", "target2", "independent_outcome", outcome="success", evidence_ref=result2)
        scores = self.build(root)["projection"]["credit_scores"]
        self.assertEqual(list(scores.values()), [0.2])
        self.assert_code("duplicate_episode_outcome", lambda: store.record("duplicate", "task/chain", "target2", "independent_outcome", outcome="failure", evidence_ref=result2))

    def test_usage_wrong_source_kind_cross_namespace_and_cutoff(self):
        ref = self.add("service"); store = UsageStore(self.cards)
        wrong = self.source("feedback")
        self.assert_code("invalid_usage_evidence_type", lambda: store.record("wrong", "task/chain", "target", "independent_outcome", outcome="success", evidence_ref=wrong))
        self.assert_code("cross_namespace_usage", lambda: store.record("cross", "task/other", "target", "open", ref))
        cutoff = self.ledger.head()
        store.record("opened", "task/chain", "target", "open", ref)
        self.assertEqual(store.visible("task/chain", cutoff), [])

    def test_rebuild_deterministic_and_never_copies_old_derived_view(self):
        self.add("service")
        root = self.root()
        a = self.build(root); b = self.build(root)
        self.assertEqual(a["projection_ref"], b["projection_ref"])
        with self.ledger.transaction():
            self.ledger.put({"kind": "index", "id": "wrong-old", "version": 1, "namespace": "task/chain", "detail": "False rule"}, "task/chain")
        self.assertEqual(self.build(root)["projection_ref"], a["projection_ref"])

    def test_persistence_failure_releases_no_content_or_budget_mutation(self):
        self.add("service")
        def fail(events): raise OSError("disk full")
        s = self.session(persist=fail)
        remaining = copy.deepcopy(s.remaining)
        with self.assertRaises(OSError): s.briefs()
        self.assertEqual(s.events, [])
        self.assertEqual(s.remaining, remaining)
        self.assertFalse(s.loaded)

    def test_request_cap_and_offline_tokenizer_cannot_claim_live(self):
        self.add("service")
        config = read_runtime(); config["max_requests"] = 1
        s = self.session(config=config); s.briefs()
        self.assert_code("runtime_request_limit", lambda: s.open({"op": "open_memory", "id": "service"}))
        config["tokenizer"]["live_qualified"] = True
        self.assert_code("offline_tokenizer_not_live", lambda: validate_runtime(config))

    def test_socket_real_client_and_forbidden_operation_metered(self):
        self.add("service")
        s = self.session(); s.briefs()
        workspace = Path(self.tmp.name)/"worker"; workspace.mkdir()
        (workspace/"open_memory.py").write_text(CLIENT)
        with RuntimeSocket(workspace, s):
            result = subprocess.run([sys.executable, str(workspace/"open_memory.py"), "service"], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("memory", json.loads(result.stdout))
            with socket.socket(socket.AF_UNIX) as connection:
                connection.connect(str(workspace/".memory.sock"))
                connection.sendall(b'{"op":"source","id":"service"}\n')
                self.assertIn("error", json.loads(connection.recv(8192)))
        self.assertFalse((workspace/".memory.sock").exists())
        self.assertEqual(len(s.events), 3)


class LegacyTests(Fixture):
    def snapshot(self):
        text = "The service health check was unavailable."
        record = {"id": "learn:n1", "version": 1, "expected_version": 0, "category": "environment/services", "level": 0,
            "brief": "Check service health.", "content": "The integration depends on service availability.",
            "conditions": "Applies when this service is required.", "limits": "Other failures need separate diagnosis.",
            "source_refs": [{"id": "event", "sha256": digest(text)}], "support_ids": [], "support_versions": {},
            "links": [], "supersedes": [], "episode_id": "learn", "sequence": 0}
        return {"adapter": "reference-v1", "records": [record], "sources": {"event": {"text": text, "sha256": digest(text)}}}

    def adapt(self, snapshot=None):
        snapshot = snapshot or self.snapshot()
        source = self.source(text=snapshot["sources"]["event"]["text"])
        return adapt_leaf(snapshot, "learn:n1", 1, {"event": source}, self.evidence, "task/chain", self.ledger.head(), AT, "failure")

    def test_original_preserved_and_applicability_limits_mapped(self):
        snapshot = self.snapshot(); artifact = self.adapt(snapshot)
        self.assertEqual(artifact["original_snapshot"], snapshot)
        raw = artifact["proposal"]["cards"][0]
        self.assertIn(snapshot["records"][0]["content"], raw["detail"])
        self.assertIn(snapshot["records"][0]["conditions"], raw["detail"])
        self.assertIn(snapshot["records"][0]["limits"], raw["detail"])
        r = self.cards.extract("migrate", artifact["proposal"], "task/chain", self.ledger.head(), AT)
        self.assertEqual(r["result"]["status"], "extracted")

    def test_abstract_or_missing_legacy_evidence_not_invented(self):
        snapshot = self.snapshot(); snapshot["records"][0]["level"] = 1
        self.assert_code("legacy_abstraction_not_canonical", lambda: self.adapt(snapshot))
        snapshot = self.snapshot(); snapshot["records"][0]["source_refs"][0]["sha256"] = "a"*64
        self.assert_code("legacy_source_hash_mismatch", lambda: self.adapt(snapshot))
