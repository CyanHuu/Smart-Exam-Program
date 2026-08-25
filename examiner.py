import os
import sys

import pandas as pd


def _find_column(columns, keywords):
    for column in columns:
        name = str(column).strip().replace(" ", "").lower()
        if any(keyword in name for keyword in keywords):
            return column
    return None


def _normalise_flag(value, field, false_values, true_values):
    text = str(value).strip().lower()
    if text in {"0.0", "1.0"}:
        text = text[:-2]
    if text in false_values:
        return 0
    if text in true_values:
        return 1
    raise ValueError(f"{field}必须填写0/1或对应文字")


def analyze_teacher_list(file_path):
    """读取教师名单，返回 core_logic 使用的标准字段。"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"教师文件未找到: {file_path}")
    if os.path.splitext(file_path)[1].lower() not in {".xls", ".xlsx"}:
        raise ValueError("教师文件必须是.xls或.xlsx")
    try:
        df = pd.read_excel(file_path, dtype=str)
    except Exception as exc:
        raise ValueError(f"读取教师文件失败: {exc}") from exc
    df.columns = [str(column).strip() for column in df.columns]

    id_col = _find_column(df.columns, ["工号", "id", "number"])
    name_col = _find_column(df.columns, ["姓名", "name"])
    gender_col = _find_column(df.columns, ["性别", "gender"])
    experience_col = _find_column(df.columns, ["经验", "experience", "有经验"])
    dept_col = _find_column(df.columns, ["部门", "department", "dept"])
    required = [("工号", id_col), ("姓名", name_col), ("性别", gender_col), ("经验", experience_col), ("部门", dept_col)]
    missing = [label for label, column in required if column is None]
    if missing:
        raise ValueError(f"教师文件缺少必要列: {', '.join(missing)}")

    result = pd.DataFrame({
        "id_col": df[id_col].fillna("").map(lambda value: str(value).strip()),
        "name_col": df[name_col].fillna("").map(lambda value: str(value).strip()),
        "gender_col": df[gender_col].fillna("").map(lambda value: _normalise_flag(value, "性别", {"0", "女", "female", "f"}, {"1", "男", "male", "m"})),
        "experience_col": df[experience_col].fillna("").map(lambda value: _normalise_flag(value, "经验", {"0", "否", "无", "无经验", "no", "false"}, {"1", "是", "有", "有经验", "yes", "true"})),
        "dept_col": df[dept_col].fillna("").map(lambda value: str(value).strip()),
    })
    if result.empty:
        raise ValueError("教师名单为空")
    if result["id_col"].eq("").any():
        raise ValueError("工号列存在空值")
    if result["name_col"].eq("").any():
        raise ValueError("姓名列存在空值")
    duplicates = result.loc[result["id_col"].duplicated(keep=False), "id_col"].unique().tolist()
    if duplicates:
        raise ValueError(f"工号列存在重复值: {', '.join(duplicates[:10])}")
    return result


def main():
    file_path = sys.argv[1] if len(sys.argv) > 1 else "2.老师名单.xls"
    print(analyze_teacher_list(file_path))


if __name__ == "__main__":
    main()
