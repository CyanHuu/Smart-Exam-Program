"""全局监考优化器。

大模型只产生 policy 草案；本模块是唯一会产生人员安排的地方。
"""

from collections import defaultdict
from contextlib import redirect_stdout
from datetime import timedelta
from io import StringIO
import re
from time import perf_counter

from ortools.sat.python import cp_model

from core_logic import _teacher_records, assign_exam_sessions, preference_weights
from schedule_loader import periods_overlap


DEFAULT_POLICY = {
    "experience_weight": 60,
    "gender_weight": 25,
    "department_weight": 15,
    "fairness_weight": 100,
    "consecutive_penalty": 20,
    "stability_weight": 100,
    "backup_count": 2,
    "max_formal_count": None,
    "max_total_count": None,
    "consecutive_gap_minutes": 120,
    "time_limit_seconds": 20,
    "random_seed": 7,
    "unavailable": {},
    "avoid_rooms": {},
    "allow_consecutive": {},
}


def normalise_policy(policy=None):
    result = dict(DEFAULT_POLICY)
    result.update(policy or {})
    numeric = (
        "experience_weight", "gender_weight", "department_weight", "fairness_weight",
        "consecutive_penalty", "stability_weight", "backup_count",
        "consecutive_gap_minutes", "time_limit_seconds", "random_seed",
    )
    for key in numeric:
        result[key] = int(result[key])
        if result[key] < 0:
            raise ValueError(f"{key}不能为负数")
    if not 1 <= result["time_limit_seconds"] <= 120:
        raise ValueError("time_limit_seconds必须为1～120")
    if not 0 <= result["backup_count"] <= 5:
        raise ValueError("backup_count必须为0～5")
    maximum = result.get("max_formal_count")
    if maximum not in (None, ""):
        result["max_formal_count"] = int(maximum)
        if result["max_formal_count"] < 0:
            raise ValueError("max_formal_count不能为负数")
    else:
        result["max_formal_count"] = None
    total_maximum = result.get("max_total_count")
    if total_maximum not in (None, ""):
        result["max_total_count"] = int(total_maximum)
        if result["max_total_count"] < 0:
            raise ValueError("max_total_count不能为负数")
    else:
        result["max_total_count"] = None
    for key in ("unavailable", "avoid_rooms", "allow_consecutive"):
        result[key] = dict(result.get(key) or {})
    return result


def _items(value):
    if value in (None, ""):
        return set()
    return {item.strip() for item in re.split(r"[,;，；\n]+", str(value)) if item.strip()}


def _yes_no(value, default=True):
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "1.0", "是", "允许", "yes", "true", "y"}:
        return True
    if text in {"0", "0.0", "否", "不允许", "no", "false", "n"}:
        return False
    raise ValueError(f"允许连续监考必须填写是/否或1/0，当前值: {value}")


def teacher_constraints(teacher_df, policy=None):
    policy = normalise_policy(policy)
    constraints = {}
    for _, row in teacher_df.iterrows():
        teacher_id = str(row["id_col"]).strip()
        max_text = str(row.get("max_formal_count_col", "") or "").strip()
        maximum = int(float(max_text)) if max_text else None
        if maximum is not None and maximum < 0:
            raise ValueError(f"教师 {teacher_id} 的最多正式监考次数不能为负数")
        if policy["max_formal_count"] is not None:
            maximum = min(maximum, policy["max_formal_count"]) if maximum is not None else policy["max_formal_count"]
        if policy["max_total_count"] is not None:
            maximum = min(maximum, policy["max_total_count"]) if maximum is not None else policy["max_total_count"]
        unavailable = _items(row.get("unavailable_sessions_col", ""))
        unavailable.update(map(str, policy["unavailable"].get(teacher_id, [])))
        avoid_rooms = _items(row.get("avoid_rooms_col", ""))
        avoid_rooms.update(map(str, policy["avoid_rooms"].get(teacher_id, [])))
        allow = _yes_no(row.get("allow_consecutive_col", ""), True)
        if teacher_id in policy["allow_consecutive"]:
            allow = bool(policy["allow_consecutive"][teacher_id])
        constraints[teacher_id] = {
            "unavailable": unavailable,
            "avoid_rooms": avoid_rooms,
            "max_formal_count": maximum,
            "max_total_count": policy["max_total_count"],
            "allow_consecutive": allow,
        }
    return constraints


