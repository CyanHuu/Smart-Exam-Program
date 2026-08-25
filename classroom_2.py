import os

import pandas as pd


def _find_column(columns, keywords):
    for column in columns:
        name = str(column).strip().replace(" ", "").lower()
        if any(keyword in name for keyword in keywords):
            return column
    return None


def get_classroom_info(file_path):
    """读取考场编号和监考人数，拒绝空值、重复编号和非法人数。"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"考场文件未找到: {file_path}")
    if os.path.splitext(file_path)[1].lower() not in {".xls", ".xlsx"}:
        raise ValueError("考场文件必须是.xls或.xlsx")
    try:
        df = pd.read_excel(file_path, dtype=str)
    except Exception as exc:
        raise ValueError(f"读取考场文件失败: {exc}") from exc

    room_col = _find_column(df.columns, ["教室编号", "考场号", "room", "classroom"])
    count_col = _find_column(df.columns, ["监考人数", "所需人数", "count", "proctor"])
    if room_col is None or count_col is None:
        raise ValueError("考场文件缺少必要列：教室编号/考场号、监考人数")

    result = []
    seen = set()
    for index, row in df.iterrows():
        room = "" if pd.isna(row[room_col]) else str(row[room_col]).strip()
        count_text = "" if pd.isna(row[count_col]) else str(row[count_col]).strip()
        if not room or room.lower() == "nan":
            raise ValueError(f"考场表第{index + 2}行考场编号为空")
        if room in seen:
            raise ValueError(f"考场编号重复: {room}")
        try:
            count_value = float(count_text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"考场 {room} 的监考人数必须是正整数") from exc
        if not count_value.is_integer() or count_value <= 0:
            raise ValueError(f"考场 {room} 的监考人数必须是正整数")
        seen.add(room)
        result.append((room, int(count_value)))
    if not result:
        raise ValueError("未找到有效考场信息")
    return result


if __name__ == "__main__":
    import sys
    print(get_classroom_info(sys.argv[1] if len(sys.argv) > 1 else "1.教室编号输入.xls"))
