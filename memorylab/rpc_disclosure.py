"""Controller-owned RPC adapters over the existing role disclosure services."""
import copy
import hashlib
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from .common import save
from .contracts import bounded_text,canonical,exact_object,require,sha256
from .kernel_cli import parse_json,read_json
from .roles import EvidenceReader,obj,validate_role
from .runtime import OfflineByteTokenizer,RuntimeSession
from .workspace import read_workspace_bytes

ROOT=Path(__file__).resolve().parent.parent


def disclosure_config():
    c=read_json(ROOT/"configs/rpc_disclosure_v0.json")
    exact_object(c,{"schema_version","hypothesis","source_roles","memory_roles","max_calls","max_arguments_bytes",
        "max_reply_bytes","max_total_reply_bytes","max_snapshot_bytes","counting_unit","duplicate_policy","replay_policy","formal_admission","actual_model_calls"},"invalid_rpc_disclosure_config")
    require(c["schema_version"]=="rpc-disclosure-v0" and c["source_roles"]==["extractor","consolidation","reflector"] and
        c["memory_roles"]==["solver","evaluator","proposer"] and c["counting_unit"]=="rpc_wire_bytes_not_model_tokens" and
        c["duplicate_policy"]=="reject_before_service_no_resend" and c["replay_policy"]=="frozen_initial_service_state_exact_production_methods" and
        c["formal_admission"] is False and type(c["actual_model_calls"]) is int and c["actual_model_calls"]==0,"unsupported_rpc_disclosure_policy")
    for k in ("max_calls","max_arguments_bytes","max_reply_bytes","max_total_reply_bytes","max_snapshot_bytes"):
        require(type(c[k]) is int and c[k]>0,"invalid_rpc_disclosure_limit")
    return c


def tool_specs(role):
    c=disclosure_config();string={"type":"string"}
    if role in c["source_roles"]:
        return [{"type":"function","name":"read_evidence","description":"Read a bounded range from one supplied source id.",
            "inputSchema":obj({"id":string,"start":{"type":"integer","minimum":0},"count":{"type":"integer","minimum":1,"maximum":2048}})}]
    require(role in c["memory_roles"],"rpc_disclosure_role_denied")
    return [{"type":"function","name":"open_memory","description":"Expand one already disclosed memory id.","inputSchema":obj({"id":string})}]


def clone_service(service):
    # Production methods consult these frozen values; neither read nor open
    # queries SQLite. Keep the ledger reference outside the deep copy.
    cloned=copy.copy(service)
    for k,v in vars(service).items():
        if k not in ("ledger","persist"):setattr(cloned,k,copy.deepcopy(v))
    cloned.persist=lambda events:None
    return cloned


def service_identity(service):
    if type(service) is EvidenceReader:
        return {"kind":"source","role":service.role,"namespace":service.namespace,"cutoff":service.cutoff,
            "config":service.config,"records":service.records}
    require(type(service) is RuntimeSession,"rpc_disclosure_service_type")
    return {"kind":"memory","projection":service.projection,"config":service.config,"task_text":service.task_text,"enabled":service.enabled}


def bounded_save(path,value,limit):
    require(len((json.dumps(value,ensure_ascii=False,indent=2)+"\n").encode())<=limit,"rpc_disclosure_snapshot_limit")
    save(path,value)


def bounded_json(output,name,limit):
    return parse_json(read_workspace_bytes(output,name,{"max_depth":1,"max_file_bytes":limit}).decode())