def validate_policy_references(sessions, teacher_df, policy=None):
    policy = normalise_policy(policy)
    teacher_ids = {str(value).strip() for value in teacher_df["id_col"]}
    session_ids = {str(session["session_id"]) for session in sessions}
    room_ids = {str(room) for session in sessions for room, _ in session["rooms"]}
    errors = []
    for field in ("unavailable", "avoid_rooms", "allow_consecutive"):
        unknown = sorted(set(policy[field]) - teacher_ids)
        if unknown:
            errors.append(f"{field}引用了未知教师: {', '.join(unknown)}")
    for teacher_id, values in policy["unavailable"].items():
        unknown = sorted(set(map(str, values)) - session_ids)
        if unknown:
            errors.append(f"教师 {teacher_id} 引用了未知场次: {', '.join(unknown)}")
    for teacher_id, values in policy["avoid_rooms"].items():
        unknown = sorted(set(map(str, values)) - room_ids)
        if unknown:
            errors.append(f"教师 {teacher_id} 引用了未知考场: {', '.join(unknown)}")
    return errors


def _consecutive_pairs(sessions, gap_minutes):
    ordered = sorted(enumerate(sessions), key=lambda pair: pair[1]["start"])
    result = []
    for left_index, left in ordered:
        for right_index, right in ordered:
            if right["start"] < left["end"] or left_index == right_index:
                continue
            if right["start"].date() != left["start"].date():
                continue
            if right["start"] - left["end"] <= timedelta(minutes=gap_minutes):
                result.append((left_index, right_index))
    return result


