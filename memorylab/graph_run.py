"""Fresh snapshot-only controller rounds and an explicit fixture topology driver."""
import argparse
import copy
import hashlib
import os
import tempfile
import time
from pathlib import Path

from .cards import CanonicalCards,EvidenceStore
from .common import exclusive_run,save
from .contracts import ContractError,bounded_text,canonical,exact_object,require,sha256,timestamp
from .control_stage import ControlStages
from .freeze import execution_config,implementation_files,validate_execution
from .kernel_cli import read_json
from .ledger import Ledger
from .registry import Registry
from .rpc_output import native_program,output_config
from .role_stage import RoleStages
from .roles import role_config,validate_role
from .scenario import read_scenario,validate_scenario
from .scheduling import ROLES,StageJournal,compile_schedule,schedule_config,validate_schedule
from .stage_environment import stage_environment_identity
from .stage_inputs import StageInputs
from .stores import CorpusStore,portable
from .task_stage import TaskStages


def run_config():
    c=read_json(Path(__file__).resolve().parent.parent/"configs/graph_run_v0.json")
    exact_object(c,{"schema_version","hypothesis","mode","max_program_bytes","max_total_program_bytes","initialization_policy",
        "program_policy","source_boundary","existing_store_policy","batch_policy","recovery_policy","formal_admission","model_calls"},"invalid_graph_run_config")
    expected={"schema_version":"graph-run-v0","mode":"explicit_diagnostic_transports",
        "initialization_policy":"all_snapshots_specs_graph_and_programs_before_nonoverwrite_database_publish",
        "program_policy":"exact_nondisabled_actor_slot_map_frozen_before_execution",
        "source_boundary":"origin_controller_parent_and_round_root","existing_store_policy":"exact_recipe_resume_no_reset",
        "batch_policy":"frozen_batches_and_shared_journal_round_guard","recovery_policy":"revalidate_publications_no_automatic_retry"}
    require(all(c[k]==v for k,v in expected.items()) and c["formal_admission"] is False and
        type(c["model_calls"]) is int and c["model_calls"]==0,"unsupported_graph_run_policy")
    for k in ("max_program_bytes","max_total_program_bytes"):
        require(type(c[k]) is int and c[k]>0,"invalid_graph_run_limit")
    return c


def implementation_identity():
    return sha256({name:hashlib.sha256(data).hexdigest() for name,data in implementation_files().items()})


def _options(value):
    exact_object(value,{"mode","memory_refs","scenario_refs","seeds","meta_memory_ref","shadow_memory_ref",
        "schedule","execution","role","scenario"},"invalid_graph_run_options")
    c=copy.deepcopy(value)
    c["schedule"]=validate_schedule(c["schedule"]) if c["schedule"] is not None else schedule_config()
    c["execution"]=validate_execution(c["execution"]) if c["execution"] is not None else execution_config()
    c["role"]=validate_role(c["role"]) if c["role"] is not None else role_config()
    c["scenario"]=validate_scenario(c["scenario"]) if c["scenario"] is not None else read_scenario()
    require(c["execution"]["mode"]=="offline_acceptance" and c["execution"]["live_execution_authorized"] is False,
        "live_graph_run_not_accepted")
    return c


def _compile(registry,store,initial,research,options):
    require(type(research) is dict,"graph_run_research_snapshots")
    c=store.resolve(initial)["payload"]["corpus"]
    require(c["namespace"].startswith("task/"),"graph_run_initial_namespace")
    require(set(research)==({"evaluator","proposer"} if options["meta_memory_ref"] else set()),"graph_run_research_snapshots")
    hashes={}
    for role,ref in research.items():
        value=store.resolve(ref)["payload"]["corpus"]
        require(value["namespace"]=="research/"+role,"graph_run_research_namespace")
        hashes[role]=value["sha256"]
    return compile_schedule(registry,options["memory_refs"],options["scenario_refs"],options["seeds"],c["sha256"],
        options["mode"],options["schedule"],options["execution"],options["scenario"],options["meta_memory_ref"],
        options["shadow_memory_ref"],hashes if research else None)


def _programs(value,graph,config):
    expected={n["id"] for n in graph["slots"] if n["role"] in ROLES and n["condition"]!="disabled"}
    require(type(value) is dict and set(value)==expected,"graph_run_program_slots_mismatch")
    total=0
    nodes={n['id']:n for n in graph['slots']}
    for slot,code in value.items():
        if type(code) is str:
            bounded_text(code,config['max_program_bytes'],'graph_run_program_overflow');size=len(code.encode())
        else:
            require(type(code) is dict and len(canonical(code))<=config['max_program_bytes'],'graph_run_program_overflow')
            require(nodes[slot]['role'] in output_config()['roles']+['shadow'],'native_role_program_role_denied')
            native_program(code);size=len(canonical(code))
        total+=size
    require(total<=config["max_total_program_bytes"],"graph_run_programs_overflow")
    return copy.deepcopy(value)


