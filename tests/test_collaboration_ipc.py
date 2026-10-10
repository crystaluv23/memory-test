"""Real local processes with explicit fixture JSON, never model inference."""
import hashlib
import json
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from memorylab.collaboration import Loop, prepare
from memorylab.collaboration_ipc import CLIENT, receive_frame
from memorylab.contracts import ROOT, ContractError, canonical


class IPCServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.run = Path(self.temp.name) / "run"
        prepare(self.run)
        with Loop(self.run) as loop:
            self.pending = loop.next(); self.slot = self.pending["slot"]; self.actor = loop._actor(self.slot)
            loop.dispatch_intent(self.slot)
            loop.bind(self.slot, "explicit-ipc-fixture", {"agent_id": "explicit-ipc-fixture", "model_calls": 0})
            ready = {"agent_name": "explicit-ipc-fixture", "agent_status": {"completed": canonical({"status": "awaiting_controller_binding", "packet_sha256": self.actor["packet_sha256"]}).decode()}}
            loop.start_intent(self.slot, ready)
            loop.acknowledge_start(self.slot, "explicit-ipc-fixture", "")
            self.plan = loop.plan
        self.client = self.actor["packet"]["service_client"]["command"]
        self.process = subprocess.Popen([sys.executable, "-B", "-m", "memorylab.collaboration_ipc", "--run", str(self.run),
                                         "--slot", self.slot], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ready, _, _ = select.select([self.process.stdout], [], [], 5)
        self.assertTrue(ready, "service startup timeout")
        line = self.process.stdout.readline()
        self.assertEqual(json.loads(line)["status"], "role_service_ready")

    def tearDown(self):
        if getattr(self, "process", None):
            self.process.terminate()
            try: self.process.communicate(timeout=5)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.communicate()
        shutil.rmtree(self.plan["workspace_root"], ignore_errors=True)
        shutil.rmtree(self.plan["client_root"], ignore_errors=True)
        self.temp.cleanup()

    def request(self, raw):
        return subprocess.run(self.client, input=raw.encode(), capture_output=True, timeout=10, cwd="/tmp")

    def test_ready_bind_go_prevents_early_service_and_never_resends_unknown_start(self):
        with Loop(self.run) as loop:
            i = loop.initial(); loop.phase("candidate-0", i["memory"], i["scenario"], 801)
            a = loop.task("candidate-0", 0); slot = a["id"]; target = "second-start-fixture"
            d = loop.dispatch_intent(slot)
            boot = json.loads(d["result"]["tool_request"]["message"])
            self.assertIn("Do not call any tools yet", boot["instruction"])
            self.assertEqual(boot["ready_result"], {"status": "awaiting_controller_binding", "packet_sha256": a["packet_sha256"]})
            loop.bind(slot, target, {"agent_id": target, "model_calls": 0})
            self.assertEqual(loop._pending(a)["status"], "start_required")
            with self.assertRaisesRegex(ContractError, "collaboration_start_not_reserved"):
                loop.receive(slot, 1, target, '{"op":"packet"}')
            proof = {"agent_name": target, "agent_status": {"completed": canonical(boot["ready_result"]).decode()}}
            wrong = {"agent_name": "other-role", "agent_status": proof["agent_status"]}
            with self.assertRaisesRegex(ContractError, "collaboration_ready_sender_mismatch"): loop.start_intent(slot, wrong)
            wrong = {"agent_name": target, "agent_status": {"completed": '{"status":"awaiting_controller_binding","packet_sha256":"other"}'}}
            with self.assertRaisesRegex(ContractError, "collaboration_ready_packet_mismatch"): loop.start_intent(slot, wrong)
            start = loop.start_intent(slot, proof)
            self.assertEqual(start["dispatch_status"], "call_start_once")
            self.assertEqual(json.loads(start["result"]["tool_request"]["message"]), {"status": "CONTROLLER_BOUND", "packet_sha256": a["packet_sha256"]})
            self.assertEqual(loop._pending(a)["status"], "start_unknown_do_not_resend")
            self.assertEqual(loop.start_intent(slot, proof)["dispatch_status"], "start_unknown_do_not_resend")
            with self.assertRaisesRegex(ContractError, "collaboration_start_target_mismatch"): loop.acknowledge_start(slot, "other-role", "")
            with self.assertRaisesRegex(ContractError, "invalid_native_start_tool_return"): loop.acknowledge_start(slot, target, {})
            ack = loop.acknowledge_start(slot, target, "")
            self.assertEqual(ack, loop.acknowledge_start(slot, target, ""))
            self.assertEqual(loop._pending(a)["status"], "started")
            self.assertEqual(loop.start_intent(slot, proof)["dispatch_status"], "already_acknowledged_do_not_resend")

    def test_bootstrap_returns_only_exact_own_frozen_public_packet(self):
        with Loop(self.run, readonly=True) as loop:
            boot = json.loads(loop.ledger.receipt("dispatch-" + self.slot)["result"]["tool_request"]["message"])
        invocation = boot["bootstrap_exec_command"]
        self.assertFalse(invocation["login"])
        self.assertIn("<<'MEMORYLAB_PACKET_REQUEST'", invocation["cmd"])
        result = subprocess.run(["/bin/zsh", "-c", invocation["cmd"]], capture_output=True, timeout=10, cwd="/tmp")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, canonical(self.actor["packet"]))
        with Loop(self.run, readonly=True) as loop:
            self.assertIsNotNone(loop._delivery(self.slot, 1))
            dispatch = loop.ledger.receipt("dispatch-" + self.slot)["result"]
            boot = json.loads(dispatch["tool_request"]["message"])
            self.assertEqual(boot["expected_packet_sha256"], self.actor["packet_sha256"])
            self.assertEqual(boot["service_client"], self.actor["packet"]["service_client"])
            self.assertNotIn(str(self.run), dispatch["tool_request"]["message"])
            self.assertNotIn("internal", json.loads(result.stdout))
        forbidden = self.request('{"op":"packet","slot":"other-role"}')
        self.assertIn("error", json.loads(forbidden.stdout))
        self.assertNotIn(b"other-role", forbidden.stdout)

    def test_empty_stdin_does_not_send_or_advance_state(self):
        result = self.request(' \n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"no request sent", result.stderr)
        directory = Path(self.actor["internal"]["service_client"]["directory"])
        self.assertEqual(json.loads((directory / "client-state.json").read_text()), {"status": "ready", "sequence": 1})
        with Loop(self.run, readonly=True) as loop:
            self.assertEqual(loop._messages(self.slot), [])
        self.assertEqual(self.request('{"op":"packet"}').returncode, 0)

    def test_exact_full_reply_and_real_client_ack_authorize_next_request(self):
        raw = '{"op":"scenario","request":{"op":"inspect"}}\n'
        result = self.request(raw); self.assertEqual(result.returncode, 0, result.stderr)
        with Loop(self.run, readonly=True) as loop:
            reply = loop._messages(self.slot)[0]
            self.assertEqual(reply["raw"], raw)
            self.assertEqual(result.stdout.decode(), reply["response"]["wire"])
            self.assertEqual(len(json.loads(result.stdout)["environment"]["independent_operation_records"]), 80)
            ack = loop._delivery(self.slot, 1)
            self.assertIn("not_model_context", ack["result"]["status"])
            self.assertFalse(reply["response"]["event"]["delivered"])
        final = '{"op":"finish","result":{"summary":"Unrepaired fixture","completion_claim":"partial","memory_uses":[]}}'
        result = self.request(final); self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "completed")
        self.process.wait(timeout=5)
        with Loop(self.run, readonly=True) as loop:
            report = loop.report(); self.assertEqual(report["actors"][0]["turns"], 2)
            self.assertFalse(report["actors"][0]["result"]["grade"]["full_success"])
            self.assertEqual(loop._messages(self.slot)[1]["raw"], final)
            self.assertIsNotNone(loop._delivery(self.slot, 2))
            self.assertFalse(report["gain_evidence"])

    def test_ipc_cannot_be_acknowledged_as_a_collaboration_tool_return(self):
        self.request('{"op":"open_memory","id":"undisclosed"}')
        with Loop(self.run) as loop:
            with self.assertRaisesRegex(ContractError, "manual_followup_route_not_selected"):
                loop.followup_intent(self.slot, 1)
            with self.assertRaisesRegex(ContractError, "ipc_ack_mismatch"):
                loop.acknowledge_client(self.slot, 1, "other", {}, "a" * 64, "b" * 64)

    def test_actual_ipc_engine_write_check_and_hidden_production_grade(self):
        from test_scenario import CANDIDATES
        code = CANDIDATES[self.actor["internal"]["episode"]["backend"]]
        written = self.request(canonical({"op": "write_engine", "text": code}).decode())
        self.assertEqual(written.returncode, 0, written.stderr)
        self.assertEqual(json.loads(written.stdout)["after_sha256"], hashlib.sha256(canonical(code)).hexdigest())
        check = self.request('{"op":"scenario","request":{"op":"check"}}')
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertTrue(json.loads(check.stdout)["public_smoke_passed"])
        final = {"op": "finish", "result": {"summary": "Explicit correct infrastructure fixture", "completion_claim": "complete", "memory_uses": []}}
        finished = self.request(canonical(final).decode()); self.assertEqual(finished.returncode, 0, finished.stderr)
        self.process.wait(timeout=5)
        with Loop(self.run, readonly=True) as loop:
            report = loop.report()
            self.assertTrue(report["actors"][0]["result"]["grade"]["full_success"])
            self.assertEqual(report["actors"][0]["turns"], 3)
            self.assertNotIn("groups", json.loads(finished.stdout)["public_result"])
            self.assertFalse(report["gain_evidence"])

    def test_changed_client_is_rejected_by_service_and_readonly_audit(self):
        self.process.terminate(); self.process.wait(timeout=5)
        directory = Path(self.actor["internal"]["service_client"]["directory"])
        path = directory / "client.py"; path.chmod(0o600); path.write_text("raise SystemExit('changed')\n")
        with Loop(self.run, readonly=True) as loop:
            with self.assertRaisesRegex(ContractError, "ipc_client_drift"): loop.report()
        restarted = subprocess.run([sys.executable, "-B", "-m", "memorylab.collaboration_ipc", "--run", str(self.run),
                                    "--slot", self.slot], cwd=ROOT, capture_output=True, timeout=5)
        self.assertNotEqual(restarted.returncode, 0); self.assertIn(b"ipc_client_drift", restarted.stderr)

    def test_wrong_capability_leaks_no_controller_data_or_semantic_commit(self):
        address = Path(self.actor["internal"]["service_client"]["directory"]) / "role.sock"
        with socket.socket(socket.AF_UNIX) as peer:
            peer.settimeout(5); peer.connect(str(address))
            peer.sendall(canonical({"capability": "not-this-role", "sequence": 1, "raw": '{"op":"finish","result":{}}'}) + b"\n")
            self.assertEqual(peer.recv(4096), b"")
        self.assertNotEqual(self.process.wait(timeout=5), 0)
        with Loop(self.run, readonly=True) as loop:
            self.assertEqual(loop._messages(self.slot), [])
            self.assertIsNone(loop.ledger.receipt("done-" + self.slot))
            self.assertEqual(loop.summary("baseline")["planned"], 5)

    def test_disconnected_client_remains_pending_and_does_not_retry(self):
        self.process.terminate(); self.process.wait(timeout=5)
        first = self.request('{"op":"scenario","request":{"op":"inspect"}}')
        self.assertNotEqual(first.returncode, 0)
        second = self.request('{"op":"scenario","request":{"op":"inspect"}}')
        self.assertNotEqual(second.returncode, 0); self.assertIn(b"Prior send uncertain", second.stderr)
        with Loop(self.run, readonly=True) as loop: self.assertEqual(loop._messages(self.slot), [])

    def test_packet_contains_only_own_client_and_current_public_task(self):
        self.assertNotIn(str(self.run), self.pending["prompt"])
        self.assertNotIn(self.actor["internal"]["workspace"], self.pending["prompt"])
        self.assertIn(str(self.client[1]), self.pending["prompt"])
        self.assertNotIn(self.actor["internal"]["service_client"]["capability_sha256"], self.pending["prompt"])
        self.assertIn("only allowed local file execution", self.actor["packet"]["instruction"])

    def test_rejected_request_and_intermediate_code_are_public_extractor_sources(self):
        bad = self.request('{"op":"inspect"}')
        self.assertEqual(bad.returncode, 0, bad.stderr)
        self.assertEqual(json.loads(bad.stdout)["error"], "unsupported_collaboration_operation")
        malformed = self.request('{"op":"scenario","request":"inspect"}')
        self.assertEqual(json.loads(malformed.stdout)["error"], "expected_object")
        interim = "def process(case): return {}\n"
        changed = self.request(canonical({"op": "write_engine", "text": interim}).decode())
        self.assertEqual(changed.returncode, 0, changed.stderr)
        final = {"op": "finish", "result": {"summary": "Explicit failed fixture", "completion_claim": "partial", "memory_uses": []}}
        self.assertEqual(self.request(canonical(final).decode()).returncode, 0)
        self.process.wait(timeout=5)
        with Loop(self.run, readonly=True) as loop:
            done = loop.ledger.receipt("done-" + self.slot)["result"]
            sources = [loop.evidence.read(r, "extractor", "task/baseline", loop.ledger.head()) for r in done["sources"]]
            text = "\n".join(s["text"] for s in sources)
            self.assertIn("unsupported_collaboration_operation", text)
            self.assertIn("expected_object", text)
            self.assertIn("def process(case)", text)
            self.assertNotIn("hidden_cases", text)
            tool_results = [s for s in sources if s["event_type"] == "tool_result"]
            self.assertEqual(len(tool_results), 3)
            self.assertTrue(all(json.loads(s["text"]) for s in tool_results))

    def test_rejected_private_fields_stay_in_control_not_extractor_sources(self):
        marker = "private-reasoning-must-not-enter-evidence"
        raw = canonical({"op": "scenario", "request": {"op": "inspect"}, "private_reasoning": marker}).decode()
        rejected = self.request(raw)
        self.assertEqual(rejected.returncode, 0, rejected.stderr)
        self.assertIn("error", json.loads(rejected.stdout))
        final = {"op": "finish", "result": {"summary": "Rejected envelope fixture", "completion_claim": "partial", "memory_uses": []}}
        self.assertEqual(self.request(canonical(final).decode()).returncode, 0)
        self.process.wait(timeout=5)
        with Loop(self.run, readonly=True) as loop:
            self.assertEqual(loop._messages(self.slot)[0]["raw"], raw)
            done = loop.ledger.receipt("done-" + self.slot)["result"]
            sources = [loop.evidence.read(r, "extractor", "task/baseline", loop.ledger.head()) for r in done["sources"]]
            combined = "\n".join(s["text"] for s in sources)
            self.assertNotIn(marker, combined)
            self.assertIn("request_rejected", combined)
            self.assertIn(json.loads(rejected.stdout)["error"], combined)

    def test_public_grammar_matches_nested_dispatcher_and_native_result_schema(self):
        instructions = self.actor["packet"]["instruction"]
        self.assertIn('{"op":"scenario","request":{"op":"inspect"}}', instructions)
        self.assertIn('{"op":"scenario","request":{"op":"check"}}', instructions)
        self.assertIn("The op/ result envelope is client-only", instructions)
        self.assertEqual(self.actor["packet"]["output_schema"]["required"], ["summary", "completion_claim", "memory_uses"])
        inspected = self.request('{"op":"scenario","request":{"op":"inspect"}}')
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertIn("environment", json.loads(inspected.stdout))
