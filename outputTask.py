import re
import re
import shutil

import xlrd
import xlwt
from xlutils.copy import copy


def _find_column(headers, keywords):
    # 关键词顺序代表业务优先级：先匹配实际教室编号，再匹配考试系统内部的考场号。
    for keyword in keywords:
        for index, header in enumerate(headers):
            text = str(header).strip().lower()
            if keyword in text:
                return index
    return None


def _assignment_text(teachers):
    return "，".join(
        f"{teacher[1]}（第一监考）" if index == 0 else str(teacher[1])
        for index, teacher in enumerate(teachers)
    )


def write_assignments_to_excel(assignments, input_output_path, header_row=2, output_path=None):
    """把安排写入模板。

    旧调用仍可传入同一个路径；GUI 可通过 output_path 指定新的保存路径。
    """
    target_path = output_path or input_output_path
    if output_path and output_path != input_output_path:
        shutil.copyfile(input_output_path, output_path)

    rb = xlrd.open_workbook(target_path, formatting_info=True)
    wb = copy(rb)
    for sheet_index in range(rb.nsheets):
        sheet_rb = rb.sheet_by_index(sheet_index)
        sheet_wb = wb.get_sheet(sheet_index)
        headers = sheet_rb.row_values(header_row)
        room_col = _find_column(headers, ["教室编号", "考场号", "room"])
        staff_col = _find_column(headers, ["监考人员", "监考教师", "staff", "proctor"])
        if room_col is None or staff_col is None:
            print(f"⚠️ 跳过 sheet {sheet_index}: 未找到考场或监考人员列")
            continue

        for row_index in range(header_row + 1, sheet_rb.nrows):
            room = _merged_or_cell_value(sheet_rb, row_index, room_col)
            if room and room in assignments:
                sheet_wb.write(row_index, staff_col, _assignment_text(assignments[room]))
    wb.save(target_path)
    print(f"[OK] 监考安排已写入: {target_path}")


def _merged_or_cell_value(sheet, row_index, col_index):
    for rlo, rhi, clo, chi in sheet.merged_cells:
        if rlo <= row_index < rhi and clo <= col_index < chi:
            return str(sheet.cell_value(rlo, clo)).strip() if row_index == rlo else ""
    value = sheet.cell_value(row_index, col_index)
    return str(value).strip() if value not in (None, "") else ""


def parse_room_groups(rules):
    """解析考场号分组：101-112；201,203,205；C101-C112。"""
    if isinstance(rules, str):
        parts = [part.strip() for part in re.split(r"[;；\n]+", rules) if part.strip()]
    else:
        parts = [str(part).strip() for part in rules if str(part).strip()]
    groups = []
    for part in parts:
        rooms = []
        for token in re.split(r"[,，、\s]+", part):
            if not token:
                continue
            match = re.fullmatch(r"([A-Za-z\u4e00-\u9fff]*)(\d+)\s*-\s*([A-Za-z\u4e00-\u9fff]*)(\d+)", token)
            if match:
                prefix1, start_text, prefix2, end_text = match.groups()
                if prefix1 != prefix2:
                    raise ValueError(f"考场号范围前缀不一致: {token}")
                start, end = int(start_text), int(end_text)
                if start > end or end - start > 10000:
                    raise ValueError(f"考场号范围无效: {token}")
                width = max(len(start_text), len(end_text))
                rooms.extend(f"{prefix1}{number:0{width}d}" for number in range(start, end + 1))
            else:
                rooms.append(token)
        if not rooms:
            raise ValueError(f"分组规则为空: {part}")
        groups.append(list(dict.fromkeys(rooms)))
    if not groups:
        raise ValueError("请至少输入一条考场分组规则")
    return groups


def _load_schedule(input_path, header_row):
    rb = xlrd.open_workbook(input_path, formatting_info=True)
    sheet = rb.sheet_by_index(0)
    headers = sheet.row_values(header_row)
    room_col = _find_column(headers, ["教室编号", "考场号", "room"])
    staff_col = _find_column(headers, ["监考人员", "监考教师", "staff", "proctor"])
    if room_col is None or staff_col is None:
        raise ValueError("模板中未找到教室编号/考场号或监考人员列")

    records = []
    previous_room = ""
    previous_staff = ""
    for row_index in range(header_row + 1, sheet.nrows):
        row = list(sheet.row_values(row_index))
        room = _merged_or_cell_value(sheet, row_index, room_col) or previous_room
        staff = _merged_or_cell_value(sheet, row_index, staff_col) or previous_staff
        if room:
            previous_room = room
        if staff:
            previous_staff = staff
        row[room_col] = room
        row[staff_col] = staff
        records.append(row)
    return rb, sheet, headers, room_col, staff_col, records


