import base64
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from memorylab.admission import RoleAdmission,resolve_capture
from memorylab.graph_run import GraphRun
from memorylab.ledger import Ledger
from memorylab.runtime import UsageStore
from test_kernel import AT,Fixture
from test_rpc_disclosure import CLI
import test_role_stage
import test_graph_run
import test_task_stage


class NativeRoleStageTests(Fixture):
    setup_graph=test_role_stage.RoleStageTests.setup_graph
    target=test_role_stage.RoleStageTests.target
    finish=test_role_stage.RoleStageTests.finish
    outputs=test_role_stage.RoleStageTests.outputs
    derive=test_role_stage.RoleStageTests.derive
    run_until=test_role_stage.RoleStageTests.run_until
    role_code=test_role_stage.RoleStageTests.code
    task_code=test_task_stage.TaskStageTests.code
    setup_round=test_graph_run.GraphRunTests.setup_round
    init=test_graph_run.GraphRunTests.init
    crash=staticmethod(test_role_stage.RoleStageTests.crash)

    def program(self):
        return {'transport':'native_rpc_fixed_fixture','cli_path':str(CLI),
            'cli_sha256':hashlib.sha256(CLI.read_bytes()).hexdigest(),'output_policy':'fixed_role_packet_template'}

    def execution(self):return self.ledger.objects('role_stage_execution','control')[0][0]

    def test_native_all_five_roles_publish_actual_snapshots_batches_and_usage_for_successors(self):
        self.setup_graph(research=True);program=self.program();threads=[]
        for role in ('consolidation','reflector','extractor','evaluator','proposer'):
            slot=self.target(role);result=self.roles.run_native(slot,program,AT)
            self.assertEqual(result['role_result']['outcome'],'materialized',result)
            self.assertFalse(result['formal_admission']);capture=resolve_capture(self.ledger,result['role_result']['capture'])
            threads.append(capture['transport']['thread_id']);directory=self.roles._directory(slot)
            self.assertEqual(base64.b64decode(capture['raw_events_base64']),(directory/'transport/stdout.bin').read_bytes())
            recipe=json.loads((directory/'transport/recipe.json').read_text())
            self.assertLessEqual(recipe['execution_seconds'],self.nodes[slot]['budget']['seconds'])
            for root in (str(self.db_path.parent.resolve()),str(self.inputs.output),str(self.roles.output)):
                self.assertIn(root,recipe['denied_roots'])
                self.assertEqual((directory/'transport/generated-config.toml').read_text().count(json.dumps(root)+' = "deny"'),2)
            artifacts=self.ledger.get(result['stage']['result']['artifact'])['artifacts']
            if role=='extractor':
                successor=next(n for n in self.g['slots'] if slot in n['depends_on'] and n['role']=='corpus')
                p=self.inputs.resolve(self.inputs.prepare(successor['id']))['payload']
                self.assertEqual(p['resources']['snapshot'],artifacts['snapshot'])
            elif role in ('evaluator','proposer'):
                usage=UsageStore(self.cards).visible('research/'+role,self.ledger.head())
                self.assertTrue(any(v['event_type']=='open' and v['episode']==slot for v in usage))
                self.assertFalse(any(v['event_type']=='behavior_link' for v in usage))
            self.assertTrue(self.roles.run_native(slot,program,AT)['cache_hit'])
        self.assertEqual(len(set(threads)),5)

    def test_main_all_publication_crashes_reuse_native_seal_without_reexecution(self):
        self.setup_graph();slot=self.target('extractor');program=self.program()
        with self.assertRaises(RuntimeError):self.roles.run_native(slot,program,AT,self.crash('after_seal'))
        before=self.ledger.audit();raw=(self.roles._directory(slot)/'transport/stdout.bin').read_bytes()
        with patch('memorylab.role_stage.run_native_fixture',side_effect=AssertionError('no second native call')):
            for phase in ('materialized','publication','stage','receipt'):
                with self.assertRaises(RuntimeError):self.roles.commit(self.execution(),AT,self.crash(phase))
                self.assertEqual(self.ledger.audit(),before)
            result=self.roles.run_native(slot,program,AT);self.assertTrue(self.roles.run_native(slot,program,AT)['cache_hit'])
        self.assertEqual(result['new_model_calls'],0);self.assertEqual((self.roles._directory(slot)/'transport/stdout.bin').read_bytes(),raw)
        self.assertEqual(RoleAdmission(self.cards,self.registry).report()['terminal'],1)

    def test_unsealed_native_raw_and_audits_are_interrupted_without_materialization_or_retry(self):
        self.setup_graph();slot=self.target('extractor');program=self.program()
        with self.assertRaises(RuntimeError):self.roles.run_native(slot,program,AT,self.crash('after_process'))
        with patch('memorylab.role_stage.run_native_fixture',side_effect=AssertionError('no retry')):
            result=self.roles.run_native(slot,program,AT);self.assertTrue(self.roles.run_native(slot,program,AT)['cache_hit'])
        self.assertEqual(result['stage']['result']['outcome'],'interrupted')
        capture=self.ledger.get(result['role_result']['capture']);self.assertTrue(base64.b64decode(capture['raw_events_base64']))
        self.assertTrue(capture['unverified_audits']);self.assertIsNone(result['role_result']['materialization'])
        self.assert_code('failed_role_capture',lambda:resolve_capture(self.ledger,result['role_result']['capture']))
        self.assertFalse(any(c['id'].startswith('native-fixture-') for _,c in self.cards.active(self.nodes[slot]['namespace'],self.ledger.head())))

    def test_missing_handle_preserves_started_and_cache_raw_drift_is_rejected(self):
        self.setup_graph();slot=self.target('extractor');program=self.program()
        with self.assertRaises(RuntimeError):self.roles.run_native(slot,program,AT,self.crash('after_seal'))
        handle=self.roles._directory(slot)/'transport/handle/process.json';raw=handle.read_bytes();handle.unlink();before=self.ledger.audit()
        with patch('memorylab.role_stage.run_native_fixture',side_effect=AssertionError('no retry')):
            self.assert_code('role_stage_exit_unverified',lambda:self.roles.run_native(slot,program,AT))
        self.assertEqual(self.ledger.audit(),before);handle.write_bytes(raw)
        self.roles.run_native(slot,program,AT)
        stream=self.roles._directory(slot)/'transport/stdout.bin';stream.write_bytes(stream.read_bytes()+b'{}\n')
        self.assert_code('rpc_admission_capture_drift',lambda:self.roles.resolve(slot))

    def test_shadow_child_commit_parent_crashes_do_not_publish_early_or_import_child_state(self):
        self.setup_graph(research=True,shadow=True);slot=self.target('shadow','evaluator-shadow-current');program=self.program()
        with self.assertRaises(RuntimeError):self.roles.run_native(slot,program,AT,self.crash('child_committed'))
        self.assertEqual(self.ledger.objects('role_stage_publication','control'),[])
        self.assertNotIn(slot,self.j._finished());sources=self.ledger.objects('source','research/evaluator')
        p=self.inputs.resolve(self.inputs.prepare(slot))['payload'];path=p['resources']['store_binding']['path']
        with Ledger(path) as child:before=child.audit();self.assertEqual(len(child.objects('role_capture','control')),1)
        with patch('memorylab.role_stage.run_native_fixture',side_effect=AssertionError('no shadow retry')):
            for phase in ('materialized','publication','stage','receipt'):
                with self.assertRaises(RuntimeError):self.roles.run_native(slot,program,AT,self.crash(phase))
            result=self.roles.run_native(slot,program,AT)
        with Ledger(path) as child:self.assertEqual(child.audit(),before)
        self.assertEqual(self.ledger.objects('source','research/evaluator'),sources);self.assertEqual(self.roles.resolve(slot),result)
        publication=self.ledger.get(result['publication'])
        with Ledger(path) as child:child.db.execute('DELETE FROM receipts WHERE request_id=?',(publication['child_receipt']['request_id'],))
        self.assert_code('role_stage_child_receipt_drift',lambda:self.roles.resolve(slot))

    def test_start_crash_reopens_only_unstarted_initial_disclosure_and_noeligible_calls_nothing(self):
        self.setup_graph();slot=self.target('extractor');program=self.program()
        with self.assertRaises(RuntimeError):self.roles.run_native(slot,program,AT,self.crash('start'))
        self.assertFalse((self.roles._directory(slot)/'transport').exists())
        result=self.roles.run_native(slot,program,AT);self.assertEqual(result['role_result']['outcome'],'materialized')

    def test_shadow_start_crash_reuses_registered_child_attempt_after_parent_rollback(self):
        self.setup_graph(research=True,shadow=True)
        slot=self.target('shadow','evaluator-shadow-current');program=self.program()
        with self.assertRaises(RuntimeError):self.roles.run_native(slot,program,AT,self.crash('start'))
        p=self.inputs.resolve(self.inputs.prepare(slot))['payload'];path=p['resources']['store_binding']['path']
        with Ledger(path) as child:
            attempts=child.objects('rpc_role_attempt','control')
            self.assertEqual(len(attempts),1);self.assertEqual(child.objects('role_capture','control'),[])
        self.assertEqual(self.ledger.objects('role_stage_execution','control'),[])
        self.assertFalse((self.roles._directory(slot)/'transport').exists())
        result=self.roles.run_native(slot,program,AT)
        self.assertEqual(result['role_result']['outcome'],'materialized')
        with Ledger(path) as child:
            self.assertEqual(child.objects('rpc_role_attempt','control'),attempts)
            self.assertEqual(len(child.objects('role_capture','control')),1)
        self.assertTrue(self.roles.run_native(slot,program,AT)['cache_hit'])

    def test_noeligible_native_role_has_no_actor_or_fake_capture(self):
        self.setup_graph(seeded=False);slot=self.target('consolidation')
        with patch('memorylab.role_stage.run_native_fixture',side_effect=AssertionError('no eligible means no call')):
            result=self.roles.run_native(slot,self.program(),AT)
        self.assertEqual(result['result']['outcome'],'completed');self.assertEqual(self.ledger.objects('role_capture','control'),[])

    def test_graph_driver_mixed_native_roles_and_python_tasks_runs_all_four_shadow_branches(self):
        self.setup_round(research=True,shadow=True)
        for node in self.g['slots']:
            if node['id'] in self.programs and node['role'] not in ('solver','acquisition','oracle'):self.programs[node['id']]=self.program()
        self.init()
        with GraphRun(self.root) as run:
            result=run.run(AT);self.assertEqual(result['status'],'terminal',result['attention'])
            self.assertEqual(result['stage_report']['planned'],result['stage_report']['terminal'])
            self.assertEqual(result['task_report']['planned'],result['task_report']['full_success'])
            self.assertFalse(result['formal_admission']);self.assertFalse(result['gain_evidence'])
            self.assertEqual(len([r for r in result['stage_report']['rows'] if r['role']=='shadow_grade']),4)
            self.assertEqual(run.run(AT),result)
            self.assertTrue(run.ledger.objects('rpc_role_attempt','control'))
            slot=next(n['id'] for n in run.inputs.graph['slots'] if n['role']=='evaluator')
            stream=run.actors._directory(slot)/'transport/stdout.bin';stream.write_bytes(stream.read_bytes()+b'{}\n')
            self.assert_code('rpc_admission_capture_drift',run.report)

    def test_native_descriptor_cannot_change_cli_policy_or_take_task_slots(self):
        self.setup_round();program=self.program();solver=next(n['id'] for n in self.g['slots'] if n['role']=='solver')
        changed=dict(self.programs);changed[solver]=program
        self.assert_code('native_role_program_role_denied',lambda:self.init(programs=changed))
        slot=next(n['id'] for n in self.g['slots'] if n['role']=='extractor')
        for key,value,code in (('cli_sha256','0'*64,'native_role_cli_drift'),('output_policy','arbitrary_code','unsupported_native_role_program')):
            altered=dict(program);altered[key]=value;changed=dict(self.programs);changed[slot]=altered
            self.assert_code(code,lambda:self.init(programs=changed))
