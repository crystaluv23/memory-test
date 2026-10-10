import copy
from pathlib import Path
from unittest.mock import patch

from memorylab.control_stage import ControlStages
from memorylab.contracts import canonical,sha256
from memorylab.ledger import Ledger
from memorylab.role_stage import RoleStages
from memorylab.task_stage import TaskStages
from test_kernel import AT,Fixture
import test_role_stage
import test_task_stage


class ControlStageTests(Fixture):
    setup_graph=test_role_stage.RoleStageTests.setup_graph
    finish=test_role_stage.RoleStageTests.finish
    outputs=test_role_stage.RoleStageTests.outputs
    derive=test_role_stage.RoleStageTests.derive
    run_until=test_role_stage.RoleStageTests.run_until
    role_code=test_role_stage.RoleStageTests.code
    task_code=test_task_stage.TaskStageTests.code
    crash=staticmethod(test_role_stage.RoleStageTests.crash)

    def setup_controls(self,research=False,shadow=False,derived=False,seeded=False):
        self.setup_graph(research=research,shadow=shadow,derived=derived,seeded=seeded)
        self.tasks=TaskStages(self.inputs,Path(self.tmp.name)/"task-output")
        self.controls=ControlStages(self.inputs,self.tasks,self.roles,Path(self.tmp.name)/"control-output")

    def drive(self,stop=None,fail_extract=False,bad_task=False):
        """Test-only explicit dispatch; every actor uses an isolated process."""
        failed=False
        for node in self.g["slots"]:
            if node["id"]==stop:return
            slot=node["id"];finished=self.j._finished()
            if slot in finished:continue
            if node["condition"]=="disabled" or any(d in finished and not self.j._accepts_dependency(node,finished[d]) for d in node["depends_on"]):
                self.j.close_unrun("close-"+slot,slot);continue
            if node["role"] in self.controls.roles:self.controls.run(slot,AT)
            elif node["role"] in ("solver","acquisition","oracle"):
                self.task=node;self.tasks.run_fixture(slot,self.task_code(good=not bad_task),AT)
            else:
                if node["role"]=="extractor":
                    if fail_extract and not failed:
                        # Actual unread source reference is rejected, not a flag.
                        code=self.role_code(read=False);failed=True
                    else:code=self.role_code(output={"cards":[]})
                else:code=self.role_code()
                self.roles.run_fixture(slot,code,AT)

    def test_actual_task_grade_report_research_view_and_both_typed_proposals_keep_quality_unavailable(self):
        self.setup_controls(research=True);self.drive()
        report=self.controls.task_report();self.assertEqual(report["planned"],5)
        self.assertEqual(report["full_success"],5);self.assertEqual(report["unavailable"],0)
        self.assertEqual(self.j.report()["planned"],self.j.report()["terminal"])
        for role in ("evaluator","proposer"):
            result=self.controls.resolve(role+"-view");pub=self.ledger.get(result["publication"])
            value=self.ledger.get(pub["artifacts"]["view"])["value"];text=canonical(value).decode()
            self.assertNotIn("private-reasoning-marker",text);self.assertNotIn("expected",text)
            self.assertNotIn("seed",text);self.assertNotIn("task_status",text)
            self.assertTrue(value["outcomes"]);self.assertFalse(value["promotion_eligible"])
            validation=self.controls.resolve(role+"-proposal-validate")
            value=self.ledger.get(self.ledger.get(validation["publication"])["artifacts"]["validation"])["value"]
            self.assertEqual(value["status"],"accepted");self.assertEqual(value["quality"],"unavailable")
            source=self.ledger.get(self.ledger.get(validation["publication"])["artifacts"]["observable_sources"][0])
            self.assertEqual(source["episode"],role+"-proposal")
            self.assertTrue(self.controls.run(role+"-proposal-validate",AT)["cache_hit"])
        self.assertFalse(report["gain_evidence"]);self.ledger.audit()

    def test_failed_extraction_blocks_targets_and_unexecuted_grades_remain_in_denominator(self):
        self.setup_controls(research=True);self.drive(fail_extract=True)
        report=self.controls.task_report();self.assertEqual(report["planned"],5)
        blocked=[r for r in report["rows"] if r["task_status"]=="blocked"]
        self.assertEqual(len(blocked),2);self.assertEqual(report["unavailable"],2)
        self.assertEqual(report["full_success"],3)
        for row in blocked:
            grade=self.tasks.resolve_grade(row["task"]+"-grade")
            self.assertIsNone(self.ledger.get(grade["ref"])["capture"])
            self.assertFalse(grade["value"]["full_success"]);self.assertEqual(grade["value"]["status"],"task_not_executed")
            self.assertTrue(self.controls.run(row["task"]+"-grade",AT)["cache_hit"])
        self.assertEqual(self.j.report()["terminal"],self.j.report()["planned"])
        self.ledger.audit()

    def test_wrong_behavior_is_graded_independently_and_pending_rows_are_not_removed(self):
        self.setup_controls();before=self.controls.task_report()
        self.assertEqual(before["planned"],5);self.assertEqual(before["unavailable"],5)
        self.assertEqual(before["full_success"],0)
        self.drive(bad_task=True);report=self.controls.task_report()
        self.assertEqual(report["planned"],5);self.assertEqual(report["full_success"],0)
        self.assertEqual(report["unavailable"],0)

    def test_generic_fixture_grade_cannot_be_consumed_as_actual_task_result(self):
        self.setup_controls();grade=next(n for n in self.g["slots"] if n["role"]=="grade")
        self.run_until(grade["id"]);self.finish(grade)
        self.assert_code("missing_task_grade",self.controls.task_report)

    def test_control_publication_faults_roll_back_and_reuse_exact_started_input(self):
        self.setup_controls();slot=next(n["id"] for n in self.g["slots"] if n["role"]=="rebuild")
        self.drive(stop=slot)
        with self.assertRaises(RuntimeError):self.controls.run(slot,AT,self.crash("materialized"))
        before=self.ledger.audit()
        for phase in ("materialized","publication","stage","receipt"):
            with self.assertRaises(RuntimeError):self.controls.run(slot,AT,self.crash(phase))
            self.assertEqual(self.ledger.audit(),before)
        result=self.controls.run(slot,AT);self.assertEqual(self.controls.resolve(slot),result)
        before=self.ledger.audit();self.assertTrue(self.controls.run(slot,AT)["cache_hit"])
        self.assertEqual(self.ledger.audit(),before)

    def test_shadow_rebuild_child_commit_parent_crash_and_missing_receipt_are_detected(self):
        self.setup_controls(research=True,shadow=True)
        slot=next(n["id"] for n in self.g["slots"] if n["role"]=="rebuild" and n["store_scope"]!="main")
        self.drive(stop=slot)
        with self.assertRaises(RuntimeError):self.controls.run(slot,AT,self.crash("child_committed"))
        p=self.inputs.resolve(self.inputs.prepare(slot))["payload"]
        path=p["resources"]["store_binding"]["path"]
        with Ledger(path) as child:before=child.audit();receipts=[e["request_id"] for e in child.events("control") if e["request_id"].startswith("control-child-")]
        self.assertEqual(len(receipts),1)
        for phase in ("materialized","publication","stage","receipt"):
            with self.assertRaises(RuntimeError):self.controls.run(slot,AT,self.crash(phase))
            with Ledger(path) as child:self.assertEqual(child.audit(),before)
        self.controls.run(slot,AT);self.assertTrue(self.controls.run(slot,AT)["cache_hit"])
        with Ledger(path) as child:child.db.execute("DELETE FROM receipts WHERE request_id=?",(receipts[0],))
        self.assert_code("control_child_receipt_drift",lambda:self.controls.resolve(slot))

    def test_all_four_shadow_structural_results_do_not_claim_challenge_or_discovery_quality(self):
        self.setup_controls(research=True,shadow=True);self.drive()
        slots=[n["id"] for n in self.g["slots"] if n["role"]=="shadow_grade"]
        self.assertEqual(len(slots),4)
        for slot in slots:
            result=self.controls.resolve(slot);pub=self.ledger.get(result["publication"])
            value=self.ledger.get(pub["artifacts"]["validation"])["value"]
            self.assertEqual(value["quality"],"unavailable");self.assertFalse(value["promotion_eligible"])
        self.assertEqual(self.j.report()["terminal"],self.j.report()["planned"])

    def test_control_cache_rejects_environment_drift_and_preserves_committed_ledger(self):
        self.setup_controls();slot=next(n["id"] for n in self.g["slots"] if n["role"]=="rebuild")
        self.drive(stop=slot);self.controls.run(slot,AT);before=self.ledger.audit()
        changed=copy.deepcopy(self.inputs.binding["environment"]);changed["system"]["release"]="changed-fixture"
        with patch("memorylab.stage_inputs.stage_environment_identity",return_value=changed):
            self.assert_code("stage_environment_drift",lambda:self.controls.run(slot,AT))
            self.assert_code("stage_environment_drift",lambda:self.controls.resolve(slot))
        self.assertEqual(self.ledger.audit(),before)

    def test_output_lock_alias_is_rejected_before_nested_execution(self):
        self.setup_controls()
        self.assert_code("control_stage_lock_alias",lambda:ControlStages(self.inputs,self.tasks,self.roles,self.tasks.output))
        self.assert_code("control_stage_lock_alias",lambda:ControlStages(self.inputs,self.tasks,self.roles,self.roles.output))

    def test_unexecuted_grade_publication_and_unknown_outcome_are_atomic_at_every_phase(self):
        self.setup_controls();target=next(n for n in self.g["slots"] if n["role"]=="solver" and n["bindings"]["episode"]["role"]=="target")
        grade=target["id"]+"-grade";self.drive(stop=grade,fail_extract=True)
        with self.assertRaises(RuntimeError):self.controls.run(grade,AT,self.crash("grade"))
        before=self.ledger.audit()
        for phase in ("grade","outcome","usage","stage","receipt"):
            with self.assertRaises(RuntimeError):self.controls.run(grade,AT,self.crash(phase))
            self.assertEqual(self.ledger.audit(),before)
        self.controls.run(grade,AT)
        result=self.tasks.resolve_grade(grade);self.assertEqual(result["value"]["verification"],"unavailable")
        self.assertFalse(result["value"]["full_success"]);self.assertIsNone(self.ledger.get(result["ref"])["capture"])
        self.ledger.audit()
