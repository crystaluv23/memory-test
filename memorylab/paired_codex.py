"""Two bounded native-Codex epochs, with frozen synthetic retention pairs."""
import argparse
import copy
import hashlib
import json
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .cards import CanonicalCards, EvidenceStore
from .collaboration import ROOT, environment, implementation, now
from .common import exclusive_run, save
from .contracts import canonical, exact_object, read_kernel, require, sha256
from .ledger import Ledger
from .kernel_cli import parse_json
from .lightweight_codex import NativeRPC, fresh_check, initialize_rpc, thread_params
from .registry import Registry
from .runtime import RuntimeBuilder, RuntimeSession, file_log
from .stores import CorpusStore

SOLVER = ('Solve only the public task using its current explicit contract and supplied memories. '
          'Project conventions learned earlier may be unavailable in this independent session. '
          'If required defaults are unavailable, do not invent them: use status needs_context and result null. '
          'Current explicit contracts override earlier memories. You may return {"op":"open_memory","id":"disclosed-id"} '
          'to request details, at most twice. Otherwise return exactly '
          '{"status":"complete|needs_context","result":task_result,"memory_uses":["disclosed-id"]}. '
          'Only claim memory ids you actually used; use [] if none. No explanation or extra keys.')
EXTRACTOR = ('Extract zero to three canonical experience cards ONLY from the supplied source records. '
             'Return {"cards":[{"id":"short-id","expected_version":0,"type_hint":"workflow",'
             '"brief":"concise rule with project scope and override condition","detail":"context, actual rule, applicability, exceptions",'
             '"evidence_refs":[exact_source_ref],"outcome":"unknown","status":"active","supersedes":[]}]}. '
             'Do not infer task success or new rules. Keep brief below 700 UTF-8 bytes. No extra keys. '
             'For research use type_hint research; each new card id is unique. Never copy hidden answers.')
RESEARCH = ('Use only supplied public summaries, own sources and own memories. Output a Registry proposal '
            'with exactly schema_version=1, kind, parent (exact supplied ref), mutation (null or '
            '{field,old,new}), hypothesis, evidence_refs (exact supplied source or own card refs). '
            'Only mutate one permitted field to a registered value; noop is valid. '
            'Describe observed synthetic behavior, not statistical or general coding benefit. '
            'An optional {"op":"open_memory","id":"disclosed-id"} requests own memory details.')


