"""Historical failure-derived synthetic tasks; oracle never copied to workers."""
import copy
import random
from pathlib import Path

from .common import digest, save

FAMILIES = ("recovery", "repair", "evidence")
STAGES = ("learning", "repeat", "near_transfer", "far_transfer", "rule_change")

ENTRY = '''import json, sys
from engine import process
if __name__ == "__main__":
    request = json.load(sys.stdin)
    print(json.dumps([process(case) for case in request], sort_keys=True))
'''

CONTRACTS = {
    "recovery": """Implement engine.process(case), using the production run.py entry.
case has marker_key and state[marker_key], a list of receipts, interruption,
next_id, and contract ('strict' or 'legacy'). State's marker is the only committed
pointer. Never advance it by observing staged receipts or a higher filename.
A receipt is usable only when its id equals the pointer, committed is true,
and hash == SHA256 of payload encoded as JSON sort_keys=True, ensure_ascii=False,
separators=(',', ':'). Missing/invalid/duplicate usable receipts -> action 'blocked'.
No pointer (null) -> action 'start', resume_from null, outcome 'not_started'.
With valid receipt, provider_failure or paused -> action 'pause'; unsupported ->
action 'fallback'; other interruption -> action 'resume'. resume_from is the
committed id. Outcome: provider_failure='technical_failure', unsupported=
'capability_gap', otherwise 'engineering_complete'. benefit is true only when
payload.promoted AND payload.used AND payload.independent_gain_confirmed are all
exact booleans true. Strict contract additionally requires payload.cohort_fixed
exact true; legacy does not. If blocked, outcome='integrity_failure', benefit=false.
Return exactly {resume_from, action, outcome, benefit}. Reject bool/non-integer
or negative committed pointers as blocked (resume_from null). A null pointer
starts even when staged receipts exist. No fabricated research conclusion.
""",
    "repair": """Implement engine.process(case), using production run.py entry.
case contains state (JSON object), snapshot (canonical SHA256), edits, grants.
Each edit and grant has op ('add' or 'replace') and path (JSON Pointer).
Edits also have value. Authorize exact op+path membership only; grant prefixes
do not authorize children. Validate SHA256 using JSON sort_keys=True,
ensure_ascii=False,separators=(',', ':'). A bad hash rejects the whole transaction.
Parse JSON Pointer strictly (~0='~', ~1='/'; malformed ~ escapes reject).
Apply edits in listed order to a working copy. Each parent coordinate must exist
when that edit is applied. Validate key existence and array bounds against that
current working copy, not only the original snapshot. add creates an absent object key;
replace only replaces an existing object key or valid existing array index.
Array append, deletion, root replacement, duplicate edit paths, missing parents,
negative/leading-zero/non-numeric indices and unauthorized edits reject the
whole transaction. Do not partly apply valid siblings when another edit fails.
Empty edits are accepted if hash matches. Preserve all unaffected content.
Return exactly {accepted: boolean, state: original-or-fully-repaired-object}.
Grants may include replace; do not over-generalize an old 'add-only' lesson.
""",
    "evidence": """Implement engine.process(case), using production run.py entry.
case has facts, cutoff (ISO YYYY-MM-DD), metric, period and contract ('published'
or 'observed'). Eligible facts must match metric and period, scope='total', and
published_at <= cutoff. For observed contract max(published_at, observed_at)
must be <= cutoff; a missing observed_at makes that fact ineligible. Do not sum
partial tags into a total. Values must be finite int/float excluding bool.
Among eligible facts choose latest published_at. At that timestamp differing
values mean unavailable; equal values choose lexicographically smallest id.
No eligible facts or conflicting latest values -> {status:'unavailable', value:null,
source:null}. Otherwise {status:'available', value:chosen value, source:chosen id}.
Dates and ids are well formed. A citation alone doesn't establish semantic scope.
The observed contract is stricter; do not apply it to published contract cases.
""",
}