def initialize(cards,registry,initial,research,options,programs,output,fault=None):
    """No whole-DB backup, source mutation, actor start or model call."""
    require(registry.ledger is cards.ledger,"graph_run_origin_mismatch")
    store=CorpusStore(cards);options=_options(options);config=run_config();research=copy.deepcopy(research)
    graph=_compile(registry,store,initial,research,options)
    require(graph["budget_preview"]["fits_round_guard"],"graph_run_prospective_budget_exceeded")
    programs=_programs(programs,graph,config)
    root=Path(output);require(not root.is_symlink(),"invalid_graph_run_root");root=root.resolve()
    require(root!=cards.ledger.path.parent,"graph_run_origin_root_alias")
    recipe={"source_store":str(cards.ledger.path),"output":str(root),"snapshots":{"task":initial,**research},
        "options":options,"programs":programs,"config":config,"kernel_sha256":registry.fingerprint,
        "implementation_sha256":implementation_identity(),"environment":stage_environment_identity()}
    root.mkdir(parents=True,exist_ok=True)
    destination=root/"ledger.sqlite"
    with exclusive_run(root):
        if destination.exists():
            with GraphRun(root) as run:
                require(run.value["recipe"]==recipe,"graph_run_recipe_drift")
                return {"status":"graph_run_ready","run":str(root),"plan":run.inputs.plan,"cache_hit":True,"actual_model_calls":0}
        require(all(p.name==".executor.lock" or p.name=="scopes" and p.is_dir() and not any(p.iterdir())
            for p in root.iterdir()),"graph_run_root_not_empty")
        spec_refs=[]
        for r in options["memory_refs"]+options["scenario_refs"]+[options["meta_memory_ref"],options["shadow_memory_ref"]]:
            if r is not None and r not in spec_refs:spec_refs.append(r)
        with tempfile.TemporaryDirectory(prefix=".round-pending-",dir=root) as temporary:
            staging=Path(temporary)/"ledger.sqlite"
            fork=store.fork("round-task-"+sha256(recipe),initial,staging,"task/checkpoint",registry,spec_refs,"initial_checkpoint")
            if fault:fault("task_import")
            with Ledger(staging) as ledger:
                copied_cards=CanonicalCards(EvidenceStore(ledger,cards.kernel));copied_registry=Registry(ledger,cards.kernel)
                copied_store=CorpusStore(copied_cards);imports={"task":fork["import"]};snapshots={}
                with ledger.mutation():
                    for role,ref in research.items():
                        imports[role]=copied_store.import_snapshot("round-research-"+sha256({"recipe":recipe,"role":role}),
                            store,ref,"research/"+role,"initial_checkpoint")
                        if fault:fault("research_"+role)
                    for label,receipt in imports.items():
                        ns="task/checkpoint" if label=="task" else "research/"+label
                        snapshots[label]=copied_store.capture("round-corpus-"+sha256({"recipe":recipe,"label":label}),ns,receipt["result"]["cutoff"])
                    graph=_compile(copied_registry,copied_store,snapshots["task"],{k:v for k,v in snapshots.items() if k!="task"},options)
                    journal=StageJournal(ledger,graph)
                    inputs=StageInputs(copied_cards,copied_registry,journal,snapshots["task"],root/"scopes",
                        {k:v for k,v in snapshots.items() if k!="task"},options["role"],options["scenario"],
                        denied_roots=(cards.ledger.path.parent,root))
                    driver=ledger.put({"kind":"graph_driver_binding","id":"driver-"+inputs.plan,"version":1,
                        "binding":inputs.binding_ref,"programs":programs,"config":config,"output":str(root)},"control")
                    value={"kind":"graph_run","id":"round","version":1,"recipe":recipe,"graph":graph,"initial":snapshots["task"],
                        "research":{k:v for k,v in snapshots.items() if k!="task"},"imports":imports,"spec_refs":spec_refs,
                        "binding":inputs.binding_ref,"driver":driver,"formal_admission":False}
                    ref=ledger.put(value,"control")
                    if fault:fault("bindings")
                    ledger.commit_receipt("graph-run-ready",{"recipe":recipe},{"status":"ready","run":ref,"actual_model_calls":0},"control")
                    if fault:fault("ready")
                ledger.audit();ledger.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            with staging.open("rb") as stream:os.fsync(stream.fileno())
            if fault:fault("before_publish")
            os.link(staging,destination)
            fd=os.open(root,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
            if fault:fault("published")
        return {"status":"graph_run_ready","run":str(root),"plan":graph["sha256"],"cache_hit":False,"actual_model_calls":0}


class GraphRun:
    def __init__(self,output):
        root=Path(output);require(not root.is_symlink(),"invalid_graph_run_root");self.root=root.resolve()
        path=self.root/"ledger.sqlite"
        require(path.is_file() and not path.is_symlink() and path.stat().st_nlink==1,"unpublished_graph_run")
        self.ledger=Ledger(path)
        try:
            values=self.ledger.objects("graph_run","control");require(len(values)==1,"missing_graph_run_descriptor")
            self.ref,self.value=values[0];ref=self.ref;recipe=self.value["recipe"]
            require(recipe["output"]==str(self.root) and recipe["config"]==run_config(),"graph_run_recipe_drift")
            require(recipe["environment"]==stage_environment_identity(),"stage_environment_drift")
            require(recipe["implementation_sha256"]==implementation_identity(),"stage_implementation_drift")
            ready=self.ledger.receipt("graph-run-ready",{"recipe":recipe})
            require(ready is not None and ready["result"].get("run")==ref,"unpublished_graph_run")
            self.cards=CanonicalCards(EvidenceStore(self.ledger));self.registry=Registry(self.ledger,self.cards.kernel)
            require(self.registry.fingerprint==recipe["kernel_sha256"],"graph_run_kernel_drift")
            self.store=CorpusStore(self.cards)
            self._imports()
            options=recipe["options"]
            self.journal=StageJournal(self.ledger,self.value["graph"])
            self.inputs=StageInputs(self.cards,self.registry,self.journal,self.value["initial"],self.root/"scopes",self.value["research"],
                options["role"],options["scenario"],denied_roots=(Path(recipe["source_store"]).parent,self.root))
            require(self.inputs.binding_ref==self.value["binding"],"graph_run_input_binding_drift")
            self.driver=self.ledger.get(self.value["driver"])
            require(self.driver=={"kind":"graph_driver_binding","id":"driver-"+self.inputs.plan,"version":1,
                "binding":self.inputs.binding_ref,"programs":recipe["programs"],"config":recipe["config"],"output":str(self.root)},"graph_driver_binding_drift")
            _programs(self.driver["programs"],self.inputs.graph,self.driver["config"])
            self.tasks=TaskStages(self.inputs,self.root/"task-output")
            self.actors=RoleStages(self.inputs,self.root/"role-output")
            self.controls=ControlStages(self.inputs,self.tasks,self.actors,self.root/"control-output")
        except BaseException:
            self.ledger.close();raise

    def _imports(self):
        for label,receipt in self.value["imports"].items():
            require(self.ledger.receipt(receipt["request_id"])==receipt,"graph_run_import_receipt_drift")
            result=receipt["result"];archive=self.ledger.get(result["origin"]);original=archive["snapshot"]
            old_ref=self.value["recipe"]["snapshots"][label]
            require(sha256(original)==old_ref["sha256"] and original["portable_sha256"]==result["portable_sha256"],"graph_run_origin_snapshot_drift")
            ns="task/checkpoint" if label=="task" else "research/"+label
            payload=self.store._payload(ns,result["cutoff"])
            require(portable(payload)==result["portable_sha256"],"graph_run_initial_corpus_drift")
            ref=self.value["initial"] if label=="task" else self.value["research"][label]
            captured=self.store.resolve(ref)["payload"]
            require(captured==payload,"graph_run_initial_snapshot_drift")
        require(set(self.value["imports"])=={"task",*self.value["research"]},"graph_run_import_roles_drift")
        for r in self.value["spec_refs"]:self.registry.resolve(r)

    def close(self):self.ledger.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    def _preflight(self):
        self.inputs.preflight()
        require(self.ledger.get(self.ref)==self.value and self.ledger.get(self.value["driver"])==self.driver and
            self.driver["programs"]==self.value["recipe"]["programs"] and str(self.root)==self.value["recipe"]["output"],
            "graph_driver_binding_drift")

    def _verify_finished(self,node):
        with self.ledger.mutation(),self.ledger.cached_reads(self.inputs.config["ledger_decode_cache_bytes"]):
            self.inputs.preflight();self.inputs.refresh();d=self.inputs._dependency(node["id"])
            if d["result"]["outcome"] in ("disabled","blocked"):return
            role=node["role"];slot=node["id"]
            if role in self.controls.roles:
                self.tasks.resolve_grade(slot) if role=="grade" else self.controls.resolve(slot)
            elif role in ("solver","acquisition","oracle"):self.tasks.resolve_task(slot)
            else:
                a=self.ledger.get(d["result"]["attempt"]);p=self.inputs.resolve(a["bound_input"])["payload"]
                self.actors.resolve_noop(slot) if p["action"]=="bound_noop" else self.actors.resolve(slot)

    def _report(self,status=None,attention=None):
        with self.ledger.mutation(),self.ledger.cached_reads(self.inputs.config["ledger_decode_cache_bytes"]),self.inputs.validation_scope():
            self._preflight();finished=self.journal._finished()
            for node in self.inputs.graph["slots"]:
                if node["id"] in finished:self._verify_finished(node)
            stages=self.journal.report();tasks=self.controls.task_report()
            return {"status":status or ("terminal" if stages["terminal"]==stages["planned"] else "partial"),
                "plan":self.inputs.plan,"stage_report":stages,"task_report":tasks,"attention":attention,
                "category":"infrastructure_acceptance","actual_model_calls":0,"formal_admission":False,
                "gain_evidence":False,"promotion_eligible":False,"kernel_frozen":False,"independent_sample":False}

    def report(self):
        with exclusive_run(self.root):return self._report()

    def run(self,at,limit=None,fault=None):
        timestamp(at);require(limit is None or type(limit) is int and limit>0,"invalid_graph_run_limit")
        with exclusive_run(self.root):
            with self.ledger.mutation(),self.ledger.cached_reads(self.inputs.config["ledger_decode_cache_bytes"]),self.inputs.validation_scope():
                self._preflight();require(self.inputs.graph["budget_preview"]["fits_round_guard"],"graph_run_prospective_budget_exceeded")
                finished=self.journal._finished()
                for node in self.inputs.graph["slots"]:
                    if node["id"] in finished:self._verify_finished(node)
            pending=[n for n in self.inputs.graph["slots"] if n["id"] not in finished]
            if not pending:return self._report()
            batch=next(b for b in self.inputs.graph["budget_preview"]["batches"] if pending[0]["id"] in b["slots"])
            started=time.monotonic();handled=0;attention=None
            for node in pending:
                self._preflight()
                slot=node["id"]
                if slot not in batch["slots"] or limit is not None and handled>=limit or time.monotonic()-started>=self.inputs.graph["execution"]["batch_seconds"]:break
                finished=self.journal._finished()
                if any(d not in finished for d in node["depends_on"]):attention={"slot":slot,"code":"stage_dependencies_unavailable"};break
                try:
                    if node["condition"]=="disabled" or any(not self.journal._accepts_dependency(node,finished[d]) for d in node["depends_on"]):
                        self.journal.close_unrun("driver-close-"+sha256({"binding":self.value["driver"],"slot":slot}),slot)
                    elif node["role"] in self.controls.roles:self.controls.run(slot,at,fault)
                    elif node["role"] in ("solver","acquisition","oracle"):self.tasks.run_fixture(slot,self.driver["programs"][slot],at,fault)
                    else:
                        program=self.driver['programs'][slot]
                        if type(program) is dict:self.actors.run_native(slot,program,at,fault)
                        else:self.actors.run_fixture(slot,program,at,fault)
                except ContractError as exc:
                    self.inputs.preflight();attention={"slot":slot,"code":exc.code};break
                handled+=1
            result=self._report("needs_attention" if attention else None,attention)
            save(self.root/"report.json",result)
            return result


def main(argv=None):
    parser=argparse.ArgumentParser(description="Snapshot-only graph rounds, explicit Python fixtures, zero model calls")
    sub=parser.add_subparsers(dest="action",required=True)
    prepare=sub.add_parser("prepare");prepare.add_argument("--request",required=True);prepare.add_argument("--output",required=True)
    for name in ("run","report"):
        p=sub.add_parser(name);p.add_argument("--run",required=True)
        if name=="run":p.add_argument("--timestamp",required=True);p.add_argument("--limit",type=int)
    args=parser.parse_args(argv)
    if args.action=="prepare":
        request=read_json(args.request)
        exact_object(request,{"source_db","initial_snapshot","research_snapshots","options","programs"},"invalid_graph_run_request")
        source=Path(request["source_db"])
        require(source.is_file() and not source.is_symlink(),"invalid_graph_run_source")
        with Ledger(source) as ledger:
            cards=CanonicalCards(EvidenceStore(ledger));registry=Registry(ledger,cards.kernel)
            result=initialize(cards,registry,request["initial_snapshot"],request["research_snapshots"],request["options"],request["programs"],args.output)
    else:
        with GraphRun(args.run) as run:result=run.run(args.timestamp,args.limit) if args.action=="run" else run.report()
    print(canonical(result).decode());return result