class RPCDisclosure:
    @classmethod
    def reopen_initial(cls,packet,service,output):
        output=Path(output)
        require(not (output.parent/'transport').exists(),'rpc_admission_already_started')
        with tempfile.TemporaryDirectory(prefix='memory-disclosure-check-') as tmp:
            gateway=cls(packet,service,Path(tmp)/'disclosure')
            for name in ('initial.json','calls.json','service.json'):
                require(bounded_json(output,name,gateway.config['max_snapshot_bytes'])==
                    bounded_json(gateway.output,name,gateway.config['max_snapshot_bytes']),'rpc_disclosure_initial_resume_drift')
            gateway.output=output;gateway._check();return gateway

    def __init__(self,packet,service,output):
        self.config=disclosure_config();exact_object(packet,{"payload","sha256"},"rpc_disclosure_packet")
        require(packet["sha256"]==sha256(packet["payload"]),"rpc_disclosure_packet_hash")
        self.packet=copy.deepcopy(packet);p=packet["payload"];self.role=p["role"]
        self.specs=tool_specs(self.role);self.service=service
        if self.role in self.config["source_roles"]:
            require(type(service) is EvidenceReader and service.role==self.role and service.namespace==p["namespace"] and
                service.cutoff==p["cutoff"] and service.events==[],"rpc_disclosure_source_binding")
            catalog=p["context"].get("sources") if self.role=="extractor" else p["context"].get("source_catalog")
            require(catalog==service.catalog(),"rpc_disclosure_source_catalog")
        else:
            require(type(service) is RuntimeSession and service.projection["namespace"]==p["namespace"] and service.loaded and
                len(service.events)==1 and service.events[0]["operation"]=="auto_load" and
                service.events[0]["response"]==p["context"].get("briefs"),"rpc_disclosure_memory_binding")
            require(type(service.tokenizer) is OfflineByteTokenizer,"rpc_disclosure_tokenizer_not_qualified")
        self.initial=clone_service(service);self.initial_identity=copy.deepcopy(service_identity(service))
        self.implementation={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in
            ("memorylab/rpc_disclosure.py","memorylab/roles.py","memorylab/runtime.py","memorylab/contracts.py","configs/rpc_disclosure_v0.json")}
        self.binding={"packet":self.packet,"tools":self.specs,"initial_service_sha256":sha256(self.initial_identity),
            "config":self.config,"implementation":self.implementation}
        snapshot={"binding":self.binding,"service":self.initial_identity}
        require(len((json.dumps(snapshot,ensure_ascii=False,indent=2)+"\n").encode())<=self.config["max_snapshot_bytes"],"rpc_disclosure_snapshot_limit")
        self.output=Path(output);require(not self.output.exists(),"rpc_disclosure_output_exists");self.output.mkdir(parents=True)
        self.calls=[];self.thread=None;self.turn=None;self.total_bytes=0
        bounded_save(self.output/"initial.json",snapshot,self.config["max_snapshot_bytes"])
        self._save()
        original=service.persist
        def persist(events):
            original(copy.deepcopy(events));bounded_save(self.output/"service.json",events,self.config["max_snapshot_bytes"])
        service.persist=persist
        bounded_save(self.output/"service.json",service.events,self.config["max_snapshot_bytes"])

    @property
    def binding_sha256(self):return sha256(self.binding)

    def _audit(self):return {"binding_sha256":self.binding_sha256,"thread":self.thread,"turn":self.turn,
        "calls":self.calls,"total_reply_bytes":self.total_bytes,"counting_unit":self.config["counting_unit"]}

    def _save(self):bounded_save(self.output/"calls.json",self._audit(),self.config["max_snapshot_bytes"])

    def _check(self):
        require(self.packet["sha256"]==sha256(self.packet["payload"]) and sha256(service_identity(self.service))==sha256(self.initial_identity),"rpc_disclosure_service_drift")
        require(all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in self.implementation.items()),"rpc_disclosure_implementation_drift")
        require(bounded_json(self.output,"initial.json",self.config["max_snapshot_bytes"])=={"binding":self.binding,"service":self.initial_identity} and
            bounded_json(self.output,"calls.json",self.config["max_snapshot_bytes"])==self._audit() and
            bounded_json(self.output,"service.json",self.config["max_snapshot_bytes"])==self.service.events,"rpc_disclosure_audit_drift")
        if type(self.service) is EvidenceReader:
            remaining=self.service.events[-1]["remaining_bytes"] if self.service.events else self.initial.remaining
            require(self.service.remaining==remaining,"rpc_disclosure_quota_drift")
        else:
            expected=set(self.initial.disclosed)
            for event in self.service.events[len(self.initial.events):]:
                if event["success"] and event["response"] and "memory" in event["response"]:
                    expected.add(event["response"]["memory"]["id"]);expected.update(event["response"]["memory"]["support_ids"])
            require(self.service.remaining==self.service.events[-1]["remaining"] and self.service.disclosed==expected and
                self.service.loaded==self.initial.loaded and self.service.items==self.initial.items,"rpc_disclosure_quota_drift")

    def bind(self,thread,turn):
        self._check();require(self.thread is None and self.turn is None,"rpc_disclosure_session_reused")
        bounded_text(thread,256,"rpc_disclosure_thread");bounded_text(turn,256,"rpc_disclosure_turn")
        self.thread,self.turn=thread,turn;self._save()

    def _params(self,message):
        exact_object(message,{"id","method","params"},"rpc_disclosure_server_request")
        id_=message["id"]
        require(type(id_) is int and 0<=id_ or type(id_) is str and 0<len(id_)<=256,"rpc_disclosure_request_id")
        require(message["method"]=="item/tool/call","rpc_disclosure_method")
        p=message["params"]
        keys={"threadId","turnId","callId","tool","arguments"}
        exact_object(p,keys|({"namespace"} if type(p) is dict and "namespace" in p else set()),"rpc_disclosure_params")
        require(p["threadId"]==self.thread and p["turnId"]==self.turn and self.thread is not None and self.turn is not None,"rpc_disclosure_session")
        bounded_text(p["callId"],256,"rpc_disclosure_call_id")
        require(p.get("namespace") is None and p["tool"]==self.specs[0]["name"],"rpc_disclosure_tool_denied")
        require(len(canonical(p["arguments"]))<=self.config["max_arguments_bytes"],"rpc_disclosure_arguments_limit")
        return p

    def _invoke(self,p,service):
        args=p["arguments"]
        # Preserve extra/invalid fields for the production validator; do not
        # silently normalize a malformed request into a permitted one.
        request=({**args,"op":p["tool"]} if "op" not in args else {"invalid_rpc_arguments":args}) if type(args) is dict else args
        wire=service.read(request) if type(service) is EvidenceReader else service.open(request)
        result={"contentItems":[{"type":"inputText","text":wire.decode()}] if wire else [],
            "success":bool(wire) and "error" not in parse_json(wire.decode())}
        return result

    def reply(self,message):
        self._check();p=self._params(message)
        require(len(self.calls)<self.config["max_calls"],"rpc_disclosure_call_limit")
        require(not any(c["request"]["id"]==message["id"] or c["request"]["params"]["callId"]==p["callId"] for c in self.calls),"rpc_disclosure_duplicate")
        require(not any(c["state"]!="completed" for c in self.calls),"rpc_disclosure_pending")
        row={"request":copy.deepcopy(message),"state":"pending"};self.calls.append(row);self._save()
        result=self._invoke(p,self.service);reply={"id":message["id"],"result":result};wire=canonical(reply)+b"\n"
        require(len(wire)<=self.config["max_reply_bytes"] and self.total_bytes+len(wire)<=self.config["max_total_reply_bytes"],"rpc_disclosure_reply_limit")
        row.update(state="completed",reply=reply,reply_sha256=hashlib.sha256(wire).hexdigest(),reply_bytes=len(wire))
        self.total_bytes+=len(wire);self._save()
        return copy.deepcopy(reply)

    def verify(self,requests,replies):
        self._check()
        return verify_calls(self,requests,replies)


