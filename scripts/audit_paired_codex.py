"""Frozen, read-only wire reconstruction and fresh standalone grade replay."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--run',required=True);a=p.parse_args();run=Path(a.run).resolve()
sys.path.insert(0,str(run/'source-snapshot'))
from memorylab.contracts import canonical,require,sha256
from memorylab.ledger import Ledger
from memorylab.cards import CanonicalCards,EvidenceStore
from memorylab.registry import Registry

plan=json.loads((run/'plan.json').read_text());unsigned=dict(plan);digest=unsigned.pop('sha256');require(sha256(unsigned)==digest,'audit_plan_drift')
for name,h in plan['source_hashes'].items():require(hashlib.sha256((run/'source-snapshot'/name).read_bytes()).hexdigest()==h,'audit_frozen_source_drift')
state=json.loads((run/'state.json').read_text());actors=[];regraded=[];tokens=0;production_rejections=[]
with Ledger(run/'ledger.sqlite',readonly=True) as ledger:
    registry=Registry(ledger,plan['kernel']);cards=CanonicalCards(EvidenceStore(ledger,plan['kernel']))
    for entry in state.get('acquisition_corpus',{}).get('cards',[]):cards.read(entry['ref'],'extractor','task/shared-acquisition',ledger.head())
    for folder in sorted((run/'actors').iterdir()):
        result_path=folder/'result.json'
        require(result_path.exists(),'audit_unsealed_started_actor')
        result=json.loads(result_path.read_text());exit_=json.loads((folder/'exit.json').read_text())
        receipt_path=folder/'production-receipt.json'
        if receipt_path.exists():
            receipt=json.loads(receipt_path.read_text())
            require(ledger.receipt(receipt['request_id'])==receipt,'audit_production_receipt_drift')
            if receipt['result']['status']=='rejected':production_rejections.append({'slot':folder.name,'code':receipt['result']['code']})
        require(exit_['exit_code'] is not None and not exit_['wire_limit_exceeded'],'audit_process_unsealed')
        for name,h in exit_['raw_hashes'].items():require(hashlib.sha256((folder/name).read_bytes()).hexdigest()==h,'audit_raw_drift')
        incoming=[json.loads(s) for s in (folder/'stdin.bin').read_text().splitlines()]
        outgoing=[json.loads(s) for s in (folder/'stdout.bin').read_text().splitlines()]
        starts=[x for x in incoming if x.get('method')=='thread/start'];require(len(starts)==1,'audit_not_fresh')
        request=starts[0];q=request['params']
        require(q['model']==plan['config']['model'] and q['ephemeral'] is True and q['environments']==[] and q['config']['features.memories'] is False,'audit_recipe_drift')
        reserved=json.loads((folder/'reserved.json').read_text());require(q['developerInstructions']==reserved['instruction'],'audit_role_instruction')
        replies=[x['result'] for x in outgoing if x.get('id')==request['id'] and 'result' in x]
        require(len(replies)==1 and replies[0]['thread']['id']==result['thread_id'] and replies[0]['instructionSources']==[] and not replies[0]['thread']['turns'] and not replies[0]['thread'].get('parentThreadId') and not replies[0]['thread'].get('forkedFromId'),'audit_inherited_context')
        turns=[x for x in incoming if x.get('method')=='turn/start'];messages=[];usage=[]
        for x in outgoing:
            require(not ('method' in x and 'id' in x),'audit_server_request')
            params=x.get('params',{});method=x.get('method')
            require(params.get('threadId',result['thread_id'])==result['thread_id'],'audit_cross_thread')
            if method in ('item/started','item/completed'):
                require(params['item']['type'] in ('userMessage','agentMessage','reasoning','plan'),'audit_subject_tool')
            if method=='item/completed' and params['item']['type']=='agentMessage':messages.append(params['item']['text'])
            if method=='thread/tokenUsage/updated':usage.append(params['tokenUsage']['total'])
        require(result['usage']==(usage[-1] if usage else None),'audit_usage_drift')
        tokens+=(result['usage'] or {}).get('totalTokens',0)
        if result['status']=='completed':
            delivery=json.loads((folder/'deliveries.json').read_text())
            require(len(turns)==len(delivery)==len(messages),'audit_turn_count')
            require(delivery[0]['input']==canonical(reserved['packet']).decode(),'audit_initial_packet')
            require(reserved['packet_sha256']==sha256(reserved['packet']),'audit_packet_hash')
            events=json.loads((folder/'memory-events.json').read_text()) if (folder/'memory-events.json').exists() else []
            require([json.loads(m) for m in messages[:-1]]==[e['request'] for e in events if e['operation']=='open'],'audit_memory_request')
            opens=[e for e in events if e['operation']=='open']
            for i,(t,d) in enumerate(zip(turns,delivery)):
                require(t['params']['threadId']==result['thread_id'] and t['params']['environments']==[] and t['params']['input']==[{'type':'text','text':d['input']}],'audit_exact_turn_input')
                turn_reply=[x['result'] for x in outgoing if x.get('id')==t['id'] and 'result' in x]
                require(len(turn_reply)==1 and turn_reply[0]==d['ack'],'audit_actual_delivery_ack')
                if i:require(d['input']==canonical(opens[i-1]['response']).decode()+'\n','audit_exact_memory_reply')
            require((folder/'final.txt').read_text()==messages[-1],'audit_exact_final')
        actors.append({'slot':folder.name,'status':result['status'],'tokens':(result['usage'] or {}).get('totalTokens'),'turns':len(turns)})
    for row in state['results']:
        folder=run/'actors'/row['slot']
        epoch=json.loads((run/f"epoch-{row['epoch']}.json").read_text());scenario=registry.resolve(epoch['scenario'])['config']
        expected_catalog=plan['catalog'][f"{row['epoch']}-{scenario['transfer_distance']}-{scenario['rule_change']}"]
        require(epoch['cases']==expected_catalog,'audit_challenge_not_applied')
        case=next(c for c in epoch['cases'] if c['id']==row['case'])
        packet=json.loads((folder/'reserved.json').read_text())['packet']
        require(packet['task']==case['public'] and set(packet)=={'role','task','memories'},'audit_gold_or_task_drift')
        events=json.loads((folder/'memory-events.json').read_text());require(packet['memories']==events[0]['response'],'audit_memory_delivery')
        if row['arm']=='off':require(packet['memories']['items']==[] and all(not e['memory_refs'] for e in events),'audit_off_memory_leak')
        if row['status']=='completed':
            require(json.loads((folder/'task.json').read_text())==case,'audit_gold_snapshot_drift')
            require(json.loads((folder/'final.txt').read_text())==json.loads((folder/'final.json').read_text()),'audit_grader_final_drift')
            process=subprocess.run([sys.executable,'-I',str(run/'source-snapshot/memorylab/paired_grader.py'),str(folder/'task.json'),str(folder/'final.json'),str(folder/'disclosed.json')],capture_output=True,text=True,timeout=10)
            require(process.returncode==0,'audit_grader_process');g=json.loads(process.stdout)
            require(all(row[k]==v for k,v in g.items() if k!='category'),'audit_grade_mismatch')
            regraded.append({'slot':row['slot'],**g})
    for research in state['research']:
        role=research['role'];slot=research['slot'];folder=run/'actors'/slot
        require(json.loads((folder/'final.txt').read_text())==research['proposal'],'audit_research_raw')
        require(ledger.receipt('commit-'+slot)==research['receipt'],'audit_registry_receipt')
        for ref in research['proposal']['evidence_refs']:cards.proposal_evidence(ref,role,'research/'+role,ledger.head())
require(tokens==state['known_tokens'],'audit_study_usage')
print(json.dumps({'category':'independent_readonly_native_pair_audit','actors_verified':len(actors),'actors':actors,
 'solver_grades_recomputed':len(regraded),'regrades':regraded,'known_tokens':tokens,'planned_pairs':plan['planned_pairs'],
 'production_rejections':production_rejections,'missing_solver_sessions':plan['planned_solver_sessions']-len(state['results']),
 'new_model_calls':0,'formal_admission':False,'real_project_gain_proved':False},indent=2))
