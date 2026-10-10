"""Persistent request-side reservations. Current counter/provider are fixtures only."""
import copy
import hashlib
from pathlib import Path

from .contracts import canonical,exact_object,identifier,require,sha256
from .kernel_cli import read_json
from .ledger import Ledger
from .scheduling import ROLES


def budget_config():
    c=read_json(Path(__file__).resolve().parent.parent/"configs/provider_budget_v0.json")
    exact_object(c,{"schema_version","hypothesis","category","model","counter","counter_units","fixed_input_count",
        "minimum_output_tokens","maximum_request_bytes","unknown_usage","automatic_retry","formal_admission",
        "provider_budget_qualified","tokenizer_qualified"},"invalid_provider_budget_config")
    expected={"schema_version":"provider-budget-v0","category":"infrastructure_acceptance","model":"memorylab-fixture",
        "counter":"fixed_fixture_100","counter_units":"simulated_never_model_tokens","fixed_input_count":100,
        "minimum_output_tokens":16,"unknown_usage":"retain_full_reservation","automatic_retry":False,
        "formal_admission":False,"provider_budget_qualified":False,"tokenizer_qualified":False}
    require(all(type(c[k]) is type(v) and c[k]==v for k,v in expected.items()),"unsupported_provider_budget_policy")
    require(type(c["maximum_request_bytes"]) is int and c["maximum_request_bytes"]>0,"invalid_provider_budget_limit")
    return c


def limits(value):
    exact_object(value,{"requests","input_tokens","output_tokens"},"invalid_provider_budget_limits")
    require(all(type(v) is int and v>0 for v in value.values()),"invalid_provider_budget_limits")


def call_id(value):
    identifier(value);require(len(value)<=65,"provider_budget_call_id")


def implementation():
    root=Path(__file__).resolve().parent
    return sha256({n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in
        ("provider_budget.py","ledger.py","contracts.py","kernel_cli.py","scheduling.py","freeze.py")})


