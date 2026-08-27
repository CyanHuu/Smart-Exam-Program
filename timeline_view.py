"""教师监考时间轴：只读展示层，不参与排考计算。"""

from datetime import datetime, timedelta
import re

from PyQt5.QtCore import Qt, QRect, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def build_timeline_tasks(schedule_results):
    """把现有排考结果转换为时间轴任务，保留正式/备选角色。"""
    tasks = []
    for result in schedule_results.values():
        session = result["session"]
        rooms = dict(session["rooms"])
        room_meta = session.get("room_meta", {})
        for role, source in (("正式监考", result.get("assignments", {})),
                             ("备选监考", result.get("backups", {}))):
            for room, teachers in source.items():
                for teacher in teachers:
                    tasks.append({
                        "teacher_id": teacher[0],
                        "teacher_name": teacher[1],
                        "department": teacher[4],
                        "session_id": session["session_id"],
                        "period_text": session.get("period_text", ""),
                        "subject": _clean_subject(_subject(session)),
                        "session_label": _session_label(session["start"]),
                        "proctor_need": rooms.get(room, 0),
                        "candidate_count": room_meta.get(room, {}).get("candidate_count", "未标注") or "未标注",
                        "exam_type": _exam_type(session),
                        "remark": "无",
                        "room": room,
                        "start": session["start"],
                        "end": session["end"],
                        "role": role,
                    })
    return tasks


def _subject(session):
    text = session.get("period_text", "")
    match = re.search(r"考试科目\s*[:：]?\s*(.*?)\s*考试时间", text)
    return match.group(1).strip() if match else session.get("title", "未标注") or "未标注"


def _exam_type(session):
    text = f"{session.get('title', '')} {session.get('period_text', '')}"
    for name in ("期末考试", "期中考试", "补考", "等级考试"):
        if name[:-2] in text:
            return name
    return "未标注"


def _clean_subject(value):
    return re.sub(r"^\s*\d{1,3}\s*[-_、.：:]?\s*", "", value).strip() or "未标注"


def _session_label(start):
    if start.hour < 12:
        return "上午场"
    if start.hour < 18:
        return "下午场"
    return "晚上场"


def _task_color(task):
    palette = ("#DCEBFF", "#E1F4E7", "#FBE8D5", "#EAE2FF", "#DDF3F3", "#FFF0C9")
    subject = task.get("subject", "未标注")
    return QColor(palette[sum(ord(char) for char in subject) % len(palette)])


