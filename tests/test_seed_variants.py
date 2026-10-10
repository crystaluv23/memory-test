"""Contract and leakage tests, explicitly model-free fixtures."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from memorylab.common import save
from memorylab.contracts import ContractError
from memorylab.seed_memory import Archive, SeedSession
from memorylab.seed_variants import ROOT, Experiment, prepare, cases, validate_config
from memorylab.seed_grader import grade

C=json.loads((ROOT/'configs/seed_variants_v0.json').read_text())

class Fixture(Experiment):
    def ask(self,slot,instruction,packet,session=None):
        if slot=='canary':return {'ready':True}
        if packet['role']=='extractor':
            return {'cards':[{'source_ref':s['ref'],'brief':s['source']['text'][:400], 'detail':s['source']['text'],'evidence_refs':[s['ref']]} for s in packet['sources']]}
        if packet['role']=='abstractor':
            return {'indices':[{'type':g['type'],'brief':'Synthetic scoped abstraction','detail':'Context and few shots '+json.dumps(g['experiences']), 'evidence_refs':[c['ref'] for c in g['experiences']]} for g in packet['groups']]}
        if packet['role']=='reflector':
            return {'brief':'Source-backed synthetic lesson','detail':json.dumps(packet['source'])[:7500], 'evidence_refs':[packet['source_ref']]}
        if packet['role'] in ('proposer','evaluator'):
            field='candidate' if packet['role']=='proposer' else 'challenge'
            return {field:'no_index' if field=='candidate' else 'far','reason':'Model-free fixture','evidence_refs':[packet['source_ref']]}
        stage='confirmation' if slot.startswith('confirmation') else 'screening'
        case=next(c for c in cases(stage,'far' if stage=='confirmation' else 'near') if c['public']==packet['task'])
        return {'status':'complete','result':case['gold'],'memory_uses':[],'ratings':[]}

class SeedTests(unittest.TestCase):
    def memory(self,tmp):
        a=Archive(Path(tmp));source=a.put({'text':'Synthetic Aster rule'})
        refs=[]
        for i,t in enumerate(('workflow/testing/triage','workflow/testing/triage','environment/services')):
            refs.append(a.experience({'brief':'Aster triage rule '+str(i),'detail':'Original event and rule','evidence_refs':[source]},[source],t,i+1,'leaf'+str(i)))
        ix=a.experience({'brief':'Aster scoped synthesis','detail':'Few shots leaf0 and leaf1','evidence_refs':refs[:2]},refs[:2],'workflow/testing/triage',4,'index',1,refs[:2])
        return a,refs,ix

    def test_single_factor_and_parent_constraints(self):
        validate_config(C);bad=copy.deepcopy(C);bad['arms']['no_index']['reflection']=False
        with self.assertRaisesRegex(ContractError,'single_factor'):validate_config(bad)
        bad=copy.deepcopy(C);bad['taxonomy'].append('other/child')
        with self.assertRaisesRegex(ContractError,'orphan_type'):validate_config(bad)

    def test_index_references_and_files_and_progressive_disclosure(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,rs,ix=self.memory(tmp);folder=Path(tmp)/'view'
            manifest=a.projection(folder,rs+[ix],{})
            self.assertTrue((folder/'workflow/testing/triage/_INDEX.md').exists())
            self.assertEqual(len(manifest),4)
            self.assertIn(rs[0],(folder/'workflow/testing/triage/_INDEX.md').read_text())
            cfg=copy.deepcopy(C);cfg['local_k']=0;cfg['global_k']=1
            ss=SeedSession(a,rs+[ix],{},C['arms']['seed'],cfg,{'type':'workflow/testing/triage'},Path(tmp)/'session')
            response=json.loads(ss.briefs());self.assertEqual(response['items'][0]['id'],'index')
            self.assertNotIn('leaf0',ss.disclosed)
            self.assertEqual(json.loads(ss.open({'op':'open_memory','id':'leaf0'}))['error'],'not_disclosed')
            self.assertEqual(len(json.loads(ss.open({'op':'open_memory','id':'index'}))['supports']),2)
            self.assertIn('leaf0',ss.disclosed)
            self.assertIn('detail',json.loads(ss.open({'op':'open_memory','id':'leaf0'})))
            with self.assertRaisesRegex(ContractError,'type_conflict'):
                a.experience({'brief':'wrong','detail':'wrong','evidence_refs':[rs[0],rs[2]]},rs,'workflow/testing/triage',5,'bad',1,[rs[0],rs[2]])

    def test_off_discloses_nothing_and_global_budget_is_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,rs,ix=self.memory(tmp)
            off=SeedSession(a,rs+[ix],{},C['arms']['off'],C,{},Path(tmp)/'off')
            self.assertEqual(json.loads(off.briefs())['items'],[])
            self.assertEqual(json.loads(off.open({'op':'open_memory','id':'index'}))['error'],'not_disclosed')
            cfg=copy.deepcopy(C);cfg['brief_bytes_max']=1
            ss=SeedSession(a,rs+[ix],{},C['arms']['seed'],cfg,{},Path(tmp)/'tight')
            reply=json.loads(ss.briefs());self.assertTrue(reply['budget_dropped']);self.assertFalse(reply['items'])

    def test_ancestor_retrieval_and_importance(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,rs,ix=self.memory(tmp);cfg=copy.deepcopy(C);cfg['local_k']=1;cfg['global_k']=0
            ss=SeedSession(a,rs+[ix],{},C['arms']['seed'],cfg,{'type':'workflow/testing/triage','task':'no overlapping words'},Path(tmp)/'a')
            self.assertEqual(json.loads(ss.briefs())['items'][0]['id'],'index')
            new=ss.ratings({'ratings':[{'id':'index','value':1}],'memory_uses':['index']})
            self.assertEqual(new[ix],0.5)
            self.assertNotIn('independent_outcome',json.loads((Path(tmp)/'a/importance-update.json').read_text()))

    def test_grader_uses_disclosed_and_exact_types(self):
        task=cases()[0];f={'status':'complete','result':task['gold'],'memory_uses':['unknown'],'ratings':[{'id':'unknown','value':1}]}
        self.assertFalse(grade(task,f,[])['valid_final'])
        f['memory_uses']=[];f['ratings']=[];self.assertTrue(grade(task,f,[])['full_success'])
        f['ratings']=[{'id':'x','value':True}];f['memory_uses']=['x'];self.assertFalse(grade(task,f,['x'])['valid_final'])
        for c in cases():self.assertNotIn('gold',c['public'])

    def test_frozen_fixture_complete_48_units_and_supersession_and_research(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)/'run';prepare(run,ROOT/'configs/seed_variants_v0.json');s=Fixture(run)
            self.assertEqual(s.execute(),'completed');self.assertEqual(len(s.state['results']),48)
            self.assertTrue(all(r['status']=='completed' for r in s.state['results']))
            maintenance=json.loads((run/'maintenance/seed.json').read_text())
            self.assertTrue(set(maintenance['retired'])-set(maintenance['active']))
            for r in maintenance['retired']:self.assertTrue(s.archive.get(r))
            self.assertEqual(len(s.state['research']),4)
            self.assertEqual(len(s.own['proposer']),2)
            public=s.public_summary('confirmation')
            self.assertEqual(public['applied_challenge'],'far')
            self.assertEqual(public['applied_candidate'],'no_index')
            self.assertNotIn('gold',json.dumps(public))
            self.assertEqual(public['public_tasks'][0]['public'],cases('confirmation','far')[0]['public'])
            self.assertIn('seed',public['mechanism_configs'])
            with self.assertRaisesRegex(ContractError,'not_fresh'):s.execute()

if __name__=='__main__':unittest.main()
