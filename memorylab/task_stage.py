"""Real isolated task/grade processes, explicit fixtures, atomic stage records."""
import base64
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from . import sandbox
from .admission import RoleAdmission
from .common import exclusive_run,save
from .contracts import (bounded_text,canonical,exact_object,require,sha256,timestamp)
from .kernel_cli import parse_json,read_json
from .roles import RoleObserver,capture_transport,process_status
from .runtime import OfflineByteTokenizer,RuntimeBuilder,RuntimeSession,RuntimeSocket,UsageStore,file_log
from .scenario import ScenarioPolicy,ScenarioSocket,verify
from .workspace import workspace_snapshot,read_workspace_bytes

ZERO={"session_invocations":0,"input_tokens":0,"output_tokens":0}


def task_config():
    c=read_json(Path(__file__).resolve().parent.parent/"configs/task_stage_v0.json")
    exact_object(c,{"schema_version","hypothesis","mode","max_fixture_bytes","max_seal_bytes","task_roles","automatic_retry",
        "usage_basis","behavior_link_policy","observable_policy","formal_admission","model_calls"},"invalid_task_stage_config")
    require(c["schema_version"]=="task-stage-v0" and c["mode"]=="explicit_python_transport_fixture" and
        c["task_roles"]==["solver","acquisition","oracle"] and c["automatic_retry"] is False and c["formal_admission"] is False and
        type(c["model_calls"]) is int and c["model_calls"]==0 and
        c["usage_basis"]=="served_memory_and_self_claim_separate_from_model_read_and_causal_benefit" and
        c["behavior_link_policy"]=="no_automatic_inference_from_open_or_claim" and
        c["observable_policy"]=="whole_event_or_explicit_unavailable_metadata","unsupported_task_stage_policy")
    for key in ("max_fixture_bytes","max_seal_bytes"):
        require(type(c[key]) is int and c[key]>0,"invalid_task_stage_limit")
    return c