class TimelineCanvas(QWidget):
    task_clicked = pyqtSignal(dict)
    task_context_menu = pyqtSignal(dict, object)
    teacher_clicked = pyqtSignal(str)

    def __init__(self, tasks, teachers, parent=None):
        super().__init__(parent)
        self.tasks = tasks
        self.teachers = teachers
        self.selected = None
        self.left_width = 145
        self.row_height = 78
        self.px_per_minute = 0.85
        self.date_column_width = 78
        self.lunch_column_width = 45
        self._sync_size()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMouseTracking(True)

    def set_data(self, tasks, teachers):
        self.tasks = tasks
        self.teachers = teachers
        self.selected = None
        self._sync_size()
        self.updateGeometry()
        self.update()

    def _required_size(self):
        start, end = self._time_range()
        day_count = (end.date() - start.date()).days + 1
        day_width = self.date_column_width + int(9 * 60 * self.px_per_minute) + self.lunch_column_width
        return QSize(
            int(max(680, self.left_width + day_count * day_width + 30)),
            max(190, 62 + len(self.teachers) * self.row_height),
        )

    def _sync_size(self):
        size = self._required_size()
        self.setMinimumSize(size)
        self.resize(size)

    def sizeHint(self):
        return self._required_size()

    def minimumSizeHint(self):
        return self._required_size()

    def _time_range(self):
        values = [value for task in self.tasks for value in (task["start"], task["end"])]
        if not values:
            today = datetime.now().date()
            return datetime(today.year, today.month, today.day, 8), datetime(today.year, today.month, today.day, 18)
        first = min(values).date()
        last = max(values).date()
        start = datetime(first.year, first.month, first.day, 8)
        end = datetime(last.year, last.month, last.day, 18)
        return start, end

    def _working_offset(self, value, origin_date):
        day_minutes = 9 * 60
        clock = value.hour * 60 + value.minute
        if clock <= 8 * 60:
            within_day = 0
        elif clock < 12 * 60:
            within_day = clock - 8 * 60
        elif clock <= 13 * 60:
            within_day = 4 * 60
        elif clock < 18 * 60:
            within_day = 4 * 60 + clock - 13 * 60
        else:
            within_day = day_minutes
        return (value.date() - origin_date).days * day_minutes + within_day

    def _x(self, value, start):
        day_width = self.date_column_width + int(9 * 60 * self.px_per_minute) + self.lunch_column_width
        day_index = (value.date() - start.date()).days
        clock = value.hour * 60 + value.minute
        if clock <= 8 * 60:
            within_day = 0
            break_width = 0
        elif clock < 12 * 60:
            within_day = clock - 8 * 60
            break_width = 0
        elif clock == 12 * 60:
            within_day = 4 * 60
            break_width = 0
        elif clock <= 13 * 60:
            within_day = 4 * 60
            break_width = self.lunch_column_width
        elif clock < 18 * 60:
            within_day = 4 * 60 + clock - 13 * 60
            break_width = self.lunch_column_width
        else:
            within_day = 9 * 60
            break_width = self.lunch_column_width
        return (self.left_width + day_index * day_width + self.date_column_width
                + int(within_day * self.px_per_minute) + break_width)

    def _conflicts(self, task):
        return any(
            other is not task
            and other["teacher_id"] == task["teacher_id"]
            and task["start"] < other["end"]
            and other["start"] < task["end"]
            for other in self.tasks
        )

    def paintEvent(self, _event):
        start, end = self._time_range()
        width = self.width()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        painter.setFont(QFont("Microsoft YaHei", 9))

        header_height = 58
        painter.setPen(QPen(QColor("#B8CDE2")))
        painter.fillRect(0, 0, width, header_height, QColor("#EAF2FB"))
        painter.drawText(QRect(12, 0, self.left_width - 20, header_height), Qt.AlignVCenter, "教师")
        day_count = (end.date() - start.date()).days + 1
        for day_index in range(day_count):
            day = start.date() + timedelta(days=day_index)
            day_start = datetime(day.year, day.month, day.day, 8)
            day_end = datetime(day.year, day.month, day.day, 18)
            day_x = self.left_width + day_index * (self.date_column_width + int(9 * 60 * self.px_per_minute) + self.lunch_column_width)
            time_x = day_x + self.date_column_width
            day_right = self._x(day_end, start)
            painter.fillRect(day_x, 0, self.date_column_width, self.height(), QColor("#F2F6FB"))
            painter.setPen(QColor("#163A63"))
            painter.setFont(QFont("Microsoft YaHei", 8, QFont.Bold))
            painter.drawText(QRect(day_x + 5, 0, self.date_column_width - 10, header_height), Qt.AlignCenter,
                             day.strftime("%Y-%m-%d"))
            lunch_x = time_x + int(4 * 60 * self.px_per_minute)
            painter.fillRect(lunch_x, 0, self.lunch_column_width, self.height(), QColor("#F3F5F7"))
            painter.setPen(QPen(QColor("#C7D2DF"), 1))
            painter.drawText(QRect(lunch_x, 0, self.lunch_column_width, header_height), Qt.AlignCenter, "午休")
            painter.setFont(QFont("Microsoft YaHei", 8))
            for hour in tuple(range(8, 12)) + tuple(range(13, 18)):
                current = datetime(day.year, day.month, day.day, hour)
                x = self._x(current, start)
                painter.setPen(QPen(QColor("#D8E4F0"), 1, Qt.DashLine))
                painter.drawLine(x, header_height, x, self.height())
                painter.setPen(QColor("#2463A6"))
                painter.drawText(QRect(x + 4, 24, 58, 28), Qt.AlignLeft | Qt.AlignVCenter, current.strftime("%H:%M"))
            painter.setPen(QPen(QColor("#B8CDE2"), 1))
            painter.drawLine(day_x, 0, day_x, self.height())
            painter.drawLine(time_x, 0, time_x, self.height())
            painter.drawLine(lunch_x, 0, lunch_x, self.height())
            painter.drawLine(lunch_x + self.lunch_column_width, 0, lunch_x + self.lunch_column_width, self.height())
            painter.drawLine(day_right, 0, day_right, self.height())

        # Keep the right edge visible for the last date.
        painter.setPen(QPen(QColor("#B8CDE2"), 1))
        painter.drawLine(self._x(end, start), 0, self._x(end, start), self.height())

        teacher_index = {teacher[0]: index for index, teacher in enumerate(self.teachers)}
        for index, teacher in enumerate(self.teachers):
            y = header_height + index * self.row_height
            painter.fillRect(0, y, width, self.row_height, QColor("#FAFCFF") if index % 2 else QColor("#FFFFFF"))
            painter.setPen(QPen(QColor("#D8E4F0")))
            painter.drawLine(0, y + self.row_height, width, y + self.row_height)
            painter.setPen(QColor("#25364D"))
            painter.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
            painter.drawText(QRect(12, y, self.left_width - 20, 28), Qt.AlignVCenter, teacher[1])
            painter.setFont(QFont("Microsoft YaHei", 8))
            painter.setPen(QColor("#74869A"))
            painter.drawText(QRect(12, y + 30, self.left_width - 20, 24), Qt.AlignVCenter, teacher[4])

        for task in self.tasks:
            row = teacher_index.get(task["teacher_id"])
            if row is None:
                continue
            x = self._x(task["start"], start)
            right = self._x(task["end"], start)
            rect = QRect(x, header_height + row * self.row_height + 7, max(80, right - x), 64)
            conflict = self._conflicts(task)
            color = _task_color(task)
            if task is self.selected:
                painter.setPen(QPen(QColor("#165DFF"), 3))
            elif conflict:
                painter.setPen(QPen(QColor("#E06B6B"), 2))
            else:
                painter.setPen(QPen(color.darker(115), 1))
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 6, 6)
            painter.setPen(QColor("#17324D"))
            painter.setFont(QFont("Microsoft YaHei", 8, QFont.Bold))
            painter.drawText(rect.adjusted(8, 3, -8, -43), Qt.AlignVCenter, f"{task['session_label']}  {task['room']}")
            painter.setFont(QFont("Microsoft YaHei", 8))
            painter.drawText(rect.adjusted(8, 21, -8, -25), Qt.AlignVCenter, task["subject"])
            suffix = "⚠ 时间冲突" if conflict else f"{task['start']:%H:%M}-{task['end']:%H:%M}"
            painter.drawText(rect.adjusted(8, 40, -8, -4), Qt.AlignVCenter, suffix)

        if not self.tasks:
            painter.setPen(QColor("#74869A"))
            painter.setFont(QFont("Microsoft YaHei", 11))
            painter.drawText(QRect(self.left_width + 20, header_height + 24, 420, 40),
                             Qt.AlignVCenter, "暂无可显示的排考任务，请先完成排考或调整筛选条件")

        painter.end()

    def mousePressEvent(self, event):
        start, _ = self._time_range()
        header_height = 58
        teacher_index = {teacher[0]: index for index, teacher in enumerate(self.teachers)}
        for task in self.tasks:
            row = teacher_index.get(task["teacher_id"])
            if row is None:
                continue
            rect = QRect(self._x(task["start"], start), header_height + row * self.row_height + 7,
                         max(80, self._x(task["end"], start) - self._x(task["start"], start)), 64)
            if rect.contains(event.pos()):
                if event.button() == Qt.RightButton:
                    if task["role"] == "正式监考":
                        self.task_context_menu.emit(task, event.globalPos())
                    return
                self.selected = task
                self.task_clicked.emit(task)
                self.update()
                return
        row = (event.pos().y() - header_height) // self.row_height
        if event.button() == Qt.LeftButton and event.pos().x() < self.left_width and 0 <= row < len(self.teachers):
            self.teacher_clicked.emit(self.teachers[row][0])


