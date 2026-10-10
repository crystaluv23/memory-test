"""Read-only historical provenance and checkpoint staging; never H2 acceptance."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .common import save
from .contracts import (ContractError, SHA, canonical, exact_object, identifier,
                        positive_int, require, sha256, timestamp)
from .kernel_cli import parse_json, read_json

ROOT = Path(__file__).resolve().parent.parent
SECRET = re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"\b(?:sk|ghp|github_pat)-[A-Za-z0-9_-]{20,}|\bghp_[A-Za-z0-9]{20,}|"
    rb"\b(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*[\"']?[A-Za-z0-9_+/=-]{24,}", re.I)


def read_history(path=None):
    config = read_json(path or ROOT/"configs/history_v0.json")
    exact_object(config, {"schema_version", "status", "hypothesis", "max_source_bytes", "max_event_bytes",
        "max_checkpoint_file_bytes", "max_checkpoint_bytes", "minimum_adapter_chains", "minimum_untouched_holdout_chains",
        "forbidden_path_parts", "required_verifier_coverage", "feedback_predicates", "live_execution_authorized"}, "invalid_history_config")
    require(config["schema_version"] == "history-contract-v0" and config["live_execution_authorized"] is False,
            "unsupported_history_contract")
    for key in ("max_source_bytes", "max_event_bytes", "max_checkpoint_file_bytes", "max_checkpoint_bytes",
                "minimum_adapter_chains", "minimum_untouched_holdout_chains"):
        positive_int(config[key], "invalid_history_limit")
    require(config["required_verifier_coverage"] == ["production_entry", "recovery", "successor_consumption", "illegal_action"] and
        config["feedback_predicates"] == ["none", "production_check_failed"], "unsupported_history_registry")
    require(type(config["forbidden_path_parts"]) is list and all(type(p) is str for p in config["forbidden_path_parts"]) and
        {".git", ".codex", ".env", "auth.json", "credentials", "secrets", "memory", "memories", "reports", "progress", "runs", "answers", "gold"}
        <= set(config["forbidden_path_parts"]), "unsafe_history_path_policy")
    return config


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked_bytes(path, maximum):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "invalid_history_source_file")
    # Read an additional byte to detect concurrent growth and cap memory usage.
    with path.open("rb") as stream:
        data = stream.read(maximum+1)
    require(len(data) <= maximum, "history_source_too_large")
    return data


def source_event(locator, project, config):
    exact_object(locator, {"path", "file_sha256", "line", "line_sha256", "text_sha256", "timestamp"}, "invalid_history_locator")
    positive_int(locator["line"], "invalid_history_line")
    for key in ("file_sha256", "line_sha256", "text_sha256"):
        require(type(locator[key]) is str and SHA.fullmatch(locator[key]), "invalid_history_hash")
    data = checked_bytes(locator["path"], config["max_source_bytes"])
    require(digest(data) == locator["file_sha256"], "history_source_drift")
    lines = data.splitlines(keepends=True)
    require(locator["line"] <= len(lines), "history_line_missing")
    line = lines[locator["line"]-1]
    require(digest(line) == locator["line_sha256"], "history_line_drift")
    row = parse_json(line.decode("utf-8"))
    require(type(row) is dict,"invalid_history_event")
    payload = row.get("payload", {})
    require(type(payload) is dict, "invalid_history_event")
    if row.get("type") == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
        content = payload.get("content")
        require(type(content) is list and bool(content) and all(type(c) is dict and c.get("type") == "input_text" and
            type(c.get("text")) is str for c in content), "unsupported_history_user_content")
        text = "\n".join(c["text"] for c in content)
    elif row.get("type") == "event_msg" and payload.get("type") == "user_message":
        text = payload.get("message")
        require(type(text) is str, "invalid_history_user_content")
    else:
        raise ContractError("history_event_not_user_input")
    encoded = text.encode("utf-8")
    require(bool(text.strip()) and len(encoded) <= config["max_event_bytes"], "invalid_history_event_size")
    require(digest(encoded) == locator["text_sha256"], "history_text_drift")
    require(row.get("timestamp") == locator["timestamp"], "history_timestamp_drift")
    timestamp(locator["timestamp"])
    require(not any(marker in text for marker in ("<environment_context>", "# AGENTS.md instructions", "<INSTRUCTIONS>")),
        "history_injected_context_not_task")
    require(not SECRET.search(encoded), "history_secret_material")
    metadata = None
    for raw in lines:
        parsed = parse_json(raw.decode("utf-8"))
        require(type(parsed) is dict,"invalid_history_event")
        if parsed.get("type") == "session_meta":
            require(metadata is None, "duplicate_history_session_meta")
            metadata = parsed.get("payload", {})
    require(type(metadata) is dict and type(metadata.get("cwd")) is str and Path(metadata["cwd"]).resolve() == Path(project).resolve(), "history_wrong_project")
    require(type(metadata.get("id")) is str and bool(metadata["id"]), "missing_history_session_id")
    return {"text": text, "timestamp": locator["timestamp"], "session_id": metadata["id"],
            "provenance": locator, "sha256": digest(encoded)}


def git(project, *args):
    command = ["git", "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.pager=cat", "-C", str(project), *args]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"})
    require(result.returncode == 0, "historical_git_object_unavailable")
    return result.stdout


def project_identity(project):
    return {"head": git(project, "rev-parse", "HEAD").decode().strip(),
        "status_sha256": digest(git(project, "status", "--porcelain=v1", "-z", "--untracked-files=all")),
        "diff_sha256": digest(git(project, "diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD"))}


def checkpoint(project, value, learning_time, config):
    exact_object(value, {"commit", "files"}, "invalid_history_checkpoint")
    commit = value["commit"]
    require(type(commit) is str and re.fullmatch(r"[0-9a-f]{40}", commit), "invalid_history_commit")
    require(git(project, "cat-file", "-t", commit).strip() == b"commit", "history_checkpoint_not_commit")
    committed = git(project, "show", "-s", "--format=%cI", commit).decode().strip()
    require(timestamp(committed) <= timestamp(learning_time), "post_learning_checkpoint")
    paths = value["files"]
    require(type(paths) is list and all(type(p) is str for p in paths) and len(paths) == len(set(paths)), "invalid_checkpoint_paths")
    files = {}; total = 0
    for path in paths:
        require(type(path) is str and bool(path) and "\\" not in path and "\n" not in path and "\x00" not in path,
                "invalid_checkpoint_path")
        pure = PurePosixPath(path)
        require(not pure.is_absolute() and str(pure) == path and not any(part in ("..", ".") for part in pure.parts), "invalid_checkpoint_path")
        require(not any(part.casefold() in config["forbidden_path_parts"] or part.casefold().startswith(".env") or
            part.casefold().endswith((".key", ".pem", ".p12", ".pfx")) for part in pure.parts), "forbidden_checkpoint_path")
        tree = git(project, "ls-tree", "-z", commit, "--", path).split(b"\0")
        rows = [r for r in tree if r]
        require(len(rows) == 1, "checkpoint_path_missing")
        header, actual = rows[0].split(b"\t", 1)
        mode, kind, blob = header.split()
        require(actual.decode() == path and mode in (b"100644", b"100755") and kind == b"blob", "nonregular_checkpoint_file")
        size = int(git(project, "cat-file", "-s", blob.decode()).strip())
        require(size <= config["max_checkpoint_file_bytes"], "checkpoint_file_too_large")
        total += size
        require(total <= config["max_checkpoint_bytes"], "checkpoint_too_large")
        data = git(project, "cat-file", "blob", blob.decode())
        require(len(data) == size and not SECRET.search(data), "history_secret_material")
        files[path] = data
    return {"commit": commit, "commit_timestamp": committed, "files": {p:digest(b) for p,b in files.items()}, "bytes":total}, files


def audit_candidate(candidate, project, config):
    """Return honest blockers; structural checks alone cannot attest H2 execution."""
    exact_object(candidate, {"id", "split", "checkpoint", "learning", "target", "run_contract", "environment", "feedback", "verifier"}, "invalid_history_candidate")
    identifier(candidate["id"])
    require(candidate["split"] in ("adapter", "untouched_holdout"), "invalid_history_split")
    blockers = []; events = {}; state = None; files = {}
    for role in ("learning", "target"):
        locator = candidate[role]
        if locator is None:
            blockers.append("missing_"+role+"_original_event")
        else:
            events[role] = source_event(locator, project, config)
    if len(events) == 2:
        require(timestamp(events["learning"]["timestamp"]) < timestamp(events["target"]["timestamp"]), "history_event_order")
        require(events["learning"]["provenance"] != events["target"]["provenance"], "same_history_event")
    if candidate["checkpoint"] is None:
        blockers.append("missing_pre_learning_checkpoint")
    elif "learning" not in events:
        blockers.append("cannot_bind_checkpoint_cutoff")
    else:
        state, files = checkpoint(project,candidate["checkpoint"],events["learning"]["timestamp"],config)
        if not files: blockers.append("missing_checkpoint_dependency_allowlist")
    run = candidate["run_contract"]
    if run is not None:
        exact_object(run,{"path","sha256"},"invalid_history_run_contract")
        data = checked_bytes(run["path"],config["max_source_bytes"])
        require(digest(data)==run["sha256"],"history_contract_drift")
        contract = parse_json(data.decode()).get("contract",{})
        require(type(contract) is dict, "invalid_history_run_contract")
        if state is not None: require(contract.get("code_revision")==state["commit"],"history_contract_revision_mismatch")
        if "learning" in events: require(timestamp(contract.get("created_at")) <= timestamp(events["learning"]["timestamp"]),"post_learning_run_contract")
    for name in ("environment","feedback","verifier"):
        if candidate[name] is None: blockers.append("missing_"+name+"_contract")
    if candidate["environment"] is not None:
        env=candidate["environment"]
        exact_object(env,{"python","dependencies","external_io","replay_manifest_sha256"},"invalid_history_environment")
        require(type(env["python"]) is str and bool(env["python"]) and type(env["dependencies"]) is list, "invalid_history_environment")
        require(env["external_io"]=="fixed_replay" and type(env["replay_manifest_sha256"]) is str and SHA.fullmatch(env["replay_manifest_sha256"]),"unbound_history_external_io")
    if candidate["feedback"] is not None:
        feedback=candidate["feedback"]
        exact_object(feedback,{"predicate","production_check_sha256"},"invalid_history_feedback")
        require(feedback["predicate"] in config["feedback_predicates"],"unregistered_history_feedback")
        if feedback["predicate"]=="none": require(feedback["production_check_sha256"] is None,"invalid_history_feedback")
        else: require(type(feedback["production_check_sha256"]) is str and SHA.fullmatch(feedback["production_check_sha256"]),"unbound_history_feedback")
    if candidate["verifier"] is not None:
        verifier=candidate["verifier"]
        exact_object(verifier,{"id","sha256","coverage","independent_contract_sha256"},"invalid_history_verifier")
        identifier(verifier["id"])
        for key in ("sha256","independent_contract_sha256"):
            require(type(verifier[key]) is str and SHA.fullmatch(verifier[key]),"unbound_history_verifier")
        require(type(verifier["coverage"]) is list and all(type(c) is str for c in verifier["coverage"]) and
            set(config["required_verifier_coverage"]) <= set(verifier["coverage"]),"incomplete_history_verifier")
    result={"id":candidate["id"],"split":candidate["split"],"status":"incomplete" if blockers else "staged_requires_independent_replay",
        "blockers":blockers,"checkpoint":state,"source_events":{k:{key:v[key] for key in ("timestamp","session_id","sha256","provenance")} for k,v in events.items()},
        "candidate_sha256":sha256(candidate),"h2_accepted":False,"live_model_calls":0}
    return result,files,events


def audit_catalog(catalog, config=None):
    config=read_history() if config is None else config
    exact_object(catalog,{"schema_version","category","project","candidates"},"invalid_history_catalog")
    require(catalog["schema_version"]==1 and catalog["category"] in ("real_history_candidates","infrastructure_fixture"),"invalid_history_catalog")
    project=Path(catalog["project"]).resolve()
    if catalog["category"]=="real_history_candidates":
        require(project==Path("/Users/l/Project/Recommandation"),"wrong_real_history_project")
    require(type(catalog["candidates"]) is list and all(type(c) is dict and type(c.get("id")) is str for c in catalog["candidates"]),"invalid_history_candidate_list")
    require(len({c["id"] for c in catalog["candidates"]})==len(catalog["candidates"]),"duplicate_history_candidate")
    before=project_identity(project); rows=[]
    for candidate in catalog["candidates"]:
        try: row,_,_=audit_candidate(candidate,project,config)
        except (ContractError,OSError,ValueError,UnicodeError,subprocess.SubprocessError) as exc:
            row={"id":candidate.get("id"),"split":candidate.get("split"),"status":"rejected","blockers":[getattr(exc,"code","history_source_unavailable")],"h2_accepted":False,"live_model_calls":0}
        rows.append(row)
    require(project_identity(project)==before,"original_project_changed_during_audit")
    return {"schema_version":1,"catalog_sha256":sha256(catalog),"contract_sha256":sha256(config),"category":catalog["category"],
        "original_project":before,"original_project_unchanged":True,"identity_scope":"HEAD, status names, tracked diff; declared sources bind individual hashes",
        "planned":len(rows),"rows":rows,"h2_accepted":0,"kernel_frozen":False,"live_model_calls":0,
        "adapter_requirement":config["minimum_adapter_chains"],"untouched_holdout_requirement":config["minimum_untouched_holdout_chains"]}


def stage(catalog, candidate_id, output, config=None):
    config=read_history() if config is None else config
    # Validate the complete catalog before selecting, preserving its denominator.
    audit=audit_catalog(catalog,config)
    selected=[c for c in catalog["candidates"] if c["id"]==candidate_id]
    require(len(selected)==1,"history_candidate_missing")
    project=Path(catalog["project"]).resolve(); output=Path(output).resolve()
    require(not output.is_relative_to(project) and not output.is_relative_to(Path.home()/".codex"),"history_output_in_source")
    before=project_identity(project)
    result,files,events=audit_candidate(selected[0],project,config)
    require(result["status"]=="staged_requires_independent_replay","history_candidate_incomplete")
    payload={"schema_version":1,"category":catalog["category"],"candidate":selected[0],"audit":result,"contract":config,"original_project":before,
        "requires_independent_content_review":True,"requires_full_production_replay":True,"h2_accepted":False,"live_model_calls":0}
    fingerprint=sha256(payload); output.mkdir(parents=True,exist_ok=True); destination=output/fingerprint
    if destination.exists():
        verify_package(destination)
        return {"path":str(destination),"fingerprint":fingerprint,"cache_hit":True,"h2_accepted":False,"live_model_calls":0}
    staging=Path(tempfile.mkdtemp(prefix=".history-",dir=output))
    try:
        for name,data in files.items():
            path=staging/"solver"/"checkpoint"/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        # Private scoring/provenance stays outside solver. These tasks are authentic
        # inputs, not a summary of the old agent's patch or successful answer.
        for role,event in events.items():
            path=staging/"tasks"/(role+".txt");path.parent.mkdir(parents=True,exist_ok=True);path.write_text(event["text"],encoding="utf-8")
        save(staging/"controller.json",payload)
        require(project_identity(project)==before,"original_project_changed_during_stage")
        # Re-read authentic files to detect concurrent changes during export.
        refreshed,_,_=audit_candidate(selected[0],project,config)
        require(refreshed==result,"history_sources_changed_during_stage")
        manifest={"fingerprint":fingerprint,"files":{str(p.relative_to(staging)):digest(p.read_bytes()) for p in staging.rglob("*") if p.is_file()},
            "h2_accepted":False,"live_model_calls":0}
        save(staging/"manifest.json",manifest)
        for path in staging.rglob("*"):
            if path.is_file():
                with path.open("rb") as stream:os.fsync(stream.fileno())
        for directory in sorted((p for p in staging.rglob("*") if p.is_dir()),key=lambda p:len(p.parts),reverse=True)+[staging]:
            fd=os.open(directory,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        os.rename(staging,destination)
        fd=os.open(output,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if staging.exists():shutil.rmtree(staging)
    verify_package(destination)
    return {"path":str(destination),"fingerprint":fingerprint,"cache_hit":False,"h2_accepted":False,"live_model_calls":0}


def verify_package(path):
    path=Path(path)
    require(path.is_dir() and not path.is_symlink(),"invalid_history_package")
    require(not any(p.is_symlink() for p in path.rglob("*")),"history_package_symlink")
    manifest=read_json(path/"manifest.json")
    exact_object(manifest,{"fingerprint","files","h2_accepted","live_model_calls"},"invalid_history_manifest")
    actual={str(p.relative_to(path)):digest(p.read_bytes()) for p in path.rglob("*") if p.is_file() and p!=path/"manifest.json"}
    require(actual==manifest["files"],"history_package_drift")
    payload=read_json(path/"controller.json")
    require(sha256(payload)==manifest["fingerprint"] and manifest["h2_accepted"] is False and manifest["live_model_calls"]==0,"invalid_history_package_identity")
    return payload


def main(argv=None):
    parser=argparse.ArgumentParser(description="Read-only authentic historical audit; no model calls")
    parser.add_argument("--config")
    sub=parser.add_subparsers(dest="action",required=True)
    p=sub.add_parser("audit");p.add_argument("--catalog",required=True);p.add_argument("--output",required=True)
    p=sub.add_parser("stage");p.add_argument("--catalog",required=True);p.add_argument("--candidate",required=True);p.add_argument("--output",required=True)
    p=sub.add_parser("verify");p.add_argument("--package",required=True)
    args=parser.parse_args(argv);config=read_history(args.config)
    if args.action=="audit":
        catalog=read_json(args.catalog)
        output=Path(args.output).resolve();project=Path(catalog["project"]).resolve()
        require(not output.is_relative_to(project) and not output.is_relative_to(Path.home()/".codex"),"history_output_in_source")
        result=audit_catalog(catalog,config);save(output,result)
    elif args.action=="stage": result=stage(read_json(args.catalog),args.candidate,args.output,config)
    else:
        payload=verify_package(args.package);result={"fingerprint":sha256(payload),"status":"staged_requires_independent_replay","h2_accepted":False,"live_model_calls":0}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result
