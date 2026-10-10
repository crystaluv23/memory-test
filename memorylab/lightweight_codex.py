"""Bounded observational flywheel using fresh native Codex app-server threads."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from .app_server_probe import ProbeRPC
from .rpc_role import FreshRoleRPC
from .collaboration import Loop, ROOT, prepare as prepare_loop
from .common import save, exclusive_run
from .contracts import canonical, require, sha256
from .sandbox import worker_environment


class NativeRPC(ProbeRPC):
    next_message = FreshRoleRPC.next_message

    def __init__(self, cli, workspace, output, config):
        self.deadline = time.monotonic() + config['actor_wall_seconds']
        self.events = []
        self.thread_id = None
        self.turn_id = None
        cmd = [cli, '--no-daemon', '-a', 'never', '--enable', 'skip_host_skill_discovery']
        for f in ('memories', 'apps', 'plugins', 'browser_use', 'computer_use', 'multi_agent', 'shell_snapshot', 'skill_search', 'js_repl'):
            cmd += ['--disable', f]
        for override in ('web_search="disabled"', 'mcp_servers={}', 'analytics.enabled=false', 'feedback.enabled=false'):
            cmd += ['-c', override]
        for override in ('model_provider="memorylab-native"', 'model_providers.memorylab-native.name="Native CLI observed"', 'model_providers.memorylab-native.requires_openai_auth=true',
                         'model_providers.memorylab-native.supports_websockets=false', 'model_providers.memorylab-native.request_max_retries=0',
                         'model_providers.memorylab-native.stream_max_retries=0'):
            cmd += ['-c', override]
        cmd += ['app-server', '--listen', 'stdio://']
        env = worker_environment()
        # CLI reads its existing login in place. Subject tools are disabled by
        # environments=[]; no auth data is opened or copied by this controller.
        env.update(HOME=str(workspace), CODEX_HOME=str(Path.home()/'.codex'), TMPDIR=str(workspace))
        for key in ('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY', 'https_proxy', 'http_proxy', 'all_proxy', 'no_proxy'):
            if key in os.environ: env[key] = os.environ[key]
        limits = {'max_wire_bytes': config['wire_bytes_max'], 'max_line_bytes': config['line_bytes_max'],
                  'timeout_seconds': config['actor_wall_seconds']}
        super().__init__(cmd, workspace, env, limits, output)

    def event(self, value):
        require('id' not in value and type(value.get('method')) is str, 'native_unexpected_server_request')
        p = value.get('params', {})
        if value['method'] == 'error':
            require(False, 'native_connection_error_stop_no_retry', str((p.get('error') or {}).get('message')))
        if p.get('threadId') is not None and self.thread_id:
            require(p['threadId'] == self.thread_id, 'native_cross_thread')
        if p.get('turnId') is not None and self.turn_id:
            require(p['turnId'] == self.turn_id, 'native_cross_turn')
        if value['method'] in ('item/started', 'item/completed'):
            require(p.get('item', {}).get('type') in ('userMessage', 'agentMessage', 'reasoning', 'plan'), 'native_actor_tool_violation')
        self.events.append(value)

    def call(self, method, params=None, notify=False):
        require(method in ('initialize','initialized','model/list','thread/start','turn/start'), 'native_method_forbidden')
        require(notify == (method == 'initialized'), 'native_notification_policy')
        self.sequence += 1
        message = {'method': method, 'params': params or {}}
        if not notify: message['id'] = self.sequence
        raw = canonical(message) + b'\n'
        require(len(raw) <= self.config['max_line_bytes'], 'native_input_overflow')
        self._count(len(raw)); self.records.append({'direction':'request','message':message})
        self.process.stdin.write(raw); self.process.stdin.flush(); self.raw_stdin.write(raw); self.raw_stdin.flush()
        if notify: return
        while True:
            v = self.next_message(self.deadline)
            if 'id' not in v: self.event(v); continue
            require(v.get('id') == self.sequence and 'method' not in v, 'native_response_identity')
            require('error' not in v and 'result' in v, 'native_rpc_error', str(v.get('error')))
            return v['result']

    def start_turn(self, text, config):
        self.turn_id = None
        r = self.call('turn/start', {'threadId':self.thread_id, 'input':[{'type':'text','text':text}],
                                    'effort':config['reasoning_effort'], 'environments':[]})
        self.turn_id = r['turn']['id']
        return r

    def complete(self):
        finals = []
        while True:
            v = self.next_message(self.deadline); self.event(v)
            if v['method'] == 'item/completed':
                item = v['params']['item']
                if item['type'] == 'agentMessage': finals.append(item['text'])
            if v['method'] == 'turn/completed':
                require(v['params']['turn']['id'] == self.turn_id and v['params']['turn']['status'] == 'completed', 'native_turn_failed')
                require(len(finals) == 1, 'native_final_count')
                # No code fence repair or synthetic role output.
                json.loads(finals[0])
                return finals[0]

    def usage(self):
        values = [e['params']['tokenUsage']['total'] for e in self.events if e['method'] == 'thread/tokenUsage/updated']
        return values[-1] if values else None


def initialize_rpc(rpc):
    rpc.call('initialize', {'clientInfo':{'name':'memorylab-native-lightweight','version':'0.1'},'capabilities':{'experimentalApi':True}})
    rpc.call('initialized', notify=True)


def thread_params(config, workspace, instruction):
    return {'model':config['model'], 'cwd':str(workspace), 'approvalPolicy':'never',
            'ephemeral':True,'experimentalRawEvents':False,'environments':[],
            'baseInstructions':'You are an independent experimental participant. Use only this session supplied public packet and service replies. Never access other sessions, global memory, files, tools, or external sources. Output exactly one JSON object per turn.',
            'developerInstructions':instruction,
            'config':{'features.memories':False,'features.apps':False,'features.plugins':False,
                      'features.multi_agent':False,'web_search':'disabled','model_reasoning_effort':config['reasoning_effort']}}


def fresh_check(result):
    t = result['thread']
    require(t['ephemeral'] and t.get('turns') == [] and not t.get('forkedFromId') and
            not t.get('parentThreadId') and result.get('instructionSources') == [], 'native_thread_not_fresh')


def prepare(run, config_path):
    config = json.loads(Path(config_path).read_text())
    c = json.loads((ROOT/'configs/collaboration_v0.json').read_text())
    c.update(transport='controller_codex_app_server',direct_actor_tools='forbidden_instruction_only_not_os_qualified',
             message_delivery_policy='reserved_exact_followup_actual_tool_return_ack_no_unknown_resend',
             actor_wall_seconds=config['actor_wall_seconds'],actor_turns_max=config['actor_turns_max'],
             agent_slots_max=config['sessions_max'], seed=config['seed'])
    if config.get('solver_failure_policy') == 'record_zero_skip_extraction_continue_unique_units':
        c['termination_policy']='native_solver_failure_continue_other_units_preserve_denominator'
    origin = config.get('continuation_from')
    bootstrap = None; origin_record = None; schedule = None; summaries = None
    if origin:
        from .ledger import Ledger
        from .registry import Registry
        from .cards import CanonicalCards, EvidenceStore
        from .stores import CorpusStore
        origin = (ROOT/origin).resolve()
        source_plan=json.loads((origin/'plan.json').read_text())
        source_hash=hashlib.sha256((origin/'ledger.sqlite').read_bytes()).hexdigest()
        staging=Path(tempfile.mkdtemp(prefix='ml-native-research-checkpoint-'))
        with Ledger(origin/'ledger.sqlite',readonly=True) as source:
            db=sqlite3.connect(staging/'origin.sqlite');source.db.backup(db);db.close()
        origin_db=Ledger(staging/'origin.sqlite');registry=Registry(origin_db,source_plan['kernel'])
        source_cards=CanonicalCards(EvidenceStore(origin_db,source_plan['kernel']))
        store=CorpusStore(source_cards)
        proposal=origin_db.receipt('done-evaluator-0')
        require(proposal is not None,'continuation_evaluator_missing')
        candidate=proposal['result']['materialization']['result']['candidate']
        require(candidate is not None,'continuation_challenge_missing')
        memory=origin_db.receipt('initial-specs')['result']['memory']
        bootstrap={'memory':registry.resolve(memory)['config'],'scenario':registry.resolve(candidate)['config']}
        cutoff=origin_db.head()
        snapshots={ns:store.capture('continuation-'+ns.replace('/','-'),ns,cutoff) for ns in ('research/proposer','research/evaluator')}
        origin_record={'run':str(origin),'source_ledger_sha256':source_hash,'source_plan_sha256':source_plan['sha256'],
                       'challenge_ref':candidate,'snapshot_store':str(origin_db.path),'snapshots':snapshots}
        schedule=['challenge-1','proposer-1','candidate-1']
    if config.get('bootstrap_summary_from'):
        source_run=(ROOT/config['bootstrap_summary_from']).resolve()
        public=json.loads((source_run/'native-report.json').read_text())
        summary=next(s for s in public['phases'] if s['phase']=='challenge-1')
        require(summary['completed']+summary['terminated']==summary['planned'],'incomplete_bootstrap_summary')
        summaries={'challenge-1':summary}
        failures=json.loads((source_run/'native-failure.json').read_text())
        summaries['challenge-1']['failure_diagnostics']=[{'slot':failures['slot'],'reason':failures['error'],'classification':'transport_not_memory_quality'}]
        origin_record['summary_origin']={'run':str(source_run),'report_sha256':hashlib.sha256((source_run/'native-report.json').read_bytes()).hexdigest(),
                                        'ledger_sha256':hashlib.sha256((source_run/'ledger.sqlite').read_bytes()).hexdigest()}
        schedule=['proposer-1','candidate-1']
    result = prepare_loop(run, c, initial_specs=bootstrap, schedule=schedule, bootstrap_summaries=summaries)
    if origin:
        try:
            with Loop(run) as loop:
                destination=CorpusStore(loop.cards)
                for ns,ref in snapshots.items():
                    destination.import_snapshot('bootstrap-'+ns.replace('/','-'),store,ref,ns,'initial_checkpoint')
        finally: origin_db.close()
        require(hashlib.sha256((origin/'ledger.sqlite').read_bytes()).hexdigest()==source_hash,'continuation_origin_modified')
    cli = shutil.which('codex'); require(bool(cli), 'codex_missing')
    cli = str(Path(cli).resolve())
    plan={'config':config,'config_file':Path(config_path).name,'cli':cli,'cli_sha256':hashlib.sha256(Path(cli).read_bytes()).hexdigest(),
          'loop_plan':result['plan'],'created_at':time.time(),'origin':origin_record}
    plan['sha256']=sha256(plan);save(Path(run)/'native-plan.json',plan)
    return result


def native_plan(run):
    p=json.loads((Path(run)/'native-plan.json').read_text());v=dict(p);digest=v.pop('sha256')
    require(sha256(v)==digest,'native_plan_drift')
    require(p['config']==json.loads((Path(run)/'source-snapshot/configs'/p['config_file']).read_text()),'native_config_not_frozen')
    require(p['loop_plan']==json.loads((Path(run)/'plan.json').read_text())['sha256'],'native_loop_plan_drift')
    return p


def canary(run):
    run = Path(run); p = native_plan(run); config=p['config']
    output = run/'native-canary'; output.mkdir()
    with tempfile.TemporaryDirectory(prefix='ml-native-') as w:
        rpc = NativeRPC(p['cli'], Path(w), output, config)
        try:
            initialize_rpc(rpc)
            models = rpc.call('model/list', {'limit':100})
            save(output/'models.json', models)
            require(any(m.get('model') == config['model'] or m.get('id') == config['model'] for m in models['data']), 'requested_model_unavailable')
            params = thread_params(config,Path(w),'Return exactly {"status":"context_isolation_ready"}.')
            save(output/'thread-recipe.json', params)
            r = rpc.call('thread/start',params); rpc.thread_id=r['thread']['id']; fresh_check(r)
            save(output/'thread-start.json', r)
            rpc.start_turn('Return exactly {"status":"context_isolation_ready"}.',config)
            raw = rpc.complete(); require(json.loads(raw)=={'status':'context_isolation_ready'},'canary_final_mismatch')
            save(output/'result.json', {'status':'observed_native_canary_passed','final':raw,'usage':rpc.usage(),
                  'memory_disabled':True,'environment_tools_disabled':True,'full_provider_context_verified':False,'formal_admission':False})
        except Exception as exc:
            save(output/'result.json', {'status':'failed','error':str(exc),'usage':rpc.usage(),'formal_admission':False})
            raise
        finally: rpc.close()
    return json.loads((output/'result.json').read_text())


def execute(run, limit=None):
    run = Path(run).resolve(); p=native_plan(run); config=p['config']
    require(hashlib.sha256(Path(p['cli']).read_bytes()).hexdigest()==p['cli_sha256'],'native_cli_drift')
    require(json.loads((run/'native-canary/result.json').read_text()).get('status')=='observed_native_canary_passed', 'native_canary_required')
    started=time.monotonic(); completed=0
    existing=list((run/'native-actors').glob('*/result.json')) if (run/'native-actors').exists() else []
    tokens=sum((json.loads(f.read_text()).get('usage') or {}).get('totalTokens',0) for f in existing)
    while limit is None or completed < limit:
        with exclusive_run(run), Loop(run) as loop:
            pending=loop.next()
            finished = pending['status']=='observed_schedule_complete_not_qualified'
            if not finished:
                require(time.time()-p['created_at'] < config['total_wall_seconds'],'native_run_wall_guard')
                require(tokens < config['observed_tokens_guard'], 'native_observed_token_guard')
                require(pending['status']=='ready_to_dispatch','native_pending_needs_attention',pending['status'])
                slot=pending['slot']; a=loop._actor(slot)
                intent=loop.dispatch_intent(slot)
        if finished: return report(run)
        output=run/'native-actors'/slot; output.mkdir(parents=True)
        rpc=None; bound=False; failure=None; t0=time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix='ml-native-') as w:
                rpc=NativeRPC(p['cli'],Path(w),output,config); initialize_rpc(rpc)
                params=thread_params(config,Path(w),a['packet']['instruction'])
                save(output/'thread-recipe.json',params)
                r=rpc.call('thread/start',params); rpc.thread_id=r['thread']['id']; fresh_check(r)
                with exclusive_run(run), Loop(run) as loop: loop.bind(slot,rpc.thread_id,r)
                bound=True
                # Packet supplied exactly once from frozen controller record.
                text=canonical(a['packet']).decode()
                for seq in range(1, config['actor_turns_max']+1):
                    turn=rpc.start_turn(text,config)
                    if seq>1:
                        with exclusive_run(run), Loop(run) as loop:
                            loop.acknowledge_followup(slot,seq-1,rpc.thread_id,{**turn,'thread_id':rpc.thread_id})
                    raw=rpc.complete()
                    with exclusive_run(run), Loop(run) as loop:
                        response=loop.receive(slot,seq,rpc.thread_id,raw)
                        done=loop.ledger.receipt('done-'+slot)
                        if done: break
                        send=loop.followup_intent(slot,seq)
                        text=send['result']['tool_request']['message']
                require(done is not None, 'native_turn_limit')
        except Exception as exc:
            failure=str(exc)
        finally:
            if rpc: rpc.close()
        usage=rpc.usage() if rpc else None
        result={'slot':slot,'role':pending['role'],'status':'failed' if failure else 'completed',
                'error':failure,'thread_id':rpc.thread_id if rpc else None,'usage':usage,
                'wall_seconds':time.monotonic()-t0,'formal_admission':False,'full_provider_context_verified':False}
        save(output/'result.json',result)
        if failure:
            benign=(pending['role']=='solver' and config.get('solver_failure_policy')=='record_zero_skip_extraction_continue_unique_units' and
                    failure.startswith(('native_connection_error_stop_no_retry','rpc_role_timeout','native_turn_failed')))
            if bound:
                with exclusive_run(run),Loop(run) as loop:
                    if not loop.ledger.receipt('done-'+slot):
                        loop.interrupt_intent(slot,'actor_failed')
                        loop.terminate(slot,rpc.thread_id,'actor_failed',{'route':'native_process_exit','process':json.loads((output/'process.json').read_text()),'exit':json.loads((output/'exit.json').read_text()),'transport_failure_only':benign,'failure':failure})
            if not benign:
                save(run/'native-failure.json', result)
                return report(run)
            failures_path=run/'native-unit-failures.json'
            failures=json.loads(failures_path.read_text()) if failures_path.exists() else []
            save(failures_path, failures+[result])
        tokens += (usage or {}).get('totalTokens',0); completed += 1
        print(json.dumps(result),flush=True)
        save(run/'native-checkpoint.json',{'completed_this_invocation':completed,'observed_tokens':tokens,'last_slot':slot})
    return report(run)


def report(run):
    with Loop(run,readonly=True) as loop: r=loop.report()
    actors=[json.loads(f.read_text()) for f in sorted((Path(run)/'native-actors').glob('*/result.json'))]
    r['native_actors']=actors
    r['client_observed_tokens']=sum((a['usage'] or {}).get('totalTokens',0) for a in actors)
    save(Path(run)/'native-report.json',r)
    return {'status':'failed' if (Path(run)/'native-failure.json').exists() else 'observed',
            'actors':len(actors),'phases':[{k:v for k,v in p.items() if k!='memory'} for p in r['phases']],
            'client_observed_tokens':r['client_observed_tokens'],'formal_admission':False}


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--run',required=True)
    p.add_argument('--config',default=str(ROOT/'configs/lightweight_codex_v0.json'));p.add_argument('--limit',type=int)
    p.add_argument('operation',choices=['prepare','canary','execute','report']);a=p.parse_args()
    result=prepare(a.run,a.config) if a.operation=='prepare' else execute(a.run,a.limit) if a.operation=='execute' else globals()[a.operation](a.run)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
