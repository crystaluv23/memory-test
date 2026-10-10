"""Negative controls for native observation transport; no model calls."""
import unittest
from memorylab.lightweight_codex import NativeRPC, fresh_check, thread_params
from memorylab.contracts import ContractError
from pathlib import Path

class NativeTransportTests(unittest.TestCase):
    def event_rpc(self):
        r=object.__new__(NativeRPC);r.thread_id='thread-a';r.turn_id='turn-a';r.events=[]
        return r

    def test_normal_user_input_allowed_but_tools_rejected(self):
        r=self.event_rpc()
        r.event({'method':'item/started','params':{'threadId':'thread-a','turnId':'turn-a','item':{'type':'userMessage'}}})
        for kind in ('commandExecution','fileChange','mcpToolCall','dynamicToolCall','webSearch'):
            with self.assertRaisesRegex(ContractError,'native_actor_tool_violation'):
                r.event({'method':'item/started','params':{'item':{'type':kind}}})
        self.assertEqual(len(r.events),1)

    def test_cross_role_thread_and_turn_rejected(self):
        r=self.event_rpc()
        for fields in ({'threadId':'other'},{'turnId':'other'}):
            with self.assertRaisesRegex(ContractError,'native_cross_'):
                r.event({'method':'item/completed','params':{**fields,'item':{'type':'agentMessage'}}})

    def test_server_request_cannot_be_interpreted_as_notification(self):
        with self.assertRaisesRegex(ContractError,'native_unexpected_server_request'):
            self.event_rpc().event({'id':1,'method':'item/tool/call','params':{}})

    def test_fresh_thread_identity_and_instructions_required(self):
        r={'thread':{'id':'one','ephemeral':True,'turns':[]},'instructionSources':[]}
        fresh_check(r)
        for patch in ({'ephemeral':False},{'turns':[{}]},{'parentThreadId':'parent'},{'forkedFromId':'other'}):
            with self.assertRaisesRegex(ContractError,'native_thread_not_fresh'):
                fresh_check({**r,'thread':{**r['thread'],**patch}})
        with self.assertRaisesRegex(ContractError,'native_thread_not_fresh'):
            fresh_check({**r,'instructionSources':['global']})

    def test_normal_null_error_allowed_and_retry_notification_rejected(self):
        r=self.event_rpc()
        r.event({'method':'thread/status/changed','params':{'error':None}})
        with self.assertRaisesRegex(ContractError,'native_connection_error_stop_no_retry'):
            r.event({'method':'error','params':{'error':{'message':'Reconnecting'},'willRetry':True}})
        self.assertEqual(len(r.events),1)

    def test_recipe_disables_environment_and_memory(self):
        p=thread_params({'model':'frozen','reasoning_effort':'low'},Path('/tmp/subject'),'role')
        self.assertEqual(p['environments'],[]);self.assertTrue(p['ephemeral'])
        self.assertFalse(p['config']['features.memories'])
        self.assertEqual(p['model'],'frozen')

class NativeLoopTests(unittest.TestCase):
    def test_native_bind_and_delivery_reject_wrong_thread(self):
        import tempfile, json, shutil
        from memorylab.collaboration import ROOT, prepare, Loop
        with tempfile.TemporaryDirectory() as d:
            run=Path(d)/'run'
            c=json.loads((ROOT/'configs/collaboration_v0.json').read_text())
            c.update(transport='controller_codex_app_server',direct_actor_tools='forbidden_instruction_only_not_os_qualified',
                message_delivery_policy='reserved_exact_followup_actual_tool_return_ack_no_unknown_resend')
            prepare(run,c)
            with Loop(run) as loop:
                try:
                    a=loop.next();slot=a['slot'];loop.dispatch_intent(slot)
                    with self.assertRaisesRegex(ContractError,'native_thread_not_fresh'):
                        loop.bind(slot,'one',{'thread':{'id':'one','ephemeral':False},'instructionSources':[]})
                    loop.bind(slot,'one',{'thread':{'id':'one','ephemeral':True,'turns':[]},'instructionSources':[]})
                    loop.receive(slot,1,'one','{"op":"scenario","request":{"op":"inspect"}}')
                    loop.followup_intent(slot,1)
                    with self.assertRaisesRegex(ContractError,'native_delivery_thread_mismatch'):
                        loop.acknowledge_followup(slot,1,'one',{'turn':{'id':'turn'},'thread_id':'other'})
                    loop.acknowledge_followup(slot,1,'one',{'turn':{'id':'turn'},'thread_id':'one'})
                    self.assertEqual(loop._delivery(slot,1)['result']['status'],'native_turn_start_acknowledged_not_model_comprehension')
                finally:
                    shutil.rmtree(loop.plan['workspace_root'],ignore_errors=True)
                    shutil.rmtree(loop.plan['client_root'],ignore_errors=True)

