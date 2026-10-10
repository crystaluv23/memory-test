import copy
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from memorylab.common import tree_hash
from memorylab.contracts import canonical
from memorylab.scenario import (CLIENT, BACKENDS, ScenarioPolicy, ScenarioSocket, compile_chain,
                                materialize, probes, production, read_scenario, verify)
from test_environment import GOOD, FINAL
from test_kernel import Fixture

# Independently authored candidate; not imported into task generation/verifier.
GOOD_SERVICE = '''import math
def process(c):
    done=set(c['committed_ids']); seen=set(done); accepted=[]; skipped=[]; deferred=[]
    for job in c['jobs']:
        id_=job['id']
        if id_ in seen:
            skipped.append(id_); continue
        seen.add(id_)
        amount=job['amount']
        if type(amount) not in (int,float) or not math.isfinite(amount):
            deferred.append(dict(id=id_,reason='invalid_amount')); continue
        env=c['services'].get(job['dependency'])
        versions=(1,2) if c['contract']=='dual' else (1,)
        if not isinstance(env,dict) or env.get('ready') is not True or type(env.get('api_version')) is not int or env['api_version'] not in versions:
            deferred.append(dict(id=id_,reason='dependency_unavailable')); continue
        accepted.append(id_); done.add(id_)
    return dict(accepted=accepted,skipped=skipped,deferred=deferred,committed_ids=sorted(done))
'''
GOOD_REPAIR = GOOD['repair'] + '''
_base=process
def process(c):
    if c['capability_mode']=='add_only' and any(e['op']!='add' for e in c['edits']):
        return {'accepted':False,'state':c['state']}
    return _base(c)
'''
CANDIDATES = {**GOOD, "repair": GOOD_REPAIR, "service": GOOD_SERVICE}