def compile_cases(epoch, distance, change):
    """Entire finite catalog is frozen before calls, including controller-only gold."""
    require(distance in ("near", "far") and change in ("none", "registered_revision"), "unregistered_pair_case")
    prefix = f"e{epoch}-"
    rows = [{"id": prefix+"a", "severity": 3, "safety_hold": False},
            {"id": prefix+"b", "severity": 4, "safety_hold": False},
            {"id": prefix+"a", "severity": 5, "safety_hold": True}]
    def answer(route, reason): return [{"id": r["id"], "route": x, "reason": y} for r,x,y in zip(rows,route,reason)]
    cases = [{"id":"repeat", "condition":"retention", "public":{
        "task":"Apply the previously taught Aster support triage defaults to these tickets. Return a list of {id,route,reason} in input order. Preserve duplicates. No current override.",
        "project":"Aster", "tickets":rows}, "gold":answer(["QUEUE","FAST","HOLD"],["routine","urgent","blocked"])}]
    # Explicit field mapping carries no historical threshold or route labels.
    transfer = copy.deepcopy(rows)
    for r in transfer: r["priority"] = r.pop("severity"); r["paused"] = r.pop("safety_hold")
    public = {"task":"Apply Aster support triage defaults to migrated tickets: priority maps to severity, paused maps to safety_hold. Return {id,route,reason} in input order, retaining duplicates. No current override.", "project":"Aster"}
    if distance == "far":
        public["records"] = [{"ticket":{"code":r["id"]}, "signals":{"priority":r["priority"], "paused":r["paused"]}} for r in transfer]
        public["task"] += " Nested ticket.code is the output id; signals holds the mapped fields."
    else: public["tickets"] = transfer
    cases.append({"id":"transfer", "condition":"retention", "public":public, "gold":cases[0]["gold"]})
    threshold = 2 if change == "registered_revision" else 5
    cases.append({"id":"stale", "condition":"current_override", "public":{
        "task":f"Aster CURRENT triage contract supersedes every archived default: safety_hold=true gives route REVIEW/reason check; otherwise severity >= {threshold} gives route RAPID/reason updated; lower gives route WAIT/reason updated_low. Preserve order and duplicate ids. Return a list of {{id,route,reason}}.",
        "project":"Aster", "tickets":rows},
        "gold":answer(["RAPID","RAPID","REVIEW"] if threshold == 2 else ["WAIT","WAIT","REVIEW"],
                      ["updated","updated","check"] if threshold == 2 else ["updated_low","updated_low","check"])})
    cases.append({"id":"unrelated", "condition":"no_relevant_memory", "public":{
        "task":"Unrelated arithmetic task. Return {total: sum of the integers, count: number of integers}. No project convention applies.",
        "integers":[7+epoch,-3,5]}, "gold":{"total":9+epoch,"count":3}})
    cases.append({"id":"counter", "condition":"other_project_counterexample", "public":{
        "task":"Boreal CURRENT support triage contract (archived Aster and Boreal defaults do not apply): safety_hold=true gives route STOP/reason current_hold; otherwise severity >= 4 gives route NOW/reason current_high; lower gives route LATER/reason current_low. Preserve order and duplicates. Return {id,route,reason} rows.",
        "project":"Boreal", "tickets":rows}, "gold":answer(["LATER","NOW","STOP"],["current_low","current_high","current_hold"])})
    return cases


def prepare(run, config_path):
    run = Path(run).resolve(); require(not run.exists(), "paired_run_exists")
    config = json.loads(Path(config_path).read_text())
    require(config["schema_version"] == "paired-retention-v0" and config["epochs"] == 2 and
            config["cases_per_epoch"] == 5 and config["formal_admission"] is False and
            config["automatic_retry"] is False, "unsafe_pair_config")
    cli = str(Path(shutil.which("codex")).resolve())
    catalog = {f"{e}-{d}-{c}":compile_cases(e,d,c) for e in range(2) for d in ("near","far") for c in ("none","registered_revision")}
    order = []; rng = random.Random(config["seed"])
    for e in range(2):
        for case in compile_cases(e,"near","none"):
            arms = ["on","off"]; rng.shuffle(arms)
            order.append({"epoch":e,"case":case["id"],"arms":arms})
    completion = None
    hashes = implementation(); run.mkdir(parents=True)
    if config.get('completion_from'):
        origin=(ROOT/config['completion_from']).resolve();state=json.loads((origin/'state.json').read_text())
        require(state['status']=='halted' and state.get('failure')=='paired_proposal_rejected' and
                len(state['results'])==20 and all(r['status']=='completed' for r in state['results']), 'incomplete_completion_origin')
        folder=origin/'actors/proposer-1';bad=json.loads((folder/'production-receipt.json').read_text())
        require(bad['result']['code']=='role_kind_mismatch' and
                json.loads((folder/'exit.json').read_text())['exit_code'] is not None,'unknown_completion_origin')
        old_plan=json.loads((origin/'plan.json').read_text());old_epoch=json.loads((origin/'epoch-1.json').read_text())
        old_hash=hashlib.sha256((origin/'ledger.sqlite').read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix='ml-completion-research-copy-') as tmp:
            image=Path(tmp)/'origin.sqlite'
            with Ledger(origin/'ledger.sqlite',readonly=True) as old:
                db=sqlite3.connect(image);old.db.backup(db);db.close()
            with Ledger(image) as old:
                store=CorpusStore(CanonicalCards(EvidenceStore(old,old_plan['kernel'])))
                ref=store.capture('completion-own-research','research/proposer',old.head())
                snapshot=store.resolve(ref)
                save(run/'origin-corpus-snapshot.json',snapshot)
                fork=store.fork('completion-import',ref,run/'ledger.sqlite','research/proposer',Registry(old,old_plan['kernel']),[old_epoch['memory']],purpose='initial_checkpoint')
        require(hashlib.sha256((origin/'ledger.sqlite').read_bytes()).hexdigest()==old_hash,'completion_origin_modified')
        completion={'origin':str(origin),'ledger_sha256':old_hash,'plan_sha256':old_plan['sha256'],
                    'public_rows':state['results'],'public_rows_sha256':sha256(state['results']),
                    'tested_memory':old_epoch['memory'],'prior_rejection':bad,'fork':fork,
                    'original_known_tokens':state['known_tokens']}
        catalog={};order=[]
    for name in hashes:
        dest=run/"source-snapshot"/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
    plan = {"config":config,"config_sha256":sha256(config),"cli":cli,"cli_sha256":hashlib.sha256(Path(cli).read_bytes()).hexdigest(),
            "source_hashes":hashes,"environment":environment(),"kernel":read_kernel(),"catalog":catalog,"order":order,
            "created_at":now(),"category":"synthetic_project_preferences","planned_pairs":0 if completion else 10,"planned_solver_sessions":0 if completion else 20,
            "formal_admission":False,"full_provider_context_verified":False}
    if completion:plan['completion']=completion
    plan["sha256"]=sha256(plan);save(run/"plan.json",plan)
    save(run/"state.json",{"status":"prepared","results":[],"research":[],"known_tokens":0,"sessions":0})
    return {"run":str(run),"plan":plan["sha256"],"model_calls":0}


