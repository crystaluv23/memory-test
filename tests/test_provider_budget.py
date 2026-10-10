import copy
import concurrent.futures
import sqlite3
from unittest.mock import patch
from pathlib import Path

from memorylab.provider_budget import ProviderBudget
from memorylab.ledger import Ledger
from test_kernel import Fixture


class ProviderBudgetTests(Fixture):
    def setup_budget(self,count=2):
        self.path=Path(self.tmp.name)/'budget.sqlite'
        self.round={'requests':count,'input_tokens':100*count,'output_tokens':64*count}
        self.slots={key:{'role':role,'limits':{'requests':1,'input_tokens':100,'output_tokens':64},'per_request_output_tokens':64}
            for key,role in list((('task','solver'),('maintenance','extractor'),('research','proposer')))[:count]}
        return ProviderBudget(self.path,self.round,self.slots,create=True)

    def reopen(self):return ProviderBudget(self.path,self.round,self.slots)

    def request(self):return {'model':'memorylab-fixture','input':[{'role':'user','content':'harmless'}],'store':False,'stream':True}

    def response(self,id_='resp-one',**usage):
        u={'input_tokens':100,'output_tokens':10,'total_tokens':110,**usage}
        return {'id':id_,'object':'response','model':'memorylab-fixture','status':'completed','usage':u}

    def test_sealed_readonly_report_preserves_database_and_cannot_grant_dispatch(self):
        gate=self.setup_budget();ref=gate.prepare('call-one','task',self.request());gate.dispatch(ref);gate.finish(ref,self.response())
        expected=gate.report();before={p.name:p.read_bytes() for p in self.path.parent.glob('budget.sqlite*')}
        readonly=ProviderBudget(self.path,self.round,self.slots,readonly=True)
        self.assertEqual(readonly.report(),expected)
        for action in (lambda:readonly.prepare('new','task',self.request()),lambda:readonly.dispatch(ref),lambda:readonly.finish(ref,self.response())):
            self.assert_code('provider_budget_read_only',action)
        with readonly._open() as ledger:
            self.assert_code('readonly_ledger_write_denied',lambda:ledger.put({'kind':'test','id':'x','version':1},'control'))
            with self.assertRaises(sqlite3.OperationalError):ledger.db.execute('DELETE FROM receipts')
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.path.parent.glob('budget.sqlite*')})
        self.assert_code('provider_budget_read_only',lambda:ProviderBudget(self.path,self.round,self.slots,create=True,readonly=True))

    def test_readonly_missing_or_open_journal_never_creates_or_checkpoints_database(self):
        absent=Path(self.tmp.name)/'absent.sqlite'
        self.assert_code('readonly_ledger_missing',lambda:Ledger(absent,readonly=True));self.assertFalse(absent.exists())
        gate=self.setup_budget();gate.prepare('call-one','task',self.request())
        before=self.path.read_bytes()
        for suffix in ('-wal','-shm','-journal'):
            journal=Path(str(self.path)+suffix);journal.write_bytes(b'harmless-unsealed-marker')
            self.assert_code('readonly_ledger_unsealed',lambda:ProviderBudget(self.path,self.round,self.slots,readonly=True))
            self.assertEqual(journal.read_bytes(),b'harmless-unsealed-marker');journal.unlink()
        self.assertEqual(self.path.read_bytes(),before)

    def test_prelaunch_guard_caps_gross_costs_and_cache_never_reauthorizes_dispatch(self):
        gate=self.setup_budget();request=self.request();request['max_output_tokens']=1000
        ref=gate.prepare('call-one','task',request);forward=gate.dispatch(ref)
        self.assertEqual(forward['max_output_tokens'],64);self.assertEqual(forward['truncation'],'disabled')
        self.assertEqual(request['max_output_tokens'],1000)
        self.assertEqual(gate.prepare('call-one','task',request),ref)
        self.assert_code('provider_budget_already_dispatched',lambda:self.reopen().dispatch(ref))
        response=self.response();response['usage']['input_tokens_details']={'cached_tokens':90}
        terminal=gate.finish(ref,response);self.assertEqual(gate.finish(ref,response),terminal)
        self.assertEqual(gate.report()['input_tokens'],100);self.assertEqual(gate.report()['output_tokens'],10)
        second=gate.prepare('call-two','maintenance',self.request());gate.dispatch(second);gate.finish(second,self.response('resp-two'))
        report=self.reopen().report();self.assertEqual(report['requests'],2);self.assertEqual(report['input_tokens'],200)
        self.assertEqual(report['output_tokens'],20);self.assertEqual(report['unknown_requests'],0)
        self.assertFalse(report['provider_budget_qualified']);self.assertFalse(report['tokenizer_qualified'])
        self.assert_code('provider_budget_request_guard',lambda:gate.prepare('call-three','task',self.request()))

    def test_before_or_after_dispatch_crash_retains_full_reserved_cost_and_missing_db_cannot_reset(self):
        gate=self.setup_budget();ref=gate.prepare('call-one','task',self.request())
        self.assertEqual(self.reopen().report()['rows'][0]['status'],'reserved')
        self.reopen().dispatch(ref);report=self.reopen().report()
        self.assertEqual((report['input_tokens'],report['output_tokens'],report['unknown_requests']),(100,64,1))
        self.assert_code('provider_budget_request_guard',lambda:self.reopen().prepare('call-two','task',self.request()))
        self.assert_code('provider_budget_already_dispatched',lambda:self.reopen().dispatch(ref))
        self.path.rename(self.path.with_suffix('.saved'))
        self.assert_code('provider_budget_database_missing',gate.report)
        self.assert_code('provider_budget_database_missing',self.reopen)
        self.assertFalse(self.path.exists())

    def test_usage_breach_is_committed_blocks_all_slots_and_never_releases_known_excess(self):
        gate=self.setup_budget();ref=gate.prepare('call-one','task',self.request());gate.dispatch(ref)
        response=self.response(output_tokens=80,total_tokens=180)
        self.assert_code('provider_budget_usage_breach',lambda:gate.finish(ref,response))
        self.assertEqual((self.reopen().report()['breaches'],self.reopen().report()['output_tokens']),(1,80))
        self.assertEqual(self.reopen().report()['unknown_requests'],0)
        self.assert_code('provider_budget_breached',lambda:self.reopen().prepare('call-two','maintenance',self.request()))
        self.assert_code('provider_budget_usage_breach',lambda:self.reopen().finish(ref,response))
        self.assert_code('idempotency_conflict',lambda:gate.finish(ref,self.response()))

    def test_bad_identity_missing_bool_negative_mismatch_usage_and_duplicate_response_ids_persist_breach(self):
        cases=[{},self.response(input_tokens=True),self.response(input_tokens=-1),self.response(input_tokens=99,total_tokens=109),self.response(total_tokens=0)]
        wrong=self.response();wrong['model']='other';cases.append(wrong)
        wrong=self.response();wrong['object']='response.input_tokens';cases.append(wrong)
        for i,response in enumerate(cases):
            with self.subTest(response=response):
                path=Path(self.tmp.name)/('bad-'+str(i)+'.sqlite')
                bound={'requests':1,'input_tokens':100,'output_tokens':64};slots={'task':{'role':'solver','limits':bound,'per_request_output_tokens':64}}
                gate=ProviderBudget(path,bound,slots,create=True);ref=gate.prepare('call-one','task',self.request());gate.dispatch(ref)
                self.assert_code('provider_budget_usage_breach',lambda:gate.finish(ref,response))
                self.assertEqual(gate.report()['breaches'],1);self.assertGreaterEqual(gate.report()['output_tokens'],64)
                if response=={}:self.assertEqual(gate.report()['unknown_requests'],1)
        gate=self.setup_budget();a=gate.prepare('call-one','task',self.request());gate.dispatch(a);gate.finish(a,self.response())
        b=gate.prepare('call-two','maintenance',self.request());gate.dispatch(b)
        self.assert_code('provider_budget_usage_breach',lambda:gate.finish(b,self.response()))

    def test_contract_slots_hidden_history_and_bad_bounds_reject_before_counter_or_reservation(self):
        gate=self.setup_budget();before=gate.report()
        cases=[('provider_budget_model',{'model':'real'}),('provider_budget_hidden_context',{**self.request(),'previous_response_id':'hidden'}),
            ('provider_budget_hidden_context',{**self.request(),'conversation':'hidden'}),('provider_budget_hidden_context',{**self.request(),'background':True}),
            ('provider_budget_hidden_context',{**self.request(),'truncation':'auto'}),('provider_budget_output_limit',{**self.request(),'max_output_tokens':True}),
            ('provider_budget_output_limit',{**self.request(),'max_output_tokens':15})]
        for code,request in cases:self.assert_code(code,lambda:gate.prepare('call-one','task',request))
        self.assert_code('provider_budget_unregistered_slot',lambda:gate.prepare('call-one','unregistered',self.request()))
        self.assertEqual(gate.report(),before)
        different=copy.deepcopy(self.slots);different['task']['role']='evaluator'
        self.assert_code('idempotency_conflict',lambda:ProviderBudget(self.path,self.round,different))
        self.assert_code('provider_budget_exists',lambda:ProviderBudget(self.path,self.round,self.slots,create=True))
        self.assert_code('provider_budget_plan_over_guard',lambda:ProviderBudget(Path(self.tmp.name)/'bad.sqlite',{'requests':1,'input_tokens':200,'output_tokens':128},self.slots,create=True))
        self.assertFalse((Path(self.tmp.name)/'bad.sqlite').exists())

    def test_missing_receipt_or_reservation_cannot_disappear_from_denominator(self):
        gate=self.setup_budget();ref=gate.prepare('call-one','task',self.request());gate.dispatch(ref)
        with Ledger(self.path) as ledger:ledger.db.execute('DELETE FROM receipts WHERE request_id=?',('budget-sent-call-one',))
        self.assert_code('provider_budget_receipt_missing',gate.report)
        second=Path(self.tmp.name)/'second.sqlite';gate=ProviderBudget(second,self.round,self.slots,create=True)
        ref=gate.prepare('call-one','task',self.request())
        with Ledger(second) as ledger:ledger.db.execute('DELETE FROM objects WHERE kind=?',('provider_budget_reservation',))
        self.assert_code('unresolved_reference',gate.report)

    def test_concurrent_same_slot_reservations_cannot_exceed_frozen_calls(self):
        self.setup_budget(count=1)
        def reserve(i):
            try:return self.reopen().prepare('call-'+str(i),'task',self.request())
            except ValueError as e:return getattr(e,'code',type(e).__name__)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:values=list(pool.map(reserve,range(2)))
        self.assertEqual(sum(type(v) is dict for v in values),1);self.assertIn('provider_budget_request_guard',values)
        self.assertEqual(self.reopen().report()['requests'],1)

    def test_implementation_drift_stops_existing_instance_and_corrupt_contract_is_not_rebound(self):
        gate=self.setup_budget();gate.prepare('call-one','task',self.request())
        with patch('memorylab.provider_budget.implementation',return_value='0'*64):
            self.assert_code('provider_budget_implementation_drift',gate.report)
            self.assert_code('idempotency_conflict',self.reopen)
        before=gate.report();self.assertEqual(before['requests'],1);self.assertEqual(before['unknown_requests'],1)