def optimise_exam_sessions(
    sessions, teacher_df, policy=None, previous_results=None,
    unavailable_teacher_ids=None, locked_session_ids=None,
):
    """全局优化正式与备选监考，返回与旧 GUI/导出器兼容的结构。"""
    policy = normalise_policy(policy)
    errors = validate_policy_references(sessions, teacher_df, policy)
    if errors:
        raise ValueError("；".join(errors))
    teachers = _teacher_records(teacher_df)
    limits = teacher_constraints(teacher_df, policy)
    teacher_by_id = {teacher[0]: teacher for teacher in teachers}
    unavailable_teacher_ids = set(map(str, unavailable_teacher_ids or ()))
    unknown_absent = unavailable_teacher_ids - set(teacher_by_id)
    if unknown_absent:
        raise ValueError(f"未知请假教师: {', '.join(sorted(unknown_absent))}")
    locked_session_ids = set(map(str, locked_session_ids or ()))

    sessions_do_not_overlap = not any(
        periods_overlap(sessions[left], sessions[right])
        for left in range(len(sessions)) for right in range(left + 1, len(sessions))
    )
    each_session_has_capacity = all(
        sum(needed for _, needed in session["rooms"]) <= len(teachers)
        for session in sessions
    )
    unrestricted = (
        not unavailable_teacher_ids and not previous_results and not locked_session_ids
        and sessions_do_not_overlap and each_session_has_capacity
        and all(
            not rule["unavailable"] and not rule["avoid_rooms"]
            and rule["max_formal_count"] is None and rule["max_total_count"] is None and rule["allow_consecutive"]
            for rule in limits.values()
        )
    )
    if unrestricted:
        started = perf_counter()
        counts = defaultdict(int)
        results = {}
        for session in sorted(sessions, key=lambda value: value["start"]):
            used = set()
            assignments = {}
            for room, needed in session["rooms"]:
                room = str(room)
                current = []
                first_candidates = [teacher for teacher in teachers if teacher[0] not in used and teacher[2]]
                first_candidates = first_candidates or [teacher for teacher in teachers if teacher[0] not in used]
                if first_candidates:
                    first_teacher = min(first_candidates, key=lambda teacher: (counts[teacher[0]], teacher[0]))
                    current.append(first_teacher)
                    used.add(first_teacher[0])
                    counts[first_teacher[0]] += 1
                while len(current) < int(needed):
                    candidates = [teacher for teacher in teachers if teacher[0] not in used]
                    if not candidates:
                        break
                    teacher = min(candidates, key=lambda item: (counts[item[0]], item[2], item[0]))
                    current.append(teacher)
                    used.add(teacher[0])
                    counts[teacher[0]] += 1
                assignments[room] = current
            results[str(session["session_id"])] = {"session": session, "assignments": assignments, "backups": {}}
        _assign_backups(results, sessions, teachers, limits, policy["backup_count"])
        for result in results.values():
            result["report"] = _session_report(result, policy["backup_count"])
        metrics = schedule_metrics(results, teachers, limits)
        metrics["backup_shortage"] = sum(
            result["report"]["backup_shortage"] for result in results.values()
        )
        metrics.update({
            "solver_status": "FAST_GREEDY",
            "objective_value": 0,
            "wall_time_seconds": round(perf_counter() - started, 3),
        })
        return results, metrics

    model = cp_model.CpModel()
    formal, first = {}, {}
    formal_shortage, first_shortage = {}, {}
    room_needs = {}
    session_rooms = []
    backup_need = policy["backup_count"]

    for s, session in enumerate(sessions):
        rooms = [(str(room), int(needed)) for room, needed in session["rooms"]]
        session_rooms.append(rooms)
        for room, needed in rooms:
            room_needs[(s, room)] = needed
            formal_shortage[s, room] = model.new_int_var(0, needed, f"formal_short_{s}_{room}")
            first_shortage[s, room] = model.new_bool_var(f"first_short_{s}_{room}")
            for teacher in teachers:
                teacher_id = teacher[0]
                formal[teacher_id, s, room] = model.new_bool_var(f"x_{teacher_id}_{s}_{room}")
                first[teacher_id, s, room] = model.new_bool_var(f"f_{teacher_id}_{s}_{room}")
                model.add(first[teacher_id, s, room] <= formal[teacher_id, s, room])
                blocked = (
                    teacher_id in unavailable_teacher_ids
                    or str(session["session_id"]) in limits[teacher_id]["unavailable"]
                    or room in limits[teacher_id]["avoid_rooms"]
                )
                if blocked:
                    model.add(formal[teacher_id, s, room] == 0)
            model.add(
                sum(formal[t[0], s, room] for t in teachers) + formal_shortage[s, room] == needed
            )
            model.add(
                sum(first[t[0], s, room] for t in teachers) + first_shortage[s, room] == 1
            )

    assigned = {}
    formal_counts = {}
    for teacher in teachers:
        teacher_id = teacher[0]
        for s, rooms in enumerate(session_rooms):
            assigned[teacher_id, s] = sum(formal[teacher_id, s, room] for room, _ in rooms)
            model.add(assigned[teacher_id, s] <= 1)
        for left in range(len(sessions)):
            for right in range(left + 1, len(sessions)):
                if periods_overlap(sessions[left], sessions[right]):
                    model.add(assigned[teacher_id, left] + assigned[teacher_id, right] <= 1)
        formal_counts[teacher_id] = model.new_int_var(0, len(sessions), f"count_{teacher_id}")
        model.add(
            formal_counts[teacher_id] == sum(
                formal[teacher_id, s, room]
                for s, rooms in enumerate(session_rooms) for room, _ in rooms
            )
        )
        maximum = limits[teacher_id]["max_formal_count"]
        if maximum is not None:
            model.add(formal_counts[teacher_id] <= maximum)

    # 无个人限制时，理想均摊边界是可证明的，直接作为硬约束以保证结果稳定。
    sessions_do_not_overlap = not any(
        periods_overlap(sessions[left], sessions[right])
        for left in range(len(sessions)) for right in range(left + 1, len(sessions))
    )
    each_session_has_capacity = all(
        sum(needed for _, needed in rooms) <= len(teachers) for rooms in session_rooms
    )
    unrestricted = (
        not unavailable_teacher_ids and not previous_results and not locked_session_ids
        and sessions_do_not_overlap and each_session_has_capacity
        and all(
            not rule["unavailable"] and not rule["avoid_rooms"]
            and rule["max_formal_count"] is None and rule["max_total_count"] is None and rule["allow_consecutive"]
            for rule in limits.values()
        )
    )
    if unrestricted:
        total_slots = sum(needed for rooms in session_rooms for _, needed in rooms)
        lower = total_slots // len(teachers)
        upper = lower + bool(total_slots % len(teachers))
        for count in formal_counts.values():
            model.add(count >= lower)
            model.add(count <= upper)

    consecutive_both = []
    for teacher in teachers:
        teacher_id = teacher[0]
        for left, right in _consecutive_pairs(sessions, policy["consecutive_gap_minutes"]):
            if not limits[teacher_id]["allow_consecutive"]:
                model.add(assigned[teacher_id, left] + assigned[teacher_id, right] <= 1)
            elif policy["consecutive_penalty"]:
                both = model.new_bool_var(f"consecutive_{teacher_id}_{left}_{right}")
                model.add(both >= assigned[teacher_id, left] + assigned[teacher_id, right] - 1)
                consecutive_both.append(both)

    previous_formal = set()
    if previous_results:
        session_index = {str(session["session_id"]): index for index, session in enumerate(sessions)}
        for session_id, result in previous_results.items():
            if str(session_id) not in session_index:
                continue
            s = session_index[str(session_id)]
            for room, room_teachers in result.get("assignments", {}).items():
                previous_formal.update((teacher[0], s, str(room)) for teacher in room_teachers)
        for s, session in enumerate(sessions):
            if str(session["session_id"]) not in locked_session_ids:
                continue
            for room, _ in session_rooms[s]:
                for teacher in teachers:
                    teacher_id = teacher[0]
                    model.add(formal[teacher_id, s, room] == int((teacher_id, s, room) in previous_formal))

        # 稳定性是硬约束：旧正式安排只要不违反本次不可用/回避条件，就必须保留。
        # 只有受影响教师或新硬约束冲突时，才允许重新分配。
        for teacher_id, s, room in previous_formal:
            key = (teacher_id, s, room)
            if key not in formal:
                continue
            session_id = str(sessions[s]["session_id"])
            rule = limits[teacher_id]
            if teacher_id in unavailable_teacher_ids or session_id in rule["unavailable"] or room in rule["avoid_rooms"]:
                continue
            model.add(formal[key] == 1)

    # 现有贪心算法能在毫秒级给出可执行方案，用它热启动 CP-SAT，避免大数据从零搜索。
    try:
        with redirect_stdout(StringIO()):
            hint_results, _ = assign_exam_sessions(
                sessions, teacher_df, weights=preference_weights("default"),
                backup_count=backup_need, random_seed=policy["random_seed"],
            )
        session_index = {str(session["session_id"]): index for index, session in enumerate(sessions)}
        for session_id, result in hint_results.items():
            s = session_index[str(session_id)]
            for room, items in result["assignments"].items():
                for index, teacher in enumerate(items):
                    if (teacher[0], s, str(room)) in formal:
                        model.add_hint(formal[teacher[0], s, str(room)], 1)
                        if index == 0:
                            model.add_hint(first[teacher[0], s, str(room)], 1)
    except Exception:
        # 扩展约束可能让旧算法的方案不可用；提示不是正确性前提。
        pass

    max_count = model.new_int_var(0, len(sessions), "max_formal_count")
    min_count = model.new_int_var(0, len(sessions), "min_formal_count")
    model.add_max_equality(max_count, list(formal_counts.values()))
    model.add_min_equality(min_count, list(formal_counts.values()))

    gender_mix, department_mix = [], []
    for s, rooms in enumerate(session_rooms):
        for room, needed in rooms:
            if needed < 2:
                continue
            male = model.new_bool_var(f"male_{s}_{room}")
            female = model.new_bool_var(f"female_{s}_{room}")
            male_sum = sum(formal[t[0], s, room] for t in teachers if t[3] == 1)
            female_sum = sum(formal[t[0], s, room] for t in teachers if t[3] == 0)
            model.add(male_sum >= male)
            model.add(male_sum <= needed * male)
            model.add(female_sum >= female)
            model.add(female_sum <= needed * female)
            mixed = model.new_bool_var(f"gender_mix_{s}_{room}")
            model.add(mixed <= male)
            model.add(mixed <= female)
            model.add(mixed >= male + female - 1)
            gender_mix.append(mixed)

            present = []
            for department in sorted({teacher[4] for teacher in teachers}):
                has_department = model.new_bool_var(f"dept_{s}_{room}_{len(present)}")
                dept_sum = sum(formal[t[0], s, room] for t in teachers if t[4] == department)
                model.add(dept_sum >= has_department)
                model.add(dept_sum <= needed * has_department)
                present.append(has_department)
            diverse = model.new_bool_var(f"dept_mix_{s}_{room}")
            model.add(sum(present) >= 2 * diverse)
            department_mix.append(diverse)

    shortage_cost = sum(formal_shortage.values()) * 1_000_000 + sum(first_shortage.values()) * 100_000
    fairness_cost = (max_count - min_count) * policy["fairness_weight"] * 100
    consecutive_cost = sum(consecutive_both) * policy["consecutive_penalty"]
    experienced_first = sum(
        first[t[0], s, room]
        for t in teachers if t[2] == 1
        for s, rooms in enumerate(session_rooms) for room, _ in rooms
    )
    soft_reward = (
        experienced_first * policy["experience_weight"]
        + sum(gender_mix) * policy["gender_weight"]
        + sum(department_mix) * policy["department_weight"]
    )
    model.minimize(shortage_cost + fairness_cost + consecutive_cost - soft_reward)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = policy["time_limit_seconds"]
    solver.parameters.random_seed = policy["random_seed"]
    solver.parameters.num_search_workers = 8
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise ValueError(
            f"在当前硬约束下未产生可执行安排（{solver.status_name(status)}），"
            "请放宽限制或增加求解时间"
        )

    results = {}
    for s, session in enumerate(sessions):
        assignments = {}
        for room, needed in session_rooms[s]:
            selected = [t for t in teachers if solver.value(formal[t[0], s, room])]
            selected.sort(key=lambda t: (not solver.value(first[t[0], s, room]), t[0]))
            assignments[room] = selected
        results[str(session["session_id"])] = {
            "session": session,
            "assignments": assignments,
            "backups": {},
        }
    _assign_backups(
        results, sessions, teachers, limits, backup_need,
        previous_results=previous_results,
        unavailable_teacher_ids=unavailable_teacher_ids,
        consecutive_gap_minutes=policy["consecutive_gap_minutes"],
    )
    for result in results.values():
        result["report"] = _session_report(result, backup_need)
    metrics = schedule_metrics(results, teachers, limits)
    metrics["backup_shortage"] = sum(result["report"]["backup_shortage"] for result in results.values())
    metrics.update({
        "solver_status": solver.status_name(status),
        "objective_value": round(solver.objective_value, 2),
        "wall_time_seconds": round(solver.wall_time, 3),
    })
    return results, metrics