class Study:
    def __init__(self, run):
        self.run=Path(run).resolve();self.plan=json.loads((self.run/"plan.json").read_text());p=dict(self.plan);h=p.pop("sha256")
        require(sha256(p)==h,"paired_plan_drift")
        self.config=self.plan["config"]
        for name,h in self.plan["source_hashes"].items():
            require(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==h,"paired_source_drift")
        require(hashlib.sha256(Path(self.plan["cli"]).read_bytes()).hexdigest()==self.plan["cli_sha256"],"paired_cli_drift")
        self.state=json.loads((self.run/"state.json").read_text())
        self.ledger=Ledger(self.run/"ledger.sqlite");self.registry=Registry(self.ledger,self.plan["kernel"])
        self.evidence=EvidenceStore(self.ledger,self.plan["kernel"]);self.cards=CanonicalCards(self.evidence)
        self.builder=RuntimeBuilder(self.cards,self.registry);self.start=time.monotonic()

    def checkpoint(self): save(self.run/"state.json",self.state)

    def source(self, id_, ns, text, kind="user_input"):
        return self.evidence.register(id_,ns,kind,id_,now(),text)

    def session(self, slot, ns, spec, text, enabled=True):
        cutoff=self.ledger.head();projection=self.builder.build(ns,cutoff,spec)
        path=self.run/"actors"/slot
        save(path/"memory-snapshot.json",{"cutoff":cutoff,"projection":projection})
        return RuntimeSession(self.ledger,projection["projection_ref"],text,persist=file_log(path/"memory-events.json"),enabled=enabled)

    def ask(self, slot, instruction, packet, session=None):
        folder=self.run/"actors"/slot
        require(not (folder/"reserved.json").exists(),"paired_no_resend")
        require(self.state["known_tokens"] < self.config["observed_tokens_guard"] and
                self.state["sessions"] < self.config["sessions_max"] and
                time.monotonic()-self.start < self.config["total_wall_seconds"],"paired_budget_guard")
        self.state["sessions"]+=1;self.checkpoint()
        save(folder/"reserved.json",{"slot":slot,"instruction":instruction,"packet":packet,"packet_sha256":sha256(packet),"at":now()})
        rpc=None;raw=None;failure=None;deliveries=[];t0=time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="ml-paired-subject-") as w:
                rpc=NativeRPC(self.plan["cli"],Path(w),folder,self.config);initialize_rpc(rpc)
                params=thread_params(self.config,Path(w),instruction);save(folder/"thread-recipe.json",params)
                thread=rpc.call("thread/start",params);fresh_check(thread);rpc.thread_id=thread["thread"]["id"]
                save(folder/"thread-start.json",thread);text=canonical(packet).decode()
                for i in range(self.config["actor_turns_max"]):
                    ack=rpc.start_turn(text,self.config);deliveries.append({"input":text,"ack":ack,"thread_id":rpc.thread_id})
                    save(folder/"deliveries.json",deliveries);raw=rpc.complete();obj=parse_json(raw)
                    if type(obj) is dict and obj.get("op")=="open_memory":
                        require(session is not None,"paired_role_memory_denied")
                        require(i+1 < self.config["actor_turns_max"],"paired_turn_limit")
                        text=session.open(obj).decode();raw=None
                    else: break
                require(raw is not None,"paired_missing_final");(folder/"final.txt").write_text(raw)
        except Exception as exc: failure=str(exc)
        finally:
            if rpc:rpc.close()
        usage=rpc.usage() if rpc else None
        self.state["known_tokens"]+=(usage or {}).get("totalTokens",0)
        result={"slot":slot,"status":"failed" if failure else "completed","failure":failure,"usage":usage,
                "thread_id":rpc.thread_id if rpc else None,"wall_seconds":time.monotonic()-t0,"full_provider_context_verified":False}
        save(folder/"result.json",result);self.checkpoint();print(json.dumps(result),flush=True)
        if failure: raise RuntimeError(failure)
        return parse_json(raw)

    def extract(self, slot, ns, refs):
        cutoff=self.ledger.head();sources=[{"ref":r,"source":self.evidence.read(r,"extractor",ns,cutoff)} for r in refs]
        raw=self.ask(slot,EXTRACTOR+f' Prefix every new card id with {slot}-.',
                     {"role":"extractor","sources":sources,"card_id_prefix":slot+'-'})
        receipt=self.cards.extract("commit-"+slot,raw,ns,cutoff,now())
        save(self.run/"actors"/slot/"production-receipt.json",receipt)
        require(receipt["result"]["status"]=="extracted","paired_extraction_rejected")
        return receipt

    def paired_epoch(self, epoch, memory, scenario):
        sc=self.registry.resolve(scenario)["config"];key=f'{epoch}-{sc["transfer_distance"]}-{sc["rule_change"]}'
        cases=self.plan["catalog"][key]
        save(self.run/f"epoch-{epoch}.json",{"memory":memory,"scenario":scenario,"cases":cases})
        for case in cases:
            ordering=next(r["arms"] for r in self.plan["order"] if r["epoch"]==epoch and r["case"]==case["id"])
            for arm in ordering:
                slot=f'e{epoch}-{case["id"]}-{arm}';folder=self.run/"actors"/slot
                session=self.session(slot,"task/shared-acquisition",memory,canonical(case["public"]).decode(),enabled=arm=="on")
                briefs=json.loads(session.briefs());packet={"role":"solver","task":case["public"],"memories":briefs}
                failure=None; grade={"full_success":False,"needs_context":False,"wrong_assertion":False,"valid_final":False}
                try:
                    final=self.ask(slot,SOLVER,packet,session)
                    save(folder/"final.json",final);save(folder/"task.json",case);save(folder/"disclosed.json",sorted(session.disclosed))
                    command=[sys.executable,"-I",str(self.run/"source-snapshot/memorylab/paired_grader.py"),str(folder/"task.json"),str(folder/"final.json"),str(folder/"disclosed.json")]
                    process=subprocess.run(command,capture_output=True,text=True,timeout=10)
                    require(process.returncode==0,"paired_grader_failed");grade=json.loads(process.stdout)
                    save(folder/"grader-process.json",{"command":command,"stdout":process.stdout,"stderr":process.stderr,"exit_code":process.returncode})
                except Exception as exc:
                    failure=str(exc)
                    if not failure.startswith(("native_connection_error_stop_no_retry","rpc_role_timeout","native_turn_failed")):
                        raise
                row={"epoch":epoch,"case":case["id"],"condition":case["condition"],"arm":arm,"slot":slot,
                     "status":"terminated" if failure else "completed","failure":failure,**grade}
                save(folder/"grade.json",row);self.state["results"].append(row);self.checkpoint()

    def research(self, slot, role, parent, memory, public_summary=None):
        ns="research/"+role;field_set=self.config["memory_mutable_fields" if role=="proposer" else "scenario_mutable_fields"]
        summary=public_summary or {"category":"synthetic_project_preferences","rows":self.state["results"],"planned_solver_sessions":20,
                 "known_client_tokens_so_far":self.state["known_tokens"],"formal_admission":False}
        ref=self.source("summary-"+slot,ns,canonical(summary).decode(),"visible_outcome")
        spec=self.registry.resolve(parent);session=self.session(slot,ns,memory,canonical(summary).decode());briefs=json.loads(session.briefs())
        packet={"role":role,"parent_ref":parent,"parent_spec":spec,"mutable_fields":{f:self.plan["kernel"][spec["kind"]+"_fields"][f] for f in field_set},
                "sources":[{"ref":ref,"text":canonical(summary).decode()}],"own_memories":briefs,
                "study_scope":"retention task defaults intentionally available only via past learning; controls have complete current rules. Final untested proposal will not activate."}
        packet['output_schema_example']={'schema_version':1,'kind':spec['kind'],'parent':parent,'mutation':None,
                                         'hypothesis':'Your evidence-limited hypothesis','evidence_refs':[ref]}
        raw=self.ask(slot,RESEARCH+f' This role outputs kind exactly {json.dumps(spec["kind"])}.',packet,session)
        mutation=raw.get("mutation") if type(raw) is dict else None
        require(mutation is None or mutation.get("field") in field_set,"paired_mutation_not_predeclared")
        receipt=self.registry.propose("commit-"+slot,raw,role,self.ledger.head(),self.cards.proposal_evidence)
        save(self.run/"actors"/slot/"production-receipt.json",receipt)
        require(receipt["result"]["status"] in ("accepted","noop"),"paired_proposal_rejected")
        self.state["research"].append({"slot":slot,"role":role,"proposal":raw,"receipt":receipt});self.checkpoint()
        final_ref=self.source("proposal-"+slot,ns,canonical(raw).decode(),"agent_output")
        self.extract("extract-"+slot,ns,[ref,final_ref])
        return receipt["result"]["candidate"] or parent

    def execute(self):
        require(self.state["status"]=="prepared","paired_run_not_fresh_no_unknown_replay")
        self.state["status"]="running";self.checkpoint()
        memory=self.registry.register_root("memory","paired-initial-memory")
        scenario=self.registry.register_root("scenario","paired-initial-scenario")
        self.state["initial_memory"]=memory;self.state["initial_scenario"]=scenario;self.checkpoint()
        canary=self.ask("canary","Return exactly {\"ready\":true}. No tools.",{"purpose":"readiness"})
        require(canary=={"ready":True},"paired_canary_not_ready")
        refs=[self.source(note["id"],"task/shared-acquisition",note["text"]) for note in self.config["learning_notes"]]
        self.extract("acquisition-extract","task/shared-acquisition",refs)
        self.state["acquisition_corpus"]=self.cards.corpus("task/shared-acquisition",self.ledger.head());self.checkpoint()
        self.paired_epoch(0,memory,scenario)
        proposed=self.research("proposer-0","proposer",memory,memory)
        challenged=self.research("evaluator-0","evaluator",scenario,proposed)
        self.paired_epoch(1,proposed,challenged)
        untested=self.research("proposer-1","proposer",proposed,proposed)
        self.state.update(status="completed",tested_memory=proposed,tested_scenario=challenged,untested_final_proposal=untested)
        self.checkpoint()

    def execute_completion(self):
        require(self.state['status']=='prepared' and 'completion' in self.plan,'invalid_completion_state')
        self.state['status']='running';self.checkpoint();c=self.plan['completion'];memory=c['tested_memory']
        self.registry.resolve(memory)
        ready=self.ask('completion-canary','Return exactly {"ready":true}. No tools.',{'purpose':'readiness'})
        require(ready=={'ready':True},'paired_canary_not_ready')
        summary={'category':'synthetic_project_preferences','rows':c['public_rows'],'planned_solver_sessions':20,
                 'origin_plan_sha256':c['plan_sha256'],'prior_final_structure_rejection':c['prior_rejection']['result'],
                 'formal_admission':False,'all_task_results_are_original_not_new_solver_executions':True}
        candidate=self.research('completion-proposer','proposer',memory,memory,public_summary=summary)
        self.state.update(status='completed',tested_memory=memory,untested_final_proposal=candidate,
                          completion_origin=c['origin'],source_rows_sha256=c['public_rows_sha256'])
        self.checkpoint()


