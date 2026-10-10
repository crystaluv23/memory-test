import base64
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from memorylab.admission import resolve_capture
from memorylab.cards import CanonicalCards,EvidenceStore
from memorylab.contracts import canonical,sha256
from memorylab.ledger import Ledger
from memorylab.registry import Registry
from memorylab.rpc_admission import RPCAdmission
from memorylab.rpc_output import validate_output
from memorylab.rpc_role import run_fixture,validate_sealed
from memorylab.runtime import RuntimeBuilder,RuntimeSession
from test_kernel import AT,Fixture
from test_rpc_disclosure import CLI
import test_rpc_disclosure as disclosure_fixtures


class RPCAdmissionTests(Fixture):
    def gateway(self,role='extractor'):
        g=disclosure_fixtures.RPCDisclosureTests.gateway(self,role)
        root=Path(self.tmp.name)/('native-root-'+str(self.n));root.mkdir()
        g.output.rename(root/'disclosure');g.output=root/'disclosure'
        return g

    def final(self,g):
        p=g.packet['payload'];role=p['role']
        if role=='extractor':
            source=p['context']['sources'][0]['ref']
            return {'cards':[self.raw_card([source],id='native-'+str(self.n),outcome='unknown',
                detail='RPC_PRIVATE_PRESET-'+str(self.n)+': apply only when this integration uses the service; diagnose unrelated failures separately.')]}
        if role=='consolidation':
            refs=[c['ref'] for c in p['context']['derived_input']['payload']['cards']]
            return {'indices':[{'type_hint':'environment','brief':'Service integration checks','detail':'Navigation to the relevant conditional checks.','support_refs':refs}]}
        if role=='reflector':
            return {'annotations':[{'support':p['context']['derived_input']['payload']['eligible_refs'][0],
                'text':'RPC_PRIVATE_PRESET-'+str(self.n)+': verify the service precondition before applying this rule.'}]}
        return self.proposal(p['context']['parent'],**({'field':'context_profile','value':'pressure'} if role=='evaluator' else {}))

    def run_native(self,g,final=None,label=None):
        admission=RPCAdmission(self.cards,self.registry);label=label or 'native-'+str(self.n)
        attempt=admission.prepare('prepare-'+label,g,CLI)['result']['attempt']
        result=run_fixture(CLI,g.output.parent/'transport',g,self.final(g) if final is None else final)
        return admission,attempt,result,label

    def test_actual_native_five_role_outputs_materialize_through_original_production_methods(self):
        threads=[]
        for role in ('extractor','consolidation','reflector','evaluator','proposer'):
            with self.subTest(role=role):
                g=self.gateway(role);admission,attempt,native,label=self.run_native(g)
                self.assertEqual(native['status'],'actual_fresh_thread_fixture_chain_passed_not_formal',native.get('error'))
                receipt=admission.complete('complete-'+label,attempt,AT);result=receipt['result']
                self.assertEqual(result['outcome'],'materialized',result['code']);self.assertFalse(result['formal_admission']);self.assertFalse(result['promotion_eligible'])
                capture=resolve_capture(self.ledger,result['capture']);threads.append(capture['transport']['thread_id'])
                self.assertEqual(capture['transport_kind'],'codex_rpc_fixture')
                self.assertEqual(base64.b64decode(capture['raw_events_base64']),(g.output.parent/'transport'/'stdout.bin').read_bytes())
                self.assertEqual(base64.b64decode(capture['raw_stdin_base64']),(g.output.parent/'transport'/'stdin.bin').read_bytes())
                first=json.loads((g.output.parent/'transport'/'provider-request-1.bin').read_text())
                self.assertNotIn('RPC_PRIVATE_PRESET-'+str(self.n)+':',canonical(first).decode())
                self.assertEqual(first['text']['format']['schema'],g.packet['payload']['output_schema'])
                provider=json.loads((g.output.parent/'transport'/'provider.json').read_text());self.assertEqual(provider['role'],role)
                self.assertTrue(all(v['slot_id']==role for v in provider['budget_report']['rows']))
                before=self.ledger.audit();self.assertEqual(admission.complete('complete-'+label,attempt,AT),receipt);self.assertEqual(self.ledger.audit(),before)
                if role=='extractor':
                    m=self.registry.register_root('memory','consumed-'+str(self.n))
                    projection=RuntimeBuilder(self.cards,self.registry).build(g.packet['payload']['namespace'],self.ledger.head(),m)
                    session=RuntimeSession(self.ledger,projection['projection_ref'],'service integration')
                    briefs=json.loads(session.briefs());self.assertTrue(briefs['items'])
                    self.assertIn('RPC_PRIVATE_PRESET',json.loads(session.open({'op':'open_memory','id':briefs['items'][0]['id']}))['memory']['detail'])
                self.ledger.close()
                def snapshot():return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(self.tmp.name).rglob('*') if p.is_file()}
                sealed=snapshot()
                with Ledger(self.db_path,readonly=True) as readonly:
                    registry=Registry(readonly,self.kernel);cards=CanonicalCards(EvidenceStore(readonly,self.kernel))
                    self.assertEqual(RPCAdmission(cards,registry).verify('complete-'+label),receipt)
                    self.assertEqual(RPCAdmission(cards,registry).verify('complete-'+label),receipt)
                    self.assert_code('readonly_ledger_write_denied',lambda:registry.register_root('memory','unauthorized'))
                self.assertEqual(snapshot(),sealed)
                self.ledger=Ledger(self.db_path);self.registry=Registry(self.ledger,self.kernel)
                self.evidence=EvidenceStore(self.ledger,self.kernel);self.cards=CanonicalCards(self.evidence)
        self.assertEqual(len(set(threads)),5)

    def test_atomic_crashes_roll_back_capture_and_materialization_then_reuse_same_native_output(self):
        g=self.gateway();admission,attempt,native,label=self.run_native(g)
        self.assertEqual(native['status'],'actual_fresh_thread_fixture_chain_passed_not_formal',native.get('error'))
        before=self.ledger.audit();raw=(g.output.parent/'transport'/'stdout.bin').read_bytes()
        for phase in ('raw','materialized','receipt'):
            def fail(current):
                if current==phase:raise RuntimeError('controller interrupted')
            with self.assertRaises(RuntimeError):admission.complete('finish-atomic',attempt,AT,fail)
            self.assertEqual(self.ledger.audit(),before);self.assertFalse(self.cards.active('task/chain',self.ledger.head()))
        result=admission.complete('finish-atomic',attempt,AT)['result'];self.assertEqual(result['outcome'],'materialized')
        self.assertEqual((g.output.parent/'transport'/'stdout.bin').read_bytes(),raw)
        self.assert_code('role_attempt_already_completed',lambda:admission.complete('different-finish',attempt,AT))

    def test_bad_schema_and_semantically_unsupported_outcome_retain_failed_raw_without_cards(self):
        for condition in ('schema','outcome'):
            g=self.gateway();final=self.final(g)
            if condition=='schema':final['cards'][0]['expected_version']=False
            else:final['cards'][0]['outcome']='success'
            admission,attempt,native,label=self.run_native(g,final)
            if condition=='schema':self.assertEqual(native['error']['code'],'rpc_role_output_schema')
            result=admission.complete('complete-'+label,attempt,AT)['result']
            self.assertEqual(result['outcome'],'rejected')
            self.assertEqual(result['code'],'rpc_role_output_schema' if condition=='schema' else 'unsupported_extraction_outcome')
            self.assertFalse(self.cards.active('task/chain',self.ledger.head()))
            capture=self.ledger.get(result['capture']);self.assertTrue(base64.b64decode(capture['raw_events_base64']))
            self.assertEqual(admission.verify('complete-'+label)['result'],result)

    def test_pending_failed_and_materialized_slots_keep_the_full_report_denominator(self):
        for status in ('pending','failed','materialized'):
            g=self.gateway();a=RPCAdmission(self.cards,self.registry)
            attempt=a.prepare('prepare-'+status,g,CLI)['result']['attempt']
            if status=='pending':continue
            final=self.final(g)
            if status=='failed':final['cards'][0]['outcome']='success'
            run_fixture(CLI,g.output.parent/'transport',g,final)
            a.complete('complete-'+status,attempt,AT)
        report=a.report();self.assertEqual((report['planned'],report['terminal']),(3,2))
        self.assertEqual(sorted(r['status'] for r in report['rows']),['materialized','rejected','started'])
        self.assertFalse(report['formal_admission']);self.assertEqual(report['infrastructure_model_tokens'],0)

    def test_readonly_kernel_lookup_cannot_create_or_change_a_contract(self):
        self.ledger.close()
        before=self.db_path.read_bytes()
        with Ledger(self.db_path,readonly=True) as readonly:
            changed=copy.deepcopy(self.kernel);changed['limits']['extract_cards_per_batch']+=1
            self.assert_code('reference_hash_mismatch',lambda:readonly.bind_kernel(changed))
        self.assertEqual(self.db_path.read_bytes(),before)
        empty=Path(self.tmp.name)/'empty.sqlite'
        with Ledger(empty):pass
        before=empty.read_bytes()
        with Ledger(empty,readonly=True) as readonly:
            self.assert_code('unresolved_reference',lambda:Registry(readonly,self.kernel))
        self.assertEqual(empty.read_bytes(),before)

    def test_another_legal_materialization_and_mutated_raw_cannot_replace_committed_output(self):
        g=self.gateway();a,attempt,native,label=self.run_native(g)
        receipt=a.complete('complete-'+label,attempt,AT);result=receipt['result'];capture=self.ledger.get(result['capture'])
        transport,sources,memories,disclosed=a._verified(a._attempt(attempt),g.output.parent,result['capture'])
        other=self.cards.extract('unrelated-legal',{'cards':[]},'task/chain',self.ledger.head(),AT)
        changed=copy.deepcopy(result);changed['materialization']=other
        self.assert_code('rpc_admission_materialization_drift',lambda:a._verify_materialization(a._attempt(attempt),changed,capture,transport,disclosed))
        raw=g.output.parent/'transport'/'stdout.bin';raw.write_bytes(raw.read_bytes()+b'{}\n')
        self.assert_code('rpc_admission_capture_drift',lambda:a.verify('complete-'+label))

    def test_persisted_cli_run_and_separate_process_verify_are_read_only(self):
        for role in ('extractor','proposer'):
            root=Path(self.tmp.name)/('persisted-'+role)
            command=[sys.executable,'-B','-m','memorylab.rpc_materialization_fixture']
            proc=subprocess.run(command+['run','--role',role,'--cli',str(CLI),'--output',str(root)],capture_output=True,timeout=60)
            self.assertEqual(proc.returncode,0,proc.stderr.decode())
            result=json.loads(proc.stdout);self.assertEqual(result['status'],'typed_native_rpc_materialized_not_formal')
            before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            proc=subprocess.run(command+['verify','--output',str(root)],capture_output=True,timeout=20)
            self.assertEqual(proc.returncode,0,proc.stderr.decode());verified=json.loads(proc.stdout)
            self.assertEqual(verified,result['read_only_verification']);self.assertEqual(verified['denominator']['planned'],1)
            self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()})

    def test_sealed_final_schema_or_input_mutation_and_missing_handle_never_commit(self):
        g=self.gateway();admission,attempt,native,label=self.run_native(g)
        self.assertEqual(native['status'],'actual_fresh_thread_fixture_chain_passed_not_formal',native.get('error'))
        handle=g.output.parent/'transport'/'handle'/'process.json';raw=handle.read_bytes();handle.unlink()
        self.assert_code('rpc_role_exit_unverified',lambda:admission.complete('missing-handle',attempt,AT));handle.write_bytes(raw)
        first=g.output.parent/'transport'/'provider-request-1.bin';original=first.read_bytes();data=json.loads(original)
        data['text']['format']['strict']=False;changed=canonical(data);first.write_bytes(changed)
        provider=g.output.parent/'transport'/'provider.json';p=json.loads(provider.read_text());p['raw_hashes'][first.name]=hashlib.sha256(changed).hexdigest();provider.write_text(json.dumps(p))
        result=admission.complete('changed-schema',attempt,AT)['result'];self.assertEqual(result['code'],'rpc_role_provider_schema')
        self.assertFalse(self.cards.active('task/chain',self.ledger.head()))

    def test_schema_validation_is_strict_for_types_enum_anyof_missing_extra_and_limits(self):
        for role in ('extractor','consolidation','reflector','evaluator','proposer'):
            g=self.gateway(role);final=self.final(g);schema=g.packet['payload']['output_schema']
            self.assertEqual(validate_output(final,schema),final)
            for bad in ({},dict(final,extra='not allowed'),[],'string'):
                self.assert_code('rpc_role_output_schema',lambda:validate_output(bad,schema))
        self.assert_code('rpc_role_output_schema',lambda:validate_output(True,{'type':'integer','enum':[1]}))
        self.assert_code('rpc_role_output_schema',lambda:validate_output({'x':1},{'anyOf':[{'type':'string'},{'type':'null'}]}))
