"""Offline controller commands; this module never starts Codex or another model."""
import argparse
import json
from pathlib import Path

from .cards import CanonicalCards, EvidenceStore
from .contracts import ContractError, read_kernel
from .ledger import Ledger
from .registry import Registry


def parse_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ContractError("duplicate_json_key", key)
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique,
                       parse_constant=lambda x: (_ for _ in ()).throw(ContractError("nonfinite_json", x)))


def read_json(path):
    return parse_json(Path(path).read_text())


def main(argv=None):
    p = argparse.ArgumentParser(description="V0 controller registry/evidence commands; offline only")
    p.add_argument("--db", required=True)
    p.add_argument("--kernel-config")
    commands = p.add_subparsers(dest="action", required=True)
    commands.add_parser("init")
    commands.add_parser("audit")
    rebuild = commands.add_parser("rebuild")
    rebuild.add_argument("--memory-ref",required=True)
    rebuild.add_argument("--namespace",required=True)
    rebuild.add_argument("--cutoff",required=True,type=int)
    rebuild.add_argument("--runtime-config")
    rebuild.add_argument("--derived-refs")
    derived_prepare=commands.add_parser("derive-input")
    derived_prepare.add_argument("--role",choices=("consolidation","reflector"),required=True)
    derived_prepare.add_argument("--memory-ref",required=True)
    derived_prepare.add_argument("--namespace",required=True)
    derived_prepare.add_argument("--cutoff",type=int,required=True)
    derived_accept=commands.add_parser("derive-admit")
    derived_accept.add_argument("--request-id",required=True)
    derived_accept.add_argument("--input",required=True)
    derived_accept.add_argument("--response",required=True)
    derived_accept.add_argument("--invocation",required=True)
    freeze = commands.add_parser("prepare-v0")
    freeze.add_argument("--memory-refs",required=True)
    freeze.add_argument("--scenario-ref",required=True)
    freeze.add_argument("--namespace",required=True)
    freeze.add_argument("--seeds",required=True)
    freeze.add_argument("--output",required=True)
    schedule = commands.add_parser("schedule-plan", help="complete prospective stage graph; no model calls")
    schedule.add_argument("--memory-refs", required=True)
    schedule.add_argument("--scenario-refs", required=True)
    schedule.add_argument("--namespace", required=True)
    schedule.add_argument("--cutoff", type=int, required=True)
    schedule.add_argument("--seeds", required=True)
    schedule.add_argument("--learning-mode", choices=("component_comparison", "independent_learning"), required=True)
    schedule.add_argument("--meta-memory-ref")
    schedule.add_argument("--shadow-memory-ref")
    schedule.add_argument("--research-corpora")
    bundle = commands.add_parser("verify-bundle")
    bundle.add_argument("--bundle",required=True)
    root = commands.add_parser("root")
    root.add_argument("--kind", choices=("memory", "scenario"), required=True)
    root.add_argument("--id", required=True)
    root.add_argument("--config")
    source = commands.add_parser("source")
    source.add_argument("--input", required=True,
        help="JSON with request_id, namespace, event_type, episode, timestamp, text, visibility")
    extract = commands.add_parser("extract")
    extract.add_argument("--input", required=True)
    extract.add_argument("--request-id", required=True)
    extract.add_argument("--namespace", required=True)
    extract.add_argument("--cutoff", required=True, type=int)
    extract.add_argument("--timestamp", required=True)
    corpus = commands.add_parser("corpus")
    corpus.add_argument("--namespace", required=True)
    corpus.add_argument("--cutoff", required=True, type=int)
    proposal = commands.add_parser("propose")
    proposal.add_argument("--input", required=True)
    proposal.add_argument("--request-id", required=True)
    proposal.add_argument("--role", choices=("evaluator", "proposer"), required=True)
    proposal.add_argument("--cutoff", required=True, type=int)
    args = p.parse_args(argv)
    kernel = read_kernel(args.kernel_config)
    with Ledger(args.db) as ledger:
        registry = Registry(ledger, kernel)
        evidence = EvidenceStore(ledger, kernel)
        cards = CanonicalCards(evidence)
        if args.action == "init":
            result = {"status": "offline_registry_ready", "kernel": registry.fingerprint,
                      "head": ledger.head(), "live_model_calls": 0, "kernel_frozen": False}
        elif args.action == "audit":
            result = {**ledger.audit(), "head": ledger.head(), "live_model_calls": 0}
        elif args.action == "root":
            result = registry.register_root(args.kind, args.id, read_json(args.config) if args.config else None)
        elif args.action == "source":
            value = read_json(args.input)
            from .contracts import exact_object
            exact_object(value, {"request_id", "namespace", "event_type", "episode", "timestamp", "text", "visibility"})
            result = evidence.register(value["request_id"], value["namespace"], value["event_type"],
                value["episode"], value["timestamp"], value["text"], value["visibility"])
        elif args.action == "extract":
            result = cards.extract(args.request_id, read_json(args.input), args.namespace, args.cutoff, args.timestamp)
        elif args.action == "corpus":
            result = cards.corpus(args.namespace, args.cutoff)
        elif args.action == "rebuild":
            from .runtime import RuntimeBuilder, read_runtime
            result = RuntimeBuilder(cards,registry,read_runtime(args.runtime_config)).build(args.namespace,args.cutoff,read_json(args.memory_ref),
                read_json(args.derived_refs) if args.derived_refs else None)
        elif args.action == "derive-input":
            from .derived import DerivedStage
            result=DerivedStage(cards,registry).prepare(args.role,args.namespace,args.cutoff,read_json(args.memory_ref))
        elif args.action == "derive-admit":
            from .derived import DerivedStage
            result=DerivedStage(cards,registry).accept(args.request_id,read_json(args.input),Path(args.response).read_text(),read_json(args.invocation))
        elif args.action == "prepare-v0":
            from .freeze import prepare
            result=prepare(registry,cards,read_json(args.memory_refs),read_json(args.scenario_ref),args.namespace,read_json(args.seeds),args.output)
        elif args.action == "schedule-plan":
            from .scheduling import compile_schedule
            result=compile_schedule(registry, read_json(args.memory_refs), read_json(args.scenario_refs),
                read_json(args.seeds), cards.corpus(args.namespace,args.cutoff)["sha256"], mode=args.learning_mode,
                meta_memory_ref=read_json(args.meta_memory_ref) if args.meta_memory_ref else None,
                shadow_memory_ref=read_json(args.shadow_memory_ref) if args.shadow_memory_ref else None,
                research_corpora=read_json(args.research_corpora) if args.research_corpora else None)
        elif args.action == "verify-bundle":
            from .freeze import verify
            value=verify(args.bundle)
            result={"status":"bundle_verified","jobs":len(value["jobs"]),"mode":value["mode"],"kernel_frozen":False}
        else:
            result = registry.propose(args.request_id, read_json(args.input), args.role, args.cutoff, cards.proposal_evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return result


if __name__ == "__main__":
    main()
