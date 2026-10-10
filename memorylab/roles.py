"""Independent role packets, read-only evidence disclosure and bounded Codex IO.

Canary transport is never a formal result or a promotion proof.
"""
import copy
import hashlib
import os
import socket
import sys
import threading
from pathlib import Path

from .contracts import (ContractError, bounded_text, canonical, exact_object, identifier,
                        namespace, reference, require, sha256, text_hash)
from .derived import DerivedStage
from .history import SECRET
from .kernel_cli import parse_json, read_json
from .process import bounded_run
from . import sandbox

SOURCE_ROLES = {"extractor", "consolidation", "reflector"}
TASK_ROLES = {"solver", "acquisition", "oracle"}
RESEARCH_ROLES = {"evaluator", "proposer"}


def role_config(path=None):
    return validate_role(read_json(path or Path(__file__).resolve().parent.parent/"configs/role_v0.json"))


def validate_role(c):
    exact_object(c, {"schema_version", "status", "hypothesis", "requested_model", "cli_path", "cli_version", "cli_sha256",
        "reasoning_effort", "live_execution_authorized", "transport", "execution_identity", "authentication_policy", "formal_admission", "automatic_retry",
        "max_packet_bytes", "stdout_bytes", "stderr_bytes", "max_event_line_bytes", "max_events", "source_requests",
        "source_wire_bytes", "source_chunk_chars", "source_wire_request_bytes", "tool_seconds", "solver_prompt",
        "research_prompt", "source_prompt"}, "invalid_role_config")
    require(c["schema_version"]=="role-contract-v0" and c["transport"]=="codex_exec_jsonl", "unsupported_role_contract")
    require(c["execution_identity"]=="source_and_registered_resources_sha256","unsupported_role_execution_identity")
    require(c["authentication_policy"]=="isolated_auth_not_qualified","unsupported_role_authentication_policy")
    require(c["formal_admission"] is False and c["automatic_retry"] is False and
        c["live_execution_authorized"] is False, "unsafe_role_policy")
    for k in ("requested_model", "cli_path", "cli_version", "cli_sha256"):
        require(c[k] is None or (type(c[k]) is str and bool(c[k].strip())), "invalid_role_identity")
    require(c["cli_sha256"] is None or len(c["cli_sha256"])==64 and all(x in "0123456789abcdef" for x in c["cli_sha256"]),"invalid_role_cli_digest")
    require(c["reasoning_effort"] in {"low","medium","high","xhigh","max"}, "unsupported_role_effort")
    for k in ("max_packet_bytes", "stdout_bytes", "stderr_bytes", "max_event_line_bytes", "max_events", "source_requests",
              "source_wire_bytes", "source_chunk_chars", "source_wire_request_bytes", "tool_seconds"):
        require(type(c[k]) is int and c[k]>0, "invalid_role_limit")
    require(c["source_chunk_chars"]<=2048 and c["source_wire_request_bytes"]<=8192, "unsupported_source_wire_limit")
    for k in ("solver_prompt","research_prompt","source_prompt"): bounded_text(c[k],16000,"invalid_role_prompt")
    return c


def obj(properties):
    return {"type":"object","properties":properties,"required":list(properties),"additionalProperties":False}


def ref_schema(kinds):
    return obj({"kind":{"type":"string","enum":list(kinds)},"id":{"type":"string"},
        "version":{"type":"integer","minimum":1},"namespace":{"type":"string"},"sha256":{"type":"string"}})


def output_schema(role, kernel):
    string={"type":"string"}; arr=lambda item:{"type":"array","items":item}
    if role in TASK_ROLES:
        return obj({"summary":string,"completion_claim":{"type":"string","enum":["complete","partial"]},"memory_uses":arr(string)})
    if role=="extractor":
        card=obj({"id":string,"expected_version":{"type":"integer","minimum":0},
            "type_hint":{"type":"string","enum":kernel["taxonomy"]},"brief":string,"detail":string,
            "evidence_refs":arr(ref_schema(["source"])),"outcome":{"type":"string","enum":["success","failure","unknown"]},
            "status":{"type":"string","enum":["active","withdrawn"]},"supersedes":arr(ref_schema(["card"]))})
        return obj({"cards":arr(card)})
    if role=="consolidation":
        return obj({"indices":arr(obj({"type_hint":{"type":"string","enum":kernel["taxonomy"]+["root"]},
            "brief":string,"detail":string,"support_refs":arr(ref_schema(["card"]))}))})
    if role=="reflector":return obj({"annotations":arr(obj({"support":ref_schema(["card"]),"text":string}))})
    require(role in RESEARCH_ROLES,"invalid_model_role")
    kind="memory" if role=="proposer" else "scenario"
    primitive={"anyOf":[{"type":t} for t in ("string","integer","boolean","null")]}
    return obj({"schema_version":{"type":"integer","enum":[1]},"kind":{"type":"string","enum":[kind]},
        "parent":ref_schema([kind]),"mutation":{"anyOf":[{"type":"null"},obj({"field":string,"old":primitive,"new":primitive})]},
        "hypothesis":string,"evidence_refs":arr(ref_schema(["source","card"]))})


