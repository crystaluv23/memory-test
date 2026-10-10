"""Model-free actual CLI command-boundary diagnostic, never role admission."""
import argparse
import hashlib
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from . import sandbox
from .common import save
from .contracts import canonical,exact_object,require,sha256
from .kernel_cli import parse_json,read_json

METHODS=["initialize","initialized","permissionProfile/list","command/exec"]


def probe_config():
    c=read_json(Path(__file__).resolve().parent.parent/"configs/app_server_probe_v0.json")
    exact_object(c,{"schema_version","hypothesis","category","allowed_methods","timeout_seconds","command_timeout_ms",
        "max_wire_bytes","max_line_bytes","max_command_output_bytes","temporary_authentication","server_execution",
        "tool_permissions","legacy_sandbox_settings","authentication_store","inference_policy","raw_policy","formal_admission","authentication_qualified","provider_budget_qualified","model_calls"},"invalid_app_server_probe_config")
    expected={"schema_version":"app-server-probe-v0","category":"model_free_cli_boundary_diagnostic","allowed_methods":METHODS,
        "temporary_authentication":"none_only_harmless_markers","server_execution":"trusted_temporary_controller_not_nested_native_sandbox",
        "authentication_store":"isolated_empty_file_store_no_keychain","inference_policy":"no_thread_turn_login_account_or_provider_rpc",
        "raw_policy":"exact_bounded_wire_prefix_and_overflow_chunk_hash_no_silent_truncation",
        "tool_permissions":"explicit_profile_root_deny_minimal_runtime_read_workspace_write_private_deny_network_off",
        "legacy_sandbox_settings":"forbidden"}
    require(all(c[k]==v for k,v in expected.items()) and all(c[k] is False for k in
        ("formal_admission","authentication_qualified","provider_budget_qualified")) and type(c["model_calls"]) is int and c["model_calls"]==0,
        "unsupported_app_server_probe_policy")
    for k in ("timeout_seconds","command_timeout_ms","max_wire_bytes","max_line_bytes","max_command_output_bytes"):
        require(type(c[k]) is int and c[k]>0,"invalid_app_server_probe_limit")
    return c


