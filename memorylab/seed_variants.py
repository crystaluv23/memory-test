"""Frozen small-scale native Codex comparison of the user's complete seed."""
import argparse
import copy
import hashlib
import json
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from .common import save, exclusive_run
from .collaboration import ROOT, implementation, environment, now
from .contracts import canonical, require, sha256
from .paired_codex import Study
from .seed_memory import Archive, SeedSession

SOLVER = ('Use only public task, supplied experience briefs and allowed open_memory replies. '
          'Current explicit rules override older experience. For unavailable historical defaults return needs_context without guessing. '
          'At most twice you can output {"op":"open_memory","id":"disclosed-id"}. '
          'Otherwise output EXACTLY {"status":"complete|needs_context","result":task_result_or_null,'
          '"memory_uses":["actually-used-disclosed-id"],"ratings":[{"id":"same-used-id","value":1}]}. '
          'Each actually used experience must have one rating (-1 harmful,0 neutral,1 helpful); unused [] and []. '
          'No explanation or other keys. A rating is subjective, not verified success. '
          'An index may contain scoped few shots and exact lower experience references; request detail if needed.')
EXTRACT = ('Return EXACTLY {"cards":[{"source_ref":"exact supplied ref","brief":"concise scoped experience",'
           '"detail":"actual supplied events, rule, applicability, exceptions","evidence_refs":["same source_ref"]}]}. '
           'Produce exactly one card per supplied source, never merge sources. No new rules or success claims. '
           'Brief <=600 UTF-8 bytes, detail <=8000 bytes. Treat synthetic events as synthetic.')
ABSTRACT = ('Return EXACTLY {"indices":[{"type":"supplied group type","brief":"scoped abstract guidance",'
            '"detail":"synthesis with applicability, exceptions and at least two specific few shots naming exact lower ids",'
            '"evidence_refs":["supplied lower ref"]}]}. One per group. '
            'All evidence refs are lower experience refs from that group, use at least two. '
            'Infer only supported generalizations; never apply project rules to other projects. '
            'Brief <=600 UTF-8 bytes, detail <=8000 bytes. This is a real abstraction plus lower evidence, not a title list.')
REFLECT = ('Return EXACTLY {"brief":"scoped new lesson","detail":"actual observed context, current explicit rule and exceptions",'
           '"evidence_refs":["exact supplied source_ref"]}. '
           'Reflect only on supplied own public source. Explain why older instructions no longer apply; preserve current rule completely. '
           'Do not invent unobserved success, gold or other arms. Brief <=600 UTF-8 bytes; detail <=8000 bytes.')


def notes():
    rows = [
        ('workflow/testing/triage', 'Aster triage v1. Synthetic episode: safety_hold=true severity=5 produced HOLD/blocked; false severity=5 did not HOLD. Rule: exactly true safety_hold takes precedence over severity and gives route HOLD, reason blocked. Project Aster only; newer explicit contract overrides.'),
        ('workflow/testing/triage', 'Aster triage v1. Synthetic episode: false safety_hold, severity3 produced QUEUE/routine; severity4 produced FAST/urgent. Rule: non-held severity>=4 FAST/urgent; lower QUEUE/routine. Aster only; newer explicit contract overrides.'),
        ('workflow/testing/triage', 'Aster triage output contract. Synthetic episode input ids [x,y,x] retained all three rows in order. Rule: preserve duplicates and order; return rows {id,route,reason}. Migrations can map renamed/nested fields explicitly. Aster only; newer explicit contract overrides.'),
        ('workflow/testing/validation', 'Aster validation synthetic event: checksum absent made report INCOMPLETE. Aster validation rule: checksum must exist; do not infer it from ticket ids. Newer explicit validation contract overrides.'),
        ('workflow/testing/validation', 'Aster validation synthetic event: validation errors [E2,E1,E2] stayed unchanged. Preserve validation error order and duplicates, distinct from triage routing. Newer explicit contract overrides.'),
        ('workflow/testing/validation', 'Aster validation synthetic event: lint_warning alone did not block validation; failed_check did. Validation default treats failed_check as blocking and lint_warning as advisory. This is not the triage safety_hold rule. Newer explicit contract overrides.'),
        ('workflow/recovery', 'Aster recovery synthetic event: recovery marker ABSENT required inspection, PRESENT allowed restore. Never infer recovery marker from severity. This is a recovery-only convention, not support triage. Newer explicit contract overrides.'),
        ('workflow/recovery', 'Aster recovery synthetic event: backup age above24h required approval, below24h permitted restore. Recovery-only convention; do not apply to ticket routes. Newer explicit contract overrides.'),
        ('workflow/recovery', 'Aster recovery synthetic event: checksum mismatch aborted restore; match allowed restore. Recovery rule checks checksum before restoring. Recovery only, not triage; newer explicit contract overrides.'),
        ('environment/services', 'Boreal service triage archived v1 synthetic event: safety_hold=true yielded PAUSE/manual. Boreal only; never Aster. Newer explicit contract overrides.'),
        ('environment/services', 'Boreal service triage archived v1 synthetic event: severity3 yielded EXPRESS/high, severity2 BACKLOG/normal, when not held. Boreal threshold3, not Aster threshold4. Newer explicit contract overrides.'),
        ('environment/services', 'Boreal service triage archived v1 synthetic event input repeated ids retained order and duplicates. Return {id,route,reason}. Boreal only; newer explicit contract overrides.')]
    return [{'id': 'source-' + str(i), 'type': t, 'category': 'synthetic_project_preferences', 'text': text} for i, (t, text) in enumerate(rows)]