class EvidenceReader:
    """Validated immutable cache: socket threads never query mutable SQLite."""
    def __init__(self, cards, role, ns, cutoff, refs, config=None, persist=None):
        require(role in SOURCE_ROLES,"role_source_reader_denied")
        self.config=validate_role(config) if config is not None else role_config()
        self.role,self.namespace,self.cutoff=role,ns,cutoff
        self.persist=persist or (lambda events:None)
        require(type(refs) is list and len({canonical(r) for r in refs})==len(refs),"duplicate_role_evidence")
        self.records={};self.events=[];self.remaining=self.config["source_wire_bytes"]
        access="extractor" if role=="extractor" else "reflector"
        for ref in refs:
            reference(ref,{"source","card"})
            value=cards.evidence.read(ref,access,ns,cutoff) if ref["kind"]=="source" else cards.read(ref,access,ns,cutoff)
            text=value["text"] if ref["kind"]=="source" else canonical(value).decode()
            require(not SECRET.search(text.encode()),"role_secret_material")
            alias="read-"+sha256(ref)
            header={"id":alias,"ref":copy.deepcopy(ref),"text_sha256":text_hash(text),
                "text_bytes":len(text.encode()),"text_chars":len(text),"timestamp":value["timestamp"]}
            if ref["kind"]=="source":header.update(event_type=value["event_type"],episode=value["episode"])
            else:header.update(type_hint=value["type_hint"],brief=value["brief"],outcome=value["outcome"])
            self.records[alias]={"header":header,"text":text}

    def catalog(self):return [copy.deepcopy(v["header"]) for v in self.records.values()]

    def _emit(self, request, response):
        wire=canonical(response)+b"\n"
        if len(wire)>self.remaining:
            response={"error":"role_source_wire_limit"};wire=canonical(response)+b"\n"
        if len(wire)>self.remaining:wire=b""
        event={"sequence":len(self.events)+1,"request":copy.deepcopy(request),"response":response if wire else None,
            "wire_sha256":hashlib.sha256(wire).hexdigest(),"bytes":len(wire),"remaining_bytes":self.remaining-len(wire),
            "counting_unit":"wire_bytes_not_model_tokens"}
        self.persist(copy.deepcopy(self.events+[event]))
        self.events.append(event);self.remaining-=len(wire)
        return wire

    def read(self, request):
        # Once the finite service quota is consumed, answer with a minimal
        # non-empty envelope (protocol 2026-10-10): a zero-byte wire is
        # undeliverable on message-only transports and stalls the actor on a
        # reserved followup. The envelope spends wire bytes, not requests.
        if len(self.events)>=self.config["source_requests"]:
            try:
                exact_object(request,{"op","id","start","count"},"invalid_role_source_request")
                recorded=request
            except (ContractError,TypeError):
                recorded={"invalid":True}
            return self._emit(recorded,{"error":"role_source_quota_exhausted"})
        try:
            exact_object(request,{"op","id","start","count"},"invalid_role_source_request")
            require(request["op"]=="read_evidence","unsupported_role_source_operation")
            identifier(request["id"]);require(request["id"] in self.records,"undeclared_role_evidence")
            start,count=request["start"],request["count"];record=self.records[request["id"]]
            require(type(start) is int and 0<=start<=len(record["text"]) and type(count) is int and
                0<count<=self.config["source_chunk_chars"],"invalid_role_source_range")
            end=min(start+count,len(record["text"]))
            response={"evidence":record["header"],"start":start,"end":end,"text":record["text"][start:end],
                "complete_source":start==0 and end==len(record["text"])}
        except (ContractError,ValueError,TypeError) as exc:
            # Retain JSON requests so admission can replay the exact error and
            # quota. Non-JSON direct callers receive only a normalized envelope.
            try:canonical(request);recorded=request
            except ContractError:recorded={"invalid":True}
            return self._emit(recorded,{"error":getattr(exc,"code","invalid_role_source_request")})
        return self._emit(request,response)


