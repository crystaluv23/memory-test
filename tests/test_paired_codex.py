"""Paired-study plumbing and leakage controls; model outputs are explicit fixtures."""
import json
import tempfile
import unittest
from pathlib import Path
from memorylab.paired_codex import Study, compile_cases, prepare, summarize
from memorylab.paired_grader import grade
from memorylab.contracts import ContractError


class FixtureStudy(Study):
    def ask(self, slot, instruction, packet, session=None):
        if slot == 'canary': return {'ready': True}
        if 'extract' in slot:
            cards=[]
            for i,entry in enumerate(packet['sources']):
                text=entry['source']['text']
                cards.append({'id':slot+'-'+str(i),'expected_version':0,
                    'type_hint':'research' if entry['ref']['namespace'].startswith('research/') else 'workflow',
                    'brief':text[:200],'detail':text,'evidence_refs':[entry['ref']],
                    'outcome':'unknown','status':'active','supersedes':[]})
            return {'cards':cards}
        if packet['role'] in ('proposer','evaluator'):
            self.last_research_packet=packet
            mutation={'field':'transfer_distance','old':'near','new':'far'} if packet['role']=='evaluator' else None
            return {'schema_version':1,'kind':packet['parent_spec']['kind'],'parent':packet['parent_ref'],
                'mutation':mutation,'hypothesis':'Explicit infrastructure fixture, not research inference.',
                'evidence_refs':[packet['sources'][0]['ref']]}
        epoch=int(slot[1]);caseid=slot.split('-')[1]
        case=next(c for c in compile_cases(epoch,'far' if epoch else 'near','none') if c['id']==caseid)
        if slot.endswith('-off') and case['condition']=='retention':
            return {'status':'needs_context','result':None,'memory_uses':[]}
        return {'status':'complete','result':case['gold'],'memory_uses':[]}


class PairControls(unittest.TestCase):
    def test_catalog_contains_all_required_conditions_without_answers_in_public(self):
        for distance in ('near','far'):
            for change in ('none','registered_revision'):
                cases=compile_cases(0,distance,change)
                self.assertEqual([c['condition'] for c in cases],['retention','retention','current_override','no_relevant_memory','other_project_counterexample'])
                for c in cases:self.assertNotIn('gold',c['public'])
                self.assertNotIn('>= 4',cases[0]['public']['task'])
                self.assertNotIn('FAST',json.dumps(cases[1]['public']))

    def test_current_contract_changes_gold_without_changing_archived_retention_defaults(self):
        old=compile_cases(0,'near','none');new=compile_cases(0,'far','registered_revision')
        self.assertEqual(old[0]['gold'],new[0]['gold'])
        self.assertNotEqual(old[2]['gold'],new[2]['gold'])
        self.assertEqual(old[4]['gold'],new[4]['gold'])
        self.assertEqual(new[2]['gold'][0]['route'],'RAPID')
        self.assertEqual(new[4]['gold'][0]['route'],'LATER')

    def test_grader_separates_honest_missing_context_wrong_assertion_and_valid_result(self):
        task=compile_cases(0,'near','none')[0]
        missing=grade(task,{'status':'needs_context','result':None,'memory_uses':[]},[])
        self.assertFalse(missing['full_success']);self.assertTrue(missing['needs_context']);self.assertFalse(missing['wrong_assertion'])
        wrong=grade(task,{'status':'complete','result':[],'memory_uses':[]},[])
        self.assertTrue(wrong['wrong_assertion'])
        self.assertTrue(grade(task,{'status':'complete','result':task['gold'],'memory_uses':[]},[])['full_success'])

    def test_grader_rejects_unknown_memory_claim_and_extra_keys(self):
        task=compile_cases(0,'near','none')[0]
        final={'status':'complete','result':task['gold'],'memory_uses':['foreign-card']}
        self.assertFalse(grade(task,final,[])['valid_final'])
        self.assertFalse(grade(task,{'status':'needs_context','result':task['gold'],'memory_uses':[]},[])['valid_final'])
        final['memory_uses']=[];final['fake']='anything'
        self.assertFalse(grade(task,final,[])['valid_final'])

    def test_exact_json_distinguishes_boolean_from_integer(self):
        task=compile_cases(0,'near','none')[3]
        self.assertFalse(grade(task,{'status':'complete','result':{'total':9,'count':True},'memory_uses':[]},[])['full_success'])

    def test_two_epochs_fixture_uses_registered_challenge_keeps_all_pairs_and_off_cannot_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)/'run';prepare(run,Path(__file__).resolve().parent.parent/'configs/paired_retention_v0.json')
            s=FixtureStudy(run)
            try:
                s.execute();report=summarize(run)
                self.assertEqual(len(s.state['results']),20)
                self.assertTrue(report['descriptive_retention_acceptance']) # fixture only
                self.assertTrue(s.registry.resolve(s.state['tested_scenario'])['config']['transfer_distance']=='far')
                epoch1=json.loads((run/'epoch-1.json').read_text())
                self.assertIn('records',epoch1['cases'][1]['public'])
                self.assertEqual(len(s.state['research']),3)
                self.assertEqual(s.last_research_packet['output_schema_example']['kind'],'memory')
                self.assertEqual(s.last_research_packet['output_schema_example']['parent'],s.state['tested_memory'])
                session=s.session('fixture-off','task/shared-acquisition',s.state['tested_memory'],'Aster triage',enabled=False)
                self.assertEqual(json.loads(session.briefs())['items'],[])
                item=next(iter(session.items));self.assertIn('error',json.loads(session.open({'op':'open_memory','id':item})))
                for entry in s.state['acquisition_corpus']['cards']:
                    self.assertTrue(entry['card']['evidence_refs'])
                    self.assertEqual(entry['ref']['version'],1)
                with self.assertRaisesRegex(ContractError,'paired_run_not_fresh'):s.execute()
            finally:s.ledger.close()

    def test_research_completion_refuses_partial_task_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin=Path(tmp)/'origin';origin.mkdir()
            (origin/'state.json').write_text(json.dumps({'status':'halted','failure':'paired_proposal_rejected','results':[]}))
            config=json.loads((Path(__file__).resolve().parent.parent/'configs/paired_retention_v0.json').read_text())
            config['completion_from']=str(origin);path=Path(tmp)/'config.json';path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ContractError,'incomplete_completion_origin'):prepare(Path(tmp)/'tail',path)


if __name__=='__main__':unittest.main()
