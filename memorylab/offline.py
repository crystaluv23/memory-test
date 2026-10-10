"""One-command, explicitly scripted wiring acceptance. Never launches a model."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import sandbox
from .cards import CanonicalCards, EvidenceStore
from .common import exclusive_run, save, tree_hash
from .workspace import read_workspace_text
from .contracts import bounded_text, canonical, exact_object, require, sha256
from .freeze import environment_identity, implementation_files, verify as verify_bundle
from .kernel_cli import read_json
from .ledger import Ledger
from .registry import Registry
from .runtime import (CLIENT as MEMORY_CLIENT, RuntimeBuilder, RuntimeSession,
                      RuntimeSocket, file_log)
from .scenario import (CLIENT as SCENARIO_CLIENT, BACKENDS, ScenarioPolicy,
                       ScenarioSocket, materialize, verify)
from .trials import TrialJournal


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_candidates(value):
    exact_object(value,{"schema_version","category","candidates","learning_templates"},"invalid_scripted_candidates")
    require(type(value["schema_version"]) is int and value["schema_version"]==1,"invalid_scripted_candidates")
    require(value["category"]=="infrastructure_acceptance","scripted_not_gain_evidence")
    require(type(value["candidates"]) is dict and set(value["candidates"])<=set(BACKENDS.values()),"invalid_scripted_backend")
    require(type(value["learning_templates"]) is dict and set(value["learning_templates"])<=set(value["candidates"]),"invalid_learning_templates")
    for code in value["candidates"].values():bounded_text(code,100000,"invalid_scripted_code")
    return value


def execute(bundle, candidate_file, output, limit=None, fault=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with exclusive_run(output):
        return _execute(bundle,candidate_file,output,limit,fault)


def _execute(bundle, candidate_file, output, limit=None, fault=None):
    """fault is only used by acceptance tests; exceptions leave a started receipt."""
    payload=verify_bundle(bundle)
    require(payload["mode"]=="scripted_acceptance","offline_mode_required")
    require(payload["corpus"]["cards"]==[],"scripted_seeded_corpus_not_supported")
    require(environment_identity()==payload["environment"],"frozen_environment_drift")
    # The frozen bootstrap loads these exact modules after cwd changes. A current
    # installation with changed code must not silently execute an old plan.
    for name,data in implementation_files().items():
        if name.startswith("memorylab/"):
            require(hashlib.sha256(data).hexdigest()==payload["implementation"].get(name),"executor_implementation_drift")
    candidates=validate_candidates(read_json(candidate_file))
    required={j["episode"]["backend"] for j in payload["jobs"]}
    require(required<=set(candidates["candidates"]),"missing_scripted_backend")
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    identity={"plan_fingerprint":sha256(payload),"candidate_sha256":sha256(candidates),"category":"infrastructure_acceptance"}
    identity_path=output/"execution_identity.json"
    if identity_path.exists():require(read_json(identity_path)==identity,"execution_identity_drift")
    else:save(identity_path,identity)
    db_path=output/"controller.sqlite"
    if not db_path.exists():
        staging=output/".controller.pending";shutil.copyfile(Path(bundle)/"inputs.sqlite",staging);staging.rename(db_path)
    plan_id="plan-"+identity["plan_fingerprint"]
    jobs=payload["jobs"]
    executed=cache_hits=recovered=0
    started=time.monotonic()
    with Ledger(db_path) as ledger:
        registry=Registry(ledger,read_json(Path(bundle)/"configs/kernel_v0.json"))
        cards=CanonicalCards(EvidenceStore(ledger,registry.kernel))
        builder=RuntimeBuilder(cards,registry,payload["runtime"])
        journal=TrialJournal(ledger,cards,builder,payload["execution"])
        save(output/"candidates.json",candidates)
        for job in jobs:
            fingerprint=sha256({"execution":identity,"job":job})
            if journal.cached(fingerprint):cache_hits+=1;continue
            if limit is not None and executed>=limit:break
            if time.monotonic()-started>=payload["execution"]["batch_seconds"]:break
            request_id="start-"+fingerprint
            prior=ledger.receipt(request_id)
            if prior:
                journal.recover_interrupted("recover-"+fingerprint,prior["result"]["attempt"],now())
                recovered+=1;continue
            attempt=journal.begin(request_id,plan_id,job,fingerprint)["result"]["attempt"]
            directory=output/"trials"/job["id"];directory.mkdir(parents=True,exist_ok=True)
            workspace=Path(tempfile.mkdtemp(prefix="memorylab-worker-"))
            try:
                materialize(workspace,job["episode"])
                (workspace/"open_memory.py").write_text(MEMORY_CLIENT)
                (workspace/"scenario.py").write_text(SCENARIO_CLIENT)
                protected={k:v for k,v in tree_hash(workspace).items() if k!="engine.py"}
                probe=sandbox.probe(workspace,output);save(directory/"isolation.json",probe)
                before=read_workspace_text(workspace,"engine.py")
                runtime=builder.build(job["namespace"],ledger.head(),job["memory"])
                session=RuntimeSession(ledger,runtime["projection_ref"],(workspace/"SPEC.md").read_text(),persist=file_log(directory/"memory_wire.json"))
                brief_wire=session.briefs();save(directory/"briefs.json",json.loads(brief_wire) if brief_wire else None)
                policy=ScenarioPolicy(workspace,output,job["episode"],payload["scenario_contract"],persist=file_log(directory/"scenario_wire.json"))
                commands=[]
                with RuntimeSocket(workspace,session),ScenarioSocket(workspace,policy):
                    def command(args):
                        result=subprocess.run(sandbox.command(workspace,output,[sys.executable]+args,True,False),cwd=workspace,
                            capture_output=True,text=True,timeout=20)
                        commands.append({"argv":args,"exit_code":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
                        require(result.returncode==0,"scripted_tool_failure")
                        return result
                    command(["scenario.py","inspect"])
                    for brief in json.loads(brief_wire).get("items",[]) if brief_wire else []:
                        command(["open_memory.py",brief["id"]])
                    # This is an explicit scripted fixture, not a model solve.
                    (workspace/"engine.py").write_text(candidates["candidates"][job["episode"]["backend"]])
                    command(["scenario.py","check"])
                save(directory/"scripted_commands.json",commands)
                final={"summary":"Scripted infrastructure acceptance only","completion_claim":"complete","memory_uses":[]}
                grade=verify(workspace,output,job["episode"],protected,final)
                save(directory/"private_grade.json",grade)
                tree_hash(workspace)
                after=read_workspace_text(workspace,"engine.py");save(directory/"patch.json",{"before":before,"after":after})
                at=now();raw=[{"event_type":"user_input","episode":job["id"],"timestamp":at,"text":read_workspace_text(workspace,"SPEC.md")}]
                raw += [{"event_type":"tool_result","episode":job["id"],"timestamp":at,"text":canonical(command).decode()} for command in commands]
                raw.append({"event_type":"file_diff","episode":job["id"],"timestamp":at,"text":canonical({"before":before,"after":after}).decode()})
                template=candidates["learning_templates"].get(job["episode"]["backend"]) if job["episode"]["role"] in ("learning","interference") else None
                journal.complete("complete-"+fingerprint,attempt,raw,grade,{"model_calls":0,"tokens":0,"simulated_invocations":1},at,template,fault=fault)
                executed+=1
            finally:
                shutil.rmtree(workspace)
        report=journal.report(jobs,plan_id)
        report.update(executed_this_run=executed,cache_hits=cache_hits,recovered_interrupted=recovered,live_model_calls=0,
            elapsed_seconds=time.monotonic()-started,execution_identity=identity)
        save(output/"report.json",report)
        ledger.audit()
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description="Scripted V0 infrastructure acceptance; never starts Codex")
    p.add_argument("--bundle",required=True);p.add_argument("--candidates",required=True);p.add_argument("--output",required=True)
    p.add_argument("--limit",type=int)
    args=p.parse_args(argv)
    require(args.limit is None or args.limit>=0,"invalid_execution_limit")
    result=execute(args.bundle,args.candidates,args.output,args.limit)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result