class EvidenceSocket:
    def __init__(self,workspace,reader):
        self.path=Path(workspace)/".source.sock";self.reader=reader;self.stop=threading.Event();self.error=None
    def __enter__(self):
        self.sock=socket.socket(socket.AF_UNIX);self.sock.bind(str(self.path));self.sock.listen(4);self.sock.settimeout(.1)
        self.thread=threading.Thread(target=self._serve,daemon=True);self.thread.start();return self
    def _serve(self):
        while not self.stop.is_set():
            try:connection,_=self.sock.accept()
            except socket.timeout:continue
            except OSError:break
            with connection:
                connection.settimeout(2)
                try:
                    raw=b"";limit=self.reader.config["source_wire_request_bytes"]
                    while b"\n" not in raw and len(raw)<limit:
                        data=connection.recv(limit-len(raw))
                        if not data:break
                        raw+=data
                    if len(raw)>=limit or b"\n" not in raw:
                        request={"invalid_wire":True}
                    else:
                        try:request=parse_json(raw.split(b"\n",1)[0].decode())
                        except (ValueError,UnicodeError):request={"invalid_wire":True}
                    wire=self.reader.read(request)
                    try:connection.sendall(wire)
                    except OSError:pass
                except BaseException as exc:
                    self.error=exc;self.stop.set();break
    def __exit__(self,*_):
        self.stop.set();self.sock.close();self.thread.join(timeout=3);self.path.unlink(missing_ok=True)
        require(not self.thread.is_alive(),"source_socket_shutdown_failure")
        if self.error:raise self.error


SOURCE_CLIENT='''import argparse,json,socket
from pathlib import Path
p=argparse.ArgumentParser(description="Read a declared evidence interval")
p.add_argument("id");p.add_argument("--start",type=int,default=0);p.add_argument("--count",type=int,default=2048)
a=p.parse_args()
with socket.socket(socket.AF_UNIX) as s:
    s.settimeout(3);s.connect(str(Path(__file__).with_name(".source.sock")))
    s.sendall((json.dumps({"op":"read_evidence","id":a.id,"start":a.start,"count":a.count})+"\\n").encode())
    response=b""
    while True:
        data=s.recv(16384)
        if not data:break
        response+=data
        if len(response)>16384:raise SystemExit("evidence response overflow")
    if not response:raise SystemExit("evidence disclosure unavailable")
    print(response.decode(),end="")
'''


