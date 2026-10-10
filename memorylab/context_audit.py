"""Read-only recorded context diagnostics; never grants formal admission."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from .common import save
from .contracts import canonical, exact_object, require, sha256


def policy():
    value = json.loads((Path(__file__).resolve().parent.parent / "configs/context_audit_v0.json").read_text())
    exact_object(value, {"schema_version", "hypothesis", "max_rollout_bytes", "max_line_bytes",
        "max_records", "forbidden_input_markers", "missing_or_encrypted_input", "clearance",
        "usage_basis", "formal_admission", "model_calls"}, "invalid_context_audit_policy")
    require(value["schema_version"] == "recorded-context-audit-v0" and
        value["formal_admission"] is False and value["model_calls"] == 0 and
        value["missing_or_encrypted_input"] == "unverified_never_clean" and
        value["clearance"] == "exact_frozen_input_hash_allowlist_recorded_scope_only" and
        value["usage_basis"] == "unique_recorded_client_responses_not_provider_attestation",
        "unsupported_context_audit_policy")
    for key in ("max_rollout_bytes", "max_line_bytes", "max_records"):
        require(type(value[key]) is int and value[key] > 0, "invalid_context_audit_limit")
    require(type(value["forbidden_input_markers"]) is list and bool(value["forbidden_input_markers"]) and
        all(type(v) is str and v for v in value["forbidden_input_markers"]), "invalid_context_audit_markers")
    return value


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def encrypted(value):
    if isinstance(value, dict):
        return value.get("type") == "encrypted_content" or any(encrypted(v) for v in value.values())
    return isinstance(value, list) and any(encrypted(v) for v in value)


def audit(path, session_id, parent_thread_id, agent_path, allowed_input_hashes=None):
    """Hashes/spans only: do not export memory, secrets or private reasoning."""
    c = policy(); path = Path(path)
    require(not path.is_symlink() and path.is_file(), "invalid_context_rollout")
    require(path.stat().st_size <= c["max_rollout_bytes"], "context_rollout_limit")
    allow = allowed_input_hashes
    require(allow is None or (type(allow) is list and all(type(h) is str and len(h) == 64 and
        all(v in "0123456789abcdef" for v in h) for h in allow)), "invalid_context_input_allowlist")
    digest = hashlib.sha256(); inputs = []; hits = []; reasons = []; meta = None
    usage = {}; models = set(); cumulative = None; usage_errors = []
    size = 0; count = 0
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        while True:
            raw = stream.readline(c["max_line_bytes"] + 1)
            if not raw: break
            count += 1; size += len(raw); digest.update(raw)
            require(len(raw) <= c["max_line_bytes"] and count <= c["max_records"] and
                size <= c["max_rollout_bytes"], "context_rollout_limit")
            require(raw.endswith(b"\n"), "context_rollout_incomplete_line")
            row = json.loads(raw); require(type(row) is dict, "invalid_context_record")
            q = row.get("payload", {}); require(type(q) is dict, "invalid_context_payload")
            kind = row.get("type")
            if kind == "session_meta":
                require(meta is None and count == 1, "duplicate_or_misplaced_context_identity")
                meta = q
            incoming = kind in ("world_state", "compacted") or (kind == "response_item" and
                ((q.get("type") == "message" and q.get("role") in ("system", "developer", "user")) or
                 (q.get("type") == "agent_message" and q.get("recipient") == agent_path)))
            if incoming:
                body = q if kind in ("world_state", "compacted") else q.get("content", [])
                h = sha256(body); opaque = encrypted(body)
                inputs.append({"line": count, "kind": kind, "role": q.get("role"),
                    "sha256": h, "encrypted": opaque})
                for text in strings(body):
                    for marker in c["forbidden_input_markers"]:
                        if marker in text:
                            hit = {"line": count, "input_sha256": h, "marker": marker}
                            if hit not in hits: hits.append(hit)
                if opaque: reasons.append("encrypted_recorded_input")
                if allow is not None and h not in allow: reasons.append("unapproved_recorded_input")
            if kind == "turn_context" and type(q.get("model")) is str: models.add(q["model"])
            if kind == "token_usage_record":
                rid = q.get("response_id"); u = q.get("usage"); total = q.get("thread_token_usage")
                keys = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
                valid = (type(rid) is str and bool(rid) and q.get("thread_id") == session_id and
                    type(u) is dict and type(total) is dict)
                valid = valid and all(type(v.get(k)) is int and v[k] >= 0 for v in (u, total) for k in keys)
                if not valid:
                    usage_errors.append("invalid_recorded_usage"); continue
                if rid in usage:
                    if usage[rid] != {"usage": u, "total": total}: usage_errors.append("changed_response_usage")
                    continue
                if (u["total_tokens"] != u["input_tokens"] + u["output_tokens"] or
                    u["cached_input_tokens"] > u["input_tokens"] or u["reasoning_output_tokens"] > u["output_tokens"]):
                    usage_errors.append("inconsistent_recorded_usage")
                previous = cumulative or {k: 0 for k in keys}
                if any(total[k] != previous[k] + u[k] for k in keys):
                    usage_errors.append("inconsistent_cumulative_usage")
                usage[rid] = {"usage": u, "total": total}; cumulative = total
        after = os.fstat(stream.fileno())
    require((before.st_ino, before.st_size, before.st_mtime_ns) ==
        (after.st_ino, after.st_size, after.st_mtime_ns) and size == before.st_size,
        "context_rollout_changed_during_audit")
    require(meta is not None, "context_identity_missing")
    source = meta.get("source", {}); source = source if type(source) is dict else {}
    spawn = source.get("subagent", {}).get("thread_spawn", {})
    require(meta.get("id") == session_id and spawn.get("parent_thread_id") == parent_thread_id and
        spawn.get("agent_path") == agent_path, "context_identity_mismatch")
    if not inputs: reasons.append("recorded_inputs_missing")
    if allow is None: reasons.append("frozen_input_allowlist_missing")
    if hits: reasons.append("ambient_memory_in_recorded_input")
    status = "contaminated" if hits or "unapproved_recorded_input" in reasons else "unverified" if reasons else "recorded_inputs_allowlisted"
    return {"schema_version": c["schema_version"], "policy_sha256": sha256(c),
        "rollout_path": str(path.resolve()), "rollout_sha256": digest.hexdigest(), "rollout_bytes": size,
        "records": count, "session_id": session_id, "parent_thread_id": parent_thread_id, "agent_path": agent_path,
        "status": status, "reasons": sorted(set(reasons)), "input_hashes": inputs, "marker_hits": hits,
        "recorded_model_names": sorted(models), "recorded_client_usage": {
            "unique_responses": len(usage), "cumulative": cumulative, "errors": sorted(set(usage_errors)),
            "status": "unverified" if usage_errors or cumulative is None else "recorded_client_cumulative",
            "provider_cost": None, "provider_budget_qualified": False},
        "full_provider_context_verified": False, "formal_admission": False, "gain_evidence": False,
        "new_model_calls": 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rollout", required=True); p.add_argument("--session", required=True)
    p.add_argument("--parent", required=True); p.add_argument("--agent", required=True)
    p.add_argument("--allowlist"); p.add_argument("--output", required=True)
    a = p.parse_args(); out = Path(a.output)
    require(not out.exists(), "context_audit_output_exists")
    allowed = json.loads(Path(a.allowlist).read_text()) if a.allowlist else None
    result = audit(a.rollout, a.session, a.parent, a.agent, allowed)
    save(out, result)
    print(canonical({k: result[k] for k in ("status", "reasons", "recorded_client_usage", "formal_admission")}).decode())


if __name__ == "__main__": main()
