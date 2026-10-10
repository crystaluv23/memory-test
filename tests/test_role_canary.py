import copy
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from memorylab.contracts import canonical,sha256
from memorylab.kernel_cli import read_json
from memorylab.role_canary import execute,prepare,verify


class CanaryPacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.cli=self.root/"version-only-codex-fixture"
        self.cli.write_text("#!"+sys.executable+"\nprint('codex-cli version-fixture-no-model')\n");self.cli.chmod(0o700)
        self.prepared=prepare(self.root/"bundles",self.cli,"explicit-identity-fixture-never-called")
        self.bundle=Path(self.prepared["bundle"])
    def tearDown(self):self.tmp.cleanup()

    def script(self):
        events=[{"type":"thread.started","thread_id":"explicit-python-fixture"},{"type":"turn.started"},
            {"type":"item.completed","item":{"type":"agent_message","id":"last","text":'{"summary":"fixture did not solve","completion_claim":"partial","memory_uses":[]}'}},
            {"type":"turn.completed","usage":dict(input_tokens=80,cached_input_tokens=70,output_tokens=20)}]
        wire=b"".join(canonical(e)+b"\n" for e in events)
        return "import sys;sys.stdin.buffer.read();sys.stdout.buffer.write("+repr(wire)+");sys.stdout.flush()"

    def test_prepare_self_contained_packet_empty_runtime_and_exact_cache_no_calls(self):
        plan=verify(self.bundle)
        self.assertEqual(plan["packet"]["payload"]["role"],"solver")
        self.assertEqual(plan["packet"]["payload"]["context"]["briefs"],{"items":[]})
        self.assertEqual(plan["planned_model_sessions"],1);self.assertEqual(plan["new_model_calls"],0)
        again=prepare(self.root/"bundles",self.cli,"explicit-identity-fixture-never-called")
        self.assertEqual(again["fingerprint"],self.prepared["fingerprint"])
        self.assertTrue(again["cache_hit"])
        self.assertTrue((self.bundle/"memorylab/role_canary.py").exists())

    def test_bundle_tamper_extra_pycache_symlink_and_template_rejected(self):
        for i in range(3):
            dest=self.root/str(i);shutil.copytree(self.bundle,dest)
            if i==0:(dest/"task_template/engine.py").write_text("changed")
            if i==1:
                (dest/"memorylab/__pycache__").mkdir();(dest/"memorylab/__pycache__/injected.pyc").write_bytes(b"unsafe")
            if i==2:(dest/"link").symlink_to(self.root)
            with self.assertRaises(ValueError):verify(dest)

    def test_no_authorization_or_wrong_packet_starts_no_process_or_output(self):
        for approved,auth in ((self.prepared["packet_sha256"],False),("0"*64,True)):
            output=self.root/"unauthorized"
            with self.assertRaises(ValueError):execute(self.bundle,output,approved,auth)
            self.assertFalse(output.exists())

    def test_scripted_transport_runs_actual_independent_grade_and_preserves_failure_denominator(self):
        output=self.root/"scripted"
        r=execute(self.bundle,output,scripted_code=self.script())
        self.assertTrue(r["transport_success"])
        self.assertFalse(r["independent_behavior_success"])
        self.assertEqual(r["successful"],0);self.assertEqual(r["planned"],1);self.assertEqual(r["terminal"],1)
        self.assertEqual(r["live_model_calls"],0);self.assertFalse(r["gain_evidence"])
        self.assertEqual(r["category"],"infrastructure_acceptance")
        self.assertTrue((output/"private_grade.json").exists())
        self.assertTrue((output/"transport/process.json").exists())
        cached=execute(self.bundle,output,scripted_code=self.script())
        self.assertTrue(cached["cache_hit"]);self.assertEqual(cached["new_model_calls"],0)
        with self.assertRaises(ValueError):execute(self.bundle,output,scripted_code=self.script()+"\nprint('changed')")
        verify(self.bundle)

    def test_worker_symlink_cannot_be_collected_as_patch_and_failure_remains_terminal(self):
        code="import os;os.unlink('engine.py');os.symlink('/does-not-exist-boundary-fixture','engine.py');"+self.script()
        output=self.root/"linked-worker";r=execute(self.bundle,output,scripted_code=code)
        self.assertEqual(r["planned"],1);self.assertEqual(r["terminal"],1);self.assertEqual(r["successful"],0)
        self.assertEqual(r["workspace_boundary_error"],"unsafe_workspace_file")
        self.assertIsNone(read_json(output/"patch.json")["after"])
        self.assertFalse(read_json(output/"private_grade.json")["full_success"])
        self.assertTrue((output/"transport/events.jsonl").exists())
        cached=execute(self.bundle,output,scripted_code=code)
        self.assertTrue(cached["cache_hit"]);self.assertEqual(cached["terminal"],1);self.assertEqual(cached["new_model_calls"],0)

    def test_alive_process_or_missing_handle_is_not_closed_or_restarted(self):
        output=self.root/"running";execute(self.bundle,output,scripted_code=self.script())
        (output/"report.json").unlink();(output/"transport/process.json").unlink()
        r=execute(self.bundle,output,scripted_code=self.script())
        self.assertEqual(r["status"],"started_requires_exit_audit");self.assertEqual(r["terminal"],0)
        self.assertFalse((output/"report.json").exists())
        p=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(20)"],start_new_session=True)
        try:
            lstart=subprocess.run(["/bin/ps","-p",str(p.pid),"-o","lstart="],capture_output=True,text=True,timeout=2).stdout.strip()
            (output/"transport/process.json").write_bytes(canonical(dict(pid=p.pid,pgid=p.pid,start_time=lstart))+b"\n")
            r=execute(self.bundle,output,scripted_code=self.script())
            self.assertEqual(r["process_status"]["status"],"running");self.assertEqual(r["terminal"],0)
            self.assertIsNone(p.poll())
        finally:os.killpg(p.pid,9);p.wait(timeout=3)
        r=execute(self.bundle,output,scripted_code=self.script())
        self.assertEqual(r["status"],"interrupted");self.assertEqual(r["terminal"],1)
        self.assertEqual(r["new_model_calls"],0)

    def test_snapshot_bootstrap_verifies_outside_original_cwd(self):
        other=self.root/"other";other.mkdir()
        p=subprocess.run([sys.executable,"-I","-B",str(self.bundle/"bootstrap.py"),"verify","--bundle",str(self.bundle)],
            cwd=other,capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(__import__('json').loads(p.stdout)["new_model_calls"],0)
        verify(self.bundle)