class RoleInputs:
    def __init__(self,cards,registry,config=None):
        self.cards,self.registry=cards,registry
        self.config=validate_role(config) if config is not None else role_config()

    def _packet(self,role,ns,cutoff,prompt,context):
        namespace(ns);require(type(cutoff) is int and 0<=cutoff<=self.cards.ledger.head(),"invalid_role_cutoff")
        from .freeze import implementation_files
        implementation=sha256({name:hashlib.sha256(data).hexdigest() for name,data in implementation_files().items()})
        payload={"schema_version":"role-input-v0","role":role,"namespace":ns,"cutoff":cutoff,
            "kernel_sha256":self.registry.fingerprint,"role_config_sha256":sha256(self.config),
            "implementation_sha256":implementation,
            "requested_model":self.config["requested_model"],"reasoning_effort":self.config["reasoning_effort"],
            "prompt":prompt,"context":context,"output_schema":output_schema(role,self.registry.kernel)}
        require(len(canonical(payload))<=self.config["max_packet_bytes"],"role_packet_overflow")
        require(not SECRET.search(canonical(payload)),"role_secret_material")
        return {"payload":payload,"sha256":sha256(payload)}

    def extractor(self,ns,cutoff,source_refs,persist=None):
        reader=EvidenceReader(self.cards,"extractor",ns,cutoff,source_refs,self.config,persist)
        require(all(r["kind"]=="source" for r in source_refs),"extractor_inputs_require_sources")
        active=[{"ref":r,"card":c} for r,c in self.cards.active(ns,cutoff)]
        context={"sources":reader.catalog(),"active_cards":active,"extractor_sha256":sha256(self.registry.kernel["extractor"])}
        packet=self._packet("extractor",ns,cutoff,self.registry.kernel["extractor"]["prompt"]+"\n"+self.config["source_prompt"],context)
        return packet,reader

    def derived(self,role,ns,cutoff,memory_ref,persist=None):
        stage=DerivedStage(self.cards,self.registry);prepared=stage.prepare(role,ns,cutoff,memory_ref)
        refs=[s["ref"] for s in prepared["payload"]["sources"]]
        reader=EvidenceReader(self.cards,role,ns,cutoff,refs,self.config,persist)
        context={"derived_input":prepared,"source_catalog":reader.catalog()}
        packet=self._packet(role,ns,cutoff,prepared["payload"]["prompt"]+"\n"+self.config["source_prompt"],context)
        return packet,reader

    def research(self,role,ns,cutoff,parent,history_refs,briefs):
        require(role in RESEARCH_ROLES and ns=="research/"+role,"research_role_namespace_mismatch")
        require(type(history_refs) is list and len({canonical(r) for r in history_refs})==len(history_refs),"duplicate_research_history")
        kind="memory" if role=="proposer" else "scenario";reference(parent,{kind});spec=self.registry.resolve(parent)
        history=[{"ref":r,"source":self.cards.evidence.read(r,role,ns,cutoff)} for r in history_refs]
        context={"parent":parent,"parent_config":spec["config"],"history":history,"briefs":briefs,
            "registered_fields":self.registry.kernel["memory_fields" if role=="proposer" else "scenario_fields"]}
        return self._packet(role,ns,cutoff,self.config["research_prompt"],context)

    def task(self,role,ns,cutoff,workspace,episode,briefs):
        require(role in TASK_ROLES and ns.startswith("task/"),"task_role_namespace_mismatch")
        require(role=="solver" or briefs in (None,{"items":[]}),"memory_off_role_received_memory")
        from .workspace import read_workspace_text
        context={"current_contract":read_workspace_text(workspace,"SPEC.md"),"episode_sha256":sha256(episode),"briefs":briefs,
            "task_tools":["scenario.py inspect","scenario.py check"]}
        return self._packet(role,ns,cutoff,self.config["solver_prompt"],context)