class TaskStages:
    def __init__(self,inputs,output):
        self.inputs,self.journal,self.ledger,self.cards=inputs,inputs.journal,inputs.ledger,inputs.cards
        self.config=task_config();self.output=Path(output)
        require(not self.output.is_symlink(),"invalid_task_stage_output");self.output.mkdir(parents=True,exist_ok=True)
        self.output=self.output.resolve();self.validator=RoleAdmission(self.cards,inputs.registry,role=inputs.role)

    def _directory(self,slot):return self.output/("task-"+slot)

    def _command(self,worker,code):
        return sandbox.command(worker,self.ledger.path.parent,[sys.executable,"-I","-B","-c",code],True,False,
            denied_roots=(self.inputs.output,self.output,*self.inputs.denied_roots))

    def _memory(self,p,persist=None):
        r=p["resources"];projection=r.get("projection")
        if projection is None:
            # Memory-off paths contain no checkpoint cards, irrespective of the
            # selected mechanism. No derived generation or model call occurs.
            projection=RuntimeBuilder(self.cards,self.inputs.registry).build(p["namespace"],0,self.inputs.graph["memory_refs"][0],{})["projection_ref"]
        return RuntimeSession(self.ledger,projection,p["packet"]["payload"]["context"]["current_contract"],persist=persist,enabled=r.get("projection") is not None)

    @staticmethod
    def _restore(files,root):
        for name,data in files.items():
            relative=Path(name)
            require(not relative.is_absolute() and all(part not in (".","..") for part in relative.parts),"invalid_task_file_path")
            target=root/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)

    def run_fixture(self,slot,code,at,fault=None):
        """No implicit fixture, Codex invocation, retry, or real-model category."""
        timestamp(at);bounded_text(code,self.config["max_fixture_bytes"],"task_fixture_overflow")
        with exclusive_run(self.output):
            input_ref=self.inputs.prepare(slot);value=self.inputs.resolve(input_ref);p=value["payload"]
            require(p["role"] in self.config["task_roles"] and p["store_scope"]=="main","unsupported_task_stage_role")
            request={"input":input_ref,"fixture_sha256":hashlib.sha256(code.encode()).hexdigest(),"config":self.config,"output":str(self.output)}
            start_id="task-start-"+sha256({"plan":self.inputs.plan,"slot":slot})
            existing=self.ledger.receipt(start_id,request)
            if existing:
                execution=self.ledger.get(existing["result"]["execution"])
                completed=self.ledger.receipt(execution["complete_request_id"])
                if completed:return {**self.resolve_task(slot),"cache_hit":True,"new_model_calls":0}
                directory=self._directory(slot)
                status=process_status(directory/"transport")
                require(status["status"]=="exited","task_process_exit_unverified")
                if (directory/"seal.json").exists():return self.commit_task(existing["result"]["execution"],at,fault)
                return self._interrupted(existing["result"]["execution"],status,at,fault)
            worker=Path(tempfile.mkdtemp(prefix="memory-task-",dir="/tmp"))
            try:
                self._restore({name:text.encode() for name,text in p["resources"]["task_files"].items()},worker)
                with self.ledger.mutation():
                    begun=self.journal.begin_bound("stage-task-"+sha256(request),input_ref,self.inputs)["result"]
                    require(begun["status"]=="stage_started","task_stage_already_terminal")
                    execution=self.ledger.put({"kind":"task_execution","id":"execution-"+sha256(request),"version":1,"request":request,
                        "input":input_ref,"attempt":begun["attempt"],"worker":str(worker),"fixture_code":code,
                        "packet":p["packet"],"budget":p["budget"],"role_config":self.inputs.role,"category":"infrastructure_acceptance",
                        "control_root":str(self.output),"start_request_id":start_id,"complete_request_id":"task-end-"+sha256(request)},"control")
                    self.ledger.commit_receipt(start_id,request,{"status":"task_execution_started","execution":execution},"control")
                directory=self._directory(slot);directory.mkdir(parents=True,exist_ok=False)
                memory=self._memory(p,file_log(directory/"memory_wire.json"))
                require(json.loads(memory.briefs())==p["packet"]["payload"]["context"]["briefs"],"task_brief_input_mismatch")
                scenario=ScenarioPolicy(worker,self.ledger.path.parent,p["resources"]["episode"],self.inputs.contract,persist=file_log(directory/"scenario_wire.json"),
                    denied_roots=(self.inputs.output,self.output,*self.inputs.denied_roots))
                save(directory/"scenario_wire.json",[])
                env=sandbox.worker_environment()
                with RuntimeSocket(worker,memory),ScenarioSocket(worker,scenario):
                    capture_transport(self._command(worker,code),worker,
                        p["packet"],p["budget"],directory/"transport",self.inputs.role,env,
                        {"category":"scripted_transport_fixture","model_session_invocations":0})
                if fault:fault("after_process")
                self._seal(execution,worker,directory)
                if fault:fault("after_seal")
                return self.commit_task(execution,at,fault)
            finally:shutil.rmtree(worker)

    def _seal(self,execution_ref,worker,directory):
        files=self.validator._snapshot(directory/"transport")
        try:
            workspace={name:base64.b64encode(data).decode() for name,data in workspace_snapshot(worker).items()};boundary=None
        except (ValueError,OSError) as exc:workspace=None;boundary=getattr(exc,"code",type(exc).__name__)
        seal={"execution":execution_ref,"files":{name:hashlib.sha256(data).hexdigest() for name,data in files.items()},
            "workspace":workspace,"workspace_boundary_error":boundary,"memory_events":read_json(directory/"memory_wire.json"),
            "scenario_events":read_json(directory/"scenario_wire.json")}
        if len(canonical(seal))>self.config["max_seal_bytes"]:
            seal["workspace"]=None;seal["workspace_boundary_error"]="task_seal_byte_limit"
        require(len(canonical(seal))<=self.config["max_seal_bytes"],"task_audit_seal_overflow")
        save(directory/"seal.json",seal)

    def _read_execution(self,ref):
        execution=self.ledger.get(ref);start=self.ledger.receipt(execution["start_request_id"],execution["request"])
        require(start is not None and start["result"].get("execution")==ref,"uncommitted_task_execution")
        p=self.inputs.resolve(execution["input"])["payload"]
        require(execution["packet"]==p["packet"] and execution["budget"]==p["budget"] and execution["role_config"]==self.inputs.role and
            execution["request"]["config"]==self.config and execution["control_root"]==str(self.output) and
            execution["request"]["fixture_sha256"]==hashlib.sha256(execution["fixture_code"].encode()).hexdigest(),"task_execution_input_drift")
        return execution,p

    def _memory_audit(self,p,events):
        require(type(events) is list and events,"missing_task_memory_audit")
        memory=self._memory(p);memory.briefs()
        require(memory.events[0]==events[0],"task_memory_audit_mismatch")
        for event in events[1:]:
            if event.get("request")=={"invalid_wire":True}:
                memory._emit("detail","open",event["request"],{"error":"invalid_memory_wire"})
            else:memory.open(event["request"])
            require(memory.events[-1]==event,"task_memory_audit_mismatch")
        require(memory.events==events,"task_memory_audit_mismatch")

    def _tool_audit(self,events):
        require(type(events) is list and len(events)<=self.inputs.contract["max_tool_requests"],"invalid_task_scenario_audit")
        for i,event in enumerate(events):
            require(event["sequence"]==i+1 and type(event["delivered"]) is bool and event["request"] in ({"op":"inspect"},{"op":"check"}),"invalid_task_scenario_audit")
            wire=canonical(event["response"])+b"\n"
            require(event["wire_sha256"]==hashlib.sha256(wire).hexdigest() and event["bytes"]==len(wire) and
                event["tokens"]==OfflineByteTokenizer().count(wire) and event["tokenizer"]=={"id":OfflineByteTokenizer.id,"live_qualified":False},"task_scenario_wire_mismatch")

    def _source(self,ns,episode,index,type_,text,at,key):
        if len(text.encode())>self.cards.kernel["limits"]["source_text_bytes"]:
            text=canonical({"status":"unavailable","reason":"whole_event_exceeds_source_limit","original_sha256":hashlib.sha256(text.encode()).hexdigest(),
                "original_bytes":len(text.encode())}).decode()
        return self.cards.evidence.register("task-src-"+sha256({"key":key,"index":index}),ns,type_,episode,at,text)

    def _sealed(self,directory,name):
        return parse_json(read_workspace_bytes(directory,name,{"max_depth":1,"max_file_bytes":self.config["max_seal_bytes"]}).decode())

    def _verify_committed_capture(self,execution_ref,execution,p,capture):
        directory=self._directory(p["slot"])
        files=self.validator._snapshot(directory/"transport")
        require({name:base64.b64encode(data).decode() for name,data in files.items()}==capture["raw_files"],"task_capture_raw_drift")
        status=process_status(directory/"transport")
        require(status["status"]=="exited","task_process_exit_unverified")
        seal=capture["seal"]
        if seal is None:
            require(not (directory/"seal.json").exists() and capture["process_status"]["status"]=="exited" and
                capture["process_status"]["handle"]==status["handle"] and
                capture["raw_sha256"]=={name:hashlib.sha256(data).hexdigest() for name,data in files.items()},"task_interruption_drift")
            return
        require(self._sealed(directory,"seal.json")==seal and seal["execution"]==execution_ref,"task_capture_seal_drift")
        require(self._sealed(directory,"memory_wire.json")==seal["memory_events"] and
            self._sealed(directory,"scenario_wire.json")==seal["scenario_events"],"task_capture_audit_drift")
        require(RoleAdmission._verify(execution,files)==capture["transport"],"task_capture_transport_drift")
        require(parse_json(files["start.json"].decode())["command"]==self._command(execution["worker"],execution["fixture_code"]),"task_fixture_command_drift")
        self._memory_audit(p,seal["memory_events"]);self._tool_audit(seal["scenario_events"])

    def commit_task(self,execution_ref,at,fault=None):
        timestamp(at);execution,p=self._read_execution(execution_ref);slot=p["slot"];directory=self._directory(slot)
        prior=self.ledger.receipt(execution["complete_request_id"])
        if prior:return {**self.resolve_task(slot),"cache_hit":True,"new_model_calls":0}
        require(process_status(directory/"transport")["status"]=="exited","task_process_exit_unverified")
        seal=read_json(directory/"seal.json");require(seal["execution"]==execution_ref,"task_seal_execution_mismatch")
        require(len(canonical(seal))<=self.config["max_seal_bytes"],"task_seal_byte_limit")
        files=self.validator._snapshot(directory/"transport")
        require(seal["files"]=={name:hashlib.sha256(data).hexdigest() for name,data in files.items()},"task_seal_raw_drift")
        transport=RoleAdmission._verify(execution,files)
        start=parse_json(files["start.json"].decode())
        require(start["command"]==self._command(execution["worker"],execution["fixture_code"]),"task_fixture_command_drift")
        self._memory_audit(p,seal["memory_events"]);self._tool_audit(seal["scenario_events"])
        observer=RoleObserver(p["budget"],self.inputs.role);observer.feed(files["events.jsonl"])
        final=transport["final"];items={i["id"]:i for i in self.ledger.get(p["resources"]["projection"])["items"]} if p["resources"].get("projection") else {}
        def checkpoint(name):
            if fault:fault(name)
        with self.ledger.mutation():
            capture=self.ledger.put({"kind":"task_capture","id":"capture-"+sha256(execution_ref),"version":1,"execution":execution_ref,
                "seal":seal,"raw_files":{name:base64.b64encode(data).decode() for name,data in files.items()},"transport":transport,
                "category":"infrastructure_acceptance","formal_admission":False},"control");checkpoint("capture")
            rows=[("user_input",p["packet"]["payload"]["context"]["current_contract"])]
            for event in seal["scenario_events"]:
                if event["delivered"]:rows.append(("tool_result",canonical({"request":event["request"],"response":event["response"]}).decode()))
            for event in observer.events:
                item=event.get("item",{})
                if event["type"]=="item.completed" and item.get("type")=="command_execution":
                    rows.append(("tool_result",canonical({k:item[k] for k in ("id","command","aggregated_output","exit_code","status") if k in item}).decode()))
            if final is not None:rows.append(("agent_output",canonical(final).decode()))
            solved=seal["workspace"]
            after=None;unavailable=None
            if solved is not None and "engine.py" in solved:
                raw=base64.b64decode(solved["engine.py"],validate=True)
                try:after=raw.decode("utf-8")
                except UnicodeDecodeError:
                    unavailable={"reason":"non_utf8_task_source","bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}
            rows.append(("file_diff",canonical({"before":p["resources"]["task_files"]["engine.py"],"after":after,"boundary_error":seal["workspace_boundary_error"],"unavailable":unavailable}).decode()))
            sources=[self._source(p["namespace"],slot,i,t,text,at,execution_ref) for i,(t,text) in enumerate(rows)];checkpoint("sources")
            usage=UsageStore(self.cards);opened=set()
            for i,event in enumerate(seal["memory_events"]):
                for ref in event["memory_refs"] if event["success"] else []:
                    usage.record("task-use-"+sha256({"execution":execution_ref,"event":i,"ref":ref}),p["namespace"],slot,event["operation"],ref)
                    if event["operation"]=="open":opened.add(ref["id"])
            claimed=final.get("memory_uses",[]) if type(final) is dict else []
            if type(claimed) is not list or not all(type(x) is str for x in claimed):claimed=[]
            for id_ in dict.fromkeys(claimed):
                if id_ in items:usage.record("task-claim-"+sha256({"execution":execution_ref,"id":id_}),p["namespace"],slot,"claimed_use",items[id_]["ref"])
            checkpoint("usage")
            outcome="completed" if transport["transport_success"] and solved is not None else "failed"
            artifacts={"task_capture":capture,"observable_sources":sources,"claimed_without_open":[i for i in claimed if i not in opened],
                "runtime_usage_basis":self.config["usage_basis"]}
            finish=self.journal.complete("task-finish-"+sha256(execution_ref),execution["attempt"],outcome,ZERO,[],artifacts);checkpoint("stage")
            result={"status":"task_capture_committed","capture":capture,"stage":finish,"actual_model_calls":0,"transport_usage":transport["usage"],
                "simulated_transport_usage":True,"formal_admission":False,"gain_evidence":False,"cache_hit":False,"new_model_calls":0}
            self.ledger.commit_receipt(execution["complete_request_id"],{"execution":execution_ref,"seal_sha256":sha256(seal),"timestamp":at},result,"control");checkpoint("receipt")
            return result

    def _interrupted(self,execution_ref,status,at,fault):
        execution,p=self._read_execution(execution_ref)
        files=self.validator._snapshot(self._directory(p["slot"])/"transport")
        with self.ledger.mutation():
            capture=self.ledger.put({"kind":"task_capture","id":"capture-"+sha256(execution_ref),"version":1,"execution":execution_ref,
                "seal":None,"transport":None,"process_status":status,"category":"infrastructure_acceptance","formal_admission":False,
                "raw_files":{name:base64.b64encode(data).decode() for name,data in files.items()},
                "raw_sha256":{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}},"control")
            if fault:fault("capture")
            finish=self.journal.complete("task-finish-"+sha256(execution_ref),execution["attempt"],"interrupted",ZERO,[],{"task_capture":capture,"observable_sources":[],"reason":"exited_without_seal_no_retry"})
            if fault:fault("stage")
            result={"status":"task_capture_committed","capture":capture,"stage":finish,"actual_model_calls":0,"formal_admission":False,
                "gain_evidence":False,"cache_hit":False,"new_model_calls":0}
            self.ledger.commit_receipt(execution["complete_request_id"],{"execution":execution_ref,"process_status":status,"timestamp":at},result,"control")
            if fault:fault("receipt")
            return result

    def grade(self,slot,at,fault=None):
        timestamp(at)
        with exclusive_run(self.output):
            input_ref=self.inputs.prepare(slot);p=self.inputs.resolve(input_ref)["payload"]
            require(p["role"]=="grade","not_task_grade_stage")
            producer=self.inputs.nodes[slot]["bindings"]["solver_receipt"]
            dep=self.inputs._dependency(producer);capture_ref=dep["artifacts"].get("task_capture")
            if capture_ref is None and dep["result"]["outcome"]=="blocked":
                return self._grade_unrun(input_ref,p,producer,dep,at,fault)
            require(capture_ref is not None,"missing_task_stage_capture")
            capture=self.ledger.get(capture_ref);execution,task=self._read_execution(capture["execution"])
            committed=self.ledger.receipt(execution["complete_request_id"])
            require(committed is not None and committed["result"]["capture"]==capture_ref and task["slot"]==producer,"uncommitted_task_stage_capture")
            self._verify_committed_capture(capture["execution"],execution,task,capture)
            request_id="grade-end-"+sha256({"input":input_ref});prior=self.ledger.receipt(request_id)
            if prior:return {**self.resolve_grade(slot)["result"],"cache_hit":True}
            begun=self.journal.begin_bound("grade-start-"+sha256({"input":input_ref}),input_ref,self.inputs)["result"]
            require(begun["status"]=="stage_started","task_grade_already_terminal")
            directory=self.output/("grade-"+slot);directory.mkdir(exist_ok=True)
            sealed=directory/"seal.json"
            if sealed.exists():
                saved=read_json(sealed)
                exact_object(saved,{"input","capture","grade","grade_sha256"},"invalid_grade_seal")
                require(saved["input"]==input_ref and saved["capture"]==capture_ref and saved["grade_sha256"]==sha256(saved["grade"]),"task_grade_seal_mismatch")
                grade=saved["grade"]
            else:
                workspace=capture.get("seal",{}).get("workspace") if capture.get("seal") else None
                if workspace is None:
                    grade={"full_success":False,"behavior_success":False,"verification":"unavailable","status":"task_workspace_unavailable"}
                else:
                    with tempfile.TemporaryDirectory(prefix="memory-grade-",dir="/tmp") as temporary:
                        root=Path(temporary);self._restore({name:base64.b64decode(data,validate=True) for name,data in workspace.items()},root)
                        protected={name:hashlib.sha256(text.encode()).hexdigest() for name,text in task["resources"]["task_files"].items() if name!="engine.py"}
                        grade=verify(root,self.ledger.path.parent,task["resources"]["episode"],protected,capture["transport"]["final"],self.inputs.contract,
                            denied_roots=(self.inputs.output,self.output,*self.inputs.denied_roots))
                        grade["full_success"]=bool(grade["full_success"] and capture["transport"]["transport_success"])
                        grade["verification"]="verified"
                save(sealed,{"input":input_ref,"capture":capture_ref,"grade":grade,"grade_sha256":sha256(grade)})
            if fault:fault("after_grade")
            with self.ledger.mutation():
                self.inputs.resolve(input_ref)
                ref=self.ledger.put({"kind":"task_grade","id":"grade-"+sha256(input_ref),"version":1,"input":input_ref,"capture":capture_ref,
                    "value":grade,"category":"infrastructure_acceptance","formal_admission":False},"control")
                if fault:fault("grade")
                public={k:grade[k] for k in ("full_success","behavior_success","verification")}
                source=self._source(task["namespace"],producer,0,"visible_outcome",canonical(public).decode(),at,input_ref)
                if fault:fault("outcome")
                UsageStore(self.cards).record("grade-use-"+sha256(input_ref),task["namespace"],producer,"independent_outcome",
                    outcome="unknown" if grade["verification"]!="verified" else "success" if grade["full_success"] else "failure",evidence_ref=source)
                if fault:fault("usage")
                finish=self.journal.complete("grade-finish-"+sha256(input_ref),begun["attempt"],"completed",ZERO,[],{"grade":ref,"observable_sources":[source]})
                if fault:fault("stage")
                result={"status":"task_grade_committed","grade":ref,"stage":finish,"actual_model_calls":0,"formal_admission":False,"gain_evidence":False,"cache_hit":False}
                self.ledger.commit_receipt(request_id,{"input":input_ref,"grade_sha256":sha256(grade),"timestamp":at},result,"control")
                if fault:fault("receipt")
                return result

    def _grade_unrun(self,input_ref,p,producer,dep,at,fault):
        """A closed, never executed task is unavailable, never successful."""
        request_id="grade-end-"+sha256({"input":input_ref});prior=self.ledger.receipt(request_id)
        if prior:return {**self.resolve_grade(p["slot"])["result"],"cache_hit":True}
        begun=self.journal.begin_bound("grade-start-"+sha256({"input":input_ref}),input_ref,self.inputs)["result"]
        require(begun["status"]=="stage_started","task_grade_already_terminal")
        grade={"full_success":False,"behavior_success":False,"verification":"unavailable","status":"task_not_executed",
            "producer_receipt_sha256":dep["receipt_sha256"]}
        with self.ledger.mutation():
            self.inputs.resolve(input_ref)
            ref=self.ledger.put({"kind":"task_grade","id":"grade-"+sha256(input_ref),"version":1,"input":input_ref,"capture":None,
                "value":grade,"category":"infrastructure_acceptance","formal_admission":False},"control")
            if fault:fault("grade")
            public={k:grade[k] for k in ("full_success","behavior_success","verification")}
            ns=self.inputs.nodes[producer]["namespace"]
            source=self._source(ns,producer,0,"visible_outcome",canonical(public).decode(),at,input_ref)
            if fault:fault("outcome")
            UsageStore(self.cards).record("grade-use-"+sha256(input_ref),ns,producer,"independent_outcome",outcome="unknown",evidence_ref=source)
            if fault:fault("usage")
            finish=self.journal.complete("grade-finish-"+sha256(input_ref),begun["attempt"],"completed",ZERO,[],{"grade":ref,"observable_sources":[source]})
            if fault:fault("stage")
            result={"status":"task_grade_committed","grade":ref,"stage":finish,"actual_model_calls":0,"formal_admission":False,"gain_evidence":False,"cache_hit":False}
            self.ledger.commit_receipt(request_id,{"input":input_ref,"grade_sha256":sha256(grade),"timestamp":at},result,"control")
            if fault:fault("receipt")
            return result

    def resolve_grade(self,slot):
        with self.ledger.mutation(),self.ledger.cached_reads(self.inputs.config["ledger_decode_cache_bytes"]):
            return self._resolve_grade(slot)

    def _resolve_grade(self,slot):
        input_ref=self.inputs.prepare(slot);p=self.inputs.resolve(input_ref)["payload"]
        require(p["role"]=="grade","not_task_grade_stage")
        d=self.inputs._dependency(slot);ref=d["artifacts"].get("grade")
        require(ref is not None,"missing_task_grade")
        value=self.ledger.get(ref);grade=value["value"]
        require(value["kind"]=="task_grade" and value["input"]==input_ref and value["formal_admission"] is False and
            value["category"]=="infrastructure_acceptance","task_grade_binding_drift")
        producer=self.inputs.nodes[slot]["bindings"]["solver_receipt"];dep=self.inputs._dependency(producer)
        require(value["capture"]==dep["artifacts"].get("task_capture"),"task_grade_capture_drift")
        if value["capture"] is None:
            require(dep["result"]["outcome"]=="blocked" and grade.get("producer_receipt_sha256")==dep["receipt_sha256"] and
                grade["verification"]=="unavailable" and grade["full_success"] is False and grade["behavior_success"] is False,
                "unexecuted_task_grade_drift")
        else:
            capture=self.ledger.get(value["capture"]);execution,task=self._read_execution(capture["execution"])
            committed=self.ledger.receipt(execution["complete_request_id"])
            require(committed is not None and committed["result"]["capture"]==value["capture"] and task["slot"]==producer and
                committed["result"]["stage"]["result"]==dep["result"],"uncommitted_task_stage_capture")
            self._verify_committed_capture(capture["execution"],execution,task,capture)
            expected={"input":input_ref,"capture":value["capture"],"grade":grade,"grade_sha256":sha256(grade)}
            require(self._sealed(self.output/("grade-"+slot),"seal.json")==expected,"task_grade_seal_mismatch")
        refs=self.inputs._sources(slot,self.inputs.nodes[producer]["namespace"],d["sequence"],self.cards,None)
        require(len(refs)==1,"task_grade_public_source_drift")
        source=self.cards.evidence.read(refs[0],"extractor",self.inputs.nodes[producer]["namespace"],d["sequence"])
        require(source["text"]==canonical({k:grade[k] for k in ("full_success","behavior_success","verification")}).decode(),
            "task_grade_public_source_drift")
        end=self.ledger.receipt("grade-end-"+sha256({"input":input_ref}),
            {"input":input_ref,"grade_sha256":sha256(grade),"timestamp":source["timestamp"]})
        require(end is not None and end["result"]["grade"]==ref and end["result"]["stage"]["result"]==d["result"],"uncommitted_task_grade")
        usage={"namespace":source["namespace"],"episode":producer,"event_type":"independent_outcome","memory_ref":None,
            "outcome":"unknown" if grade["verification"]!="verified" else "success" if grade["full_success"] else "failure","evidence_ref":refs[0]}
        require(self.ledger.receipt("grade-use-"+sha256(input_ref),usage) is not None,"uncommitted_task_grade_usage")
        return {"ref":ref,"value":grade,"result":end["result"],"dependency":d}

    def resolve_task(self,slot):
        with self.ledger.mutation(),self.ledger.cached_reads(self.inputs.config["ledger_decode_cache_bytes"]):
            start=self.ledger.receipt("task-start-"+sha256({"plan":self.inputs.plan,"slot":slot}))
            require(start is not None,"missing_task_stage_execution")
            execution_ref=start["result"]["execution"];e,p=self._read_execution(execution_ref)
            end=self.ledger.receipt(e["complete_request_id"])
            require(end is not None and p["slot"]==slot,"unpublished_task_stage")
            capture=self.ledger.get(end["result"]["capture"]);d=self.inputs._dependency(slot)
            require(capture["execution"]==execution_ref and d["artifacts"].get("task_capture")==end["result"]["capture"] and
                d["result"]==end["result"]["stage"]["result"] and capture["formal_admission"] is False,"task_stage_finish_drift")
            self._verify_committed_capture(execution_ref,e,p,capture)
            sources=self.inputs._sources(slot,p["namespace"],d["sequence"],self.cards,None)
            if capture["seal"] is not None:
                require(capture["seal"]["execution"]==execution_ref and sources,"task_capture_binding_drift")
                at=self.cards.evidence.read(sources[0],"extractor",p["namespace"],d["sequence"])["timestamp"]
                require(end["request_sha256"]==sha256({"execution":execution_ref,"seal_sha256":sha256(capture["seal"]),"timestamp":at}),
                    "task_capture_receipt_drift")
            else:require(d["result"]["outcome"]=="interrupted" and not sources,"task_capture_binding_drift")
            return end["result"]
