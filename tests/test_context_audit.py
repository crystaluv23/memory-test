"""Recorded-input controls; fixtures do not prove live isolation."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memorylab.context_audit import audit, policy
from memorylab.contracts import ContractError, sha256


class ContextAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "subject.jsonl"
        self.meta = {"type": "session_meta", "payload": {"id": "child", "source": {
            "subagent": {"thread_spawn": {"parent_thread_id": "parent", "agent_path": "/root/subject"}}}}}

    def message(self, text, role="developer"):
        return {"type": "response_item", "payload": {"type": "message", "role": role,
            "content": [{"type": "input_text", "text": text}]}}

    def write(self, rows):
        self.path.write_text("".join(json.dumps(r) + "\n" for r in [self.meta] + rows))

    def run_audit(self, allowed=None):
        return audit(self.path, "child", "parent", "/root/subject", allowed)

    def test_memory_in_developer_is_contamination_even_if_hash_allowlisted(self):
        row = self.message(policy()["forbidden_input_markers"][0] + " PRIVATE LESSON")
        self.write([row]); result = self.run_audit([sha256(row["payload"]["content"])])
        self.assertEqual(result["status"], "contaminated")
        self.assertEqual(result["marker_hits"][0]["line"], 2)
        self.assertNotIn("PRIVATE LESSON", json.dumps(result))

    def test_world_state_and_incoming_agent_message_are_also_checked(self):
        for row in ({"type": "world_state", "payload": {"state": {"notes": policy()["forbidden_input_markers"][1]}}},
                    {"type": "compacted", "payload": {"replacement_history": [self.message(policy()["forbidden_input_markers"][0])] }},
                    {"type": "response_item", "payload": {"type": "agent_message", "recipient": "/root/subject",
                     "content": [{"text": policy()["forbidden_input_markers"][0]}]}}):
            self.write([row]); self.assertEqual(self.run_audit()["status"], "contaminated")

    def test_no_allowlist_and_empty_recorded_inputs_never_clear(self):
        self.write([self.message("Generic instructions")])
        self.assertEqual(self.run_audit()["status"], "unverified")
        self.write([])
        self.assertIn("recorded_inputs_missing", self.run_audit([])["reasons"])

    def test_unapproved_input_is_not_a_clean_control(self):
        self.write([self.message("A prior task answer without any marker")])
        self.assertEqual(self.run_audit([])["status"], "contaminated")

    def test_encrypted_input_cannot_be_cleared_by_a_hash(self):
        row = self.message(""); row["payload"]["content"] = [{"type": "encrypted_content", "data": "opaque"}]
        self.write([row]); result = self.run_audit([sha256(row["payload"]["content"])])
        self.assertEqual(result["status"], "unverified")
        self.assertIn("encrypted_recorded_input", result["reasons"])

    def test_exact_allowlist_clears_only_recorded_scope(self):
        row = self.message("Generic instructions"); self.write([row])
        result = self.run_audit([sha256(row["payload"]["content"])])
        self.assertEqual(result["status"], "recorded_inputs_allowlisted")
        self.assertFalse(result["full_provider_context_verified"])
        self.assertFalse(result["formal_admission"])

    def test_each_session_identity_dimension_is_bound(self):
        self.write([self.message("Generic instructions")])
        for args in (("other", "parent", "/root/subject"), ("child", "other", "/root/subject"),
                     ("child", "parent", "/root/other")):
            with self.assertRaises(ContractError) as error: audit(self.path, *args)
            self.assertEqual(error.exception.code, "context_identity_mismatch")

    def usage(self, response, n):
        u = {"input_tokens": 10, "cached_input_tokens": 5, "output_tokens": 2,
             "reasoning_output_tokens": 1, "total_tokens": 12}
        return {"type": "token_usage_record", "payload": {"thread_id": "child", "response_id": response,
            "usage": u, "thread_token_usage": {k: v*n for k, v in u.items()}}}

    def test_usage_deduplicates_responses_and_does_not_claim_hard_budget(self):
        first = self.usage("r1", 1); self.write([first, first, self.usage("r2", 2)])
        result = self.run_audit()["recorded_client_usage"]
        self.assertEqual(result["unique_responses"], 2)
        self.assertEqual(result["cumulative"]["total_tokens"], 24)
        self.assertEqual(result["status"], "recorded_client_cumulative")
        self.assertIsNone(result["provider_cost"])
        self.assertFalse(result["provider_budget_qualified"])

    def test_changed_duplicate_or_reset_usage_is_unverified(self):
        first = self.usage("r1", 1); changed = copy.deepcopy(first)
        changed["payload"]["usage"]["output_tokens"] = 3
        for rows, code in (([first, changed], "changed_response_usage"),
                           ([first, self.usage("r2", 1)], "inconsistent_cumulative_usage")):
            self.write(rows); result = self.run_audit()["recorded_client_usage"]
            self.assertEqual(result["status"], "unverified"); self.assertIn(code, result["errors"])

    def test_unknown_and_foreign_usage_never_become_zero_or_valid(self):
        self.write([]); self.assertIsNone(self.run_audit()["recorded_client_usage"]["cumulative"])
        row = self.usage("r1", 1); row["payload"]["thread_id"] = "other"
        self.write([row]); self.assertEqual(self.run_audit()["recorded_client_usage"]["status"], "unverified")

    def test_partial_line_and_bounded_input_rejected(self):
        self.write([]); self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaises(ContractError): self.run_audit()
        self.write([self.message("too long")]); c = policy(); c["max_rollout_bytes"] = 1
        with patch("memorylab.context_audit.policy", return_value=c), self.assertRaises(ContractError): self.run_audit()

    def test_symlink_is_not_an_auditable_session(self):
        self.write([]); linked = self.path.with_name("linked.jsonl"); linked.symlink_to(self.path)
        with self.assertRaises(ContractError): audit(linked, "child", "parent", "/root/subject")
