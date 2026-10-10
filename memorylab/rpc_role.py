"""Actual fresh Codex RPC transport with a local fixed Responses fixture only."""
import argparse
import copy
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path

from .app_server_probe import ProbeRPC,profile_config
from .common import save
from .contracts import canonical,exact_object,require
from .kernel_cli import parse_json,read_json
from .provider_budget import ProviderBudget
from .workspace import read_workspace_bytes
from .rpc_output import output_config,output_prompt,validate_output
from . import sandbox

ROOT=Path(__file__).resolve().parent.parent
PROMPT="Execute the supplied boundary diagnostic tool call and return the fixed diagnostic JSON. This is a model-free transport fixture."
FINAL={"category":"infrastructure_acceptance","fixture":True}


def role_config():
    c=read_json(ROOT/"configs/rpc_role_v0.json")
    exact_object(c,{"schema_version","hypothesis","category","backend","model","provider","max_provider_requests",
        "max_provider_request_bytes","max_provider_header_bytes","max_wire_bytes","max_line_bytes","timeout_seconds","request_retries","stream_retries",
        "budget_policy","budget_input_tokens","budget_output_tokens","budget_output_per_request",
        "thread_policy","authentication_policy","tool_profile","usage_policy","diagnostic_logging","provider_route","environment_policy","actual_model_calls","formal_admission",
        "authentication_qualified","provider_budget_qualified"},"invalid_rpc_role_config")
    expected={"schema_version":"rpc-role-v0","category":"infrastructure_acceptance","backend":"loopback_fixed_responses_fixture",
        "model":"memorylab-fixture","provider":"memorylab-fixture","max_provider_requests":2,"request_retries":0,"stream_retries":0,
        "budget_policy":"persistent_round_fixture_reservation_before_dispatch","budget_input_tokens":200,"budget_output_tokens":2048,"budget_output_per_request":1024,
        "thread_policy":"fresh_ephemeral_one_turn","authentication_policy":"empty_file_store_no_real_authentication",
        "tool_profile":"probe-restricted","usage_policy":"simulated_never_formal","diagnostic_logging":"codex_core=debug,codex_api=debug",
        "provider_route":"explicit_loopback_no_proxy_environment","environment_policy":"default_local_no_empty_environment_override","actual_model_calls":0}
    require(all(type(c[k]) is type(v) and c[k]==v for k,v in expected.items()) and
        all(c[k] is False for k in ("formal_admission","authentication_qualified","provider_budget_qualified")),"unsupported_rpc_role_policy")
    for k in ("max_provider_request_bytes","max_provider_header_bytes","max_wire_bytes","max_line_bytes","timeout_seconds"):
        require(type(c[k]) is int and c[k]>0,"invalid_rpc_role_limit")
    return c


