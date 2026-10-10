import json
import subprocess
import sys
from pathlib import Path

from memorylab.common import load, save
from memorylab.freeze import prepare
from memorylab.offline import execute
from test_kernel import Fixture
from test_scenario import CANDIDATES


class OfflineRoundTests(Fixture):
    def setUp(self):
        super().setUp()
        m=self.registry.register_root("memory","baseline")
        e=self.registry.register_root("scenario","first")
        self.bundle=prepare(self.registry,self.cards,[m],e,"task/acquisition",[19],Path(self.tmp.name)/"bundles")["path"]
        self.candidate_file=Path(self.tmp.name)/"candidates.json"
        templates={backend:{"cards":[{"id":"", "expected_version":0,"type_hint":"workflow/repair" if backend=="repair" else "workflow/recovery",
            "brief":"Validate the full current contract and preserve unaffected state.",
            "detail":"In this scripted fixture a production repair was independently verified. Check the current authority and state; this does not claim real memory benefit.",
            "source_indices":[1],"outcome":"success","status":"active","supersedes":[]}]} for backend in ("repair","recovery")}
        save(self.candidate_file,{"schema_version":1,"category":"infrastructure_acceptance","candidates":CANDIDATES,"learning_templates":templates})
        self.output=Path(self.tmp.name)/"round"

    def test_single_entry_full_chain_and_cache_no_repeated_execution(self):
        first=execute(self.bundle,self.candidate_file,self.output)
        self.assertEqual(first["planned"],5);self.assertEqual(first["successful"],5)
        self.assertEqual(first["live_model_calls"],0)
        self.assertFalse(first["gain_evidence"])
        self.assertFalse(first["kernel_frozen"])
        second=execute(self.bundle,self.candidate_file,self.output)
        self.assertEqual(second["executed_this_run"],0)
        self.assertEqual(second["cache_hits"],5)
        memory_logs=list((self.output/"trials").glob("*/memory_wire.json"))
        self.assertTrue(any(len(load(path))>1 for path in memory_logs))
        self.assertTrue(all(e["delivered"] for path in (self.output/"trials").glob("*/scenario_wire.json") for e in load(path)))

    def test_limit_preserves_full_denominator_and_resume(self):
        first=execute(self.bundle,self.candidate_file,self.output,limit=1)
        self.assertEqual(first["planned"],5);self.assertEqual(first["completed"],1)
        second=execute(self.bundle,self.candidate_file,self.output)
        self.assertEqual(second["completed"],5);self.assertEqual(second["executed_this_run"],4)

    def test_crash_recovery_records_failure_instead_of_repeating_call(self):
        def fault(name):
            if name=="memory":raise RuntimeError("injected crash")
        with self.assertRaises(RuntimeError):execute(self.bundle,self.candidate_file,self.output,limit=1,fault=fault)
        resumed=execute(self.bundle,self.candidate_file,self.output)
        self.assertEqual(resumed["recovered_interrupted"],1)
        self.assertEqual(resumed["successful"],4)
        self.assertEqual(resumed["planned"],5)
        self.assertEqual(resumed["rows"][0]["status"],"interrupted")

    def test_changed_candidate_cannot_continue_old_condition(self):
        execute(self.bundle,self.candidate_file,self.output,limit=1)
        value=load(self.candidate_file);value["candidates"]["repair"]+="\n# changed fixture\n";save(self.candidate_file,value)
        self.assert_code("execution_identity_drift",lambda:execute(self.bundle,self.candidate_file,self.output))

    def test_frozen_bootstrap_one_command_runs_offline_entry(self):
        result=subprocess.run([sys.executable,"-I","-B",str(Path(self.bundle)/"bootstrap.py"),"offline-round","--bundle",self.bundle,
            "--candidates",str(self.candidate_file),"--output",str(self.output),"--limit","1"],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads(result.stdout)
        self.assertEqual(report["live_model_calls"],0)
        self.assertEqual(report["planned"],5)
        self.assertEqual(report["completed"],1)
