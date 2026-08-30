"""监考人员分配核心逻辑。

硬约束：教师在一次排考中只能出现一次。
软规则：经验、性别搭配、部门差异，无法全部满足时保留可执行结果并报告原因。
"""

import random

from schedule_loader import periods_overlap


PREFERENCE_WEIGHTS = {
    "default": {"experience": 60, "gender": 25, "department": 15},
    "experience": {"experience": 70, "gender": 15, "department": 15},
    "gender": {"experience": 25, "gender": 60, "department": 15},
    "department": {"experience": 25, "gender": 15, "department": 60},
    "experience_only": {"experience": 100, "gender": 0, "department": 0},
}


def preference_weights(preference="default"):
    """把面向用户的偏好名称转换成内部权重，避免用户直接操作百分比。"""
    try:
        return dict(PREFERENCE_WEIGHTS[preference])
    except KeyError as exc:
        raise ValueError(f"未知排考偏好: {preference}") from exc


def _normalise_weights(weights):
    weights = weights or {}
    result = {
        "experience": float(weights.get("experience", 60)),
        "gender": float(weights.get("gender", 25)),
        "department": float(weights.get("department", 15)),
    }
    if any(value < 0 for value in result.values()):
        raise ValueError("规则权重不能为负数")
    if not any(result.values()):
        raise ValueError("至少需要设置一个大于0的规则权重")
    return result


def _flag(value, field):
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}必须是0或1") from exc
    if value not in (0, 1):
        raise ValueError(f"{field}必须是0或1")
    return value


def _teacher_records(teacher_df):
    required = {"id_col", "name_col", "experience_col", "gender_col", "dept_col"}
    missing = required.difference(teacher_df.columns)
    if missing:
        raise ValueError(f"教师数据缺少标准字段: {', '.join(sorted(missing))}")

    records = []
    seen_ids = set()
    for index, row in teacher_df.iterrows():
        teacher_id = str(row["id_col"]).strip()
        name = str(row["name_col"]).strip()
        dept = str(row["dept_col"]).strip()
        if not teacher_id or teacher_id.lower() == "nan":
            raise ValueError(f"教师表第{index + 2}行工号为空")
        if not name or name.lower() == "nan":
            raise ValueError(f"教师表第{index + 2}行姓名为空")
        if teacher_id in seen_ids:
            raise ValueError(f"教师工号重复: {teacher_id}")
        seen_ids.add(teacher_id)
        records.append((
            teacher_id,
            name,
            _flag(row["experience_col"], "经验"),
            _flag(row["gender_col"], "性别"),
            dept if dept.lower() != "nan" else "未填写部门",
        ))
    if not records:
        raise ValueError("教师名单为空")
    return records


