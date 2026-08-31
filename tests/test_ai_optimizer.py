import unittest
from datetime import datetime

import pandas as pd

from ai_optimizer import optimise_exam_sessions, validate_policy_references


def teachers():
    return pd.DataFrame({
        "id_col": ["T1", "T2", "T3", "T4"],
        "name_col": ["教师1", "教师2", "教师3", "教师4"],
        "experience_col": [1, 1, 0, 0],
        "gender_col": [1, 0, 1, 0],
        "dept_col": ["A", "B", "A", "B"],
        "unavailable_sessions_col": ["", "", "", ""],
        "max_formal_count_col": ["", "", "", ""],
        "allow_consecutive_col": ["", "", "", ""],
        "avoid_rooms_col": ["", "", "", ""],
    })


def sessions():
    return [
        {
            "session_id": "S1", "title": "", "period_text": "",
            "start": datetime(2026, 1, 1, 8), "end": datetime(2026, 1, 1, 10),
            "rooms": [("R1", 2)], "room_meta": {},
        },
        {
            "session_id": "S2", "title": "", "period_text": "",
            "start": datetime(2026, 1, 2, 8), "end": datetime(2026, 1, 2, 10),
            "rooms": [("R1", 2)], "room_meta": {},
        },
    ]


class OptimizerTests(unittest.TestCase):
    def test_global_fairness_and_experienced_first(self):
        results, metrics = optimise_exam_sessions(sessions(), teachers(), {"backup_count": 0, "time_limit_seconds": 5})
        self.assertEqual(metrics["shortage"], 0)
        self.assertEqual(metrics["conflicts"], 0)
        self.assertEqual(metrics["formal_min"], 1)
        self.assertEqual(metrics["formal_max"], 1)
        self.assertTrue(all(result["assignments"]["R1"][0][2] for result in results.values()))

    def test_unavailable_and_max_count_are_hard_constraints(self):
        frame = teachers()
        frame.loc[0, "unavailable_sessions_col"] = "S1"
        frame.loc[1, "max_formal_count_col"] = "1"
        results, _ = optimise_exam_sessions(sessions(), frame, {"backup_count": 0, "time_limit_seconds": 5})
        self.assertNotIn("T1", {teacher[0] for teacher in results["S1"]["assignments"]["R1"]})
        t2_count = sum(
            teacher[0] == "T2"
            for result in results.values() for teacher in result["assignments"]["R1"]
        )
        self.assertLessEqual(t2_count, 1)

    def test_policy_rejects_unknown_references(self):
        errors = validate_policy_references(sessions(), teachers(), {"unavailable": {"T1": ["missing"]}})
        self.assertTrue(errors)

    def test_shortage_never_creates_duplicate_assignment(self):
        frame = teachers().iloc[:1].copy()
        one_session = [dict(sessions()[0], rooms=[("R1", 2)])]
        results, metrics = optimise_exam_sessions(
            one_session, frame, {"backup_count": 0, "time_limit_seconds": 5}
        )
        assigned = results["S1"]["assignments"]["R1"]
        self.assertEqual(len(assigned), 1)
        self.assertEqual(metrics["shortage"], 1)
        self.assertEqual(len({teacher[0] for teacher in assigned}), 1)

    def test_backups_are_distributed_before_second_backup(self):
        one_session = [dict(sessions()[0], rooms=[("R1", 1), ("R2", 1)])]
        results, _ = optimise_exam_sessions(
            one_session, teachers(), {"backup_count": 1, "time_limit_seconds": 5}
        )
        self.assertEqual(len(results["S1"]["backups"]["R1"]), 1)
        self.assertEqual(len(results["S1"]["backups"]["R2"]), 1)

    def test_backup_target_follows_formal_count(self):
        one_session = [dict(sessions()[0], rooms=[("R1", 2)])]
        results, _ = optimise_exam_sessions(
            one_session, teachers(), {"backup_count": 2, "time_limit_seconds": 5}
        )
        self.assertEqual(len(results["S1"]["assignments"]["R1"]), 2)
        self.assertEqual(len(results["S1"]["backups"]["R1"]), 2)

    def test_replan_locks_unaffected_sessions(self):
        before, _ = optimise_exam_sessions(sessions(), teachers(), {"backup_count": 0, "time_limit_seconds": 5})
        absent = before["S1"]["assignments"]["R1"][0][0]
        unchanged = [teacher[0] for teacher in before["S2"]["assignments"]["R1"]]
        after, _ = optimise_exam_sessions(
            sessions(), teachers(), {"backup_count": 0, "time_limit_seconds": 5},
            previous_results=before, unavailable_teacher_ids=[absent], locked_session_ids=["S2"],
        )
        self.assertNotIn(absent, {teacher[0] for teacher in after["S1"]["assignments"]["R1"]})
        self.assertEqual(unchanged, [teacher[0] for teacher in after["S2"]["assignments"]["R1"]])

    def test_stability_keeps_non_conflicting_assignments_in_affected_session(self):
        before, _ = optimise_exam_sessions(sessions(), teachers(), {"backup_count": 0, "time_limit_seconds": 5})
        original = [teacher[0] for teacher in before["S1"]["assignments"]["R1"]]
        absent = original[0]
        after, _ = optimise_exam_sessions(
            sessions(), teachers(), {"backup_count": 0, "time_limit_seconds": 5},
            previous_results=before, unavailable_teacher_ids=[absent], locked_session_ids=[],
        )
        current = {teacher[0] for teacher in after["S1"]["assignments"]["R1"]}
        self.assertNotIn(absent, current)
        self.assertIn(original[1], current)


if __name__ == "__main__":
    unittest.main()