class FreshRoleRPC(ProbeRPC):
    """Bounded wire transport. Only the fixed fresh-thread/one-turn recipe is allowed."""
    def __init__(self,command,cwd,env,config,output,thread_params,disclosure=None,typed=False):
        from .rpc_disclosure import RPCDisclosure
        require(disclosure is None or type(disclosure) is RPCDisclosure,"rpc_role_disclosure_type")
        require(type(typed) is bool and (not typed or disclosure is not None and disclosure.role in output_config()['roles']),"rpc_role_output_mode")
        self.typed=typed
        keys={"model","modelProvider","cwd","approvalPolicy","permissions","ephemeral","experimentalRawEvents","runtimeWorkspaceRoots"}
        exact_object(thread_params,keys|({"dynamicTools"} if disclosure else set()),"rpc_role_thread_recipe")
        if disclosure:require(thread_params["dynamicTools"]==disclosure.specs,"rpc_role_dynamic_tools_binding")
        require(thread_params["model"]==thread_params["modelProvider"]=="memorylab-fixture" and
            thread_params["cwd"]==str(Path(cwd).resolve()) and thread_params["runtimeWorkspaceRoots"]==[str(Path(cwd).resolve())] and
            thread_params["approvalPolicy"]=="never" and thread_params["permissions"]=="probe-restricted" and
            thread_params["ephemeral"] is True and thread_params["experimentalRawEvents"] is False,"rpc_role_thread_recipe")
        self.thread_binding=canonical(thread_params);self.thread_id=None;self.turn_id=None;self.used=set();self.events=[]
        self.responses=set()
        self.disclosure=disclosure;self.server_requests=[];self.server_replies=[]
        super().__init__(command,cwd,env,config,output)
        self.deadline=time.monotonic()+self.config['timeout_seconds']
        start_time=subprocess.run(['/bin/ps','-p',str(self.process.pid),'-o','lstart='],capture_output=True,text=True,timeout=2).stdout.strip()
        save(self.output/'handle'/'process.json',{'pid':self.process.pid,'pgid':self.process.pid,'start_time':start_time or None})

    def recipe(self,method):
        if method=="initialize":return {"clientInfo":{"name":"memorylab-role-fixture","version":"0.1"},"capabilities":{"experimentalApi":True}}
        if method=="initialized":return {}
        if method=="thread/start":return parse_json(self.thread_binding.decode())
        if method=="turn/start":
            require(self.thread_id is not None,"rpc_role_thread_not_started")
            prompt=self.prompt()
            return {"threadId":self.thread_id,"input":[{"type":"text","text":prompt}],"permissions":"probe-restricted",
                **({'outputSchema':self.disclosure.packet['payload']['output_schema']} if self.typed else {})}
        require(False,"rpc_role_method_forbidden")

    def prompt(self):
        if self.typed:return output_prompt(self.disclosure.packet)
        return PROMPT if self.disclosure is None else PROMPT+"\nSupplied role packet:\n"+canonical(self.disclosure.packet).decode()

    def server_request(self,value):
        require(time.monotonic()<self.deadline,'rpc_role_timeout')
        require(self.disclosure is not None and self.turn_id is not None,"rpc_role_unexpected_server_request")
        reply=self.disclosure.reply(value);raw=canonical(reply)+b"\n"
        require(len(raw)<=self.config["max_line_bytes"],"rpc_role_line_overflow")
        require(time.monotonic()<self.deadline,'rpc_role_timeout')
        self._count(len(raw));self.records.append({"direction":"tool_reply","message":reply})
        n=self.process.stdin.write(raw);self.raw_stdin.write(raw[:n]);self.raw_stdin.flush();self.process.stdin.flush()
        require(n==len(raw),"rpc_role_incomplete_write")
        self.server_requests.append(value);self.server_replies.append(reply)

    def next_message(self,deadline):
        deadline=min(deadline,self.deadline)
        while True:
            require(time.monotonic()<deadline,'rpc_role_timeout')
            if b"\n" in self.pending:
                line,_,rest=self.pending.partition(b"\n");self.pending=bytearray(rest)
                require(len(line)<=self.config["max_line_bytes"],"rpc_role_line_overflow")
                value=parse_json(line.decode());require(type(value) is dict,"invalid_rpc_role_message")
                self.records.append({"direction":"response","message":value})
                return value
            require(len(self.pending)<=self.config["max_line_bytes"],"rpc_role_line_overflow")
            remaining=deadline-time.monotonic();require(remaining>0,"rpc_role_timeout")
            ready=self.selector.select(min(remaining,1))
            require(ready or self.process.poll() is None,"rpc_role_process_exited")
            for key,_ in ready:
                raw=os.read(key.fileobj.fileno(),65536)
                if not raw:self.selector.unregister(key.fileobj);continue
                self._capture(key.data,raw)
                if key.data=="stdout":self.pending.extend(raw)

    def event(self,value):
        require("id" not in value and type(value.get("method")) is str and type(value.get("params")) is dict,
            "rpc_role_unexpected_server_request")
        self.events.append(value)

    def call(self,method,params=None,notify=False):
        require(time.monotonic()<self.deadline,'rpc_role_timeout')
        expected=self.recipe(method)
        require(method not in self.used and canonical(params or {})==canonical(expected),"rpc_role_request_not_bound")
        require(notify==(method=="initialized"),"rpc_role_notification_policy")
        required={"initialized":"initialize","thread/start":"initialized","turn/start":"thread/start"}.get(method)
        require(required is None or required in self.used,"rpc_role_request_order")
        self.used.add(method);message={"method":method,"params":params or {}}
        if not notify:self.sequence+=1;message["id"]=self.sequence
        raw=canonical(message)+b"\n";require(len(raw)<=self.config["max_line_bytes"],"rpc_role_line_overflow")
        require(time.monotonic()<self.deadline,'rpc_role_timeout')
        self._count(len(raw));self.records.append({"direction":"request","message":message})
        n=self.process.stdin.write(raw);self.raw_stdin.write(raw[:n]);self.raw_stdin.flush();self.process.stdin.flush()
        require(n==len(raw),"rpc_role_incomplete_write")
        if notify:return
        deadline=time.monotonic()+self.config["timeout_seconds"]
        while True:
            value=self.next_message(deadline)
            if "id" in value and "method" in value and self.disclosure:
                self.server_request(value);continue
            if "id" not in value:self.event(value);continue
            require(type(value["id"]) is int and value["id"]==self.sequence and value["id"] not in self.responses and "method" not in value,
                "rpc_role_response_id")
            self.responses.add(value["id"]);require("error" not in value and "result" in value,"rpc_role_rpc_error")
            result=value["result"]
            if method=="thread/start":
                self.thread_id=result["thread"]["id"];require(type(self.thread_id) is str and bool(self.thread_id),"rpc_role_thread_id")
                require(result["thread"]["ephemeral"] is True and result["model"]=="memorylab-fixture" and result["modelProvider"]=="memorylab-fixture",
                    "rpc_role_thread_identity")
                require(result.get("instructionSources")==[] and result.get("activePermissionProfile",{}).get("id")=="probe-restricted" and
                    result["thread"].get("forkedFromId") is None and result["thread"].get("parentThreadId") is None and
                    result["thread"].get("path") is None and result["thread"].get("turns")==[],"rpc_role_thread_isolation")
            if method=="turn/start":
                self.turn_id=result["turn"]["id"];require(type(self.turn_id) is str and bool(self.turn_id),"rpc_role_turn_id")
                if self.disclosure:self.disclosure.bind(self.thread_id,self.turn_id)
            return result

    def complete(self):
        require(self.turn_id is not None,"rpc_role_turn_not_started")
        deadline=time.monotonic()+self.config["timeout_seconds"]
        while True:
            value=self.next_message(deadline)
            if "id" in value and "method" in value and self.disclosure:self.server_request(value);continue
            self.event(value)
            if value["method"]=="turn/completed":return value


