"""Request-local deduplication remains separate from durable commitments."""
import tempfile
from pathlib import Path
from unittest import TestCase
from memorylab.scenario import materialize, probes, production, verify
from test_scenario import GOOD_SERVICE
from test_environment import FINAL

OLD_INCORRECT_REFERENCE = "import math\ndef process(c):\n    done=set(c['committed_ids']); accepted=[]; skipped=[]; deferred=[]\n    for job in c['jobs']:\n        id_=job['id']\n        if id_ in done:\n            skipped.append(id_); continue\n        amount=job['amount']\n        if type(amount) not in (int,float) or not math.isfinite(amount):\n            deferred.append(dict(id=id_,reason='invalid_amount')); continue\n        env=c['services'].get(job['dependency'])\n        versions=(1,2) if c['contract']=='dual' else (1,)\n        if not isinstance(env,dict) or env.get('ready') is not True or type(env.get('api_version')) is not int or env['api_version'] not in versions:\n            deferred.append(dict(id=id_,reason='dependency_unavailable')); continue\n        accepted.append(id_); done.add(id_)\n    return dict(accepted=accepted,skipped=skipped,deferred=deferred,committed_ids=sorted(done))\n"


def episode(seed,stage=0,transfer="near"):
    return dict(id="service-semantic-audit",role="target",backend="service",stage=stage,
                seed=seed,rule_mode=None,context_profile="clean",semantic_interference="none",transfer_distance=transfer)


class ServiceSeenTests(TestCase):
    def test_deferred_duplicate_then_next_request_retry_matches_independent_results(self):
        job=dict(id="a",amount=1,dependency="queue")
        base=dict(services={"queue":{"ready":True,"api_version":1}},committed_ids=[],contract="single")
        inputs=[dict(base,jobs=[dict(job,amount="invalid"),job]),
                dict(base,jobs=[job]),
                dict(base,jobs=[dict(job,dependency="missing"),job]),
                dict(base,jobs=[dict(job,amount=False),job],committed_ids=["a","old"])]
        expected=[dict(accepted=[],skipped=["a"],deferred=[dict(id="a",reason="invalid_amount")],committed_ids=[]),
                  dict(accepted=["a"],skipped=[],deferred=[],committed_ids=["a"]),
                  dict(accepted=[],skipped=["a"],deferred=[dict(id="a",reason="dependency_unavailable")],committed_ids=[]),
                  dict(accepted=[],skipped=["a","a"],deferred=[],committed_ids=["a","old"])]
        with tempfile.TemporaryDirectory(prefix="memorylab-service-semantic-") as tmp:
            root=Path(tmp);control=root/"control";control.mkdir();worker=root/"worker"
            materialize(worker,episode(19));(worker/"engine.py").write_text(GOOD_SERVICE)
            actual=production(worker,control,inputs)
            self.assertEqual(actual["exit_code"],0,actual)
            self.assertEqual(actual["output"],expected)

    def test_old_positive_control_is_rejected_by_production_for_all_seed_transfer_modes(self):
        required={"deferred_invalid_then_duplicate","deferred_unavailable_then_duplicate",
                  "deferred_duplicate_not_processed_twice","accepted_then_invalid_duplicate_skipped",
                  "interleaved_ordered_statuses","old_commit_precedes_invalid_and_duplicate",
                  "later_request_retries_uncommitted_deferred","invalid_before_dependency_and_duplicate"}
        for seed,stage,transfer in [(3,0,"near"),(71,4,"far"),(801,4,"near")]:
            with self.subTest(seed=seed,stage=stage,transfer=transfer), tempfile.TemporaryDirectory(prefix="memorylab-service-semantic-") as tmp:
                root=Path(tmp);control=root/"control";control.mkdir();ep=episode(seed,stage,transfer)
                self.assertTrue(required <= {r["group"] for r in probes(ep,seed+17)})
                good=root/"good";protected=materialize(good,ep);(good/"engine.py").write_text(GOOD_SERVICE)
                grade=verify(good,control,ep,protected,FINAL)
                self.assertTrue(grade["full_success"],grade)
                bad=root/"bad";protected=materialize(bad,ep);(bad/"engine.py").write_text(OLD_INCORRECT_REFERENCE)
                grade=verify(bad,control,ep,protected,FINAL)
                self.assertFalse(grade["full_success"],grade)
                for group in ["deferred_invalid_then_duplicate","deferred_unavailable_then_duplicate","interleaved_ordered_statuses"]:
                    self.assertLess(grade["groups"][group]["passed"],grade["groups"][group]["total"])