class ProbeRPC:
    """Bounded stdio reader; allowed methods cannot create a model turn."""
    def __init__(self,command,cwd,env,config,output,command_requests=()):
        self.config=config;self.output=Path(output);self.records=[];self.stderr=bytearray();self.pending=bytearray();self.total=0;self.sequence=0;self.closed=False
        self.command_requests=tuple(canonical(p) for p in command_requests)
        require(not any((self.output/name).exists() for name in ("stdin.bin","stdout.bin","stderr.txt","wire.json","process.json","exit.json")),
            "app_server_probe_transport_exists")
        self.raw_stdin=(self.output/"stdin.bin").open("xb");self.raw_stdout=(self.output/"stdout.bin").open("xb")
        self.process=subprocess.Popen(command,cwd=cwd,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,start_new_session=True,bufsize=0)
        save(self.output/"process.json",{"pid":self.process.pid,"pgid":self.process.pid,"command":command,"status":"started"})
        self.selector=selectors.DefaultSelector()
        for stream,name in ((self.process.stdout,"stdout"),(self.process.stderr,"stderr")):
            os.set_blocking(stream.fileno(),False);self.selector.register(stream,selectors.EVENT_READ,name)

    def _count(self,n):
        self.total+=n;require(self.total<=self.config["max_wire_bytes"],"app_server_probe_wire_overflow")

    def _capture(self,stream,raw,strict=True):
        prefix=raw[:max(0,self.config["max_wire_bytes"]-self.total)]
        if stream=="stdout":self.raw_stdout.write(prefix)
        else:self.stderr.extend(prefix)
        self.total+=len(raw)
        if len(prefix)!=len(raw):
            self.records.append({"direction":"wire_overflow","stream":stream,"chunk_bytes":len(raw),
                "chunk_sha256":hashlib.sha256(raw).hexdigest(),"kept_bytes":len(prefix)})
        if strict:require(self.total<=self.config["max_wire_bytes"],"app_server_probe_wire_overflow")

    def call(self,method,params=None,notify=False):
        require(method in METHODS,"app_server_probe_method_forbidden")
        require(notify==(method=="initialized"),"app_server_probe_notification_policy")
        if method=="command/exec":require(canonical(params) in self.command_requests,"app_server_probe_command_not_bound")
        message={"method":method}
        if not notify:self.sequence+=1;message.update(id=self.sequence,params=params or {})
        raw=canonical(message)+b"\n";require(len(raw)<=self.config["max_line_bytes"],"app_server_probe_line_overflow")
        self._count(len(raw));self.records.append({"direction":"request","message":message})
        n=self.process.stdin.write(raw);self.raw_stdin.write(raw[:n]);self.process.stdin.flush()
        require(n==len(raw),"app_server_probe_incomplete_write")
        if notify:return None
        deadline=time.monotonic()+self.config["timeout_seconds"]
        while True:
            while b"\n" in self.pending:
                line,_,rest=self.pending.partition(b"\n");self.pending=bytearray(rest)
                require(len(line)<=self.config["max_line_bytes"],"app_server_probe_line_overflow")
                response=parse_json(line.decode());require(type(response) is dict,"invalid_app_server_probe_response")
                self.records.append({"direction":"response","message":response})
                if "id" in response:
                    require(type(response["id"]) is int and response["id"]==self.sequence and "method" not in response,"unexpected_app_server_probe_response")
                    require("error" not in response and "result" in response,"app_server_probe_rpc_error")
                    return response["result"]
                require(type(response.get("method")) is str and not response["method"].startswith(("thread/","turn/","account/")),
                    "unexpected_app_server_probe_notification")
            require(len(self.pending)<=self.config["max_line_bytes"],"app_server_probe_line_overflow")
            remaining=deadline-time.monotonic();require(remaining>0,"app_server_probe_timeout")
            events=self.selector.select(min(remaining,1))
            require(events or self.process.poll() is None,"app_server_probe_process_exited")
            for key,_ in events:
                raw=os.read(key.fileobj.fileno(),65536)
                if not raw:self.selector.unregister(key.fileobj);continue
                self._capture(key.data,raw)
                if key.data=="stdout":self.pending.extend(raw)

    def close(self):
        if self.closed:return
        self.closed=True
        self.process.stdin.close()
        try:self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid,signal.SIGTERM)
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:os.killpg(self.process.pid,signal.SIGKILL);self.process.wait(timeout=2)
        # Drain only bounded remaining bytes; raw stderr is diagnostic, never
        # instructions. No production credentials are present in this process.
        for key in list(self.selector.get_map().values()):
            try:raw=os.read(key.fileobj.fileno(),max(1,self.config["max_wire_bytes"]-self.total+1))
            except BlockingIOError:raw=b""
            self._capture(key.data,raw,False)
            if key.data=="stdout" and raw:self.records.append({"direction":"unparsed_tail","bytes":raw[:max(0,self.config["max_line_bytes"])].decode(errors="replace")})
        for f in (self.raw_stdin,self.raw_stdout):f.flush();os.fsync(f.fileno());f.close()
        save(self.output/"wire.json",self.records)
        (self.output/"stderr.txt").write_bytes(self.stderr)
        save(self.output/"exit.json",{"exit_code":self.process.returncode,"wire_bytes":self.total,
            "wire_limit_exceeded":self.total>self.config["max_wire_bytes"],"pending_bytes":len(self.pending),
            "raw_hashes":{name:hashlib.sha256((self.output/name).read_bytes()).hexdigest() for name in ("stdin.bin","stdout.bin","stderr.txt")}})
        self.selector.close();self.process.stdout.close();self.process.stderr.close()


def profile_config(workspace,private,other,runtime,positive_marker,denied_roots=()):
    q=lambda p:json.dumps(str(p))
    lines=['approval_policy = "never"','default_permissions = "probe-restricted"',
        'shell_environment_policy.inherit = "none"','shell_environment_policy.set = { PATH = "/usr/bin:/bin", HOME = '+q(workspace)+', TMPDIR = '+q(workspace)+' }',
        'analytics.enabled = false','feedback.enabled = false','web_search = "disabled"','cli_auth_credentials_store = "file"']
    for name in ("probe-restricted","probe-positive"):
        lines.extend(['[permissions.'+name+'.filesystem]', '":root" = "deny"','":minimal" = "read"',
            q(workspace)+' = "write"',q(runtime)+' = "read"',q(private)+' = "deny"',q(other)+' = "deny"'])
        if name=="probe-positive":lines.append(q(positive_marker)+' = "read"')
        for p in denied_roots:lines.append(q(p)+' = "deny"')
        lines.extend(['[permissions.'+name+'.network]','enabled = false'])
    return "\n".join(lines)+"\n"


