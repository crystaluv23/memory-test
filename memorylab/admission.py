"""Controller-only diagnostic role completion, replayed from bounded raw capture."""
import base64
import copy
import hashlib
from pathlib import Path

from .contracts import (ContractError, canonical, exact_object, identifier, reference,
                        require, sha256, timestamp)
from .derived import DerivedStage
from .kernel_cli import parse_json, read_json
from .roles import (EvidenceReader, RoleInputs, RoleObserver, process_status,
                    codex_args, role_config, validate_role)
from . import sandbox


def admission_config(path=None):
    value=read_json(path or Path(__file__).resolve().parent.parent/"configs/admission_v0.json")
    exact_object(value,{"schema_version","status","hypothesis","categories","formal_admission","automatic_retry",
        "max_capture_bytes","max_source_audit_bytes","materialization_roles","source_policy","semantic_validation"},"invalid_admission_config")
    require(value["schema_version"]=="role-admission-v0" and value["categories"]==["infrastructure_acceptance","qualification_canary"] and
        value["formal_admission"] is False and value["automatic_retry"] is False,"unsafe_admission_policy")
    require(value["materialization_roles"]==["extractor","consolidation","reflector","evaluator","proposer"] and
        value["source_policy"]=="supplied_refs_and_actual_disclosed_ranges" and value["semantic_validation"]=="not_established","unsupported_admission_policy")
    for key in ("max_capture_bytes","max_source_audit_bytes"):
        require(type(value[key]) is int and value[key]>0,"invalid_admission_limit")
    return value


def resolve_capture(ledger,ref):
    """Authority is the committed controller capture, never model self-attestation."""
    reference(ref,{"role_capture"});require(ref["namespace"]=="control","invalid_role_capture_namespace")
    value=ledger.get(ref);receipt=ledger.receipt(value["completion_request_id"])
    require(receipt is not None and receipt["result"].get("capture")==ref,"uncommitted_role_capture")
    require(value["category"] in ("infrastructure_acceptance","qualification_canary") and value["formal_admission"] is False,
        "unsupported_role_capture_category")
    require(value["transport"]["transport_success"] is True,"failed_role_capture")
    require(value["validation_code"]=="raw_transport_valid","invalid_role_capture_validation")
    return value


