"""Read-only frozen wire, source, retrieval and independent score audit."""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--run',required=True);a=p.parse_args();run=Path(a.run).resolve()
sys.path.insert(0,str(run/'source-snapshot'))
from memorylab.contracts import canonical,require,sha256
from memorylab.seed_memory import Archive,SeedSession
plan=json.loads((run/'plan.json').read_text());unsigned=dict(plan);h=unsigned.pop('sha256');require(sha256(unsigned)==h,'audit_plan_drift')
for name,h in plan['source_hashes'].items():require(hashlib.sha256((run/'source-snapshot'/name).read_bytes()).hexdigest()==h,'audit_source_drift')
state=json.loads((run/'state.json').read_text());archive=Archive(run);actors=[];tokens=0;threads=set();costs={}
objects=list((run/'objects').glob('*.json'))
for path in objects:
    record=archive.get(path.stem)
    if record.get('kind')=='experience':
        for ref in record['evidence_refs']:archive.get(ref)
        for ref in record['support_refs']:
            lower=archive.get(ref);require(lower['level']==0 and (lower['type']==record['type'] or lower['type'].startswith(record['type']+'/')),'audit_invalid_support')
for folder in sorted((run/'actors').iterdir()):
    if not (folder/'reserved.json').exists():continue
    result=json.loads((folder/'result.json').read_text());exit_=json.loads((folder/'exit.json').read_text());require(exit_['exit_code'] is not None,'audit_unsealed')
    for name,h in exit_['raw_hashes'].items():require(hashlib.sha256((folder/name).read_bytes()).hexdigest()==h,'audit_raw_drift')
    incoming=[json.loads(s) for s in (folder/'stdin.bin').read_text().splitlines()];outgoing=[json.loads(s) for s in (folder/'stdout.bin').read_text().splitlines()]
    starts=[x for x in incoming if x.get('method')=='thread/start'];require(len(starts)==1,'audit_fresh_thread')
    start=starts[0];params=start['params'];reserved=json.loads((folder/'reserved.json').read_text());packet=reserved['packet']
    require(params['model']==plan['config']['model'] and params['ephemeral'] and params['environments']==[] and not params['config']['features.memories'],'audit_recipe')
    require(params['developerInstructions']==reserved['instruction'] and sha256(packet)==reserved['packet_sha256'],'audit_instruction')
    reply=next(x['result'] for x in outgoing if x.get('id')==start['id'] and 'result' in x)
    thread=reply['thread'];require(not thread['turns'] and not thread.get('parentThreadId') and not thread.get('forkedFromId') and reply['instructionSources']==[],'audit_inheritance')
    require(thread['id'] not in threads and thread['id']==result['thread_id'],'audit_reused_thread');threads.add(thread['id'])
    usage=[];messages=[]
    for event in outgoing:
        require(not ('method' in event and 'id' in event),'audit_server_request')
        q=event.get('params',{});method=event.get('method')
        require(q.get('threadId',result['thread_id'])==result['thread_id'],'audit_cross_thread')
        if method in ('item/started','item/completed'):require(q['item']['type'] in ('userMessage','agentMessage','reasoning','plan'),'audit_actor_tool')
        if method=='item/completed' and q['item']['type']=='agentMessage':messages.append(q['item']['text'])
        if method=='thread/tokenUsage/updated':usage.append(q['tokenUsage']['total'])
    require(result['usage']==(usage[-1] if usage else None),'audit_usage');t=(result['usage'] or {}).get('totalTokens',0);tokens+=t;costs[folder.name]=t
    turns=[x for x in incoming if x.get('method')=='turn/start']
    if result['status']=='completed':
        delivery=json.loads((folder/'deliveries.json').read_text());events=json.loads((folder/'memory-events.json').read_text()) if (folder/'memory-events.json').exists() else []
        opens=[e for e in events if e['operation']=='open']
        require(len(delivery)==len(turns)==len(messages),'audit_turns')
        require(delivery[0]['input']==canonical(packet).decode(),'audit_first_packet')
        require([json.loads(m) for m in messages[:-1]]==[e['request'] for e in opens],'audit_open_requests')
        for i,(turn,d) in enumerate(zip(turns,delivery)):
            require(turn['params']['input']==[{'type':'text','text':d['input']}] and turn['params']['environments']==[] and turn['params']['threadId']==result['thread_id'],'audit_delivery')
            ack=next(x['result'] for x in outgoing if x.get('id')==turn['id'] and 'result' in x)
            require(ack==d['ack'],'audit_actual_ack')
            if i:require(d['input']==canonical(opens[i-1]['response']).decode()+'\n','audit_open_reply')
        require((folder/'final.txt').read_text()==messages[-1],'audit_final')
    for path in (folder/'memory-tree').rglob('manifest.json') if (folder/'memory-tree').exists() else []:
        for entry in json.loads(path.read_text()):require(hashlib.sha256((path.parent/entry['path']).read_bytes()).hexdigest()==entry['sha256'],'audit_physical_file')
    actors.append({'slot':folder.name,'status':result['status'],'tokens':t if result['usage'] else None,'turns':len(turns)})
