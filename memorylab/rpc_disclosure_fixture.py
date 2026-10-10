"""Persisted controller-only dynamic-tool fixture and read-only sealed verifier."""
import argparse
import json
from pathlib import Path

from .cards import CanonicalCards,EvidenceStore
from .common import save
from .contracts import canonical,read_kernel,require
from .kernel_cli import parse_json
from .ledger import Ledger
from .registry import Registry
from .roles import RoleInputs
from .rpc_disclosure import RPCDisclosure,SealedDisclosure
from .rpc_role import run_fixture,sealed_bytes,sealed_json,validate_sealed
from .runtime import RuntimeBuilder,RuntimeSession

ROLES=("extractor","consolidation","reflector","solver","evaluator","proposer")


def build_fixture(role,output):
    require(role in ROLES,"rpc_disclosure_role_denied")
    output=Path(output);require(not output.exists(),"rpc_disclosure_output_exists");output.mkdir(parents=True)
    ns="research/"+role if role in ("evaluator","proposer") else "task/chain"
    at="2026-10-08T10:00:00+08:00"
    with Ledger(output/"controller.sqlite") as ledger:
        kernel=read_kernel();registry=Registry(ledger,kernel);evidence=EvidenceStore(ledger,kernel);cards=CanonicalCards(evidence)
        source=evidence.register("fixture-source",ns,"explicit_feedback","fixture-learning",at,
            "Only integrations using this service need its health check; other failures require diagnosis.","observable")
        inputs=RoleInputs(cards,registry)
        if role=="extractor":packet,service=inputs.extractor(ns,ledger.head(),[source])
        else:
            for id_ in ("fixture-a","fixture-b") if role in ("consolidation","reflector") else ("fixture-a",):
                raw={"id":id_,"expected_version":0,"type_hint":"environment/services",
                    "brief":"Check service health for integrations that depend on it.",
                    "detail":"For integrations that depend on this service, check its health before retrying. Diagnose other failures separately.",
                    "evidence_refs":[source],"outcome":"failure","status":"active","supersedes":[]}
                cards.extract("extract-"+id_,{"cards":[raw]},ns,ledger.head(),at)
            config=dict(kernel["memory_initial"])
            if role in ("consolidation","reflector"):config.update({"consolidation.type":"single_index","reflection.trigger":"feedback"})
            memory=registry.register_root("memory","fixture-memory",config)
            if role in ("consolidation","reflector"):packet,service=inputs.derived(role,ns,ledger.head(),memory)
            else:
                projection=RuntimeBuilder(cards,registry).build(ns,ledger.head(),memory)["projection_ref"]
                service=RuntimeSession(ledger,projection,"service integration");briefs=json.loads(service.briefs())
                if role=="solver":
                    task=output/"packet-task";task.mkdir();(task/"SPEC.md").write_text("Inspect the service integration.")
                    packet=inputs.task(role,ns,ledger.head(),task,{"id":"fixture-target"},briefs)
                else:
                    parent=memory if role=="proposer" else registry.register_root("scenario","fixture-scenario")
                    packet=inputs.research(role,ns,ledger.head(),parent,[source],briefs)
        return RPCDisclosure(packet,service,output/"disclosure")


def verify_fixture(output):
    output=Path(output);transport=output/"transport";recipe=sealed_json(transport,"recipe.json")
    disclosure=SealedDisclosure(output/"disclosure",recipe["disclosure_sha256"])
    messages=[parse_json(line.decode()) for line in sealed_bytes(transport,"stdout.bin").splitlines()]
    response=next(m["result"] for m in messages if type(m.get("id")) is int and m["id"]==2 and "method" not in m)
    turn=next(m["result"] for m in messages if type(m.get("id")) is int and m["id"]==3 and "method" not in m)
    events=validate_sealed(transport,response["thread"]["id"],turn["turn"]["id"],disclosure)
    require(disclosure.thread==response["thread"]["id"] and disclosure.turn==turn["turn"]["id"],"rpc_disclosure_session")
    return {"role":disclosure.role,"event_result":events,"category":"infrastructure_acceptance",
        "actual_model_calls":0,"formal_admission":False,"kernel_frozen":False}


def main(argv=None):
    p=argparse.ArgumentParser(description="Six fixed-role model-free native diagnostics; verify never starts a subject")
    commands=p.add_subparsers(dest="action",required=True)
    run=commands.add_parser("run");run.add_argument("--role",choices=ROLES,required=True);run.add_argument("--cli",required=True);run.add_argument("--output",required=True)
    verify=commands.add_parser("verify");verify.add_argument("--output",required=True)
    a=p.parse_args(argv)
    if a.action=="verify":
        report=verify_fixture(a.output);print(canonical(report).decode());return 0
    gateway=build_fixture(a.role,a.output);result=run_fixture(a.cli,Path(a.output)/"transport",gateway)
    if result["status"]!="failed":result["read_only_verification"]=verify_fixture(a.output)
    save(Path(a.output)/"result.json",result)
    print(canonical({k:result[k] for k in ("status","actual_model_calls","formal_admission")}).decode())
    return 0 if result["status"]!="failed" else 2


if __name__=="__main__":raise SystemExit(main())