def sse_response(number,item):
    rid="resp_fixture_"+str(number)
    items=item if type(item) is list else [item]
    response={"id":rid,"object":"response","status":"completed","model":"memorylab-fixture","output":items,
        "usage":{"input_tokens":100,"output_tokens":10,"total_tokens":110,"input_tokens_details":{"cached_tokens":0},"output_tokens_details":{"reasoning_tokens":0}}}
    events=[{"type":"response.created","response":{**response,"status":"in_progress","output":[],"usage":None}}]
    for index,value in enumerate(items):
        events.extend([{"type":"response.output_item.added","output_index":index,"item":value},
            {"type":"response.output_item.done","output_index":index,"item":value}])
    events.append({"type":"response.completed","response":response})
    return b"".join(b"event: "+e["type"].encode()+b"\ndata: "+canonical(e)+b"\n\n" for e in events)


class FixtureProvider:
    def __init__(self,config,output,tool_command,tool_calls=None,role='solver',final=None):
        self.config=config;self.output=Path(output);self.tool_command=tool_command;self.requests=[];self.errors=[];self.tool_result=None;self.attempts=0;self.closed=False
        self.http_metadata=[]
        self.tool_calls=tool_calls;self.dynamic_outputs=[]
        self.role=role;self.final=copy.deepcopy(FINAL if final is None else final)
        bound={"requests":config["max_provider_requests"],"input_tokens":config["budget_input_tokens"],"output_tokens":config["budget_output_tokens"]}
        self.budget=ProviderBudget(self.output/"budget.sqlite",bound,{role:{"role":role,"limits":bound,
            "per_request_output_tokens":config["budget_output_per_request"]}},create=True)
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def metadata(self,method):
                owner.attempts+=1
                if len(owner.http_metadata)>owner.config["max_provider_requests"]:return
                raw_length=self.headers.get("Content-Length","")
                owner.http_metadata.append({"attempt":owner.attempts,"method":method,"path_bytes":len(self.path.encode()),
                    "expected_path":self.path=="/responses","authorization_present":any(self.headers.get(k) is not None for k in ("Authorization","Proxy-Authorization","X-API-Key")),
                    "content_type_is_json":self.headers.get("Content-Type")=="application/json",
                    "content_encoding_is_identity":self.headers.get("Content-Encoding","identity")=="identity",
                    "declared_length":int(raw_length) if raw_length.isascii() and raw_length.isdigit() and len(raw_length)<=10 else None,
                    "transfer_encoding_present":self.headers.get("Transfer-Encoding") is not None})
            def do_GET(self):
                self.metadata(self.command)
                if not owner.errors:owner.errors.append({"code":"rpc_fixture_method","detail":"POST only"})
                self.send_error(405)
            do_PUT=do_GET
            do_DELETE=do_GET
            def do_POST(self):
                self.metadata("POST")
                try:
                    require(self.path=="/responses","rpc_fixture_path")
                    require(sum(len(k.encode())+len(v.encode()) for k,v in self.headers.items())<=owner.config["max_provider_header_bytes"],"rpc_fixture_header_size")
                    require(not any(self.headers.get(k) is not None for k in ("Authorization","Proxy-Authorization","X-API-Key")),"rpc_fixture_authentication_forbidden")
                    require(self.headers.get("Content-Type")=="application/json","rpc_fixture_content_type")
                    require(self.headers.get("Transfer-Encoding") is None,"rpc_fixture_transfer_encoding")
                    require(self.headers.get("Content-Encoding", "identity")=="identity","rpc_fixture_content_encoding")
                    require(len(self.headers.get_all("Content-Length",[]))==1,"rpc_fixture_length_headers")
                    length=int(self.headers.get("Content-Length","0"))
                    require(0<length<=owner.config["max_provider_request_bytes"],"rpc_fixture_request_size")
                    require(owner.attempts<=owner.config["max_provider_requests"],"rpc_fixture_request_limit")
                    self.connection.settimeout(3);raw=self.rfile.read(length)
                    (owner.output/("provider-request-%d.bin"%owner.attempts)).write_bytes(raw)
                    require(len(raw)==length,"rpc_fixture_short_request")
                    request=parse_json(raw.decode());require(request.get("model")=="memorylab-fixture","rpc_fixture_model")
                    reservation=owner.budget.prepare("request-"+str(owner.attempts),owner.role,request)
                    forwarded=owner.budget.dispatch(reservation)
                    (owner.output/("provider-forwarded-%d.bin"%owner.attempts)).write_bytes(canonical(forwarded))
                    require(forwarded.get("max_output_tokens")==owner.config["budget_output_per_request"] and forwarded.get("truncation")=="disabled","rpc_fixture_forwarded_budget")
                    number=len(owner.requests)+1;owner.requests.append(request)
                    if number==1:
                        names=[t.get("name") for t in request.get("tools",[]) if t.get("type")=="function"]
                        if owner.tool_calls is None:
                            require("exec_command" in names,"rpc_fixture_builtin_unavailable",str(names))
                            item={"type":"function_call","id":"fc_fixture","call_id":"call_fixture_boundary","name":"exec_command",
                                "arguments":json.dumps({"cmd":owner.tool_command,"yield_time_ms":1000,"max_output_tokens":1000}),"status":"completed"}
                        else:
                            require(all(t["name"] in names for t in owner.tool_calls),"rpc_fixture_dynamic_unavailable",str(names))
                            item=[{"type":"function_call","id":"fc_dynamic_"+str(i),"call_id":"call_dynamic_"+str(i),"name":t["name"],
                                "arguments":canonical(t["arguments"]).decode(),"status":"completed"} for i,t in enumerate(owner.tool_calls)]
                    else:
                        if owner.tool_calls is None:
                            outputs=[x for x in request.get("input",[]) if x.get("type")=="function_call_output" and x.get("call_id")=="call_fixture_boundary"]
                            require(len(outputs)==1,"rpc_fixture_tool_output_missing");owner.tool_result=outputs[0]["output"]
                        else:
                            outputs=[x for x in request.get("input",[]) if x.get("type")=="function_call_output"]
                            require(len(outputs)==len(owner.tool_calls) and {x["call_id"] for x in outputs}=={"call_dynamic_"+str(i) for i in range(len(owner.tool_calls))},"rpc_fixture_tool_output_missing")
                            owner.dynamic_outputs=outputs
                        item={"type":"message","id":"msg_fixture_final","role":"assistant","status":"completed","phase":"final_answer",
                            "content":[{"type":"output_text","text":canonical(owner.final).decode(),"annotations":[]}]}
                    body=sse_response(number,item);(owner.output/("provider-response-%d.bin"%number)).write_bytes(body)
                    terminal=parse_json(body.split(b"data: ")[-1].strip().decode())["response"]
                    owner.budget.finish(reservation,terminal)
                    self.send_response(200);self.send_header("Content-Type","text/event-stream");self.send_header("Content-Length",str(len(body)));self.end_headers()
                    self.wfile.write(body);self.wfile.flush()
                except Exception as exc:
                    if not owner.errors:owner.errors.append({"code":getattr(exc,"code",type(exc).__name__),"detail":str(exc)})
                    body=canonical({"error":{"message":str(exc)}})
                    self.send_response(400);self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
        self.server=HTTPServer(("127.0.0.1",0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    @property
    def url(self):return "http://127.0.0.1:"+str(self.server.server_port)

    def close(self):
        if self.closed:return
        self.closed=True
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
        require(not self.thread.is_alive(),"rpc_fixture_shutdown")
        save(self.output/"provider.json",{"attempt_count":self.attempts,"request_count":len(self.requests),"errors":self.errors,"usage_policy":"simulated_never_formal",
            "http_metadata":self.http_metadata,"budget_report":self.budget.report(),
            "dynamic_outputs":self.dynamic_outputs,"role":self.role,"fixture_final":self.final,
            "tool_output":self.tool_result,"raw_hashes":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.output.glob("provider-*.bin")}})


def validate_events(events,thread_id,turn_id,dynamic_count=0,expected_final=None):
    started=[e for e in events if e["method"]=="turn/started"];finished=[e for e in events if e["method"]=="turn/completed"]
    require(len(started)==len(finished)==1,"rpc_role_turn_lifecycle")
    for e in events:
        require(type(e) is dict and "id" not in e and type(e.get("method")) is str and type(e.get("params")) is dict,"rpc_role_unexpected_server_request")
        p=e["params"]
        if "threadId" in p:require(p["threadId"]==thread_id,"rpc_role_event_thread")
        if "turnId" in p:require(p["turnId"]==turn_id,"rpc_role_event_turn")
        if "turn" in p:require(p["turn"]["id"]==turn_id,"rpc_role_event_turn")
        if "thread" in p:require(p["thread"]["id"]==thread_id,"rpc_role_event_thread")
        require(e["method"]!="error","rpc_role_error_event")
    require(finished[0]["params"]["turn"]["status"]=="completed" and finished[0]["params"]["turn"].get("error") is None,"rpc_role_turn_failed")
    items=[e["params"]["item"] for e in events if e["method"]=="item/completed"]
    allowed={"userMessage","agentMessage","dynamicToolCall" if dynamic_count else "commandExecution"}
    require(all(i["type"] in allowed for i in items) and all(e["params"]["item"]["type"] in allowed for e in events
        if e["method"]=="item/started"),"rpc_role_unexpected_item")
    commands=[i for i in items if i["type"]=="commandExecution"]
    if dynamic_count:
        require(not commands,"rpc_role_unexpected_command")
        dynamic=[i for i in items if i["type"]=="dynamicToolCall"]
        require(len(dynamic)==dynamic_count and len({i["id"] for i in dynamic})==dynamic_count and all(i["status"] in ("completed","failed") for i in dynamic),"rpc_role_dynamic_lifecycle")
    else:require(len(commands)==1 and commands[0]["exitCode"]==0 and commands[0]["status"]=="completed","rpc_role_command_failed")
    messages=[i for i in items if i["type"]=="agentMessage"]
    require(len(messages)==1 and messages[0].get("phase")=="final_answer" and
        parse_json(messages[0]["text"])==(FINAL if expected_final is None else expected_final),"rpc_role_final_mismatch")
    types=("dynamicToolCall","agentMessage") if dynamic_count else ("commandExecution","agentMessage")
    begin=[e["params"]["item"] for e in events if e["method"]=="item/started" and e["params"]["item"]["type"] in types]
    require(sorted((i["id"],i["type"]) for i in begin)==sorted((i["id"],i["type"]) for i in (dynamic if dynamic_count else commands)+messages),"rpc_role_item_lifecycle")
    usage=[e["params"] for e in events if e["method"]=="thread/tokenUsage/updated"]
    require(len(usage)==2 and usage[-1]["tokenUsage"]["total"]["totalTokens"]==220,"rpc_role_usage_missing")
    def expected(n):return {"totalTokens":110*n,"inputTokens":100*n,"cachedInputTokens":0,"cacheWriteInputTokens":0,"outputTokens":10*n,"reasoningOutputTokens":0}
    require(all(canonical(u["tokenUsage"]["last"])==canonical(expected(1)) and canonical(u["tokenUsage"]["total"])==canonical(expected(i)) for i,u in enumerate(usage,1)),"rpc_role_usage_mismatch")
    require(events.index(started[0])<events.index(finished[0]) and all(events.index(started[0])<i<events.index(finished[0])
        for i,e in enumerate(events) if e["method"] in ("item/started","item/completed","thread/tokenUsage/updated")),"rpc_role_event_order")
    return {**({"dynamic":dynamic} if dynamic_count else {"command":commands[0]}),"final":messages[0],"simulated_usage":usage[-1]}


def sealed_bytes(output,name,limit=None):
    return read_workspace_bytes(output,name,{"max_depth":1,"max_file_bytes":limit or role_config()["max_wire_bytes"]})


def sealed_json(output,name):return parse_json(sealed_bytes(output,name).decode())


def fixture_config_text(workspace,provider_url,denied_roots):
    workspace=Path(workspace);private=workspace.parent/'server-home';other=workspace.parent/'other-arm'
    text=profile_config(workspace,private,other,Path(sys.executable).resolve().parent.parent,private/'.codex/private-marker.txt',denied_roots)
    prefix='model = "memorylab-fixture"\nmodel_provider = "memorylab-fixture"\nmodel_reasoning_effort = "low"\n'
    return prefix+text+'\n[model_providers.memorylab-fixture]\nname = "Local fixed fixture"\nbase_url = '+json.dumps(provider_url)+'\nwire_api = "responses"\nrequires_openai_auth = false\nsupports_websockets = false\nrequest_max_retries = 0\nstream_max_retries = 0\nstream_idle_timeout_ms = 5000\n'


def validate_sealed(output,thread_id,turn_id,disclosure=None):
    output=Path(output);c=role_config();exited=sealed_json(output,"exit.json")
    from .roles import process_status
    handle=parse_json(read_workspace_bytes(output,'handle/process.json',{'max_depth':2,'max_file_bytes':c['max_wire_bytes']}).decode())
    require(process_status(output/'handle')['status']=='exited','rpc_role_exit_unverified')
    process=sealed_json(output,'process.json')
    require(process['pid']==handle['pid'] and process['pgid']==handle['pgid'],'rpc_role_process_handle_binding')
    require(exited["exit_code"]==0 and exited["wire_limit_exceeded"] is False,"rpc_role_shutdown")
    require(set(exited["raw_hashes"])=={"stdin.bin","stdout.bin","stderr.txt"},"rpc_role_raw_fields")
    raw={n:sealed_bytes(output,n) for n in exited["raw_hashes"]}
    require(sum(len(v) for v in raw.values())<=c["max_wire_bytes"],"rpc_role_sealed_wire_limit")
    require(all(hashlib.sha256(raw[n]).hexdigest()==h for n,h in exited["raw_hashes"].items()),"rpc_role_raw_drift")
    incoming=[parse_json(line.decode()) for line in raw["stdin.bin"].splitlines()]
    requests=[m for m in incoming if "method" in m]
    require([m.get("method") for m in requests]==["initialize","initialized","thread/start","turn/start"],"rpc_role_sealed_requests")
    recipe=sealed_json(output,"recipe.json")
    current={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/"memorylab").glob("*.py"))}
    for n in ("configs/rpc_role_v0.json","configs/rpc_disclosure_v0.json","configs/rpc_output_v0.json"):
        current[n]=hashlib.sha256((ROOT/n).read_bytes()).hexdigest()
    require(recipe["source_hashes"]==current,"rpc_role_implementation_drift")
    if disclosure is not None:
        from .rpc_disclosure import RPCDisclosure,SealedDisclosure
        require(type(disclosure) in (RPCDisclosure,SealedDisclosure),"rpc_role_disclosure_type")
        if type(disclosure) is RPCDisclosure:disclosure._check()
        disclosure=SealedDisclosure(disclosure.output,recipe.get("disclosure_sha256"))
    require(hashlib.sha256(sealed_bytes(output,"generated-config.toml")).hexdigest()==recipe["config_sha256"],"rpc_role_config_drift")
    require(type(recipe['execution_seconds']) is int and 0<recipe['execution_seconds']<=c['timeout_seconds'] and
        type(recipe['denied_roots']) is list and recipe['denied_roots']==sorted(set(recipe['denied_roots'])) and
        all(type(p) is str and str(Path(p).resolve())==p for p in recipe['denied_roots']),'rpc_role_execution_binding')
    require(sealed_bytes(output,'generated-config.toml')==fixture_config_text(recipe['thread']['cwd'],recipe['provider_url'],recipe['denied_roots']).encode(),
        'rpc_role_profile_binding')
    typed=recipe.get('typed_output')
    if typed is not None:
        require(disclosure is not None and disclosure.role in output_config()['roles'] and type(typed) is dict,'rpc_role_output_mode')
        validate_output(typed,disclosure.packet['payload']['output_schema'])
    require(recipe["prompt"]==(output_prompt(disclosure.packet) if typed is not None else
        PROMPT if disclosure is None else PROMPT+"\nSupplied role packet:\n"+canonical(disclosure.packet).decode()),"rpc_role_prompt_binding")
    require(requests[2]["params"]==recipe["thread"] and requests[3]["params"]==
        {"threadId":thread_id,"input":[{"type":"text","text":recipe["prompt"]}],"permissions":"probe-restricted",
            **({'outputSchema':disclosure.packet['payload']['output_schema']} if typed is not None else {})},"rpc_role_sealed_requests")
    messages=[parse_json(line.decode()) for line in raw["stdout.bin"].splitlines()]
    responses=[m for m in messages if "id" in m and "method" not in m]
    require([m.get("id") for m in responses]==[1,2,3] and all(type(m["id"]) is int and "method" not in m and
        "error" not in m and "result" in m for m in responses),"rpc_role_sealed_responses")
    require(responses[1]["result"]["thread"]["id"]==thread_id and responses[2]["result"]["turn"]["id"]==turn_id,"rpc_role_sealed_identity")
    dynamic_requests=[m for m in messages if "id" in m and "method" in m]
    dynamic_replies=[m for m in incoming if "method" not in m]
    if disclosure:
        require(recipe.get("disclosure_sha256")==disclosure.binding_sha256,"rpc_role_disclosure_binding")
        result=validate_events([m for m in messages if "id" not in m],thread_id,turn_id,len(dynamic_requests),typed)
        result["disclosure"]=disclosure.verify(dynamic_requests,dynamic_replies)
    else:
        require(not dynamic_requests and not dynamic_replies,"rpc_role_unexpected_server_request")
        result=validate_events([m for m in messages if "id" not in m],thread_id,turn_id)
    provider=sealed_json(output,"provider.json")
    role=disclosure.role if disclosure else 'solver'
    require(provider['role']==role and provider['fixture_final']==(FINAL if typed is None else typed),'rpc_role_provider_recipe')
    require(provider["attempt_count"]==provider["request_count"]==2 and not provider["errors"] and
        provider["usage_policy"]=="simulated_never_formal","rpc_role_provider_chain")
    require(len(provider["http_metadata"])==2 and all(m["method"]=="POST" and m["expected_path"] is True and
        m["authorization_present"] is False and m["content_type_is_json"] is True and m["content_encoding_is_identity"] is True and
        m["transfer_encoding_present"] is False for m in provider["http_metadata"]),"rpc_role_provider_headers")
    require(set(provider["raw_hashes"])=={"provider-request-1.bin","provider-request-2.bin","provider-response-1.bin","provider-response-2.bin","provider-forwarded-1.bin","provider-forwarded-2.bin"} and
        all(hashlib.sha256(sealed_bytes(output,n)).hexdigest()==h for n,h in provider["raw_hashes"].items()),"rpc_role_provider_raw_drift")
    first=parse_json(sealed_bytes(output,"provider-request-1.bin",c["max_provider_request_bytes"]).decode())
    second=parse_json(sealed_bytes(output,"provider-request-2.bin",c["max_provider_request_bytes"]).decode())
    require(first["model"]==second["model"]=="memorylab-fixture","rpc_role_provider_input")
    if typed is not None:
        for request in (first,second):
            format_=request.get('text',{}).get('format',{})
            exact_object(format_,{'type','name','schema','strict'},'rpc_role_provider_schema')
            require(format_['type']=='json_schema' and format_['strict'] is True and format_['schema']==disclosure.packet['payload']['output_schema'] and
                type(format_['name']) is str and 0<len(format_['name'])<=64,'rpc_role_provider_schema')
    texts=[v["text"] for item in first["input"] if item.get("role")=="user" for v in item.get("content",[]) if v.get("type")=="input_text"]
    require(texts.count(recipe["prompt"])==1,"rpc_role_provider_prompt_binding")
    if disclosure:
        outputs=[x for x in second["input"] if x.get("type")=="function_call_output"]
        require(outputs==provider["dynamic_outputs"] and len(outputs)==len(dynamic_requests),"rpc_role_provider_tool_binding")
        for request,reply in zip(dynamic_requests,dynamic_replies):
            provider_output=next((x["output"] for x in outputs if x["call_id"]==request["params"]["callId"]),None)
            contents=reply["result"]["contentItems"]
            expected="".join(i["text"] for i in contents)
            require(provider_output==expected,"rpc_role_provider_disclosure_binding")
            item=next((v for v in result["dynamic"] if v["id"]==request["params"]["callId"]),None)
            require(item is not None and item["tool"]==request["params"]["tool"] and item["arguments"]==request["params"]["arguments"] and
                item.get("namespace") is None and item["contentItems"]==contents and item["success"] is reply["result"]["success"] and
                item["status"]==("completed" if item["success"] else "failed"),"rpc_role_dynamic_item_binding")
        require(len({i["id"] for i in result["dynamic"]})==len(dynamic_requests) and
            {i["id"] for i in result["dynamic"]}=={r["params"]["callId"] for r in dynamic_requests},"rpc_role_dynamic_item_binding")
    else:
        outputs=[x for x in second["input"] if x.get("type")=="function_call_output" and x.get("call_id")=="call_fixture_boundary"]
        require(len(outputs)==1 and outputs[0]["output"]==provider["tool_output"],"rpc_role_provider_tool_binding")
    bound={"requests":2,"input_tokens":200,"output_tokens":2048}
    budget_bytes=sealed_bytes(output,"budget.sqlite")
    budget=ProviderBudget(output/"budget.sqlite",bound,{role:{"role":role,"limits":bound,"per_request_output_tokens":1024}},readonly=True)
    report=budget.report();require(report==provider["budget_report"] and report["requests"]==report["dispatched"]==2 and
        report["unknown_requests"]==report["breaches"]==0 and report["input_tokens"]==200 and report["output_tokens"]==20,"rpc_role_budget_binding")
    for i,request in enumerate((first,second),1):
        forwarded=parse_json(sealed_bytes(output,"provider-forwarded-%d.bin"%i,c["max_provider_request_bytes"]).decode())
        require(forwarded=={**request,"max_output_tokens":1024,"truncation":"disabled"},"rpc_role_forwarded_request")
        row=next(r for r in report["rows"] if r["id"]=="request-"+str(i))
        with budget._open() as ledger:require(ledger.get(row["reservation"])["forwarded"]==forwarded,"rpc_role_forwarded_request")
    require(sealed_bytes(output,"budget.sqlite")==budget_bytes,"rpc_role_budget_drift")
    return result


