"""教师监考时间轴：只读展示层，不参与排考计算。"""

from datetime import datetime, timedelta

from PyQt5.QtCore import Qt, QRect, pyqtSignal
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
                        "room": room,
                        "start": session["start"],
                        "end": session["end"],
                        "role": role,
                    })
    return tasks


class TimelineCanvas(QWidget):
    task_clicked = pyqtSignal(dict)
    task_context_menu = pyqtSignal(dict, object)

    def __init__(self, tasks, teachers, parent=None):
        super().__init__(parent)
        self.tasks = tasks
        self.teachers = teachers
        self.selected = None
        self.left_width = 145
        self.row_height = 70
        self.px_per_minute = 3
        self.setMinimumHeight(max(190, 62 + len(teachers) * self.row_height))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMouseTracking(True)

    def set_data(self, tasks, teachers):
        self.tasks = tasks
        self.teachers = teachers
        self.selected = None
        self.setMinimumHeight(max(190, 62 + len(teachers) * self.row_height))
        self.updateGeometry()
        self.update()

    def _time_range(self):
        values = [value for task in self.tasks for value in (task["start"], task["end"])]
        if not values:
            now = datetime.now().replace(minute=0, second=0, microsecond=0)
            return now, now.replace(hour=min(23, now.hour + 8))
        start = min(values).replace(minute=0, second=0, microsecond=0)
        end = max(values)
        end = end.replace(minute=0, second=0, microsecond=0)
        if end <= max(values):
            end = end.replace(hour=min(23, end.hour + 1))
        return start, end

    def _x(self, value, start):
        return self.left_width + int((value - start).total_seconds() / 60 * self.px_per_minute)

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
        width = max(self.width(), self.left_width + int((end - start).total_seconds() / 60 * self.px_per_minute) + 30)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        painter.setFont(QFont("Microsoft YaHei", 9))

        header_height = 48
        painter.setPen(QPen(QColor("#B8CDE2")))
        painter.fillRect(0, 0, width, header_height, QColor("#EAF2FB"))
        painter.drawText(QRect(12, 0, self.left_width - 20, header_height), Qt.AlignVCenter, "教师")
        total_minutes = max(60, int((end - start).total_seconds() / 60))
        for minute in range(0, total_minutes + 1, 60):
            x = self.left_width + minute * self.px_per_minute
            painter.setPen(QPen(QColor("#D8E4F0"), 1, Qt.DashLine))
            painter.drawLine(x, header_height, x, self.height())
            painter.setPen(QColor("#2463A6"))
            painter.drawText(x + 5, 0, 80, header_height, Qt.AlignVCenter, (start + timedelta(minutes=minute)).strftime("%H:%M"))

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
            rect = QRect(x, header_height + row * self.row_height + 10, max(80, right - x), 50)
            conflict = self._conflicts(task)
            color = QColor("#F9C74F") if task["role"] == "备选监考" else QColor("#8DB7F2")
            if conflict:
                color = QColor("#F58B8B")
            if task is self.selected:
                painter.setPen(QPen(QColor("#165DFF"), 3))
            else:
                painter.setPen(QPen(color.darker(115), 1))
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 6, 6)
            painter.setPen(QColor("#17324D"))
            painter.setFont(QFont("Microsoft YaHei", 8, QFont.Bold))
            painter.drawText(rect.adjusted(8, 4, -8, -25), Qt.TextWordWrap, f"{task['session_id']}  {task['room']}")
            suffix = "⚠ 冲突" if conflict else task["role"].replace("监考", "")
            painter.setFont(QFont("Microsoft YaHei", 8))
            painter.drawText(rect.adjusted(8, 25, -8, -4), Qt.AlignVCenter, suffix)

        if not self.tasks:
            painter.setPen(QColor("#74869A"))
            painter.setFont(QFont("Microsoft YaHei", 11))
            painter.drawText(QRect(self.left_width + 20, header_height + 24, 420, 40),
                             Qt.AlignVCenter, "暂无可显示的排考任务，请先完成排考或调整筛选条件")

        self.setMinimumWidth(width)
        painter.end()

    def mousePressEvent(self, event):
        start, _ = self._time_range()
        header_height = 48
        teacher_index = {teacher[0]: index for index, teacher in enumerate(self.teachers)}
        for task in self.tasks:
            row = teacher_index.get(task["teacher_id"])
            if row is None:
                continue
            rect = QRect(self._x(task["start"], start), header_height + row * self.row_height + 10,
                         max(80, self._x(task["end"], start) - self._x(task["start"], start)), 50)
            if rect.contains(event.pos()):
                if event.button() == Qt.RightButton:
                    if task["role"] == "正式监考":
                        self.task_context_menu.emit(task, event.globalPos())
                    return
                self.selected = task
                self.task_clicked.emit(task)
                self.update()
                return


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
        self.all_tasks = build_timeline_tasks(schedule_results)
        self.workload = workload
        self.teachers = teachers
        self.only_conflict.setChecked(False)
        self.only_formal.setChecked(False)
        self.session_combo.clear()
        self.session_combo.addItem("全部场次", "")
        for task in self.all_tasks:
            if self.session_combo.findData(task["session_id"]) < 0:
                self.session_combo.addItem(task["session_id"], task["session_id"])
        self.formal_value.setText(f"{sum(item.get('formal_count', 0) for item in workload.values())} 次")
        self.backup_value.setText(f"{sum(item.get('backup_count', 0) for item in workload.values())} 次")
        self.teacher_value.setText(f"{len(teachers)} 人")
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
        self.session_combo = QComboBox()
        self.session_combo.addItem("全部场次", "")
        for task in self.all_tasks:
            if self.session_combo.findData(task["session_id"]) < 0:
                self.session_combo.addItem(task["session_id"], task["session_id"])
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索教师姓名或工号")
        self.only_conflict = QCheckBox("只看冲突")
        self.only_formal = QCheckBox("只看正式监考")
        self.session_combo.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.only_conflict.toggled.connect(self.refresh)
        self.only_formal.toggled.connect(self.refresh)
        for widget in (self.session_combo, self.search, self.only_conflict, self.only_formal):
            filters.addWidget(widget)
        filters.addStretch(1)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh)
        filters.addWidget(refresh_button)
        root.addLayout(filters)

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
        scroll.setWidget(self.canvas)
        body.addWidget(scroll, 1)
        detail = QFrame()
        detail.setStyleSheet("QFrame { background: #FFFFFF; border: 1px solid #D8E4F0; border-radius: 8px; }")
        detail_layout = QVBoxLayout(detail)
        detail_layout.addWidget(QLabel("任务详情"))
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        detail_layout.addWidget(self.detail)
        body.addWidget(detail)
        root.addLayout(body, 1)

    def refresh(self):
        session_id = self.session_combo.currentData()
        keyword = self.search.text().strip().lower()
        tasks = [task for task in self.all_tasks
                 if (not session_id or task["session_id"] == session_id)
                 and (not keyword or keyword in task["teacher_name"].lower() or keyword in task["teacher_id"].lower())
                 and (not self.only_formal.isChecked() or task["role"] == "正式监考")]
        if self.only_conflict.isChecked():
            tasks = [task for task in tasks if self._has_conflict(task)]
        teacher_ids = {task["teacher_id"] for task in tasks}
        has_filter = bool(session_id or keyword or self.only_conflict.isChecked() or self.only_formal.isChecked())
        visible_teachers = [teacher for teacher in self.teachers if teacher[0] in teacher_ids]
        if not has_filter:
            visible_teachers = list(self.teachers)
        self.canvas.set_data(tasks, visible_teachers)
        conflicts = sum(self._has_conflict(task) for task in tasks)
        self.conflict_card.metric_value.setText(f"{conflicts} 个")

    def _has_conflict(self, task):
        return any(other is not task and other["teacher_id"] == task["teacher_id"]
                   and task["start"] < other["end"] and other["start"] < task["end"]
                   for other in self.all_tasks)

    def show_task(self, task):
        self.detail.setPlainText(
            f"教师：{task['teacher_name']}（{task['teacher_id']}）\n"
            f"部门：{task['department']}\n"
            f"考试场次：{task['session_id']}\n"
            f"考试时间：{task['period_text']}\n"
            f"考场：{task['room']}\n"
            f"角色：{task['role']}\n"
            f"状态：{'存在时间冲突' if self._has_conflict(task) else '无时间冲突'}"
        )


class TimelineDialog(QDialog):
    """保留旧入口，供外部调用；主界面使用 TimelinePanel 内嵌显示。"""
    def __init__(self, schedule_results, workload, teachers, parent=None):
        super().__init__(parent)
        self.setWindowTitle("教师监考时间轴")
        self.resize(1380, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(TimelinePanel(schedule_results, workload, teachers, self))
