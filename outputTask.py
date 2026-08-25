import math
import pandas as pd
import xlrd
import xlwt
from xlutils.copy import copy
from xlwt import Alignment
from xlwt import Workbook


def write_assignments_to_excel(assignments, input_output_path,header_row):
    """
    将 assignments 写入 .xls 文件的每个 sheet 的 '监考人员' 列
    处理合并单元格
    """
    rb = xlrd.open_workbook(input_output_path, formatting_info=True)
    wb = copy(rb)

    for sheet_index in range(rb.nsheets):
        sheet_rb = rb.sheet_by_index(sheet_index)
        sheet_wb = wb.get_sheet(sheet_index)

        # 查找列
        header_row = header_row
        classroom_col_idx = None
        proctor_col_idx = None
        for idx, cell in enumerate(sheet_rb.row_values(header_row)):
            if '教室编号' in str(cell):
                classroom_col_idx = idx
            elif '监考人员' in str(cell):
                proctor_col_idx = idx

        if classroom_col_idx is None or proctor_col_idx is None:
            print(f"⚠️ 跳过 sheet {sheet_index}: 列未找到")
            continue

        # 收集所有有效教室
        classroom_rows = []  # [(row_idx, classroom_name), ...]
        for row_idx in range(header_row+1, sheet_rb.nrows):
            val = None
            in_merged = False
            # 检查是否在合并区域
            for (rlo, rhi, clo, chi) in sheet_rb.merged_cells:
                if clo <= classroom_col_idx < chi and rlo <= row_idx < rhi:
                    in_merged = True
                    if row_idx == rlo and classroom_col_idx == clo:
                        val = sheet_rb.cell_value(rlo, clo).strip()
                    break
            if not in_merged:
                # 普通单元格
                cell_val = sheet_rb.cell_value(row_idx, classroom_col_idx)
                val = cell_val.strip() if isinstance(cell_val, str) else str(cell_val).strip()
                if val == '':
                    val = None

            if val:
                classroom_rows.append((row_idx, val))

        print(f"✅ Sheet {sheet_index}: 检测到 {len(classroom_rows)} 个教室")


        # 写入监考人员
        for row_idx, classroom in classroom_rows:
            if classroom in assignments:
                proctors = assignments[classroom]
                proctor_names = [f"{t[1]}" for t in proctors]
                proctor_str = " ".join(proctor_names)
                sheet_wb.write(row_idx, proctor_col_idx, proctor_str)
            else:
                print(f"ℹ️ 未分配：{classroom} at row {row_idx}")

    wb.save(input_output_path)
    print(f"✅ 写入完成：{input_output_path}")


def split_excel_by_serial(input_path, output_path, header_row, sheet_count):
    rb = xlrd.open_workbook(input_path, formatting_info=True)
    sheet_rb = rb.sheet_by_index(0)

    header_row = header_row
    headers = sheet_rb.row_values(header_row)

    room_col_idx = None
    staff_col_idx = None
    for idx, col_name in enumerate(headers):
        if '教室编号' in str(col_name) or 'Room' in str(col_name):
            room_col_idx = idx
        elif '监考人员' in str(col_name) or 'Staff' in str(col_name):
            staff_col_idx = idx

    if room_col_idx is None or staff_col_idx is None:
        raise ValueError("未找到 '教室编号' 或 '监考人员' 列")

    # 收集有效数据
    data_rows = []
    for row_idx in range(header_row + 1, sheet_rb.nrows):
        row = sheet_rb.row_values(row_idx)
        try:
            serial_val = row[0]
            serial_num = int(serial_val.strip().lstrip('0') or '0')
        except (ValueError, TypeError):
            continue
        data_rows.append((serial_num, row))

    data_rows.sort(key=lambda x: x[0])
    total = len(data_rows)
    if total == 0:
        raise ValueError("无有效数据")

    print(f"✅ 共读取 {total} 行有效数据，准备拆分为 {sheet_count} 个 sheet")

    items_per_sheet = math.ceil(total / sheet_count)
    wb = xlwt.Workbook(encoding='utf-8')

    for i in range(sheet_count):
        start_idx = i * items_per_sheet
        end_idx = min(start_idx + items_per_sheet, total)
        chunk = data_rows[start_idx:end_idx]
        if not chunk:
            continue

        ws = wb.add_sheet(f'Sheet{i + 1}')
        # 写表头
        for col, value in enumerate(headers):
            ws.write(0, col, value)

        # Step 1: 预处理最终值（继承空值）
        final_rows = []
        prev_room = ""
        prev_staff = ""
        for _, original_row in chunk:
            row_copy = list(original_row)
            # 教室
            room_val = str(original_row[room_col_idx]).strip()
            if room_val == "":
                room_val = prev_room
            else:
                prev_room = room_val
            row_copy[room_col_idx] = room_val

            # 监考
            staff_val = str(original_row[staff_col_idx]).strip()
            if staff_val == "":
                staff_val = prev_staff
            else:
                prev_staff = staff_val
            row_copy[staff_col_idx] = staff_val

            final_rows.append(row_copy)

        # Step 2: 写入非合并列（跳过教室和监考）
        for row_offset, final_row in enumerate(final_rows):
            for col, cell_value in enumerate(final_row):
                if col == room_col_idx or col == staff_col_idx:
                    continue  # 跳过，后面用 merge 写
                ws.write(row_offset + 1, col, cell_value)

        # Step 3: 构建合并区间（包括单行）
        def get_all_segments(values):
            """返回所有连续段，包括单行"""
            if not values:
                return []
            segments = []
            start = 0
            for i in range(1, len(values)):
                if values[i] != values[i - 1]:
                    segments.append((start, i - 1))
                    start = i
            segments.append((start, len(values) - 1))
            return segments

        room_values = [row[room_col_idx] for row in final_rows]
        staff_values = [row[staff_col_idx] for row in final_rows]

        room_segments = get_all_segments(room_values)
        staff_segments = get_all_segments(staff_values)

        # Step 4: 用 write_merge 写所有教室和监考单元格（包括单行）
        for start, end in room_segments:
            ws.write_merge(start + 1, end + 1, room_col_idx, room_col_idx, room_values[start])
        for start, end in staff_segments:
            ws.write_merge(start + 1, end + 1, staff_col_idx, staff_col_idx, staff_values[start])

    wb.save(output_path)
    print(f"✅ 拆分并合并完成，已保存至: {output_path}")