class RoleObserver:
    """Strict one-session JSONL parsing; no fabricated zero usage."""
    def __init__(self,budget,config=None):
        self.config=validate_role(config) if config is not None else role_config();self.budget=copy.deepcopy(budget)
        exact_object(budget,{"input_tokens","output_tokens","seconds","commands"},"invalid_role_budget")
        require(all(type(v) is int and v>0 for v in budget.values()),"invalid_role_budget")
        self.buffer=b"";self.events=[];self.commands=set();self.thread=None
        self.started=False;self.completed=False;self.failed=False;self.usage=None;self.final_text=None;self.error=None
    def feed(self,data):
        self.buffer+=data
        while b"\n" in self.buffer:
            line,self.buffer=self.buffer.split(b"\n",1)
            if len(line)>self.config["max_event_line_bytes"]:self.error="role_event_line_limit";break
            if len(self.events)>=self.config["max_events"]:self.error="role_event_count_limit";break
            try:
                event=parse_json(line.decode());require(type(event) is dict and type(event.get("type")) is str,"invalid_role_event")
                self.events.append(event);self._event(event)
            except (ContractError,ValueError,UnicodeError,TypeError,KeyError):
                self.usage=None;self.error="role_malformed_event";break
            if self.error:break
        if len(self.buffer)>self.config["max_event_line_bytes"]:self.error="role_event_line_limit"
        return self.error
    def _event(self,e):
        type_=e["type"]
        if type_=="thread.started":
            require(self.thread is None and type(e.get("thread_id")) is str and bool(e["thread_id"]),"invalid_subject_thread")
            self.thread=e["thread_id"]
        elif type_=="turn.started":
            require(self.thread is not None and not self.started,"multiple_subject_turns");self.started=True
        elif type_=="turn.completed":
            require(self.started and not self.completed and not self.failed,"invalid_turn_completion")
            self.completed=True
            u=e.get("usage")
            if type(u) is not dict or not all(type(u.get(k)) is int and u[k]>=0 for k in ("input_tokens","cached_input_tokens","output_tokens")):
                self.error="role_usage_unknown";return
            require(u["cached_input_tokens"]<=u["input_tokens"],"invalid_cached_usage")
            reasoning=u.get("reasoning_output_tokens")
            require(reasoning is None or type(reasoning) is int and 0<=reasoning<=u["output_tokens"],"invalid_reasoning_usage")
            self.usage=copy.deepcopy(u)
            if u["input_tokens"]>self.budget["input_tokens"] or u["output_tokens"]>self.budget["output_tokens"]:
                self.error="role_observed_token_guard"
        elif type_ in ("turn.failed","error"):
            self.failed=True;self.error="role_provider_or_turn_failure"
        elif type_.startswith("item."):
            require(self.started and not self.completed,"item_outside_subject_turn")
            item=e.get("item");require(type(item) is dict,"invalid_role_item")
            if item.get("type")=="command_execution":
                require(self.started and type(item.get("id")) is str and bool(item["id"]),"invalid_command_item")
                self.commands.add(item["id"])
                if len(self.commands)>self.budget["commands"]:self.error="role_command_guard"
            if type_=="item.completed" and item.get("type")=="agent_message":
                require(type(item.get("text")) is str,"invalid_agent_message");self.final_text=item["text"]
    def finish(self,process):
        if self.buffer and self.error is None:self.error="role_incomplete_event_line"
        final=None
        if self.final_text is not None:
            try:final=parse_json(self.final_text)
            except (ValueError,UnicodeError):self.error=self.error or "role_invalid_final_json"
        valid=bool(self.thread and self.started and self.completed and not self.failed and self.usage is not None and
            self.error is None and process["error"] is None and process["exit_code"]==0 and type(final) is dict)
        return {"transport_success":valid,"thread_id":self.thread,"turn_completed":self.completed,
            "command_count":len(self.commands),"usage":self.usage,"total_tokens":self.usage["input_tokens"]+self.usage["output_tokens"] if self.usage else None,
            "final":final,"error":self.error or process["error"] or (None if valid else "role_incomplete_execution"),
            "provider_budget_qualified":False,"resolved_model":None,"formal_admission":False,"promotion_eligible":False}


def capture_transport(command,workspace,packet,budget,output,config=None,env=None,invocation_identity=None):
    """Internal raw capture. Caller owns explicit real-vs-scripted invocation identity."""
    c=validate_role(config) if config is not None else role_config()
    env=sandbox.worker_environment(env)
    exact_object(packet,{"payload","sha256"},"invalid_role_packet")
    require(sha256(packet["payload"])==packet["sha256"],"role_packet_drift")
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    (output/"input.json").write_bytes(canonical(packet)+b"\n")
    start={"status":"started","input_sha256":packet["sha256"],"budget":budget,"command":list(command),
        "invocation_identity":invocation_identity,"formal_admission":False,"unknown_on_interruption":True}
    (output/"start.json").write_bytes(canonical(start)+b"\n")
    for p in (output/"input.json",output/"start.json"):
        with p.open("rb") as stream:os.fsync(stream.fileno())
    observer=RoleObserver(budget,c)
    with (output/"events.jsonl").open("xb") as raw:
        def observe(data):
            raw.write(data);raw.flush();return observer.feed(data)
        def process_started(proc):
            import subprocess
            start_time=subprocess.run(["/bin/ps","-p",str(proc.pid),"-o","lstart="],capture_output=True,text=True,timeout=2).stdout.strip()
            handle={"pid":proc.pid,"pgid":proc.pid,"start_time":start_time or None}
            (output/"process.json").write_bytes(canonical(handle)+b"\n")
            with (output/"process.json").open("rb") as stream:os.fsync(stream.fileno())
        process=bounded_run(command,workspace,canonical(packet["payload"])+b"\n",budget["seconds"],c["stdout_bytes"],c["stderr_bytes"],env,observe,process_started)
    import base64
    (output/"stderr.bin").write_bytes(base64.b64decode(process["raw_streams"]["stderr"]["base64"]))
    result=observer.finish(process);result.update(process=process,input_sha256=packet["sha256"])
    (output/"transport.json").write_bytes(canonical(result)+b"\n")
    return result


