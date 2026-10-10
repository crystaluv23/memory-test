"""Local dependency task. Fixture expectations are explicitly authored, not worker code."""
import copy
import random

from .contracts import sha256

CONTRACT = """Implement engine.process(case), using the existing production run.py.
case contains jobs, services, committed_ids, and contract ('single' or 'dual').
Each job has id (string), amount (finite number, excluding bool), and dependency.
The services map is the actual observed environment health for this request.
A dependency is available only when its ready is exactly true and api_version is
exactly integer 1 for single, or exactly integer 1/2 for dual. Missing, false,
unknown version and bool version are unavailable. Never treat a successful local
command or an older receipt as evidence of present service health.
Process jobs in order. Previously committed ids are skipped; ids must not be
processed twice. Add an id to request-local seen at its first encounter, even
when deferred; later jobs with that id are skipped in this request. This seen
set is not a durable commitment and resets for the next request. Invalid amount is deferred with reason 'invalid_amount',
unavailable dependency with reason 'dependency_unavailable'. Deferred jobs are
not committed. Return exactly {accepted:[ids], skipped:[ids], deferred:[{id,reason}],
committed_ids:[sorted unique committed ids]}. Preserve old commitments, allowing
a future request to retry deferred jobs when the dependency is actually ready.
"""

BROKEN = '''def process(c):
    ids=[j['id'] for j in c['jobs']]
    return {'accepted':ids,'skipped':[],'deferred':[],
            'committed_ids':sorted(set(c['committed_ids']+ids))}
'''


def cases(seed, dual):
    rng = random.Random(seed)
    a, b = "job"+str(rng.randrange(10000)), "job"+str(rng.randrange(10000)+10000)
    job = {"id": a, "amount": rng.randrange(1, 100), "dependency": "queue"}
    base = {"jobs": [job], "services": {"queue": {"ready": True, "api_version": 1}},
            "committed_ids": [], "contract": "dual" if dual else "single"}
    def accepted(ids, skipped=(), deferred=(), committed=None):
        return {"accepted": ids, "skipped": list(skipped), "deferred": list(deferred), "committed_ids": sorted(ids if committed is None else committed)}
    rows = [{"group": "healthy", "input": copy.deepcopy(base), "expected": accepted([a])}]
    for name, service in (("not_ready", {"ready": False, "api_version": 1}), ("missing", None),
            ("bad_version", {"ready": True, "api_version": 3}), ("bool_ready", {"ready": 1, "api_version": 1}),
            ("bool_version", {"ready": True, "api_version": True})):
        case = copy.deepcopy(base)
        case["services"] = {"queue": service} if service is not None else {}
        rows.append({"group": name, "input": case, "expected": accepted([], deferred=[{"id": a, "reason": "dependency_unavailable"}])})
    case = copy.deepcopy(base); case["services"]["queue"]["api_version"] = 2
    rows.append({"group": "version_change", "input": case, "expected": accepted([a]) if dual else accepted([], deferred=[{"id": a, "reason": "dependency_unavailable"}])})
    case = copy.deepcopy(base); case["jobs"].append(copy.deepcopy(job))
    rows.append({"group": "idempotent", "input": case, "expected": accepted([a], skipped=[a])})
    case = copy.deepcopy(base); case["committed_ids"] = [a, b]
    rows.append({"group": "preserve_committed", "input": case, "expected": accepted([], skipped=[a], committed=[a,b])})
    for name, amount in (("bool_amount", True), ("invalid_amount", "not-a-number")):
        case = copy.deepcopy(base); case["jobs"][0]["amount"] = amount
        rows.append({"group": name, "input": case, "expected": accepted([], deferred=[{"id": a, "reason": "invalid_amount"}])})
    case = copy.deepcopy(base); case["jobs"].append({"id": b, "amount": 3, "dependency": "missing"})
    rows.append({"group": "partial_environment", "input": case, "expected": accepted([a], deferred=[{"id": b, "reason": "dependency_unavailable"}])})
    # A failed first occurrence still consumes the id only for this request.
    case = copy.deepcopy(base); case["jobs"] = [dict(job, amount="not-a-number"), copy.deepcopy(job)]
    rows.append({"group": "deferred_invalid_then_duplicate", "input": case,
                 "expected": accepted([], skipped=[a], deferred=[{"id": a, "reason": "invalid_amount"}])})
    case = copy.deepcopy(base); case["jobs"] = [dict(job, dependency="missing"), copy.deepcopy(job)]
    rows.append({"group": "deferred_unavailable_then_duplicate", "input": case,
                 "expected": accepted([], skipped=[a], deferred=[{"id": a, "reason": "dependency_unavailable"}])})
    case = copy.deepcopy(base); case["jobs"] = [dict(job, amount="not-a-number"), dict(job, amount=False)]
    rows.append({"group": "deferred_duplicate_not_processed_twice", "input": case,
                 "expected": accepted([], skipped=[a], deferred=[{"id": a, "reason": "invalid_amount"}])})
    case = copy.deepcopy(base); case["jobs"] = [copy.deepcopy(job), dict(job, amount=False)]
    rows.append({"group": "accepted_then_invalid_duplicate_skipped", "input": case,
                 "expected": accepted([a], skipped=[a])})
    case = copy.deepcopy(base); other = dict(job, id=b)
    case["jobs"] = [dict(job, dependency="missing"), other, copy.deepcopy(job), copy.deepcopy(other), dict(job, amount=False)]
    rows.append({"group": "interleaved_ordered_statuses", "input": case,
                 "expected": accepted([b], skipped=[a,b,a], deferred=[{"id": a, "reason": "dependency_unavailable"}])})
    case = copy.deepcopy(base); case["committed_ids"] = [a,b]; case["jobs"] = [dict(job, amount=False), copy.deepcopy(job)]
    rows.append({"group": "old_commit_precedes_invalid_and_duplicate", "input": case,
                 "expected": accepted([], skipped=[a,a], committed=[a,b])})
    case = copy.deepcopy(base)
    rows.append({"group": "later_request_retries_uncommitted_deferred", "input": case,
                 "expected": accepted([a])})
    case = copy.deepcopy(base); case["services"]["queue"]["ready"] = False
    case["jobs"] = [dict(job, amount="not-a-number"), copy.deepcopy(job)]
    rows.append({"group": "invalid_before_dependency_and_duplicate", "input": case,
                 "expected": accepted([], skipped=[a], deferred=[{"id": a, "reason": "invalid_amount"}])})
    rng.shuffle(rows)
    return rows


def suite_fingerprint(seed, dual):
    return sha256(cases(seed, dual))
