import base64
import sys
import tempfile
import unittest

from memorylab.process import bounded_run


class ProcessTests(unittest.TestCase):
    def run_process(self,script,data=b"",seconds=2,stdout=1024,stderr=256):
        with tempfile.TemporaryDirectory() as cwd:
            return bounded_run([sys.executable,"-I","-c",script],cwd,data,seconds,stdout,stderr)

    def test_exact_bytes_bidirectional_transport_and_both_streams(self):
        result=self.run_process("import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data); sys.stderr.write('diagnostic')",b"abc\x00\xff")
        self.assertIsNone(result["error"]);self.assertEqual(result["exit_code"],0)
        self.assertEqual(base64.b64decode(result["raw_streams"]["stdout"]["base64"]),b"abc\x00\xff")
        self.assertEqual(result["stderr"],"diagnostic")

    def test_stdout_and_stderr_overflow_bounded_and_not_success(self):
        for stream in ("stdout","stderr"):
            with self.subTest(stream=stream):
                result=self.run_process("import sys; sys.%s.write('x'*100000); sys.%s.flush()"%(stream,stream))
                self.assertEqual(result["error"],"production_"+stream+"_overflow")
                self.assertTrue(result[stream+"_truncated"])
                self.assertEqual(result["raw_streams"][stream]["bytes"],1024 if stream=="stdout" else 256)

    def test_timeout_preserves_already_emitted_partial_output(self):
        result=self.run_process("import sys,time; print('partial',flush=True); time.sleep(10)",seconds=1)
        self.assertEqual(result["error"],"production_timeout");self.assertIn("partial",result["stdout"])
        self.assertLess(result["wall_seconds"],2)

    def test_closed_pipes_cannot_bypass_wall_clock_limit(self):
        result=self.run_process("import os,time; os.close(1); os.close(2); time.sleep(10)",seconds=1)
        self.assertEqual(result["error"],"production_timeout");self.assertLess(result["wall_seconds"],2)

    def test_large_stdin_and_output_do_not_deadlock(self):
        data=b"a"*200000
        result=self.run_process("import sys; sys.stdout.write('start\\n'); sys.stdout.flush(); data=sys.stdin.buffer.read(); print(len(data))",data)
        self.assertIsNone(result["error"]);self.assertIn("200000",result["stdout"])