def _export_styles():
    """分组表使用与原模板一致的清晰打印样式。"""
    title = xlwt.easyxf(
        "font: name 微软雅黑, height 220, bold on;"
        "align: horiz center, vert center, wrap on"
    )
    subtitle = xlwt.easyxf(
        "font: name 微软雅黑, height 180;"
        "align: horiz left, vert center, wrap on"
    )
    header = xlwt.easyxf(
        "font: name 微软雅黑, height 180, bold on;"
        "align: horiz center, vert center, wrap on;"
        "borders: left thin, right thin, top thin, bottom thin;"
        "pattern: pattern solid, fore_colour ice_blue"
    )
    body = xlwt.easyxf(
        "font: name 微软雅黑, height 180;"
        "align: horiz center, vert center, wrap on;"
        "borders: left thin, right thin, top thin, bottom thin"
    )
    note = xlwt.easyxf(
        "font: name 微软雅黑, height 180, italic on, colour red;"
        "align: horiz left, vert center"
    )
    return title, subtitle, header, body, note


def _write_rows(ws, rows, header_row, room_col, staff_col, source_sheet=None):
    """写入分组表，同时保留模板的标题、表头、列宽、行高和合并结构。"""
    title, subtitle, header_style, body_style, _ = _export_styles()
    column_count = max(len(row) for row in rows) if rows else 1
    if source_sheet is not None:
        for col_index in range(column_count):
            info = source_sheet.colinfo_map.get(col_index)
            ws.col(col_index).width = info.width if info else 2200
        for row_index in range(len(rows)):
            info = source_sheet.rowinfo_map.get(row_index)
            if info and info.height:
                ws.row(row_index).height = info.height

    header_merges = []
    if source_sheet is not None:
        header_merges = [
            (rlo, rhi, clo, chi)
            for rlo, rhi, clo, chi in source_sheet.merged_cells
            if rhi <= header_row + 1
        ]

    for row_index, row in enumerate(rows):
        style = title if row_index == 0 else subtitle if row_index < header_row else header_style
        if row_index > header_row:
            style = body_style
        for col_index in range(column_count):
            if any(rlo <= row_index < rhi and clo <= col_index < chi for rlo, rhi, clo, chi in header_merges):
                continue
            if row_index > header_row and col_index in (room_col, staff_col):
                continue
            value = row[col_index] if col_index < len(row) else ""
            ws.write(row_index, col_index, value, style)

    # 标题及表头的合并单元格直接沿用原模板结构。
    if source_sheet is not None:
        for rlo, rhi, clo, chi in header_merges:
            value = rows[rlo][clo] if rlo < len(rows) and clo < len(rows[rlo]) else ""
            ws.write_merge(rlo, rhi - 1, clo, chi - 1, value, title if rlo == 0 else subtitle if rlo < header_row else header_style)

    data_start = header_row + 1
    for col_index in (room_col, staff_col):
        start = 0
        values = [str(row[col_index]) for row in rows[data_start:]]
        while start < len(values):
            end = start
            while end + 1 < len(values) and values[end + 1] == values[start]:
                end += 1
            first_row = data_start + start
            last_row = data_start + end
            if values[start]:
                if first_row == last_row:
                    ws.write(first_row, col_index, values[start], body_style)
                else:
                    ws.write_merge(first_row, last_row, col_index, col_index, values[start], body_style)
            start = end + 1


def split_excel_by_room_groups(input_path, output_path, header_row, rules):
    """按照考场号范围/列表生成分组 Sheet，并保留标题行和表头。"""
    groups = parse_room_groups(rules)
    _, source_sheet, headers, room_col, staff_col, records = _load_schedule(input_path, header_row)
    workbook = xlwt.Workbook(encoding="utf-8")
    for index, group in enumerate(groups, start=1):
        selected = [row for row in records if row[room_col] in set(group)]
        rows = [source_sheet.row_values(row_index) for row_index in range(header_row + 1)]
        rows.extend(selected)
        sheet_name = f"第{index}组"[:31]
        worksheet = workbook.add_sheet(sheet_name)
        _write_rows(worksheet, rows, header_row, room_col, staff_col, source_sheet)
        if not selected:
            worksheet.write(header_row + 1, 0, f"未找到匹配考场：{','.join(group)}", _export_styles()[-1])
    workbook.save(output_path)
    print(f"[OK] 已按考场号导出 {len(groups)} 组: {output_path}")


def split_excel_by_serial(input_path, output_path, header_row, sheet_count):
    """兼容旧入口：按原表中的考场顺序平均切组，但不再依赖序号列。"""
    if sheet_count < 1:
        raise ValueError("分组数量必须大于0")
    _, _, _, room_col, _, records = _load_schedule(input_path, header_row)
    rooms = list(dict.fromkeys(row[room_col] for row in records if row[room_col]))
    if not rooms:
        raise ValueError("模板中没有有效考场号")
    size = (len(rooms) + sheet_count - 1) // sheet_count
    groups = [rooms[start:start + size] for start in range(0, len(rooms), size)]
    return split_excel_by_room_groups(input_path, output_path, header_row, groups)