def run_fixture(cli,output,disclosure=None,role_output=None,denied_roots=(),timeout_seconds=None):
    c=role_config();binary=Path(cli)
    require(timeout_seconds is None or type(timeout_seconds) is int and timeout_seconds>0,'rpc_role_execution_binding')
    if timeout_seconds is not None:c['timeout_seconds']=min(c['timeout_seconds'],timeout_seconds)
    denied_roots=sorted({str(Path(p).resolve()) for p in denied_roots})
    require(binary.is_file() and not binary.is_symlink(),"rpc_role_cli");binary=binary.resolve()
    output=Path(output);require(not output.exists(),"rpc_role_output_exists");output.mkdir(parents=True)
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/"memorylab").glob("*.py"))}
    sources["configs/rpc_role_v0.json"]=hashlib.sha256((ROOT/"configs/rpc_role_v0.json").read_bytes()).hexdigest()
    sources["configs/rpc_disclosure_v0.json"]=hashlib.sha256((ROOT/"configs/rpc_disclosure_v0.json").read_bytes()).hexdigest()
    sources["configs/rpc_output_v0.json"]=hashlib.sha256((ROOT/"configs/rpc_output_v0.json").read_bytes()).hexdigest()
    if role_output is not None:
        require(disclosure is not None and disclosure.role in output_config()['roles'] and type(role_output) is dict and
            len(canonical(role_output))<=output_config()['max_output_bytes'],'rpc_role_output_mode')
        role_output=copy.deepcopy(role_output)
    cli_hash=hashlib.sha256(binary.read_bytes()).hexdigest()
    result={"status":"failed","category":"infrastructure_acceptance","actual_model_calls":0,"formal_admission":False,
        "authentication_qualified":False,"provider_budget_qualified":False,"kernel_frozen":False,"usage_policy":"simulated_never_formal",
        "cli_sha256":cli_hash,"source_hashes":sources,"config":c}
    try:
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="memory-rpc-role-",dir="/tmp"))).resolve()
            workspace=root/"subject";private=root/"server-home";other=root/"other-arm"
            for p in (workspace,private/".codex",other):p.mkdir(parents=True)
            (workspace/"SPEC.txt").write_text("Only this harmless workspace belongs to the subject.")
            marker=private/".codex"/"private-marker.txt";marker.write_text("harmless-private-marker-not-input")
            tool_calls=None
            if disclosure:
                disclosure._check()
                name=disclosure.specs[0]["name"]
                ids=[x["id"] for x in disclosure.service.catalog()] if name=="read_evidence" else sorted(disclosure.service.disclosed)
                arguments=([{"id":ids[0],"start":0,"count":128}] if ids else [])+[{"id":"undeclared","start":0,"count":128}] if name=="read_evidence" else ([{"id":ids[0]}] if ids else [])+[{"id":"undeclared"}]
                tool_calls=[{"name":name,"arguments":a} for a in arguments]
            provider=FixtureProvider(c,output,"",tool_calls,disclosure.role if disclosure else 'solver',role_output);stack.callback(provider.close)
            code='''import pathlib,os,json,socket,sys
w=pathlib.Path('.'); (w/'local.txt').write_text('ok')
c={'workspace_read':(w/'SPEC.txt').is_file(),'workspace_write':(w/'local.txt').read_text()=='ok','environment_isolated':os.environ.get('MEMORYLAB_CONTROLLER_MARKER') is None}
try:pathlib.Path(sys.argv[1]).read_bytes();c['private_read_denied']=False
except PermissionError:c['private_read_denied']=True
with socket.socket() as s:
 s.settimeout(2)
 try:s.connect(('127.0.0.1',int(sys.argv[3])));c['network_denied']=False
 except PermissionError:c['network_denied']=True
try:pathlib.Path(sys.argv[2]).write_text('bad');c['private_write_denied']=False
except PermissionError:c['private_write_denied']=True
print(json.dumps(c))
'''
            provider.tool_command=shlex.join([sys.executable,"-I","-B","-c",code,str(marker),str(private/"forbidden.txt"),str(provider.server.server_port)])
            text=fixture_config_text(workspace,provider.url,denied_roots)
            (private/".codex"/"config.toml").write_text(text);(output/"generated-config.toml").write_text(text)
            env=sandbox.worker_environment();env.update(HOME=str(private),CODEX_HOME=str(private/".codex"),TMPDIR=str(root),MEMORYLAB_CONTROLLER_MARKER="harmless-env-not-input")
            env["RUST_LOG"]=c["diagnostic_logging"]
            env.update(NO_PROXY="127.0.0.1,localhost",no_proxy="127.0.0.1,localhost")
            version=subprocess.run([str(binary),"--version"],cwd=workspace,env=env,text=True,capture_output=True,timeout=c["timeout_seconds"])
            require(version.returncode==0,"rpc_role_cli_version");result["cli_version"]=version.stdout.strip()
            cmd=[str(binary),"--no-daemon","--strict-config","-a","never","--enable","skip_host_skill_discovery"]
            for feature in ("memories","apps","plugins","browser_use","computer_use","multi_agent","shell_snapshot","skill_search"):
                cmd.extend(["--disable",feature])
            cmd.extend(["app-server","--listen","stdio://"])
            params={"model":"memorylab-fixture","modelProvider":"memorylab-fixture","cwd":str(workspace),"approvalPolicy":"never",
                "permissions":"probe-restricted","ephemeral":True,"experimentalRawEvents":False,"runtimeWorkspaceRoots":[str(workspace)]}
            if disclosure:params["dynamicTools"]=disclosure.specs
            rpc=FreshRoleRPC(cmd,workspace,env,c,output,params,disclosure,role_output is not None);stack.callback(rpc.close)
            save(output/"recipe.json",{"thread":params,"prompt":rpc.prompt(),"cli_sha256":cli_hash,"source_hashes":sources,
                "config_sha256":hashlib.sha256(text.encode()).hexdigest(),"disclosure_sha256":disclosure.binding_sha256 if disclosure else None,
                "typed_output":role_output,'denied_roots':denied_roots,'execution_seconds':c['timeout_seconds'],'provider_url':provider.url})
            rpc.call("initialize",rpc.recipe("initialize"));rpc.call("initialized",{},True)
            result["thread_response"]=rpc.call("thread/start",params)
            result["turn_response"]=rpc.call("turn/start",rpc.recipe("turn/start"));rpc.complete()
            result["event_result"]=validate_events(rpc.events,rpc.thread_id,rpc.turn_id,len(rpc.server_requests),role_output)
            require(provider.attempts==len(provider.requests)==2 and not provider.errors,"rpc_role_provider_chain")
            raw=canonical(provider.requests)
            require(b"harmless-private-marker-not-input" not in raw and b"harmless-env-not-input" not in raw,"rpc_role_private_context_leak")
            if disclosure:result["role"]=disclosure.role
            else:
                tool_output=provider.tool_result
                require(type(tool_output) is str,"rpc_role_tool_output")
                lines=tool_output.splitlines();checks=next((parse_json(x) for x in lines if x.startswith('{"workspace_read"')),None)
                require(checks is not None and set(checks)=={"workspace_read","workspace_write","environment_isolated","private_read_denied","network_denied","private_write_denied"} and
                    all(v is True for v in checks.values()),"rpc_role_tool_boundary")
                result["checks"]=checks
            rpc.close();provider.close()
            result["event_result"]=validate_sealed(output,rpc.thread_id,rpc.turn_id,disclosure)
            sealed_messages=[parse_json(line.decode()) for line in (output/"stdout.bin").read_bytes().splitlines()]
            save(output/"events.json",[m for m in sealed_messages if "id" not in m])
            require(hashlib.sha256(binary.read_bytes()).hexdigest()==cli_hash and
                all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in sources.items()),"rpc_role_identity_drift")
            result["status"]="actual_fresh_thread_fixture_chain_passed_not_formal"
    except Exception as exc:result["error"]={"code":getattr(exc,"code",type(exc).__name__),"detail":str(exc)}
    finally:save(output/"result.json",result)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description="Actual independent Codex thread with local fixed fixture, no real model")
    p.add_argument("--cli",required=True);p.add_argument("--output",required=True);a=p.parse_args(argv)
    r=run_fixture(a.cli,a.output);print(canonical({k:r[k] for k in ("status","actual_model_calls","formal_admission")}).decode())
    return 0 if r["status"]!="failed" else 2


if __name__=="__main__":raise SystemExit(main())
