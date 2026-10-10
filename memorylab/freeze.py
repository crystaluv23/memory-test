"""Self-contained content-addressed controller bundles. No model calls here."""
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .contracts import (canonical, exact_object, identifier, positive_int, require,
                        sha256)
from .kernel_cli import read_json
from .scenario import compile_chain, read_scenario
from .runtime import read_runtime

RESOURCES = ["configs/kernel_v0.json", "configs/runtime_v0.json", "configs/scenario_v0.json", "configs/execution_v0.json", "configs/epoch_v0.json", "configs/history_v0.json", "configs/derived_v0.json", "configs/schedule_v0.json", "configs/role_v0.json", "configs/canary_v0.json", "configs/admission_v0.json", "configs/store_v0.json", "configs/workspace_v0.json", "configs/stage_input_v0.json", "configs/task_stage_v0.json", "configs/role_stage_v0.json", "configs/control_stage_v0.json", "configs/graph_run_v0.json", "configs/app_server_probe_v0.json", "configs/rpc_role_v0.json", "configs/provider_budget_v0.json", "configs/rpc_disclosure_v0.json", "configs/rpc_output_v0.json"]


def execution_config(path=None):
    root = Path(__file__).resolve().parent.parent
    config = read_json(path or root/"configs/execution_v0.json")
    return validate_execution(config)


def validate_execution(config):
    exact_object(config, {"schema_version", "status", "hypothesis", "mode", "live_execution_authorized", "model", "resolved_model",
        "cli_version", "reasoning_effort", "tokenizer", "episode_seconds", "episode_commands", "episode_input_tokens", "episode_output_tokens",
        "batch_seconds", "round_calls", "round_tokens", "automatic_retry", "unknown_usage_blocks_promotion", "freeze_resources"}, "invalid_execution_config")
    require(config["schema_version"] == "execution-contract-v0", "unsupported_execution_config")
    require(config["mode"] in ("offline_acceptance", "live_canary", "formal_comparison"), "invalid_execution_mode")
    require(type(config["live_execution_authorized"]) is bool, "invalid_execution_authorization")
    require(config["automatic_retry"] is False and config["unknown_usage_blocks_promotion"] is True, "unsafe_execution_policy")
    require(config["freeze_resources"] == RESOURCES, "unsupported_freeze_resources")
    for field in ("episode_seconds", "episode_commands", "episode_input_tokens", "episode_output_tokens", "batch_seconds", "round_calls", "round_tokens"):
        positive_int(config[field], "invalid_execution_limit")
    exact_object(config["tokenizer"], {"id", "live_qualified"}, "invalid_execution_tokenizer")
    identifier(config["tokenizer"]["id"])
    require(type(config["tokenizer"]["live_qualified"]) is bool, "invalid_execution_tokenizer")
    return config


def live_blockers(config, attestation=None):
    blockers = []
    if config["mode"] == "offline_acceptance": blockers.append("offline_acceptance_only")
    if not config["live_execution_authorized"]: blockers.append("live_not_authorized")
    for field in ("model", "resolved_model", "cli_version"):
        if type(config[field]) is not str or not config[field].strip(): blockers.append("missing_"+field)
    if not config["tokenizer"]["live_qualified"]: blockers.append("unqualified_tokenizer")
    if not attestation: blockers.append("missing_role_isolation_and_budget_attestation")
    else:
        # A text assertion is insufficient. S8 must bind actual probe/usage
        # receipts; this implementation deliberately has no live-ready shortcut.
        blockers.append("live_attestation_validator_not_accepted")
    return blockers


def implementation_files(root=None):
    root = Path(root or Path(__file__).resolve().parent.parent)
    paths = sorted((root/"memorylab").glob("*.py")) + [root/p for p in RESOURCES]
    contents = {}
    for path in paths:
        require(path.is_file() and not path.is_symlink(), "invalid_freeze_resource")
        contents[str(path.relative_to(root))] = path.read_bytes()
    return contents


