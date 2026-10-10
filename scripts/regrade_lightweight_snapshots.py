"""Recompute frozen deterministic behavior grades in new isolated workspaces."""
import argparse,hashlib,json,sys,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--run',required=True);a=p.parse_args();run=Path(a.run).resolve()
sys.path.insert(0,str(run/'source-snapshot'))
from memorylab.scenario import materialize,verify
from memorylab.contracts import require

rows=[]
for folder in sorted((run/'task-snapshots').glob('*')):
    m=json.loads((folder/'snapshot-metadata.json').read_text())
    for name,h in m['after_hashes'].items():
        require(hashlib.sha256((folder/name).read_bytes()).hexdigest()==h,'regrade_snapshot_drift')
    with tempfile.TemporaryDirectory(prefix='ml-independent-regrade-') as w:
        workspace=Path(w);protected=materialize(workspace,m['episode'])
        require((workspace/'engine.py').read_text()==m['before_engine'],'regrade_initial_task_drift')
        (workspace/'engine.py').write_bytes((folder/'engine.py').read_bytes())
        grade=verify(workspace,run,m['episode'],protected,m['final'])
        # Execution path/timing can differ; behavior and final validity must not.
        fields=('full_success','behavior_success','false_completion','protected_violations','groups')
        require(all(grade[k]==m['grade'][k] for k in fields),'regrade_outcome_mismatch')
        rows.append({'slot':m['slot'],'full_success':grade['full_success'],'groups':grade['groups']})
print(json.dumps({'category':'independent_fresh_workspace_behavior_regrade','completed_tasks':len(rows),
 'successes':sum(r['full_success'] for r in rows),'rows':rows,'new_model_calls':0,'causal_memory_gain_proved':False},indent=2))
