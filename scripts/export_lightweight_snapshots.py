"""Export exact completed task files without following subject-controlled links."""
import argparse, hashlib, json, sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--run',required=True);a=p.parse_args();run=Path(a.run).resolve()
sys.path.insert(0,str(run/'source-snapshot'))
from memorylab.collaboration import Loop
from memorylab.workspace import read_workspace_bytes
from memorylab.contracts import require

exported=[]
with Loop(run,readonly=True) as loop:
    for ar in loop.report()['actors']:
        if ar['status']!='completed' or ar['role']!='solver':continue
        slot=ar['slot'];done=loop.ledger.receipt('done-'+slot)['result'];actor=loop._actor(slot)
        hashes=done['workspace_snapshot']['hashes'];out=run/'task-snapshots'/slot
        out.mkdir(parents=True,exist_ok=True)
        for name,digest in hashes.items():
            data=read_workspace_bytes(loop.workspace(actor),name)
            require(hashlib.sha256(data).hexdigest()==digest,'export_task_drift')
            target=out/name;target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():require(target.read_bytes()==data,'export_overwrite_denied')
            else:target.write_bytes(data)
        metadata={'slot':slot,'episode':actor['internal']['episode'],'protected':actor['internal']['protected'],
                  'before_engine':actor['internal']['initial_engine'],'after_hashes':hashes,'grade':done['grade'],
                  'memory_events':done['memory_events'],'memory_claims':done['memory_claims'],'final':done['final']}
        text=json.dumps(metadata,ensure_ascii=False,indent=2)+'\n';target=out/'snapshot-metadata.json'
        if target.exists():require(target.read_text()==text,'export_metadata_drift')
        else:target.write_text(text)
        exported.append(slot)
print(json.dumps({'exported':exported,'count':len(exported),'source_run':str(run)}))
