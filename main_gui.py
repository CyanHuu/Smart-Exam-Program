import sys
import os
import threading
from pathlib import Path
from io import StringIO

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox, QTableWidget,
    QTableWidgetItem, QTextEdit, QHeaderView, QSplitter, QComboBox,
    QInputDialog, QDialog, QSizePolicy, QDialogButtonBox
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QMutex
from PyQt5.QtGui import QBrush, QColor, QFont, QTextCursor

# 导入核心逻辑
sys.path.append(str(Path(__file__).parent))
from core_logic import (
    assign_proctors,
    build_backup_assignments,
    preference_weights,
    _teacher_records,
)
from classroom_2 import get_classroom_info
from examiner import analyze_teacher_list
from outputTask import (
    get_schedule_rooms,
    write_assignments_to_excel,
    split_excel_by_room_groups,
)


# ====== 日志重定向器 ======
class LogCapture(StringIO):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def write(self, s):
        # 换行符也是日志内容，不能过滤，否则多段信息会粘在同一行。
        if s:
            super().write(s)
            self.callback(s)


# ====== 工作线程信号 ======
class WorkerSignals(QObject):
    finished = pyqtSignal(dict, str)
    log_message = pyqtSignal(str)


# ====== 后台工作类 ======
class ArrangementWorker:
    def __init__(self, classroom_path, examiner_path, weights):
        self.classroom_path = classroom_path
        self.examiner_path = examiner_path
        self.weights = weights
        self.signals = WorkerSignals()
        self.report = {}
        self.teacher_pool = []
        self.classroom_data = []

    def run(self):
        # 重定向 print 到日志
        old_stdout = sys.stdout
        log_capture = LogCapture(self.signals.log_message.emit)
        sys.stdout = log_capture
        try:
            # 捕获所有可能的异常，并确保错误信息进入日志
            classroom_data = get_classroom_info(self.classroom_path)
            teacher_df = analyze_teacher_list(self.examiner_path)
            self.classroom_data = classroom_data
            self.teacher_pool = _teacher_records(teacher_df)
            assignments, self.report = assign_proctors(
                classroom_data, teacher_df, weights=self.weights, return_report=True
            )
            self.backup_assignments = build_backup_assignments(
                classroom_data, self.teacher_pool, assignments, self.weights, backup_count=2
            )
            self.signals.finished.emit(assignments, "")
        except Exception as e:
            import traceback
            error_msg = str(e)
            tb_str = traceback.format_exc()

            # 先把错误打印到日志区（用户可见）
            self.signals.log_message.emit(f"\n❌ 发生错误：{error_msg}\n")
            self.signals.log_message.emit("详细堆栈信息：\n")
            self.signals.log_message.emit(tb_str)

            # 再通过 finished 信号通知主线程失败
            self.signals.finished.emit(None, error_msg)
        finally:
            sys.stdout = old_stdout