class ProviderBudget:
    def __init__(self,path,round_limits,slots,create=False,readonly=False):
        require(type(readonly) is bool and not (readonly and create),"provider_budget_read_only")
        self.readonly=readonly
        self.config=budget_config();limits(round_limits)
        require(type(slots) is dict and bool(slots),"provider_budget_slots")
        for key,slot in slots.items():
            identifier(key);exact_object(slot,{"role","limits","per_request_output_tokens"},"provider_budget_slot")
            require(type(slot["role"]) is str and slot["role"] in ROLES,"provider_budget_role");limits(slot["limits"])
            require(type(slot["per_request_output_tokens"]) is int and slot["per_request_output_tokens"]>=16 and
                slot["per_request_output_tokens"]<=slot["limits"]["output_tokens"],"provider_budget_output_limit")
        require(all(sum(s["limits"][k] for s in slots.values())<=round_limits[k] for k in round_limits),"provider_budget_plan_over_guard")
        self.contract={"kind":"provider_budget_contract","id":"provider-budget-v0","version":1,"config":self.config,
            "implementation_sha256":implementation(),"round_limits":copy.deepcopy(round_limits),"slots":copy.deepcopy(slots)}
        self.path=Path(path).absolute();require(not self.path.is_symlink(),"provider_budget_path")
        if create:
            require(not self.path.exists(),"provider_budget_exists");self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.path.open('xb'):pass
            with Ledger(self.path) as ledger,ledger.transaction():
                self.ref=ledger.put(self.contract,"control")
                ledger.commit_receipt("provider-budget-init",self.contract,{"budget_action":"init","contract":self.ref},"control")
        else:
            require(self.path.is_file(),"provider_budget_database_missing")
            with self._open() as ledger:
                receipt=ledger.receipt("provider-budget-init",self.contract);require(receipt is not None,"provider_budget_not_initialized")
                self.ref=receipt["result"]["contract"];require(ledger.get(self.ref)==self.contract,"provider_budget_contract_drift")

    def _check(self,ledger):
        require(implementation()==self.contract["implementation_sha256"],"provider_budget_implementation_drift")
        require(ledger.get(self.ref)==self.contract,"provider_budget_contract_drift");ledger.audit()
        init=ledger.receipt("provider-budget-init",self.contract)
        require(init is not None and init["result"]["contract"]==self.ref,"provider_budget_not_initialized")

    def _open(self):
        require(self.path.is_file() and not self.path.is_symlink(),"provider_budget_database_missing")
        return Ledger(self.path,readonly=self.readonly)

    def _state(self,ledger):
        self._check(ledger);rows=[]
        # Read authoritative receipts first, so deleting a reservation object
        # cannot silently remove its denominator or return its budget.
        for event in ledger.events("control"):
            receipt=event["data"].get("receipt",{});result=receipt.get("result",{})
            if result.get("budget_action") in ("init","prepare","dispatch","finish"):
                require(ledger.receipt(event["request_id"])==receipt,"provider_budget_receipt_missing")
            if result.get("budget_action")!="prepare":continue
            ref=result["reservation"];r=ledger.get(ref)
            require(r["contract"]==self.ref and r["slot_id"] in self.contract["slots"],"provider_budget_reservation_binding")
            require(receipt["request_id"]=="budget-prepare-"+r["id"],"provider_budget_reservation_binding")
            sent=ledger.receipt("budget-sent-"+r["id"]);finish=ledger.receipt("budget-finish-"+r["id"])
            if sent:require(sent["result"]["reservation"]==ref,"provider_budget_dispatch_binding")
            if finish:require(sent is not None and finish["result"]["reservation"]==ref,"provider_budget_finish_binding")
            terminal=finish["result"] if finish else None
            valid=terminal is not None and terminal["status"]=="settled"
            known=terminal.get("usage") if terminal and type(terminal.get("usage")) is dict else {}
            charged=lambda k: max(r[k],known[k]) if type(known.get(k)) is int and known[k]>=0 else r[k]
            rows.append({"reservation":ref,"id":r["id"],"slot_id":r["slot_id"],"dispatched":sent is not None,
                "status":terminal["status"] if terminal else "unknown" if sent else "reserved",
                "cost_unknown":terminal["usage_known"] is False if terminal else True,
                "input_tokens":terminal["usage"]["input_tokens"] if valid else charged("input_tokens"),
                "output_tokens":terminal["usage"]["output_tokens"] if valid else charged("output_tokens"),
                "usage":terminal.get("usage") if terminal else None})
        require({sha256(r["reservation"]) for r in rows}=={sha256(ref) for ref,_ in ledger.objects("provider_budget_reservation","control")},"provider_budget_reservation_missing")
        return rows

    def prepare(self,id_,slot_id,request):
        require(not self.readonly,"provider_budget_read_only")
        call_id(id_);require(slot_id in self.contract["slots"],"provider_budget_unregistered_slot")
        require(type(request) is dict and request.get("model")==self.config["model"],"provider_budget_model")
        require(len(canonical(request))<=self.config["maximum_request_bytes"],"provider_budget_request_size")
        require(not any(k in request for k in ("conversation","previous_response_id")) and request.get("background",False) is False and
            request.get("store",False) is False and request.get("truncation","disabled")=="disabled","provider_budget_hidden_context")
        original={"contract":self.ref,"slot_id":slot_id,"original_request":copy.deepcopy(request)}
        with self._open() as ledger,ledger.transaction():
            rows=self._state(ledger);cached=ledger.receipt("budget-prepare-"+id_,original)
            require(not any(r["status"]=="breach" for r in rows),"provider_budget_breached")
            if cached:return cached["result"]["reservation"]
            slot=self.contract["slots"][slot_id];own=[r for r in rows if r["slot_id"]==slot_id]
            for selected,bound in ((rows,self.contract["round_limits"]),(own,slot["limits"])):
                require(len(selected)<bound["requests"],"provider_budget_request_guard")
                require(sum(r["input_tokens"] for r in selected)+self.config["fixed_input_count"]<=bound["input_tokens"],"provider_budget_input_guard")
            remaining=min(self.contract["round_limits"]["output_tokens"]-sum(r["output_tokens"] for r in rows),
                slot["limits"]["output_tokens"]-sum(r["output_tokens"] for r in own),slot["per_request_output_tokens"])
            if "max_output_tokens" in request:
                require(type(request["max_output_tokens"]) is int and request["max_output_tokens"]>=16,"provider_budget_output_limit")
                remaining=min(remaining,request["max_output_tokens"])
            require(remaining>=self.config["minimum_output_tokens"],"provider_budget_output_guard")
            forwarded={**copy.deepcopy(request),"max_output_tokens":remaining,"truncation":"disabled"}
            require(len(canonical(forwarded))<=self.config["maximum_request_bytes"],"provider_budget_request_size")
            # This is intentionally the fixed fixture counter; no real tokenizer
            # or remote token-count endpoint is inferred from its integer.
            counter={"kind":self.config["counter"],"units":self.config["counter_units"],"request_sha256":sha256(forwarded),
                "input_tokens":self.config["fixed_input_count"],"qualified":False}
            ref=ledger.put({"kind":"provider_budget_reservation","id":id_,"version":1,"contract":self.ref,"slot_id":slot_id,
                "original_sha256":sha256(request),"forwarded":forwarded,"counter":counter,
                "input_tokens":counter["input_tokens"],"output_tokens":remaining},"control")
            ledger.commit_receipt("budget-prepare-"+id_,original,{"budget_action":"prepare","reservation":ref},"control")
            return ref

    def dispatch(self,ref):
        require(not self.readonly,"provider_budget_read_only")
        with self._open() as ledger,ledger.transaction():
            rows=self._state(ledger);r=ledger.get(ref)
            require(any(row["reservation"]==ref for row in rows),"provider_budget_unknown_reservation")
            require(not any(row["status"]=="breach" for row in rows),"provider_budget_breached")
            require(ledger.receipt("budget-sent-"+r["id"]) is None,"provider_budget_already_dispatched")
            ledger.commit_receipt("budget-sent-"+r["id"],{"reservation":ref,"forwarded_sha256":sha256(r["forwarded"])},
                {"budget_action":"dispatch","reservation":ref},"control")
            return r["forwarded"]

    def finish(self,ref,response):
        require(not self.readonly,"provider_budget_read_only")
        # Errors are decisions, not a rollback that hides the incurred request.
        request={"reservation":ref,"response":copy.deepcopy(response)};canonical(request)
        with self._open() as ledger,ledger.transaction():
            rows=self._state(ledger);require(any(row["reservation"]==ref for row in rows),"provider_budget_unknown_reservation");r=ledger.get(ref)
            require(ledger.receipt("budget-sent-"+r["id"]) is not None,"provider_budget_not_dispatched")
            cached=ledger.receipt("budget-finish-"+r["id"],request)
            if cached:
                require(cached["result"]["status"]=="settled","provider_budget_usage_breach")
                return cached["result"]
            usage=response.get("usage") if type(response) is dict else None
            good=type(response) is dict and response.get("object")=="response" and type(response.get("id")) is str and bool(response["id"]) and response.get("model")==self.config["model"] and response.get("status") in ("completed","incomplete")
            good=good and type(usage) is dict and all(type(usage.get(k)) is int and usage[k]>=0 for k in ("input_tokens","output_tokens","total_tokens"))
            known=good and usage["total_tokens"]==usage["input_tokens"]+usage["output_tokens"]
            if good:good=usage["input_tokens"]==r["input_tokens"] and usage["output_tokens"]<=r["output_tokens"] and usage["total_tokens"]==usage["input_tokens"]+usage["output_tokens"]
            if good:
                for key,detail,bound in (("input_tokens_details","cached_tokens",usage["input_tokens"]),("output_tokens_details","reasoning_tokens",usage["output_tokens"])):
                    data=usage.get(key,{})
                    good=good and type(data) is dict and type(data.get(detail,0)) is int and 0<=data.get(detail,0)<=bound
            prior=[ledger.receipt("budget-finish-"+row["id"]) for row in self._state(ledger) if row["id"]!=r["id"]]
            unique=type(response) is dict and not any(p and p["result"].get("response_id")==response.get("id") for p in prior)
            good=good and unique;known=known and unique
            result={"budget_action":"finish","reservation":ref,"status":"settled" if good else "breach",
                "response_id":response.get("id") if type(response) is dict else None,"response_sha256":sha256(response),"usage":usage,"usage_known":known}
            ledger.commit_receipt("budget-finish-"+r["id"],request,result,"control")
        require(good,"provider_budget_usage_breach")
        return result

    def report(self):
        from contextlib import nullcontext
        with self._open() as ledger,(nullcontext() if self.readonly else ledger.transaction()):
            rows=self._state(ledger)
            return {"category":"infrastructure_acceptance","counter_units":self.config["counter_units"],"contract":self.ref,
                "requests":len(rows),"dispatched":sum(r["dispatched"] for r in rows),"input_tokens":sum(r["input_tokens"] for r in rows),
                "output_tokens":sum(r["output_tokens"] for r in rows),"unknown_requests":sum(r["cost_unknown"] for r in rows),
                "breaches":sum(r["status"]=="breach" for r in rows),"rows":rows,"formal_admission":False,
                "provider_budget_qualified":False,"tokenizer_qualified":False}
