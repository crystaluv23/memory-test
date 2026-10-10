import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from memorylab.contracts import canonical,sha256
from memorylab.derived import DerivedStage
from memorylab.roles import (EvidenceReader,EvidenceSocket,RoleInputs,RoleObserver,SOURCE_CLIENT,
                             capture_transport,role_config,run_canary)
from test_kernel import AT,Fixture

BUDGET=dict(input_tokens=1000,output_tokens=200,seconds=2,commands=3)


def trace(usage=None, extra=None):
    events=[{"type":"thread.started","thread_id":"independent-subject"},{"type":"turn.started"},
        {"type":"item.started","item":{"type":"command_execution","id":"cmd1"}},
        {"type":"item.completed","item":{"type":"command_execution","id":"cmd1"}},
        {"type":"item.completed","item":{"type":"agent_message","id":"answer","text":'{"summary":"actual result","completion_claim":"partial","memory_uses":[]}'}}]
    events.append({"type":"turn.completed","usage":usage if usage is not None else dict(input_tokens=100,cached_input_tokens=90,output_tokens=10,reasoning_output_tokens=4)})
    events.extend(extra or [])
    return b"".join(canonical(e)+b"\n" for e in events)


class RoleInputTests(Fixture):
    def setUp(self):
        super().setUp();self.inputs=RoleInputs(self.cards,self.registry)

    def test_sources_are_handles_not_unbounded_inline_raw_and_real_range_reads(self):
        text="真实反馈\n"+"large observable record "*10000
        src=self.source(text=text)
        packet,reader=self.inputs.extractor("task/chain",self.ledger.head(),[src])
        self.assertNotIn("large observable record",canonical(packet).decode())
        self.assertLess(len(canonical(packet)),131072)
        header=reader.catalog()[0]
        out=json.loads(reader.read(dict(op="read_evidence",id=header["id"],start=0,count=100)))
        self.assertEqual(out["text"],text[:100]);self.assertFalse(out["complete_source"])
        self.assertEqual(out["evidence"]["ref"],src)
        self.assertEqual(out["evidence"]["text_sha256"],hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(reader.events[0]["bytes"],len(canonical(out))+1)
        self.assertEqual(packet["payload"]["context"]["extractor_sha256"],self.cards.extractor["sha256"])

    def test_reader_rejects_hidden_future_other_role_other_namespace_and_forged_refs(self):
        future_cutoff=self.ledger.head();src=self.source()
        self.assert_code("future_source_denied",lambda:self.inputs.extractor("task/chain",future_cutoff,[src]))
        self.assert_code("cross_namespace_source",lambda:self.inputs.extractor("task/other",self.ledger.head(),[src]))
        hidden=self.source("private",ns="control",visibility="controller_only")
        self.assert_code("cross_namespace_source",lambda:self.inputs.extractor("task/chain",self.ledger.head(),[hidden]))
        self.assert_code("role_source_reader_denied",lambda:EvidenceReader(self.cards,"solver","task/chain",self.ledger.head(),[src]))
        self.assert_code("reference_hash_mismatch",lambda:self.inputs.extractor("task/chain",self.ledger.head(),[dict(src,sha256="0"*64)]))

    def test_error_repeat_full_wire_limits_and_persist_before_release(self):
        src=self.source(text="来源条件必须保留。")
        c=role_config();c["source_requests"]=3
        reader=EvidenceReader(self.cards,"reflector","task/chain",self.ledger.head(),[src],c)
        h=reader.catalog()[0];q=dict(op="read_evidence",id=h["id"],start=0,count=100)
        w1=reader.read(q);w2=reader.read(q)
        self.assertEqual(w1,w2);self.assertTrue(json.loads(w1)["complete_source"])
        reader.read(dict(q,count=True))
        self.assertEqual(len(reader.events),3)
        # Protocol 2026-10-10: an exhausted request quota answers with a minimal
        # non-empty envelope (spending wire bytes) instead of a zero-byte wire,
        # which message-only transports cannot deliver.
        quota=b'{"error":"role_source_quota_exhausted"}\n'
        self.assertEqual(reader.read(q),quota);self.assertEqual(reader.read(q),quota)
        self.assertEqual(reader.remaining,c["source_wire_bytes"]-sum(e["bytes"] for e in reader.events))
        def fail(_):raise OSError("persistent store unavailable")
        bad=EvidenceReader(self.cards,"reflector","task/chain",self.ledger.head(),[src],persist=fail)
        with self.assertRaises(OSError):bad.read(q)
        self.assertEqual(bad.events,[]);self.assertEqual(bad.remaining,c["source_wire_bytes"])
        tiny=role_config();tiny["source_wire_bytes"]=10
        limited=EvidenceReader(self.cards,"reflector","task/chain",self.ledger.head(),[src],tiny)
        self.assertEqual(limited.read(q),b"");self.assertEqual(limited.remaining,10)

    def test_socket_uses_actual_registered_readonly_cache_and_standalone_client(self):
        src=self.source();packet,reader=self.inputs.extractor("task/chain",self.ledger.head(),[src])
        workspace=Path(self.tmp.name)/"worker";workspace.mkdir();(workspace/"read_source.py").write_text(SOURCE_CLIENT)
        before=self.ledger.head()
        with EvidenceSocket(workspace,reader):
            result=subprocess.run([sys.executable,"-I","-B",str(workspace/"read_source.py"),reader.catalog()[0]["id"]],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)["text"],self.ledger.get(src)["text"])
        self.assertFalse((workspace/".source.sock").exists());self.assertEqual(self.ledger.head(),before)

    def test_derived_progressive_sources_do_not_overflow_on_large_real_observables(self):
        src=self.source(text="A public service feedback record. "*15000)
        a=self.extract(self.raw_card([src],id="a"),"extractA")["result"]["cards"][0]
        b=self.extract(self.raw_card([src],id="b"),"extractB")["result"]["cards"][0]
        m=self.registry.register_root("memory","withIndex",dict(self.kernel["memory_initial"],**{"consolidation.type":"single_index"}))
        packet,reader=self.inputs.derived("consolidation","task/chain",self.ledger.head(),m)
        p=packet["payload"]["context"]["derived_input"]["payload"]
        self.assertTrue(p["scheduled"]);self.assertNotIn("text",p["sources"][0])
        self.assertEqual(p["sources"][0]["text_sha256"],self.ledger.get(src)["sha256"])
        self.assertEqual(len(reader.catalog()),1)
        self.assertEqual({r["ref"]["id"] for r in p["cards"]},{a["id"],b["id"]})

    def test_research_separate_namespace_fixed_fields_and_source_only_history(self):
        m=self.registry.register_root("memory","current")
        src=self.source("ownHistory",ns="research/proposer",text="Observed own research: no gain evidence.")
        p=self.inputs.research("proposer","research/proposer",self.ledger.head(),m,[src],{"items":[]})
        self.assertEqual(p["payload"]["context"]["registered_fields"],self.kernel["memory_fields"])
        self.assert_code("research_role_namespace_mismatch",lambda:self.inputs.research("evaluator","research/proposer",self.ledger.head(),m,[src],{}))
        other=self.source("otherHistory",ns="research/evaluator")
        self.assert_code("cross_namespace_source",lambda:self.inputs.research("proposer","research/proposer",self.ledger.head(),m,[other],{}))

    def test_secret_material_fails_closed_before_packet_or_source_release(self):
        src=self.source(text="api_key="+"z"*40)
        self.assert_code("role_secret_material",lambda:self.inputs.extractor("task/chain",self.ledger.head(),[src]))

    def test_execution_code_changes_packet_approval_identity(self):
        from unittest.mock import patch
        src=self.source();first,_=self.inputs.extractor("task/chain",self.ledger.head(),[src])
        with patch("memorylab.freeze.implementation_files",return_value={"memorylab/sandbox.py":b"different registered execution policy"}):
            changed,_=self.inputs.extractor("task/chain",self.ledger.head(),[src])
        self.assertNotEqual(first["payload"]["implementation_sha256"],changed["payload"]["implementation_sha256"])
        self.assertNotEqual(first["sha256"],changed["sha256"])


