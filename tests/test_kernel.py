import copy
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from memorylab.cards import CanonicalCards, EvidenceStore
from memorylab.contracts import (ContractError, canonical, mutation_candidate, read_kernel,
                                 sha256, validate_kernel, validate_spec)
from memorylab.ledger import Ledger
from memorylab.registry import Registry

AT = "2026-10-08T10:00:00+08:00"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.db_path)
        self.kernel = read_kernel()
        self.registry = Registry(self.ledger, self.kernel)
        self.evidence = EvidenceStore(self.ledger, self.kernel)
        self.cards = CanonicalCards(self.evidence)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def source(self, id_="first", ns="task/chain", text="A service dependency was unavailable; checking its health revealed the failure.", visibility="observable", at=AT):
        return self.evidence.register(id_, ns, "explicit_feedback", "learn", at, text, visibility)

    def raw_card(self, refs=None, **changes):
        value = {"id": "", "expected_version": 0, "type_hint": "environment/services",
                 "brief": "Check the service health when this integration fails.",
                 "detail": "In this service environment the health check was unavailable. The rule only applies to integrations that depend on this service; other failures require separate diagnosis.",
                 "evidence_refs": refs or [self.source()], "outcome": "failure", "status": "active", "supersedes": []}
        return dict(value, **changes)

    def extract(self, raw=None, request="extract1", cutoff=None, ns="task/chain", at=AT):
        raw = self.raw_card() if raw is None else raw
        return self.cards.extract(request, {"cards": [raw]}, ns,
            self.ledger.head() if cutoff is None else cutoff, at)

    def proposal(self, parent, field="organizer.type", value="fixed_tree", **changes):
        spec = self.registry.resolve(parent)
        result = {"schema_version": 1, "kind": parent["kind"], "parent": parent,
                  "mutation": {"field": field, "old": spec["config"].get(field), "new": value},
                  "hypothesis": "Test whether this single organization change affects retrieval.", "evidence_refs": []}
        result.update(changes)
        return result

    def propose(self, proposal, id_="proposal1", role="proposer", cutoff=None):
        return self.registry.propose(id_, proposal, role,
            self.ledger.head() if cutoff is None else cutoff, self.cards.proposal_evidence)

    def assert_code(self, code, callback):
        with self.assertRaises(ContractError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)