def cases(stage='screening', challenge='near'):
    rows = [{'id': 'x'+stage, 'severity': 3, 'safety_hold': False}, {'id': 'y'+stage, 'severity': 4, 'safety_hold': False},
            {'id': 'x'+stage, 'severity': 5, 'safety_hold': True}]
    if stage == 'confirmation':
        rows = [{'id': 'c', 'severity': 1, 'safety_hold': False}, {'id': 'c', 'severity': 2, 'safety_hold': False}, {'id': 'd', 'severity': 4, 'safety_hold': True}]
    def answer(routes, reasons): return [{'id': r['id'], 'route': a, 'reason': b} for r, a, b in zip(rows, routes, reasons)]
    current = 'Aster CURRENT contract supersedes all archived triage rules: safety_hold=true gives REVIEW/check; otherwise severity>=2 gives RAPID/updated; lower WAIT/updated_low. Preserve order and duplicates. Return {id,route,reason} rows.'
    oldgold = answer(['QUEUE','FAST','HOLD'], ['routine','urgent','blocked'])
    newgold = answer(['RAPID','RAPID','REVIEW'], ['updated','updated','check']) if stage == 'screening' else answer(['WAIT','RAPID','REVIEW'], ['updated_low','updated','check'])
    recallgold = oldgold if stage == 'screening' else newgold
    result = [{'id':'repeat','condition':'retention','public': {'type':'workflow/testing/triage', 'project':'Aster',
        'task':'Apply Aster most recently taught triage defaults. No new override. Return {id,route,reason} rows in input order.', 'tickets':rows}, 'gold': recallgold}]
    migrated = [{'ticket':{'key':r['id']},'signals':{'priority':r['severity'],'paused':r['safety_hold']}} for r in rows]
    if challenge == 'far': migrated = [{'envelope': {'record':r}} for r in migrated]
    result.append({'id':'transfer','condition':'transfer','public': {'type':'workflow/testing/triage','project':'Aster',
       'task':'Apply most recently taught Aster triage defaults, no new override. ticket.key maps to id; signals.priority maps to severity, signals.paused maps to safety_hold. '+
       ('Each record is wrapped inside envelope.record. ' if challenge == 'far' else '')+'Return {id,route,reason} rows in input order.', 'records':migrated}, 'gold':recallgold})
    result.append({'id':'stale','condition':'current_override','public': {'type':'workflow/testing/triage','project':'Aster','task':current,'tickets':rows},'gold':newgold,'feedback':current})
    result.append({'id':'unrelated','condition':'no_relevant_memory','public': {'type':'','task':'Return {total:sum,count:length} for these integers. No project convention applies.', 'integers':[13,-4,7]}, 'gold':{'total':16,'count':3}})
    result.append({'id':'counter','condition':'other_project_counterexample','public': {'type':'environment/services','project':'Boreal','task':'Boreal CURRENT contract: held gives STOP/current_hold; otherwise severity>=4 NOW/current_high; lower LATER/current_low. Earlier defaults do not apply. Return {id,route,reason} rows, preserve order and duplicates. held means safety_hold exactly true.','tickets':rows},'gold':answer(['LATER','NOW','STOP'] if stage=='screening' else ['LATER','LATER','STOP'],['current_low','current_high','current_hold'] if stage=='screening' else ['current_low','current_low','current_hold'])})
    result.append({'id':'updated','condition':'post_feedback_retention','public': {'type':'workflow/testing/triage','project':'Aster','task':'Apply most recently taught Aster triage defaults to fresh tickets. No new override. Return {id,route,reason} rows preserving input order.', 'tickets':rows}, 'gold':newgold})
    return result if stage == 'screening' else [result[1], result[4], result[3]]


