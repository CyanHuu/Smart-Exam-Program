import math

import pandas as pd
import xlrd
import xlwt
from xlrd import open_workbook
from xlutils.copy import copy
from xlwt import Alignment


def split_excel_by_serial(input_path, output_path, header_row,sheet_count):
    """
        按照“序号”列将 Excel 表格拆分为多个 sheet，每个 sheet 包含连续序号段
        :param input_path: 输入 .xls 文件路径
        :param output_path: 输出 .xls 文件路径
        :param sheet_count: 要拆分成的 sheet 数量
        """
    # === 1. 读取原始数据 ===
    rb = xlrd.open_workbook(input_path, formatting_info=True)
    sheet_rb = rb.sheet_by_index(0)

    # 获取表头
    header_row = header_row
    headers = sheet_rb.row_values(header_row)

    # 查找“教室编号”和“监考人员”列索引
    room_col_idx = None
    staff_col_idx = None
    for idx, col_name in enumerate(headers):
        if '教室编号' in str(col_name) or 'Room' in str(col_name):
            room_col_idx = idx
        elif '监考人员' in str(col_name) or 'Staff' in str(col_name):
            staff_col_idx = idx

    if room_col_idx is None or staff_col_idx is None:
        raise ValueError("未找到 '教室编号' 或 '监考人员' 列，请检查表头")

    # 收集有效数据行（从第3行开始）
    data_rows = []
    for row_idx in range(header_row+1, sheet_rb.nrows):
        row = sheet_rb.row_values(row_idx)
        try:
            serial_val = row[0]  # 序号在第一列
            serial_num = int(serial_val.strip().lstrip('0') or '0')
        except (ValueError, TypeError):
            continue
        data_rows.append((serial_num, row))

    # 按序号排序
    data_rows.sort(key=lambda x: x[0])
    total = len(data_rows)

    if total == 0:
        raise ValueError("无有效数据")

    print(f"✅ 共读取 {total} 行有效数据，准备拆分为 {sheet_count} 个 sheet")

    # === 2. 计算每个 sheet 的大小 ===
    items_per_sheet = math.ceil(total / sheet_count)
    wb = xlwt.Workbook(encoding='utf-8')

    for i in range(sheet_count):
        start_idx = i * items_per_sheet
        end_idx = min(start_idx + items_per_sheet, total)
        chunk = data_rows[start_idx:end_idx]

        if not chunk:
            continue

        # 创建新 sheet
        sheet_name = f'Sheet{i + 1}'
        ws = wb.add_sheet(sheet_name)

        # 写入表头
        for col, value in enumerate(headers):
            ws.write(0, col, value)

        # === 3. 处理数据行，支持合并 ===
        prev_room = ""
        prev_staff = ""

        for row_offset, (serial_num, row_data) in enumerate(chunk):
            # 当前行
            current_room = str(row_data[room_col_idx]).strip()
            current_staff = str(row_data[staff_col_idx]).strip()

            # 判断是否为空
            is_room_empty = current_room == ""
            is_staff_empty = current_staff == ""

            # 合并逻辑：如果当前为空，就用上一个非空值
            if is_room_empty:
                current_room = prev_room
            else:
                prev_room = current_room

            if is_staff_empty:
                current_staff = prev_staff
            else:
                prev_staff = current_staff

            # 写入当前行（先写其他列）
            for col, cell_value in enumerate(row_data):
                if col == room_col_idx:
                    continue  # 暂不写，后面统一处理合并
                elif col == staff_col_idx:
                    continue
                ws.write(row_offset + 1, col, cell_value)

            # --- 合并“教室编号” ---
            if is_room_empty and row_offset > 0:
                # 合并当前行到上一行（连续空行会自动延续）
                # 但我们需要记录合并区间
                pass  # 先不写，稍后统一处理

            # --- 合并“监考人员” ---
            if is_staff_empty and row_offset > 0:
                pass

    # === 4. 统一处理合并（遍历所有行，构建合并区域）===
    # 预填充 room_map 和 staff_map
    room_map = {}
    staff_map = {}
    for row_offset, (_, row_data) in enumerate(chunk):
        current_room = str(row_data[room_col_idx]).strip()
        current_staff = str(row_data[staff_col_idx]).strip()

        if current_room != "":
            room_map[row_offset] = current_room
        else:
            # 如果当前为空，则继承上一个非空值
            for r in reversed(range(row_offset)):
                if r in room_map:
                    room_map[row_offset] = room_map[r]
                    break

        if current_staff != "":
            staff_map[row_offset] = current_staff
        else:
            for r in reversed(range(row_offset)):
                if r in staff_map:
                    staff_map[row_offset] = staff_map[r]
                    break

    # 构建合并区域
    merge_ranges = []

    current_room_start = 0
    current_staff_start = 0

    for row_offset in range(len(chunk)):
        # 教室编号合并
        if row_offset == 0 or room_map.get(row_offset) != room_map.get(row_offset - 1):
            if row_offset > 0 and current_room_start < row_offset - 1:
                merge_ranges.append(('room', current_room_start + 1, row_offset))
            current_room_start = row_offset

        # 监考人员合并
        if row_offset == 0 or staff_map.get(row_offset) != staff_map.get(row_offset - 1):
            if row_offset > 0 and current_staff_start < row_offset - 1:
                merge_ranges.append(('staff', current_staff_start + 1, row_offset))
            current_staff_start = row_offset

    # 最后一段
    if current_room_start < len(chunk) - 1:
        merge_ranges.append(('room', current_room_start + 1, len(chunk)))
    if current_staff_start < len(chunk) - 1:
        merge_ranges.append(('staff', current_staff_start + 1, len(chunk)))

    # 执行合并
    for merge_type, start_row, end_row in merge_ranges:
        value_to_merge = room_map[start_row - 1] if merge_type == 'room' else staff_map[start_row - 1]
        ws.write_merge(start_row, end_row, room_col_idx if merge_type == 'room' else staff_col_idx,
                       room_col_idx if merge_type == 'room' else staff_col_idx, value_to_merge)

    # === 5. 保存文件 ===
    wb.save(output_path)
    print(f"✅ 拆分并合并完成，已保存至: {output_path}")