# ====== 主窗口 ======
class ProctorArrangerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        # 设置按钮通用样式（可复用）
        button_style = """
        QPushButton {
            font-size: 14pt; /* 字体调大 */
            font-weight: bold;
            color: white;
            background-color: #4a79a5; /* 背景色：柔和蓝色 */
            border: none;
            border-radius: 8px;
            padding: 10px 20px;
        }
        QPushButton:hover {
            background-color: #5a89b5; /* 悬停时稍亮 */
        }
        QPushButton:pressed {
            background-color: #3a6995; /* 按下时稍暗 */
        }
        QPushButton:disabled {
            background-color: #555555;
            color: #aaaaaa;
        }
        """
        self.setWindowTitle("浙水院监考安排系统for朱巍")
        self.resize(1200, 800)  # 更合理的初始大小
        self.assignments = None
        self.teacher_pool = []
        self.room_requirements = {}
        self.backup_assignments = {}
        self.table_row_records = []
        self.selected_room = ""
        self.selected_result_row = -1
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        main_layout = QVBoxLayout(self.central_widget)

        # 上部分：控制区
        control_widget = QWidget()
        control_layout = QVBoxLayout(control_widget)

        room_hbox = QHBoxLayout()
        self.btn_select_room = QPushButton("选择1.教室信息文件")
        self.btn_select_room.clicked.connect(self.select_classroom)
        self.label_room = QLabel("未选择")
        room_hbox.addWidget(self.btn_select_room)
        room_hbox.addWidget(self.label_room)

        teacher_hbox = QHBoxLayout()
        self.btn_select_teacher = QPushButton("选择2.监考老师文件")
        self.btn_select_teacher.clicked.connect(self.select_examiner)
        self.label_teacher = QLabel("未选择")
        teacher_hbox.addWidget(self.btn_select_teacher)
        teacher_hbox.addWidget(self.label_teacher)

        self.btn_run = QPushButton("立即安排")
        self.btn_run.setStyleSheet(button_style)
        self.btn_run.clicked.connect(self.run_arrangement)
        self.btn_run.setEnabled(False)
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: blue; font-weight: bold;")

        control_layout.addLayout(room_hbox)
        control_layout.addLayout(teacher_hbox)

        preference_hbox = QHBoxLayout()
        preference_hbox.addWidget(QLabel("排考偏好"))
        self.preference_combo = QComboBox()
        preferences = [
            ("default", "默认：经验 > 男女搭配 > 不同部门"),
            ("experience", "经验优先：经验 > 男女搭配 > 部门"),
            ("gender", "男女搭配优先：男女搭配 > 经验 > 部门"),
            ("department", "部门均衡优先：部门 > 经验 > 男女搭配"),
            ("experience_only", "只优先安排有经验教师"),
        ]
        for key, label in preferences:
            self.preference_combo.addItem(label, key)
        self.preference_combo.setToolTip("选择规则侧重点，系统会自动处理内部评分")
        preference_hbox.addWidget(self.preference_combo, 1)
        control_layout.addLayout(preference_hbox)
        control_layout.addWidget(self.btn_run)
        control_layout.addWidget(self.status_label)

        # 表格
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(8)
        self.result_table.setHorizontalHeaderLabels([
            "教室编号", "姓名", "工号", "部门", "性别", "经验", "角色", "备选监考"
        ])
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.result_table.cellClicked.connect(self.select_result_row)
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Interactive)  # 允许手动调整，但我们会设默认宽
        self.result_table.resizeColumnsToContents()

        # ====== 关键修改 1：使用 QSplitter 实现可拖动分隔 ======
        splitter = QSplitter(Qt.Horizontal)
        self.result_splitter = splitter
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        splitter.addWidget(self.result_table)

        # 日志区域
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Microsoft YaHei UI", 11))
        self.log_text.setLineWrapMode(QTextEdit.WidgetWidth)
        self.log_text.setMinimumWidth(280)
        self.log_text.setStyleSheet("""
            background-color: #252525;
            color: #e0e0e0;
            border: 1px solid #555;
            border-radius: 6px;
            padding: 8px;
        """)
        log_panel = QWidget()
        log_panel_layout = QVBoxLayout(log_panel)
        log_panel_layout.setContentsMargins(4, 0, 0, 0)
        self.log_title = QLabel("运行日志")
        log_panel_layout.addWidget(self.log_title)
        log_panel_layout.addWidget(self.log_text)
        splitter.addWidget(log_panel)
        splitter.setSizes([850, 350])  # 左侧结果表，右侧运行日志
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        # 导出按钮
        export_layout = QHBoxLayout()
        self.btn_export = QPushButton("数据导出")
        self.btn_export.clicked.connect(self.export_data)
        self.btn_export.setEnabled(False)
        self.btn_split = QPushButton("分组导出")
        self.btn_split.clicked.connect(self.split_export)
        self.btn_split.setEnabled(False)
        self.btn_export.setStyleSheet(button_style)
        self.btn_split.setStyleSheet(button_style)
        export_layout.addWidget(self.btn_export)
        export_layout.addWidget(self.btn_split)

        edit_layout = QHBoxLayout()
        self.btn_add_person = QPushButton("给选中考场增添人员")
        self.btn_delete_person = QPushButton("删除选中人员")
        self.btn_replace_person = QPushButton("更换选中人员")
        self.btn_add_person.setStyleSheet(button_style)
        self.btn_delete_person.setStyleSheet(button_style)
        self.btn_replace_person.setStyleSheet(button_style)
        self.btn_add_person.clicked.connect(self.add_person_to_room)
        self.btn_delete_person.clicked.connect(self.delete_person_from_room)
        self.btn_replace_person.clicked.connect(self.replace_person_in_room)
        self.btn_add_person.setEnabled(False)
        self.btn_delete_person.setEnabled(False)
        self.btn_replace_person.setEnabled(False)
        self.selected_room_label = QLabel("请先点击安排结果中的考场或教师")
        edit_layout.addWidget(self.btn_add_person)
        edit_layout.addWidget(self.btn_delete_person)
        edit_layout.addWidget(self.btn_replace_person)
        edit_layout.addWidget(self.selected_room_label, 1)

        # 组装布局
        main_layout.addWidget(control_widget)
        main_layout.addWidget(QLabel("安排结果："))
        main_layout.addWidget(splitter, 1)  # 中间区域填满上下剩余空间
        main_layout.addLayout(edit_layout)
        main_layout.addLayout(export_layout)

    def select_classroom(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择教室文件", "", "Excel Files (*.xls *.xlsx)")
        if path:
            self.classroom_path = path
            self.label_room.setText(os.path.basename(path))
            self.update_ready()

    def select_examiner(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择监考老师文件", "", "Excel Files (*.xls *.xlsx)")
        if path:
            self.examiner_path = path
            self.label_teacher.setText(os.path.basename(path))
            self.update_ready()

    def update_ready(self):
        ready = hasattr(self, 'classroom_path') and hasattr(self, 'examiner_path')
        self.btn_run.setEnabled(ready)

    def run_arrangement(self):
        self.log_text.clear()
        self.log_text.show()
        self.log_title.show()
        self.result_splitter.setSizes([850, 350])
        self.status_label.setText("正在安排中，请等待~")
        self.result_table.setRowCount(0)
        self.assignments = None
        self.teacher_pool = []
        self.room_requirements = {}
        self.backup_assignments = {}
        self.table_row_records = []
        self.selected_room = ""
        self.selected_result_row = -1
        self.btn_export.setEnabled(False)
        self.btn_split.setEnabled(False)
        self.btn_add_person.setEnabled(False)
        self.btn_delete_person.setEnabled(False)
        self.btn_replace_person.setEnabled(False)
        QApplication.processEvents()

        weights = preference_weights(self.preference_combo.currentData())
        self.worker = ArrangementWorker(self.classroom_path, self.examiner_path, weights)
        self.worker.signals.finished.connect(self.on_finished)
        self.worker.signals.log_message.connect(self.append_log)
        thread = threading.Thread(target=self.worker.run)
        thread.daemon = True
        thread.start()

    def append_log(self, msg):
        self.log_text.moveCursor(QTextCursor.End)
        self.log_text.insertPlainText(msg)
        self.log_text.ensureCursorVisible()

    def on_finished(self, assignments, error):
        if error or assignments is None:
            self.log_text.show()
            self.log_title.show()
            self.result_splitter.setSizes([650, 550])
            QMessageBox.critical(self, "错误", f"安排失败：\n{error}")
            self.status_label.setText("❌ 安排失败")
            self.status_label.setStyleSheet("color: #ff4444; font-weight: bold; font-size: 14pt;")
            return

        self.assignments = assignments
        self.report = getattr(self.worker, "report", {})
        self.teacher_pool = getattr(self.worker, "teacher_pool", [])
        self.room_requirements = dict(getattr(self.worker, "classroom_data", []))
        self.backup_assignments = getattr(self.worker, "backup_assignments", {})
        self.status_label.setText(
            f"✅ 排考完成：{self.report.get('total_rooms', 0)} 个考场，"
            f"已安排 {self.report.get('total_assigned', 0)} 人，"
            f"缺口 {self.report.get('shortage', 0)} 人"
        )
        self.status_label.setStyleSheet("color: #4a79a5; font-weight: bold; font-size: 14pt;")
        self.display_results_with_merge(assignments)
        # 成功后保留日志框，避免界面留下空白区域；结果表仍占主要空间。
        self.log_text.show()
        self.log_title.show()
        self.result_splitter.setSizes([850, 350])
        self.btn_export.setEnabled(True)
        self.btn_split.setEnabled(True)

    def select_result_row(self, row, _column):
        """点击任意结果行，选中该行所属考场。"""
        if row < 0 or row >= len(self.table_row_records):
            return
        room, teacher_index = self.table_row_records[row]
        self.selected_room = room
        self.selected_result_row = row
        self.selected_room_label.setText(
            f"当前考场：{room}（已安排 {len(self.assignments.get(room, []))} 人）"
        )
        self.btn_add_person.setEnabled(True)
        self.btn_delete_person.setEnabled(teacher_index is not None)
        self.btn_replace_person.setEnabled(teacher_index is not None)

    def refresh_backups(self):
        self.backup_assignments = build_backup_assignments(
            list(self.room_requirements.items()),
            self.teacher_pool,
            self.assignments,
            preference_weights(self.preference_combo.currentData()),
            backup_count=2,
        )

    def choose_teacher(self, title, prompt, choices):
        """使用较大的选择窗口，避免教师信息被省略号截断。"""
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(prompt)
        dialog.setComboBoxItems(choices)
        dialog.setComboBoxEditable(False)
        dialog.resize(760, 420)
        ok = dialog.exec_() == QDialog.Accepted
        return dialog.textValue(), ok

    def add_person_to_room(self):
        if not self.assignments or not self.selected_room:
            QMessageBox.information(self, "提示", "请先点击一个考场。")
            return
        assigned_ids = {
            teacher[0]
            for room_teachers in self.assignments.values()
            for teacher in room_teachers
        }
        available = [teacher for teacher in self.teacher_pool if teacher[0] not in assigned_ids]
        required = self.room_requirements.get(self.selected_room)
        current = self.assignments[self.selected_room]
        if required and len(current) >= required:
            answer = QMessageBox.question(
                self,
                "确认添加",
                f"{self.selected_room} 已满足 {required} 名监考需求，仍要继续添加吗？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        move_from_room = None
        if available:
            choices = [f"{teacher[1]}（{teacher[0]}）｜{teacher[4]}" for teacher in available]
            candidates = available
            dialog_title = "添加监考人员"
            prompt = f"选择要添加到 {self.selected_room} 的教师："
        else:
            # 没有空闲教师时允许调动，保证教师仍然不会出现在两个考场。
            movable = [
                (room, teacher)
                for room, room_teachers in self.assignments.items()
                if room != self.selected_room
                for teacher in room_teachers
            ]
            if not movable:
                QMessageBox.information(self, "无法添加", "没有可添加或调入的教师。")
                return
            move_from_room = {teacher[0]: room for room, teacher in movable}
            candidates = [teacher for _, teacher in movable]
            choices = [
                f"{teacher[1]}（{teacher[0]}）｜来源考场：{move_from_room[teacher[0]]}"
                for teacher in candidates
            ]
            dialog_title = "从其他考场调入教师"
            prompt = (
                f"当前没有空闲教师。选择调入 {self.selected_room} 的教师：\n"
                "调入后，原考场可能出现人员缺口。"
            )
        choice, ok = self.choose_teacher(dialog_title, prompt, choices)
        if not ok:
            return
        selected = candidates[choices.index(choice)]
        if move_from_room:
            source_room = move_from_room[selected[0]]
            self.assignments[source_room] = [
                teacher for teacher in self.assignments[source_room]
                if teacher[0] != selected[0]
            ]
        current.append(selected)
        self.refresh_backups()
        self.display_results_with_merge(self.assignments)
        if move_from_room:
            self.status_label.setText(f"✅ 已将 {selected[1]} 调入 {self.selected_room}")
        else:
            self.status_label.setText(f"✅ 已向 {self.selected_room} 添加 1 名监考教师")
        self.status_label.setStyleSheet("color: #4a79a5; font-weight: bold; font-size: 14pt;")

    def replace_person_in_room(self):
        if not self.assignments or not self.selected_room:
            return
        if self.selected_result_row < 0 or self.selected_result_row >= len(self.table_row_records):
            return
        room, teacher_index = self.table_row_records[self.selected_result_row]
        if room != self.selected_room or teacher_index is None:
            QMessageBox.information(self, "提示", "请点击要更换的教师所在行。")
            return

        target = self.assignments[room][teacher_index]
        assigned_rooms = {
            teacher[0]: other_room
            for other_room, room_teachers in self.assignments.items()
            for teacher in room_teachers
        }
        candidates = [
            teacher for teacher in self.teacher_pool
            if teacher[0] != target[0] and assigned_rooms.get(teacher[0]) != room
        ]
        if not candidates:
            QMessageBox.information(self, "无法更换", "没有可用的替换教师。")
            return
        choices = []
        for teacher in candidates:
            source = assigned_rooms.get(teacher[0])
            tag = f"当前安排：{source}（存在时间冲突）" if source else "空闲/备选教师"
            choices.append(f"{teacher[1]}（{teacher[0]}）｜{tag}")
        choice, ok = self.choose_teacher(
            "更换监考人员", f"选择替换 {target[1]} 的教师：", choices
        )
        if not ok:
            return
        replacement = candidates[choices.index(choice)]
        source_room = assigned_rooms.get(replacement[0])
        if source_room:
            answer = QMessageBox.question(
                self,
                "发现时间冲突",
                f"{replacement[1]} 已安排在 {source_room}。\n"
                f"如果继续更换，将从 {source_room} 调出，原考场可能出现缺口。\n是否继续？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            self.assignments[source_room] = [
                teacher for teacher in self.assignments[source_room]
                if teacher[0] != replacement[0]
            ]
        self.assignments[room][teacher_index] = replacement
        self.refresh_backups()
        self.display_results_with_merge(self.assignments)
        self.status_label.setText(f"✅ 已将 {target[1]} 更换为 {replacement[1]}")
        self.status_label.setStyleSheet("color: #4a79a5; font-weight: bold; font-size: 14pt;")

    def delete_person_from_room(self):
        if not self.assignments or not self.selected_room:
            return
        if self.selected_result_row < 0 or self.selected_result_row >= len(self.table_row_records):
            return
        room, teacher_index = self.table_row_records[self.selected_result_row]
        if room != self.selected_room or teacher_index is None:
            QMessageBox.information(self, "提示", "请点击要删除的教师所在行。")
            return
        teacher = self.assignments[room][teacher_index]
        answer = QMessageBox.question(
            self,
            "确认删除",
            f"确定从 {room} 删除 {teacher[1]}（{teacher[0]}）吗？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        del self.assignments[room][teacher_index]
        self.refresh_backups()
        self.display_results_with_merge(self.assignments)
        self.status_label.setText(f"✅ 已从 {room} 删除 1 名监考教师")
        self.status_label.setStyleSheet("color: #4a79a5; font-weight: bold; font-size: 14pt;")



    def display_results_with_merge(self, assignments):
        self.result_table.setRowCount(0)
        self.table_row_records = []
        self.selected_room = ""
        self.selected_result_row = -1
        if hasattr(self, "btn_add_person"):
            self.btn_add_person.setEnabled(False)
            self.btn_delete_person.setEnabled(False)
            self.btn_replace_person.setEnabled(False)
            self.selected_room_label.setText("请先点击安排结果中的考场或教师")
        row = 0
        room_start_row = {}
        missing_brush = QBrush(QColor("#FFF2A8"))
        missing_text_brush = QBrush(QColor("#8A4B00"))

        # 填充教师行；人数不足时额外增加一行黄色提醒。
        for room, teachers in assignments.items():
            start_row = row
            for t in teachers:
                id_, name, exp, gender, dept = t
                self.result_table.insertRow(row)
                # 考场列先留空，后面合并
                self.result_table.setItem(row, 0, QTableWidgetItem(""))
                self.result_table.setItem(row, 1, QTableWidgetItem(name))
                self.result_table.setItem(row, 2, QTableWidgetItem(id_))
                self.result_table.setItem(row, 3, QTableWidgetItem(dept))
                gender_str = "女" if gender == 0 else "男"
                self.result_table.setItem(row, 4, QTableWidgetItem(gender_str))
                exp_str = "有经验" if exp == 1 else "无经验"
                self.result_table.setItem(row, 5, QTableWidgetItem(exp_str))
                self.result_table.setItem(row, 6, QTableWidgetItem("第一监考" if row == start_row else "监考人员"))
                self.table_row_records.append((room, row - start_row))
                row += 1

            required = self.room_requirements.get(room, len(teachers))
            missing = max(0, required - len(teachers))
            if missing:
                self.result_table.insertRow(row)
                self.result_table.setItem(row, 0, QTableWidgetItem(""))
                self.result_table.setItem(row, 1, QTableWidgetItem(f"缺少 {missing} 名监考教师"))
                self.result_table.setItem(row, 6, QTableWidgetItem("待补充"))
                for column in range(8):
                    item = self.result_table.item(row, column)
                    if item is None:
                        item = QTableWidgetItem("")
                        self.result_table.setItem(row, column, item)
                    item.setBackground(missing_brush)
                    item.setForeground(missing_text_brush)
                self.table_row_records.append((room, None))
                row += 1

            # 需求人数为空或异常时也保留一个可点击的考场行。
            if row == start_row:
                self.result_table.insertRow(row)
                self.result_table.setItem(row, 0, QTableWidgetItem(""))
                self.result_table.setItem(row, 1, QTableWidgetItem("暂无监考人员"))
                self.result_table.setItem(row, 6, QTableWidgetItem("待补充"))
                for column in range(8):
                    item = self.result_table.item(row, column)
                    if item is None:
                        item = QTableWidgetItem("")
                        self.result_table.setItem(row, column, item)
                    item.setBackground(missing_brush)
                    item.setForeground(missing_text_brush)
                self.table_row_records.append((room, None))
                row += 1
            room_start_row[room] = start_row

        # 第三遍：合并考场单元格
        for room, teachers in assignments.items():
            count = len(teachers)
            start_row = room_start_row[room]
            self.result_table.setItem(start_row, 0, QTableWidgetItem(room))
            end_row = start_row
            while end_row + 1 < len(self.table_row_records) and self.table_row_records[end_row + 1][0] == room:
                end_row += 1
            if end_row > start_row:
                self.result_table.setSpan(start_row, 0, end_row - start_row + 1, 1)
            backup_names = self.backup_assignments.get(room, [])
            backup_text = "、".join(teacher[1] for teacher in backup_names) or "暂无"
            self.result_table.setItem(
                start_row, 7, QTableWidgetItem(backup_text)
            )
            if end_row > start_row:
                self.result_table.setSpan(start_row, 7, end_row - start_row + 1, 1)

        # 让所有列根据内容自动调整宽度
        self.result_table.resizeColumnsToContents()
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.result_table.scrollToTop()



    def export_data(self):
        if not self.assignments:
            return
        template_path, _ = QFileDialog.getOpenFileName(
            self, "选择监考安排模板", "", "Excel 97-2003 (*.xls)"
        )
        if not template_path:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存监考安排表", "", "Excel 97-2003 (*.xls)")
        if path:
            if not path.lower().endswith(".xls"):
                path += ".xls"
            try:
                write_assignments_to_excel(
                    self.assignments, template_path, header_row=2, output_path=path
                )
                self.last_export_path = path
                QMessageBox.information(self, "成功", f"数据已导出至：\n{path}")
            except Exception as e:
                self.append_log(f"导出失败: {e}\n")
                QMessageBox.critical(self, "导出失败", str(e))

    def split_export(self):
        main_path = getattr(self, "last_export_path", "")
        if not main_path:
            main_path, _ = QFileDialog.getOpenFileName(
                self, "选择已导出的监考安排主表", "", "Excel 97-2003 (*.xls)"
            )
        if not main_path:
            return

        # 2. 选择分组表的保存路径
        split_path, _ = QFileDialog.getSaveFileName(
            self, "保存分组监考表", "", "Excel 97-2003 (*.xls)"
        )
        if not split_path:
            return
        if not split_path.endswith(".xls"):
            split_path += ".xls"

        # 3. 选择自动识别或手动指定分组范围。
        rooms = get_schedule_rooms(main_path, header_row=2)
        if not rooms:
            QMessageBox.information(self, "无法分组", "排考表中没有识别到有效教室编号。")
            return
        auto_rules = ",".join(rooms)
        dialog = QDialog(self)
        dialog.setWindowTitle("设置分组方式")
        dialog.resize(560, 360)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.addWidget(QLabel("选择分组方式："))
        mode_combo = QComboBox()
        mode_combo.addItem("自动识别表格中的全部教室（1组）", "auto")
        mode_combo.addItem("手动指定范围或考场（多行多组）", "manual")
        dialog_layout.addWidget(mode_combo)
        dialog_layout.addWidget(QLabel(
            "自动模式会读取当前排考表中的全部教室。\n"
            "手动模式示例：101-112 或 101,103,105，每行一组。"
        ))
        rules_edit = QTextEdit()
        rules_edit.setPlainText(auto_rules)
        rules_edit.setReadOnly(True)
        dialog_layout.addWidget(rules_edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog_layout.addWidget(buttons)

        def update_group_mode(index):
            is_manual = mode_combo.itemData(index) == "manual"
            rules_edit.setReadOnly(not is_manual)
            if is_manual and rules_edit.toPlainText() == auto_rules:
                rules_edit.clear()
                rules_edit.setPlaceholderText("例如：101-112\n或：101,103,105")
            elif not is_manual:
                rules_edit.setPlainText(auto_rules)
                rules_edit.setPlaceholderText("")

        mode_combo.currentIndexChanged.connect(update_group_mode)
        ok = dialog.exec_() == QDialog.Accepted
        rules = rules_edit.toPlainText()
        if not ok:
            return

        # 4. 执行分组
        try:
            groups = split_excel_by_room_groups(main_path, split_path, header_row=2, rules=rules)
            QMessageBox.information(self, "成功", f"分组表已导出至：\n{split_path}")
            self.append_log("\n" + f"[分组导出] 已按考场号生成分组：{split_path}\n")
        except Exception as e:
            error_msg = f"分组导出失败: {str(e)}"
            self.append_log("\n" +f"[错误] {error_msg}\n")
            QMessageBox.critical(self, "分组导出失败", error_msg)


# === 启动 ===
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ProctorArrangerApp()
    window.show()
    sys.exit(app.exec_())
