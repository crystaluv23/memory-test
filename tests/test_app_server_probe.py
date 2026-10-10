import copy
import json
import sys
from pathlib import Path

from memorylab.app_server_probe import METHODS,ProbeRPC,probe_config,run_probe
from test_kernel import Fixture

CLI=Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')


class AppServerProbeTests(Fixture):
    def client(self,code,config=None,commands=()):
        root=Path(self.tmp.name);self.sequence=getattr(self,'sequence',0)+1;output=root/('wire-'+str(self.sequence));output.mkdir()
        return ProbeRPC([sys.executable,'-I','-B','-c',code],root,{},config or probe_config(),output,commands)

    def test_actual_pinned_cli_command_boundary_positive_negative_controls_no_model_or_auth_admission(self):
        output=Path(self.tmp.name)/'native'
        result=run_probe(CLI,output)
        self.assertEqual(result['status'],'command_boundary_passed_not_authentication_qualified',result.get('error'))
        self.assertEqual(result['checks']['private_read_errors'],['PermissionError','PermissionError'])
        for k in ('workspace_read','workspace_write','environment_isolated','private_write_denied','positive_private_read',
            'control_socket_denied','network_denied','network_target_reachable','control_socket_reachable'):
            self.assertIs(result['checks'][k],True,k)
        self.assertEqual(result['actual_model_calls'],0);self.assertFalse(result['authentication_qualified']);self.assertFalse(result['provider_budget_qualified'])
        wire=json.loads((output/'wire.json').read_text())
        requests=[r['message'] for r in wire if r['direction']=='request']
        self.assertTrue(requests);self.assertTrue(all(r['method'] in METHODS for r in requests))
        commands=[r['params'] for r in requests if r['method']=='command/exec']
        self.assertEqual([r['permissionProfile'] for r in commands],['probe-restricted','probe-positive'])
        self.assertTrue(all('sandboxPolicy' not in r for r in commands))
        start=json.loads((output/'start.json').read_text())
        self.assertNotIn('--sandbox',start['command']);self.assertNotIn('--ignore-user-config',start['command'])
        self.assertEqual(json.loads((output/'exit.json').read_text())['exit_code'],0)
        actual_requests=[json.loads(line) for line in (output/'stdin.bin').read_bytes().splitlines()]
        self.assertEqual(actual_requests,requests)
        responses=[r['message'] for r in wire if r['direction']=='response']
        self.assertEqual([json.loads(line) for line in (output/'stdout.bin').read_bytes().splitlines()],responses)
        before=(output/'result.json').read_bytes()
        self.assert_code('app_server_probe_output_exists',lambda:run_probe(CLI,output))
        self.assertEqual((output/'result.json').read_bytes(),before)

    def test_paid_methods_and_unbound_or_mutated_commands_are_rejected_before_write(self):
        bound={'command':['literal-fixed-probe'],'permissionProfile':'probe-restricted'}
        rpc=self.client('import sys;sys.stdin.read()',commands=(bound,))
        try:
            for method in ('thread/start','turn/start','account/login/start','model/list'):
                self.assert_code('app_server_probe_method_forbidden',lambda:rpc.call(method,{}))
            changed=copy.deepcopy(bound);changed['command']=['codex','exec','not-a-probe']
            self.assert_code('app_server_probe_command_not_bound',lambda:rpc.call('command/exec',changed))
            bound['permissionProfile']=':danger-full-access'
            self.assert_code('app_server_probe_command_not_bound',lambda:rpc.call('command/exec',bound))
            self.assertEqual(rpc.records,[])
        finally:rpc.close()
        self.assertEqual(json.loads((rpc.output/'wire.json').read_text()),[])
        self.assertEqual((rpc.output/'stdin.bin').read_bytes(),b'')

    def test_wrong_id_error_and_paid_notification_cannot_become_success(self):
        messages=[({'id':True,'result':{}},'unexpected_app_server_probe_response'),
            ({'id':99,'result':{}},'unexpected_app_server_probe_response'),
            ({'id':1,'error':{'code':-1,'message':'actual failure'}},'app_server_probe_rpc_error'),
            ({'method':'turn/started','params':{}},'unexpected_app_server_probe_notification')]
        for value,code in messages:
            with self.subTest(code=code,value=value):
                program='import json,sys;sys.stdin.readline();print('+repr(json.dumps(value))+',flush=True)'
                rpc=self.client(program)
                try:self.assert_code(code,lambda:rpc.call('initialize',{}))
                finally:rpc.close()
                raw=json.loads((rpc.output/'wire.json').read_text())
                self.assertEqual(raw[1]['message'],value)
                self.assertIsNotNone(rpc.process.poll())

    def test_overflow_and_silent_exit_preserve_exit_proof_without_an_inference_retry(self):
        c=probe_config();c['max_wire_bytes']=256
        rpc=self.client('import sys;sys.stdin.readline();print("x"*4096,flush=True)',c)
        try:self.assert_code('app_server_probe_wire_overflow',lambda:rpc.call('permissionProfile/list'))
        finally:rpc.close()
        exit_=json.loads((rpc.output/'exit.json').read_text())
        self.assertTrue(exit_['wire_limit_exceeded']);self.assertIsNotNone(rpc.process.poll())
        wire=json.loads((rpc.output/'wire.json').read_text());overflow=next(r for r in wire if r['direction']=='wire_overflow')
        self.assertEqual(overflow['chunk_bytes'],4097);self.assertLess(overflow['kept_bytes'],overflow['chunk_bytes'])
        self.assertTrue((rpc.output/'stdout.bin').read_bytes().startswith(b'x'))
        rpc=self.client('import sys;sys.stdin.readline()')
        try:self.assert_code('app_server_probe_process_exited',lambda:rpc.call('initialize',{}))
        finally:rpc.close()
        self.assertEqual(len([r for r in rpc.records if r['direction']=='request']),1)
        self.assertEqual(rpc.process.returncode,0)

    def test_timeout_terminates_the_actual_live_handle_and_close_is_idempotent(self):
        c=probe_config();c['timeout_seconds']=1
        rpc=self.client('import sys,time;sys.stdin.readline();time.sleep(60)',c)
        try:self.assert_code('app_server_probe_timeout',lambda:rpc.call('initialize',{}))
        finally:rpc.close()
        self.assertIsNotNone(rpc.process.poll())
        before=(rpc.output/'wire.json').read_bytes();rpc.close()
        self.assertEqual((rpc.output/'wire.json').read_bytes(),before)
