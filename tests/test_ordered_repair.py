"""Independent positive/negative controls for ordered repair transactions."""
import copy
import json
import tempfile
from pathlib import Path
from unittest import TestCase

from memorylab.common import digest
from memorylab.scenario import materialize, probes, production, verify
from memorylab.tasks import oracle
from test_scenario import GOOD_REPAIR
from test_environment import FINAL

# Exact real submission preserved by the diagnostic, not a repaired subject.
PREVALIDATES_ORIGINAL = "import copy\nimport hashlib\nimport json\nimport re\n\n\ndef _pointer(path):\n    if not isinstance(path, str) or not path.startswith('/'):\n        raise ValueError('Non-root JSON Pointer required')\n    tokens = []\n    for raw in path[1:].split('/'):\n        decoded = []\n        i = 0\n        while i < len(raw):\n            if raw[i] != '~':\n                decoded.append(raw[i])\n                i += 1\n            else:\n                if i + 1 >= len(raw) or raw[i + 1] not in '01':\n                    raise ValueError('Malformed pointer escape')\n                decoded.append('~' if raw[i + 1] == '0' else '/')\n                i += 2\n        tokens.append(''.join(decoded))\n    return tokens\n\n\ndef _index(token, length):\n    if re.fullmatch(r'0|[1-9][0-9]*', token) is None:\n        raise ValueError('Invalid array index')\n    index = int(token)\n    if index >= length:\n        raise ValueError('Array index does not exist')\n    return index\n\n\ndef _parent(state, tokens):\n    node = state\n    for token in tokens[:-1]:\n        if isinstance(node, dict):\n            if token not in node:\n                raise ValueError('Missing parent')\n            node = node[token]\n        elif isinstance(node, list):\n            node = node[_index(token, len(node))]\n        else:\n            raise ValueError('Parent is not a container')\n    return node\n\n\ndef _coordinate(state, tokens, op):\n    parent = _parent(state, tokens)\n    token = tokens[-1]\n    if isinstance(parent, dict):\n        if op == 'add' and token in parent:\n            raise ValueError('Add requires an absent object key')\n        if op == 'replace' and token not in parent:\n            raise ValueError('Replace requires an existing object key')\n        return parent, token\n    if isinstance(parent, list) and op == 'replace':\n        return parent, _index(token, len(parent))\n    raise ValueError('Unsupported target')\n\n\ndef process(case):\n    original = copy.deepcopy(case['state'])\n    rejected = {'accepted': False, 'state': original}\n    try:\n        if not isinstance(original, dict):\n            return rejected\n        canonical = json.dumps(original, sort_keys=True, ensure_ascii=False, separators=(',', ':'))\n        digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()\n        if case['snapshot'] != digest:\n            return rejected\n        edits = case['edits']\n        grants = case['grants']\n        if not isinstance(edits, list) or not isinstance(grants, list):\n            return rejected\n        mode = case.get('capability_mode', 'both')\n        if mode not in ('add_only', 'both'):\n            return rejected\n        allowed = set()\n        for grant in grants:\n            if isinstance(grant, dict):\n                op, path = grant.get('op'), grant.get('path')\n                if isinstance(op, str) and isinstance(path, str):\n                    allowed.add((op, path))\n        seen = set()\n        plan = []\n        for edit in edits:\n            if not isinstance(edit, dict):\n                return rejected\n            op, path = edit['op'], edit['path']\n            if op not in ('add', 'replace') or (mode == 'add_only' and op != 'add'):\n                return rejected\n            tokens = _pointer(path)\n            identity = tuple(tokens)\n            if identity in seen or (op, path) not in allowed:\n                return rejected\n            seen.add(identity)\n            _coordinate(original, tokens, op)\n            plan.append((op, tokens, copy.deepcopy(edit['value'])))\n        repaired = copy.deepcopy(original)\n        for op, tokens, value in plan:\n            parent, key = _coordinate(repaired, tokens, op)\n            parent[key] = value\n        return {'accepted': True, 'state': repaired}\n    except (KeyError, TypeError, ValueError, IndexError, OverflowError, UnicodeError):\n        return rejected\n"


def episode(seed=801, transfer="near", mode="both"):
    return dict(id="semantic-audit", role="target", backend="repair", stage=2,
                seed=seed, rule_mode=mode, context_profile="clean",
                semantic_interference="none", transfer_distance=transfer)