class RoleTransportTests(Fixture):
    def packet(self):
        workspace=Path(self.tmp.name)/"subject";workspace.mkdir(exist_ok=True);(workspace/"SPEC.md").write_text("Current declared task only.")
        return workspace,RoleInputs(self.cards,self.registry).task("solver","task/subject",0,workspace,{"id":"episode"},{"items":[]})

    def capture(self,data,name="out",budget=None,config=None,sleep=False):
        workspace,packet=self.packet()
        code="import sys,time;sys.stdin.buffer.read();sys.stdout.buffer.write("+repr(data)+");sys.stdout.flush();"+("time.sleep(10)" if sleep else "")
        return capture_transport([sys.executable,"-I","-B","-c",code],workspace,packet,budget or BUDGET,Path(self.tmp.name)/name,config)

    def test_actual_subprocess_capture_final_usage_and_raw_bytes(self):
        data=trace();r=self.capture(data)
        self.assertTrue(r["transport_success"]);self.assertEqual(r["total_tokens"],110)
        self.assertEqual(r["usage"]["cached_input_tokens"],90);self.assertEqual(r["usage"]["reasoning_output_tokens"],4)
        self.assertEqual(r["command_count"],1)
        self.assertFalse(r["formal_admission"]);self.assertFalse(r["provider_budget_qualified"])
        self.assertEqual((Path(self.tmp.name)/"out/events.jsonl").read_bytes(),data)
        self.assertTrue((Path(self.tmp.name)/"out/start.json").exists())

    def test_missing_boolean_duplicate_and_inconsistent_usage_are_unknown(self):
        cases=[trace(usage={}),trace(usage=dict(input_tokens=True,cached_input_tokens=0,output_tokens=1)),
               trace(usage=dict(input_tokens=1,cached_input_tokens=2,output_tokens=1)),
               trace(extra=[{"type":"turn.completed","usage":dict(input_tokens=100,cached_input_tokens=90,output_tokens=10)}])]
        for i,data in enumerate(cases):
            r=self.capture(data,name="invalid"+str(i));self.assertFalse(r["transport_success"])
            self.assertIsNone(r["usage"]);self.assertIsNone(r["total_tokens"])

    def test_post_response_token_overage_stops_preserves_cost_and_never_qualifies(self):
        r=self.capture(trace(usage=dict(input_tokens=1001,cached_input_tokens=0,output_tokens=20)),sleep=True)
        self.assertEqual(r["error"],"role_observed_token_guard");self.assertEqual(r["total_tokens"],1021)
        self.assertFalse(r["provider_budget_qualified"]);self.assertLess(r["process"]["wall_seconds"],2)

    def test_command_limit_partial_utf8_timeout_and_incomplete_lines_are_failures(self):
        extra=[{"type":"item.started","item":{"type":"command_execution","id":"cmd"+str(i)}} for i in range(5)]
        prefix=b'\x7b"type":"thread.started","thread_id":"one"}\n{"type":"turn.started"}\n'
        r=self.capture(prefix+b"".join(canonical(e)+b"\n" for e in extra),name="commands",sleep=True)
        self.assertEqual(r["error"],"role_command_guard");self.assertIsNone(r["total_tokens"])
        r=self.capture(b'{"type":"thread.started","thread_id":"one"}',name="partial")
        self.assertFalse(r["transport_success"]);self.assertEqual(r["error"],"role_incomplete_event_line")
        r=self.capture(b"\xff\n",name="invalidutf8");self.assertFalse(r["transport_success"])
        self.assertEqual((Path(self.tmp.name)/"invalidutf8/events.jsonl").read_bytes(),b"\xff\n")
        r=self.capture(prefix,name="timeout",budget=dict(BUDGET,seconds=1),sleep=True)
        self.assertEqual(r["error"],"production_timeout");self.assertIsNone(r["usage"])

    def test_capture_guard_line_and_stream_overflow_is_bounded_and_not_parsed_success(self):
        c=role_config();c["max_event_line_bytes"]=32
        r=self.capture(b"x"*100,name="line",config=c);self.assertEqual(r["error"],"role_event_line_limit")
        c=role_config();c["stdout_bytes"]=128
        r=self.capture(trace(),name="overflow",config=c)
        self.assertTrue(r["process"]["stdout_truncated"])
        self.assertEqual((Path(self.tmp.name)/"overflow/events.jsonl").stat().st_size,128)
        self.assertFalse(r["transport_success"])

    def test_unauthorized_canary_fails_before_any_process_or_output_directory(self):
        workspace,packet=self.packet();output=Path(self.tmp.name)/"neverStarted"
        self.assert_code("role_canary_not_authorized",lambda:run_canary(role_config(),workspace,Path(self.tmp.name),packet,BUDGET,output,packet["sha256"]))
        self.assertFalse(output.exists())