BROKEN = {
    "recovery": '''def process(case):
    rows = case["receipts"]
    latest = max(rows, key=lambda r: r["id"]) if rows else None
    return {"resume_from": latest["id"] if latest else None,
            "action": "resume", "outcome": "engineering_complete",
            "benefit": bool(latest)}
''',
    "repair": '''import copy
def process(case):
    state = copy.deepcopy(case["state"])
    for edit in case["edits"]:
        key = edit["path"].split("/")[-1]
        state[key] = edit["value"]
    return {"accepted": True, "state": state}
''',
    "evidence": '''def process(case):
    facts = [f for f in case["facts"] if f["metric"] == case["metric"]]
    if not facts:
        return {"status": "unavailable", "value": None, "source": None}
    f = max(facts, key=lambda f: f["published_at"])
    return {"status": "available", "value": f["value"], "source": f["id"]}
''',
}


def oracle(family, case):
    if family == "recovery":
        marker = case["state"][case["marker_key"]]
        output = {"resume_from": marker, "action": "blocked",
                  "outcome": "integrity_failure", "benefit": False}
        if marker is None:
            return dict(resume_from=None, action="start", outcome="not_started", benefit=False)
        if type(marker) is not int or marker < 0:
            output["resume_from"] = None
            return output
        usable = [r for r in case["receipts"] if r["id"] == marker
                  and r["committed"] is True and r["hash"] == digest(r["payload"])]
        if len(usable) != 1:
            return output
        why = case["interruption"]
        output.update(action={"paused": "pause", "provider_failure": "pause",
                              "unsupported": "fallback"}.get(why, "resume"),
                      outcome={"provider_failure": "technical_failure",
                               "unsupported": "capability_gap"}.get(why, "engineering_complete"))
        payload = usable[0]["payload"]
        keys = ["promoted", "used", "independent_gain_confirmed"]
        if case["contract"] == "strict":
            keys.append("cohort_fixed")
        output["benefit"] = all(payload.get(k) is True for k in keys)
        return output
    if family == "repair":
        original = copy.deepcopy(case["state"])
        state = copy.deepcopy(original)
        try:
            if case["snapshot"] != digest(original):
                raise ValueError("snapshot")
            used = set()
            for edit in case["edits"]:
                path, op = edit["path"], edit["op"]
                if path in used or not path.startswith("/") or op not in ("add", "replace"):
                    raise ValueError("path/op")
                used.add(path)
                if not any(g["op"] == op and g["path"] == path for g in case["grants"]):
                    raise ValueError("authority")
                parts = []
                for raw in path[1:].split("/"):
                    import re
                    if re.search(r"~(?![01])", raw):
                        raise ValueError("escape")
                    parts.append(raw.replace("~1", "/").replace("~0", "~"))
                target = state
                for part in parts[:-1]:
                    if isinstance(target, list):
                        if not part.isdigit() or str(int(part)) != part:
                            raise ValueError("index")
                        target = target[int(part)]
                    else:
                        target = target[part]
                key = parts[-1]
                if isinstance(target, dict):
                    if (op == "add") == (key in target):
                        raise ValueError("existence")
                    target[key] = copy.deepcopy(edit["value"])
                elif isinstance(target, list) and op == "replace":
                    if not key.isdigit() or str(int(key)) != key:
                        raise ValueError("index")
                    target[int(key)] = copy.deepcopy(edit["value"])
                else:
                    raise ValueError("target")
            return {"accepted": True, "state": state}
        except (ValueError, KeyError, IndexError, TypeError):
            return {"accepted": False, "state": original}
    import math
    eligible = []
    for fact in case["facts"]:
        value = fact["value"]
        if (fact["metric"] != case["metric"] or fact["period"] != case["period"]
                or fact["scope"] != "total" or fact["published_at"] > case["cutoff"]
                or type(value) not in (int, float) or not math.isfinite(value)):
            continue
        if case["contract"] == "observed" and (not fact.get("observed_at")
                or max(fact["published_at"], fact["observed_at"]) > case["cutoff"]):
            continue
        eligible.append(fact)
    absent = dict(status="unavailable", value=None, source=None)
    if not eligible:
        return absent
    latest = max(f["published_at"] for f in eligible)
    candidates = [f for f in eligible if f["published_at"] == latest]
    if len({f["value"] for f in candidates}) != 1:
        return absent
    chosen = min(candidates, key=lambda f: f["id"])
    return dict(status="available", value=chosen["value"], source=chosen["id"])


