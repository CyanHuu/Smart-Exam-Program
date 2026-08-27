import sys
import os
import threading
from pathlib import Path
from io import StringIO

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox, QTableWidget,
    QTableWidgetItem, QTextEdit, QHeaderView, QSplitter, QComboBox,
    QInputDialog, QDialog, QSizePolicy, QDialogButtonBox, QLineEdit, QCheckBox,
    QFrame, QStackedWidget, QButtonGroup, QMenu
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QMutex
from PyQt5.QtGui import QBrush, QColor, QFont, QTextCursor, QTextOption, QTextCharFormat

# 导入核心逻辑
sys.path.append(str(Path(__file__).parent))
from core_logic import (
    assign_exam_sessions,
    build_workload_stats,
    preference_weights,
    rebuild_session_backups,
    _teacher_records,
)
from classroom_2 import get_classroom_info
from examiner import analyze_teacher_list
from schedule_loader import load_exam_sessions, periods_overlap
from outputTask import (
    get_schedule_rooms,
    split_schedule_by_room_groups,
    write_assignments_to_excel,
    write_session_assignments_to_excel,
    split_excel_by_room_groups,
)
from timeline_view import TimelinePanel


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
    def __init__(self, classroom_path, examiner_path, schedule_path, weights):
        self.classroom_path = classroom_path
        self.examiner_path = examiner_path
        self.schedule_path = schedule_path
        self.weights = weights
        self.signals = WorkerSignals()
        self.report = {}
        self.teacher_pool = []
        self.classroom_data = []
        self.schedule_results = {}
        self.workload = {}

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
            sessions = load_exam_sessions(self.schedule_path, classroom_data)
            self.schedule_results, self.workload = assign_exam_sessions(
                sessions, teacher_df, weights=self.weights, backup_count=2, random_seed=7
            )
            # 多场次排考不会自动调用旧版单场次报告，这里输出适合右侧窄面板的日志。
            print("\n" + "=" * 26)
            print("【多场次排考完成】")
            print("=" * 26)
            for session_id, result in self.schedule_results.items():
                session = result["session"]
                report = result["report"]
                print(f"\n【场次 {session_id}｜工作表 {session_id}】")
                print(f"时间：{session['period_text']}")
                print(f"考场：{report['total_rooms']} 个")
                print(f"需求：{report['total_needed']} 人")
                print(f"安排：{report['total_assigned']} 人")
                print(f"缺口：{report['shortage']} 人")
                print(
                    "规则：经验 "
                    f"{report['experience_first_count']}/{report['total_rooms']}，"
                    f"男女 {report['gender_mix_count']}/{report['total_rooms']}，"
                    f"部门 {report['department_mix_count']}/{report['total_rooms']}"
                )
                backup_total = sum(len(items) for items in result["backups"].values())
                print(f"备选：{backup_total} 人")
                for warning in report.get("warnings", []):
                    print(f"提醒：{warning}")
            print(f"\n【教师工作量】已统计 {len(self.workload)} 名教师")
            print("=" * 26 + "\n")
            self.signals.finished.emit(self.schedule_results, "")
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
        # 方案 A：明亮蓝白工作台主题，日志区域保留深色以增强对比。
        QApplication.instance().setStyleSheet("""
            QMainWindow, QWidget { background: #f5f7fb; color: #25364d; }
            QWidget#controlPanel, QWidget#logPanel {
                background: #ffffff; border: 1px solid #dbe4ef; border-radius: 10px;
            }
            QLabel { color: #25364d; }
            QLabel#pageTitle { color: #2463a6; font-size: 22pt; font-weight: bold; }
            QLabel#statusLabel { color: #24916e; font-weight: bold; }
            QPushButton {
                color: #ffffff; background: #2463a6; border: 1px solid #367fbf;
                border-radius: 7px; padding: 8px 14px; font-size: 11pt; font-weight: bold;
            }
            QPushButton:hover { background: #3478b8; }
            QPushButton:pressed { background: #1e568e; }
            QPushButton:disabled { color: #9aa7b5; background: #d6dde5; border-color: #c5ced8; }
            QComboBox, QLineEdit {
                color: #25364d; background: #ffffff; border: 1px solid #c8d8ea;
                border-radius: 5px; padding: 6px;
            }
            QComboBox QAbstractItemView { color: #25364d; background: #ffffff; selection-background-color: #dcecff; }
            QTableWidget {
                color: #25364d; background: #ffffff; alternate-background-color: #f3f7fb;
                gridline-color: #d3deea; border: 1px solid #c8d8ea; border-radius: 8px;
                selection-background-color: #dcecff; selection-color: #1d4f83;
            }
            QHeaderView::section {
                color: #294967; background: #eaf2fb; border: 0; border-right: 1px solid #c8d8ea;
                border-bottom: 1px solid #b8cde2; padding: 7px; font-weight: bold;
            }
            QSplitter::handle { background: #dbe4ef; }
            QScrollBar:vertical, QScrollBar:horizontal { background: #eef3f8; width: 12px; height: 12px; }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #b8cde2; border-radius: 5px; }
            QDialog { background: #f5f7fb; }
            QCheckBox { color: #25364d; spacing: 6px; }
        """)
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
        self.resize(1200, 800)
        self.setMinimumSize(980, 650)
        self.assignments = None
        self.schedule_results = {}
        self.workload = {}
        self.current_session_id = ""
        self.teacher_pool = []
        self.room_requirements = {}
        self.backup_assignments = {}
        self.table_row_records = []
        self.selected_room = ""
        self.selected_result_row = -1
        self.removed_proctors = {}
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        root_layout = QHBoxLayout(self.central_widget)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(10)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(180)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(8, 14, 8, 14)
        sidebar_title = QLabel("智能排考系统")
        sidebar_title.setStyleSheet("font-size: 16pt; font-weight: bold; color: #2463A6;")
        sidebar_layout.addWidget(sidebar_title)
        sidebar_layout.addSpacing(12)
        self.page_stack = QStackedWidget()
        menu_group = QButtonGroup(self)
        menu_group.setExclusive(True)
        menu_items = [("排考工作台", 0), ("排考结果", 1), ("教师管理", 2), ("数据导出", 3)]
        for label, index in menu_items:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setStyleSheet("QPushButton { text-align: left; padding: 10px; color: #25364D; background: transparent; border: 0; } QPushButton:checked { color: #2463A6; background: #EAF2FB; font-weight: bold; border-radius: 6px; }")
            button.clicked.connect(lambda _checked, i=index: self.page_stack.setCurrentIndex(i))
            menu_group.addButton(button, index)
            sidebar_layout.addWidget(button)
            if index == 0:
                button.setChecked(True)
        sidebar_layout.addStretch(1)
        root_layout.addWidget(sidebar)

        main_container = QWidget()
        main_layout = QVBoxLayout(main_container)
        main_layout.setContentsMargins(8, 6, 8, 6)
        main_layout.setSpacing(10)
        root_layout.addWidget(main_container, 1)

        page_title = QLabel("智能排考系统")
        page_title.setObjectName("pageTitle")
        page_title.setText("智能排考系统　/　排考总览")
        main_layout.addWidget(page_title)

        # 上部分：控制区
        control_widget = QWidget()
        control_widget.setObjectName("controlPanel")
        control_layout = QVBoxLayout(control_widget)
        control_layout.setContentsMargins(14, 12, 14, 12)
        control_layout.setSpacing(7)

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

        schedule_hbox = QHBoxLayout()
        self.btn_select_schedule = QPushButton("选择3.考试安排模板")
        self.btn_select_schedule.clicked.connect(self.select_schedule)
        self.label_schedule = QLabel("未选择")
        schedule_hbox.addWidget(self.btn_select_schedule)
        schedule_hbox.addWidget(self.label_schedule)

        self.btn_run = QPushButton("立即安排")
        self.btn_run.setStyleSheet(button_style)
        self.btn_run.clicked.connect(self.run_arrangement)
        self.btn_run.setEnabled(False)
        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #70d6ad; font-weight: bold;")

        control_layout.addLayout(room_hbox)
        control_layout.addLayout(teacher_hbox)
        control_layout.addLayout(schedule_hbox)

        preference_hbox = QHBoxLayout()
        preference_hbox.addWidget(QLabel("排考偏好"))
        self.preference_combo = QComboBox()
        preferences = [
            ("default", "默认（经验优先）"),
            ("experience", "经验优先"),
            ("gender", "男女搭配优先"),
            ("department", "部门均衡优先"),
            ("experience_only", "仅按经验排序"),
        ]
        for key, label in preferences:
            self.preference_combo.addItem(label, key)
        self.preference_combo.setMinimumHeight(34)
        self.preference_combo.setToolTip(
            "默认：经验 > 男女搭配 > 部门\n"
            "经验优先：经验 > 男女搭配 > 部门\n"
            "男女搭配优先：男女搭配 > 经验 > 部门\n"
            "部门均衡优先：部门 > 经验 > 男女搭配\n"
            "仅按经验排序：只优先安排有经验教师"
        )
        preference_hbox.addWidget(self.preference_combo, 1)
        control_layout.addLayout(preference_hbox)
        self.session_combo = QComboBox()
        self.session_combo.currentIndexChanged.connect(self.display_current_session)
        self.session_combo.setEnabled(False)
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
        self.result_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_table.customContextMenuRequested.connect(self.show_result_context_menu)
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
        log_font = QFont("Microsoft YaHei UI", 9)
        log_font.setStyleHint(QFont.SansSerif)
        self.log_text.setFont(log_font)
        self.log_text.setLineWrapMode(QTextEdit.WidgetWidth)
        self.log_text.setWordWrapMode(QTextOption.WrapAnywhere)
        self.log_text.setMinimumSize(330, 180)
        self.log_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.log_text.setStyleSheet("""
            background-color: #F7FAFD;
            color: #36506B;
            border: 1px solid #C7D9EB;
            border-radius: 6px;
            padding: 6px;
        """)
        log_panel = QWidget()
        log_panel.setObjectName("logPanel")
        log_panel_layout = QVBoxLayout(log_panel)
        log_panel_layout.setContentsMargins(10, 8, 10, 8)
        log_panel_layout.setSpacing(6)
        self.log_title = QLabel("运行日志")
        log_panel_layout.addWidget(self.log_title)
        log_panel_layout.addWidget(self.log_text)
        splitter.addWidget(log_panel)
        splitter.setSizes([780, 420])  # 左侧结果表，右侧运行日志
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

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

        # 保留原有操作方法的按钮对象，但不再把它们放在页面上；通过结果表右键菜单触发。
        self.btn_add_person = QPushButton("给选中考场增添人员")
        self.btn_delete_person = QPushButton("删除选中人员")
        self.btn_replace_person = QPushButton("更换选中人员")
        self.btn_add_person.clicked.connect(self.add_person_to_room)
        self.btn_delete_person.clicked.connect(self.delete_person_from_room)
        self.btn_replace_person.clicked.connect(self.replace_person_in_room)
        self.btn_add_person.setEnabled(False)
        self.btn_delete_person.setEnabled(False)
        self.btn_replace_person.setEnabled(False)
        self.btn_workload = QPushButton("查看教师工作量")
        self.btn_workload.setStyleSheet(button_style)
        self.btn_workload.clicked.connect(self.show_workload)
        self.btn_workload.setEnabled(False)
        self.selected_room_label = QLabel("请右键点击结果表格中的姓名进行调整")

        # 组装页面：工作台只保留输入和立即安排。
        workbench_page = QWidget()
        workbench_layout = QVBoxLayout(workbench_page)
        workbench_layout.addWidget(control_widget)
        workbench_layout.addStretch(1)

        result_page = QWidget()
        result_layout = QVBoxLayout(result_page)
        result_layout.addWidget(QLabel("排考结果"))
        result_switch = QHBoxLayout()
        self.btn_result_table = QPushButton("表格和运行日志")
        self.btn_result_timeline = QPushButton("教师时间轴")
        self.btn_result_table.clicked.connect(lambda: self.result_stack.setCurrentIndex(0))
        self.btn_result_timeline.clicked.connect(self.show_result_timeline)
        result_switch.addWidget(self.btn_result_table)
        result_switch.addWidget(self.btn_result_timeline)
        result_switch.addStretch(1)
        result_layout.addLayout(result_switch)
        self.result_stack = QStackedWidget()
        table_page = QWidget()
        table_layout = QVBoxLayout(table_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        session_hbox = QHBoxLayout()
        session_hbox.addWidget(QLabel("当前考试场次（对应模板工作表）"))
        session_hbox.addWidget(self.session_combo, 1)
        table_layout.addLayout(session_hbox)
        table_layout.addWidget(splitter, 1)
        self.result_stack.addWidget(table_page)
        self.timeline_panel = TimelinePanel({}, {}, [], self)
        self.timeline_panel.task_context_menu.connect(self.show_timeline_context_menu)
        self.result_stack.addWidget(self.timeline_panel)
        result_layout.addWidget(self.result_stack, 1)
        result_layout.addWidget(self.selected_room_label)
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("background: #FFF3B0; color: #795500; border: 1px solid #F0C36A; border-radius: 5px; padding: 7px;")
        self.warning_label.hide()
        result_layout.addWidget(self.warning_label)

        teacher_page = QWidget()
        teacher_layout = QVBoxLayout(teacher_page)
        teacher_layout.addWidget(QLabel("教师管理"))
        teacher_layout.addWidget(QLabel("查看教师正式监考、备选待命和总任务量。"))
        teacher_layout.addWidget(self.btn_workload)
        teacher_layout.addStretch(1)

        export_page = QWidget()
        export_page_layout = QVBoxLayout(export_page)
        export_page_layout.addWidget(QLabel("数据导出"))
        export_page_layout.addWidget(QLabel("导出完整排考结果，或按考场规则进行分组导出。"))
        export_page_layout.addLayout(export_layout)
        export_page_layout.addStretch(1)

        self.page_stack.addWidget(workbench_page)
        self.page_stack.addWidget(result_page)
        self.page_stack.addWidget(teacher_page)
        self.page_stack.addWidget(export_page)
        main_layout.addWidget(self.page_stack, 1)

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

    def select_schedule(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择考试安排模板", "", "Excel Files (*.xls *.xlsx)"
        )
        if path:
            self.schedule_path = path
            self.label_schedule.setText(os.path.basename(path))
            self.update_ready()

    def update_ready(self):
        ready = all(
            hasattr(self, name)
            for name in ("classroom_path", "examiner_path", "schedule_path")
        )
        self.btn_run.setEnabled(ready)

    def run_arrangement(self):
        self.log_text.clear()
        self.log_text.show()
        self.log_title.show()
        self.result_splitter.setSizes([780, 420])
        self.status_label.setText("正在安排中，请等待~")
        self.result_table.setRowCount(0)
        self.assignments = None
        self.schedule_results = {}
        self.workload = {}
        self.current_session_id = ""
        self.teacher_pool = []
        self.room_requirements = {}
        self.backup_assignments = {}
        self.table_row_records = []
        self.selected_room = ""
        self.selected_result_row = -1
        self.removed_proctors = {}
        self.btn_export.setEnabled(False)
        self.btn_split.setEnabled(False)
        self.btn_add_person.setEnabled(False)
        self.btn_delete_person.setEnabled(False)
        self.btn_replace_person.setEnabled(False)
        self.btn_workload.setEnabled(False)
        self.session_combo.clear()
        self.session_combo.setEnabled(False)
        QApplication.processEvents()

        weights = preference_weights(self.preference_combo.currentData())
        self.worker = ArrangementWorker(
            self.classroom_path, self.examiner_path, self.schedule_path, weights
        )
        self.worker.signals.finished.connect(self.on_finished)
        self.worker.signals.log_message.connect(self.append_log)
        thread = threading.Thread(target=self.worker.run)
        thread.daemon = True
        thread.start()

    def append_log(self, msg):
        # 缺口行使用黄色高亮，避免在大量日志中被忽略。
        for line in msg.splitlines(True):
            self.log_text.moveCursor(QTextCursor.End)
            format_ = QTextCharFormat()
            plain_line = line.replace(" ", "")
            if "缺口：" in plain_line and "缺口：0" not in plain_line:
                format_.setBackground(QColor("#FFD166"))
                format_.setForeground(QColor("#5A3B00"))
                format_.setFontWeight(QFont.Bold)
            elif "提醒：" in line or "⚠" in line:
                format_.setBackground(QColor("#FFF0C2"))
                format_.setForeground(QColor("#8A5A00"))
            self.log_text.textCursor().insertText(line, format_)
        self.log_text.ensureCursorVisible()

    def show_log_start(self):
        """排考结束后回到日志开头，先展示总体结果。"""
        self.log_text.moveCursor(QTextCursor.Start)
        self.log_text.verticalScrollBar().setValue(0)

    def on_finished(self, schedule_results, error):
        if error or schedule_results is None:
            self.log_text.show()
            self.log_title.show()
            self.result_splitter.setSizes([650, 550])
            self.show_log_start()
            QMessageBox.critical(self, "错误", f"安排失败：\n{error}")
            self.status_label.setText("❌ 安排失败")
            self.status_label.setStyleSheet("color: #ff7b72; font-weight: bold; font-size: 14pt;")
            return

        self.schedule_results = schedule_results
        self.workload = getattr(self.worker, "workload", {})
        self.teacher_pool = getattr(self.worker, "teacher_pool", [])
        self.timeline_panel.set_data(self.schedule_results, self.workload, self.teacher_pool)
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        for session_id, result in self.schedule_results.items():
            session = result["session"]
            label = f"工作表 {session_id}｜{session['period_text']}"
            self.session_combo.addItem(label, session_id)
        self.session_combo.blockSignals(False)
        self.session_combo.setEnabled(bool(self.schedule_results))
        self.report = self._combined_report()
        self.status_label.setText(
            f"✅ 排考完成：{self.report.get('total_sessions', 0)} 个场次，"
            f"{self.report.get('total_rooms', 0)} 个考场，"
            f"已安排 {self.report.get('total_assigned', 0)} 人，"
            f"缺口 {self.report.get('shortage', 0)} 人"
        )
        self.status_label.setStyleSheet("color: #70d6ad; font-weight: bold; font-size: 14pt;")
        if self.schedule_results:
            self.session_combo.setCurrentIndex(0)
            self.display_current_session()
        self.update_shortage_warning()
        # 成功后保留日志框，避免界面留下空白区域；结果表仍占主要空间。
        self.log_text.show()
        self.log_title.show()
        self.result_splitter.setSizes([780, 420])
        self.show_log_start()
        self.btn_export.setEnabled(True)
        self.btn_split.setEnabled(True)
        self.btn_workload.setEnabled(True)

    def show_result_timeline(self):
        if not self.schedule_results:
            self.warning_label.setText("⚠ 还没有排考结果，请先在排考工作台完成排考。")
            self.warning_label.show()
            return
        self.timeline_panel.set_data(self.schedule_results, self.workload, self.teacher_pool)
        self.update_shortage_warning()
        self.result_stack.setCurrentIndex(1)

    def update_shortage_warning(self):
        shortages = []
        for session_id, result in self.schedule_results.items():
            for item in result["report"].get("unfilled_rooms", []):
                shortages.append(
                    f"场次 {session_id} 的考场 {item['room']} 缺少 {item['missing']} 名监考教师"
                )
        if shortages:
            self.warning_label.setText("⚠ 人员缺口：" + "；".join(shortages))
            self.warning_label.show()
        else:
            self.warning_label.hide()

    def refresh_after_manual_change(self, detail, excluded_ids=None):
        """人工增删改后立即刷新全部场次、备选、日志和底部缺口提醒。"""
        self.refresh_backups(excluded_ids=excluded_ids)
        for session_id in self.schedule_results:
            self.refresh_session_report(session_id)
        self.display_current_session()
        self.update_shortage_warning()
        self.log_text.clear()
        self.append_log("【调整后最新总结果】以下为全部考试安排表\n")
        self.append_log(detail + "\n")
        for session_id in self.schedule_results:
            self.append_current_session_summary(session_id)
        report = self._combined_report()
        self.show_log_start()
        self.status_label.setText(
            f"✅ 已自动刷新：{report['total_sessions']} 个场次，"
            f"安排 {report['total_assigned']} 人，缺口 {report['shortage']} 人"
        )
        self.status_label.setStyleSheet("color: #70d6ad; font-weight: bold; font-size: 14pt;")

    def _combined_report(self):
        reports = [result["report"] for result in self.schedule_results.values()]
        return {
            "total_sessions": len(reports),
            "total_rooms": sum(report.get("total_rooms", 0) for report in reports),
            "total_assigned": sum(report.get("total_assigned", 0) for report in reports),
            "shortage": sum(report.get("shortage", 0) for report in reports),
        }

    def display_current_session(self):
        session_id = self.session_combo.currentData()
        if not session_id or session_id not in self.schedule_results:
            return
        self.current_session_id = session_id
        result = self.schedule_results[session_id]
        self.assignments = result["assignments"]
        self.backup_assignments = result["backups"]
        self.room_requirements = dict(result["session"]["rooms"])
        self.display_results_with_merge(self.assignments)

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

    def show_result_context_menu(self, position):
        """在结果表的教师姓名单元格上提供 Windows 风格右键操作菜单。"""
        item = self.result_table.itemAt(position)
        if item is None or item.column() != 1:
            return
        row = item.row()
        if row < 0 or row >= len(self.table_row_records):
            return
        room, teacher_index = self.table_row_records[row]
        if room not in self.assignments:
            return
        self.select_result_row(row, 1)
        self.show_adjustment_menu(
            self.result_table.viewport().mapToGlobal(position),
            has_teacher=teacher_index is not None,
        )

    def show_timeline_context_menu(self, task, global_pos):
        """把时间轴正式监考任务映射回结果表，再复用同一组调整操作。"""
        index = self.session_combo.findData(task["session_id"])
        if index >= 0:
            self.session_combo.setCurrentIndex(index)
        if not self.assignments:
            return
        for row, (room, teacher_index) in enumerate(self.table_row_records):
            if room != task["room"] or teacher_index is None:
                continue
            teacher = self.assignments[room][teacher_index]
            if teacher[0] == task["teacher_id"]:
                self.select_result_row(row, 1)
                self.show_adjustment_menu(global_pos)
                return

    def show_adjustment_menu(self, global_pos, has_teacher=True):
        """显示增删改右键菜单。"""
        menu = QMenu(self)
        menu.addAction("给选中考场增添人员", self.add_person_to_room)
        if has_teacher:
            menu.addAction("删除选中人员", self.delete_person_from_room)
            menu.addAction("更换选中人员", self.replace_person_in_room)
        menu.exec_(global_pos)

    def show_workload(self):
        if not self.workload:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("教师工作量统计")
        dialog.resize(1080, 620)
        layout = QVBoxLayout(dialog)
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("查找教师："))
        search_box = QLineEdit(dialog)
        search_box.setPlaceholderText("输入姓名、工号、场次或考场号")
        search_box.setClearButtonEnabled(True)
        filter_layout.addWidget(search_box, 1)
        show_assigned_only = QCheckBox("仅显示有任务教师", dialog)
        show_assigned_only.setChecked(True)
        show_assigned_only.setToolTip("取消勾选后显示所有教师，包括总任务量为 0 的教师")
        filter_layout.addWidget(show_assigned_only)
        layout.addLayout(filter_layout)
        summary_label = QLabel(dialog)
        layout.addWidget(summary_label)
        table = QTableWidget(dialog)
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels([
            "教师姓名", "工号", "正式监考", "备选待命", "总任务量", "任务明细（场次｜时间｜考场）"
        ])
        table.setAlternatingRowColors(True)
        table.setWordWrap(True)
        table.setTextElideMode(Qt.ElideNone)
        table.setStyleSheet(
            "QHeaderView::section { background: #4a79a5; color: white; "
            "font-weight: bold; padding: 6px; }"
        )
        rows = sorted(
            self.workload.values(),
            key=lambda item: (-item["total_count"], item["name"]),
        )
        header = table.horizontalHeader()
        for column in (0, 1, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        table.setColumnWidth(5, 620)

        def refresh_table():
            keyword = search_box.text().strip().lower()
            filtered = []
            for item in rows:
                if show_assigned_only.isChecked() and item["total_count"] == 0:
                    continue
                searchable = " ".join([
                    str(item["name"]), str(item["teacher_id"]),
                    *[
                        f"{task['session']} {task.get('room', '')} {task['role']}"
                        for task in item["sessions"]
                    ],
                ]).lower()
                if keyword and keyword not in searchable:
                    continue
                filtered.append(item)

            table.setRowCount(len(filtered))
            for row_index, item in enumerate(filtered):
                values = [
                    item["name"], item["teacher_id"], str(item["formal_count"]),
                    str(item["backup_count"]), str(item["total_count"]),
                ]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(value)
                    if column in (1, 2, 3, 4):
                        cell.setTextAlignment(Qt.AlignCenter)
                    table.setItem(row_index, column, cell)
                details = "\n".join(
                    f"{task['role'].replace('监考', '')}｜{task['session']}｜考场 {task.get('room', '未标明')}"
                    for task in item["sessions"]
                ) or "暂无任务"
                detail_cell = QTableWidgetItem(details)
                detail_cell.setToolTip(details)
                table.setItem(row_index, 5, detail_cell)
                table.setRowHeight(row_index, max(42, 24 * details.count("\n") + 18))
            hidden = len(rows) - len(filtered)
            hint = f"已隐藏 {hidden} 名无任务教师" if hidden else "当前没有无任务教师"
            summary_label.setText(
                f"显示 {len(filtered)} / {len(rows)} 名教师　｜　{hint}"
            )

        search_box.textChanged.connect(refresh_table)
        show_assigned_only.toggled.connect(lambda _checked: refresh_table())
        refresh_table()
        layout.addWidget(table)
        dialog.exec_()

    def refresh_backups(self, excluded_ids=None):
        if not self.current_session_id or not self.schedule_results:
            return
        for session_id in self.schedule_results:
            rebuild_session_backups(
                self.schedule_results,
                session_id,
                self.teacher_pool,
                preference_weights(self.preference_combo.currentData()),
                backup_count=2,
                excluded_ids=excluded_ids if session_id == self.current_session_id else None,
            )
        self.backup_assignments = self.schedule_results[self.current_session_id]["backups"]
        self.workload = build_workload_stats(self.schedule_results, self.teacher_pool)
        self.timeline_panel.set_data(self.schedule_results, self.workload, self.teacher_pool)

    def refresh_current_report(self):
        """人工修改后重新计算当前场次的概况。"""
        self.refresh_session_report(self.current_session_id)

    def refresh_session_report(self, session_id):
        """重新计算指定工作表的概况。"""
        result = self.schedule_results.get(session_id)
        if not result:
            return
        assignments = result["assignments"]
        # 界面层已将模板中的考场列表规范成 {考场号: 监考需求人数}。
        rooms = dict(result["session"]["rooms"])
        total_needed = sum(rooms.values())
        total_assigned = sum(len(items) for items in assignments.values())
        experience_first = sum(
            bool(items and items[0][2] == 1) for items in assignments.values()
        )
        gender_mix = sum(len({teacher[3] for teacher in items}) > 1 for items in assignments.values())
        department_mix = sum(len({teacher[4] for teacher in items}) > 1 for items in assignments.values())
        unfilled_rooms = []
        for room, required in rooms.items():
            missing = max(0, required - len(assignments.get(room, [])))
            if missing:
                unfilled_rooms.append({"room": room, "needed": required, "assigned": len(assignments.get(room, [])), "missing": missing})
        result["report"].update({
            "total_needed": total_needed,
            "total_assigned": total_assigned,
            "shortage": max(0, total_needed - total_assigned),
            "experience_first_count": experience_first,
            "gender_mix_count": gender_mix,
            "department_mix_count": department_mix,
            "unfilled_rooms": unfilled_rooms,
            "warnings": [f"考场 {item['room']} 缺少 {item['missing']} 名监考教师" for item in unfilled_rooms],
            "backup_total": sum(len(items) for items in result["backups"].values()),
        })
        self.report = self._combined_report()

    def append_current_session_summary(self, session_id=None):
        """把指定工作表的完整概况追加到运行日志。"""
        session_id = session_id or self.current_session_id
        result = self.schedule_results.get(session_id)
        if not result:
            return
        session = result["session"]
        report = result["report"]
        backup_total = sum(len(items) for items in result["backups"].values())
        self.append_log(
            f"\n【场次 {session_id}｜工作表 {session_id}】\n"
            f"时间：{session['period_text']}\n"
            f"考场：{report['total_rooms']} 个\n"
            f"需求：{report['total_needed']} 人\n"
            f"安排：{report['total_assigned']} 人\n"
            f"缺口：{report['shortage']} 人\n"
            f"规则：经验 {report['experience_first_count']}/{report['total_rooms']}，"
            f"男女 {report['gender_mix_count']}/{report['total_rooms']}，"
            f"部门 {report['department_mix_count']}/{report['total_rooms']}\n"
            f"备选：{backup_total} 人\n"
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
        current_session = self.schedule_results[self.current_session_id]["session"]
        current_formal_ids = {
            teacher[0]
            for room_teachers in self.assignments.values()
            for teacher in room_teachers
        }
        formal_locations = {}
        backup_locations = {}
        removed_key = (self.current_session_id, self.selected_room)
        removed_teachers = self.removed_proctors.get(removed_key, {})
        removed_ids = set(removed_teachers)
        for session_id, result in self.schedule_results.items():
            overlaps = session_id == self.current_session_id or periods_overlap(
                current_session, result["session"]
            )
            for other_room, room_teachers in result["assignments"].items():
                for teacher in room_teachers:
                    formal_locations.setdefault(teacher[0], []).append({
                        "session_id": session_id, "room": other_room, "overlap": overlaps
                    })
            for other_room, room_teachers in result["backups"].items():
                for teacher in room_teachers:
                    backup_locations.setdefault(teacher[0], []).append({
                        "session_id": session_id, "room": other_room, "overlap": overlaps
                    })

        candidates = []
        for teacher in self.teacher_pool:
            teacher_id = teacher[0]
            if teacher_id in current_formal_ids:
                continue
            formal_conflict = any(
                location["overlap"] for location in formal_locations.get(teacher_id, [])
            )
            if formal_conflict:
                continue
            backups = backup_locations.get(teacher_id, [])
            if any(
                item["session_id"] == self.current_session_id
                and item["room"] == self.selected_room
                for item in backups
            ):
                priority = 0  # 当前考场备选
                tag = "当前考场备选"
            elif teacher_id in removed_ids:
                priority = 1  # 曾从当前考场删除
                tag = "之前删除，可恢复原位"
            elif backups:
                priority = 2  # 其他考场备选
                tag = "其他考场备选"
            elif not formal_locations.get(teacher_id):
                priority = 3  # 完全空闲
                tag = "空闲教师"
            else:
                priority = 4  # 非重叠时段正式监考
                tag = "非重叠时段"
            candidates.append((priority, teacher[1], teacher, backups, tag))
        candidates.sort(key=lambda item: (item[0], item[1]))
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

        if not candidates:
            QMessageBox.information(
                self,
                "无法添加",
                "当前没有符合规则的教师可添加。\n"
                "请检查同一时间段的正式监考和备选安排。",
            )
            return
        choices = [
            f"[{item[4]}] {item[2][1]}（{item[2][0]}）｜{item[2][4]}"
            for item in candidates
        ]
        choice, ok = self.choose_teacher(
            "添加监考人员", f"选择要添加到 {self.selected_room} 的教师：", choices
        )
        if not ok:
            return
        selected_item = candidates[choices.index(choice)]
        selected = selected_item[2]
        # 被选中的备选教师从原备选位置移除，随后由 refresh_backups 补齐空出的名额。
        for location in selected_item[3]:
            source = self.schedule_results[location["session_id"]]["backups"]
            source[location["room"]] = [
                teacher for teacher in source[location["room"]]
                if teacher[0] != selected[0]
            ]
        if selected[0] in removed_teachers:
            del removed_teachers[selected[0]]
            if not removed_teachers:
                self.removed_proctors.pop(removed_key, None)
        changed_room = self.selected_room
        changed_session = self.current_session_id
        current.append(selected)
        self.refresh_after_manual_change(
            f"[手动修改] 场次 {changed_session}｜考场 {changed_room}\n"
            f"新增正式监考：{selected[1]}（{selected[0]}）\n"
            "备选人员已自动重新检查。"
        )

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
        current_session = self.schedule_results[self.current_session_id]["session"]
        locations = {}
        for session_id, result in self.schedule_results.items():
            session = result["session"]
            overlaps = session_id == self.current_session_id or periods_overlap(current_session, session)
            for other_room, room_teachers in result["assignments"].items():
                for teacher in room_teachers:
                    locations.setdefault(teacher[0], []).append({
                        "session_id": session_id,
                        "room": other_room,
                        "role": "正式监考",
                        "overlap": overlaps,
                    })
            for other_room, room_teachers in result["backups"].items():
                for teacher in room_teachers:
                    locations.setdefault(teacher[0], []).append({
                        "session_id": session_id,
                        "room": other_room,
                        "role": "备选监考",
                        "overlap": overlaps,
                    })
        candidates = [
            teacher for teacher in self.teacher_pool
            if teacher[0] != target[0]
            and not any(
                location["session_id"] == self.current_session_id
                and location["room"] == room
                for location in locations.get(teacher[0], [])
            )
        ]
        def candidate_priority(teacher):
            teacher_locations = locations.get(teacher[0], [])
            if not teacher_locations:
                return 0  # 完全空闲
            if not any(location["overlap"] for location in teacher_locations):
                return 1  # 仅在非重叠时段有任务
            return 2  # 存在时间冲突

        candidates.sort(key=lambda teacher: (candidate_priority(teacher), teacher[1]))
        if not candidates:
            QMessageBox.information(self, "无法更换", "没有可用的替换教师。")
            return
        choices = []
        for teacher in candidates:
            teacher_locations = locations.get(teacher[0], [])
            conflict = next((location for location in teacher_locations if location["overlap"]), None)
            other = teacher_locations[0] if teacher_locations else None
            if conflict:
                tag = f"{conflict['session_id']} {conflict['room']}｜{conflict['role']}（时间冲突）"
            elif other:
                tag = f"{other['session_id']} {other['room']}｜{other['role']}（非重叠时段）"
            else:
                tag = "空闲教师"
            choices.append(f"{teacher[1]}（{teacher[0]}）｜{tag}")
        choice, ok = self.choose_teacher(
            "更换监考人员", f"选择替换 {target[1]} 的教师：", choices
        )
        if not ok:
            return
        replacement = candidates[choices.index(choice)]
        changed_session = self.current_session_id
        changed_room = room
        old_name = target[1]
        replacement_locations = locations.get(replacement[0], [])
        conflict = next((location for location in replacement_locations if location["overlap"]), None)
        if conflict:
            answer = QMessageBox.question(
                self,
                "发现时间冲突",
                f"{replacement[1]} 已在 {conflict['session_id']} 的 {conflict['room']} 执行{conflict['role']}。\n"
                "如果继续更换，将从原安排中调出，原考场可能出现缺口。\n是否继续？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            source_result = self.schedule_results[conflict["session_id"]]
            source_items = source_result["assignments"] if conflict["role"] == "正式监考" else source_result["backups"]
            source_items[conflict["room"]] = [
                teacher for teacher in source_items[conflict["room"]]
                if teacher[0] != replacement[0]
            ]
        self.assignments[room][teacher_index] = replacement
        conflict_note = "（已处理原安排中的时间冲突）" if conflict else ""
        self.refresh_after_manual_change(
            f"[手动修改] 场次 {changed_session}｜考场 {changed_room}\n"
            f"更换监考：{old_name} → {replacement[1]}（{replacement[0]}）{conflict_note}\n"
            "备选人员已自动重新检查。"
        )

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
        changed_session = self.current_session_id
        changed_room = room
        deleted_name = teacher[1]
        del self.assignments[room][teacher_index]
        self.removed_proctors.setdefault((changed_session, changed_room), {})[teacher[0]] = teacher
        # 删除的教师保持空闲，不因备选刷新而自动流转到其他考场。
        self.refresh_after_manual_change(
            f"[手动修改] 场次 {changed_session}｜考场 {changed_room}\n"
            f"删除正式监考：{deleted_name}（{teacher[0]}）\n"
            "备选人员已自动重新检查。",
            excluded_ids={teacher[0]},
        )



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
        if not self.schedule_results and not self.assignments:
            return
        template_path = getattr(self, "schedule_path", "")
        if not template_path:
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
                if self.schedule_results:
                    write_session_assignments_to_excel(
                        self.schedule_results, template_path, header_row=2, output_path=path
                    )
                else:
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
            if self.schedule_results:
                groups = split_schedule_by_room_groups(
                    main_path, split_path, header_row=2, rules=rules
                )
            else:
                groups = split_excel_by_room_groups(
                    main_path, split_path, header_row=2, rules=rules
                )
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