class ScenarioTests(Fixture):
    def test_inspect_uses_frozen_public_inputs_after_worker_file_substitution(self):
        ep=self.chain()["episodes"][0];ws,protected=self.workspace(ep)
        policy=ScenarioPolicy(ws,Path(self.tmp.name),ep)
        original=policy._context()
        outside=Path(self.tmp.name)/"outside-marker";outside.write_text("harmless external marker")
        for name in ("SPEC.md","sample_input.json"):
            (ws/name).unlink();(ws/name).symlink_to(outside)
        self.assertEqual(policy._context(),original)
        graded=verify(ws,Path(self.tmp.name),ep,protected,FINAL)
        self.assertFalse(graded["full_success"])
        self.assertEqual(graded["production"]["error"],"unsafe_workspace_file")
        self.assertNotIn("harmless external marker",json.dumps(graded))

    def test_production_created_link_is_a_failed_grade_without_parent_collection(self):
        ep=self.chain()["episodes"][0];ws,protected=self.workspace(ep)
        (ws/"engine.py").write_text("import os\nos.symlink('SPEC.md','created-link')\ndef process(c):return {}\n")
        control=Path(self.tmp.name)/"control";control.mkdir()
        graded=verify(ws,control,ep,protected,FINAL)
        self.assertTrue((ws/"created-link").is_symlink())
        self.assertFalse(graded["full_success"])
        self.assertTrue(any(v.startswith("workspace_boundary:") for v in graded["protected_violations"]))

    def chain(self, family="feedback_retention", seed=19, **changes):
        config = dict(self.kernel["scenario_initial"], family=family, **changes)
        id_ = "e"+str(len(self.ledger.objects("scenario", "control")))
        root = self.registry.register_root("scenario", id_, config)
        return compile_chain(self.registry, root, seed)

    def workspace(self, episode, suffix="worker"):
        ws = Path(self.tmp.name)/suffix
        return ws, materialize(ws, episode)

    def test_deterministic_compilation_and_separate_categories(self):
        chain = self.chain()
        same = compile_chain(self.registry, chain["scenario"], 19)
        other = compile_chain(self.registry, chain["scenario"], 20)
        self.assertEqual(chain, same)
        self.assertEqual(chain["semantic_signature"], other["semantic_signature"])
        self.assertNotEqual(chain["sha256"], other["sha256"])
        self.assertEqual(chain["category"], "historical_failure_derived_synthetic")

    def test_all_six_families_have_learning_target_counterexample_and_exact_distractors(self):
        for family in BACKENDS:
            c = self.chain(family, distractor_count=4)
            roles = [e["role"] for e in c["episodes"]]
            self.assertEqual(roles.count("interference"), 4)
            self.assertEqual(roles.count("learning"), 2 if family=="abstraction_transfer" else 1)
            self.assertEqual(roles[-2:], ["target", "counterexample"])

    def test_current_workspace_has_no_lesson_gold_or_episode_role(self):
        ep = self.chain()["episodes"][-2]
        ws, protected = self.workspace(ep)
        text = (ws/"SPEC.md").read_text() + (ws/"AGENTS.md").read_text()
        for forbidden in ("learning", "target", "remember", "expected", "feedback_retention"):
            self.assertNotIn(forbidden, text)
        sample = json.loads((ws/"sample_input.json").read_text())
        self.assertIsInstance(sample, list)
        self.assertNotIn("expected", sample[0])
        self.assertNotIn("engine.py", protected)

    def test_reference_candidates_pass_full_registered_chain_not_only_smoke(self):
        counter = 0
        for family in BACKENDS:
            chain = self.chain(family, transfer_distance="far", rule_change="registered_revision")
            for ep in chain["episodes"]:
                ws, protected = self.workspace(ep, "worker"+str(counter)); counter += 1
                (ws/"engine.py").write_text(CANDIDATES[ep["backend"]])
                g = verify(ws, Path(self.tmp.name)/"control", ep, protected, FINAL)
                self.assertTrue(g["full_success"], (family,ep,g))

    def test_broken_implementations_and_false_completions_fail_every_family(self):
        for i,family in enumerate(BACKENDS):
            ep = self.chain(family)["episodes"][-2]
            ws, protected = self.workspace(ep, "worker"+str(i))
            g = verify(ws, Path(self.tmp.name)/"control", ep, protected, FINAL)
            self.assertFalse(g["full_success"])
            self.assertTrue(g["false_completion"])

    def test_rule_revision_rejects_stale_repair_and_evidence_rules(self):
        for i,(family, candidate) in enumerate((("feedback_retention", GOOD["repair"]),
            ("stale_memory", GOOD["evidence"].replace("c['contract']=='observed'", "True")))):
            ep = self.chain(family, rule_change="registered_revision")["episodes"][-2]
            ws, protected = self.workspace(ep, "worker"+str(i))
            (ws/"engine.py").write_text(candidate)
            g = verify(ws, Path(self.tmp.name)/"control", ep, protected, FINAL)
            self.assertFalse(g["full_success"], g)

    def test_transfer_really_changes_state_and_paths(self):
        near = self.chain(transfer_distance="near")["episodes"][-2]
        far = self.chain(transfer_distance="far")["episodes"][-2]
        n, f = probes(near, 23), probes(far, 23)
        self.assertNotIn("package", n[0]["input"]["state"])
        self.assertIn("package", f[0]["input"]["state"])
        self.assertTrue(all(e["path"].startswith("/package/records/") for r in f for e in r["input"]["edits"]))
        self.assertNotEqual(n[0]["input"]["snapshot"], f[0]["input"]["snapshot"])

    def test_pressure_semantics_identical_actual_tool_response_changes(self):
        clean = self.chain(context_profile="clean")["episodes"][-2]
        pressure = self.chain(context_profile="pressure")["episodes"][-2]
        self.assertEqual(probes(clean), probes(pressure))
        ws1,_ = self.workspace(clean, "clean"); ws2,_ = self.workspace(pressure, "pressure")
        p1 = ScenarioPolicy(ws1, Path(self.tmp.name)/"control", clean)
        p2 = ScenarioPolicy(ws2, Path(self.tmp.name)/"control", pressure)
        w1 = p1.dispatch({"op":"inspect"}); w2 = p2.dispatch({"op":"inspect"})
        self.assertGreaterEqual(len(w2), 3*len(w1))
        self.assertFalse(p2.events[0]["delivered"])
        p2.delivered(); self.assertTrue(p2.events[0]["delivered"])
        self.assertFalse(p2.events[0]["tokenizer"]["live_qualified"])
        self.assertNotIn("expected", json.loads(w2)["environment"])

    def test_semantic_interference_has_explicit_inapplicable_scope(self):
        ep = self.chain(semantic_interference="similar_inapplicable")["episodes"][-2]
        ws,_ = self.workspace(ep)
        p = ScenarioPolicy(ws, Path(self.tmp.name)/"control", ep)
        wire = p.dispatch({"op":"inspect"})
        self.assertIn("not the current task", json.loads(wire)["environment"]["other_environment_contract"])

    def test_interference_is_backend_specific_and_does_not_change_gold(self):
        texts={}
        for family,backend in BACKENDS.items():
            ep=self.chain(family=family,semantic_interference="similar_inapplicable")["episodes"][-2]
            plain=dict(ep,semantic_interference="none")
            self.assertEqual(probes(ep),probes(plain))
            ws,_=self.workspace(ep,"scoped-"+family)
            p=ScenarioPolicy(ws,Path(self.tmp.name)/"control",ep)
            text=json.loads(p.dispatch({"op":"inspect"}))["environment"]["other_environment_contract"]
            self.assertIn("not the current task",text)
            if backend!="evidence":self.assertNotIn("observed_at",text)
            texts[backend]=text
        self.assertEqual(len(set(texts.values())),4)

    def test_oversized_tool_reply_keeps_private_result_and_discloses_only_error(self):
        ep=self.chain()["episodes"][0];ws,_=self.workspace(ep)
        config=read_scenario();config["max_response_bytes"]=128
        p=ScenarioPolicy(ws,Path(self.tmp.name)/"control",ep,config)
        execution={"output":None,"exit_code":0,"stdout":"x"*10000,"stderr":"","error":None}
        with patch("memorylab.scenario.production",return_value=execution):wire=p.dispatch({"op":"check"})
        self.assertEqual(json.loads(wire),{"error":"scenario_response_limit"})
        self.assertEqual(p.events[0]["response"],json.loads(wire))
        self.assertEqual(p.events[0]["private_oversized_response"]["production"],execution)
        self.assertEqual(p.events[0]["bytes"],len(wire));self.assertEqual(p.feedback_count,0)

    def test_production_output_limit_retains_bounded_raw_stream(self):
        ep=self.chain()["episodes"][0];ws,_=self.workspace(ep)
        (ws/"engine.py").write_text("def process(c):\n    print('x'*5000,flush=True)\n    return {}\n")
        config=read_scenario();config["production_stdout_bytes"]=128
        result=production(ws,Path(self.tmp.name)/"control",[probes(ep)[0]["input"]],config)
        self.assertEqual(result["error"],"production_stdout_overflow")
        self.assertIsNone(result["output"]);self.assertTrue(result["stdout_truncated"])
        self.assertEqual(result["raw_streams"]["stdout"]["bytes"],128)

    def test_feedback_only_after_actual_smoke_failure_and_once(self):
        ep = self.chain()["episodes"][0]
        ws,_ = self.workspace(ep)
        p = ScenarioPolicy(ws, Path(self.tmp.name)/"control", ep)
        (ws/"engine.py").write_text("raise RuntimeError('smoke failure')\n")
        first = json.loads(p.dispatch({"op":"check"}))
        second = json.loads(p.dispatch({"op":"check"}))
        self.assertIsNotNone(first["feedback"])
        self.assertIsNone(second["feedback"])
        self.assertNotEqual(first["production"]["exit_code"], 0)
        (ws/"engine.py").write_text(CANDIDATES[ep["backend"]])
        p2 = ScenarioPolicy(ws, Path(self.tmp.name)/"control", ep)
        result = json.loads(p2.dispatch({"op":"check"}))
        self.assertTrue(result["public_smoke_passed"])
        self.assertIsNone(result["feedback"])

    def test_protected_file_and_partial_completion_cannot_pass(self):
        ep = self.chain()["episodes"][-2]
        ws, protected = self.workspace(ep)
        (ws/"engine.py").write_text(CANDIDATES[ep["backend"]])
        self.assertFalse(verify(ws, Path(self.tmp.name)/"control", ep, protected, dict(FINAL,completion_claim="partial"))["full_success"])
        (ws/"run.py").write_text((ws/"run.py").read_text()+"\n# unauthorized\n")
        g = verify(ws, Path(self.tmp.name)/"control", ep, protected, FINAL)
        self.assertEqual(g["protected_violations"], ["run.py"])
        self.assertFalse(g["full_success"])

    def test_socket_real_inspect_delivery_keeps_controller_log(self):
        ep = self.chain()["episodes"][-2]
        ws,_ = self.workspace(ep)
        (ws/"scenario.py").write_text(CLIENT)
        p = ScenarioPolicy(ws, Path(self.tmp.name)/"control", ep)
        with ScenarioSocket(ws, p):
            result = subprocess.run([sys.executable,str(ws/"scenario.py"),"inspect"], capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn("environment",json.loads(result.stdout))
        self.assertTrue(p.events[0]["delivered"])
        self.assertFalse((ws/".scenario.sock").exists())

    def test_no_overwrite_existing_workspaces(self):
        ep = self.chain()["episodes"][0]
        ws,_ = self.workspace(ep)
        self.assert_code("scenario_workspace_not_empty",lambda:materialize(ws,ep))