def _assign_backups(
    results, sessions, teachers, limits, backup_count, previous_results=None,
    unavailable_teacher_ids=None, consecutive_gap_minutes=120,
):
    """正式安排确定后分配备选；分步求解能让 135 人样例稳定在 20 秒内完成。"""
    unavailable_teacher_ids = set(unavailable_teacher_ids or ())
    assigned_times = defaultdict(list)
    formal_counts = defaultdict(int)
    for result in results.values():
        for items in result["assignments"].values():
            for teacher in items:
                assigned_times[teacher[0]].append(result["session"])
                formal_counts[teacher[0]] += 1
    total_counts = defaultdict(int, formal_counts)

    previous_lookup = {}
    for session_id, result in (previous_results or {}).items():
        for room, items in result.get("backups", {}).items():
            previous_lookup[str(session_id), str(room)] = [teacher[0] for teacher in items]

    def unavailable_for_task(teacher_id, session, room):
        if teacher_id in unavailable_teacher_ids:
            return True
        rule = limits[teacher_id]
        if str(session["session_id"]) in rule["unavailable"] or room in rule["avoid_rooms"]:
            return True
        for existing in assigned_times[teacher_id]:
            if periods_overlap(existing, session):
                return True
            if not rule["allow_consecutive"] and existing["start"].date() == session["start"].date():
                if existing["end"] <= session["start"]:
                    gap = session["start"] - existing["end"]
                elif session["end"] <= existing["start"]:
                    gap = existing["start"] - session["end"]
                else:
                    return True
                if gap <= timedelta(minutes=consecutive_gap_minutes):
                    return True
        return False

    tasks = []
    for session in sorted(sessions, key=lambda value: value["start"]):
        session_id = str(session["session_id"])
        result = results[session_id]
        result["backups"] = {}
        for room, _ in session["rooms"]:
            tasks.append((session, session_id, str(room)))
            result["backups"][str(room)] = []

    # ponytail: round-robin backup allocation; replace with weighted matching only if backup fairness needs optimisation.
    for backup_index in range(backup_count):
        # 只要还有任何考场没有备选人员，就先停止追加第二名，避免备选集中在少数考场。
        if backup_index and any(
            min(backup_count, len(results[session_id]["assignments"].get(room, []))) > 0
            and not results[session_id]["backups"][room]
            for _, session_id, room in tasks
        ):
            break
        # 每一轮只给尚未覆盖的考场补一个备选，且先处理候选人更少的考场，最大化覆盖面。
        def available_count(task):
            session, _, room = task
            return sum(
                not unavailable_for_task(teacher[0], session, room)
                and (limits[teacher[0]]["max_total_count"] is None
                     or total_counts[teacher[0]] < limits[teacher[0]]["max_total_count"])
                for teacher in teachers
            )

        for session, session_id, room in sorted(tasks, key=available_count):
            result = results[session_id]
            current = result["assignments"][room]
            target_count = min(backup_count, len(current))
            if len(result["backups"][room]) >= target_count:
                continue
            genders = {teacher[3] for teacher in current}
            departments = {teacher[4] for teacher in current}
            preferred = previous_lookup.get((session_id, room), [])

            def rank(teacher):
                return (
                    teacher[0] not in preferred,
                    preferred.index(teacher[0]) if teacher[0] in preferred else 999,
                    formal_counts[teacher[0]],
                    -(teacher[2] * 3 + int(bool(genders) and teacher[3] not in genders) * 2
                      + int(bool(departments) and teacher[4] not in departments)),
                    teacher[0],
                )

            candidates = sorted(
                (teacher for teacher in teachers
                 if not unavailable_for_task(teacher[0], session, room)
                 and (limits[teacher[0]]["max_total_count"] is None
                      or total_counts[teacher[0]] < limits[teacher[0]]["max_total_count"])),
                key=rank,
            )
            if candidates:
                selected = candidates[0]
                result["backups"][room].append(selected)
                assigned_times[selected[0]].append(session)
                total_counts[selected[0]] += 1