def environment_identity():
    executable = Path(sys.executable).resolve()
    return {"python_version": sys.version, "python_executable": str(executable),
            "python_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(), "platform": sys.platform}


def prepare(registry, cards, memory_refs, scenario_ref, ns, seeds, output, config=None):
    with cards.ledger.mutation():
        return _prepare(registry,cards,memory_refs,scenario_ref,ns,seeds,output,config)


def _prepare(registry, cards, memory_refs, scenario_ref, ns, seeds, output, config=None):
    """Freeze one focus chain and every planned slot; pending never disappears."""
    config = validate_execution(config) if config is not None else execution_config()
    require(config["mode"] == "offline_acceptance", "live_plan_not_implemented")
    require(type(memory_refs) is list and 1 <= len(memory_refs) <= 8, "invalid_active_population")
    require(len({canonical(r) for r in memory_refs}) == len(memory_refs), "duplicate_memory_arm")
    for ref in memory_refs:
        require(registry.resolve(ref)["kind"] == "memory", "invalid_memory_arm")
    require(type(seeds) is list and bool(seeds) and all(type(s) is int and s >= 0 for s in seeds), "invalid_seed_plan")
    require(len(set(seeds)) == len(seeds), "duplicate_repeat_seed")
    cutoff = cards.ledger.head()
    corpus = cards.corpus(ns, cutoff)
    runtime, scenario_contract = read_runtime(), read_scenario()
    from .scheduling import compile_schedule
    stages = compile_schedule(registry, memory_refs, [scenario_ref], seeds, corpus["sha256"],
                              execution=config, scenario_contract=scenario_contract)
    jobs, chains = [], []
    for repeat, seed in enumerate(seeds):
        chain = compile_chain(registry, scenario_ref, seed, scenario_contract); chains.append(chain)
        for arm, memory in enumerate(memory_refs):
            for ep in chain["episodes"]:
                jobs.append({"id": "job-r"+str(repeat)+"-a"+str(arm)+"-"+ep["id"], "repeat": repeat, "arm": arm,
                    "memory": memory, "episode": ep, "chain_sha256": chain["sha256"], "mode": "scripted_acceptance",
                    "namespace": "task/arm"+str(arm)+"-repeat"+str(repeat), "initial_corpus_sha256": corpus["sha256"]})
    files = implementation_files()
    for name,value in (("kernel", registry.kernel), ("runtime",runtime), ("scenario",scenario_contract), ("execution",config)):
        files["configs/"+name+"_v0.json"] = canonical(value)+b"\n"
    file_hashes = {name: hashlib.sha256(data).hexdigest() for name,data in files.items()}
    payload = {"schema_version": 1, "mode": "scripted_acceptance", "category": "infrastructure_acceptance", "kernel_frozen": False,
        "kernel_sha256": registry.fingerprint, "implementation": file_hashes, "environment": environment_identity(),
        "execution": config, "runtime": runtime, "scenario_contract": scenario_contract,
        "memory_refs": memory_refs, "scenario_ref": scenario_ref, "corpus": corpus, "cutoff": cutoff,
        "namespace_events_sha256": sha256(cards.ledger.events(ns,cutoff)),
        "source_snapshot_sha256": sha256([{"ref":r,"value":v} for r,v in cards.ledger.objects("source",ns)]),
        "chains": chains, "jobs": jobs, "prospective_stage_graph": stages, "budget_preview": {
            "solver_calls_if_live": len(jobs), "extractor_calls_if_live": sum(j["episode"]["role"] in ("learning", "interference") for j in jobs),
            "derived_calls_if_live": 0, "proposal_calls_if_live": 0, "oracle_calls_if_live": 0, "shadow_calls_if_live": 0,
            "planned_simulated_invocations": len(jobs), "live_model_calls": 0,
            "maximum_solver_tokens_if_live": len(jobs)*(config["episode_input_tokens"]+config["episode_output_tokens"]),
            "note": "Only offline infrastructure slots are executable; a live epoch needs all-stage scheduling and budget qualification"}}
    fingerprint = sha256(payload)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    destination = output/fingerprint
    if destination.exists():
        verify(destination)
        return {"path": str(destination), "fingerprint": fingerprint, "cache_hit": True, "new_model_calls": 0}
    staging = Path(tempfile.mkdtemp(prefix=".freeze-", dir=output))
    try:
        for name,data in files.items():
            path = staging/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        (staging/"plan.json").write_bytes(canonical(payload)+b"\n")
        # Retain exactly the declared corpus namespace history plus relevant spec
        # ancestors. The tested workers never receive this private controller DB.
        from .ledger import Ledger
        with Ledger(staging/"inputs.sqlite") as target:
            objects = []
            def add_ref(ref):
                if canonical(ref) in {canonical(r) for r,_ in objects}: return
                value = cards.ledger.get(ref); objects.append((ref,value))
                if value.get("parent") is not None: add_ref(value["parent"])
            for ref in memory_refs+[scenario_ref]: add_ref(ref)
            for ref,value in cards.ledger.objects("kernel", "control"): objects.append((ref,value))
            for kind in ("source", "card", "index"):
                for ref,value in cards.ledger.objects(kind,ns): objects.append((ref,value))
            with target.transaction():
                for ref,value in objects: target.put(value,ref["namespace"])
                for sequence,request_id,row_ns,hash_,data in cards.ledger.db.execute(
                    "SELECT sequence,request_id,namespace,sha256,payload FROM events WHERE namespace=? AND sequence<=?", (ns,cutoff)):
                    target.db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (sequence,request_id,row_ns,hash_,data))
                for row in cards.ledger.db.execute("SELECT request_id,request_sha256,namespace,sha256,payload FROM receipts WHERE namespace=?", (ns,)):
                    # prepare cutoff is current head; all namespace receipts belong
                    # to the same visible committed snapshot.
                    target.db.execute("INSERT INTO receipts VALUES(?,?,?,?,?)",row)
            target.audit()
        bootstrap = "from pathlib import Path\nimport sys\nsys.path.insert(0,str(Path(__file__).resolve().parent))\nfrom memorylab.__main__ import main\nmain()\n"
        (staging/"bootstrap.py").write_text(bootstrap)
        contents = {str(p.relative_to(staging)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(staging.rglob("*")) if p.is_file()}
        manifest = {"schema_version": 1, "fingerprint": fingerprint, "payload_sha256": fingerprint, "files": contents,
                    "category": "infrastructure_acceptance", "kernel_frozen": False}
        (staging/"manifest.json").write_bytes(canonical(manifest)+b"\n")
        for file in staging.rglob("*"):
            if file.is_file():
                with file.open("rb") as stream: os.fsync(stream.fileno())
        for directory in sorted((p for p in staging.rglob("*") if p.is_dir()), key=lambda p:len(p.parts),reverse=True)+[staging]:
            fd=os.open(directory,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        os.rename(staging,destination)
        fd=os.open(output,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    verify(destination)
    return {"path": str(destination), "fingerprint": fingerprint, "cache_hit": False, "new_model_calls": 0}


def verify(bundle):
    bundle = Path(bundle)
    manifest = read_json(bundle/"manifest.json")
    exact_object(manifest, {"schema_version", "fingerprint", "payload_sha256", "files", "category", "kernel_frozen"}, "invalid_bundle_manifest")
    require(not any(p.is_symlink() for p in bundle.rglob("*")),"frozen_bundle_symlink")
    current = {str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(bundle.rglob("*")) if p.is_file() and p != bundle/"manifest.json"}
    require(current == manifest["files"], "frozen_bundle_drift")
    payload = read_json(bundle/"plan.json")
    require(sha256(payload) == manifest["payload_sha256"] == manifest["fingerprint"], "frozen_payload_drift")
    require(manifest["kernel_frozen"] is False and payload["category"] == "infrastructure_acceptance", "unaccepted_kernel_freeze")
    return payload
