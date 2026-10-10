"""Predeclared exact paired decisions; uncertainty is an outcome, not a winner."""
import math

from .contracts import exact_object, identifier, require


def tail(k,n,p):
    return sum(math.comb(n,j)*p**j*(1-p)**(n-j) for j in range(k,n+1))


def cp_lower(k,n,alpha):
    if k==0:return 0.0
    lo,hi=0.0,1.0
    for _ in range(60):
        mid=(lo+hi)/2
        if tail(k,n,mid)<alpha:lo=mid
        else:hi=mid
    return (lo+hi)/2


def cp_upper(k,n,alpha):
    return 1.0 if k==n else 1-cp_lower(n-k,n,alpha)


def paired_decision(rows,config,stage="confirmation"):
    require(stage in ("screening","confirmation"),"invalid_comparison_stage")
    require(type(rows) is list and bool(rows),"invalid_paired_data")
    ids=set();wins=losses=severe=0;costs_known=True
    for row in rows:
        exact_object(row,{"unit","baseline_success","candidate_success","baseline_cost","candidate_cost","severe_violation"},"invalid_paired_row")
        identifier(row["unit"])
        require(row["unit"] not in ids,"duplicate_independent_unit");ids.add(row["unit"])
        for key in ("baseline_success","candidate_success","severe_violation"):
            require(type(row[key]) is bool,"invalid_paired_boolean")
        wins+=int(row["candidate_success"] and not row["baseline_success"])
        losses+=int(row["baseline_success"] and not row["candidate_success"])
        severe+=int(row["severe_violation"])
        for key in ("baseline_cost","candidate_cost"):
            value=row[key]
            if value is None:costs_known=False
            else:require(type(value) in (int,float) and math.isfinite(value) and value>=0,"invalid_paired_cost")
    n=len(rows)
    require(config["decision_claims"]==["better","worse","noninferior"] and
        config["multiple_comparison"]=="bonferroni_predeclared_comparisons_and_claims" and
        config["confirmation_comparisons_max"]==config["proposals_max"]+2*config["shadow_comparisons_max"],"unsupported_decision_rule")
    alpha=config["alpha"]/(config["confirmation_comparisons_max"]*len(config["decision_claims"]))
    difference=(wins-losses)/n
    discordant=wins+losses
    p_better=tail(wins,discordant,.5) if discordant else 1.0
    p_worse=tail(losses,discordant,.5) if discordant else 1.0
    lower=cp_lower(wins,n,alpha/2)-cp_upper(losses,n,alpha/2)
    base={"units":n,"wins":wins,"losses":losses,"effect":difference,"p_better":p_better,"p_worse":p_worse,
          "alpha_adjusted":alpha,"alpha_family":config["alpha"],"error_scope":config["error_scope"],
          "comparisons_max":config["confirmation_comparisons_max"],"decision_claims":list(config["decision_claims"]),
          "cost_claim_scope":config["cost_claim_scope"],"difference_lower_bound":lower,"severe_violations":severe,"cost_known":costs_known}
    if stage=="confirmation":require(n<=config["confirmation_max"],"undeclared_extra_confirmation")
    if severe>config["severe_regression_tolerance"]:return {**base,"verdict":"unusable","code":"severe_violation"}
    if stage=="screening":return {**base,"verdict":"confirmation_required","code":"screening_not_promotion"}
    if n<config["confirmation_min"]:return {**base,"verdict":"insufficient","code":"too_few_independent_units"}
    if not costs_known:return {**base,"verdict":"insufficient","code":"unknown_cost"}
    if difference<=-config["success_gain_min"] and p_worse<=alpha:return {**base,"verdict":"worse","code":"confirmed_behavior_harm"}
    if difference>=config["success_gain_min"] and p_better<=alpha:return {**base,"verdict":"better","code":"confirmed_behavior_gain"}
    baseline=sum(r["baseline_cost"] for r in rows)
    saving=(baseline-sum(r["candidate_cost"] for r in rows))/baseline if baseline>0 else None
    if lower>-config["noninferiority_margin"] and saving is not None and saving>=config["cost_reduction_min"]:
        return {**base,"cost_saving_fraction":saving,"verdict":"cheaper_noninferior","code":"confirmed_cost_and_quality"}
    return {**base,"cost_saving_fraction":saving,"verdict":"indistinguishable","code":"predeclared_threshold_not_met"}
