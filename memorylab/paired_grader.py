"""Standalone deterministic grader: no model, corpus, or executable subject code."""
import argparse
import json
from pathlib import Path


def grade(task, final, disclosed):
    valid = (type(final) is dict and set(final) == {"status", "result", "memory_uses"}
             and final["status"] in ("complete", "needs_context")
             and type(final["memory_uses"]) is list
             and all(type(i) is str and i in disclosed for i in final["memory_uses"])
             and (final["status"] != "needs_context" or final["result"] is None))
    complete = valid and final["status"] == "complete"
    # Serialized JSON distinguishes bool from int and retains array ordering.
    matched = complete and json.dumps(final["result"], sort_keys=True) == json.dumps(task["gold"], sort_keys=True)
    return {"full_success": bool(matched), "valid_final": bool(valid),
            "needs_context": bool(valid and final["status"] == "needs_context" and final["result"] is None),
            "wrong_assertion": bool(complete and not matched), "category": "synthetic_project_preferences"}


def main():
    p = argparse.ArgumentParser(); p.add_argument("task"); p.add_argument("final"); p.add_argument("disclosed")
    a = p.parse_args()
    print(json.dumps(grade(json.loads(Path(a.task).read_text()), json.loads(Path(a.final).read_text()),
                           json.loads(Path(a.disclosed).read_text()))))


if __name__ == "__main__": main()