def verify_calls(context,requests,replies):
    require(type(context.calls) is list and len(requests)==len(replies)==len(context.calls),"rpc_disclosure_wire_count")
    require(len(context.calls)<=context.config["max_calls"],"rpc_disclosure_call_limit")
    service=clone_service(context.initial);total=0;request_ids=set();call_ids=set()
    for row,request,reply in zip(context.calls,requests,replies):
        require(row.get("state")=="completed","rpc_disclosure_pending")
        exact_object(row,{"state","request","reply","reply_sha256","reply_bytes"},"rpc_disclosure_call_audit")
        require(row["request"]==request and row["reply"]==reply,"rpc_disclosure_wire_mismatch")
        p=context._params(request)
        require(request["id"] not in request_ids and p["callId"] not in call_ids,"rpc_disclosure_duplicate")
        request_ids.add(request["id"]);call_ids.add(p["callId"])
        expected={"id":request["id"],"result":context._invoke(p,service)}
        wire=canonical(expected)+b"\n";total+=len(wire)
        require(len(wire)<=context.config["max_reply_bytes"] and total<=context.config["max_total_reply_bytes"],"rpc_disclosure_reply_limit")
        require(reply==expected and row["reply_sha256"]==hashlib.sha256(wire).hexdigest() and
            type(row["reply_bytes"]) is int and row["reply_bytes"]==len(wire),"rpc_disclosure_replay_mismatch")
    require(service.events==context.service.events and type(context.total_bytes) is int and total==context.total_bytes,"rpc_disclosure_service_audit_mismatch")
    return {"calls":len(context.calls),"reply_bytes":total,"service_events":len(service.events),"binding_sha256":context.binding_sha256,
        "counting_unit":context.config["counting_unit"],"formal_admission":False}


