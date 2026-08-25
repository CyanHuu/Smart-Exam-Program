"""监考人员分配核心逻辑。

硬约束：教师在一次排考中只能出现一次。
软规则：经验、性别搭配、部门差异，无法全部满足时保留可执行结果并报告原因。
"""

import random


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