def process_status(directory):
    """A missing handle is not proof of exit; never restart a still-live group."""
    import subprocess
    path=Path(directory)/"process.json"
    if not path.exists():return {"status":"exit_unverified","reason":"missing_process_handle"}
    h=read_json(path);exact_object(h,{"pid","pgid","start_time"},"invalid_role_process_handle")
    require(type(h["pid"]) is int and h["pid"]>0 and h["pgid"]==h["pid"],"invalid_role_process_handle")
    current=subprocess.run(["/bin/ps","-p",str(h["pid"]),"-o","lstart="],capture_output=True,text=True,timeout=2).stdout.strip()
    if current and h["start_time"] and current!=h["start_time"]:
        return {"status":"exited","reason":"pid_reused_different_start","handle":h}
    try:os.killpg(h["pgid"],0)
    except ProcessLookupError:return {"status":"exited","reason":"process_group_absent","handle":h}
    except PermissionError:return {"status":"exit_unverified","reason":"process_group_access_denied","handle":h}
    return {"status":"running" if current and h["start_time"]==current else "exit_unverified",
        "reason":"live_process_group","handle":h}


def codex_args(config,workspace):
    c=validate_role(config)
    args=[str(c["cli_path"]),"--no-daemon","-a","never","exec","--json","--ephemeral","--ignore-user-config","--ignore-rules",
        "--skip-git-repo-check","--sandbox","workspace-write","--cd",str(workspace),"--model",c["requested_model"],
        "-c",'model_reasoning_effort="'+c["reasoning_effort"]+'"',"-c",'web_search="disabled"',
        "-c","sandbox_workspace_write.network_access=false","--enable","skip_host_skill_discovery"]
    for feature in ("memories","apps","plugins","browser_use","computer_use","multi_agent","shell_snapshot","skill_search"):
        args.extend(["--disable",feature])
    args.extend(["--output-schema",str(Path(workspace)/"role.schema.json"),"-"])
    return args


def codex_command(config,workspace,control,packet):
    c=validate_role(config)
    require(all(c[k] for k in ("requested_model","cli_path","cli_version","cli_sha256")),"missing_role_cli_model_identity")
    require(packet["payload"]["requested_model"]==c["requested_model"] and packet["payload"]["role_config_sha256"]==sha256(c),"role_config_drift")
    binary=Path(c["cli_path"]);require(binary.is_file() and not binary.is_symlink(),"invalid_role_cli_path")
    require(hashlib.sha256(binary.read_bytes()).hexdigest()==c["cli_sha256"],"role_cli_binary_drift")
    schema=Path(workspace)/"role.schema.json"
    require(schema.read_bytes()==canonical(packet["payload"]["output_schema"])+b"\n","role_schema_drift")
    return sandbox.command(workspace,control,codex_args(c,workspace),write_isolation=True,network=True)


def run_canary(config,workspace,control,packet,budget,output,approved_packet_sha256,authorized_by_user=False):
    """Call only after explicit human approval of this exact qualification packet."""
    c=validate_role(config)
    require(authorized_by_user is True and approved_packet_sha256==packet["sha256"],"role_canary_not_authorized")
    require(c["authentication_policy"]!="isolated_auth_not_qualified","role_authentication_not_qualified")
    # Preparation/identity checks happen before starting a paid subprocess.
    command=codex_command(c,workspace,control,packet)
    probe=sandbox.probe(workspace,control)
    binary=Path(c["cli_path"])
    version=bounded_run([str(binary),"--version"],workspace,b"",15,4096,4096)
    require(version["error"] is None and version["exit_code"]==0 and version["stdout"].strip()==c["cli_version"],"role_cli_version_drift")
    identity={"cli_sha256":hashlib.sha256(binary.read_bytes()).hexdigest(),"cli_version":c["cli_version"],
        "requested_model":c["requested_model"],"reasoning_effort":c["reasoning_effort"],"isolation":probe,
        "approved_packet_sha256":approved_packet_sha256,"category":"qualification_canary","model_session_invocations":1}
    env=sandbox.worker_environment()
    result=capture_transport(command,workspace,packet,budget,output,c,env,identity)
    result.update(identity=identity,category="qualification_canary",live_model_calls=1,kernel_frozen=False,gain_evidence=False)
    (Path(output)/"canary.json").write_bytes(canonical(result)+b"\n")
    return result