def cases(family, seed, episode):
    rng = random.Random(seed * 97 + episode * 13)
    result = []
    for repetition in range(3):
        n = rng.randrange(10, 900)
        suffix = str(rng.randrange(10000))
        if family == "recovery":
            key = "committed_" + suffix if episode >= 2 else "last_completed_cycle"
            payload = dict(promoted=True, used=True, independent_gain_confirmed=True,
                           cohort_fixed=episode != 4)
            row = dict(id=n, committed=True, payload=payload, hash=digest(payload))
            base = dict(marker_key=key, state={key: n}, receipts=[row], next_id=n+1,
                        interruption="crash", contract="strict" if episode == 4 else "legacy")
            variants = [("resume", base)]
            for group, mutate in (
                ("staged_not_committed", lambda c: c["receipts"].append(dict(row, id=n+10, committed=False))),
                ("missing_receipt", lambda c: c.update(receipts=[])),
                ("corrupt_receipt", lambda c: c["receipts"][0].update(hash="0"*64)),
                ("duplicate_receipt", lambda c: c["receipts"].append(copy.deepcopy(row))),
                ("provider_control", lambda c: c.update(interruption="provider_failure")),
                ("fallback", lambda c: c.update(interruption="unsupported")),
                ("pause", lambda c: c.update(interruption="paused")),
                ("cold_start", lambda c: c["state"].update({key: None})),
                ("bad_pointer", lambda c: c["state"].update({key: True})),
            ):
                c = copy.deepcopy(base); mutate(c); variants.append((group, c))
            c = copy.deepcopy(base)
            c["receipts"][0]["payload"]["used"] = False
            c["receipts"][0]["hash"] = digest(c["receipts"][0]["payload"])
            variants.append(("gain_not_execution", c))
        elif family == "repair":
            key = "items_" + suffix
            state = {key: [{"value": n, "keep": [1, {"x": suffix}]}], "untouched": suffix,
                     "a/b": {"~key": 4}}
            path = "/" + key + "/0/missing"
            base = dict(state=state, snapshot=digest(state),
                        edits=[dict(op="add", path=path, value=suffix)],
                        grants=[dict(op="add", path=path)])
            variants = [("authorized_add", base)]
            c = copy.deepcopy(base); c["snapshot"] = "bad"; variants.append(("snapshot_bound", c))
            c = copy.deepcopy(base); c["grants"] = []; variants.append(("no_authority", c))
            c = copy.deepcopy(base); c["grants"][0]["path"] = "/"+key; variants.append(("prefix_not_authority", c))
            c = copy.deepcopy(base); c["edits"].append(dict(op="add", path="/intrusion", value=5)); variants.append(("atomic_preserve", c))
            c = copy.deepcopy(base); c["edits"].append(copy.deepcopy(c["edits"][0])); variants.append(("duplicate_edit", c))
            for group, p, op in [("authorized_replace", "/"+key+"/0/value", "replace"),
                                 ("array_replace", "/"+key+"/0", "replace"),
                                 ("no_append", "/"+key+"/1", "add"),
                                 ("no_future_parent", "/future/key", "add"),
                                 ("bad_array_index", "/"+key+"/00/value", "replace"),
                                 ("escaped_pointer", "/a~1b/~0key", "replace"),
                                 ("malformed_escape", "/a~2b/key", "add")]:
                c = copy.deepcopy(base)
                c.update(edits=[dict(op=op, path=p, value=n+1)], grants=[dict(op=op, path=p)])
                variants.append((group, c))
            c = copy.deepcopy(base); c["edits"] = []; variants.append(("empty_valid", c))
            # Changing an ancestor changes the valid domain of later edits.
            # Exercise the complete ordered transaction, including rollback,
            # rather than awarding success from isolated coordinates alone.
            def transaction(group, initial, edits, grants=None):
                case = dict(state=initial, snapshot=digest(initial), edits=edits,
                            grants=[dict(op=e["op"], path=e["path"]) for e in edits]
                            if grants is None else grants)
                variants.append((group, case))
            def edit(op, path, value):
                return dict(op=op, path=path, value=value)
            root = "/" + key
            initial = {key: {"value": n}, "untouched": suffix}
            transaction("ancestor_removes_then_child_add", initial,
                        [edit("replace", root, {}), edit("add", root+"/value", n+1)])
            transaction("ancestor_creates_then_child_replace", {key: {}, "untouched": suffix},
                        [edit("replace", root, {"value": n}), edit("replace", root+"/value", n+1)])
            transaction("ancestor_array_grows_then_child_replace", {key: [n], "untouched": suffix},
                        [edit("replace", root, [n, n+1]), edit("replace", root+"/1", n+2)])
            transaction("ancestor_array_shrinks_then_child_reject", {key: [n, n+1], "untouched": suffix},
                        [edit("replace", root, [n]), edit("replace", root+"/1", n+2)])
            transaction("ancestor_scalar_then_child_reject", initial,
                        [edit("replace", root, n), edit("replace", root+"/value", n+1)])
            transaction("child_then_ancestor_order", initial,
                        [edit("replace", root+"/value", n+1), edit("replace", root, {"value": n+2})])
            transaction("escaped_ancestor_then_child_add", {"a/b": {"~key": n}, "untouched": suffix},
                        [edit("replace", "/a~1b", {}), edit("add", "/a~1b/~0key", n+1)])
            transaction("disjoint_sibling_transaction", initial,
                        [edit("replace", root+"/value", n+1), edit("replace", "/untouched", suffix+"-changed")])
            transaction("late_failure_rolls_back_all_siblings", initial,
                        [edit("replace", "/untouched", suffix+"-changed"),
                         edit("replace", root, n), edit("replace", root+"/value", n+1)])
            transaction("added_parent_then_child_add", {"untouched": suffix},
                        [edit("add", root, {}), edit("add", root+"/value", n)])
            transaction("ancestor_grant_not_child_authority", initial,
                        [edit("replace", root, {}), edit("add", root+"/value", n+1)],
                        grants=[dict(op="replace", path=root)])
        else:
            metric = "cash_" + suffix if episode >= 2 else "revenue"
            fact = dict(id="f"+suffix, metric=metric, period="2025FY", scope="total",
                        published_at="2026-01-10", observed_at="2026-01-11", value=n)
            base = dict(facts=[fact], cutoff="2026-02-01", metric=metric, period="2025FY",
                        contract="observed" if episode == 4 else "published")
            variants = [("valid_total", base)]
            for group, additions in (
                ("future_not_visible", [dict(fact, id="future", published_at="2026-04-01", value=n*3)]),
                ("partial_not_total", [dict(fact, id="segment", scope="partial", published_at="2026-02-01", value=0)]),
                ("wrong_period", [dict(fact, id="other", period="2024FY", published_at="2026-02-01", value=n*4)]),
                ("conflicting_latest", [dict(fact, id="conflict", value=n+1)]),
                ("stable_tie", [dict(fact, id="a-first")]),
                ("wrong_metric", [dict(fact, id="other", metric="other", published_at="2026-02-01", value=n*4)]),
            ):
                c = copy.deepcopy(base); c["facts"].extend(additions); variants.append((group, c))
            for group, mutate in (
                ("no_source", lambda c: c.update(facts=[])),
                ("bool_not_number", lambda c: c["facts"][0].update(value=True)),
                ("observed_drift", lambda c: c["facts"][0].update(observed_at="2026-03-01")),
                ("missing_observation", lambda c: c["facts"][0].pop("observed_at")),
            ):
                c = copy.deepcopy(base); mutate(c); variants.append((group, c))
        result.extend(dict(group=group, input=c, expected=oracle(family, c)) for group, c in variants)
    rng.shuffle(result)
    return result


def materialize(workspace, family, seed, episode):
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "engine.py").write_text(BROKEN[family])
    (workspace / "run.py").write_text(ENTRY)
    (workspace / "SPEC.md").write_text(CONTRACTS[family] + "\nCurrent episode: " + STAGES[episode] +
                                      "\nThis is a synthetic engineering replay; no production credentials or live services.\n")
    # Example input is not part of the hidden suite and has no golden output.
    save(workspace / "sample_input.json", [cases(family, seed + 7001, episode)[0]["input"]])
    (workspace / "AGENTS.md").write_text("Read SPEC.md. Modify engine.py only. run.py is the production entry. "
        "Verify general behavior and counterexamples. Do not access outside this workspace. "
        "Do not edit AGENTS.md, SPEC.md, run.py or experiment infrastructure.\n")
    return {"family": family, "seed": seed, "episode": episode, "stage": STAGES[episode],
            "category": ("workflow/state" if family == "recovery" else
                         "workflow/authority" if family == "repair" else "data/visibility")
                         if episode != 3 else "integration/consumer",
            "contract_changed": episode == 4,
            "provenance": "historical_failure_derived_synthetic"}
