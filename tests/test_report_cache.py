import copy
import unittest
from unittest.mock import patch

from memorylab.contracts import ContractError,canonical
from memorylab.graph_run import GraphRun
from memorylab.stage_inputs import input_config
from memorylab.common import save
import test_graph_run
from test_kernel import AT


class ReportCacheTests(unittest.TestCase):
    def setUp(self):
        self.f=test_graph_run.GraphRunTests();self.f.setUp()

    def tearDown(self):self.f.tearDown()

    def make_round(self):
        self.f.setup_round();self.f.init()
        return GraphRun(self.f.root)

    def assert_code(self,code,call):
        with self.assertRaises(ContractError) as caught:call()
        self.assertEqual(caught.exception.code,code)

    def test_database_write_and_rollback_cannot_hide_missing_input_receipt(self):
        with self.make_round() as run:
            slot=run.inputs.graph['slots'][0]['id'];ref=run.inputs.prepare(slot)
            value=run.inputs.resolve(ref);request_id=value['request_id']
            with run.ledger.mutation(),run.inputs.validation_scope():
                self.assertEqual(run.inputs.resolve(ref),value)
                run.ledger.db.execute('SAVEPOINT cache_probe')
                run.ledger.db.execute('DELETE FROM receipts WHERE request_id=?',(request_id,))
                self.assert_code('uncommitted_stage_input',lambda:run.inputs.resolve(ref))
                run.ledger.db.execute('ROLLBACK TO SAVEPOINT cache_probe')
                run.ledger.db.execute('RELEASE SAVEPOINT cache_probe')
                self.assertEqual(run.inputs.resolve(ref),value)
            self.assertIsNone(run.inputs.validation_session)
            self.assertFalse(run.inputs.memo)

    def test_caller_mutation_exception_and_new_report_do_not_reuse_cached_input(self):
        with self.make_round() as run:
            slot=run.inputs.graph['slots'][0]['id'];ref=run.inputs.prepare(slot)
            expected=run.inputs.resolve(ref)
            with run.ledger.mutation(),run.inputs.validation_scope():
                changed=run.inputs.resolve(ref);changed['payload']['role']='private_fake_role'
                self.assertEqual(run.inputs.resolve(ref),expected)
            with self.assertRaisesRegex(RuntimeError,'probe'),run.ledger.mutation(),run.inputs.validation_scope():
                run.inputs.resolve(ref);raise RuntimeError('probe')
            self.assertIsNone(run.inputs.validation_session);self.assertFalse(run.inputs.dependencies)
            request_id=expected['request_id']
            run.ledger.db.execute('DELETE FROM receipts WHERE request_id=?',(request_id,))
            self.assert_code('uncommitted_stage_input',lambda:run.inputs.resolve(ref))

    def test_same_transaction_nested_decode_cache_keeps_exact_payload_hash_and_private_copies(self):
        with self.make_round() as run:
            ref=run.ref;value=run.ledger.get(ref)
            with run.ledger.mutation(),run.ledger.cached_reads(33554432):
                first=run.ledger.get(ref);first['version']=999
                with run.ledger.cached_reads(33554432):self.assertEqual(run.ledger.get(ref),value)
                with run.ledger.cached_reads(1):self.assertEqual(run.ledger.get(ref),value)
                wrong=copy.deepcopy(value);wrong['version']=999
                run.ledger.db.execute('SAVEPOINT payload_probe')
                run.ledger.db.execute('UPDATE objects SET payload=? WHERE kind=? AND id=? AND version=? AND namespace=?',
                    (canonical(wrong).decode(),ref['kind'],ref['id'],ref['version'],ref['namespace']))
                self.assert_code('corrupt_ledger_hash',lambda:run.ledger.get(ref))
                run.ledger.db.execute('ROLLBACK TO SAVEPOINT payload_probe');run.ledger.db.execute('RELEASE SAVEPOINT payload_probe')
                self.assertEqual(run.ledger.get(ref),value)
            self.assertIsNone(run.ledger.decode_cache)

    def test_memo_limit_falls_back_to_full_validation_and_scope_requires_transaction(self):
        small={**input_config(),'validation_memo_max_bytes':1}
        with patch('memorylab.stage_inputs.input_config',return_value=small),self.make_round() as run:
            self.assert_code('stage_validation_transaction_required',lambda:run.inputs.validation_scope().__enter__())
            ref=run.inputs.prepare(run.inputs.graph['slots'][0]['id'])
            expected=run.inputs.resolve(ref)
            with run.ledger.mutation(),run.inputs.validation_scope():
                self.assertEqual(run.inputs.resolve(ref),expected)
                self.assertEqual(run.inputs.memo_bytes,0);self.assertFalse(run.inputs.memo)
                self.assert_code('nested_stage_validation_scope',lambda:run.inputs.validation_scope().__enter__())
            self.assertEqual(run.report()['stage_report']['planned'],len(run.inputs.graph['slots']))

    def test_cached_report_still_verifies_task_raw_after_an_earlier_success(self):
        with self.make_round() as run:
            for _ in range(len(run.inputs.graph['slots'])):
                first=run.run(AT,limit=1)
                executions=run.ledger.objects('task_execution','control')
                if executions:break
            self.assertTrue(executions);self.assertEqual(first['status'],'partial')
            execution=executions[0][1]
            slot=run.inputs.resolve(execution['input'])['payload']['slot']
            raw=run.tasks._directory(slot)/'transport'/'events.jsonl'
            raw.write_bytes(raw.read_bytes()+b'raw drift\n')
            with self.assertRaises(ContractError):run.report()
            self.assertIsNone(run.inputs.validation_session)
            self.assertEqual(run.journal.report()['planned'],first['stage_report']['planned'])

    def test_completed_task_audit_grade_and_raw_are_checked_on_cached_consumption(self):
        with self.make_round() as run:
            result=run.run(AT);self.assertEqual(result['status'],'terminal',result.get('attention'))
            execution=run.ledger.objects('task_execution','control')[0][1]
            slot=run.inputs.resolve(execution['input'])['payload']['slot']
            for name in ('memory_wire.json','scenario_wire.json','seal.json'):
                path=run.tasks._directory(slot)/name;raw=path.read_bytes();path.write_bytes(b'[]\n')
                with self.assertRaises(ContractError):run.tasks.resolve_task(slot)
                path.write_bytes(raw);self.assertTrue(run.tasks.resolve_task(slot))
            grade=run.tasks.output/('grade-'+slot+'-grade')/'seal.json';raw=grade.read_bytes();grade.write_bytes(b'{}\n')
            self.assert_code('task_grade_seal_mismatch',lambda:run.tasks.resolve_grade(slot+'-grade'))
            grade.write_bytes(raw);self.assertEqual(run.report(),result)

    def test_python_role_raw_and_resigned_seal_cannot_replace_published_capture(self):
        with self.make_round() as run:
            run.run(AT)
            execution=run.ledger.objects('role_stage_execution','control')[0][1]
            slot=run.inputs.resolve(execution['input'])['payload']['slot'];directory=run.actors._directory(slot)
            rawpath=directory/'transport'/'events.jsonl';raw=rawpath.read_bytes();rawpath.write_bytes(raw+b'changed\n')
            self.assert_code('role_stage_raw_drift',lambda:run.actors.resolve(slot))
            rawpath.write_bytes(raw);self.assertTrue(run.actors.resolve(slot))
            sealpath=directory/'seal.json';sealraw=sealpath.read_bytes()
            import json
            seal=json.loads(sealraw);seal['source_events']=[];seal['memory_events']=[{'forged':True}]
            save(sealpath,seal)
            self.assert_code('role_stage_raw_drift',lambda:run.actors.resolve(slot))
            sealpath.write_bytes(sealraw);self.assertTrue(run.actors.resolve(slot))

    def test_interrupted_task_same_handle_accepts_later_exit_reason_but_rejects_another_handle(self):
        with self.make_round() as run:
            with self.assertRaises(RuntimeError):run.run(AT,fault=self.f.crash('after_process'))
            run.run(AT)
            captures=[v for _,v in run.ledger.objects('task_capture','control') if v['seal'] is None]
            self.assertEqual(len(captures),1);capture=captures[0]
            execution=run.ledger.get(capture['execution']);slot=run.inputs.resolve(execution['input'])['payload']['slot']
            status={**capture['process_status'],'reason':'pid_reused_different_start'}
            with patch('memorylab.task_stage.process_status',return_value=status):self.assertTrue(run.tasks.resolve_task(slot))
            wrong={**status,'handle':{**status['handle'],'pid':status['handle']['pid']+1,'pgid':status['handle']['pgid']+1}}
            with patch('memorylab.task_stage.process_status',return_value=wrong):
                self.assert_code('task_interruption_drift',lambda:run.tasks.resolve_task(slot))
