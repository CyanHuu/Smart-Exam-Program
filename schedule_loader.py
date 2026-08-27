"""读取考试安排模板中的多场次信息。"""

from datetime import datetime
import re

import xlrd


def _text(value):
    return "" if value in (None, "") else str(value).strip()


def _merged_value(sheet, row_index, col_index):
    for rlo, rhi, clo, chi in sheet.merged_cells:
        if rlo <= row_index < rhi and clo <= col_index < chi:
            return _text(sheet.cell_value(rlo, clo))
    return _text(sheet.cell_value(row_index, col_index))


def _find_column(headers, keywords):
    for keyword in keywords:
        for index, header in enumerate(headers):
            if keyword in _text(header).replace(" ", "").lower():
                return index
    return None


def _parse_period(text, sheet_name):
    normalized = _text(text).replace("／", "/").replace("：", ":")
    date_match = re.search(r"(\d{4})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})", normalized)
    times = re.findall(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", normalized)
    if not date_match or len(times) < 2:
        raise ValueError(f"工作表 {sheet_name} 的考试日期或时间无法识别：{text}")
    year, month, day = map(int, date_match.groups())
    start_hour, start_minute = map(int, times[0])
    end_hour, end_minute = map(int, times[1])
    start = datetime(year, month, day, start_hour, start_minute)
    end = datetime(year, month, day, end_hour, end_minute)
    if end <= start:
        raise ValueError(f"工作表 {sheet_name} 的考试结束时间必须晚于开始时间")
    return start, end


def periods_overlap(left, right):
    return left["start"] < right["end"] and right["start"] < left["end"]


def load_exam_sessions(template_path, classroom_data, header_row=2):
    """读取模板全部工作表，返回统一的考试场次结构。"""
    workbook = xlrd.open_workbook(template_path, formatting_info=True)
    rooms_required = {str(room).strip(): int(count) for room, count in classroom_data}
    sessions = []
    for sheet_index in range(workbook.nsheets):
        sheet = workbook.sheet_by_index(sheet_index)
        if sheet.nrows <= header_row:
            raise ValueError(f"工作表 {sheet.name} 没有有效的考场数据")
        headers = sheet.row_values(header_row)
        room_col = _find_column(headers, ["教室编号", "考场号", "room"])
        candidate_col = _find_column(headers, ["考生人数", "人数", "candidate"])
        if room_col is None:
            raise ValueError(f"工作表 {sheet.name} 缺少教室编号列")
        title = _text(sheet.cell_value(0, 0))
        period_text = _text(sheet.cell_value(1, 0))
        start, end = _parse_period(period_text, sheet.name)
        rooms = []
        room_meta = {}
        seen = set()
        previous_room = ""
        for row_index in range(header_row + 1, sheet.nrows):
            room = _merged_value(sheet, row_index, room_col) or previous_room
            if room:
                previous_room = room
            if not room:
                continue
            if room in seen:
                continue
            if room not in rooms_required:
                raise ValueError(f"工作表 {sheet.name} 的教室 {room} 不在教室输入表中")
            seen.add(room)
            rooms.append((room, rooms_required[room]))
            room_meta[room] = {
                "candidate_count": _text(sheet.cell_value(row_index, candidate_col)) if candidate_col is not None else "",
            }
        if not rooms:
            raise ValueError(f"工作表 {sheet.name} 没有识别到有效教室")
        sessions.append({
            "session_id": str(sheet.name),
            "sheet_index": sheet_index,
            "title": title,
            "period_text": period_text,
            "start": start,
            "end": end,
            "rooms": rooms,
            "room_meta": room_meta,
        })
    sessions.sort(key=lambda session: session["start"])
    return sessions