def _room_records(classroom_data):
    if not classroom_data:
        raise ValueError("考场名单为空")
    rooms = []
    seen = set()
    for index, item in enumerate(classroom_data, start=1):
        if len(item) < 2:
            raise ValueError(f"第{index}个考场记录格式错误")
        room = str(item[0]).strip()
        try:
            needed = int(item[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"考场 {room or index} 的监考人数必须是正整数") from exc
        if not room or room.lower() == "nan":
            raise ValueError(f"第{index}个考场编号为空")
        if room in seen:
            raise ValueError(f"考场编号重复: {room}")
        if needed <= 0:
            raise ValueError(f"考场 {room} 的监考人数必须是正整数")
        seen.add(room)
        rooms.append((room, needed))
    return rooms


def _score_teacher(teacher, current, weights):
    _, _, experience, gender, department = teacher
    genders = {item[3] for item in current}
    departments = {item[4] for item in current}
    return (
        experience * weights["experience"]
        + (1 if genders and gender not in genders else 0) * weights["gender"]
        + (1 if departments and department not in departments else 0) * weights["department"]
    )


def assign_proctors(classroom_data, teacher_df, weights=None, random_seed=None, return_report=False):
    """按规则分配监考人员，默认返回旧版兼容的 assignments 字典。"""
    rooms = _room_records(classroom_data)
    teachers = _teacher_records(teacher_df)
    weights = _normalise_weights(weights)
    rng = random.Random(random_seed)
    available = {teacher[0]: teacher for teacher in teachers}
    assignments = {room: [] for room, _ in rooms}
    warnings = []

    # 第一监考只在当前未使用的教师中选择，硬性防止跨考场重复。
    for room, _ in rooms:
        experienced = [teacher for teacher in available.values() if teacher[2] == 1]
        candidates = experienced or list(available.values())
        if not candidates:
            warnings.append(f"考场 {room} 无可用教师，无法安排第一监考")
            continue
        best_score = max(_score_teacher(teacher, [], weights) for teacher in candidates)
        tied = [teacher for teacher in candidates if _score_teacher(teacher, [], weights) == best_score]
        selected = rng.choice(tied)
        assignments[room].append(selected)
        del available[selected[0]]
        if not experienced:
            warnings.append(f"考场 {room} 未能安排有经验的第一监考")

    # 补足考场人数；同分候选随机，避免固定名单顺序造成长期偏置。
    for room, needed in rooms:
        current = assignments[room]
        while len(current) < needed and available:
            candidates = list(available.values())
            scores = {teacher[0]: _score_teacher(teacher, current, weights) for teacher in candidates}
            best_score = max(scores.values())
            tied = [teacher for teacher in candidates if scores[teacher[0]] == best_score]
            selected = rng.choice(tied)
            current.append(selected)
            del available[selected[0]]
        if len(current) < needed:
            warnings.append(f"考场 {room} 缺少 {needed - len(current)} 名监考教师")

    report = _build_report(rooms, assignments, teachers, available, warnings)
    print_report(report)
    return (assignments, report) if return_report else assignments


def build_backup_assignments(classroom_data, teachers, assignments, weights=None, backup_count=2):
    """为每个考场预留不在任何考场中的备选教师，避免同一时间冲突。"""
    rooms = _room_records(classroom_data)
    weights = _normalise_weights(weights)
    used_ids = {
        teacher[0]
        for room_teachers in assignments.values()
        for teacher in room_teachers
    }
    available = {
        teacher[0]: teacher
        for teacher in teachers
        if teacher[0] not in used_ids
    }
    backups = {room: [] for room, _ in rooms}
    for room, _ in rooms:
        current = assignments.get(room, [])
        for _ in range(max(0, int(backup_count))):
            if not available:
                break
            best_score = max(
                _score_teacher(teacher, current, weights)
                for teacher in available.values()
            )
            candidates = [
                teacher for teacher in available.values()
                if _score_teacher(teacher, current, weights) == best_score
            ]
            selected = candidates[0]
            backups[room].append(selected)
            del available[selected[0]]
    return backups


def _assign_one_session(rooms, teachers, blocked_ids, weights, rng):
    """在一个时间段内排考；blocked_ids来自时间重叠的其他场次。"""
    available = {
        teacher[0]: teacher
        for teacher in teachers
        if teacher[0] not in blocked_ids
    }
    assignments = {room: [] for room, _ in rooms}
    warnings = []
    for room, _ in rooms:
        experienced = [teacher for teacher in available.values() if teacher[2] == 1]
        candidates = experienced or list(available.values())
        if not candidates:
            warnings.append(f"考场 {room} 无可用教师，无法安排第一监考")
            continue
        best_score = max(_score_teacher(teacher, [], weights) for teacher in candidates)
        tied = [teacher for teacher in candidates if _score_teacher(teacher, [], weights) == best_score]
        selected = rng.choice(tied)
        assignments[room].append(selected)
        del available[selected[0]]
        if not experienced:
            warnings.append(f"考场 {room} 未能安排有经验的第一监考")

    for room, needed in rooms:
        current = assignments[room]
        while len(current) < needed and available:
            scores = {
                teacher[0]: _score_teacher(teacher, current, weights)
                for teacher in available.values()
            }
            best_score = max(scores.values())
            tied = [teacher for teacher in available.values() if scores[teacher[0]] == best_score]
            selected = rng.choice(tied)
            current.append(selected)
            del available[selected[0]]
        if len(current) < needed:
            warnings.append(f"考场 {room} 缺少 {needed - len(current)} 名监考教师")
    return assignments, available, warnings


def _build_session_backups(rooms, assignments, available, weights, backup_count):
    """从本场次剩余教师中分配唯一备选，避免备选同时服务多个考场。"""
    backups = {room: [] for room, _ in rooms}
    reserve = dict(available)
    for room, _ in rooms:
        current = assignments[room]
        for _ in range(max(0, int(backup_count))):
            if not reserve:
                break
            best_score = max(_score_teacher(teacher, current, weights) for teacher in reserve.values())
            candidates = [teacher for teacher in reserve.values() if _score_teacher(teacher, current, weights) == best_score]
            selected = candidates[0]
            backups[room].append(selected)
            del reserve[selected[0]]
    return backups, reserve


def assign_exam_sessions(sessions, teacher_df, weights=None, backup_count=2, random_seed=None):
    """按多个考试时间段排考，并返回场次结果、备选结果和教师工作量。"""
    if not sessions:
        raise ValueError("考试场次为空")
    teachers = _teacher_records(teacher_df)
    weights = _normalise_weights(weights)
    rng = random.Random(random_seed)
    ordered = sorted(sessions, key=lambda session: session["start"])
    results = {}

    for session in ordered:
        rooms = _room_records(session["rooms"])
        blocked_ids = set()
        for previous in results.values():
            if periods_overlap(session, previous["session"]):
                blocked_ids.update(
                    teacher[0]
                    for room_teachers in previous["assignments"].values()
                    for teacher in room_teachers
                )
                blocked_ids.update(
                    teacher[0]
                    for room_teachers in previous["backups"].values()
                    for teacher in room_teachers
                )
        assignments, available, warnings = _assign_one_session(
            rooms, teachers, blocked_ids, weights, rng
        )
        backups, reserve = _build_session_backups(
            rooms, assignments, available, weights, backup_count
        )
        report = _build_report(rooms, assignments, teachers, reserve, warnings)
        report["backup_total"] = sum(len(items) for items in backups.values())
        report["backup_shortage"] = sum(
            max(0, backup_count - len(items)) for items in backups.values()
        )
        results[session["session_id"]] = {
            "session": session,
            "assignments": assignments,
            "backups": backups,
            "report": report,
        }

    workload = build_workload_stats(results, teachers)
    return results, workload


def build_workload_stats(session_results, teachers):
    """统计正式监考、备选待命和总任务量。"""
    workload = {
        teacher[0]: {
            "teacher_id": teacher[0],
            "name": teacher[1],
            "formal_count": 0,
            "backup_count": 0,
            "total_count": 0,
            "sessions": [],
        }
        for teacher in teachers
    }
    for result in session_results.values():
        session = result["session"]
        start = session["start"]
        end = session["end"]
        if hasattr(start, "strftime") and hasattr(end, "strftime"):
            period = f"{start:%Y-%m-%d %H:%M}-{end:%H:%M}"
        else:
            period = f"{start}-{end}"
        label = f"{session['session_id']} {period}"
        for room, room_teachers in result["assignments"].items():
            for teacher in room_teachers:
                item = workload[teacher[0]]
                item["formal_count"] += 1
                item["total_count"] += 1
                item["sessions"].append(
                    {"session": label, "room": room, "role": "正式监考"}
                )
        for room, room_teachers in result["backups"].items():
            for teacher in room_teachers:
                item = workload[teacher[0]]
                item["backup_count"] += 1
                item["total_count"] += 1
                item["sessions"].append(
                    {"session": label, "room": room, "role": "备选监考"}
                )
    return workload


def rebuild_session_backups(
    session_results, session_id, teachers, weights=None, backup_count=2, excluded_ids=None
):
    """人工调整后保留原备选，只补缺口，避免其他考场名单无故洗牌。"""
    if session_id not in session_results:
        raise ValueError(f"未知考试场次: {session_id}")
    target = session_results[session_id]
    weights = _normalise_weights(weights)
    blocked_ids = set()
    for other_id, other in session_results.items():
        if other_id == session_id or not periods_overlap(target["session"], other["session"]):
            continue
        blocked_ids.update(
            teacher[0]
            for room_teachers in other["assignments"].values()
            for teacher in room_teachers
        )
        blocked_ids.update(
            teacher[0]
            for room_teachers in other["backups"].values()
            for teacher in room_teachers
        )
    excluded_ids = set(excluded_ids or ())
    current_ids = {
        teacher[0]
        for room_teachers in target["assignments"].values()
        for teacher in room_teachers
    }
    teacher_map = {teacher[0]: teacher for teacher in teachers}
    used_ids = blocked_ids | current_ids | excluded_ids
    rooms = _room_records(target["session"]["rooms"])
    backups = {room: [] for room, _ in rooms}
    for room in backups:
        for teacher in target.get("backups", {}).get(room, []):
            if teacher[0] in teacher_map and teacher[0] not in used_ids:
                backups[room].append(teacher_map[teacher[0]])
                used_ids.add(teacher[0])
    available = {
        teacher[0]: teacher
        for teacher in teachers
        if teacher[0] not in used_ids
    }
    for room, _ in rooms:
        current = target["assignments"].get(room, [])
        while len(backups[room]) < max(0, int(backup_count)) and available:
            best_score = max(
                _score_teacher(teacher, current, weights)
                for teacher in available.values()
            )
            selected = next(
                teacher for teacher in available.values()
                if _score_teacher(teacher, current, weights) == best_score
            )
            backups[room].append(selected)
            del available[selected[0]]
    target["backups"] = backups
    return backups


def _build_report(rooms, assignments, teachers, available, warnings):
    total_needed = sum(needed for _, needed in rooms)
    total_assigned = sum(len(room_teachers) for room_teachers in assignments.values())
    unfilled = []
    experience_ok = 0
    gender_mix_ok = 0
    department_mix_ok = 0
    for room, needed in rooms:
        current = assignments[room]
        missing = max(0, needed - len(current))
        if missing:
            unfilled.append({"room": room, "needed": needed, "assigned": len(current), "missing": missing})
        if current and current[0][2] == 1:
            experience_ok += 1
        if len({teacher[3] for teacher in current}) > 1:
            gender_mix_ok += 1
        if len({teacher[4] for teacher in current}) > 1:
            department_mix_ok += 1
    counts = {teacher[0]: 0 for teacher in teachers}
    names = {teacher[0]: teacher[1] for teacher in teachers}
    for room_teachers in assignments.values():
        for teacher in room_teachers:
            counts[teacher[0]] += 1
    return {
        "total_rooms": len(rooms),
        "total_needed": total_needed,
        "total_assigned": total_assigned,
        "shortage": max(0, total_needed - total_assigned),
        "available_teachers": len(available),
        "unfilled_rooms": unfilled,
        "experience_first_count": experience_ok,
        "gender_mix_count": gender_mix_ok,
        "department_mix_count": department_mix_ok,
        "teacher_counts": {names[teacher_id]: count for teacher_id, count in counts.items()},
        "warnings": warnings,
    }


def print_report(report):
    total_rooms = report["total_rooms"]
    print("\n" + "─" * 28)
    print("【排考结果】")
    print("─" * 28)
    print("【排考概况】")
    print(f"  考场数量：{total_rooms} 个")
    print(f"  监考需求：{report['total_needed']} 人")
    print(f"  已安排：  {report['total_assigned']} 人")
    print(f"  人员缺口：{report['shortage']} 人")
    print(f"  未安排教师：{report['available_teachers']} 人")
    print("\n【规则满足情况】")
    print(f"  有经验第一监考：{report['experience_first_count']}/{total_rooms}")
    print(f"  男女搭配：      {report['gender_mix_count']}/{total_rooms}")
    print(f"  不同部门：      {report['department_mix_count']}/{total_rooms}")
    warnings = report["warnings"]
    print("\n【异常提醒】")
    if warnings:
        for warning in warnings:
            print(f"  [提醒] {warning}")
    else:
        print("  无异常，所有考场均已满足人数需求")
    print("─" * 28 + "\n")
