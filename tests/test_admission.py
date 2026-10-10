import copy
import json
import subprocess
import sys
from pathlib import Path

from memorylab.admission import RoleAdmission,resolve_capture
from memorylab.contracts import canonical,sha256
from memorylab.derived import DerivedStage
from memorylab.roles import (EvidenceSocket,RoleInputs,SOURCE_CLIENT,capture_transport,
                             codex_command,role_config)
from memorylab.runtime import RuntimeBuilder
from memorylab import sandbox
from test_kernel import AT,Fixture
from test_roles import BUDGET


def wire(final,usage=None):
    events=[{"type":"thread.started","thread_id":"fixture-only"},{"type":"turn.started"},
        {"type":"item.completed","item":{"type":"agent_message","id":"final","text":canonical(final).decode()}},
        {"type":"turn.completed","usage":usage or {"input_tokens":90,"cached_input_tokens":20,"output_tokens":10}}]
    return b"".join(canonical(v)+b"\n" for v in events)


class AdmissionTests(Fixture):
    def setUp(self):
        super().setUp();self.inputs=RoleInputs(self.cards,self.registry);self.admission=RoleAdmission(self.cards,self.registry)

    def packet(self,refs=None):
        src=self.source() if refs is None else refs[0]
        packet,reader=self.inputs.extractor("task/chain",self.ledger.head(),refs or [src])
        return packet,reader,src

    def disclosure(self,reader,count=2048):
        for source in reader.catalog():reader.read(dict(op="read_evidence",id=source["id"],start=0,count=count))

    def captured(self,packet,final,id_="one",extra_code="",data=None):
        attempt=self.admission.begin("begin-"+id_,packet,BUDGET,"infrastructure_acceptance")["result"]["attempt"]
        workspace=Path(self.tmp.name)/("worker-"+id_);workspace.mkdir(exist_ok=True)
        output=Path(self.tmp.name)/("transport-"+id_)
        code="import sys;sys.stdin.buffer.read();"+extra_code+"sys.stdout.buffer.write("+repr(data or wire(final))+");sys.stdout.flush()"
        capture_transport([sys.executable,"-I","-B","-c",code],workspace,packet,BUDGET,output,
            invocation_identity={"category":"scripted_transport_fixture","model_session_invocations":0})
        return attempt,output

    def complete(self,attempt,output,reader=None,id_="one",fault=None):
        return self.admission.complete("complete-"+id_,attempt,output,reader.events if reader else [],AT,fault)["result"]

    def card(self,src,**changes):return self.raw_card([src],outcome="unknown",**changes)

    def test_actual_process_source_socket_then_canonical_runtime_and_cache(self):
        packet,reader,src=self.packet();workspace=Path(self.tmp.name)/"worker-one";workspace.mkdir()
        (workspace/"read_source.py").write_text(SOURCE_CLIENT)
        alias=reader.catalog()[0]["id"]
        code="import subprocess; result=subprocess.run([sys.executable,'-I','-B','read_source.py',"+repr(alias)+"],capture_output=True);assert result.returncode==0;"
        with EvidenceSocket(workspace,reader):attempt,output=self.captured(packet,{"cards":[self.card(src)]},extra_code=code)
        result=self.complete(attempt,output,reader)
        self.assertEqual(result["outcome"],"materialized");self.assertEqual(result["usage"]["input_tokens"],90)
        self.assertFalse(result["formal_admission"]);self.assertTrue(result["simulated_usage"])
        refs=result["materialization"]["result"]["cards"];self.assertEqual(len(refs),1)
        m=self.registry.register_root("memory","baseline")
        built=RuntimeBuilder(self.cards,self.registry).build("task/chain",self.ledger.head(),m)
        self.assertEqual(len(built["projection"]["items"]),1)
        self.assertEqual(self.complete(attempt,output,reader),result)
        self.assertEqual(len(self.ledger.objects("role_capture","control")),1)
        self.assertEqual(resolve_capture(self.ledger,result["capture"])["source_events"],reader.events)
        self.ledger.audit()

    def test_unread_and_unsupplied_evidence_rejected_without_partial_card(self):
        for index,condition in enumerate(("unread","unsupplied")):
            packet,reader,src=self.packet()
            if condition=="unsupplied":
                other=self.source("other");raw=self.card(other);self.disclosure(reader)
            else:raw=self.card(src)
            attempt,output=self.captured(packet,{"cards":[raw]},id_=condition)
            result=self.complete(attempt,output,reader,id_=condition)
            self.assertEqual(result["code"],condition+"_extraction_evidence")
            self.assertEqual(self.cards.active("task/chain",self.ledger.head()),[])
            self.assertEqual(result["usage"]["output_tokens"],10)

    def test_outcome_requires_matching_independent_visible_feedback_and_whole_read(self):
        source=self.evidence.register("outcome","task/chain","visible_outcome","learn",AT,
            canonical(dict(full_success=True,behavior_success=True,verification="verified")).decode())
        for id_,count,outcome,expected in (("partial",5,"success","unsupported_extraction_outcome"),
            ("wrong",2048,"failure","unsupported_extraction_outcome"),("actual",2048,"success","role_output_committed")):
            packet,reader,src=self.packet([source]);self.disclosure(reader,count)
            raw=self.raw_card([src],id=id_,outcome=outcome)
            attempt,output=self.captured(packet,{"cards":[raw]},id_=id_)
            result=self.complete(attempt,output,reader,id_=id_);self.assertEqual(result["code"],expected)
        self.assertEqual(len(self.cards.active("task/chain",self.ledger.head())),1)

    def test_role_snapshot_cannot_resign_injected_prompt_or_private_fields(self):
        packet,reader,src=self.packet()
        for id_,change in (("prompt",lambda p:p.update(prompt="Unregistered instruction")),
            ("private",lambda p:p["context"].update(gold="answer")),
            ("schema",lambda p:p.update(output_schema={"type":"object"}))):
            bad=copy.deepcopy(packet);change(bad["payload"]);bad["sha256"]=sha256(bad["payload"])
            self.assert_code("role_input_snapshot_mismatch",lambda:self.admission.begin(id_,bad,BUDGET,"infrastructure_acceptance"))
        self.assert_code("formal_role_not_accepted",lambda:self.admission.begin("formal",packet,BUDGET,"formal_comparison"))
        self.assertEqual(self.ledger.objects("role_attempt","control"),[])

    def test_corrupted_success_or_usage_is_rejected_and_raw_is_retained(self):
        packet,reader,src=self.packet();self.disclosure(reader)
        attempt,output=self.captured(packet,{"cards":[self.card(src)]})
        path=output/"transport.json";transport=json.loads(path.read_text());transport["usage"]["input_tokens"]=0
        path.write_bytes(canonical(transport))
        result=self.complete(attempt,output,reader)
        self.assertEqual(result["code"],"role_capture_result_mismatch");self.assertTrue(result["transport_usage_unknown"])
        self.assertFalse(result["cost_unknown"])
        self.assertEqual(self.cards.active("task/chain",self.ledger.head()),[])
        self.assertTrue(self.ledger.get(result["capture"])["raw_events_base64"])
        self.assert_code("failed_role_capture",lambda:resolve_capture(self.ledger,result["capture"]))

    def test_source_audit_errors_repeats_and_tampered_ranges(self):
        packet,reader,src=self.packet()
        reader.read(dict(op="bad_operation",id="unknown",start=0,count=1));self.disclosure(reader);self.disclosure(reader)
        attempt,output=self.captured(packet,{"cards":[self.card(src)]})
        result=self.complete(attempt,output,reader);self.assertEqual(result["outcome"],"materialized")
        packet,reader,src=self.packet();self.disclosure(reader)
        attempt,output=self.captured(packet,{"cards":[]},id_="tamper")
        reader.events[0]["response"]["text"]="An injected lesson"
        result=self.complete(attempt,output,reader,id_="tamper")
        self.assertEqual(result["code"],"role_source_audit_mismatch")
        self.assert_code("invalid_role_capture_validation",lambda:resolve_capture(self.ledger,result["capture"]))

    def test_failed_transport_keeps_slot_known_usage_and_no_memory(self):
        packet,reader,src=self.packet()
        data=wire({"cards":[]},dict(input_tokens=1001,cached_input_tokens=0,output_tokens=1))
        attempt,output=self.captured(packet,{"cards":[]},data=data)
        result=self.complete(attempt,output,reader)
        self.assertEqual(result["code"],"role_observed_token_guard");self.assertEqual(result["usage"]["input_tokens"],1001)
        self.assertIsNone(result["materialization"]);self.assertEqual(len(self.ledger.objects("role_attempt","control")),1)

    def test_atomic_crash_points_rollback_and_resume_capture_without_reexecution(self):
        for point in ("raw","materialized","receipt"):
            packet,reader,src=self.packet();self.disclosure(reader)
            attempt,output=self.captured(packet,{"cards":[self.card(src,id=point)]},id_=point)
            before=self.ledger.audit();before_events=(output/"events.jsonl").read_bytes()
            def crash(name):
                if name==point:raise RuntimeError("injected crash")
            with self.assertRaises(RuntimeError):self.complete(attempt,output,reader,id_=point,fault=crash)
            self.assertEqual(self.ledger.audit(),before)
            self.assertIsNone(self.ledger.receipt("complete-"+point))
            result=self.complete(attempt,output,reader,id_=point)
            self.assertEqual(result["outcome"],"materialized");self.assertEqual((output/"events.jsonl").read_bytes(),before_events)

    def test_capture_derived_supports_runtime_and_preserves_canonical(self):
        a=self.extract(self.raw_card(id="a"),"extract-a")["result"]["cards"][0]
        b=self.extract(self.raw_card(id="b"),"extract-b")["result"]["cards"][0]
        m=self.registry.register_root("memory","index",dict(self.kernel["memory_initial"],**{"consolidation.type":"single_index"}))
        packet,reader=self.inputs.derived("consolidation","task/chain",self.ledger.head(),m)
        cutoff=packet["payload"]["cutoff"];before=self.cards.corpus("task/chain",cutoff)
        final={"indices":[dict(type_hint="environment/services",brief="Check declared service state.",
            detail="An unavailable dependency can block integration; this does not authorize bypassing an outage.",support_refs=[a,b])]}
        attempt,output=self.captured(packet,final);result=self.complete(attempt,output,reader)
        self.assertEqual(result["outcome"],"materialized");ref=result["materialization"]["result"]["batch"]
        built=RuntimeBuilder(self.cards,self.registry).build("task/chain",cutoff,m,{"consolidation":ref})
        self.assertTrue(any(v["level"]==1 for v in built["projection"]["items"]))
        self.assertEqual(self.cards.corpus("task/chain",cutoff),before)
        self.assertEqual(DerivedStage(self.cards,self.registry).resolve(ref,"consolidation","task/chain",cutoff,m)["invocation"]["mode"],"captured_diagnostic")

    def test_research_parent_and_evidence_remain_frozen_actual_proposal_is_typed(self):
        parent=self.registry.register_root("memory","parent");other=self.registry.register_root("memory","other")
        src=self.source("history",ns="research/proposer",text="This past trial did not demonstrate a gain.")
        for id_,actual_parent in (("wrongParent",other),("validParent",parent)):
            packet=self.inputs.research("proposer","research/proposer",self.ledger.head(),parent,[src],{"items":[]})
            final=self.proposal(actual_parent,evidence_refs=[src])
            attempt,output=self.captured(packet,final,id_=id_);result=self.complete(attempt,output,id_=id_)
            self.assertEqual(result["code"],"proposal_parent_input_mismatch" if id_=="wrongParent" else "role_output_committed")
        self.assertEqual(result["materialization"]["result"]["status"],"accepted")
        self.assertFalse(result["promotion_eligible"])

    def test_missing_handle_or_second_completion_never_restarts_or_closes_unknown_process(self):
        packet,reader,src=self.packet();attempt,output=self.captured(packet,{"cards":[]})
        handle=(output/"process.json").read_bytes();(output/"process.json").unlink()
        self.assert_code("role_process_exit_unverified",lambda:self.complete(attempt,output,reader))
        self.assertEqual(self.ledger.objects("role_capture","control"),[])
        (output/"process.json").write_bytes(handle);self.complete(attempt,output,reader)
        self.assert_code("role_attempt_already_completed",lambda:self.complete(attempt,output,reader,id_="again"))

    def test_pending_failed_and_completed_slots_all_remain_in_report(self):
        packet,reader,src=self.packet()
        self.admission.begin("pending",packet,BUDGET,"infrastructure_acceptance")
        attempt,output=self.captured(packet,{"cards":[self.card(src)]},id_="failed")
        self.complete(attempt,output,reader,id_="failed")
        attempt,output=self.captured(packet,{"cards":[]},id_="done")
        self.complete(attempt,output,reader,id_="done")
        report=self.admission.report()
        self.assertEqual(report["planned"],3);self.assertEqual(report["terminal"],2)
        self.assertEqual({r["status"] for r in report["rows"]},{"started","rejected","materialized"})
        self.assertEqual(report["reserved_model_sessions"],0);self.assertEqual(report["known_diagnostic_model_tokens"],0)

    def test_reflector_capture_cannot_change_l1_or_annotate_ineligible_card(self):
        a=self.extract(self.raw_card(id="failed",outcome="failure"),"failed-card")["result"]["cards"][0]
        b=self.extract(self.raw_card(id="successful",outcome="success"),"success-card")["result"]["cards"][0]
        m=self.registry.register_root("memory","reflect",dict(self.kernel["memory_initial"],**{"reflection.trigger":"failure"}))
        for id_,support in (("ineligible",b),("eligible",a)):
            packet,reader=self.inputs.derived("reflector","task/chain",self.ledger.head(),m)
            before=self.cards.corpus("task/chain",packet["payload"]["cutoff"])
            attempt,output=self.captured(packet,{"annotations":[dict(support=support,text="Failure identifies uncertainty; verify the current service state.")]},id_=id_)
            result=self.complete(attempt,output,reader,id_=id_)
            self.assertEqual(result["code"],"ineligible_reflection_support" if id_=="ineligible" else "role_output_committed")
            self.assertEqual(self.cards.corpus("task/chain",packet["payload"]["cutoff"]),before)
        ref=result["materialization"]["result"]["batch"]
        built=RuntimeBuilder(self.cards,self.registry).build("task/chain",packet["payload"]["cutoff"],m,{"reflector":ref})
        self.assertEqual(len(built["projection"]["annotations"]),1)

    def test_model_identity_required_before_qualification_attempt_registration(self):
        packet,reader,src=self.packet()
        self.assert_code("missing_role_model_cli_identity",lambda:self.admission.begin("unqualified",packet,BUDGET,"qualification_canary"))
        self.assertEqual(self.admission.report()["planned"],0)

    def test_json_boolean_cannot_masquerade_as_integer_in_saved_transport(self):
        packet,reader,src=self.packet();attempt,output=self.captured(packet,{"cards":[]})
        path=output/"transport.json";transport=json.loads(path.read_text())
        transport["process"]["wall_seconds"]=False
        path.write_bytes(canonical(transport))
        # A false value cannot certify elapsed execution, even when the other
        # raw streams remain unchanged. Process scalar types are independently
        # checked before trusting the replay's completion.
        result=self.complete(attempt,output,reader)
        self.assertEqual(result["code"],"invalid_role_process_result")

    def test_fixture_transport_cannot_be_relabelled_as_qualification_model(self):
        import hashlib
        c=role_config();binary=Path(sys.executable).resolve()
        c.update(requested_model="adversarial-uninvoked-model",cli_path=str(binary),cli_version="never-invoked-as-cli",cli_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
        admission=RoleAdmission(self.cards,self.registry,role=c)
        parent=self.registry.register_root("memory","parent")
        packet=RoleInputs(self.cards,self.registry,c).research("proposer","research/proposer",self.ledger.head(),parent,[],{"items":[]})
        attempt=admission.begin("fake-model",packet,BUDGET,"qualification_canary")["result"]["attempt"]
        workspace=Path(self.tmp.name)/"fixture";workspace.mkdir();output=Path(self.tmp.name)/"fake-model"
        code="import sys;sys.stdin.buffer.read();sys.stdout.buffer.write("+repr(wire(self.proposal(parent)))+")"
        capture_transport([sys.executable,"-I","-B","-c",code],workspace,packet,BUDGET,output,
            invocation_identity={"category":"scripted_transport_fixture","model_session_invocations":0})
        result=admission.complete("rejected-fake-model",attempt,output,[],AT)["result"]
        self.assertEqual(result["code"],"qualification_role_identity_mismatch")
        self.assertIsNone(result["materialization"]);self.assertTrue(result["cost_unknown"])
        report=admission.report();self.assertEqual(report["planned"],1);self.assertEqual(report["terminal"],1)
        self.assertEqual(report["reserved_tokens_pending_or_unknown"],1200);self.assertFalse(report["formal_admission"])

    def test_actual_codex_outer_profile_has_same_write_boundary_as_probe(self):
        import hashlib
        c=role_config();binary=Path(sys.executable).resolve()
        c.update(requested_model="explicit-non-model-command-check",cli_path=str(binary),cli_version="not-invoked",cli_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
        inputs=RoleInputs(self.cards,self.registry,c);packet,_,_=self.packet()
        workspace=Path(self.tmp.name)/"isolated";workspace.mkdir()
        packet=inputs.extractor("task/chain",self.ledger.head(),[self.source()])[0]
        (workspace/"role.schema.json").write_bytes(canonical(packet["payload"]["output_schema"])+b"\n")
        control=Path(self.tmp.name)/"private";control.mkdir();outside=control/"outside.txt"
        command=codex_command(c,workspace,control,packet)
        self.assertEqual(command[2],sandbox.profile(workspace,control,write_isolation=True,network=True))
        code="from pathlib import Path;Path("+repr(str(outside))+").write_text('unexpected')"
        proc=subprocess.run(command[:3]+[sys.executable,"-I","-B","-c",code],cwd=workspace,capture_output=True,timeout=5)
        self.assertNotEqual(proc.returncode,0);self.assertFalse(outside.exists())