def run_probe(cli,output):
    c=probe_config();binary=Path(cli)
    require(binary.is_file() and not binary.is_symlink(),"invalid_app_server_probe_cli");binary=binary.resolve()
    output=Path(output);require(not output.exists(),"app_server_probe_output_exists");output.mkdir(parents=True)
    sources={name:hashlib.sha256((Path(__file__).resolve().parent.parent/name).read_bytes()).hexdigest() for name in
        ("memorylab/app_server_probe.py","memorylab/sandbox.py","memorylab/contracts.py","memorylab/common.py","memorylab/kernel_cli.py","configs/app_server_probe_v0.json")}
    identity={"cli_path":str(binary),"cli_sha256":hashlib.sha256(binary.read_bytes()).hexdigest(),"source_hashes":sources}
    result={"category":c["category"],"status":"failed","config":c,"identity":identity,"checks":{},
        "actual_model_calls":0,"formal_admission":False,"authentication_qualified":False,"provider_budget_qualified":False,"kernel_frozen":False}
    rpc=None
    try:
        with ExitStack() as stack:
            temporary=stack.enter_context(tempfile.TemporaryDirectory(prefix="memory-cli-boundary-",dir="/tmp"))
            root=Path(temporary).resolve();workspace=root/"subject";private=root/"server-home";other=root/"other-arm"
            for p in (workspace,private/".codex",other):p.mkdir(parents=True)
            (workspace/"SPEC.txt").write_text("A harmless model-free boundary diagnostic.")
            marker=private/".codex"/"private-auth-marker.txt";marker.write_text("harmless-private-marker")
            sibling=other/"marker.txt";sibling.write_text("harmless-other-arm-marker")
            runtime=Path(sys.executable).resolve().parent.parent
            text=profile_config(workspace,private,other,runtime,marker)
            (output/"generated-config.toml").write_text(text)
            (private/".codex"/"config.toml").write_text(text)
            env=sandbox.worker_environment();env.update(HOME=str(private),CODEX_HOME=str(private/".codex"),TMPDIR=str(root),
                MEMORYLAB_CONTROLLER_MARKER="harmless-environment-marker")
            # Only a generated configuration in an isolated empty home. These
            # app-server options differ from the exec subcommand's ignore flags.
            command=[str(binary),"--no-daemon","--strict-config","-a","never"]
            command.extend(["--enable","skip_host_skill_discovery"])
            for feature in ("memories","apps","plugins","browser_use","computer_use","multi_agent","shell_snapshot","skill_search"):
                command.extend(["--disable",feature])
            command.extend(["app-server","--listen","stdio://"])
            version=subprocess.run([str(binary),"--version"],
                cwd=root,env=env,text=True,capture_output=True,timeout=c["timeout_seconds"])
            save(output/"version.json",{"exit_code":version.returncode,"stdout":version.stdout,"stderr":version.stderr})
            require(version.returncode==0,"app_server_probe_version_failed");identity["cli_version"]=version.stdout.strip()
            save(output/"start.json",{"identity":identity,"command":command,"config_sha256":hashlib.sha256(text.encode()).hexdigest(),
                "environment_keys":sorted(env),"environment_sha256":sha256(env),"actual_model_calls":0})
            code='''import json,os,pathlib,socket,sys
w=pathlib.Path(sys.argv[1]);paths=[pathlib.Path(x) for x in sys.argv[2:4]];control_socket=sys.argv[4];port=int(sys.argv[5])
checks={'workspace_read':(w/'SPEC.txt').read_text().startswith('A harmless')}
(w/'local.txt').write_text('ok');checks['workspace_write']=(w/'local.txt').read_text()=='ok'
checks['environment_isolated']=os.environ.get('MEMORYLAB_CONTROLLER_MARKER') is None and os.environ.get('CODEX_HOME') is None
checks['private_read_errors']=[]
for p in paths:
 try:p.read_bytes();checks['private_read_errors'].append('readable')
 except OSError as e:checks['private_read_errors'].append(type(e).__name__)
try:(paths[0].parent/'forbidden-write.txt').write_text('bad');checks['private_write_denied']=False
except PermissionError:checks['private_write_denied']=True
for name,family,target in [('control_socket_denied',socket.AF_UNIX,control_socket),('network_denied',socket.AF_INET,('127.0.0.1',port))]:
 with socket.socket(family,socket.SOCK_STREAM) as s:
  s.settimeout(2)
  try:s.connect(target);checks[name]=False
  except PermissionError:checks[name]=True
  except OSError as e:checks[name]=False;checks[name+'_error']=type(e).__name__
print(json.dumps(checks))
'''
            control_socket=private/"controller.sock"
            unix=stack.enter_context(socket.socket(socket.AF_UNIX,socket.SOCK_STREAM));unix.bind(str(control_socket));unix.listen(2)
            tcp=stack.enter_context(socket.socket(socket.AF_INET,socket.SOCK_STREAM));tcp.bind(("127.0.0.1",0));tcp.listen(2)
            # Actual reachable listeners prevent connection refusal or missing
            # targets from being misreported as a permission denial.
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:s.connect(str(control_socket))
            with socket.create_connection(tcp.getsockname(),timeout=2):pass
            params={"command":[sys.executable,"-I","-B","-c",code,str(workspace),str(marker),str(sibling),str(control_socket),str(tcp.getsockname()[1])],"cwd":str(workspace),
                "permissionProfile":"probe-restricted","timeoutMs":c["command_timeout_ms"],"outputBytesCap":c["max_command_output_bytes"]}
            positive={**params,"permissionProfile":"probe-positive","command":[sys.executable,"-I","-B","-c",
                "import pathlib,sys;print(pathlib.Path(sys.argv[1]).read_text())",str(marker)]}
            rpc=ProbeRPC(command,workspace,env,c,output,(params,positive));stack.callback(rpc.close)
            result["initialize"]=rpc.call("initialize",{"clientInfo":{"name":"memorylab-boundary-probe","version":"0.1"},"capabilities":{"experimentalApi":True}})
            rpc.call("initialized",notify=True)
            profiles=rpc.call("permissionProfile/list");result["profiles"]=profiles
            require(all(any(p["id"]==name and p["allowed"] is True for p in profiles["data"]) for name in ("probe-restricted","probe-positive")),
                "app_server_probe_profiles_unavailable")
            response=rpc.call("command/exec",params);result["restricted_response"]=response
            require(response["exitCode"]==0,"app_server_probe_command_failed")
            checks=parse_json(response["stdout"]);result["checks"]=checks
            checks["network_target_reachable"]=True;checks["control_socket_reachable"]=True
            response=rpc.call("command/exec",positive);result["positive_response"]=response
            checks["positive_private_read"]=response["exitCode"]==0 and response["stdout"].strip()=="harmless-private-marker"
            require(all(checks[k] is True for k in ("workspace_read","workspace_write","environment_isolated","private_write_denied","positive_private_read",
                "control_socket_denied","network_denied","network_target_reachable","control_socket_reachable")) and
                checks["private_read_errors"]==["PermissionError","PermissionError"],"app_server_probe_boundary_failed")
            require(hashlib.sha256(binary.read_bytes()).hexdigest()==identity["cli_sha256"],"app_server_probe_cli_drift")
            require(all(hashlib.sha256((Path(__file__).resolve().parent.parent/name).read_bytes()).hexdigest()==value for name,value in sources.items()),"app_server_probe_source_drift")
            result["status"]="command_boundary_passed_not_authentication_qualified"
            rpc.close();rpc=None
            exited=read_json(output/"exit.json")
            require(exited["exit_code"]==0 and exited["wire_limit_exceeded"] is False and exited["pending_bytes"]==0,"app_server_probe_shutdown_unverified")
    except Exception as exc:
        result["status"]="failed"
        result["error"]={"code":getattr(exc,"code",type(exc).__name__),"detail":str(exc)}
    finally:
        if rpc is not None:
            try:rpc.close()
            except Exception as exc:result["status"]="failed";result["cleanup_error"]=str(exc)
        save(output/"result.json",result)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description="Actual app-server command-boundary diagnostic, zero model calls")
    p.add_argument("--cli",required=True);p.add_argument("--output",required=True);args=p.parse_args(argv)
    result=run_probe(args.cli,args.output)
    print(canonical({k:result[k] for k in ("status","checks","actual_model_calls","authentication_qualified")}).decode())
    return 0 if result["status"]!="failed" else 2


if __name__=="__main__":raise SystemExit(main())
