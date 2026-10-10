"""Controller contracts, not evidence of model inference or memory benefit."""
import copy
import json
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from memorylab.collaboration import Loop, prepare, resolve_capture
from memorylab.contracts import ContractError, canonical, ROOT


class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "run"
        config = json.loads((ROOT / "configs/collaboration_v0.json").read_text())
        config.update(transport="controller_collaboration_tools", direct_actor_tools="forbidden_instruction_only_not_os_qualified",
                      message_delivery_policy="reserved_exact_followup_actual_tool_return_ack_no_unknown_resend")
        prepare(self.run, config)
        self.loop = Loop(self.run)

    def tearDown(self):
        self.loop.ledger.close()
        shutil.rmtree(self.loop.plan["workspace_root"], ignore_errors=True)
        shutil.rmtree(self.loop.plan["client_root"], ignore_errors=True)
        self.tmp.cleanup()

    def bound(self, actor):
        slot = actor["id"]
        self.loop.dispatch_intent(slot)
        self.loop.bind(slot, "observed-test-" + slot, {"agent_id": "observed-test-" + slot})
        return slot, "observed-test-" + slot

    def task(self):
        i = self.loop.initial()
        self.loop.phase("baseline", i["memory"], i["scenario"], 801)
        return self.loop.task("baseline", 0)

    def send(self, actor, sequence, message):
        slot = actor["id"]
        if sequence > 1 and len(self.loop._messages(slot)) == sequence - 1 and self.loop._delivery(slot, sequence - 1) is None:
            self.deliver(actor, sequence - 1)
        return self.loop.receive(slot, sequence, "observed-test-" + slot, canonical(message).decode())

    def deliver(self, actor, sequence):
        slot = actor["id"]; agent = "observed-test-" + slot
        reserved = self.loop.followup_intent(slot, sequence)
        return self.loop.acknowledge_followup(slot, sequence, agent,
                    {"agent_id": agent, "status": "explicit-fixture-tool-return-no-model"})

    def finish_broken(self):
        a = self.task(); self.bound(a)
        self.send(a, 1, {"op": "finish", "result": {"summary": "Unrepaired test candidate", "completion_claim": "complete", "memory_uses": []}})
        return a

    def seed_card(self, ns, id_, brief):
        from memorylab.collaboration import now
        source = self.loop.evidence.register("fixture-source-" + id_, ns, "explicit_feedback", "fixture-episode", now(), brief)
        card = {"id": id_, "expected_version": 0, "type_hint": "workflow/repair", "brief": brief,
                "detail": brief, "evidence_refs": [source], "outcome": "unknown", "status": "active", "supersedes": []}
        return self.loop.cards.extract("fixture-extract-" + id_, {"cards": [card]}, ns, self.loop.ledger.head(), now())["result"]["cards"][0]

    def test_revocation_blocks_open_loop_planning_binding_and_execution_without_mutation(self):
        a = self.task(); slot, agent = self.bound(a)
        (self.run / "revocation.json").write_text('{"reason":"ambient_memory_contamination"}')
        before = self.loop.ledger.head(); initial = self.loop.initial()
        operations = [lambda: self.loop.next(), lambda: self.loop.dispatch_intent(slot),
            lambda: self.loop.bind(slot, agent, {"agent_id": agent}),
            lambda: self.loop.start_intent(slot, {}), lambda: self.loop.followup_intent(slot, 1),
            lambda: self.loop.client_delivery_intent(slot, 1),
            lambda: self.loop.receive(slot, 1, agent, '{"op":"inspect"}'),
            lambda: self.loop.task("baseline", 1), lambda: self.loop.extractor("baseline", 0),
            lambda: self.loop.research("proposer-0", "proposer", initial["memory"], ["baseline"]),
            lambda: self.loop.phase("extra", initial["memory"], initial["scenario"], 1)]
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(ContractError) as error: operation()
            self.assertEqual(error.exception.code, "collaboration_execution_revoked")
            self.assertEqual(self.loop.ledger.head(), before)
        self.assertEqual(self.loop.report()["phases"][0]["planned"], 5)

    def test_revocation_preserves_real_delivery_ack_and_termination_cleanup(self):
        a = self.task(); slot, agent = self.bound(a)
        self.send(a, 1, {"op": "inspect"}); self.loop.followup_intent(slot, 1)
        (self.run / "revocation.json").write_text('{"reason":"ambient_memory_contamination"}')
        self.loop.acknowledge_followup(slot, 1, agent, {"status": "actual-fixture-return"})
        self.assertIsNotNone(self.loop._delivery(slot, 1))
        self.loop.interrupt_intent(slot, "actor_failed")
        self.loop.terminate(slot, agent, "actor_failed", {"previous_status": "running"})
        self.loop.ledger.close()
        with Loop(self.run, readonly=True) as reader:
            r = reader.report()
            self.assertEqual(r["actors"][0]["status"], "terminated")
            self.assertEqual(r["phases"][0]["planned"], 5)
            self.assertFalse(r["gain_evidence"])

    def test_claims_are_deduplicated_disclosure_bound_and_never_behavior_credit(self):
        from memorylab.runtime import UsageStore, credit_scores
        known = self.seed_card("task/baseline", "known-repair", "snapshot JSON repair transaction exact grant")
        hidden = self.seed_card("task/baseline", "unrelated-card", "polygon curvature geometry")
        a = self.task(); self.bound(a)
        self.assertEqual([i["id"] for i in a["packet"]["role_input"]["payload"]["context"]["briefs"]["items"]], [known["id"]])
        final = {"summary": "Unrepaired fixture with claims", "completion_claim": "partial",
                 "memory_uses": [known["id"], known["id"], hidden["id"], "unknown-id"]}
        self.send(a, 1, {"op": "finish", "result": final})
        done = self.loop.ledger.receipt("done-" + a["id"])["result"]
        self.assertEqual(done["memory_claims"]["claimed_without_open"], [known["id"], hidden["id"], "unknown-id"])
        self.assertEqual(done["memory_claims"]["undisclosed_memory_claims"], [hidden["id"], "unknown-id"])
        events = UsageStore(self.loop.cards).visible("task/baseline", self.loop.ledger.head())
        claims = [e for e in events if e["event_type"] == "claimed_use"]
        self.assertEqual([e["memory_ref"] for e in claims], [known])
        self.assertFalse(any(e["event_type"] == "behavior_link" for e in events))
        self.assertEqual(credit_scores(events, .2), {})
        before = self.loop.ledger.head(); self.loop.report(); self.assertEqual(before, self.loop.ledger.head())

    def test_research_actual_open_is_recorded_without_invented_outcome_or_credit(self):
        from memorylab.runtime import UsageStore, credit_scores
        known = self.seed_card("research/proposer", "research-lesson", "research proposer mechanism")
        a = self.loop.research("proposer-0", "proposer", self.loop.initial()["memory"], [])
        self.bound(a)
        self.send(a, 1, {"op": "open_memory", "id": known["id"]})
        proposal = {"schema_version": 1, "kind": "memory", "parent": self.loop.initial()["memory"],
                    "mutation": None, "hypothesis": "Explicit fixture noop without quality evidence", "evidence_refs": []}
        self.send(a, 2, {"op": "finish", "result": proposal})
        events = UsageStore(self.loop.cards).visible("research/proposer", self.loop.ledger.head())
        self.assertEqual([e["event_type"] for e in events], ["auto_load", "open"])
        self.assertTrue(all(e["memory_ref"] == known for e in events))
        self.assertEqual(credit_scores(events, .2), {})
        self.loop.report()

    def test_generated_reply_is_not_actor_delivery_and_unknown_send_is_not_repeated(self):
        a = self.task(); slot, agent = self.bound(a)
        reply = self.send(a, 1, {"op": "scenario", "request": {"op": "inspect"}})
        self.assertFalse(reply["response"]["event"]["delivered"])
        self.assertEqual(self.loop.next()["status"], "ready_to_deliver_same_agent")
        with self.assertRaisesRegex(ContractError, "collaboration_previous_reply_not_delivered"):
            self.loop.receive(slot, 2, agent, '{"op":"finish","result":{}}')
        reserved = self.loop.followup_intent(slot, 1)
        self.assertEqual(reserved["dispatch_status"], "call_followup_once")
        self.assertEqual(reserved["result"]["tool_request"], {"target": agent, "message": reply["response"]["wire"]})
        self.assertEqual(self.loop.next()["status"], "delivery_unknown_do_not_resend")
        self.assertEqual(self.loop.followup_intent(slot, 1)["dispatch_status"], "delivery_unknown_do_not_resend")
        self.deliver(a, 1)
        self.assertEqual(self.loop.next()["status"], "started")
        self.assertEqual(self.loop.followup_intent(slot, 1)["dispatch_status"], "already_acknowledged_do_not_resend")

    def test_delivery_binds_sender_original_wire_and_idempotent_tool_result(self):
        a = self.task(); slot, agent = self.bound(a)
        self.send(a, 1, {"op": "scenario", "request": {"op": "inspect"}})
        with self.assertRaisesRegex(ContractError, "collaboration_followup_not_reserved"):
            self.loop.acknowledge_followup(slot, 1, agent, {"status": "fixture"})
        self.loop.followup_intent(slot, 1)
        with self.assertRaisesRegex(ContractError, "collaboration_sender_mismatch"):
            self.loop.acknowledge_followup(slot, 1, "other", {"status": "fixture"})
        with self.assertRaisesRegex(ContractError, "invalid_collaboration_followup_result"):
            self.loop.acknowledge_followup(slot, 1, agent, {"error": "fixture send failure"})
        result = {"agent_id": agent, "status": "fixture"}
        first = self.loop.acknowledge_followup(slot, 1, agent, result); head = self.loop.ledger.head()
        self.assertEqual(self.loop.acknowledge_followup(slot, 1, agent, result), first)
        self.assertEqual(self.loop.ledger.head(), head)
        with self.assertRaisesRegex(ContractError, "idempotency_conflict"):
            self.loop.acknowledge_followup(slot, 1, agent, {"agent_id": agent, "status": "changed"})
        self.loop.report()

    def test_unacknowledged_evidence_does_not_authorize_extraction(self):
        self.finish_broken(); a = self.loop.extractor("baseline", 0); slot, agent = self.bound(a)
        row = a["packet"]["role_input"]["payload"]["context"]["sources"][-1]
        self.send(a, 1, {"op": "read_evidence", "id": row["id"], "start": 0, "count": 2048})
        self.assertEqual(self.loop._reader(a).events, [])
        with self.assertRaisesRegex(ContractError, "collaboration_previous_reply_not_delivered"):
            self.loop.receive(slot, 2, agent, '{"op":"finish","result":{"cards":[]}}')
        self.deliver(a, 1)
        self.assertEqual(len(self.loop._reader(a).events), 1)
        self.send(a, 2, {"op": "finish", "result": {"cards": []}})

    def test_expired_actor_requires_actual_interrupt_and_halts_with_denominator(self):
        a = self.task(); slot, agent = self.bound(a)
        with self.assertRaisesRegex(ContractError, "collaboration_deadline_not_reached"):
            self.loop.interrupt_intent(slot, "wall_deadline")
        with self.assertRaisesRegex(ContractError, "collaboration_interrupt_not_reserved"):
            self.loop.terminate(slot, agent, "wall_deadline", {"previous_status": "fixture_running"})
        with patch("memorylab.collaboration.now", return_value="2099-10-08T10:00:00+00:00"):
            self.assertEqual(self.loop.next()["status"], "interrupt_required")
            reserved = self.loop.interrupt_intent(slot, "wall_deadline")
            self.assertEqual(reserved["result"]["tool_request"], {"target": agent})
            self.assertEqual(self.loop.next()["status"], "interrupt_unknown_do_not_repeat")
            result = {"previous_status": "fixture_running"}
            first = self.loop.terminate(slot, agent, "wall_deadline", result)
            self.assertEqual(self.loop.terminate(slot, agent, "wall_deadline", result), first)
        report = self.loop.next()
        self.assertEqual(report["status"], "observed_run_terminated")
        self.assertEqual(report["report"]["actors"][0]["status"], "terminated")
        self.assertEqual(len(report["report"]["planned_schedule"]), 7)
        summary = report["report"]["phases"][0]
        self.assertEqual((summary["planned"], summary["completed"], summary["terminated"], summary["successes"]), (5, 0, 1, 0))
        self.assertIsNone(self.loop.ledger.receipt("done-" + slot))
        with self.assertRaisesRegex(ContractError, "collaboration_run_terminated"):
            self.loop.dispatch_intent(slot)

    def test_turn_exhaustion_stops_without_fabricated_final_or_extraction(self):
        a = self.task(); slot, agent = self.bound(a)
        for n in range(1, self.loop.config["actor_turns_max"] + 1):
            self.send(a, n, {"op": "open_memory", "id": "undisclosed"})
        self.assertEqual(self.loop.next()["interrupt_reason"], "turn_limit")
        with self.assertRaisesRegex(ContractError, "collaboration_turn_limit"):
            self.loop.followup_intent(slot, self.loop.config["actor_turns_max"])
        self.loop.interrupt_intent(slot, "turn_limit")
        self.loop.terminate(slot, agent, "turn_limit", {"previous_status": "fixture_completed_request"})
        report = self.loop.next()["report"]
        self.assertEqual(report["actors"][0]["turns"], self.loop.config["actor_turns_max"])
        self.assertFalse(report["gain_evidence"])
        self.assertEqual(len(report["actors"]), 1)
        self.assertEqual(self.loop.cards.active("task/baseline", self.loop.ledger.head()), [])

    def test_frozen_plan_and_no_provider_dispatch(self):
        report = self.loop.report()
        self.assertEqual(report["actors"], [])
        self.assertIsNone(report["provider_tokens"])
        self.assertFalse(report["formal_admission"])
        packet = self.loop.next()
        self.assertEqual(packet["slot"], "baseline-task-0")
        self.assertEqual(packet["status"], "ready_to_dispatch")
        self.assertNotIn(str(self.run), packet["prompt"])
        self.assertNotIn("expected", json.loads(packet["prompt"])["files"])

    def test_dispatch_unknown_is_not_respawn_permission(self):
        a = self.task(); self.loop.dispatch_intent(a["id"])
        self.assertEqual(self.loop.next()["status"], "dispatch_unknown_do_not_respawn")

    def test_binding_without_intent_wrong_sender_and_agent_reuse_rejected(self):
        a = self.task(); slot = a["id"]
        with self.assertRaisesRegex(ContractError, "dispatch_not_reserved"):
            self.loop.bind(slot, "test", {"agent_id": "test"})
        self.bound(a)
        with self.assertRaisesRegex(ContractError, "collaboration_sender_mismatch"):
            self.loop.receive(slot, 1, "other", '{"op":"scenario","request":{"op":"inspect"}}')
        with self.assertRaisesRegex(ContractError, "idempotency_conflict"):
            self.loop.bind(slot, "new", {"agent_id": "new"})

    def test_actual_inspect_and_exact_duplicate_does_not_run_again(self):
        a = self.task(); self.bound(a)
        message = {"op": "scenario", "request": {"op": "inspect"}}
        r = self.send(a, 1, message)
        self.assertIn("current_contract", json.loads(r["response"]["wire"])["environment"])
        with patch("memorylab.collaboration.ScenarioPolicy", side_effect=AssertionError("should not execute")):
            self.assertEqual(self.send(a, 1, message), r)
        with self.assertRaisesRegex(ContractError, "idempotency_conflict"):
            self.send(a, 1, {"op": "scenario", "request": {"op": "check"}})

    def test_engine_write_only_current_file_then_independent_failure_grade(self):
        a = self.task(); self.bound(a)
        self.send(a, 1, {"op": "write_engine", "text": "def process(c): return {}\n"})
        self.send(a, 2, {"op": "finish", "result": {"summary": "Incorrect candidate", "completion_claim": "complete", "memory_uses": []}})
        done = self.loop.ledger.receipt("done-" + a["id"])["result"]
        self.assertFalse(done["grade"]["full_success"])
        self.assertTrue(done["grade"]["false_completion"])
        self.assertEqual(done["grade"]["protected_violations"], [])
        self.assertIsNone(done["provider_cost"])
        with self.assertRaisesRegex(ContractError, "actor_already_terminal"):
            self.send(a, 3, {"op": "write_engine", "text": "def process(c): return c\n"})

    def test_full_denominator_and_failure_extraction_without_hidden_answers(self):
        self.finish_broken()
        s = self.loop.summary("baseline")
        self.assertEqual((s["planned"], s["completed"], s["successes"]), (5, 1, 0))
        a = self.loop.extractor("baseline", 0)
        self.assertNotIn("hidden_cases", a["packet"]["role_input"]["payload"]["context"])
        self.assertNotIn("production", a["packet"]["role_input"]["payload"]["context"])
        self.bound(a)
        catalog = a["packet"]["role_input"]["payload"]["context"]["sources"]
        req = {"op": "read_evidence", "id": catalog[-1]["id"], "start": 0, "count": 2048}
        reply = self.send(a, 1, req)
        visible = json.loads(reply["response"]["wire"])
        self.assertIn('"full_success":false', visible["text"])
        self.assertNotIn("groups", visible["text"])
        self.send(a, 2, {"op": "finish", "result": {"cards": []}})
        self.assertEqual(self.loop.next()["slot"], "baseline-task-1")

    def test_unread_citation_and_source_namespace_denied(self):
        self.finish_broken(); a = self.loop.extractor("baseline", 0); self.bound(a)
        source = a["internal"]["source_refs"][0]
        card = {"id": "bad", "expected_version": 0, "type_hint": "workflow/repair", "brief": "A lesson",
                "detail": "Unsupported lesson", "evidence_refs": [source], "outcome": "unknown", "status": "active", "supersedes": []}
        denied = self.send(a, 1, {"op": "finish", "result": {"cards": [card]}})
        self.assertEqual(denied["response"]["error"], "collaboration_unread_extraction_source")
        self.assertIsNone(self.loop.ledger.receipt("done-" + a["id"]))
        self.assertIsNotNone(self.loop.ledger.receipt("intent-" + a["id"] + "-1"))
        self.assertEqual(self.send(a, 1, {"op": "finish", "result": {"cards": [card]}}), denied)
        self.send(a, 2, {"op": "finish", "result": {"cards": []}})
        self.assertIsNotNone(self.loop.ledger.receipt("done-" + a["id"]))

    def test_memory_off_or_undisclosed_id_cannot_open_raw_source(self):
        a = self.task(); self.bound(a)
        r = self.send(a, 1, {"op": "open_memory", "id": "not-disclosed"})
        self.assertEqual(json.loads(r["response"]["wire"])["error"], "undisclosed_memory_denied")

    def test_sequence_gap_deadline_and_complete_workspace_drift(self):
        a = self.task(); self.bound(a)
        with self.assertRaisesRegex(ContractError, "collaboration_sequence_gap"):
            self.send(a, 2, {"op": "scenario", "request": {"op": "inspect"}})
        with patch("memorylab.collaboration.now", return_value="2099-10-08T10:00:00+00:00"):
            with self.assertRaisesRegex(ContractError, "collaboration_wall_deadline"):
                self.send(a, 1, {"op": "scenario", "request": {"op": "inspect"}})
        self.send(a, 1, {"op": "finish", "result": {"summary": "Still broken", "completion_claim": "partial", "memory_uses": []}})
        (self.loop.workspace(a) / "engine.py").write_text("tampered\n")
        with self.assertRaisesRegex(ContractError, "completed_collaboration_workspace_drift"):
            self.loop.report()

    def test_capture_identity_is_bound_to_actual_raw_and_parent(self):
        a = self.finish_broken()
        capture = self.loop.ledger.receipt("done-" + a["id"])["result"]["capture"]
        self.assertEqual(resolve_capture(self.loop.ledger, capture)["actor"], a["ref"])
        self.assertEqual(self.loop.next()["slot"], "baseline-extract-0")

    def test_controller_crash_preserves_actual_raw_and_refuses_resend(self):
        a = self.task(); self.bound(a)
        message = {"op": "finish", "result": {"summary": "Interrupted controller", "completion_claim": "partial", "memory_uses": []}}
        raw = canonical(message).decode()
        with patch.object(self.loop, "_finish", side_effect=RuntimeError("controller-crash")):
            with self.assertRaisesRegex(RuntimeError, "controller-crash"):
                self.send(a, 1, message)
        pending = self.loop.next()
        self.assertEqual(pending["status"], "needs_attention_unsealed_response")
        self.assertEqual(pending["unsealed_response"]["raw"], raw)
        with self.assertRaisesRegex(ContractError, "unsealed_collaboration_message_needs_attention"):
            self.send(a, 1, message)

    def test_malformed_actor_json_kept_and_repair_does_not_rewrite_history(self):
        a = self.task(); slot, agent = self.bound(a)
        first = self.loop.receive(slot, 1, agent, "not JSON")
        self.assertEqual(first["raw"], "not JSON")
        self.assertEqual(first["response"]["status"], "rejected_request_no_semantic_commit")
        self.send(a, 2, {"op": "finish", "result": {"summary": "Valid partial", "completion_claim": "partial", "memory_uses": []}})
        self.assertEqual(self.loop._messages(slot)[0]["raw"], "not JSON")

    def test_worker_link_is_failed_without_parent_read_of_target(self):
        a = self.task(); self.bound(a)
        ws = self.loop.workspace(a)
        private = Path(self.tmp.name) / "private-marker"
        private.write_text("PRIVATE_CONTENT_MUST_NOT_BE_COLLECTED")
        (ws / "engine.py").unlink(); (ws / "engine.py").symlink_to(private)
        self.send(a, 1, {"op": "finish", "result": {"summary": "Invalid filesystem", "completion_claim": "complete", "memory_uses": []}})
        done = self.loop.ledger.receipt("done-" + a["id"])["result"]
        self.assertFalse(done["grade"]["full_success"])
        self.assertIsNotNone(done["workspace_snapshot"]["boundary_error"])
        texts = [self.loop.evidence.read(ref, "extractor", "task/baseline", self.loop.ledger.head())["text"] for ref in done["sources"]]
        self.assertNotIn("PRIVATE_CONTENT_MUST_NOT_BE_COLLECTED", "".join(texts))
        self.assertEqual(self.loop.report()["actors"][0]["status"], "completed")

    def test_unplanned_phase_and_early_research_rejected(self):
        i = self.loop.initial()
        with self.assertRaisesRegex(ContractError, "unplanned_collaboration_phase"):
            self.loop.phase("another-budget", i["memory"], i["scenario"], 801)
        self.task()
        with self.assertRaisesRegex(ContractError, "incomplete_research_input"):
            self.loop.research("proposer-0", "proposer", i["memory"], ["baseline"])

    def test_correct_submission_passes_actual_registered_workspace_and_check(self):
        from test_scenario import CANDIDATES
        a = self.task(); self.bound(a); ws = self.loop.workspace(a)
        self.assertFalse(ws.is_relative_to(self.run))
        self.assertNotIn(str(ws), self.loop.prompt(a["id"]))
        self.send(a, 1, {"op": "write_engine", "text": CANDIDATES[a["internal"]["episode"]["backend"]]})
        smoke = self.send(a, 2, {"op": "scenario", "request": {"op": "check"}})
        self.assertTrue(json.loads(smoke["response"]["wire"])["public_smoke_passed"])
        self.send(a, 3, {"op": "finish", "result": {"summary": "Independently authored reference candidate", "completion_claim": "complete", "memory_uses": []}})
        done = self.loop.ledger.receipt("done-" + a["id"])["result"]
        self.assertTrue(done["grade"]["full_success"])
        self.assertTrue(self.loop.report()["actors"][0]["result"]["grade"]["full_success"])

    def test_actual_worker_grader_denies_control_and_sibling_even_for_correct_code(self):
        from test_scenario import CANDIDATES
        a = self.task(); self.bound(a); ws = self.loop.workspace(a)
        controller = self.run / "controller-marker"; controller.write_text("CONTROL_PRIVATE_MARKER")
        sibling = Path(self.loop.plan["workspace_root"]) / "other-subject"; sibling.mkdir()
        marker = sibling / "marker"; marker.write_text("OTHER_PRIVATE_MARKER")
        code = 'import pathlib\nfor p in '+repr([str(controller), str(marker)])+':\n try:\n  pathlib.Path(p).read_text()\n  raise RuntimeError("private path was readable")\n except PermissionError:\n  pass\n' + CANDIDATES[a["internal"]["episode"]["backend"]]
        self.send(a, 1, {"op": "write_engine", "text": code})
        self.send(a, 2, {"op": "finish", "result": {"summary": "Private read negative controls", "completion_claim": "complete", "memory_uses": []}})
        done = self.loop.ledger.receipt("done-" + a["id"])["result"]
        self.assertTrue(done["grade"]["full_success"])
        self.assertNotIn("CONTROL_PRIVATE_MARKER", done["grade"]["production"]["stdout"])
        self.assertNotIn("OTHER_PRIVATE_MARKER", done["grade"]["production"]["stdout"])

    def test_complete_bounded_schedule_actual_services_noop_and_generated_index(self):
        """Scripted role text only inside a test; actual graders remain negative."""
        seen = []
        while True:
            pending = self.loop.next()
            if pending["status"] == "observed_schedule_complete_not_qualified": break
            a = self.loop._actor(pending["slot"]); self.bound(a); seen.append(a["id"])
            role = a["internal"]["role"]; n = 1
            if role == "solver":
                result = {"summary": "Unrepaired control", "completion_claim": "partial", "memory_uses": []}
            elif role == "extractor":
                context = a["packet"]["role_input"]["payload"]["context"]
                row = context["sources"][-1]
                self.send(a, n, {"op": "read_evidence", "id": row["id"], "start": 0, "count": 2048}); n += 1
                result = {"cards": [{"id": "", "expected_version": 0, "type_hint": "workflow/repair",
                          "brief": "An observed unrepaired task", "detail": "Outcome is failure; this is no repair recommendation.",
                          "evidence_refs": [row["ref"]], "outcome": "failure", "status": "active", "supersedes": []}]}
            elif role in ("consolidation", "reflector"):
                p = a["internal"]["derived_input"]["payload"]
                result = {"indices": [{"type_hint": "root", "brief": "Navigate failure evidence", "detail": "Read each supported card; no repair is established.",
                         "support_refs": [c["ref"] for c in p["cards"][:2]]}]} if role == "consolidation" else {"annotations": []}
            else:
                c = a["packet"]["role_input"]["payload"]["context"]
                mutation = {"field": "consolidation.type", "old": "none", "new": "single_index"} if a["id"] == "proposer-0" else None
                result = {"schema_version": 1, "kind": "memory" if role == "proposer" else "scenario",
                          "parent": c["parent"], "mutation": mutation,
                          "hypothesis": "A structural test, without a quality claim.", "evidence_refs": []}
            self.send(a, n, {"op": "finish", "result": result})
        self.assertIn("candidate-0-consolidation-2", seen)
        self.assertIn("proposer-1-extract", seen)
        report = pending["report"]
        self.assertEqual(len(report["phases"]), 4)
        self.assertTrue(all(s["successes"] == 0 and s["completed"] == s["planned"] for s in report["phases"]))
        self.assertFalse(pending["delivery_ready"])
        self.assertFalse(report["gain_evidence"])
        self.assertEqual(self.loop.next()["status"], "observed_schedule_complete_not_qualified")
        # A target outcome cannot shift the frozen projection used by its counterexample.
        target = self.loop._actor("candidate-0-task-3")["internal"]
        counter = self.loop._actor("candidate-0-task-4")["internal"]
        self.assertEqual(target["cutoff"], counter["cutoff"])
        self.assertEqual(target["projection"], counter["projection"])

    def test_readonly_report_does_not_change_database_bytes(self):
        a = self.finish_broken()
        self.loop.ledger.close()
        before = (self.run / "ledger.sqlite").read_bytes()
        with Loop(self.run, readonly=True) as reader:
            report = reader.report()
        self.assertEqual(report["actors"][0]["status"], "completed")
        self.assertEqual(before, (self.run / "ledger.sqlite").read_bytes())
        self.loop = Loop(self.run)