class TypeAndRegistryTests(Fixture):
    def test_recursive_and_cyclic_json_rejected(self):
        loop = []; loop.append(loop)
        with self.assertRaises(ContractError): canonical(loop)

    def test_unsupported_scenario_and_verifier_registry_rejected(self):
        for key, change in (("scenario_fields", {"context_profile": ["clean", "fake"]}),
                            ("verifier_mapping", {"feedback_retention": "anything"})):
            altered = copy.deepcopy(self.kernel); altered[key].update(change)
            with self.assertRaises(ContractError): validate_kernel(altered)

    def test_default_contract_and_roots(self):
        m = self.registry.register_root("memory", "baseline")
        e = self.registry.register_root("scenario", "first")
        self.assertEqual(self.registry.resolve(m)["config"], self.kernel["memory_initial"])
        self.assertEqual(self.registry.resolve(e)["config"], self.kernel["scenario_initial"])
        self.assertEqual(self.ledger.audit()["objects"], 3)

    def test_single_mutation_is_deterministic_and_records_receipt(self):
        root = self.registry.register_root("memory", "baseline")
        proposal = self.proposal(root)
        before = copy.deepcopy(self.registry.resolve(root))
        result = self.propose(proposal)
        again = self.propose(proposal, "proposal2")
        child = self.registry.resolve(result["result"]["candidate"])
        self.assertEqual(result["result"]["candidate"], again["result"]["candidate"])
        self.assertEqual([k for k in child["config"] if child["config"][k] != before["config"][k]], ["organizer.type"])
        self.assertEqual(self.registry.resolve(root), before)
        self.assertEqual(self.ledger.audit()["receipts"], 2)

    def test_scenario_cannot_change_family_or_verifier(self):
        root = self.registry.register_root("scenario", "first")
        for index, field in enumerate(("family", "verifier", "model", "budget", "seed")):
            p = self.proposal(root, field, "changed")
            r = self.propose(p, "case" + str(index), role="evaluator")
            self.assertEqual(r["result"]["code"], "unregistered_field")
        self.assertEqual(len(self.ledger.objects("scenario", "control")), 1)

    def test_scenario_valid_mutation_preserves_family(self):
        root = self.registry.register_root("scenario", "first")
        p = self.proposal(root, "transfer_distance", "far")
        r = self.propose(p, role="evaluator")
        child = self.registry.resolve(r["result"]["candidate"])
        self.assertEqual(child["config"]["family"], self.kernel["scenario_initial"]["family"])

    def test_wrong_role_rejected_and_retained(self):
        root = self.registry.register_root("memory", "baseline")
        r = self.propose(self.proposal(root), role="evaluator")
        self.assertEqual(r["result"]["code"], "role_kind_mismatch")
        self.assertEqual(len(self.ledger.events("research/evaluator")), 1)

    def test_multiple_fields_and_metadata_are_rejected(self):
        root = self.registry.register_root("memory", "baseline")
        for index, extra in enumerate(("config", "metadata", "model", "permission", "second_mutation")):
            p = self.proposal(root); p[extra] = {"retrieval.type": "tree"}
            r = self.propose(p, "case" + str(index))
            self.assertEqual(r["result"]["code"], "invalid_proposal_fields")
        self.assertEqual(len(self.ledger.objects("memory", "control")), 1)

    def test_invalid_mutation_fields_rejected(self):
        root = self.registry.register_root("memory", "baseline")
        p = self.proposal(root); p["mutation"]["also"] = "credit.type"
        self.assertEqual(self.propose(p)["result"]["code"], "invalid_mutation_fields")

    def test_bool_float_and_unregistered_values_cannot_pass_enum(self):
        root = self.registry.register_root("memory", "baseline")
        for i, value in enumerate((True, False, 2.0, 3, "2", None)):
            p = self.proposal(root, "loader.global_briefs", value)
            self.assertEqual(self.propose(p, "case"+str(i))["result"]["code"], "unregistered_value")

    def test_parent_old_value_type_must_match(self):
        root = self.registry.register_root("memory", "baseline")
        for i, value in enumerate((False, 0.0, "0", None)):
            p = self.proposal(root, "loader.global_briefs", 2); p["mutation"]["old"] = value
            self.assertEqual(self.propose(p, "case"+str(i))["result"]["code"], "stale_field")

    def test_hash_and_version_drift_rejected(self):
        root = self.registry.register_root("memory", "baseline")
        for i, (field, value) in enumerate((("sha256", "a"*64), ("version", 2), ("namespace", "task/chain"))):
            p = self.proposal(root); p["parent"] = dict(root, **{field: value})
            self.assertEqual(self.propose(p, "case"+str(i))["result"]["status"], "rejected")

    def test_incompatible_tree_retrieval_is_rejected(self):
        root = self.registry.register_root("memory", "baseline")
        p = self.proposal(root, "retrieval.type", "tree")
        self.assertEqual(self.propose(p)["result"]["code"], "incompatible_components")

    def test_null_and_same_value_noops_recorded_without_child(self):
        root = self.registry.register_root("memory", "baseline")
        for i, mutation in enumerate((None, {"field": "organizer.type", "old": "flat", "new": "flat"})):
            r = self.propose(self.proposal(root, mutation=mutation), "case"+str(i))
            self.assertEqual(r["result"]["status"], "noop")
        self.assertEqual(len(self.ledger.objects("memory", "control")), 1)
        self.assertEqual(self.ledger.audit()["receipts"], 2)

    def test_evidence_is_checked_even_for_noop(self):
        root = self.registry.register_root("memory", "baseline")
        source = self.source("hidden", "research/proposer", visibility="controller_only")
        p = self.proposal(root, mutation=None, evidence_refs=[source])
        self.assertEqual(self.propose(p)["result"]["code"], "hidden_source_denied")

    def test_kernel_is_bound_to_ledger(self):
        altered = copy.deepcopy(self.kernel); altered["limits"]["card_brief_bytes"] += 1
        self.assert_code("immutable_object_conflict", lambda: Registry(self.ledger, altered))

    def test_unregistered_new_primitive_rejected(self):
        altered = copy.deepcopy(self.kernel); altered["memory_fields"]["retrieval.type"].append("run_python")
        self.assert_code("unsupported_primitive_registry", lambda: validate_kernel(altered))

    def test_nan_infinity_non_json_and_surrogates_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf"), (1,), {1: "key"}, {"text": "\ud800"}):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ContractError): canonical(value)

    def test_duplicate_json_keys_rejected(self):
        from memorylab.kernel_cli import read_json
        path = Path(self.tmp.name)/"duplicate.json"; path.write_text('{"x":1,"x":2}')
        self.assert_code("duplicate_json_key", lambda: read_json(path))

    def test_bad_ids_cannot_be_paths(self):
        for value in ("../x", "a/b", "a:b", "", "-x", "a"*97, "名字"):
            with self.assertRaises(ContractError): self.registry.register_root("memory", value)

    def test_returned_value_does_not_mutate_ledger(self):
        ref = self.registry.register_root("memory", "baseline")
        spec = self.registry.resolve(ref); spec["config"]["credit.type"] = "outcome_ema"
        self.assertEqual(self.registry.resolve(ref)["config"]["credit.type"], "none")

    def test_request_idempotency_binds_role_cutoff_and_content(self):
        root = self.registry.register_root("memory", "baseline")
        p = self.proposal(root)
        first = self.propose(p, cutoff=0)
        self.assertEqual(first, self.propose(p, cutoff=0))
        p["hypothesis"] += " different"
        self.assert_code("idempotency_conflict", lambda: self.propose(p, cutoff=0))
        self.assertEqual(len(self.ledger.objects("memory", "control")), 2)

    def test_failure_after_child_before_receipt_rolls_back(self):
        root = self.registry.register_root("memory", "baseline")
        with patch.object(self.ledger, "commit_receipt", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError): self.propose(self.proposal(root))
        self.assertEqual(len(self.ledger.objects("memory", "control")), 1)
        self.assertEqual(self.ledger.head(), 0)
        self.assertIsNone(self.ledger.receipt("proposal1"))

    def test_generic_store_requires_transaction_and_is_immutable(self):
        value = {"kind": "object", "id": "sample", "version": 1, "value": 1}
        self.assert_code("transaction_required", lambda: self.ledger.put(value, "control"))
        with self.ledger.transaction(): ref = self.ledger.put(value, "control")
        with self.ledger.transaction(): self.assertEqual(ref, self.ledger.put(value, "control"))
        with self.assertRaises(ContractError):
            with self.ledger.transaction(): self.ledger.put(dict(value, value=2), "control")
        self.assertEqual(self.ledger.get(ref), value)

    def test_sqlite_corruption_detected_on_read(self):
        ref = self.registry.register_root("memory", "baseline")
        self.ledger.db.execute("UPDATE objects SET payload='{}' WHERE id='baseline'")
        self.assert_code("corrupt_ledger_hash", lambda: self.registry.resolve(ref))

    def test_concurrent_same_root_is_one_immutable_object(self):
        errors = []
        def worker():
            try:
                with Ledger(self.db_path) as ledger:
                    Registry(ledger).register_root("memory", "same")
            except BaseException as exc: errors.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(self.ledger.objects("memory", "control")), 1)

    def test_cli_init_root_audit_does_not_call_model(self):
        db = Path(self.tmp.name)/"cli.sqlite"
        for tail in (["init"], ["root", "--kind", "memory", "--id", "cliRoot"], ["audit"]):
            r = subprocess.run([sys.executable, "-m", "memorylab", "kernel", "--db", str(db)] + tail,
                               capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, r.stderr)
            result = json.loads(r.stdout)
            if "live_model_calls" in result: self.assertEqual(result["live_model_calls"], 0)


