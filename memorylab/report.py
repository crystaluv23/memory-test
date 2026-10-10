import csv
import json
from collections import defaultdict
from pathlib import Path

from .common import encode, load, save
from .memory import ReferenceMemory


def summarize(run_path):
    run = Path(run_path).resolve()
    manifest, state = load(run / "manifest.json"), load(run / "state.json")
    arms, rows, reviews, mapping = {}, [], [], []
    recurrence = defaultdict(int)
    for index, trial in enumerate(manifest["trials"]):
        arm = arms.setdefault(trial["arm"], {"planned": 0, "finished": 0, "missing": 0,
            "full_successes": 0, "false_completions": 0, "transfer_planned": 0, "transfer_successes": 0,
            "solve_seconds": 0, "reflection_seconds": 0, "known_input_tokens": 0, "known_cached_tokens": 0,
            "known_output_tokens": 0, "unknown_usage_calls": 0, "commands": 0, "repeated_commands": 0,
            "automatic_brief_bytes": 0, "disclosure_bytes": 0, "disclosure_calls": 0,
            "disclosure_seconds": 0, "accepted_notes": 0, "rejected_batches": 0,
            "overflow_briefs": 0, "active_notes": 0, "archived_versions": 0,
            "costs_by_phase": {p: {"known_input_tokens": 0, "known_cached_tokens": 0,
                 "known_output_tokens": 0, "unknown_usage_calls": 0, "wall_seconds": 0,
                 "commands": 0} for p in ("solve", "reflection")},
            "unseen_memory_claims": 0, "storage_bytes": 0, "recurring_failed_groups": 0})
        arm["planned"] += 1
        arm["transfer_planned"] += int(trial["episode"] >= 2)
        directory = run / "trials" / trial["id"]
        status = state["trials"][trial["id"]]
        finished = status in ("completed", "failed", "interrupted")
        arm["finished" if finished else "missing"] += 1
        result = load(directory / "grade.json") if (directory / "grade.json").exists() and finished else {}
        success = bool(result.get("full_success")) and status == "completed"
        arm["full_successes"] += int(success)
        arm["transfer_successes"] += int(success and trial["episode"] >= 2)
        arm["false_completions"] += int(result.get("false_completion", False))
        row = dict(trial, status=status, full_success=success if finished else None)
        row["failed_groups"] = [name for name, score in result.get("groups", {}).items() if score["passed"] != score["total"]]
        for phase in ("solve", "reflection"):
            file = directory / phase / "metrics.json"
            if not file.exists():
                if finished and phase == "solve":
                    arm["unknown_usage_calls"] += 1
                continue
            metrics = load(file)
            phase_cost = arm["costs_by_phase"][phase]
            row[phase+"_seconds"] = metrics["wall_seconds"]
            arm[phase+"_seconds"] += metrics["wall_seconds"]
            phase_cost["wall_seconds"] += metrics["wall_seconds"]
            phase_cost["commands"] += metrics["command_count"]
            usage = metrics.get("usage")
            if usage is None:
                arm["unknown_usage_calls"] += 1
                phase_cost["unknown_usage_calls"] += 1
            else:
                arm["known_input_tokens"] += usage["input_tokens"]
                arm["known_cached_tokens"] += usage["cached_input_tokens"]
                arm["known_output_tokens"] += usage["output_tokens"]
                phase_cost["known_input_tokens"] += usage["input_tokens"]
                phase_cost["known_cached_tokens"] += usage["cached_input_tokens"]
                phase_cost["known_output_tokens"] += usage["output_tokens"]
            arm["commands"] += metrics["command_count"]
            arm["repeated_commands"] += metrics["repeated_commands"]
        automatic_file = directory / "automatic_briefs.json"
        if automatic_file.exists():
            arm["automatic_brief_bytes"] += load(automatic_file)["bytes"]
            arm["overflow_briefs"] += len(load(automatic_file)["overflow"])
        for log in ("memory_requests.json", "reflection_requests.json"):
            if (directory / log).exists():
                requests = load(directory / log)
                arm["disclosure_bytes"] += sum(x["bytes"] for x in requests)
                arm["disclosure_calls"] += len(requests)
                arm["disclosure_seconds"] += sum(x["elapsed_ms"] for x in requests)/1000
        if (directory / "memory_commit.json").exists():
            commit = load(directory / "memory_commit.json")
            arm["accepted_notes"] += commit["accepted"]
            arm["rejected_batches"] += int(bool(commit["errors"]))
        if (directory / "memory_use_audit.json").exists():
            arm["unseen_memory_claims"] += len(load(directory / "memory_use_audit.json")["unseen_declared"])
        for group, score in result.get("groups", {}).items():
            if score["passed"] != score["total"]:
                key = (trial["arm"], trial["repeat"], trial["family"], group)
                arm["recurring_failed_groups"] += int(recurrence[key] > 0)
                recurrence[key] += 1
        alias = f"review-{index+1:04}"
        reviews.append({"review_id": alias, "full_success": row["full_success"],
                        "source_support": None, "generalization": None, "staleness": None,
                        "action_changed": None, "maintenance_quality": None, "reviewer": None, "notes": ""})
        mapping.append({"review_id": alias, "trial_id": trial["id"]})
        rows.append(row)
    for name, arm in arms.items():
        arm["planned_success_fraction"] = arm["full_successes"]/arm["planned"]
        arm["finished_success_fraction"] = arm["full_successes"]/arm["finished"] if arm["finished"] else None
        arm["coverage"] = arm["finished"]/arm["planned"]
        arm["total_tokens"] = None if arm["unknown_usage_calls"] else arm["known_input_tokens"]+arm["known_output_tokens"]
        arm["cost_usd"] = None
        arm["storage_bytes"] = sum(p.stat().st_size for p in (run / "memory").glob("*-"+name+".json"))
        for file in (run / "memory").glob("*-"+name+".json"):
            memory = ReferenceMemory(load(file))
            arm["active_notes"] += len(memory.active())
            arm["archived_versions"] += len(memory.records)
    report = {"status": state["status"], "kind": manifest["kind"], "arms": arms,
              "trials": rows, "comparison_conclusion": "not_evaluated",
              "limitations": ["Historical failure-derived synthetic tasks; no real project replay yet.",
                "No mechanism variants selected in environment phase.",
                "Memory use is self-reported plus observed disclosure, not causal benefit.",
                "Missing/partial traces imply unknown cost; no price or token estimates invented."]}
    save(run / "report.json", report)
    save(run / "review_mapping.private.json", mapping)
    # Anonymous review packets omit arm/model metadata and private grading expectations.
    aliases = {m["trial_id"]: m["review_id"] for m in mapping}
    def anonymize(text):
        for original, anonymous in sorted(aliases.items(), key=lambda pair: -len(pair[0])):
            text = text.replace(original, anonymous)
        return text
    for item in mapping:
        directory = run / "trials" / item["trial_id"]
        if not directory.exists():
            continue
        packet = run / "reviews" / item["review_id"]
        packet.mkdir(parents=True, exist_ok=True)
        for name in ("feedback.json", "memory_commit.json", "memory_use_audit.json", "automatic_briefs.json",
                     "memory_before.json", "memory_after.json", "solve/final.json", "reflection/final.json",
                     "workspace_initial/SPEC.md", "workspace_initial/engine.py", "workspace_solved/engine.py"):
            file = directory / name
            if file.exists():
                target = packet / name.replace("/", "__")
                target.write_text(anonymize(file.read_text()))
        save(packet / "PACKET.json", {"review_id": item["review_id"], "anonymized": True,
             "note": "Source ids anonymized for review only; exact hash verification uses original control artifacts. Memory layout may still suggest adapter type; blinding is partial."})
    review_path = run / "blind_review.json"
    # Refresh generated outcome fields while preserving human review work.
    if review_path.exists():
        earlier = {r["review_id"]: r for r in load(review_path)}
        reviews = [dict(row, **{key: value for key, value in earlier.get(row["review_id"], {}).items()
                     if key not in ("review_id", "full_success")}) for row in reviews]
    save(review_path, reviews)
    lines = ["# 执行流程报告", "", "状态："+state["status"], "",
             "本报告不作机制优劣结论。未执行任务为 missing；失败和中断留在分母。", "",
             "| 运行标签 | 已结束 / 计划 | 完整成功 | 解题秒 | 反思秒 | 读取字节 | token（未知为 null） |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for name, arm in arms.items():
        lines.append(f"| {name} | {arm['finished']} / {arm['planned']} | {arm['full_successes']} | {arm['solve_seconds']:.1f} | {arm['reflection_seconds']:.1f} | {arm['disclosure_bytes']} | {arm['total_tokens']} |")
    lines += ["", "独立语义审查请填写 blind_review.json，映射单独存储，不能以受测 agent 自评替代。",
              "", "完整指标和逐任务结果见 report.json；费用未获得，不估算。"]
    (run / "report.md").write_text("\n".join(lines)+"\n")
    with (run / "trials.csv").open("w", newline="") as stream:
        fields = list(dict.fromkeys(key for row in rows for key in row))
        writer = csv.DictWriter(stream, fields)
        writer.writeheader(); writer.writerows({key: encode(value) if isinstance(value, (list, dict)) else value for key, value in row.items()} for row in rows)
    return report
