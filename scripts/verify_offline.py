"""Run offline checks once and persist the precise scope and raw evidence."""
import argparse
import hashlib
import pathlib
import re
import subprocess
import sys
import time
from datetime import datetime,timezone

ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from memorylab.common import save
from memorylab.contracts import require,sha256


def main():
    parser=argparse.ArgumentParser(description="Offline contracts only; zero live model calls")
    parser.add_argument("--output",default=str(ROOT/"runs/meta-meta-acceptance-20261008"))
    args=parser.parse_args()
    paths=sorted([p for folder,glob in (("memorylab","*.py"),("tests","*.py"),("scripts","*.py"),("configs","*.json"),("docs","*.md"))
        for p in (ROOT/folder).glob(glob)]+[ROOT/"AGENTS.md",ROOT/"README.md"])
    def snapshot():return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    before=snapshot();identity=sha256(before);base=pathlib.Path(args.output).resolve()/identity[:16]
    base.mkdir(parents=True,exist_ok=True)
    # An identical implementation can have another real test attempt. Never
    # overwrite a failed attempt or imply a cached test run was newly executed.
    attempt=1
    while True:
        output=base/("attempt-%03d"%attempt)
        try:output.mkdir();break
        except FileExistsError:attempt+=1
    command=[sys.executable,"-m","unittest","discover","-s","tests","-v"]
    started=time.monotonic()
    with (output/"unittest.stdout.txt").open("wb") as stdout,(output/"unittest.stderr.txt").open("wb") as stderr:
        proc=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr)
    raw=(output/"unittest.stderr.txt").read_bytes();count=re.search(rb"Ran (\d+) tests",raw)
    record={"schema_version":1,"timestamp":datetime.now(timezone.utc).isoformat(),"category":"infrastructure_acceptance",
        "command":command,"python_version":sys.version,"python_executable":sys.executable,"source_config_test_doc_hashes":before,
        "source_identity":identity,"source_unchanged":before==snapshot(),"exit_code":proc.returncode,
        "tests_run":int(count.group(1)) if count else None,"wall_seconds":time.monotonic()-started,
        "log_hashes":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob("*.txt")},
        "live_model_calls":0,"memory_gain_evidence":False,"kernel_frozen":False,"overall_meta_meta_acceptance":"incomplete"}
    save(output/"validation.json",record)
    print({"path":str(output),"tests_run":record["tests_run"],"exit_code":proc.returncode,"source_unchanged":record["source_unchanged"],"wall_seconds":record["wall_seconds"]})
    if proc.returncode:print(raw.decode(errors="replace")[-6000:])
    require(record["source_unchanged"],"source_changed_during_validation")
    return proc.returncode


if __name__=="__main__":raise SystemExit(main())
