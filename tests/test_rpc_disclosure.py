import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from memorylab.contracts import canonical,sha256
from memorylab.roles import RoleInputs,EvidenceReader,role_config
from memorylab.rpc_disclosure import RPCDisclosure,SealedDisclosure,clone_service,disclosure_config,tool_specs
from memorylab.rpc_disclosure_fixture import build_fixture,verify_fixture
from memorylab.rpc_role import run_fixture,validate_sealed
from memorylab.runtime import RuntimeBuilder,RuntimeSession,read_runtime
from test_kernel import AT,Fixture

CLI=Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')


class RPCDisclosureTests(Fixture):
    def gateway(self,role='extractor',enabled=True,config=None):
        self.n=getattr(self,'n',0)+1
        ns='research/'+role if role in ('evaluator','proposer') else 'task/chain'
        src=self.source('source-'+str(self.n),ns=ns,text='Only integrations using this service need the dependency check. Other failures require diagnosis.')
        inputs=RoleInputs(self.cards,self.registry)
        if role in ('extractor','consolidation','reflector'):
            if role=='extractor':packet,service=inputs.extractor(ns,self.ledger.head(),[src])
            else:
                self.extract(self.raw_card([src],id='a'+str(self.n)),request='extract-a'+str(self.n),ns=ns)
                self.extract(self.raw_card([src],id='b'+str(self.n)),request='extract-b'+str(self.n),ns=ns)
                spec=self.registry.register_root('memory','m'+str(self.n),dict(self.kernel['memory_initial'],**{'consolidation.type':'single_index','reflection.trigger':'feedback'}))
                packet,service=inputs.derived(role,ns,self.ledger.head(),spec)
            if config:
                service=EvidenceReader(self.cards,role,ns,packet['payload']['cutoff'],[v['ref'] for v in service.catalog()],config)
        else:
            self.extract(self.raw_card([src],id='a'+str(self.n)),request='extract-a'+str(self.n),ns=ns)
            spec=self.registry.register_root('memory','m'+str(self.n))
            projection=RuntimeBuilder(self.cards,self.registry,config=config or read_runtime()).build(ns,self.ledger.head(),spec)['projection_ref']
            service=RuntimeSession(self.ledger,projection,'service integration',enabled=enabled);briefs=json.loads(service.briefs())
            if role=='solver':
                w=Path(self.tmp.name)/('worker-'+str(self.n));w.mkdir();(w/'SPEC.md').write_text('Inspect the integration service.')
                packet=inputs.task(role,ns,self.ledger.head(),w,{'id':'target'},briefs)
            else:
                parent=spec if role=='proposer' else self.registry.register_root('scenario','e'+str(self.n))
                packet=inputs.research(role,ns,self.ledger.head(),parent,[src],briefs)
        output=Path(self.tmp.name)/('disclosure-'+str(self.n))
        return RPCDisclosure(packet,service,output)

    def request(self,g,args=None,id_=7,call='call1',**changes):
        source=g.specs[0]['name']=='read_evidence'
        ids=[v['id'] for v in g.service.catalog()] if source else sorted(g.service.disclosed)
        p={'threadId':'thread1','turnId':'turn1','callId':call,'tool':g.specs[0]['name'],
            'arguments':args if args is not None else {'id':ids[0],**({'start':0,'count':128} if source else {})}}
        p.update(changes)
        return {'id':id_,'method':'item/tool/call','params':p}

    def test_registered_source_and_memory_roles_use_same_services_and_exact_replay(self):
        for role in ('extractor','consolidation','reflector','solver','evaluator','proposer'):
            with self.subTest(role=role):
                g=self.gateway(role);g.bind('thread1','turn1');q=self.request(g);reply=g.reply(q)
                self.assertTrue(reply['result']['success']);text=reply['result']['contentItems'][0]['text']
                self.assertIn('dependency' if role in ('extractor','consolidation','reflector') else 'health check',text)
                before=copy.deepcopy(g.service.events)
                report=g.verify([q],[reply]);self.assertEqual(g.service.events,before)
                self.assertFalse(report['formal_admission']);self.assertEqual(report['reply_bytes'],len(canonical(reply))+1)
                self.assertEqual(json.loads((g.output/'service.json').read_text()),g.service.events)

    def test_initial_reopen_cannot_refund_consumed_disclosure_or_hide_transport(self):
        for role in ('extractor','evaluator'):
            with self.subTest(role=role):
                g=self.gateway(role);initial=g.initial_identity
                reopened=RPCDisclosure.reopen_initial(g.packet,clone_service(g.initial),g.output)
                self.assertEqual(reopened.initial_identity,initial)
                g.bind('thread1','turn1');q=self.request(g);g.reply(q)
                before={p.name:p.read_bytes() for p in g.output.iterdir()}
                self.assert_code('rpc_disclosure_initial_resume_drift',
                    lambda:RPCDisclosure.reopen_initial(g.packet,clone_service(g.initial),g.output))
                self.assertEqual({p.name:p.read_bytes() for p in g.output.iterdir()},before)
                transport=g.output.parent/'transport';transport.mkdir()
                self.assert_code('rpc_admission_already_started',
                    lambda:RPCDisclosure.reopen_initial(g.packet,clone_service(g.initial),g.output))
                transport.rmdir()

    def test_actual_fresh_codex_dynamic_requests_disclose_and_reach_next_provider_input(self):
        identities=[]
        for role in ('extractor','consolidation','reflector','solver','evaluator','proposer'):
            with self.subTest(role=role):
                g=self.gateway(role);out=Path(self.tmp.name)/('native-'+role);result=run_fixture(CLI,out,g)
                self.assertEqual(result['status'],'actual_fresh_thread_fixture_chain_passed_not_formal',result.get('error'))
                self.assertEqual(result['role'],role);self.assertEqual(result['actual_model_calls'],0);self.assertFalse(result['formal_admission'])
                thread=result['thread_response']['thread']['id'];turn=result['turn_response']['turn']['id'];identities.append(thread)
                self.assertEqual(validate_sealed(out,thread,turn,g),result['event_result'])
                calls=json.loads((g.output/'calls.json').read_text())['calls'];self.assertEqual(len(calls),2)
                self.assertEqual([c['reply']['result']['success'] for c in calls],[True,False])
                data=json.loads((out/'provider-request-2.bin').read_bytes())
                actual=[x for x in data['input'] if x.get('type')=='function_call_output']
                self.assertEqual(len(actual),2)
                self.assertTrue(any('undeclared_role_evidence' in json.dumps(x) or 'undisclosed_memory_denied' in json.dumps(x) for x in actual))
                first=json.loads((out/'provider-request-1.bin').read_bytes())
                self.assertIn(g.packet['sha256'],canonical(first).decode())
        self.assertEqual(len(set(identities)),6)

    def test_wrong_identity_unknown_tool_and_duplicate_do_not_disclose(self):
        g=self.gateway();g.bind('thread1','turn1')
        for changes,code in [({'threadId':'other'},'rpc_disclosure_session'),({'turnId':'other'},'rpc_disclosure_session'),
            ({'tool':'open_memory'},'rpc_disclosure_tool_denied'),({'namespace':'other'},'rpc_disclosure_tool_denied')]:
            self.assert_code(code,lambda:g.reply(self.request(g,**changes)))
        self.assertEqual(g.service.events,[]);self.assertEqual(g.calls,[])
        q=self.request(g);g.reply(q);before=copy.deepcopy(g.service.events)
        self.assert_code('rpc_disclosure_duplicate',lambda:g.reply(q))
        self.assert_code('rpc_disclosure_duplicate',lambda:g.reply(self.request(g,id_=8)))
        self.assert_code('rpc_disclosure_duplicate',lambda:g.reply(self.request(g,call='new')))
        self.assertEqual(g.service.events,before)
        self.assert_code('rpc_disclosure_session_reused',lambda:g.bind('thread2','turn2'))
        for role in ('acquisition','oracle','grade','control'):self.assert_code('rpc_disclosure_role_denied',lambda:tool_specs(role))

    def test_illegal_arguments_finite_source_errors_empty_wire_and_off_memory(self):
        c=role_config();c['source_requests']=2;g=self.gateway(config=c);g.bind('thread1','turn1')
        args={'id':g.service.catalog()[0]['id'],'start':False,'count':128}
        a=g.reply(self.request(g,args));self.assertFalse(a['result']['success'])
        args.update(start=0,op='read_evidence')
        b=g.reply(self.request(g,args,id_=8,call='call2'));self.assertFalse(b['result']['success'])
        self.assertNotIn('dependency check',json.dumps(b))
        q=self.request(g,id_=9,call='call3');z=g.reply(q)
        self.assertEqual(z['result'],{'contentItems':[],'success':False});self.assertEqual(len(g.service.events),2)
        queries=[v['request'] for v in g.calls];replies=[v['reply'] for v in g.calls];g.verify(queries,replies)
        off=self.gateway('solver',enabled=False);off.bind('thread1','turn1')
        denied=off.reply(self.request(off,{'id':'undeclared'}));self.assertFalse(denied['result']['success'])

    def test_pending_audit_failure_and_reply_limit_never_grant_uncounted_content(self):
        g=self.gateway();g.bind('thread1','turn1');q=self.request(g)
        with patch('memorylab.rpc_disclosure.save',side_effect=OSError('private audit unavailable')):
            with self.assertRaises(OSError):g.reply(q)
        self.assertEqual(g.service.events,[])
        self.assert_code('rpc_disclosure_audit_drift',lambda:g.reply(q))
        # A different failed attempt is retained; the original output cannot be reset.
        self.assert_code('rpc_disclosure_output_exists',lambda:RPCDisclosure(g.packet,g.initial,g.output))
        h=self.gateway();h.bind('thread1','turn1');h.config['max_reply_bytes']=1;h._save()
        # Changing the published quota is itself a contract drift, before reads.
        self.assert_code('rpc_disclosure_audit_drift',lambda:h.reply(self.request(h)))

    def test_snapshot_audit_and_replay_mismatch_are_fail_closed(self):
        g=self.gateway();g.bind('thread1','turn1');q=self.request(g);r=g.reply(q)
        altered=copy.deepcopy(r);altered['result']['contentItems'][0]['text']='forged'
        self.assert_code('rpc_disclosure_wire_mismatch',lambda:g.verify([q],[altered]))
        self.assert_code('rpc_disclosure_wire_count',lambda:g.verify([],[]))
        g.service.records[next(iter(g.service.records))]['text']='changed source'
        self.assert_code('rpc_disclosure_service_drift',lambda:g.reply(self.request(g,id_=8,call='call2')))

    def test_sealed_snapshot_replay_all_roles_needs_no_live_gateway_and_has_no_send(self):
        for role in ('extractor','consolidation','reflector','solver','evaluator','proposer'):
            g=self.gateway(role);g.bind('thread1','turn1');q=self.request(g);r=g.reply(q)
            snapshot=SealedDisclosure(g.output,g.binding_sha256)
            self.assertEqual(snapshot.verify([q],[r]),g.verify([q],[r]))
            self.assertFalse(hasattr(snapshot,'reply'));self.assertFalse(hasattr(snapshot,'bind'))
            # Repeated verification starts from initial quotas, never the final
            # state, and doesn't alter any persisted audit bytes.
            before={p.name:p.read_bytes() for p in g.output.glob('*.json')}
            self.assertEqual(snapshot.verify([q],[r]),snapshot.verify([q],[r]))
            self.assertEqual(before,{p.name:p.read_bytes() for p in g.output.glob('*.json')})

    def test_frozen_outer_limits_and_persist_failure_leave_pending_without_release(self):
        for limit in ('max_reply_bytes','max_total_reply_bytes'):
            config=disclosure_config();config[limit]=1
            with patch('memorylab.rpc_disclosure.disclosure_config',side_effect=lambda:copy.deepcopy(config)):
                g=self.gateway();g.bind('thread1','turn1');q=self.request(g)
                self.assert_code('rpc_disclosure_reply_limit',lambda:g.reply(q))
                self.assertEqual(g.calls[0]['state'],'pending');self.assertEqual(g.total_bytes,0)
                self.assertEqual(len(g.service.events),1)
                self.assert_code('rpc_disclosure_pending',lambda:g.reply(self.request(g,id_=8,call='new')))
                sealed=SealedDisclosure(g.output,g.binding_sha256)
                self.assert_code('rpc_disclosure_pending',lambda:sealed.verify([q],[{}]))
        h=self.gateway();h.bind('thread1','turn1')
        def fail(events):raise OSError('source audit unavailable')
        h.service.persist=fail
        with self.assertRaises(OSError):h.reply(self.request(h))
        self.assertEqual(h.calls[0]['state'],'pending');self.assertEqual(h.service.events,[])

    def test_frozen_call_argument_snapshot_limits_refuse_before_service(self):
        config=disclosure_config();config['max_calls']=1;config['max_arguments_bytes']=256
        with patch('memorylab.rpc_disclosure.disclosure_config',side_effect=lambda:copy.deepcopy(config)):
            g=self.gateway();g.bind('thread1','turn1')
            self.assert_code('rpc_disclosure_arguments_limit',lambda:g.reply(self.request(g,{'id':'x'*257})))
            self.assertEqual(g.calls,[]);self.assertEqual(g.service.events,[])
            g.reply(self.request(g));before=copy.deepcopy(g.service.events)
            self.assert_code('rpc_disclosure_call_limit',lambda:g.reply(self.request(g,id_=8,call='new')))
            self.assertEqual(g.service.events,before)
        config=disclosure_config();config['max_snapshot_bytes']=1
        with patch('memorylab.rpc_disclosure.disclosure_config',side_effect=lambda:copy.deepcopy(config)):
            self.assert_code('rpc_disclosure_snapshot_limit',lambda:self.gateway())
            self.assertFalse((Path(self.tmp.name)/('disclosure-'+str(self.n))).exists())

    def test_quota_reset_disclosure_injection_and_resigned_audit_do_not_hide_drift(self):
        g=self.gateway();g.bind('thread1','turn1');q=self.request(g);r=g.reply(q)
        g.service.remaining+=1
        self.assert_code('rpc_disclosure_quota_drift',lambda:g.reply(self.request(g,id_=8,call='new')))
        # Even a matching edited in-memory/file audit cannot change the replay
        # result produced from the bound initial service.
        g.service.events[-1]['remaining_bytes']+=1
        (g.output/'service.json').write_text(json.dumps(g.service.events))
        self.assert_code('rpc_disclosure_service_audit_mismatch',lambda:g.verify([q],[r]))
        h=self.gateway('solver');h.bind('thread1','turn1');h.service.disclosed.add('undeclared')
        self.assert_code('rpc_disclosure_quota_drift',lambda:h.reply(self.request(h,id_=8,call='new')))

    def test_sealed_binding_reply_duplicates_and_unsafe_files_reject(self):
        g=self.gateway();g.bind('thread1','turn1');q=self.request(g);r=g.reply(q)
        self.assert_code('rpc_disclosure_snapshot_binding',lambda:SealedDisclosure(g.output,'0'*64))
        audit=json.loads((g.output/'calls.json').read_text());row=audit['calls'][0]
        altered=copy.deepcopy(r);altered['result']['contentItems'][0]['text']='forged'
        row['reply']=altered
        (g.output/'calls.json').write_text(json.dumps(audit))
        sealed=SealedDisclosure(g.output,g.binding_sha256)
        self.assert_code('rpc_disclosure_replay_mismatch',lambda:sealed.verify([q],[altered]))
        row['reply']=r;audit['calls'].append(copy.deepcopy(row));audit['total_reply_bytes']*=2
        (g.output/'calls.json').write_text(json.dumps(audit));sealed=SealedDisclosure(g.output,g.binding_sha256)
        self.assert_code('rpc_disclosure_duplicate',lambda:sealed.verify([q,q],[r,r]))
        initial=g.output/'initial.json';safe=g.output/'saved-initial.json';initial.rename(safe);initial.symlink_to(safe)
        self.assert_code('unsafe_workspace_file',lambda:SealedDisclosure(g.output,g.binding_sha256))

    def test_persisted_native_fixture_verifies_in_a_new_process_after_controller_closed(self):
        root=Path(self.tmp.name)/'persistent';g=build_fixture('solver',root)
        result=run_fixture(CLI,root/'transport',g)
        self.assertEqual(result['status'],'actual_fresh_thread_fixture_chain_passed_not_formal',result.get('error'))
        before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
        first=verify_fixture(root)
        completed=subprocess.run([sys.executable,'-m','memorylab.rpc_disclosure_fixture','verify','--output',str(root)],
            cwd=Path(__file__).resolve().parent.parent,capture_output=True,text=True,timeout=10)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertEqual(json.loads(completed.stdout),first)
        self.assertEqual(before,{str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()})
        events=copy.deepcopy(result['event_result']['dynamic'])
        stdout=root/'transport'/'stdout.bin';raw=stdout.read_bytes()
        messages=[json.loads(v) for v in raw.splitlines()]
        item=next(m['params']['item'] for m in messages if m.get('method')=='item/completed' and m['params']['item']['type']=='dynamicToolCall')
        item['contentItems']=[{'type':'inputText','text':'forged'}]
        changed=b'\n'.join(canonical(m) for m in messages)+b'\n';stdout.write_bytes(changed)
        exit_path=root/'transport'/'exit.json';exit_raw=exit_path.read_bytes();exit_record=json.loads(exit_raw)
        exit_record['raw_hashes']['stdout.bin']=hashlib.sha256(changed).hexdigest();exit_path.write_text(json.dumps(exit_record))
        self.assert_code('rpc_role_dynamic_item_binding',lambda:verify_fixture(root))
        stdout.write_bytes(raw);exit_path.write_bytes(exit_raw)
        config=root/'transport'/'generated-config.toml';config_raw=config.read_bytes();config.write_bytes(config_raw+b'\n')
        self.assert_code('rpc_role_config_drift',lambda:verify_fixture(root));config.write_bytes(config_raw)
        recipe_path=root/'transport'/'recipe.json';recipe_raw=recipe_path.read_bytes();recipe=json.loads(recipe_raw);recipe['prompt']='forged'
        recipe_path.write_text(json.dumps(recipe));self.assert_code('rpc_role_prompt_binding',lambda:verify_fixture(root));recipe_path.write_bytes(recipe_raw)
        from memorylab.rpc_role import validate_events
        notifications=[m for m in messages if 'id' not in m]
        # A completed item with an unrelated side effect is also forbidden.
        notifications.append({'method':'item/completed','params':{'threadId':g.thread,'turnId':g.turn,
            'item':{'id':'unexpected','type':'fileChange'}}})
        self.assert_code('rpc_role_unexpected_item',lambda:validate_events(notifications,g.thread,g.turn,2))
        self.assertEqual(events,result['event_result']['dynamic'])