class OrderedRepairTests(TestCase):
    def test_working_copy_order_and_atomicity_have_independent_expected_states(self):
        def edit(op, path, value):return dict(op=op, path=path, value=value)
        rows = [
            ({"a":{"b":1}}, [edit("replace","/a",{}),edit("add","/a/b",3)], True, {"a":{"b":3}}),
            ({"a":{}}, [edit("replace","/a",{"b":2}),edit("replace","/a/b",3)], True, {"a":{"b":3}}),
            ({"a":[1]}, [edit("replace","/a",[2,3]),edit("replace","/a/1",4)], True, {"a":[2,4]}),
            ({"a":[1,2]}, [edit("replace","/a",[2]),edit("replace","/a/1",4)], False, {"a":[1,2]}),
            ({"a":{"b":1}}, [edit("replace","/a",7),edit("replace","/a/b",3)], False, {"a":{"b":1}}),
            ({"a":{"b":1}}, [edit("replace","/a/b",3),edit("replace","/a",{"b":2})], True, {"a":{"b":2}}),
            ({"a/b":{"~key":1}}, [edit("replace","/a~1b",{}),edit("add","/a~1b/~0key",3)], True, {"a/b":{"~key":3}}),
            ({"a":{"b":1},"c":2}, [edit("replace","/c",4),edit("replace","/a",7),edit("replace","/a/b",3)], False, {"a":{"b":1},"c":2}),
            ({"keep":1}, [edit("add","/new",{}),edit("add","/new/b",3)], True, {"keep":1,"new":{"b":3}}),
        ]
        inputs=[];expected=[]
        for state,edits,accepted,after in rows:
            case=dict(state=state,snapshot=digest(state),edits=edits,
                      grants=[dict(op=e["op"],path=e["path"]) for e in edits],capability_mode="both")
            answer=dict(accepted=accepted,state=after)
            before=copy.deepcopy(case)
            self.assertEqual(oracle("repair",case),answer)
            self.assertEqual(case,before)
            inputs.append(case);expected.append(answer)
        with tempfile.TemporaryDirectory(prefix="memorylab-semantic-test-") as tmp:
            root=Path(tmp);worker=root/"worker";control=root/"control";control.mkdir()
            materialize(worker,episode());(worker/"engine.py").write_text(GOOD_REPAIR)
            result=production(worker,control,inputs)
            self.assertEqual(result["exit_code"],0,result)
            self.assertEqual(result["output"],expected)

    def test_real_original_state_prevalidation_false_positive_is_now_rejected(self):
        required={"ancestor_removes_then_child_add","ancestor_creates_then_child_replace",
                  "ancestor_array_grows_then_child_replace","ancestor_array_shrinks_then_child_reject",
                  "ancestor_scalar_then_child_reject","child_then_ancestor_order",
                  "escaped_ancestor_then_child_add","disjoint_sibling_transaction",
                  "late_failure_rolls_back_all_siblings","added_parent_then_child_add",
                  "ancestor_grant_not_child_authority"}
        for seed,transfer,mode in [(3,"near","both"),(71,"far","both"),(801,"far","add_only")]:
            with self.subTest(seed=seed,transfer=transfer,mode=mode), tempfile.TemporaryDirectory(prefix="memorylab-semantic-test-") as tmp:
                root=Path(tmp);control=root/"control";control.mkdir();ep=episode(seed,transfer,mode)
                rows=probes(ep,seed+17)
                self.assertTrue(required <= {r["group"] for r in rows})
                good=root/"good";protected=materialize(good,ep);(good/"engine.py").write_text(GOOD_REPAIR)
                grade=verify(good,control,ep,protected,FINAL)
                self.assertTrue(grade["full_success"],grade)
                bad=root/"bad";protected=materialize(bad,ep);(bad/"engine.py").write_text(PREVALIDATES_ORIGINAL)
                grade=verify(bad,control,ep,protected,FINAL)
                self.assertFalse(grade["full_success"],grade)
                if mode=="both":
                    for group in ["ancestor_removes_then_child_add","ancestor_creates_then_child_replace",
                                  "ancestor_array_grows_then_child_replace","escaped_ancestor_then_child_add"]:
                        self.assertLess(grade["groups"][group]["passed"],grade["groups"][group]["total"])
                else:
                    self.assertLess(grade["groups"]["added_parent_then_child_add"]["passed"],grade["groups"]["added_parent_then_child_add"]["total"])
