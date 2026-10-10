"""Receipt-bound diagnostic actor stages; explicit fixtures, no formal proof."""
import base64
import hashlib
import json
import shutil
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

from . import sandbox
from .admission import RoleAdmission
from .rpc_admission import RPCAdmission
from .rpc_disclosure import RPCDisclosure,SealedDisclosure
from .rpc_output import native_program
from .rpc_materialization_fixture import fixed_final
from .rpc_role import run_fixture as run_native_fixture,sealed_json
from .common import exclusive_run,save
from .contracts import bounded_text,canonical,exact_object,require,sha256,timestamp
from .derived import DerivedStage
from .kernel_cli import parse_json,read_json
from .roles import EvidenceSocket,SOURCE_CLIENT,capture_transport,process_status
from .runtime import CLIENT,RuntimeSession,RuntimeSocket,UsageStore,file_log
from .stores import CorpusStore
from .task_stage import ZERO
from .workspace import read_workspace_bytes


def role_stage_config():
    c=read_json(Path(__file__).resolve().parent.parent/"configs/role_stage_v0.json")
    exact_object(c,{"schema_version","hypothesis","mode","roles","max_fixture_bytes","max_seal_bytes","control_boundary",
        "cross_store_policy","automatic_retry","formal_admission","model_calls"},"invalid_role_stage_config")
    require(c["schema_version"]=="role-stage-v0" and c["mode"]=="explicit_diagnostic_transports" and
        c["roles"]==["extractor","consolidation","reflector","evaluator","proposer","shadow"] and
        c["control_boundary"]=="ledger_parent_scope_root_and_output_root" and
        c["cross_store_policy"]=="immutable_child_completion_then_parent_publish" and
        c["automatic_retry"] is False and c["formal_admission"] is False and type(c["model_calls"]) is int and c["model_calls"]==0,
        "unsupported_role_stage_policy")
    for key in ("max_fixture_bytes","max_seal_bytes"):require(type(c[key]) is int and c[key]>0,"invalid_role_stage_limit")
    return c