def summarize(run):
    run=Path(run);state=json.loads((run/"state.json").read_text());epochs=[]
    plan=json.loads((run/'plan.json').read_text())
    for e in range(0 if 'completion' in plan else 2):
        rows=[r for r in state["results"] if r["epoch"]==e];arms={}
        for arm in ("on","off"):
            rs=[r for r in rows if r["arm"]==arm]
            arms[arm]={"planned":5,"completed":sum(r["status"]=="completed" for r in rs),"terminated":sum(r["status"]=="terminated" for r in rs),
                       "missing":5-len(rs),"successes":sum(r["full_success"] for r in rs),"needs_context":sum(r["needs_context"] for r in rs),"wrong_assertions":sum(r["wrong_assertion"] for r in rs)}
        gains=sum(any(r["case"]==c and r["arm"]=="on" and r["full_success"] for r in rows) and
                  any(r["case"]==c and r["arm"]=="off" and not r["full_success"] and r["status"]=="completed" for r in rows) for c in ("repeat","transfer"))
        controls_ok=all(any(r["case"]==c and r["arm"]==a and r["full_success"] for r in rows) for c in ("stale","unrelated","counter") for a in ("on","off"))
        epochs.append({"epoch":e,"arms":arms,"retention_gains":gains,"all_protection_cases_pass":controls_ok,"rows":rows})
    accepted=bool(epochs) and state["status"]=="completed" and all(e["retention_gains"]==2 and e["all_protection_cases_pass"] for e in epochs)
    return {"category":"synthetic_project_preferences","status":state["status"],"planned_pairs":plan['planned_pairs'],"epochs":epochs,
            "descriptive_retention_acceptance":accepted,"known_client_tokens":state["known_tokens"],"sessions":state["sessions"],
            "research":state["research"],"formal_admission":False,"statistical_gain_claim":False,"real_project_gain_proved":False,
            "full_provider_context_verified":False,"provider_cost":None}


def main():
    p=argparse.ArgumentParser();p.add_argument("--run",required=True);p.add_argument("--config",default=str(ROOT/"configs/paired_retention_v0.json"))
    p.add_argument("operation",choices=("prepare","execute","report"));a=p.parse_args();run=Path(a.run).resolve()
    if a.operation=="prepare":print(json.dumps(prepare(run,a.config)));return
    if a.operation=="execute":
        with exclusive_run(run):
            s=Study(run)
            try:s.execute_completion() if 'completion' in s.plan else s.execute()
            except Exception as exc:s.state["status"]="halted";s.state["failure"]=str(exc);s.checkpoint()
            finally:s.ledger.close()
    r=summarize(run);save(run/"report.json",r)
    print(json.dumps({k:v for k,v in r.items() if k not in ("epochs","research")},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