def _session_report(result, backup_count):
    rooms = result["session"]["rooms"]
    assignments = result["assignments"]
    backups = result["backups"]
    unfilled = []
    for room, needed in rooms:
        assigned = len(assignments.get(str(room), []))
        if assigned < needed:
            unfilled.append({"room": str(room), "needed": needed, "assigned": assigned, "missing": needed - assigned})
    backup_shortage = sum(
        max(0, min(backup_count, len(assignments.get(str(room), []))) - len(backups.get(str(room), [])))
        for room, _ in rooms
    )
    return {
        "total_rooms": len(rooms),
        "total_needed": sum(needed for _, needed in rooms),
        "total_assigned": sum(map(len, assignments.values())),
        "shortage": sum(item["missing"] for item in unfilled),
        "unfilled_rooms": unfilled,
        "backup_total": sum(map(len, backups.values())),
        "backup_shortage": backup_shortage,
        "experience_first_count": sum(bool(items and items[0][2]) for items in assignments.values()),
        "gender_mix_count": sum(len({t[3] for t in items}) > 1 for items in assignments.values()),
        "department_mix_count": sum(len({t[4] for t in items}) > 1 for items in assignments.values()),
        "warnings": [f"考场 {item['room']} 缺少 {item['missing']} 名监考教师" for item in unfilled],
    }


