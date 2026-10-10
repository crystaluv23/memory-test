"""Read-only independent reconstruction of a sealed native observational run."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--snapshot',action='store_true');a=p.parse_args()
run=Path(a.run).resolve()
sys.path.insert(0,str(run/'source-snapshot' if a.snapshot else Path(__file__).resolve().parent.parent))
from memorylab.collaboration import Loop, resolve_capture
from memorylab.contracts import require,canonical

native_plan=json.loads((run/'native-plan.json').read_text())
frozen_config=json.loads((run/'source-snapshot/configs'/native_plan.get('config_file','lightweight_codex_v0.json')).read_text())
require(native_plan['config']==frozen_config,'audit_native_config_not_frozen')
checks=[]; total=0
with Loop(run,readonly=True) as loop:
    report=loop.report()
    for ar in report['actors']:
        if ar['status']!='completed':continue
        folder=run/'native-actors'/ar['slot']
        result=json.loads((folder/'result.json').read_text())
        exit_=json.loads((folder/'exit.json').read_text())
        require(not exit_['wire_limit_exceeded'] and exit_['exit_code'] is not None,'audit_process_not_sealed')
        for name,digest in exit_['raw_hashes'].items():
            require(hashlib.sha256((folder/name).read_bytes()).hexdigest()==digest,'audit_raw_hash_drift')
        incoming=[json.loads(line) for line in (folder/'stdin.bin').read_text().splitlines()]
        outgoing=[json.loads(line) for line in (folder/'stdout.bin').read_text().splitlines()]
        starts=[x for x in incoming if x.get('method')=='thread/start'];require(len(starts)==1,'audit_fresh_count')
        params=starts[0]['params'];require(params['model']==frozen_config['model'],'audit_model_drift');require(params['ephemeral'] is True and params['environments']==[] and
             params['config']['features.memories'] is False,'audit_discovery_policy')
        sid=starts[0]['id'];rs=[x['result'] for x in outgoing if x.get('id')==sid and 'result' in x]
        require(len(rs)==1 and rs[0]['thread']['id']==ar['agent_id'] and rs[0]['instructionSources']==[] and
            not rs[0]['thread'].get('turns') and not rs[0]['thread'].get('parentThreadId') and
            not rs[0]['thread'].get('forkedFromId'),'audit_thread_binding')
        turns=[x for x in incoming if x.get('method')=='turn/start'];finals=[];done=[];usage=[]
        for x in outgoing:
            require(not ('id' in x and 'method' in x),'audit_subject_server_request')
            q=x.get('params',{});method=x.get('method')
            if method in ('item/started','item/completed'):
                require(q['item']['type'] in ('userMessage','agentMessage','reasoning','plan'),'audit_subject_tool')
            if method=='item/completed' and q['item']['type']=='agentMessage':finals.append(q['item']['text'])
            if method=='turn/completed':done.append(q['turn']);require(q['turn']['status']=='completed','audit_failed_turn')
            if method=='thread/tokenUsage/updated':usage.append(q['tokenUsage']['total'])
        messages=loop._messages(ar['slot'])
        require(len(turns)==len(done)==len(finals)==len(messages),'audit_turn_denominator')
        actor=loop._actor(ar['slot'])
        require(turns[0]['params']['input']==[{'type':'text','text':canonical(actor['packet']).decode()}],'audit_initial_packet')
        for index,(turn,final,msg) in enumerate(zip(turns,finals,messages)):
            require(turn['params']['threadId']==ar['agent_id'] and turn['params']['environments']==[] and final==msg['raw'],'audit_exact_model_request')
            if index:
                reserve=loop.ledger.receipt('send-'+ar['slot']+'-'+str(index))
                require(turn['params']['input']==[{'type':'text','text':reserve['result']['tool_request']['message']}],'audit_exact_service_reply')
                ack=loop.ledger.receipt('delivered-'+ar['slot']+'-'+str(index))
                turn_response=[x['result'] for x in outgoing if x.get('id')==turn['id'] and 'result' in x]
                require(len(turn_response)==1 and ack['result']['record']['tool_result']=={**turn_response[0],'thread_id':ar['agent_id']},'audit_native_delivery_ack')
        require(usage and result['usage']==usage[-1],'audit_usage_from_raw')
        u=usage[-1];require(u['totalTokens']==u['inputTokens']+u['outputTokens'],'audit_token_sum')
        total+=u['totalTokens']
        checks.append({'slot':ar['slot'],'turns':len(turns),'tokens':u['totalTokens'],'raw_sha256':hashlib.sha256((folder/'stdout.bin').read_bytes()).hexdigest()})
print(json.dumps({'category':'independent_readonly_transport_and_production_receipt_audit',
 'completed_actors_verified':len(checks),'observed_tokens':total,'actors':checks,
 'planned_tasks_materialized':sum(p['planned'] for p in report['phases']),'unmaterialized_schedule_entries':[p['phase'] for p in report['planned_schedule'] if not p['materialized']],'completed_tasks':sum(p['completed'] for p in report['phases']),
 'task_successes':sum(p['successes'] for p in report['phases']),
 'phases':[{'phase':p['phase'],'planned':p['planned'],'completed':p['completed'],'successes':p['successes']} for p in report['phases']],
 'full_provider_context_verified':False,'causal_memory_gain_proved':False,'formal_admission':False},indent=2))
