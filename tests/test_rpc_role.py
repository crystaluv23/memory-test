import copy
import hashlib
import http.client
import json
import sys
import time
from pathlib import Path

from memorylab.rpc_role import FreshRoleRPC,FixtureProvider,role_config,run_fixture,validate_events,validate_sealed,PROMPT
from test_kernel import Fixture

CLI=Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')


class RPCRoleTests(Fixture):
    def params(self):
        root=str(Path(self.tmp.name).resolve())
        return {'model':'memorylab-fixture','modelProvider':'memorylab-fixture','cwd':root,'approvalPolicy':'never',
            'permissions':'probe-restricted','ephemeral':True,'experimentalRawEvents':False,'runtimeWorkspaceRoots':[root]}

    def rpc(self,code,config=None,params=None):
        self.n=getattr(self,'n',0)+1;out=Path(self.tmp.name)/('rpc-'+str(self.n));out.mkdir()
        return FreshRoleRPC([sys.executable,'-I','-B','-c',code],Path(self.tmp.name).resolve(),{},config or role_config(),out,params or self.params())

    def test_actual_codex_fresh_thread_builtin_output_next_request_and_sealed_events(self):
        identities=[]
        for n in range(2):
            output=Path(self.tmp.name)/('native-'+str(n));result=run_fixture(CLI,output)
            self.assertEqual(result['status'],'actual_fresh_thread_fixture_chain_passed_not_formal',result.get('error'))
            self.assertTrue(all(result['checks'].values()));self.assertEqual(result['actual_model_calls'],0)
            self.assertFalse(result['authentication_qualified']);self.assertFalse(result['provider_budget_qualified']);self.assertFalse(result['formal_admission'])
            thread=result['thread_response']['thread'];identities.append(thread['id']);self.assertTrue(thread['ephemeral']);self.assertIsNone(thread['path'])
            provider=json.loads((output/'provider.json').read_text());self.assertEqual(provider['attempt_count'],2);self.assertEqual(provider['request_count'],2);self.assertEqual(provider['errors'],[])
            budget=provider['budget_report'];self.assertEqual((budget['requests'],budget['dispatched'],budget['input_tokens'],budget['output_tokens']),(2,2,200,20))
            self.assertFalse(budget['provider_budget_qualified']);self.assertEqual(budget['unknown_requests'],0)
            self.assertTrue(all(m['authorization_present'] is False and m['content_type_is_json'] is True for m in provider['http_metadata']))
            self.assertTrue(all(hashlib.sha256((output/k).read_bytes()).hexdigest()==v for k,v in provider['raw_hashes'].items()))
            first=json.loads((output/'provider-request-1.bin').read_bytes());second=json.loads((output/'provider-request-2.bin').read_bytes())
            self.assertNotIn('max_output_tokens',first)
            for i,request in enumerate((first,second),1):
                forwarded=json.loads((output/('provider-forwarded-%d.bin'%i)).read_bytes())
                self.assertEqual(forwarded,{**request,'max_output_tokens':1024,'truncation':'disabled'})
            self.assertEqual(first['model'],'memorylab-fixture');self.assertIn(PROMPT,json.dumps(first))
            actual=next(x['output'] for x in second['input'] if x.get('type')=='function_call_output')
            self.assertEqual(actual,provider['tool_output']);self.assertIn('"network_denied": true',actual)
            wire=json.loads((output/'wire.json').read_text());requests=[r['message'] for r in wire if r['direction']=='request']
            self.assertEqual([r['method'] for r in requests],['initialize','initialized','thread/start','turn/start'])
            self.assertEqual([json.loads(line) for line in (output/'stdin.bin').read_bytes().splitlines()],requests)
            events=json.loads((output/'events.json').read_text())
            self.assertEqual(validate_events(events,thread['id'],result['turn_response']['turn']['id']),result['event_result'])
            self.assertEqual(validate_sealed(output,thread['id'],result['turn_response']['turn']['id']),result['event_result'])
            for mutation,code in [('duplicate','rpc_role_turn_lifecycle'),('wrong_thread','rpc_role_event_thread'),('wrong_turn','rpc_role_event_turn'),
                ('no_usage','rpc_role_usage_missing'),('bad_usage','rpc_role_usage_mismatch'),('error','rpc_role_error_event')]:
                changed=copy.deepcopy(events)
                if mutation=='duplicate':changed.append(next(e for e in changed if e['method']=='turn/completed'))
                elif mutation=='wrong_thread':next(e for e in changed if 'threadId' in e['params'])['params']['threadId']='other'
                elif mutation=='wrong_turn':next(e for e in changed if 'turnId' in e['params'])['params']['turnId']='other'
                elif mutation=='no_usage':changed=[e for e in changed if e['method']!='thread/tokenUsage/updated']
                elif mutation=='bad_usage':next(e for e in changed if e['method']=='thread/tokenUsage/updated')['params']['tokenUsage']['last']['inputTokens']=-1
                else:changed.append({'method':'error','params':{'threadId':thread['id']}})
                self.assert_code(code,lambda:validate_events(changed,thread['id'],result['turn_response']['turn']['id']))
            before=(output/'result.json').read_bytes();self.assert_code('rpc_role_output_exists',lambda:run_fixture(CLI,output));self.assertEqual((output/'result.json').read_bytes(),before)
            raw=(output/'stdout.bin').read_bytes();(output/'stdout.bin').write_bytes(raw+b'{}\n')
            self.assert_code('rpc_role_raw_drift',lambda:validate_sealed(output,thread['id'],result['turn_response']['turn']['id']))
            (output/'stdout.bin').write_bytes(raw)
            exited=(output/'exit.json').read_bytes();bad=json.loads(exited);bad['raw_hashes']['../../outside']=bad['raw_hashes']['stdin.bin']
            (output/'exit.json').write_text(json.dumps(bad))
            self.assert_code('rpc_role_raw_fields',lambda:validate_sealed(output,thread['id'],result['turn_response']['turn']['id']))
            (output/'exit.json').write_bytes(exited)
        self.assertNotEqual(identities[0],identities[1])

    def test_unbound_recipe_permissions_resume_or_second_turn_never_written(self):
        p=self.params();rpc=self.rpc('import sys;sys.stdin.read()',params=p)
        try:
            p['permissions']=':danger-full-access'
            for method in ('thread/resume','thread/fork','turn/steer','account/login/start','command/exec'):
                self.assert_code('rpc_role_method_forbidden',lambda:rpc.call(method,{}))
            self.assert_code('rpc_role_request_not_bound',lambda:rpc.call('thread/start',p))
            self.assert_code('rpc_role_request_order',lambda:rpc.call('thread/start',rpc.recipe('thread/start')))
            self.assertEqual(rpc.records,[])
            self.assertNotEqual(rpc.recipe('thread/start')['permissions'],p['permissions'])
        finally:rpc.close()
        self.assertEqual((rpc.output/'stdin.bin').read_bytes(),b'')
        self.assertIsNotNone(rpc.process.poll())
        p=self.params();p['ephemeral']=False
        self.assert_code('rpc_role_thread_recipe',lambda:self.rpc('',params=p))

    def test_wrong_rpc_response_server_request_and_actual_timeout_are_not_completion(self):
        for message,code in [({'id':True,'result':{}},'rpc_role_response_id'),({'id':1,'error':{}},'rpc_role_rpc_error'),
            ({'id':55,'method':'item/tool/call','params':{}},'rpc_role_response_id')]:
            rpc=self.rpc('import sys;sys.stdin.readline();print('+repr(json.dumps(message))+',flush=True)')
            try:self.assert_code(code,lambda:rpc.call('initialize',rpc.recipe('initialize')))
            finally:rpc.close()
            self.assertIn(json.dumps(message).encode(),(rpc.output/'stdout.bin').read_bytes());self.assertIsNotNone(rpc.process.poll())
        c=role_config();c['timeout_seconds']=1;rpc=self.rpc('import sys,time;sys.stdin.readline();time.sleep(60)',c)
        try:self.assert_code('rpc_role_timeout',lambda:rpc.call('initialize',rpc.recipe('initialize')))
        finally:rpc.close()
        self.assertIsNotNone(rpc.process.poll());before=(rpc.output/'exit.json').read_bytes();rpc.close();self.assertEqual((rpc.output/'exit.json').read_bytes(),before)

    def test_fixture_wrong_model_missing_output_and_third_attempt_are_failures_with_raw(self):
        for first in ({'model':'real-model','input':[],'tools':[]},{'model':'memorylab-fixture','input':[],'tools':[{'type':'function','name':'exec_command'}]}):
            self.n=getattr(self,'n',0)+1;out=Path(self.tmp.name)/('provider-'+str(self.n));out.mkdir();provider=FixtureProvider(role_config(),out,'literal')
            try:
                def post(body):
                    c=http.client.HTTPConnection('127.0.0.1',provider.server.server_port,timeout=3)
                    c.request('POST','/responses',json.dumps(body),{'Content-Type':'application/json'});r=c.getresponse();status=r.status;r.read();c.close();return status
                self.assertEqual(post(first),400 if first['model']=='real-model' else 200)
                self.assertEqual(post({'model':'memorylab-fixture','input':[]}),400)
                self.assertEqual(post(first),400)
            finally:provider.close()
            record=json.loads((out/'provider.json').read_text());self.assertEqual(record['attempt_count'],3);self.assertTrue(record['errors'])
            self.assertEqual(json.loads((out/'provider-request-1.bin').read_bytes()),first)
            before=(out/'provider.json').read_bytes();provider.close();self.assertEqual((out/'provider.json').read_bytes(),before)

    def test_absolute_deadline_survives_notification_stream_and_refuses_new_sends(self):
        c=role_config();c['timeout_seconds']=1
        code='import sys,time,json;sys.stdin.readline()\nwhile True:\n print(json.dumps({"method":"fixture/noise","params":{}}),flush=True);time.sleep(.01)'
        rpc=self.rpc(code,c);started=time.monotonic()
        try:
            self.assert_code('rpc_role_timeout',lambda:rpc.call('initialize',rpc.recipe('initialize')))
            self.assertLess(time.monotonic()-started,3);self.assertTrue(rpc.events)
            sent=(rpc.output/'stdin.bin').read_bytes()
            rpc.pending=bytearray(b'{}\n')
            self.assert_code('rpc_role_timeout',lambda:rpc.next_message(time.monotonic()+10))
            self.assert_code('rpc_role_timeout',lambda:rpc.call('initialized',{},notify=True))
            self.assert_code('rpc_role_timeout',lambda:rpc.server_request({'method':'item/tool/call'}))
            self.assertEqual((rpc.output/'stdin.bin').read_bytes(),sent)
        finally:rpc.close()
        self.assertIsNotNone(rpc.process.poll())

    def test_http_method_path_auth_encoding_size_and_invalid_json_are_bounded_failures_without_credentials(self):
        cases=[('GET','/responses',{},'',405,'rpc_fixture_method'),('POST','/wrong',{},'{}',400,'rpc_fixture_path'),
            ('POST','/responses',{'Authorization':'harmless-header-marker-never-save'},'{}',400,'rpc_fixture_authentication_forbidden'),
            ('POST','/responses',{'Content-Encoding':'gzip'},'{}',400,'rpc_fixture_content_encoding'),
            ('POST','/responses',{'X-Large':'x'*9000},'{}',400,'rpc_fixture_header_size'),
            ('POST','/responses',{},'{invalid',400,'JSONDecodeError')]
        for i,(method,path,headers,body,status,error) in enumerate(cases):
            with self.subTest(error=error):
                out=Path(self.tmp.name)/('http-'+str(i));out.mkdir();provider=FixtureProvider(role_config(),out,'literal')
                try:
                    c=http.client.HTTPConnection('127.0.0.1',provider.server.server_port,timeout=3)
                    c.request(method,path,body,{'Content-Type':'application/json',**headers});r=c.getresponse();self.assertEqual(r.status,status);r.read();c.close()
                finally:provider.close()
                record=json.loads((out/'provider.json').read_text());self.assertEqual(record['attempt_count'],1)
                self.assertEqual(record['errors'][0]['code'],error);self.assertEqual(record['budget_report']['dispatched'],0)
                self.assertNotIn('harmless-header-marker-never-save',(out/'provider.json').read_text())