require(tokens==state['known_tokens'] and len(actors)==state['sessions'],'audit_accounting')
regrades=[];retrieval=[]
with tempfile.TemporaryDirectory(prefix='seed-independent-audit-') as scratch:
    for row in state['results']:
        if not row.get('slot'):continue
        folder=run/'actors'/row['slot']
        if not (folder/'reserved.json').exists():continue
        packet=json.loads((folder/'reserved.json').read_text())['packet'];snapshot=json.loads((folder/'memory-snapshot.json').read_text())
        case=next(c for c in (plan['screening'] if row['stage']=='screening' else plan['confirmation'][state['challenge']]) if c['id']==row['case'])
        require(packet['task']==case['public'] and set(packet)=={'role','task','memories'},'audit_public_task_or_gold_leak')
        require(json.loads((folder/'task.json').read_text())==case,'audit_gold_drift')
        events=json.loads((folder/'memory-events.json').read_text());ss=SeedSession(archive,snapshot['active'],snapshot['importance'],snapshot['arm'],plan['config'],case['public'],Path(scratch)/row['slot'])
        require(json.loads(ss.briefs())==packet['memories']==events[0]['response'],'audit_retrieval_drift')
        for event in events[1:]:require(json.loads(ss.open(event['request']))==event['response'],'audit_detail_drift')
        if row['arm']=='off':require(packet['memories']['items']==[] and not ss.disclosed,'audit_off_leak')
        retrieval.append({'slot':row['slot'],'brief_bytes':ss.brief_bytes,'detail_bytes':ss.detail_bytes,'opens':len(ss.opened),'dropped':packet['memories']['budget_dropped']})
        if row['status']=='completed':
            require(json.loads((folder/'final.txt').read_text())==json.loads((folder/'final.json').read_text()),'audit_grade_input')
            disclosed=json.loads((folder/'disclosed.json').read_text());require(disclosed==sorted(ss.disclosed),'audit_disclosed')
            process=subprocess.run([sys.executable,'-I',str(run/'source-snapshot/memorylab/seed_grader.py'),str(folder/'task.json'),str(folder/'final.json'),str(folder/'disclosed.json')],capture_output=True,text=True,timeout=10)
            require(process.returncode==0,'audit_grader');grade=json.loads(process.stdout);require(all(row[k]==v for k,v in grade.items()),'audit_score_mismatch')
            if grade['valid_final']:
                actual=json.loads((folder/'importance-update.json').read_text())
                require(ss.ratings(json.loads((folder/'final.json').read_text()))==actual['importance'],'audit_credit_drift')
            regrades.append({'slot':row['slot'],**grade})
for research in state['research']:
    slot=research['role']+'-'+str(research['epoch']);packet=json.loads((run/'actors'/slot/'reserved.json').read_text())['packet']
    require(packet['source_ref']==research['source'] and json.loads((run/'actors'/slot/'final.txt').read_text())==research['proposal'],'audit_research')
    for m in packet['own_memories']['items']:
        own=archive.get(m['ref']);require(all(archive.get(r).get('namespace')=='research/'+research['role'] for r in own['evidence_refs']),'audit_research_isolation')
maintenance=[]
for path in (run/'maintenance').glob('*.json') if (run/'maintenance').exists() else []:
    data=json.loads(path.read_text());arm=path.stem;source=archive.get(data['source']);require(source['namespace']==arm and 'gold' not in source,'audit_maintenance_leak')
    for ref in data['retired']:archive.get(ref)
    for ref in data['active']:
        card=archive.get(ref)
        require(not set(card['support_refs']) & (set(data['retired'])-{data['preserved_output_contract']}),'audit_stale_index')
    maintenance.append({'arm':arm,'retired':len(data['retired']),'old_versions_preserved':True})
# Equal allocation of genuinely shared native prep. Per-arm maintenance charged fully.
c=json.loads((run/'plan.json').read_text())['config'];enabled=[a for a,s in c['arms'].items() if s['enabled']];indexed=[a for a,s in c['arms'].items() if s['indices']]
prep=sum(t for slot,t in costs.items() if slot.startswith('acquire-') and slot!='acquire-indices');ix=costs.get('acquire-indices',0)
arm_cost={}
for arm,spec in c['arms'].items():
    arm_cost[arm]={'shared_acquisition':prep/len(enabled) if spec['enabled'] else 0,'shared_index':ix/len(indexed) if spec['indices'] else 0,
        'screening_solvers':sum(t for slot,t in costs.items() if slot.startswith('screening-') and slot.endswith('-'+arm)),
        'maintenance':costs.get('reflect-'+arm,0)+costs.get('reindex-'+arm,0),
        'confirmation_solvers':sum(t for slot,t in costs.items() if slot.startswith('confirmation-') and slot.endswith('-'+arm))}
    arm_cost[arm]['screening_total']=sum(arm_cost[arm][k] for k in ('shared_acquisition','shared_index','screening_solvers','maintenance'))
print(json.dumps({'audit_passed':True,'actors_verified':len(actors),'actors':actors,'solver_grades_recomputed':len(regrades),
 'known_tokens':tokens,'unknown_usage_sessions':sum(a['tokens'] is None for a in actors),'new_model_calls':0,'planned_solver_sessions':48,'missing':sum(r['status']=='missing' for r in state['results']),
 'failed':sum(r['status']=='failed' for r in state['results']),'immutable_objects_verified':len(objects),'maintenance':maintenance,
 'arm_cost_allocation':arm_cost,'retrieval':retrieval,'formal_admission':False,'full_provider_context_verified':False,'provider_fee':None},ensure_ascii=False,indent=2))
