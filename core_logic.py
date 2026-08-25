from classroom_2 import get_classroom_info
from examiner import analyze_teacher_list
from outputTask import write_assignments_to_excel, split_excel_by_serial



def assign_proctors(classroom_data, teacher_df):
    """为考场安排监考老师，实现经验>男女搭配>不同部门的优先级规则"""

    # 获取教室信息
    classrooms = [item[0] for item in classroom_data]
    classroom_to_num = {item[0]: item[1] for item in classroom_data}

    # 按优先级排序：有经验(1) > 性别(女=0) > 部门
    teacher_df = teacher_df.sort_values(
        by=['experience_col', 'gender_col', 'dept_col'],
        ascending=[False, True, True]
    ).reset_index(drop=True)

    # 创建老师列表（按优先级排序）
    teacher_list = []
    for _, row in teacher_df.iterrows():
        teacher_list.append((
            str(row['id_col']),  # 工号
            str(row['name_col']),  # 姓名
            row['experience_col'],  # 经验 - 0或1
            row['gender_col'], #性别 0女1男
            str(row['dept_col'])  # 部门
        ))
    # 按优先级排序：经验(1>0) > 性别(女=0, 男=1) > 部门(按部门字母顺序)

    # 检查总老师数是否足够
    total_teachers = len(teacher_list)
    total_needed = sum(classroom_to_num.values())
    if total_teachers < total_needed:
        print(f"警告: 老师总数({total_teachers})小于所需总人数({total_needed})，将尽可能安排")

    # 计算有经验老师数量
    experienced_teachers = [t for t in teacher_list if t[2] == 1]
    experienced_count = len(experienced_teachers)
    num_classrooms = len(classrooms)
    if experienced_count < num_classrooms:
        print(f"警告: 有经验老师数量({experienced_count})少于考场数量({num_classrooms})，"
              f"部分考场可能都是没有经验的老师哦。")

    # 分类并按性别排序（女优先）
    experienced = sorted([t for t in teacher_list if t[2] == 1], key=lambda x: x[3])
    inexperienced = sorted([t for t in teacher_list if t[2] == 0], key=lambda x: x[3])

    # ✅ 核心：使用列表直接管理，不再用 assigned 集合做冗余判断
    # 所有老师按“先有经验，后无经验”排序，准备依次分配
    all_teachers = experienced + inexperienced
    assigned_flags = [False] * len(all_teachers)  # 标记是否已分配

    assignments = {room: [] for room in classrooms}

    # === 阶段1：每个考场先分配1名有经验老师（如果还有）===
    for room in classrooms:
        assigned = False
        for i, teacher in enumerate(experienced):
            if not assigned_flags[all_teachers.index(teacher)]:
                assignments[room].append(teacher)
                assigned_flags[all_teachers.index(teacher)] = True
                assigned = True
                break
        if not assigned:
            print(f"⚠️  警告：考场 {room} 无法分配有经验老师")

    # === 阶段2：填满所有考场，必须满足人数需求 ===
    for room in classrooms:
        current = assignments[room]
        needed = classroom_to_num[room]
        while len(current) < needed:
            best_teacher = None
            best_idx = -1
            current_genders = {t[3] for t in current}
            current_depts = {t[4] for t in current}

            # 优先找异性
            for i, teacher in enumerate(all_teachers):
                if assigned_flags[i]:
                    continue
                # 优先使用无经验老师，但也可用有经验的（如果前面没分完）
                if teacher[3] not in current_genders:
                    best_teacher = teacher
                    best_idx = i
                    break

            # 如果没找到异性，找不同部门
            if best_teacher is None:
                for i, teacher in enumerate(all_teachers):
                    if assigned_flags[i]:
                        continue
                    if teacher[4] not in current_depts:
                        best_teacher = teacher
                        best_idx = i
                        break

            # 最后：随便找一个可用的
            if best_teacher is None:
                for i, teacher in enumerate(all_teachers):
                    if not assigned_flags[i]:
                        best_teacher = teacher
                        best_idx = i
                        break

            # 必须分配，除非无老师可用
            if best_teacher is None:
                print(f"错误：老师已耗尽，无法填满考场 {room}")
                break

            current.append(best_teacher)
            assigned_flags[best_idx] = True

    # === 输出结果 ===
    print("\n" + "=" * 80)
    print("考场监考老师安排结果")
    print("=" * 80)

    all_filled = True
    for room in classrooms:
        actual = len(assignments[room])
        needed = classroom_to_num[room]
        if actual < needed:
            all_filled = False
        print(f"\n考场: {room}")
        status = "✅" if actual >= needed else "❌"
        print(f"监考老师数量: {actual} (需求: {needed}) {status}")
        for i, (id_, name, exp, gender, dept) in enumerate(assignments[room], 1):
            gender_str = "女" if gender == 0 else "男"
            exp_str = "有经验" if exp == 1 else "无经验"
            print(f"  {i}. {name} (工号: {id_}, 部门: {dept}, 性别: {gender_str}, 经验: {exp_str})")

    # 统计
    total_assigned = sum(len(assignments[room]) for room in classrooms)
    exp_satisfied = sum(1 for t in assignments.values() if any(teacher[2] == 1 for teacher in t))
    mixed_gender = sum(1 for t in assignments.values() if len(set(teacher[3] for teacher in t)) == 2)

    print("\n" + "=" * 30)
    print(f"📊 分配统计")
    print("\n" + f"考场总数: {len(classrooms)}, 总共需要: {total_needed} 名监考老师")
    print("\n" + f"总计安排老师: {total_assigned} 人")
    print("\n" + f"剩余可用老师: {len(all_teachers) - total_assigned} 人")
    print("\n" + f"有经验老师总数: {len(experienced)} 人")
    print("\n" + f"考场有经验老师满足率: {exp_satisfied}/{len(classrooms)}")
    print("\n" + f"考场男女搭配满足率: {mixed_gender}/{len(classrooms)}")
    if total_assigned < total_needed:
        print("❌ 警告: 有考场未达到所需监考人数！")
    else:
        print("✅ 所有考场均已满足监考人数需求！")
    print("\n" + "=" * 50)

    return assignments

def main():
# 将该功能提供给用户使用，有简易的操作界面，主要是：用户选择本地的‘classroom_path’和‘examiner_path’，读取教室信息及监考老师信息，用户点击‘立即安排’，
#页面提示‘正在安排中，请等待~’，并将监考安排的输出结果显示在页面上，用户确认无误后可以点击‘数据导出’，选择本地‘write_excel_path’将监考人员数据写入表格，并提供‘分组导出’，
    classroom_path = '/Users/jiangye/Documents/小程序相关/教室编号输入.xls'
    examiner_path = '/Users/jiangye/Documents/小程序相关/2.老师名单.xls'
    classroom = get_classroom_info(classroom_path)
    examiner = analyze_teacher_list(examiner_path)
    # 执行安排
    assignments = assign_proctors(classroom, examiner)
    if not assignments:
        print("❌ 监考分配失败，无法写入。")
        return
    #输出数据
    write_excel_path = '/Users/jiangye/Documents/小程序相关/3.目标写入表格.xls'
    split_excel_path = '/Users/jiangye/Documents/小程序相关/输出分组表格.xls'
    header_row = 2
    sheet_count = 4
    write_assignments_to_excel(assignments,write_excel_path,header_row)
    split_excel_by_serial(write_excel_path,split_excel_path,header_row,sheet_count)


if __name__ == "__main__":
    main()
