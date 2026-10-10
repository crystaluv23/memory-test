"""One reviewable, self-contained Codex qualification packet; no default call."""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .cards import CanonicalCards,EvidenceStore
from .common import exclusive_run,save,tree_hash
from .contracts import canonical,exact_object,read_kernel,require,sha256,text_hash
from .freeze import environment_identity,implementation_files
from .kernel_cli import read_json
from .ledger import Ledger
from .process import bounded_run
from .registry import Registry
from .roles import RoleInputs,capture_transport,process_status,role_config,run_canary
from .runtime import CLIENT as MEMORY_CLIENT,RuntimeBuilder,RuntimeSession,RuntimeSocket,file_log
from .scenario import CLIENT as SCENARIO_CLIENT,ScenarioPolicy,ScenarioSocket,compile_chain,materialize,read_scenario,verify as grade
from . import sandbox
from .workspace import read_workspace_text


def canary_config(path=None):
    value=read_json(path or Path(__file__).resolve().parent.parent/"configs/canary_v0.json")
    exact_object(value,{"schema_version","hypothesis","category","live_execution_authorized","formal_admission","model_sessions",
        "seed","episode_index","memory","budget","automatic_retry","provider_hard_token_limit","semantic_gain_claim"},"invalid_canary_config")
    require(value["schema_version"]=="role-canary-v0" and value["category"]=="qualification_canary" and
        value["model_sessions"]==1 and value["memory"]=="empty_baseline" and value["episode_index"]==0 and
        value["live_execution_authorized"] is False and value["formal_admission"] is False and
        value["automatic_retry"] is False and value["provider_hard_token_limit"] is False and value["semantic_gain_claim"] is False,"unsupported_canary_policy")
    require(type(value["seed"]) is int and value["seed"]>=0,"invalid_canary_seed")
    exact_object(value["budget"],{"input_tokens","output_tokens","seconds","commands"},"invalid_canary_budget")
    require(all(type(v) is int and v>0 for v in value["budget"].values()),"invalid_canary_budget")
    return value


def bundle_files(root):
    root=Path(root)
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()}


