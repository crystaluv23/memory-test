"""Independent JSON grader. Cannot execute subject code or read other arms."""
import json
import sys
from pathlib import Path


def grade(task, final, disclosed):
    valid = type(final) is dict and set(final) == {'status', 'result', 'memory_uses', 'ratings'}
    if valid:
        uses = final['memory_uses']; ratings = final['ratings']
        valid = (final['status'] in ('complete', 'needs_context') and type(uses) is list and
                 all(type(x) is str and x in disclosed for x in uses) and len(set(uses)) == len(uses) and
                 type(ratings) is list and all(type(r) is dict and set(r) == {'id', 'value'} and
                 type(r['id']) is str and r['id'] in uses and type(r['value']) is int and r['value'] in (-1, 0, 1) for r in ratings))
        valid = valid and len({r['id'] for r in ratings}) == len(ratings) and {r['id'] for r in ratings} == set(uses)
        valid = valid and (final['status'] != 'needs_context' or final['result'] is None)
    complete = valid and final['status'] == 'complete'
    matched = complete and json.dumps(final['result'], sort_keys=True) == json.dumps(task['gold'], sort_keys=True)
    return {'full_success': bool(matched), 'valid_final': bool(valid),
            'needs_context': bool(valid and final['status'] == 'needs_context'),
            'wrong_assertion': bool(complete and not matched)}


if __name__ == '__main__':
    print(json.dumps(grade(*[json.loads(Path(p).read_text()) for p in sys.argv[1:]])))