class TimelinePanel(QWidget):
    task_context_menu = pyqtSignal(dict, object)
    def __init__(self, schedule_results, workload, teachers, parent=None):
        super().__init__(parent)
        self.all_tasks = build_timeline_tasks(schedule_results)
        self.workload = workload
        self.teachers = teachers
        self._build_ui()
        self.refresh()

    def set_data(self, schedule_results, workload, teachers):
        selected_date = self.date_combo.currentData()
        self.all_tasks = build_timeline_tasks(schedule_results)
        self.workload = workload
        self.teachers = teachers
        self.only_conflict.setChecked(False)
        self.only_formal.setChecked(False)
        self.date_combo.blockSignals(True)
        self.date_combo.clear()
        self.date_combo.addItem("全部日期（总览）", "")
        dates = sorted({task["start"].date() for task in self.all_tasks})
        for value in dates:
            self.date_combo.addItem(value.strftime("%Y-%m-%d"), value.isoformat())
        self.date_combo.blockSignals(False)
        saved_index = self.date_combo.findData(selected_date)
        if saved_index >= 0:
            self.date_combo.setCurrentIndex(saved_index)
        self.refresh()

    def _card(self, title, value, color):
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background: {color}; border: 1px solid #D8E4F0; border-radius: 8px; }}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        label = QLabel(title)
        number = QLabel(str(value))
        number.setStyleSheet("font-size: 24pt; font-weight: bold; color: #2463A6;")
        card.metric_value = number
        layout.addWidget(label)
        layout.addWidget(number)
        return card

    def _build_ui(self):
        self.setStyleSheet("QDialog { background: #F5F7FB; } QLabel { color: #25364D; }")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        title = QLabel("教师监考时间轴")
        title.setStyleSheet("font-size: 20pt; font-weight: bold; color: #163A63;")
        root.addWidget(title)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("考试日期"))
        self.date_combo = QComboBox()
        self.date_combo.addItem("全部日期（总览）", "")
        dates = sorted({task["start"].date() for task in self.all_tasks})
        for value in dates:
            self.date_combo.addItem(value.strftime("%Y-%m-%d"), value.isoformat())
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索教师姓名或工号")
        self.only_conflict = QCheckBox("只看冲突")
        self.only_formal = QCheckBox("只看正式监考")
        self.date_combo.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.only_conflict.toggled.connect(self.refresh)
        self.only_formal.toggled.connect(self.refresh)
        for widget in (self.date_combo, self.search, self.only_conflict, self.only_formal):
            filters.addWidget(widget)
        filters.addStretch(1)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh)
        filters.addWidget(refresh_button)
        root.addLayout(filters)
        self.range_hint = QLabel()
        self.range_hint.setStyleSheet("color: #74869A;")
        root.addWidget(self.range_hint)

        cards = QHBoxLayout()
        total_formal = sum(item.get("formal_count", 0) for item in self.workload.values())
        total_backup = sum(item.get("backup_count", 0) for item in self.workload.values())
        formal_card = self._card("正式监考", f"{total_formal} 次", "#EEF5FF")
        backup_card = self._card("备选任务", f"{total_backup} 次", "#F3FBF5")
        teacher_card = self._card("教师人数", f"{len(self.teachers)} 人", "#F5F0FF")
        self.formal_value = formal_card.metric_value
        self.backup_value = backup_card.metric_value
        self.teacher_value = teacher_card.metric_value
        cards.addWidget(formal_card)
        cards.addWidget(backup_card)
        cards.addWidget(teacher_card)
        self.conflict_card = self._card("时间冲突", "0 个", "#FFF4F4")
        cards.addWidget(self.conflict_card)
        root.addLayout(cards)

        body = QHBoxLayout()
        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        self.canvas = TimelineCanvas([], self.teachers)
        self.canvas.task_clicked.connect(self.show_task)
        self.canvas.task_context_menu.connect(self.task_context_menu)
        self.canvas.teacher_clicked.connect(self.show_teacher_summary)
        scroll.setWidget(self.canvas)
        body.addWidget(scroll, 1)
        detail = QFrame()
        detail.setFixedWidth(260)
        detail.setStyleSheet("QFrame { background: #FFFFFF; border: 1px solid #D8E4F0; border-radius: 8px; }")
        detail_layout = QVBoxLayout(detail)
        detail_layout.addWidget(QLabel("任务详情"))
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        detail_layout.addWidget(self.detail)
        body.addWidget(detail)
        root.addLayout(body, 1)

    def refresh(self):
        date_key = self.date_combo.currentData()
        keyword = self.search.text().strip().lower()
        tasks = [task for task in self.all_tasks
                 if (not date_key or task["start"].date().isoformat() == date_key)
                 and (not keyword or keyword in task["teacher_name"].lower() or keyword in task["teacher_id"].lower())
                 and (not self.only_formal.isChecked() or task["role"] == "正式监考")]
        if self.only_conflict.isChecked():
            tasks = [task for task in tasks if self._has_conflict(task)]
        teacher_ids = {task["teacher_id"] for task in tasks}
        visible_teachers = [teacher for teacher in self.teachers if teacher[0] in teacher_ids]
        self.canvas.set_data(tasks, visible_teachers)
        conflicts = sum(self._has_conflict(task) for task in tasks)
        formal = sum(task["role"] == "正式监考" for task in tasks)
        backups = sum(task["role"] == "备选监考" for task in tasks)
        self.formal_value.setText(f"{formal} 次")
        self.backup_value.setText(f"{backups} 次")
        self.teacher_value.setText(f"{len(visible_teachers)} 人")
        self.conflict_card.metric_value.setText(f"{conflicts} 个")
        sessions = {task["session_id"] for task in tasks}
        if not date_key:
            dates = {task["start"].date() for task in tasks}
            self.range_hint.setText(f"总览包含 {len(dates)} 个考试日期、{len(sessions)} 个考试场次；可横向滚动查看完整时间表。")
        elif len(sessions) == 1:
            self.range_hint.setText("当前日期仅有一个考试时间段；时间轴仍用于查看教师任务和潜在冲突。")
        elif sessions:
            self.range_hint.setText(f"当前日期共 {len(sessions)} 个考试场次，横轴按当天最早至最晚考试时间展示。")
        else:
            self.range_hint.setText("当前日期没有符合筛选条件的教师任务。")

    def _has_conflict(self, task):
        return any(other is not task and other["teacher_id"] == task["teacher_id"]
                   and task["start"] < other["end"] and other["start"] < task["end"]
                   for other in self.all_tasks)

    def show_task(self, task):
        is_formal = task["role"] == "正式监考"
        badge = "#DCEBFF" if is_formal else "#FFF0C9"
        role_color = "#1558B0" if is_formal else "#A86500"
        rows = [
            ("教师", task["teacher_name"]),
            ("监考角色", task["role"]),
            ("考试场次", task["session_id"]),
            ("考试科目", task["subject"]),
            ("考场", task["room"]),
            ("时间", f"{task['start']:%H:%M} - {task['end']:%H:%M}（{(task['end'] - task['start']).total_seconds() / 3600:.1f} 小时）"),
            ("监考人数", f"{task['proctor_need']} 人"),
            ("考生人数", f"{task['candidate_count']} 人"),
            ("考试类型", task["exam_type"]),
            ("备注", task["remark"]),
        ]
        table_rows = "".join(
            f"<tr><td style='color:#74869A;padding:8px 6px;text-align:left'>{label}</td>"
            f"<td style='padding:8px 6px;text-align:right;color:#25364D'>{value}</td></tr>"
            for label, value in rows
        )
        self.detail.setHtml(
            f"<div style='padding:4px'><div style='background:{badge};color:{role_color};padding:9px 10px;border-radius:8px;font-size:13pt;font-weight:bold;border:1px solid {role_color}'>● {task['role']}</div>"
            f"<table width='100%' cellspacing='0' style='margin-top:10px'>{table_rows}</table></div>"
        )

    def show_teacher_summary(self, teacher_id):
        teacher = next((item for item in self.teachers if item[0] == teacher_id), None)
        name = teacher[1] if teacher else teacher_id
        self.detail.setHtml(
            f"<div style='padding:8px'><b style='color:#163A63'>已选监考教师</b>"
            f"<p style='font-size:12pt;color:#25364D'>{name}</p>"
            f"<p style='color:#74869A'>请点击左侧时间轴中的任务块，查看该任务的详细信息。</p></div>"
        )


class TimelineDialog(QDialog):
    """保留旧入口，供外部调用；主界面使用 TimelinePanel 内嵌显示。"""
    def __init__(self, schedule_results, workload, teachers, parent=None):
        super().__init__(parent)
        self.setWindowTitle("教师监考时间轴")
        self.resize(1380, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(TimelinePanel(schedule_results, workload, teachers, self))