def prepare(output,cli,model,effort="high"):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    cli=Path(cli).resolve();require(cli.is_file(),"missing_canary_cli")
    require(type(model) is str and bool(model.strip()),"missing_canary_model")
    version=bounded_run([str(cli),"--version"],output,b"",15,4096,4096)
    require(version["error"] is None and version["exit_code"]==0 and version["stdout"].startswith("codex-cli "),"invalid_canary_cli_version")
    c=role_config();c.update(requested_model=model,cli_path=str(cli),cli_version=version["stdout"].strip(),
        cli_sha256=hashlib.sha256(cli.read_bytes()).hexdigest(),reasoning_effort=effort)
    profile=canary_config();staging=Path(tempfile.mkdtemp(prefix=".role-prepare-",dir=output))
    try:
        with Ledger(staging/"inputs.sqlite") as ledger:
            registry=Registry(ledger,read_kernel());cards=CanonicalCards(EvidenceStore(ledger))
            memory=registry.register_root("memory","probeBaseline");scenario=registry.register_root("scenario","probeScenario")
            ep=compile_chain(registry,scenario,profile["seed"])["episodes"][0]
            workspace=staging/"task_template";materialize(workspace,ep)
            (workspace/"open_memory.py").write_text(MEMORY_CLIENT);(workspace/"scenario.py").write_text(SCENARIO_CLIENT)
            built=RuntimeBuilder(cards,registry).build("task/probe",ledger.head(),memory)
            runtime=RuntimeSession(ledger,built["projection_ref"],(workspace/"SPEC.md").read_text())
            briefs=json.loads(runtime.briefs());cutoff=ledger.head()
            packet=RoleInputs(cards,registry,c).task("solver","task/probe",cutoff,workspace,ep,briefs)
            (workspace/"role.schema.json").write_bytes(canonical(packet["payload"]["output_schema"])+b"\n")
            plan={"schema_version":"frozen-role-canary-v0","category":"qualification_canary","profile":profile,
                "role_config":c,"packet":packet,"episode":ep,"memory":memory,"projection_ref":built["projection_ref"],
                "cutoff":cutoff,"brief_wire_sha256":runtime.events[0]["wire_sha256"],"environment":environment_identity(),
                "scenario_contract":read_scenario(),"kernel_frozen":False,"gain_evidence":False,"planned_model_sessions":1,
                "new_model_calls":0,"requires_explicit_user_authorization":True}
            ledger.audit()
        for name,data in implementation_files().items():
            file=staging/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(data)
        (staging/"bootstrap.py").write_text("from pathlib import Path\nimport sys\nsys.path.insert(0,str(Path(__file__).resolve().parent))\nfrom memorylab.role_canary import main\nmain()\n")
        (staging/"plan.json").write_bytes(canonical(plan)+b"\n")
        fingerprint=sha256({"plan":plan,"implementation":bundle_files(staging)})
        destination=output/fingerprint
        if destination.exists():
            verify(destination);return {"bundle":str(destination),"fingerprint":fingerprint,"packet_sha256":packet["sha256"],"cache_hit":True,"new_model_calls":0}
        manifest={"schema_version":1,"fingerprint":fingerprint,"plan_sha256":sha256(plan),"files":bundle_files(staging)}
        (staging/"manifest.json").write_bytes(canonical(manifest)+b"\n")
        for file in staging.rglob("*"):
            if file.is_file():
                with file.open("rb") as stream:os.fsync(stream.fileno())
        for directory in sorted((p for p in staging.rglob("*") if p.is_dir()),key=lambda p:len(p.parts),reverse=True)+[staging]:
            fd=os.open(directory,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        os.rename(staging,destination)
        fd=os.open(output,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
        verify(destination)
        return {"bundle":str(destination),"fingerprint":fingerprint,"packet_sha256":packet["sha256"],"cache_hit":False,"new_model_calls":0}
    finally:
        if staging.exists():shutil.rmtree(staging)


def verify(bundle):
    bundle=Path(bundle).resolve();manifest=read_json(bundle/"manifest.json")
    exact_object(manifest,{"schema_version","fingerprint","plan_sha256","files"},"invalid_canary_manifest")
    require(not any(p.is_symlink() for p in bundle.rglob("*")),"canary_bundle_symlink")
    current=bundle_files(bundle);current.pop("manifest.json",None)
    require(current==manifest["files"],"canary_bundle_drift")
    plan=read_json(bundle/"plan.json")
    require(sha256(plan)==manifest["plan_sha256"] and sha256({"plan":plan,"implementation":current})==manifest["fingerprint"],"canary_plan_drift")
    require(plan["category"]=="qualification_canary" and plan["kernel_frozen"] is False and plan["gain_evidence"] is False,"canary_cannot_be_formal")
    return plan


def execute(bundle,output,approved_packet_sha256=None,authorized_by_user=False,scripted_code=None):
    bundle=Path(bundle).resolve();plan=verify(bundle)
    require(environment_identity()==plan["environment"],"canary_environment_drift")
    require(implementation_files()=={name:(bundle/name).read_bytes() for name in implementation_files()},"canary_implementation_drift_use_frozen_bootstrap")
    if scripted_code is None:
        require(authorized_by_user is True and approved_packet_sha256==plan["packet"]["sha256"],"role_canary_not_authorized")
    else:require(type(scripted_code) is str and bool(scripted_code),"invalid_canary_fixture")
    output=Path(output).resolve()
    require(bundle not in output.parents and output!=bundle,"canary_results_inside_bundle")
    output.mkdir(parents=True,exist_ok=True)
    with exclusive_run(output):
        identity={"bundle_sha256":read_json(bundle/"manifest.json")["fingerprint"],"packet_sha256":plan["packet"]["sha256"],
            "mode":"scripted_acceptance" if scripted_code is not None else "qualification_canary",
            "fixture_sha256":text_hash(scripted_code) if scripted_code is not None else None}
        ident=output/"identity.json"
        if ident.exists():require(read_json(ident)==identity,"canary_attempt_identity_drift")
        else:save(ident,identity)
        if (output/"report.json").exists():return {**read_json(output/"report.json"),"cache_hit":True,"new_model_calls":0}
        if (output/"transport/start.json").exists():
            status=process_status(output/"transport")
            if status["status"]!="exited":
                return {"identity":identity,"status":"started_requires_exit_audit","planned":1,"terminal":0,
                    "process_status":status,"successful":0,"cost_unknown":scripted_code is None,"new_model_calls":0,
                    "category":"infrastructure_acceptance" if scripted_code is not None else "qualification_canary",
                    "formal_admission":False,"gain_evidence":False,"kernel_frozen":False}
            result={"identity":identity,"status":"interrupted","planned":1,"terminal":1,"successful":0,"usage":None,
                "process_status":status,"model_sessions_reserved":0 if scripted_code is not None else 1,
                "cost_unknown":scripted_code is None,"category":"infrastructure_acceptance" if scripted_code is not None else "qualification_canary",
                "kernel_frozen":False,"gain_evidence":False,"formal_admission":False,"promotion_eligible":False,"new_model_calls":0}
            save(output/"report.json",result);return result
        worker=Path(tempfile.mkdtemp(prefix="memory-role-",dir="/tmp"))
        try:
            shutil.copytree(bundle/"task_template",worker,dirs_exist_ok=True)
            db=output/"controller.sqlite";shutil.copyfile(bundle/"inputs.sqlite",db)
            with Ledger(db) as ledger:
                runtime=RuntimeSession(ledger,plan["projection_ref"],(worker/"SPEC.md").read_text(),persist=file_log(output/"memory_wire.json"))
                brief=runtime.briefs();require(hashlib.sha256(brief).hexdigest()==plan["brief_wire_sha256"],"canary_runtime_drift")
                policy=ScenarioPolicy(worker,output,plan["episode"],plan["scenario_contract"],persist=file_log(output/"scenario_wire.json"))
                protected={k:v for k,v in tree_hash(worker).items() if k!="engine.py"};before=(worker/"engine.py").read_text()
                with RuntimeSocket(worker,runtime),ScenarioSocket(worker,policy):
                    if scripted_code is None:
                        transport=run_canary(plan["role_config"],worker,output,plan["packet"],plan["profile"]["budget"],
                            output/"transport",approved_packet_sha256,authorized_by_user)
                    else:
                        transport=capture_transport(sandbox.command(worker,output,[sys.executable,"-I","-B","-c",scripted_code],True,False),
                            worker,plan["packet"],plan["profile"]["budget"],output/"transport",plan["role_config"],
                            invocation_identity={"category":"scripted_transport_fixture","model_session_invocations":0})
                independent=grade(worker,output,plan["episode"],protected,transport["final"])
                save(output/"private_grade.json",independent)
                try:
                    # Recheck the entire tree after production; engine.py is
                    # not the only artifact a candidate can substitute.
                    tree_hash(worker);after=read_workspace_text(worker,"engine.py");boundary_error=None
                except (ValueError,OSError,UnicodeError) as exc:
                    after=None;boundary_error=getattr(exc,"code",type(exc).__name__)
                save(output/"patch.json",{"before":before,"after":after,"boundary_error":boundary_error})
                success=transport["transport_success"] and independent["full_success"] and boundary_error is None
                result={"identity":identity,"status":"completed","planned":1,"terminal":1,"successful":int(success),
                    "transport_success":transport["transport_success"],"independent_behavior_success":independent["behavior_success"],
                    "independent_full_success":independent["full_success"],"usage":transport["usage"],"cost_unknown":transport["usage"] is None,
                    "provider_budget_qualified":False,"resolved_model":None,"category":"infrastructure_acceptance" if scripted_code is not None else "qualification_canary",
                    "live_model_calls":0 if scripted_code is not None else 1,"simulated_usage":scripted_code is not None,
                    "kernel_frozen":False,"gain_evidence":False,"formal_admission":False,"promotion_eligible":False,
                    "workspace_boundary_error":boundary_error,
                    "cache_hit":False,"new_model_calls":0 if scripted_code is not None else 1}
                save(output/"report.json",result);return result
        finally:shutil.rmtree(worker)


def main(argv=None):
    p=argparse.ArgumentParser(description="Prepare or explicitly authorize one diagnostic Codex session; not a formal comparison")
    s=p.add_subparsers(dest="action",required=True)
    a=s.add_parser("prepare");a.add_argument("--output",required=True);a.add_argument("--cli",required=True);a.add_argument("--model",required=True);a.add_argument("--effort",default="high")
    a=s.add_parser("verify");a.add_argument("--bundle",required=True)
    a=s.add_parser("execute");a.add_argument("--bundle",required=True);a.add_argument("--output",required=True);a.add_argument("--approve-model-calls-for",required=True)
    args=p.parse_args(argv)
    if args.action=="prepare":result=prepare(args.output,args.cli,args.model,args.effort)
    elif args.action=="verify":result={"status":"verified","plan":verify(args.bundle),"new_model_calls":0}
    else:result=execute(args.bundle,args.output,args.approve_model_calls_for,authorized_by_user=True)
    print(json.dumps(result,ensure_ascii=False,indent=2));return result


if __name__=="__main__":main()