def schedule_metrics(results, teachers, limits=None):
    counts = {teacher[0]: 0 for teacher in teachers}
    conflicts = 0
    assigned_by_teacher = defaultdict(list)
    shortage = backup_shortage = experience = gender = department = rooms = 0
    for result in results.values():
        session = result["session"]
        room_needs = dict((str(room), int(needed)) for room, needed in session["rooms"])
        rooms += len(room_needs)
        for room, needed in room_needs.items():
            selected = result.get("assignments", {}).get(room, [])
            shortage += max(0, needed - len(selected))
            experience += bool(selected and selected[0][2])
            gender += len({t[3] for t in selected}) > 1
            department += len({t[4] for t in selected}) > 1
            for teacher in selected:
                counts[teacher[0]] += 1
                assigned_by_teacher[teacher[0]].append(session)
            backup_shortage += max(0, 0)
        for items in result.get("backups", {}).values():
            for teacher in items:
                assigned_by_teacher[teacher[0]].append(session)
    for teacher_sessions in assigned_by_teacher.values():
        for index, left in enumerate(teacher_sessions):
            conflicts += sum(periods_overlap(left, right) for right in teacher_sessions[index + 1:])
    values = list(counts.values()) or [0]
    return {
        "shortage": shortage,
        "conflicts": conflicts,
        "formal_min": min(values),
        "formal_max": max(values),
        "formal_range": max(values) - min(values),
        "formal_zero_teachers": sum(value == 0 for value in values),
        "experience_first": experience,
        "gender_mix": gender,
        "department_mix": department,
        "total_rooms": rooms,
        "teacher_formal_counts": counts,
    }