class SealedDisclosure:
    """Read-only production replay. This object has no send, bind or resume API."""
    _params=RPCDisclosure._params
    _invoke=RPCDisclosure._invoke

    def __init__(self,output,expected_binding_sha256):
        self.output=Path(output);self.config=disclosure_config();limit=self.config["max_snapshot_bytes"]
        snapshot=bounded_json(self.output,"initial.json",limit)
        exact_object(snapshot,{"binding","service"},"rpc_disclosure_snapshot")
        self.binding=snapshot["binding"];identity=snapshot["service"]
        exact_object(self.binding,{"packet","tools","initial_service_sha256","config","implementation"},"rpc_disclosure_binding")
        require(sha256(self.binding)==expected_binding_sha256 and self.binding["initial_service_sha256"]==sha256(identity),"rpc_disclosure_snapshot_binding")
        require(self.binding["config"]==self.config,"rpc_disclosure_config_drift")
        implementation={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in
            ("memorylab/rpc_disclosure.py","memorylab/roles.py","memorylab/runtime.py","memorylab/contracts.py","configs/rpc_disclosure_v0.json")}
        require(self.binding["implementation"]==implementation,"rpc_disclosure_implementation_drift")
        self.packet=self.binding["packet"];exact_object(self.packet,{"payload","sha256"},"rpc_disclosure_packet")
        require(self.packet["sha256"]==sha256(self.packet["payload"]),"rpc_disclosure_packet_hash")
        p=self.packet["payload"];self.role=p["role"];self.specs=tool_specs(self.role)
        require(self.specs==self.binding["tools"],"rpc_disclosure_tool_denied")
        if self.role in self.config["source_roles"]:
            exact_object(identity,{"kind","role","namespace","cutoff","config","records"},"rpc_disclosure_source_snapshot")
            require(identity["kind"]=="source" and identity["role"]==self.role and identity["namespace"]==p["namespace"] and
                identity["cutoff"]==p["cutoff"],"rpc_disclosure_source_binding")
            # EvidenceReader's production read method consumes only the frozen
            # cache and quotas; it never reads a database or any filesystem.
            service=EvidenceReader.__new__(EvidenceReader)
            service.config=validate_role(identity["config"])
            service.role,service.namespace,service.cutoff=identity["role"],identity["namespace"],identity["cutoff"]
            service.records=copy.deepcopy(identity["records"]);service.events=[]
            service.remaining=service.config["source_wire_bytes"];service.persist=lambda events:None
            catalog=p["context"].get("sources") if self.role=="extractor" else p["context"].get("source_catalog")
            require(service.catalog()==catalog,"rpc_disclosure_source_catalog")
        else:
            exact_object(identity,{"kind","projection","config","task_text","enabled"},"rpc_disclosure_memory_snapshot")
            projection=identity["projection"]
            require(identity["kind"]=="memory" and projection["runtime"]==identity["config"] and projection["namespace"]==p["namespace"],"rpc_disclosure_memory_binding")
            require(identity["config"]["tokenizer"]=={"id":OfflineByteTokenizer.id,"live_qualified":False},"rpc_disclosure_tokenizer_not_qualified")
            ref={k:projection[k] for k in ("kind","id","version","namespace")};ref["sha256"]=sha256(projection)
            class ProjectionSnapshot:
                def get(self,requested):
                    require(requested==ref,"rpc_disclosure_projection_binding")
                    return copy.deepcopy(projection)
            service=RuntimeSession(ProjectionSnapshot(),ref,identity["task_text"],enabled=identity["enabled"])
            service.briefs()
            require(service.events[0]["response"]==p["context"].get("briefs"),"rpc_disclosure_memory_binding")
        require(service_identity(service)==identity,"rpc_disclosure_service_drift")
        self.initial=service
        audit=bounded_json(self.output,"calls.json",limit)
        exact_object(audit,{"binding_sha256","thread","turn","calls","total_reply_bytes","counting_unit"},"rpc_disclosure_call_audit")
        require(audit["binding_sha256"]==self.binding_sha256 and audit["counting_unit"]==self.config["counting_unit"],"rpc_disclosure_audit_drift")
        bounded_text(audit["thread"],256,"rpc_disclosure_thread");bounded_text(audit["turn"],256,"rpc_disclosure_turn")
        self.thread,self.turn=audit["thread"],audit["turn"]
        self.calls,self.total_bytes=audit["calls"],audit["total_reply_bytes"]
        self.service=SimpleNamespace(events=bounded_json(self.output,"service.json",limit))

    @property
    def binding_sha256(self):return sha256(self.binding)

    def verify(self,requests,replies):return verify_calls(self,requests,replies)
