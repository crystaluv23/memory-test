import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memorylab import sandbox
from memorylab.roles import role_config,run_canary
from test_kernel import Fixture


class AuthenticationIsolationTests(Fixture):
    def test_fake_home_credentials_denied_without_reading_real_credentials(self):
        with tempfile.TemporaryDirectory() as temporary,tempfile.TemporaryDirectory(dir="/tmp") as worker:
            home=Path(temporary)
            paths=[home/".codex/auth.json",home/".config/example-tool/fixture.key"]
            for path in paths:path.parent.mkdir(parents=True,exist_ok=True);path.write_text("harmless-marker")
            script='''import json,pathlib,sys
results=[]
for name in sys.argv[1:]:
 try:pathlib.Path(name).read_bytes();results.append(False)
 except PermissionError:results.append(True)
print(json.dumps(results))
'''
            with patch("memorylab.sandbox.Path.home",return_value=home):
                command=sandbox.command(worker,Path(self.tmp.name),[sys.executable,"-I","-B","-c",script]+[str(p) for p in paths],True,False)
            result=subprocess.run(command,cwd=worker,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),[True,True])

    def test_even_packet_approval_cannot_bypass_unqualified_authentication(self):
        output=Path(self.tmp.name)/"must-not-start"
        packet={"sha256":"explicit-approved-fixture-not-a-model-request"}
        with patch("memorylab.roles.codex_command",side_effect=AssertionError("no paid command before authentication qualification")):
            self.assert_code("role_authentication_not_qualified",lambda:run_canary(role_config(),Path(self.tmp.name),Path(self.tmp.name),packet,{},output,packet["sha256"],True))
        self.assertFalse(output.exists())