def assignment_reasons(results, teacher_df):
    teachers = {teacher[0]: teacher for teacher in _teacher_records(teacher_df)}
    counts = defaultdict(int)
    for result in results.values():
        for items in result["assignments"].values():
            for teacher in items:
                counts[teacher[0]] += 1
    reasons = {}
    for session_id, result in results.items():
        for room, items in result["assignments"].items():
            genders = {teacher[3] for teacher in items}
            departments = {teacher[4] for teacher in items}
            for index, teacher in enumerate(items):
                facts = [f"当前正式监考工作量 {counts[teacher[0]]} 次", "该场次可用"]
                if index == 0 and teacher[2]:
                    facts.append("具有经验，适合第一监考")
                if len(genders) > 1:
                    facts.append("与同考场教师形成性别搭配")
                if len(departments) > 1:
                    facts.append("与同考场教师部门不同")
                reasons[f"{session_id}|{room}|{teacher[0]}"] = facts
    return reasons


def compare_metrics(baseline, optimised):
    keys = (
        "shortage", "conflicts", "formal_range", "formal_zero_teachers",
        "experience_first", "gender_mix", "department_mix",
    )
    return {
        key: {"before": baseline.get(key, 0), "after": optimised.get(key, 0), "change": optimised.get(key, 0) - baseline.get(key, 0)}
        for key in keys
    }