def validate_config(c):
    require(c['schema_version']=='seed-variants-v0' and not c['automatic_retry'] and not c['formal_admission'], 'seed_unsafe_config')
    seed=c['arms']['seed']
    fields={'no_index':'indices','recent_global':'global_rank','lexical_local':'local_rank','no_reflection':'reflection'}
    for arm, field in fields.items():
        require({k for k in seed if seed[k]!=c['arms'][arm][k]}=={field}, 'seed_not_single_factor')
    for t in c['taxonomy']:
        require(re.fullmatch('[a-z]+(?:/[a-z]+)*',t) is not None, 'seed_unsafe_type')
        if '/' in t: require(t.rsplit('/',1)[0] in c['taxonomy'],'seed_orphan_type')


# re kept local to avoid allowing path text as a category.
import re

def prepare(run, config):
    run=Path(run).resolve(); require(not run.exists(),'seed_run_exists'); c=json.loads(Path(config).read_text()); validate_config(c)
    hashes=implementation()
    for name in ('scripts/audit_seed_variants.py','tests/test_seed_variants.py'):
        hashes[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    cli=str(Path(shutil.which('codex')).resolve()); run.mkdir(parents=True)
    for name in hashes:
        dest=run/'source-snapshot'/name; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(ROOT/name,dest)
    rng=random.Random(c['seed']); order=[]
    for case in cases():
        arms=list(c['arms']);rng.shuffle(arms);order.append({'case':case['id'],'arms':arms})
    p={'config':c,'source_hashes':hashes,'cli':cli,'cli_sha256':hashlib.sha256(Path(cli).read_bytes()).hexdigest(),
       'created_at':now(),'environment':environment(),'notes':notes(),'screening':cases(),
       'confirmation':{d:cases('confirmation',d) for d in c['challenges']},'order':order,
       'planned_solver_sessions':48,'formal_admission':False,'full_provider_context_verified':False}
    p['sha256']=sha256(p);save(run/'plan.json',p)
    rows=[{'stage':'screening','case':case['id'],'condition':case['condition'],'arm':arm,'status':'missing','full_success':False,'wrong_assertion':False,'needs_context':False,'valid_final':False} for case in cases() for arm in c['arms']]
    rows += [{'stage':'confirmation','case':case['id'],'condition':case['condition'],'arm':arm,'status':'missing','full_success':False,'wrong_assertion':False,'needs_context':False,'valid_final':False} for case in cases('confirmation') for arm in ('seed','candidate')]
    save(run/'state.json',{'status':'prepared','results':rows,'research':[],'known_tokens':0,'sessions':0})
    return {'run':str(run),'plan':p['sha256'],'model_calls':0}


class Experiment:
    ask=Study.ask
    def __init__(self, run):
        self.run=Path(run).resolve();self.plan=json.loads((self.run/'plan.json').read_text());p=dict(self.plan);h=p.pop('sha256')
        require(sha256(p)==h,'seed_plan_drift');self.config=p['config'];validate_config(self.config)
        for name,h in p['source_hashes'].items(): require(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==h,'seed_source_drift')
        require(hashlib.sha256(Path(p['cli']).read_bytes()).hexdigest()==p['cli_sha256'],'seed_cli_drift')
        self.state=json.loads((self.run/'state.json').read_text());self.archive=Archive(self.run);self.start=time.monotonic()
        self.active={};self.importance={};self.clock=13;self.own={}

    def checkpoint(self):save(self.run/'state.json',self.state)

    def card(self,slot,source,category,id_,stamp):
        raw=self.ask(slot,REFLECT,{'role':'reflector','source_ref':source,'source':self.archive.get(source)})
        ref=self.archive.experience(raw,[source],category,stamp,id_)
        save(self.run/'actors'/slot/'production-receipt.json',{'status':'accepted','ref':ref,'source':source})
        return ref

    def indices(self,slot,leaves):
        groups={}
        for ref in leaves:
            card=self.archive.get(ref);groups.setdefault(card['type'],[]).append(ref)
        groups={t:rs for t,rs in groups.items() if len(rs)>=2}
        if not groups:return []
        raw=self.ask(slot,ABSTRACT,{'role':'abstractor','groups':[{'type':t,'experiences':[{'ref':r,**self.archive.get(r)} for r in rs]} for t,rs in groups.items()]})
        require(type(raw) is dict and set(raw)=={'indices'} and type(raw['indices']) is list and len(raw['indices'])==len(groups),'seed_index_shape')
        result=[];seen=set()
        for item in raw['indices']:
            require(type(item) is dict and set(item)=={'type','brief','detail','evidence_refs'} and item['type'] in groups and item['type'] not in seen,'seed_index_group')
            t=item['type'];seen.add(t);supported=item['evidence_refs']
            value={k:v for k,v in item.items() if k!='type'}
            result.append(self.archive.experience(value,groups[t],t,self.clock,slot+'-'+t.replace('/','-'),1,supported))
        save(self.run/'actors'/slot/'production-receipt.json',{'status':'accepted','refs':result})
        self.clock+=1
        return result

    def acquire(self):
        refs=[self.archive.put(n) for n in self.plan['notes']];leaves=[]
        for batch in range(4):
            sources=refs[batch*3:batch*3+3];slot=f'acquire-{batch}'
            raw=self.ask(slot,EXTRACT,{'role':'extractor','sources':[{'ref':r,'source':self.archive.get(r)} for r in sources]})
            require(type(raw) is dict and set(raw)=={'cards'} and type(raw['cards']) is list and len(raw['cards'])==3,'seed_extraction_shape')
            done=[];seen=set()
            for c in raw['cards']:
                require(type(c) is dict and set(c)=={'source_ref','brief','detail','evidence_refs'} and c['source_ref'] in sources and c['source_ref'] not in seen,'seed_source_mapping')
                source=c['source_ref'];seen.add(source);i=refs.index(source);n=self.plan['notes'][i]
                v={k:x for k,x in c.items() if k!='source_ref'}
                ref=self.archive.experience(v,[source],n['type'],i+1,'leaf-'+str(i));done.append(ref);leaves.append(ref)
            save(self.run/'actors'/slot/'production-receipt.json',{'status':'accepted','refs':done})
        indices=self.indices('acquire-indices',leaves)
        save(self.run/'acquisition.json',{'sources':refs,'leaves':leaves,'indices':indices})
        for arm,spec in self.config['arms'].items():
            self.active[arm]=list(leaves)+(list(indices) if spec['indices'] else []) if spec['enabled'] else []
            self.importance[arm]={}

    def session(self,slot,arm,task):
        folder=self.run/'actors'/slot
        manifest=self.archive.projection(folder/'memory-tree',self.active[arm],self.importance[arm])
        save(folder/'memory-snapshot.json',{'active':self.active[arm],'importance':self.importance[arm],'arm':self.config['arms'][arm], 'manifest':manifest})
        return SeedSession(self.archive,self.active[arm],self.importance[arm],self.config['arms'][arm],self.config,task,folder)

    def solve(self,stage,case,arm,key=None):
        slot=f'{stage}-{case["id"]}-{arm}';folder=self.run/'actors'/slot;session=self.session(slot,arm,case['public'])
        packet={'role':'solver','task':case['public'],'memories':json.loads(session.briefs())}
        row=next(r for r in self.state['results'] if r['stage']==stage and r['case']==case['id'] and r['arm']==(key or arm))
        row.update(slot=slot,mechanism=arm)
        save(folder/'task.json',case)
        try:
            final=self.ask(slot,SOLVER,packet,session);save(folder/'final.json',final);save(folder/'disclosed.json',sorted(session.disclosed))
            command=[sys.executable,'-I',str(self.run/'source-snapshot/memorylab/seed_grader.py'),str(folder/'task.json'),str(folder/'final.json'),str(folder/'disclosed.json')]
            result=subprocess.run(command,capture_output=True,text=True,timeout=10)
            require(result.returncode==0,'seed_grader_failure');grade=json.loads(result.stdout)
            save(folder/'grader-process.json',{'command':command,'stdout':result.stdout,'stderr':result.stderr,'exit_code':result.returncode})
            row.update(status='completed',**grade)
            if grade['valid_final']:self.importance[arm]=session.ratings(final)
        except Exception as exc:
            row.update(status='failed' if (folder/'reserved.json').exists() else 'missing',failure=str(exc))
            if not str(exc).startswith(('native_connection_error_stop_no_retry','rpc_role_timeout','native_turn_failed')):raise
        finally:
            self.checkpoint();save(folder/'grade.json',row)

    def maintenance(self,case,arm):
        if not self.config['arms'][arm]['reflection']:return
        slot=f'reflect-{arm}'; prior=next(r for r in self.state['results'] if r['stage']=='screening' and r['case']==case['id'] and r['arm']==arm)
        folder=self.run/'actors'/prior['slot']
        source=self.archive.put({'kind':'own_public_feedback','namespace':arm,'category':'synthetic_project_preferences',
              'public_task':case['public'],'explicit_feedback':case['feedback'],
              'own_final':json.loads((folder/'final.json').read_text()) if (folder/'final.json').exists() else None})
        self.clock=21
        leaf=self.card(slot,source,'workflow/testing/triage','revision-'+arm,20)
        retired=[r for r in self.active[arm] if self.archive.get(r)['type']=='workflow/testing/triage']
        keep=[r for r in self.active[arm] if r not in retired]
        # A single replacement leaf cannot support an index. Preserve the independent
        # output-contract experience (order/duplicates) without the obsolete routing rules.
        order=next(r for r in retired if self.archive.get(r)['id']=='leaf-2')
        keep += [order,leaf]
        if self.config['arms'][arm]['indices']:
            keep += self.indices('reindex-'+arm,[order,leaf])
        self.active[arm]=keep
        save(self.run/'maintenance'/f'{arm}.json',{'source':source,'retired':retired,'preserved_output_contract':order,'new_leaf':leaf,'active':keep,
            'supersession_explicit':True,'original_versions_preserved':True})

    def public_summary(self,stage):
        rows=[{k:v for k,v in r.items() if k not in ('slot','failure')} for r in self.state['results'] if r['stage']==stage]
        costs={}
        for r in self.state['results']:
            if r['stage']!=stage or not r.get('slot'):continue
            path=self.run/'actors'/r['slot']/'result.json'
            if path.exists():
                actor=json.loads(path.read_text());key=r.get('mechanism',r['arm']);costs[key]=costs.get(key,0)+(actor['usage'] or {}).get('totalTokens',0)
        public_cases=self.plan['screening'] if stage=='screening' else self.plan['confirmation'][self.state['challenge']]
        return {'category':'synthetic_project_preferences','stage':stage,'rows':rows,'solver_known_tokens':costs,
                'public_tasks':[{'id':c['id'],'condition':c['condition'],'public':c['public']} for c in public_cases],
                'mechanism_configs':self.config['arms'],
                'applied_candidate':self.state.get('candidate') if stage=='confirmation' else None,
                'applied_challenge':self.state.get('challenge') if stage=='confirmation' else 'near',
                'planned_solver_sessions':42 if stage=='screening' else 6,'formal_admission':False,'real_project_gain_proved':False}

    def research(self,role,epoch,summary):
        slot=f'{role}-{epoch}';source=self.archive.put({'kind':'public_research_summary','namespace':'research/'+role,'summary':summary})
        own=self.own.get(role,[])
        if role=='proposer':
            values=self.config['eligible_candidates'];field='candidate'
            instruction='Nominate one supplied single-factor ablation for paired comparison with full seed. Prefer protective-task correctness, then retention/transfer, then actual cost. Do not claim statistical significance.'
        else:
            values=self.config['challenges'];field='challenge'
            instruction='Choose a supplied finite challenge. far adds nesting to public mappings, near retains shallow mapping. Seek a discriminating transfer test without changing hidden gold.'
        example={field:values[0],'reason':'evidence-limited reason','evidence_refs':[source]}
        session=SeedSession(self.archive,own,{},self.config['arms']['seed'],self.config,{'type':'research/mechanisms'},self.run/'actors'/slot)
        self.archive.projection(self.run/'actors'/slot/'memory-tree',own,{})
        save(self.run/'actors'/slot/'memory-snapshot.json',{'active':own,'namespace':'research/'+role})
        packet={'role':role,'source_ref':source,'public_summary':summary,'allowed':values,
                'own_memories':json.loads(session.briefs()), 'output_example':example,
                'phase':'will_be_tested' if epoch==0 else 'record_only_unactivated'}
        raw=self.ask(slot,instruction+' You may first request {\"op\":\"open_memory\",\"id\":\"disclosed-id\"} at most twice. Otherwise return exactly the output_example keys. Select one allowed value; evidence_refs=[source_ref]. No extra keys.',packet,session)
        require(type(raw) is dict and set(raw)==set(example) and raw[field] in values and type(raw['reason']) is str and raw['evidence_refs']==[source],'seed_research_rejected')
        save(self.run/'actors'/slot/'production-receipt.json',{'status':'accepted','proposal':raw,'source':source})
        memorysource=self.archive.put({'kind':'own_research_reflection_source','namespace':'research/'+role,'public_summary':summary,'own_proposal':raw})
        card=self.card(slot+'-reflect',memorysource,'research/mechanisms',slot+'-lesson',self.clock);self.clock+=1
        self.own[role]=own+[card]
        self.state['research'].append({'role':role,'epoch':epoch,'proposal':raw,'source':source,'memory':card});self.checkpoint()
        return raw[field]

    def execute(self):
        require(self.state['status']=='prepared','seed_run_not_fresh');self.state['status']='running';self.checkpoint()
        try:
            ready=self.ask('canary','Return exactly {"ready":true}.',{'role':'isolation_canary'})
            require(ready=={'ready':True},'seed_canary_failure');self.acquire()
            for case in self.plan['screening']:
                arms=next(o['arms'] for o in self.plan['order'] if o['case']==case['id'])
                for arm in arms:self.solve('screening',case,arm)
                if 'feedback' in case:
                    for arm in arms:self.maintenance(case,arm)
            summary=self.public_summary('screening');candidate=self.research('proposer',0,summary);challenge=self.research('evaluator',0,summary)
            self.state.update(candidate=candidate,challenge=challenge);self.checkpoint()
            save(self.run/'confirmation-choice.json',{'candidate':candidate,'challenge':challenge,'cases':self.plan['confirmation'][challenge]})
            for i,case in enumerate(self.plan['confirmation'][challenge]):
                arms=['seed',candidate] if i%2==0 else [candidate,'seed']
                for arm in arms:self.solve('confirmation',case,arm,'candidate' if arm==candidate else None)
            summary=self.public_summary('confirmation')
            self.research('proposer',1,summary);self.research('evaluator',1,summary)
            self.state['status']='completed'
        except Exception as exc:
            self.state.update(status='halted',failure=str(exc))
        finally:
            save(self.run/'final-memory-states.json',{'active':self.active,'importance':self.importance,'research_memory':self.own})
            self.checkpoint()
        return self.state['status']


def summarize(run):
    run=Path(run);state=json.loads((run/'state.json').read_text());result={'status':state['status'],'planned_solver_sessions':48,
        'known_client_tokens':state['known_tokens'],'sessions':state['sessions'],'formal_admission':False,'real_project_gain_proved':False,
        'category':'synthetic_project_preferences','screening':{},'confirmation':{},'candidate':state.get('candidate'),'challenge':state.get('challenge')}
    for stage in ('screening','confirmation'):
        for arm in sorted({r['arm'] for r in state['results'] if r['stage']==stage}):
            rows=[r for r in state['results'] if r['stage']==stage and r['arm']==arm]
            result[stage][arm]={'planned':len(rows),'success':sum(r['full_success'] for r in rows),'wrong_assertions':sum(r['wrong_assertion'] for r in rows),'needs_context':sum(r['needs_context'] for r in rows),
             'missing':sum(r['status']=='missing' for r in rows),'failed':sum(r['status']=='failed' for r in rows)}
    seed=[r for r in state['results'] if r['stage']=='screening' and r['arm']=='seed'];off=[r for r in state['results'] if r['stage']=='screening' and r['arm']=='off']
    memorytypes={'retention','transfer','post_feedback_retention'};protect={'current_override','no_relevant_memory','other_project_counterexample'}
    result['seed_retention_gain_observed']=all(next(s for s in seed if s['case']==r['case'])['full_success'] and not r['full_success'] for r in off if r['condition'] in memorytypes)
    result['seed_protection_passed']=all(r['full_success'] for r in seed if r['condition'] in protect)
    result['descriptive_seed_acceptance']=result['seed_retention_gain_observed'] and result['seed_protection_passed'] and state['status']=='completed'
    result['variant_superiority']='not_established_until_independent_audit_and_cost_accounting'
    save(run/'result.json',result);return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['prepare','execute','report']);parser.add_argument('--run',required=True);parser.add_argument('--config',default=str(ROOT/'configs/seed_variants_v0.json'));a=parser.parse_args()
    if a.operation=='prepare':result=prepare(a.run,a.config)
    elif a.operation=='report':result=summarize(a.run)
    else:
        with exclusive_run(a.run):result={'status':Experiment(a.run).execute()}
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