class EvidenceAndCardTests(Fixture):
    def test_empty_corpus_obeys_permissions_and_future_cutoff(self):
        self.assert_code("future_cutoff", lambda: self.cards.corpus("task/chain", 1))
        self.assert_code("controller_namespace_denied", lambda: self.cards.active("control", 0))

    def test_copying_actual_sequence_does_not_forge_source_identity(self):
        ref = self.source()
        raw = self.ledger.get(ref); raw["id"] = "fake"
        with self.ledger.transaction(): fake = self.ledger.put(raw, "task/chain")
        self.assert_code("source_event_binding_mismatch", lambda: self.evidence.read(fake, "extractor", "task/chain", self.ledger.head()))

    def test_maximum_request_id_fits_derived_source_id(self):
        ref = self.source("x"*96)
        self.assertLessEqual(len(ref["id"]), 96)
        self.evidence.read(ref, "extractor", "task/chain", self.ledger.head())

    def test_source_hash_and_visible_read(self):
        ref = self.source()
        s = self.evidence.read(ref, "extractor", "task/chain", self.ledger.head())
        self.assertEqual(s["event_type"], "explicit_feedback")
        self.assertEqual(len(self.evidence.visible("extractor", "task/chain", self.ledger.head())), 1)

    def test_source_solver_hidden_namespace_and_cutoff_boundaries(self):
        ref = self.source()
        hidden = self.source("hidden", visibility="controller_only")
        for code, fn in (
            ("source_access_denied", lambda: self.evidence.read(ref, "solver", "task/chain", self.ledger.head())),
            ("future_source_denied", lambda: self.evidence.read(ref, "extractor", "task/chain", 0)),
            ("cross_namespace_source", lambda: self.evidence.read(ref, "extractor", "task/other", self.ledger.head())),
            ("hidden_source_denied", lambda: self.evidence.read(hidden, "extractor", "task/chain", self.ledger.head())),
            ("source_access_denied", lambda: self.evidence.read(ref, "evaluator", "task/chain", self.ledger.head())),
            ("future_cutoff", lambda: self.evidence.read(ref, "extractor", "task/chain", self.ledger.head()+1))):
            self.assert_code(code, fn)

    def test_research_namespace_not_shared(self):
        ref = self.source("research", "research/evaluator")
        self.assertEqual(self.evidence.read(ref, "evaluator", "research/evaluator", self.ledger.head())["text"], self.ledger.get(ref)["text"])
        self.assert_code("cross_namespace_source", lambda: self.evidence.read(ref, "proposer", "research/proposer", self.ledger.head()))

    def test_source_registration_idempotent_and_conflicting_text_rejected(self):
        first = self.source()
        self.assertEqual(first, self.source())
        self.assert_code("idempotency_conflict", lambda: self.source(text="Different source"))
        self.assertEqual(len(self.ledger.objects("source", "task/chain")), 1)

    def test_source_hash_corruption_and_fake_binding_rejected(self):
        ref = self.source()
        raw = self.ledger.get(ref); raw["id"] = "fake"; raw["sequence"] = self.ledger.head()
        with self.ledger.transaction(): fake = self.ledger.put(raw, "task/chain")
        self.assert_code("source_event_binding_mismatch", lambda: self.evidence.read(fake, "extractor", "task/chain", self.ledger.head()))
        raw["text"] = "altered"
        self.assert_code("source_text_hash_mismatch", lambda: self.evidence.validate_source(raw))

    def test_invalid_source_timestamps_and_control_visibility(self):
        for at in ("2026-10-08", "bad", "2026-10-08T10:00:00"):
            with self.assertRaises(ContractError): self.source(at=at)
        self.assert_code("control_source_visible", lambda: self.source(ns="control"))
        self.assertEqual(len(self.ledger.objects("source", "task/chain")), 0)

    def test_cards_committed_atomically_with_receipt_and_old_reads(self):
        r = self.extract()
        ref = r["result"]["cards"][0]
        card = self.cards.read(ref, "extractor", "task/chain", self.ledger.head())
        self.assertEqual(card["version"], 1)
        self.assertEqual(r["result"]["semantic_validation"], "requires_independent_review")
        self.assertEqual(len(self.cards.active("task/chain", self.ledger.head())), 1)

    def test_extraction_future_commit_not_visible_at_old_cutoff(self):
        raw = self.raw_card(); cutoff = self.ledger.head()
        ref = self.extract(raw)["result"]["cards"][0]
        self.assert_code("future_or_uncommitted_card", lambda: self.cards.read(ref, "extractor", "task/chain", cutoff))
        self.assertEqual(self.cards.active("task/chain", cutoff), [])

    def test_stale_extraction_cutoff_rejected(self):
        raw = self.raw_card(); cutoff = self.ledger.head()
        self.extract(raw, "firstExtraction")
        r = self.extract(raw, "later", cutoff=cutoff)
        self.assertEqual(r["result"]["code"], "stale_extraction_cutoff")

    def test_bad_second_card_leaves_no_partial_card(self):
        first = self.raw_card(); bad = dict(first, brief="")
        r = self.cards.extract("batch", {"cards": [first,bad]}, "task/chain", self.ledger.head(), AT)
        self.assertEqual(r["result"]["status"], "rejected")
        self.assertEqual(self.ledger.objects("card", "task/chain"), [])
        self.assertEqual(self.ledger.audit()["receipts"], 2)

    def test_invalid_card_types_counts_and_extra_fields(self):
        raw = self.raw_card()
        changes = ({"expected_version": True}, {"expected_version": 0.0}, {"type_hint": "hidden/answers"},
                   {"outcome": True}, {"status": "approved"}, {"brief": "x"*1401}, {"detail": "x"*12001},
                   {"model": "override"}, {"evidence_refs": []})
        for i, change in enumerate(changes):
            r = self.extract(dict(raw, **change), "case"+str(i))
            self.assertEqual(r["result"]["status"], "rejected", change)
        self.assertEqual(self.ledger.objects("card", "task/chain"), [])

    def test_hidden_cross_namespace_future_source_reject_extraction(self):
        normal = self.source()
        hidden = self.source("hidden", visibility="controller_only")
        other = self.source("other", ns="task/other")
        for i, (ref, cutoff, code) in enumerate(((hidden,self.ledger.head(),"hidden_source_denied"),
                (other,self.ledger.head(),"cross_namespace_card"), (normal,0,"future_source_denied"))):
            raw = self.raw_card([ref])
            r = self.extract(raw, "case"+str(i), cutoff=cutoff)
            self.assertEqual(r["result"]["code"], code)

    def test_card_revision_and_supersession_preserve_history(self):
        first_raw = self.raw_card(id="lesson")
        first = self.extract(first_raw)["result"]["cards"][0]
        revised_raw = dict(first_raw, expected_version=1, brief="Revised applicability.")
        second = self.extract(revised_raw, "revision")["result"]["cards"][0]
        third_raw = dict(first_raw, id="replacement", supersedes=[second])
        third = self.extract(third_raw, "supersede")["result"]["cards"][0]
        self.assertEqual([r for r,_ in self.cards.active("task/chain", self.ledger.head())], [third])
        self.assertEqual(self.cards.read(first, "extractor", "task/chain", self.ledger.head())["brief"], first_raw["brief"])
        self.assertEqual(self.cards.read(second, "extractor", "task/chain", self.ledger.head())["brief"], revised_raw["brief"])

    def test_withdrawn_cards_not_active(self):
        raw = self.raw_card(id="lesson")
        self.extract(raw)
        r = self.extract(dict(raw,expected_version=1,status="withdrawn"), "withdraw")
        self.assertEqual(r["result"]["status"], "extracted")
        self.assertEqual(self.cards.active("task/chain",self.ledger.head()), [])

    def test_duplicate_supersession_and_revision_conflict_atomic(self):
        raw = self.raw_card(id="lesson")
        first = self.extract(raw)["result"]["cards"][0]
        a = dict(raw,id="replaceA",supersedes=[first]); b = dict(a,id="replaceB")
        r = self.cards.extract("batch", {"cards":[a,b]}, "task/chain", self.ledger.head(), AT)
        self.assertEqual(r["result"]["code"], "duplicate_batch_supersession")
        b = dict(raw,expected_version=1)
        r = self.cards.extract("batch2", {"cards":[a,b]}, "task/chain", self.ledger.head(), AT)
        self.assertEqual(r["result"]["code"], "revision_supersession_conflict")
        self.assertEqual(len(self.ledger.objects("card","task/chain")),1)

    def test_inactive_or_stale_revision_cannot_reanimate_card(self):
        raw=self.raw_card(id="lesson"); first=self.extract(raw)["result"]["cards"][0]
        self.extract(dict(raw,id="replacement",supersedes=[first]),"replace")
        self.assertEqual(self.extract(dict(raw,expected_version=1),"revive")["result"]["code"], "inactive_card_revision")
        self.assertEqual(self.extract(raw,"stale")["result"]["code"], "stale_card_version")

    def test_future_timestamp_and_time_regression_rejected(self):
        source=self.source(at="2026-10-09T00:00:00Z")
        r=self.extract(self.raw_card([source]),at=AT)
        self.assertEqual(r["result"]["code"],"future_source_timestamp")
        source2=self.source("older",at="2026-10-07T00:00:00Z")
        raw=self.raw_card([source2],id="lesson")
        self.extract(raw,"new")
        r=self.extract(dict(raw,expected_version=1),"old",at="2026-10-07T10:00:00Z")
        self.assertEqual(r["result"]["code"],"card_timestamp_regression")

    def test_crash_after_cards_before_receipt_rolls_back(self):
        raw=self.raw_card(); head=self.ledger.head()
        with patch.object(self.ledger,"commit_receipt",side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError): self.extract(raw)
        self.assertEqual(self.ledger.objects("card","task/chain"),[])
        self.assertEqual(self.ledger.head(),head)
        self.assertIsNone(self.ledger.receipt("extract1"))

    def test_empty_extraction_is_valid_and_idempotent(self):
        r=self.cards.extract("empty",{"cards":[]},"task/chain",0,AT)
        self.assertEqual(r["result"]["status"],"extracted")
        self.assertEqual(self.cards.extract("empty",{"cards":[]},"task/chain",0,AT),r)
        self.assertEqual(self.ledger.objects("card","task/chain"),[])

    def test_corpus_is_snapshot_bound_not_mutable_view(self):
        first=self.extract()["result"]["cards"][0]; cutoff=self.ledger.head()
        c1=self.cards.corpus("task/chain",cutoff)
        source=self.source("second",text="Independent later event.")
        self.extract(self.raw_card([source]),"secondExtract")
        self.assertEqual(self.cards.corpus("task/chain",cutoff),c1)
        self.assertNotEqual(self.cards.corpus("task/chain",self.ledger.head())["sha256"],c1["sha256"])
        c1["cards"][0]["card"]["brief"]="Mutated returned data"
        self.assertNotEqual(self.cards.read(first,"extractor","task/chain",self.ledger.head())["brief"],c1["cards"][0]["card"]["brief"])

    def test_research_proposal_has_time_bound_evidence(self):
        source=self.source("research","research/proposer")
        raw=self.raw_card([source])
        ref=self.extract(raw,ns="research/proposer")["result"]["cards"][0]
        root=self.registry.register_root("memory","baseline")
        r=self.propose(self.proposal(root,evidence_refs=[ref]))
        self.assertEqual(r["result"]["status"],"accepted")


if __name__ == "__main__":
    unittest.main()
