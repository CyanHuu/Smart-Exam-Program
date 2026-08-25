import sys
import os
import threading
from pathlib import Path
from io import StringIO

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox, QTableWidget,
    QTableWidgetItem, QTextEdit, QHeaderView, QSplitter, QComboBox,
    QInputDialog
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QMutex
from PyQt5.QtGui import QFont

# 导入核心逻辑
sys.path.append(str(Path(__file__).parent))
from core_logic import assign_proctors, preference_weights
from classroom_2 import get_classroom_info
from examiner import analyze_teacher_list
from outputTask import write_assignments_to_excel, split_excel_by_room_groups


# ====== 日志重定向器 ======
class LogCapture(StringIO):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def write(self, s):
        if s.strip():  # 忽略纯空白
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

    def run(self):
        # 重定向 print 到日志
        old_stdout = sys.stdout
        log_capture = LogCapture(self.signals.log_message.emit)
        sys.stdout = log_capture
        try:
            # 捕获所有可能的异常，并确保错误信息进入日志
            classroom_data = get_classroom_info(self.classroom_path)
            teacher_df = analyze_teacher_list(self.examiner_path)
            assignments, self.report = assign_proctors(
                classroom_data, teacher_df, weights=self.weights, return_report=True
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
        self.result_table.setColumnCount(7)
        self.result_table.setHorizontalHeaderLabels([
            "教室编号", "姓名", "工号", "部门", "性别", "经验", "角色"
        ])
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Interactive)  # 允许手动调整，但我们会设默认宽
        self.result_table.resizeColumnsToContents()

        # ====== 关键修改 1：使用 QSplitter 实现可拖动分隔 ======
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.result_table)

        # 日志区域
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Courier", 12))
        self.log_text.setMaximumHeight(200)  # 初始最大高，可被 splitter 覆盖
        self.log_text.setStyleSheet("""
            background-color: #252525;
            color: #e0e0e0;
            border: 1px solid #555;
            border-radius: 6px;
            padding: 8px;
        """)
        splitter.addWidget(self.log_text)
        splitter.setSizes([600, 200])  # 初始比例：表格 600px，日志 200px

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

        # 组装布局
        main_layout.addWidget(control_widget)
        main_layout.addWidget(QLabel("安排结果："))
        main_layout.addWidget(splitter)  # ← 使用 splitter 替代直接 addWidget
        main_layout.addWidget(QLabel("运行日志："))
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
        self.status_label.setText("正在安排中，请等待~")
        self.result_table.setRowCount(0)
        self.assignments = None
        self.btn_export.setEnabled(False)
        self.btn_split.setEnabled(False)
        QApplication.processEvents()

        weights = preference_weights(self.preference_combo.currentData())
        self.worker = ArrangementWorker(self.classroom_path, self.examiner_path, weights)
        self.worker.signals.finished.connect(self.on_finished)
        self.worker.signals.log_message.connect(self.append_log)
        thread = threading.Thread(target=self.worker.run)
        thread.daemon = True
        thread.start()

    def append_log(self, msg):
        self.log_text.moveCursor(-1)  # Move to end
        self.log_text.insertPlainText(msg)
        self.log_text.ensureCursorVisible()

    def on_finished(self, assignments, error):
        if error or assignments is None:
            QMessageBox.critical(self, "错误", f"安排失败：\n{error}")
            self.status_label.setText("❌ 安排失败")
            self.status_label.setStyleSheet("color: #ff4444; font-weight: bold; font-size: 14pt;")
            return

        self.assignments = assignments
        self.report = getattr(self.worker, "report", {})
        self.status_label.setText("✅ 安排完成！")
        self.status_label.setStyleSheet("color: #4a79a5; font-weight: bold; font-size: 14pt;")
        self.display_results_with_merge(assignments)
        if self.report:
            self.append_log(
                f"\n缺口：{self.report.get('shortage', 0)} 人；"
                f"第一监考有经验：{self.report.get('experience_first_count', 0)}/{self.report.get('total_rooms', 0)}\n"
            )
            for warning in self.report.get("warnings", []):
                self.append_log(f"⚠️ {warning}\n")
        self.btn_export.setEnabled(True)
        self.btn_split.setEnabled(True)



    def display_results_with_merge(self, assignments):
        self.result_table.setRowCount(0)
        row = 0
        room_start_row = {}
        room_teacher_count = {}

        # 第一遍：计算每个考场的老师数量
        for room, teachers in assignments.items():
            room_teacher_count[room] = len(teachers)

        # 第二遍：填充表格并记录起始行
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
                row += 1
            room_start_row[room] = start_row

        # 第三遍：合并考场单元格
        current_row = 0
        for room, teachers in assignments.items():
            count = len(teachers)
            if count > 0:
                self.result_table.setItem(current_row, 0, QTableWidgetItem(room))
                if count > 1:
                    self.result_table.setSpan(current_row, 0, count, 1)
                current_row += count

        # 让所有列根据内容自动调整宽度
        self.result_table.resizeColumnsToContents()
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)



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

        # 3. 直接输入考场号范围/列表，一行代表一组。
        rules, ok = QInputDialog.getMultiLineText(
            self,
            "输入考场分组规则",
            "每行一组，按教室编号输入，例如：C101-C112；C201,C203,C205",
            "C101-C106\nC107-C112",
        )
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