class RoleStages:
    def __init__(self,inputs,output):
        self.inputs,self.journal,self.ledger=inputs,inputs.journal,inputs.ledger
        self.config=role_stage_config();self.output=Path(output)
        require(not self.output.is_symlink(),"invalid_role_stage_output")
        self.output.mkdir(parents=True,exist_ok=True);self.output=self.output.resolve()

    def _directory(self,slot):return self.output/("role-"+slot)

    def _command(self,worker,code):
        return sandbox.command(worker,self.ledger.path.parent,[sys.executable,"-I","-B","-c",code],True,False,
            denied_roots=(self.inputs.output,self.output,*self.inputs.denied_roots))

    @staticmethod
    def _binding(p,scope):
        binding={"path":scope["path"],"binding":scope["binding"]} if scope else None
        require(p["resources"].get("store_binding")==binding,"role_stage_store_binding_mismatch")
        return binding

    def _memory(self,p,cards,persist=None):
        if "projection" not in p["resources"]:return None
        context=p["packet"]["payload"]["context"]
        role=p["packet"]["payload"]["role"]
        require(role in ("evaluator","proposer"),"unexpected_role_stage_memory")
        text=canonical([cards.evidence.read(r,role,p["namespace"],p["cutoff"]) for r in p["resources"]["source_refs"]]).decode()
        memory=RuntimeSession(cards.ledger,p["resources"]["projection"],text,persist=persist)
        require(json.loads(memory.briefs())==context["briefs"],"role_stage_brief_mismatch")
        return memory

    @staticmethod
    def _native(e):return e['request'].get('transport')=='native_rpc_fixed_fixture'

    def _admission(self,e,cards,registry):
        return (RPCAdmission if self._native(e) else RoleAdmission)(cards,registry,role=self.inputs.role)

    def run_native(self,slot,program,at,fault=None):
        timestamp(at);native_program(program)
        with exclusive_run(self.output):
            input_ref=self.inputs.prepare(slot);p=self.inputs.resolve(input_ref)['payload']
            require(p['role'] in self.config['roles'],'unsupported_role_stage_role')
            if p['action']=='disabled':return self.journal.begin_bound('role-disabled-'+sha256(input_ref),input_ref,self.inputs)
            if p['action']=='bound_noop':return self._noop(input_ref,p,at,fault)
            final=fixed_final(p['packet'])
            request={'input':input_ref,'transport':'native_rpc_fixed_fixture','program':program,'final':final,
                'config':self.config,'output':str(self.output)}
            start_id='role-stage-start-'+sha256({'plan':self.inputs.plan,'slot':slot})
            existing=self.ledger.receipt(start_id,request);directory=self._directory(slot)
            if existing:
                ref=existing['result']['execution'];e=self.ledger.get(ref)
                if self.ledger.receipt(e['complete_request_id']):return {**self.resolve(slot),'cache_hit':True,'new_model_calls':0}
                require(process_status(directory/'transport/handle')['status']=='exited','role_stage_exit_unverified')
                return self.commit(ref,at,fault,interrupted=not (directory/'seal.json').exists())
            directory.mkdir(parents=True,exist_ok=True)
            with self.inputs._scope(self.inputs.nodes[slot]) as (cards,registry,store,corpus,scope):
                binding=self._binding(p,scope);admission=RPCAdmission(cards,registry,role=self.inputs.role)
                service=admission._regenerate(p['packet']) or self._memory(p,cards)
                gateway=(RPCDisclosure.reopen_initial(p['packet'],service,directory/'disclosure') if (directory/'disclosure').exists()
                    else RPCDisclosure(p['packet'],service,directory/'disclosure'))
                with self.ledger.mutation():
                    begun=self.journal.begin_bound('role-stage-begin-'+sha256(request),input_ref,self.inputs)['result']
                    require(begun['status']=='stage_started','role_stage_already_terminal')
                    role_attempt=admission.prepare('role-actor-begin-'+sha256(request),gateway,program['cli_path'])['result']['attempt']
                    ref=self.ledger.put({'kind':'role_stage_execution','id':'execution-'+sha256(request),'version':1,'request':request,
                        'input':input_ref,'stage_attempt':begun['attempt'],'role_attempt':role_attempt,'store_binding':binding,
                        'packet':p['packet'],'budget':p['budget'],'timestamp':at,'role_config':self.inputs.role,
                        'category':'infrastructure_acceptance','start_request_id':start_id,'complete_request_id':'role-stage-end-'+sha256(request)},'control')
                    self.ledger.commit_receipt(start_id,request,{'status':'role_stage_started','execution':ref},'control')
                    if fault:fault('start')
                roots=sorted({str(self.ledger.path.parent),str(cards.ledger.path.parent),str(self.inputs.output),str(self.output),*self.inputs.denied_roots})
                run_native_fixture(program['cli_path'],directory/'transport',gateway,final,roots,p['budget']['seconds'])
                if fault:fault('after_process')
                files=admission._snapshot_rpc(directory)
                seal={'execution':ref,'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()},
                    'source_events':gateway.service.events if gateway.initial_identity['kind']=='source' else [],
                    'memory_events':gateway.service.events if gateway.initial_identity['kind']=='memory' else []}
                require(len(canonical(seal))<=self.config['max_seal_bytes'],'role_stage_seal_overflow');save(directory/'seal.json',seal)
                if fault:fault('after_seal')
            return self.commit(ref,at,fault)

    def run_fixture(self,slot,code,at,fault=None):
        timestamp(at)
        with exclusive_run(self.output):
            input_ref=self.inputs.prepare(slot);p=self.inputs.resolve(input_ref)["payload"]
            require(p["role"] in self.config["roles"],"unsupported_role_stage_role")
            if p["action"]=="disabled":
                return self.journal.begin_bound("role-disabled-"+sha256(input_ref),input_ref,self.inputs)
            if p["action"]=="bound_noop":return self._noop(input_ref,p,at,fault)
            bounded_text(code,self.config["max_fixture_bytes"],"role_fixture_overflow")
            request={"input":input_ref,"fixture_sha256":hashlib.sha256(code.encode()).hexdigest(),"config":self.config,"output":str(self.output)}
            start_id="role-stage-start-"+sha256({"plan":self.inputs.plan,"slot":slot})
            existing=self.ledger.receipt(start_id,request)
            if existing:
                ref=existing["result"]["execution"];execution=self.ledger.get(ref)
                if self.ledger.receipt(execution["complete_request_id"]):return {**self.resolve(slot),"cache_hit":True,"new_model_calls":0}
                directory=self._directory(slot);status=process_status(directory/"transport")
                require(status["status"]=="exited","role_stage_exit_unverified")
                return self.commit(ref,at,fault,interrupted=not (directory/"seal.json").exists())
            worker=Path(tempfile.mkdtemp(prefix="memory-role-stage-",dir="/tmp"))
            try:
                node=self.inputs.nodes[slot]
                with self.inputs._scope(node) as (cards,registry,store,corpus,scope):
                    binding=self._binding(p,scope);admission=RoleAdmission(cards,registry,role=self.inputs.role)
                    with self.ledger.mutation():
                        begun=self.journal.begin_bound("role-stage-begin-"+sha256(request),input_ref,self.inputs)["result"]
                        require(begun["status"]=="stage_started","role_stage_already_terminal")
                        role_attempt=admission.begin("role-actor-begin-"+sha256(request),p["packet"],p["budget"],"infrastructure_acceptance")["result"]["attempt"]
                        ref=self.ledger.put({"kind":"role_stage_execution","id":"execution-"+sha256(request),"version":1,"request":request,
                            "input":input_ref,"stage_attempt":begun["attempt"],"role_attempt":role_attempt,"store_binding":binding,
                            "worker":str(worker),"fixture_code":code,"packet":p["packet"],"budget":p["budget"],"timestamp":at,
                            "role_config":self.inputs.role,"category":"infrastructure_acceptance","start_request_id":start_id,
                            "complete_request_id":"role-stage-end-"+sha256(request)},"control")
                        self.ledger.commit_receipt(start_id,request,{"status":"role_stage_started","execution":ref},"control")
                    directory=self._directory(slot);directory.mkdir(exist_ok=False)
                    reader=admission._regenerate(p["packet"])
                    save(directory/"source_wire.json",[]);save(directory/"memory_wire.json",[])
                    if reader:
                        reader.persist=file_log(directory/"source_wire.json");(worker/"read_source.py").write_text(SOURCE_CLIENT)
                    memory=self._memory(p,cards,file_log(directory/"memory_wire.json"))
                    if memory:(worker/"open_memory.py").write_text(CLIENT)
                    (worker/"role.schema.json").write_bytes(canonical(p["packet"]["payload"]["output_schema"])+b"\n")
                    with ExitStack() as stack:
                        if reader:stack.enter_context(EvidenceSocket(worker,reader))
                        if memory:stack.enter_context(RuntimeSocket(worker,memory))
                        capture_transport(self._command(worker,code),worker,p["packet"],p["budget"],directory/"transport",self.inputs.role,
                            invocation_identity={"category":"scripted_transport_fixture","model_session_invocations":0})
                    if fault:fault("after_process")
                    files=admission._snapshot(directory/"transport")
                    seal={"execution":ref,"files":{name:hashlib.sha256(data).hexdigest() for name,data in files.items()},
                        "source_events":read_json(directory/"source_wire.json"),"memory_events":read_json(directory/"memory_wire.json")}
                    require(len(canonical(seal))<=self.config["max_seal_bytes"],"role_stage_seal_overflow")
                    save(directory/"seal.json",seal)
                    if fault:fault("after_seal")
                return self.commit(ref,at,fault)
            finally:shutil.rmtree(worker)

    def _execution(self,ref):
        e=self.ledger.get(ref);start=self.ledger.receipt(e["start_request_id"],e["request"])
        require(start is not None and start["result"]["execution"]==ref,"uncommitted_role_stage_execution")
        p=self.inputs.resolve(e["input"])["payload"]
        require(e["packet"]==p["packet"] and e["budget"]==p["budget"] and e["role_config"]==self.inputs.role and
            e["request"]["config"]==self.config and e["request"]["output"]==str(self.output),"role_stage_execution_drift")
        if self._native(e):
            native_program(e['request']['program'])
            require(e['request']['input']==e['input'] and e['request']['final']==fixed_final(p['packet']),'role_stage_execution_drift')
        else:require(e["request"]["fixture_sha256"]==hashlib.sha256(e["fixture_code"].encode()).hexdigest(),"role_stage_execution_drift")
        return e,p

    def _memory_audit(self,p,cards,events):
        require(type(events) is list,"invalid_role_memory_audit")
        memory=self._memory(p,cards)
        if memory is None:require(not events,"unexpected_role_memory_audit");return
        require(events and memory.events[0]==events[0],"role_memory_audit_mismatch")
        for event in events[1:]:
            if event.get("request")=={"invalid_wire":True}:memory._emit("detail","open",event["request"],{"error":"invalid_memory_wire"})
            else:memory.open(event["request"])
            require(memory.events[-1]==event,"role_memory_audit_mismatch")
        require(memory.events==events,"role_memory_audit_mismatch")

    def _artifacts(self,p,cards,completion,memory_events,at,key):
        r=completion["result"];art={"role_completion":completion["request_id"],"role_capture":r["capture"],"observable_sources":[]}
        if p["role"]=="extractor" and r["outcome"]=="materialized":
            art["snapshot"]=CorpusStore(cards).capture("role-snapshot-"+sha256(key),p["namespace"],cards.ledger.head())
        elif p["role"] in ("consolidation","reflector") and r["outcome"]=="materialized":
            art["batch"]=r["materialization"]["result"]["batch"]
        elif p["packet"]["payload"]["role"] in ("evaluator","proposer"):
            capture=cards.ledger.get(r["capture"]);final=capture["transport"].get("final")
            text=canonical(final if final is not None else {"status":"unavailable","reason":r["code"]}).decode()
            if len(text.encode())>cards.kernel["limits"]["source_text_bytes"]:
                text=canonical({"status":"unavailable","reason":"whole_event_exceeds_source_limit","original_sha256":hashlib.sha256(text.encode()).hexdigest(),"original_bytes":len(text.encode())}).decode()
            art["observable_sources"]=[cards.evidence.register("role-source-"+sha256(key),p["namespace"],"agent_output",p["slot"],at,text)]
            for i,event in enumerate(memory_events):
                for ref in event["memory_refs"] if event["success"] else []:
                    UsageStore(cards).record("role-use-"+sha256({"key":key,"event":i,"ref":ref}),p["namespace"],p["slot"],event["operation"],ref)
            art["proposal"]=r["materialization"]
        return art

    def _child(self,e,p,cards,registry,seal,interrupted):
        admission=self._admission(e,cards,registry);directory=self._directory(p["slot"])
        request={"execution":e,"seal_sha256":sha256(seal) if seal else None,"interrupted":interrupted}
        id_="role-child-publish-"+sha256(e)
        with cards.ledger.mutation():
            prior=cards.ledger.receipt(id_,request)
            if prior:
                if self._native(e):admission.verify(prior['result']['completion']['request_id'])
                return prior
            if self._native(e):
                if interrupted:
                    completion=admission.interrupt('role-actor-end-'+sha256(e),e['role_attempt'],e['timestamp'])
                    artifacts={'role_completion':completion['request_id'],'role_capture':completion['result']['capture'],'observable_sources':[]}
                else:
                    self._memory_audit(p,cards,seal['memory_events'])
                    completion=admission.complete('role-actor-end-'+sha256(e),e['role_attempt'],e['timestamp'])
                    capture=cards.ledger.get(completion['result']['capture'])
                    if completion['result']['outcome']=='materialized':
                        require(capture['source_events']==seal['source_events'] and capture['memory_events']==seal['memory_events'],'role_stage_native_audit_drift')
                    artifacts=self._artifacts(p,cards,completion,seal['memory_events'],e['timestamp'],e)
            elif interrupted:
                audits={};remaining=admission.config["max_source_audit_bytes"]
                for name in ("source_wire.json","memory_wire.json"):
                    path=directory/name
                    if not path.exists():continue
                    require(path.is_file() and not path.is_symlink(),"invalid_interrupted_role_audit")
                    with path.open("rb") as stream:data=stream.read(remaining+1)
                    require(len(data)<=remaining,"invalid_interrupted_role_audit");audits[name]=data;remaining-=len(data)
                completion=admission.interrupt("role-actor-end-"+sha256(e),e["role_attempt"],directory/"transport",e["timestamp"],audits)
                artifacts={"role_completion":completion["request_id"],"role_capture":completion["result"]["capture"],"observable_sources":[]}
            else:
                self._memory_audit(p,cards,seal["memory_events"])
                completion=admission.complete("role-actor-end-"+sha256(e),e["role_attempt"],directory/"transport",seal["source_events"],e["timestamp"])
                artifacts=self._artifacts(p,cards,completion,seal["memory_events"],e["timestamp"],e)
            result={"completion":completion,"artifacts":artifacts,"store_binding":e["store_binding"],"input":e["input"],
                "outcome":"interrupted" if interrupted else "completed" if completion["result"]["outcome"]=="materialized" else "failed"}
            return cards.ledger.commit_receipt(id_,request,result,"control")

    def commit(self,execution_ref,at,fault=None,interrupted=False):
        timestamp(at);e,p=self._execution(execution_ref)
        if self.ledger.receipt(e["complete_request_id"]):return {**self.resolve(p["slot"]),"cache_hit":True,"new_model_calls":0}
        directory=self._directory(p["slot"])
        require(process_status(directory/"transport"/"handle" if self._native(e) else directory/"transport")["status"]=="exited","role_stage_exit_unverified")
        require(not interrupted or not (directory/"seal.json").exists(),"sealed_role_cannot_be_interrupted")
        seal=None if interrupted else read_json(directory/"seal.json")
        if seal:
            exact_object(seal,{"execution","files","source_events","memory_events"},"invalid_role_stage_seal")
            require(seal["execution"]==execution_ref and len(canonical(seal))<=self.config["max_seal_bytes"],"role_stage_seal_mismatch")
        with self.inputs._scope(self.inputs.nodes[p["slot"]]) as (cards,registry,store,corpus,scope):
            self._binding(p,scope);admission=self._admission(e,cards,registry)
            files=admission._snapshot_rpc(directory) if self._native(e) else admission._snapshot(directory/"transport")
            if seal:
                require(seal["files"]=={name:hashlib.sha256(data).hexdigest() for name,data in files.items()},"role_stage_raw_drift")
                if self._native(e):
                    recipe=sealed_json(directory/'transport','recipe.json')
                    roots=sorted({str(self.ledger.path.parent),str(cards.ledger.path.parent),str(self.inputs.output),str(self.output),*self.inputs.denied_roots})
                    require(recipe['cli_sha256']==e['request']['program']['cli_sha256'] and recipe['typed_output']==e['request']['final'] and
                        recipe['denied_roots']==roots and recipe['execution_seconds']<=p['budget']['seconds'],'role_stage_native_recipe_drift')
                else:
                    start=parse_json(files["start.json"].decode())
                    require(start["command"]==self._command(e["worker"],e["fixture_code"]),"role_stage_command_drift")
            child=None
            if scope:
                child=self._child(e,p,cards,registry,seal,interrupted)
                if fault:fault("child_committed")
            with self.ledger.mutation():
                if child is None:child=self._child(e,p,cards,registry,seal,interrupted)
                if fault:fault("materialized")
                published=self.ledger.put({"kind":"role_stage_publication","id":"publish-"+sha256(execution_ref),"version":1,
                    "execution":execution_ref,"input":e["input"],"store_binding":e["store_binding"],"child_receipt":child,
                    "child_receipt_sha256":sha256(child),"seal":seal,"formal_admission":False},"control")
                if fault:fault("publication")
                artifacts={**child["result"]["artifacts"],"role_publication":published,"store_binding":e["store_binding"]}
                finish=self.journal.complete("role-stage-finish-"+sha256(execution_ref),e["stage_attempt"],child["result"]["outcome"],ZERO,[],artifacts)
                if fault:fault("stage")
                result={"status":"role_stage_committed","publication":published,"stage":finish,"role_result":child["result"]["completion"]["result"],
                    "actual_model_calls":0,"formal_admission":False,"gain_evidence":False,"cache_hit":False,"new_model_calls":0}
                self.ledger.commit_receipt(e["complete_request_id"],{"execution":execution_ref,"child_receipt_sha256":sha256(child)},result,"control")
                if fault:fault("receipt")
                return result

    def resolve(self,slot):
        start=self.ledger.receipt("role-stage-start-"+sha256({"plan":self.inputs.plan,"slot":slot}))
        require(start is not None,"missing_role_stage_execution")
        e,p=self._execution(start["result"]["execution"]);completion=self.ledger.receipt(e["complete_request_id"])
        require(completion is not None,"unpublished_role_stage")
        publication=self.ledger.get(completion["result"]["publication"])
        require(publication["execution"]==start["result"]["execution"] and publication["input"]==e["input"] and publication["store_binding"]==e["store_binding"] and
            sha256(publication["child_receipt"])==publication["child_receipt_sha256"],"role_stage_publication_drift")
        require(completion["request_sha256"]==sha256({"execution":publication["execution"],"child_receipt_sha256":publication["child_receipt_sha256"]}),"role_stage_publication_drift")
        with self.inputs._scope(self.inputs.nodes[slot]) as (cards,registry,store,corpus,scope):
            self._binding(p,scope);child=publication["child_receipt"]
            require(cards.ledger.receipt(child["request_id"])==child,"role_stage_child_receipt_drift")
            role=child["result"]["completion"]
            require(cards.ledger.receipt(role["request_id"])==role,"role_stage_completion_drift")
            directory=self._directory(slot);admission=self._admission(e,cards,registry)
            if self._native(e):admission.verify(role['request_id'])
            files=admission._snapshot_rpc(directory) if self._native(e) else admission._snapshot(directory/"transport")
            capture=cards.ledger.get(role["result"]["capture"])
            hashes={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}
            seal=publication["seal"]
            if seal is None:
                require(not (directory/"seal.json").exists(),"role_stage_interruption_drift")
            else:
                saved=parse_json(read_workspace_bytes(directory,"seal.json",{"max_depth":1,"max_file_bytes":self.config["max_seal_bytes"]}).decode())
                require(saved==seal and seal["files"]==hashes,"role_stage_raw_drift")
            if not self._native(e):
                require(capture["files"]==hashes,"role_stage_raw_drift")
                status=process_status(directory/"transport")
                require(status["status"]=="exited","role_stage_exit_unverified")
                if seal:
                    for name,key in (("source_wire.json","source_events"),("memory_wire.json","memory_events")):
                        actual=parse_json(read_workspace_bytes(directory,name,{"max_depth":1,"max_file_bytes":self.config["max_seal_bytes"]}).decode())
                        require(actual==seal[key],"role_stage_audit_drift")
                else:
                    require(capture["process_status"]["status"]=="exited" and capture["process_status"]["handle"]==status["handle"],"role_stage_interruption_drift")
                    expected=capture["unverified_audits"]
                    present={name for name in ("source_wire.json","memory_wire.json") if (directory/name).exists()}
                    require(present==set(expected),"role_stage_interruption_drift")
                    for name,value in expected.items():
                        raw=read_workspace_bytes(directory,name,{"max_depth":1,"max_file_bytes":self.config["max_seal_bytes"]})
                        require(base64.b64encode(raw).decode()==value["base64"] and hashlib.sha256(raw).hexdigest()==value["sha256"] and
                            len(raw)==value["bytes"],"role_stage_interruption_drift")
            require(child["result"]["input"]==e["input"] and child["result"]["store_binding"]==e["store_binding"] and
                completion["result"]["role_result"]==role["result"],"role_stage_publication_drift")
        dependency=self.inputs._dependency(slot)
        require(dependency["artifacts"]["role_publication"]==completion["result"]["publication"] and
            dependency["result"]==completion["result"]["stage"]["result"],"role_stage_finish_drift")
        return completion["result"]

    def _noop(self,input_ref,p,at,fault):
        require(p["role"] in ("consolidation","reflector"),"unsupported_role_noop")
        with self.inputs._scope(self.inputs.nodes[p["slot"]]) as (cards,registry,store,corpus,scope):
            self._binding(p,scope)
            started=self.journal.begin_bound("role-noop-start-"+sha256(input_ref),input_ref,self.inputs)["result"]
            if started["status"]=="stage_cache_hit":return started
            input_=p["packet"]["payload"]["context"]["derived_input"]
            with self.ledger.mutation():
                with cards.ledger.mutation():
                    result=DerivedStage(cards,registry).accept("role-noop-batch-"+sha256(input_ref),input_,canonical({"indices" if p["role"]=="consolidation" else "annotations":[]}).decode(),
                        {"mode":"scripted_acceptance","input_sha256":input_["sha256"],"model":"explicit-offline-fixture","model_calls":0,"input_tokens":None,"output_tokens":None})
                    require(result["result"]["batch"] is not None,"rejected_role_noop")
                if fault:fault("noop_materialized")
                finish=self.journal.complete("role-noop-finish-"+sha256(input_ref),started["attempt"],"completed",ZERO,[],{
                    "batch":result["result"]["batch"],"child_receipt":result,"store_binding":p["resources"].get("store_binding"),"deterministic_noeligible":True})
                if fault:fault("noop_stage")
                return finish

    def resolve_noop(self,slot):
        with self.ledger.mutation():
            ref=self.inputs.prepare(slot);p=self.inputs.resolve(ref)["payload"]
            require(p["action"]=="bound_noop","not_bound_role_noop")
            d=self.inputs._dependency(slot);art=d["artifacts"]
            with self.inputs._scope(self.inputs.nodes[slot]) as (cards,registry,store,corpus,scope):
                self._binding(p,scope);child=art.get("child_receipt")
                require(art.get("deterministic_noeligible") is True and child is not None and
                    cards.ledger.receipt(child["request_id"])==child and child["result"].get("batch")==art.get("batch"),"role_noop_receipt_drift")
                DerivedStage(cards,registry).resolve(art["batch"],p["role"],p["namespace"],p["cutoff"],p["resources"]["memory"])
                require(d["result"]["outcome"]=="completed","role_noop_finish_drift")
            return d["result"]