class NativeFailureScheduleTests(unittest.TestCase):
    def test_connection_failure_counts_zero_skips_extractor_and_advances_unique_unit(self):
        import tempfile,json,shutil
        from memorylab.collaboration import ROOT,prepare,Loop
        with tempfile.TemporaryDirectory() as d:
            run=Path(d)/'run';c=json.loads((ROOT/'configs/collaboration_v0.json').read_text())
            c.update(transport='controller_codex_app_server',direct_actor_tools='forbidden_instruction_only_not_os_qualified',
                message_delivery_policy='reserved_exact_followup_actual_tool_return_ack_no_unknown_resend',
                termination_policy='native_solver_failure_continue_other_units_preserve_denominator')
            prepare(run,c)
            with Loop(run) as loop:
                try:
                    a=loop.next();slot=a['slot'];loop.dispatch_intent(slot)
                    loop.bind(slot,'fixture-thread',{'thread':{'id':'fixture-thread','ephemeral':True,'turns':[]},'instructionSources':[]})
                    loop.interrupt_intent(slot,'actor_failed')
                    loop.terminate(slot,'fixture-thread','actor_failed',{'transport_failure_only':True,
                        'failure':'native_connection_error_stop_no_retry: explicit fixture','exit':{'exit_code':1}})
                    next_=loop.next();self.assertEqual(next_['slot'],'baseline-task-1')
                    self.assertIsNotNone(loop.ledger.receipt('skip-extract-'+slot))
                    summary=loop.summary('baseline');self.assertEqual(summary['terminated'],1)
                    self.assertFalse(summary['rows'][0]['full_success'])
                    self.assertIsNone(loop.ledger.receipt('done-'+slot))
                    self.assertIsNone(loop.ledger.receipt('done-baseline-extract-0'))
                finally:
                    shutil.rmtree(loop.plan['workspace_root'],ignore_errors=True);shutil.rmtree(loop.plan['client_root'],ignore_errors=True)

    def test_unknown_failure_does_not_gain_unit_continuation(self):
        import tempfile,json,shutil
        from memorylab.collaboration import ROOT,prepare,Loop
        with tempfile.TemporaryDirectory() as d:
            run=Path(d)/'run';c=json.loads((ROOT/'configs/collaboration_v0.json').read_text())
            c.update(transport='controller_codex_app_server',direct_actor_tools='forbidden_instruction_only_not_os_qualified',
                message_delivery_policy='reserved_exact_followup_actual_tool_return_ack_no_unknown_resend',
                termination_policy='native_solver_failure_continue_other_units_preserve_denominator')
            prepare(run,c)
            with Loop(run) as loop:
                try:
                    a=loop.next();slot=a['slot'];loop.dispatch_intent(slot)
                    loop.bind(slot,'fixture-thread',{'thread':{'id':'fixture-thread','ephemeral':True,'turns':[]},'instructionSources':[]})
                    loop.interrupt_intent(slot,'actor_failed')
                    loop.terminate(slot,'fixture-thread','actor_failed',{'transport_failure_only':True,
                        'failure':'unrecognized_contract_error','exit':{'exit_code':1}})
                    self.assertTrue(loop._halted());self.assertFalse(loop._unit_failure(slot))
                finally:
                    shutil.rmtree(loop.plan['workspace_root'],ignore_errors=True);shutil.rmtree(loop.plan['client_root'],ignore_errors=True)

if __name__=='__main__':unittest.main()
