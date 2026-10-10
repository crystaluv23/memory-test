"""Measure declared offline controller scale; never claim whole F2 or gain."""
import argparse
import hashlib
import math
import pathlib
import platform
import sys
import tempfile
import time
from datetime import datetime,timezone

ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from memorylab.cards import CanonicalCards,EvidenceStore
from memorylab.common import save
from memorylab.contracts import read_kernel,require,sha256
from memorylab.freeze import environment_identity,execution_config,implementation_files,prepare,verify
from memorylab.kernel_cli import read_json
from memorylab.ledger import Ledger
from memorylab.registry import Registry
from memorylab.runtime import RuntimeBuilder
from memorylab.trials import TrialJournal

created_output=None

def main():
    global created_output
    parser=argparse.ArgumentParser();parser.add_argument("--output",required=True);args=parser.parse_args()
    output=pathlib.Path(args.output).resolve();require(not output.exists(),"friction_result_already_exists")
    config=read_json(ROOT/"configs/friction_probe.json")
    require(config["population"]==8 and config["canonical_cards"]==24 and config["repeats"]==10 and
        config["terminal_trial_policy"]=="missing_no_execution" and config["live_model_calls"]==0 and
        config["acceptance_gate_complete"] is False,"unsupported_friction_probe")
    output.mkdir(parents=True)
    created_output=output
    at=datetime.now(timezone.utc).isoformat()
    orchestration_paths=[pathlib.Path(__file__).resolve(),ROOT/"configs/friction_probe.json"]
    orchestration_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in orchestration_paths}
    identity=sha256({k:hashlib.sha256(v).hexdigest() for k,v in implementation_files().items()})
    with tempfile.TemporaryDirectory(prefix="memorylab-friction-") as tmp,Ledger(output/"fixture.sqlite") as ledger:
        registry=Registry(ledger,read_kernel());evidence=EvidenceStore(ledger);cards=CanonicalCards(evidence)
        source=evidence.register("benchmarkSource","task/benchmark","explicit_hypothesis","fixture",at,
            "Synthetic metadata for controller latency only; no actual task was executed and no behavior conclusion follows.")
        for batch in range(8):
            raw=[]
            for index in range(3):
                n=batch*3+index
                raw.append({"id":"fixture-card-"+str(n),"expected_version":0,"type_hint":"environment/services",
                    "brief":"Controller latency fixture "+str(n),"detail":"Fixture only. No learned rule, real outcome or mechanism gain is asserted.",
                    "evidence_refs":[source],"outcome":"unknown","status":"active","supersedes":[]})
            cards.extract("benchmarkExtract"+str(batch),{"cards":raw},"task/benchmark",ledger.head(),at)
        memories=[]
        for i in range(8):
            m=dict(registry.kernel["memory_initial"],**{"organizer.type":"flat" if i%2==0 else "fixed_tree",
                "consolidation.type":"none" if i//2%2==0 else "single_index","credit.type":"none" if i//4==0 else "outcome_ema"})
            memories.append(registry.register_root("memory","fixture-m"+str(i),m))
        scenario=registry.register_root("scenario","fixture-focus")
        first=prepare(registry,cards,memories,scenario,"task/benchmark",config["seeds"],pathlib.Path(tmp)/"seed-bundle")
        jobs=verify(first["path"])["jobs"]
        journal=TrialJournal(ledger,cards,RuntimeBuilder(cards,registry),execution_config())
        for job in jobs:
            fingerprint=sha256({"job":job,"benchmark":"missing_no_execution"})
            attempt=journal.begin("start-"+job["id"],"fixture-plan",job,fingerprint)["result"]["attempt"]
            journal.complete("complete-"+job["id"],attempt,[],{"full_success":False,"behavior_success":False,"status":"missing"},
                {"model_calls":0,"tokens":0,"simulated_invocations":0},at)
        timings=[]
        for repeat in range(config["repeats"]):
            started=time.perf_counter()
            frozen=prepare(registry,cards,memories,scenario,"task/benchmark",config["seeds"],pathlib.Path(tmp)/("fresh-"+str(repeat)))
            prepare_seconds=time.perf_counter()-started
            require(frozen["cache_hit"] is False,"friction_prepare_used_cache")
            started=time.perf_counter();report=journal.report(jobs,"fixture-plan");report_seconds=time.perf_counter()-started
            require(report["planned"]==len(jobs) and report["successful"]==0,"friction_denominator_changed")
            timings.append({"repeat":repeat,"prepare_seconds":prepare_seconds,"report_seconds":report_seconds,
                "cache_hit":frozen["cache_hit"],"planned":report["planned"],"completed_missing":report["completed"],"fingerprint":frozen["fingerprint"]})
        p95=lambda key:sorted(row[key] for row in timings)[math.ceil(.95*len(timings))-1]
        result={"schema_version":1,"timestamp":at,"category":"infrastructure_acceptance","config":config,"config_sha256":sha256(config),
            "implementation_identity":identity,"orchestration_hashes":orchestration_hashes,"environment":environment_identity(),"machine":platform.platform(),"timings":timings,
            "prepare_p95_seconds":p95("prepare_seconds"),"report_p95_seconds":p95("report_seconds"),
            "offline_scale_within_target":p95("prepare_seconds")<=config["prepare_p95_seconds"] and p95("report_seconds")<=config["report_p95_seconds"],
            "acceptance_gate_complete":False,"kernel_frozen":False,"live_model_calls":0,"actual_task_executions":0,"memory_gain_evidence":False}
        require({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in orchestration_paths}==orchestration_hashes,
            "friction_orchestration_changed")
        require(sha256({k:hashlib.sha256(v).hexdigest() for k,v in implementation_files().items()})==identity,"friction_implementation_changed")
        ledger.audit();save(output/"timings.json",result)
        print({"path":str(output),"planned":len(jobs),"prepare_p95_seconds":result["prepare_p95_seconds"],"report_p95_seconds":result["report_p95_seconds"],"offline_scale_within_target":result["offline_scale_within_target"],"acceptance_gate_complete":False})


if __name__=="__main__":
    try:main()
    except Exception as exc:
        parser=argparse.ArgumentParser();parser.add_argument("--output",required=True)
        args=parser.parse_args()
        output=pathlib.Path(args.output).resolve()
        if created_output==output and output.is_dir():save(output/"failure.json",{"category":"infrastructure_acceptance","status":"failed",
            "reason":getattr(exc,"code",type(exc).__name__),"live_model_calls":0,"acceptance_gate_complete":False})
        raise