class RoleAdmission:
    def __init__(self,cards,registry,config=None,role=None):
        require(cards.ledger is registry.ledger,"role_admission_store_mismatch")
        self.cards,self.registry,self.ledger=cards,registry,cards.ledger
        self.config=admission_config(config) if config is not None else admission_config()
        self.role=validate_role(role) if role is not None else role_config()

    def _regenerate(self,packet):
        exact_object(packet,{"payload","sha256"},"invalid_role_packet")
        require(sha256(packet["payload"])==packet["sha256"],"role_packet_drift")
        p=packet["payload"];role=p["role"];context=p["context"]
        require(role in self.config["materialization_roles"],"unsupported_admission_role")
        inputs=RoleInputs(self.cards,self.registry,self.role)
        if role=="extractor":
            actual,reader=inputs.extractor(p["namespace"],p["cutoff"],[s["ref"] for s in context["sources"]])
        elif role in ("consolidation","reflector"):
            actual,reader=inputs.derived(role,p["namespace"],p["cutoff"],context["derived_input"]["payload"]["spec"])
        else:
            actual=inputs.research(role,p["namespace"],p["cutoff"],context["parent"],[s["ref"] for s in context["history"]],context["briefs"])
            reader=None
        require(actual==packet,"role_input_snapshot_mismatch")
        return reader

    def begin(self,request_id,packet,budget,category):
        identifier(request_id);require(category in self.config["categories"],"formal_role_not_accepted")
        self._regenerate(packet);RoleObserver(budget,self.role)
        p=packet["payload"]
        if category=="qualification_canary":
            require(all(self.role[k] for k in ("requested_model","cli_path","cli_version","cli_sha256")),"missing_role_model_cli_identity")
            if p["role"]=="extractor":
                require(self.registry.kernel["extractor"]["model"]==self.role["requested_model"],"fixed_extractor_model_mismatch")
            if p["role"] in ("consolidation","reflector"):
                require(p["context"]["derived_input"]["payload"]["model"]==self.role["requested_model"],"fixed_derived_model_mismatch")
        request={"packet":packet,"budget":budget,"category":category,"role_config":self.role,"admission_config":self.config,
            "control_root":str(self.ledger.path.parent)}
        with self.ledger.mutation():
            prior=self.ledger.receipt(request_id,request)
            if prior:return prior
            ref=self.ledger.put({"kind":"role_attempt","id":"role-attempt-"+sha256({"request_id":request_id}),"version":1,
                "begin_request_id":request_id,**copy.deepcopy(request),"reserved_model_sessions":int(category=="qualification_canary")},"control")
            return self.ledger.commit_receipt(request_id,request,{"status":"role_started","attempt":ref,
                "formal_admission":False,"promotion_eligible":False},"control")

    def _snapshot(self,directory):
        names=("input.json","start.json","process.json","events.jsonl","stderr.bin","transport.json")
        total=0;files={}
        for name in names:
            path=directory/name
            if not path.exists():continue
            require(path.is_file() and not path.is_symlink(),"invalid_role_capture_file")
            size=path.stat().st_size;require(size<=self.config["max_capture_bytes"]-total,"role_capture_overflow")
            with path.open("rb") as stream:data=stream.read(self.config["max_capture_bytes"]-total+1)
            require(len(data)==size,"role_capture_changed_during_read")
            files[name]=data;total+=size
        return files

    @staticmethod
    def _verify(attempt,files):
        require(set(files)=={"input.json","start.json","process.json","events.jsonl","stderr.bin","transport.json"},"incomplete_role_capture")
        packet=attempt["packet"];require(parse_json(files["input.json"].decode())==packet,"role_capture_input_mismatch")
        start=parse_json(files["start.json"].decode())
        exact_object(start,{"status","input_sha256","budget","command","invocation_identity","formal_admission","unknown_on_interruption"},"invalid_role_start")
        require(start["status"]=="started" and start["input_sha256"]==packet["sha256"] and start["budget"]==attempt["budget"] and
            start["formal_admission"] is False and start["unknown_on_interruption"] is True,"role_capture_start_mismatch")
        identity=start["invocation_identity"]
        require(type(identity) is dict,"missing_role_invocation_identity")
        if attempt["category"]=="infrastructure_acceptance":
            require(identity=={"category":"scripted_transport_fixture","model_session_invocations":0},"scripted_role_identity_mismatch")
        else:
            c=attempt["role_config"]
            require(identity.get("category")=="qualification_canary" and type(identity.get("model_session_invocations")) is int and
                identity["model_session_invocations"]==1 and identity.get("approved_packet_sha256")==packet["sha256"] and
                all(identity.get(k)==c[k] for k in ("cli_sha256","cli_version","requested_model","reasoning_effort")) and
                type(identity.get("isolation")) is dict,"qualification_role_identity_mismatch")
            require(canonical(identity["isolation"])==canonical(dict(workspace_read=True,control_read_denied=True,outside_write_denied=True,workspace_write=True)),"unqualified_role_isolation")
            command=start["command"]
            require(type(command) is list and command.count("--cd")==1,"role_capture_command_mismatch")
            workspace=command[command.index("--cd")+1]
            require(type(workspace) is str and command==sandbox.command(workspace,attempt["control_root"],
                codex_args(c,workspace),write_isolation=True,network=True),"role_capture_command_mismatch")
        transport=parse_json(files["transport.json"].decode());process=transport["process"]
        require(type(process["exit_code"]) is int and type(process["stdout_truncated"]) is bool and
            type(process["stderr_truncated"]) is bool and (process["error"] is None or type(process["error"]) is str) and
            type(process["wall_seconds"]) in (int,float) and process["wall_seconds"]>=0,"invalid_role_process_result")
        for name,file_ in (("stdout","events.jsonl"),("stderr","stderr.bin")):
            raw=process["raw_streams"][name];data=files[file_]
            require(type(raw["bytes"]) is int and raw["bytes"]==len(data) and raw["sha256"]==hashlib.sha256(data).hexdigest() and
                base64.b64decode(raw["base64"],validate=True)==data,"role_capture_stream_mismatch")
            require(len(data)<=attempt["role_config"][name+"_bytes"],"role_capture_stream_overflow")
            require(process[name]==data.decode("utf-8",errors="replace"),"role_capture_decoded_stream_mismatch")
        observer=RoleObserver(attempt["budget"],attempt["role_config"])
        # Replay the prefix that the actual bounded observer could have received.
        # A stop reason can leave a suffix in the same OS read; feed it once so
        # limits and unprocessed bytes remain exactly observable, never accepted.
        observer.feed(files["events.jsonl"])
        verified=observer.finish(process);verified.update(process=process,input_sha256=packet["sha256"])
        require(canonical(verified)==canonical(transport),"role_capture_result_mismatch")
        return verified

    def _reads(self,packet,reader,events):
        require(type(events) is list and len(canonical(events))<=self.config["max_source_audit_bytes"],"source_audit_overflow")
        if reader is None:
            require(not events,"unexpected_role_source_audit");return {}
        disclosed={}
        for event in events:
            require(type(event) is dict and "request" in event,"invalid_role_source_audit")
            request=event["request"]
            # Invalid requests intentionally retain a normalized envelope.
            reader.read(request)
            require(reader.events and canonical(reader.events[-1])==canonical(event),"role_source_audit_mismatch")
            response=event["response"]
            if type(response) is dict and type(response.get("text")) is str and response["text"]:
                disclosed.setdefault(canonical(response["evidence"]["ref"]),[]).append((response["start"],response["end"]))
        require(canonical(reader.events)==canonical(events),"role_source_audit_mismatch")
        return disclosed

    def _validate_extraction(self,attempt,final,disclosed):
        p=attempt["packet"]["payload"];context=p["context"]
        exact_object(final,{"cards"},"invalid_extraction_fields")
        require(type(final["cards"]) is list,"invalid_extraction_count")
        supplied={canonical(s["ref"]) for s in context["sources"]}
        active={canonical(v["ref"]) for v in context["active_cards"]}
        ids={v["card"]["id"] for v in context["active_cards"]}
        for raw in final["cards"]:
            require(type(raw) is dict and type(raw.get("evidence_refs")) is list and type(raw.get("supersedes")) is list,"invalid_extract_card_fields")
            for ref in raw["evidence_refs"]:
                require(canonical(ref) in supplied,"unsupplied_extraction_evidence")
                require(canonical(ref) in disclosed,"unread_extraction_evidence")
            require(all(canonical(r) in active for r in raw["supersedes"]),"unsupplied_extraction_supersession")
            require(raw.get("expected_version")==0 or raw.get("id") in ids,"unsupplied_extraction_revision")
            if raw.get("outcome") in ("success","failure"):
                outcomes=[]
                for ref in raw["evidence_refs"]:
                    source=self.cards.evidence.read(ref,"extractor",p["namespace"],p["cutoff"])
                    if source["event_type"]=="visible_outcome":
                        end=0
                        for start,stop in sorted(disclosed[canonical(ref)]):
                            if start>end:break
                            end=max(end,stop)
                        if end<len(source["text"]):continue
                        try:value=parse_json(source["text"])
                        except ValueError:continue
                        if type(value) is dict and value.get("verification")=="verified" and all(type(value.get(k)) is bool for k in ("full_success","behavior_success")):
                            outcomes.append("success" if value["full_success"] and value["behavior_success"] else "failure")
                require(outcomes and all(v==raw["outcome"] for v in outcomes),"unsupported_extraction_outcome")
    def _extract(self,attempt,final,disclosed,request_id,at):
        self._validate_extraction(attempt,final,disclosed)
        p=attempt['packet']['payload']
        return self.cards.extract(request_id,final,p["namespace"],p["cutoff"],at)

    def _validate_proposal_input(self,attempt,final):
        p=attempt['packet']['payload']
        require(final.get('parent')==p['context']['parent'],'proposal_parent_input_mismatch')
        supplied={canonical(row['ref']) for row in p['context']['history']}
        require(type(final.get('evidence_refs')) is list and all(canonical(r) in supplied for r in final['evidence_refs']),'unsupplied_proposal_evidence')

    def _materialize(self,attempt,final,disclosed,capture_ref,capture,attempt_ref,at):
        """Shared semantic/typed commit boundary for exec and native RPC."""
        p=attempt['packet']['payload'];role=p['role']
        if role=='extractor':return self._extract(attempt,final,disclosed,'extract-'+sha256(attempt_ref),at)
        if role in ('consolidation','reflector'):
            return DerivedStage(self.cards,self.registry)._accept_capture('derive-'+sha256(attempt_ref),p['context']['derived_input'],capture_ref,capture)
        self._validate_proposal_input(attempt,final)
        return self.registry.propose('proposal-'+sha256(attempt_ref),final,role,p['cutoff'],self.cards.proposal_evidence)

    def complete(self,request_id,attempt_ref,directory,source_events,at,fault=None):
        identifier(request_id);reference(attempt_ref,{"role_attempt"});timestamp(at)
        require(attempt_ref["namespace"]=="control","invalid_role_attempt_namespace")
        attempt=self.ledger.get(attempt_ref)
        begin=self.ledger.receipt(attempt["begin_request_id"])
        require(begin is not None and begin["result"]["attempt"]==attempt_ref,"uncommitted_role_attempt")
        require(attempt["role_config"]==self.role and attempt["admission_config"]==self.config,"role_admission_config_drift")
        directory=Path(directory);require(directory.is_dir() and not directory.is_symlink(),"invalid_role_capture_directory")
        status=process_status(directory)
        require(status["status"]=="exited","role_process_exit_unverified")
        files=self._snapshot(directory)
        require(type(source_events) is list and len(canonical(source_events))<=self.config["max_source_audit_bytes"],"source_audit_overflow")
        request={"attempt":attempt_ref,"files":{k:hashlib.sha256(v).hexdigest() for k,v in files.items()},"source_events":source_events,"timestamp":at}
        def checkpoint(name):
            if fault:fault(name)
        with self.ledger.mutation():
            prior=self.ledger.receipt(request_id,request)
            if prior:return prior
            require(not any(v["attempt"]==attempt_ref for _,v in self.ledger.objects("role_capture","control")),"role_attempt_already_completed")
            capture={"kind":"role_capture","id":"role-capture-"+sha256({"attempt":attempt_ref}),"version":1,
                "attempt":attempt_ref,"packet":attempt["packet"],"category":attempt["category"],"completion_request_id":request_id,
                "files":request["files"],"raw_events_base64":base64.b64encode(files.get("events.jsonl",b"")).decode(),
                "raw_stderr_base64":base64.b64encode(files.get("stderr.bin",b"")).decode(),"source_events":source_events,
                "formal_admission":False,"semantic_validation":"not_established"}
            code=None;materialized=None;transport=None
            try:
                transport=self._verify(attempt,files)
                reader=self._regenerate(attempt["packet"])
                disclosed=self._reads(attempt["packet"],reader,source_events)
                require(transport["transport_success"],transport["error"] or "failed_role_transport")
                if attempt["category"]=="qualification_canary":
                    require(not any(v.get("transport",{}).get("thread_id")==transport["thread_id"] and
                        v["category"]=="qualification_canary" for _,v in self.ledger.objects("role_capture","control")),"reused_subject_thread")
            except (ContractError,ValueError,UnicodeError,KeyError,TypeError) as exc:
                code=getattr(exc,"code","invalid_role_capture")
            capture["transport"]=transport or {"transport_success":False,"usage":None,"total_tokens":None,"error":code}
            capture["validation_code"]=code or "raw_transport_valid"
            capture_ref=self.ledger.put(capture,"control");checkpoint("raw")
            if code is None:
                p=attempt["packet"]["payload"];final=transport["final"];role=p["role"]
                try:
                    with self.ledger.savepoint():
                        materialized=self._materialize(attempt,final,disclosed,capture_ref,capture,attempt_ref,at)
                        if materialized["result"]["status"]=="rejected":code=materialized["result"]["code"]
                except ContractError as exc:code=exc.code
            checkpoint("materialized")
            result={"status":"role_completed","outcome":"rejected" if code else "materialized","code":code or "role_output_committed",
                "attempt":attempt_ref,"capture":capture_ref,"materialization":materialized,"category":attempt["category"],
                "usage":capture["transport"]["usage"],"cost_unknown":attempt["category"]=="qualification_canary" and capture["transport"]["usage"] is None,
                "transport_usage_unknown":capture["transport"]["usage"] is None,
                "reserved_model_sessions":attempt["reserved_model_sessions"],"simulated_usage":attempt["category"]=="infrastructure_acceptance",
                "formal_admission":False,"gain_evidence":False,"promotion_eligible":False}
            receipt=self.ledger.commit_receipt(request_id,request,result,"control");checkpoint("receipt")
            return receipt

    def interrupt(self,request_id,attempt_ref,directory,at,raw_audits=None):
        """Exited, unsealed work is terminal evidence, never materialized anew."""
        identifier(request_id);timestamp(at);reference(attempt_ref,{"role_attempt"})
        require(attempt_ref["namespace"]=="control","invalid_role_attempt_namespace")
        directory=Path(directory);require(directory.is_dir() and not directory.is_symlink(),"invalid_role_capture_directory")
        attempt=self.ledger.get(attempt_ref);begin=self.ledger.receipt(attempt["begin_request_id"])
        require(begin is not None and begin["result"]["attempt"]==attempt_ref,"uncommitted_role_attempt")
        require(attempt["role_config"]==self.role and attempt["admission_config"]==self.config,"role_admission_config_drift")
        status=process_status(directory);require(status["status"]=="exited","role_process_exit_unverified")
        files=self._snapshot(Path(directory))
        audits={} if raw_audits is None else raw_audits
        require(type(audits) is dict and set(audits)<={"source_wire.json","memory_wire.json"} and
            all(type(data) is bytes for data in audits.values()) and sum(len(data) for data in audits.values())<=self.config["max_source_audit_bytes"],"invalid_interrupted_role_audit")
        request={"attempt":attempt_ref,"files":{k:hashlib.sha256(v).hexdigest() for k,v in files.items()},"process_status":status,"timestamp":at,
            "action":"interrupt_no_materialization","unverified_audits":{k:hashlib.sha256(v).hexdigest() for k,v in audits.items()}}
        with self.ledger.mutation():
            prior=self.ledger.receipt(request_id,request)
            if prior:return prior
            require(not any(v["attempt"]==attempt_ref for _,v in self.ledger.objects("role_capture","control")),"role_attempt_already_completed")
            capture=self.ledger.put({"kind":"role_capture","id":"role-capture-"+sha256({"attempt":attempt_ref}),"version":1,
                "attempt":attempt_ref,"packet":attempt["packet"],"category":attempt["category"],"completion_request_id":request_id,
                "files":request["files"],"raw_files":{k:base64.b64encode(v).decode() for k,v in files.items()},
                "unverified_audits":{k:{"base64":base64.b64encode(v).decode(),"sha256":hashlib.sha256(v).hexdigest(),"bytes":len(v)} for k,v in audits.items()},
                "raw_events_base64":base64.b64encode(files.get("events.jsonl",b"")).decode(),
                "raw_stderr_base64":base64.b64encode(files.get("stderr.bin",b"")).decode(),"source_events":[],
                "formal_admission":False,"semantic_validation":"not_established","validation_code":"exited_without_seal_no_retry",
                "process_status":status,"transport":{"transport_success":False,"usage":None,"total_tokens":None,"error":"unsealed_interruption"}},"control")
            result={"status":"role_completed","outcome":"interrupted","code":"exited_without_seal_no_retry","attempt":attempt_ref,
                "capture":capture,"materialization":None,"category":attempt["category"],"usage":None,
                "cost_unknown":attempt["category"]=="qualification_canary","transport_usage_unknown":True,
                "reserved_model_sessions":attempt["reserved_model_sessions"],"simulated_usage":attempt["category"]=="infrastructure_acceptance",
                "formal_admission":False,"gain_evidence":False,"promotion_eligible":False}
            return self.ledger.commit_receipt(request_id,request,result,"control")

    def report(self):
        completed={}
        for ref,capture in self.ledger.objects("role_capture","control"):
            receipt=self.ledger.receipt(capture["completion_request_id"])
            require(receipt is not None and receipt["result"].get("capture")==ref and
                receipt["result"].get("attempt")==capture["attempt"],"uncommitted_role_capture")
            key=canonical(capture["attempt"])
            require(key not in completed,"duplicate_role_completion")
            completed[key]=receipt["result"]
        rows=[];known_tokens=reserved_unknown=reserved_sessions=0;unknown=False
        attempts=list(self.ledger.objects('role_attempt','control'))+list(self.ledger.objects('rpc_role_attempt','control'))
        require(set(completed)<= {canonical(r) for r,_ in attempts},'orphaned_role_capture')
        for ref,attempt in attempts:
            begin=self.ledger.receipt(attempt["begin_request_id"])
            require(begin is not None and begin["result"].get("attempt")==ref,"uncommitted_role_attempt")
            result=completed.get(canonical(ref))
            rows.append({"attempt":ref,"role":attempt["packet"]["payload"]["role"],"category":attempt["category"],
                "status":result["outcome"] if result else "started","completion":result})
            if attempt["category"]=="qualification_canary":
                reserved_sessions+=attempt["reserved_model_sessions"]
                if result is None or result["cost_unknown"]:
                    unknown=True;reserved_unknown+=attempt["budget"]["input_tokens"]+attempt["budget"]["output_tokens"]
                else:known_tokens+=result["usage"]["input_tokens"]+result["usage"]["output_tokens"]
        return {"planned":len(rows),"terminal":len(completed),"rows":rows,"reserved_model_sessions":reserved_sessions,
            "known_diagnostic_model_tokens":known_tokens,"diagnostic_cost_unknown":unknown,"reserved_tokens_pending_or_unknown":reserved_unknown,
            "infrastructure_model_tokens":0,"formal_admission":False,"gain_evidence":False,"promotion_eligible":False}
