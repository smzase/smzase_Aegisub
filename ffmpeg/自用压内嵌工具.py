import sys
import os
import json
import subprocess
import re
import time
import glob
import copy
import ctypes
from ctypes import windll, byref, c_int, sizeof
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QMimeData, QRect, QSize, QPoint
from PyQt6.QtGui import QColor, QAction, QPalette, QPainter, QBrush, QLinearGradient, QPainterPath, QCursor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QFileDialog, QFrame, QListWidgetItem, QAbstractItemView,
    QSplitter, QGridLayout, QMessageBox, QInputDialog, QDialog, 
    QStackedWidget, QDialogButtonBox, QLabel, QButtonGroup,
    QSizePolicy, QListView
)

from qfluentwidgets import (
    SubtitleLabel, LineEdit, PushButton, PrimaryPushButton, TextEdit,
    CardWidget, StrongBodyLabel, CaptionLabel, InfoBar,
    setTheme, Theme, ListWidget, FluentIcon as FIF,
    Action, RoundMenu, CheckBox, ToolButton, setThemeColor,
    SegmentedWidget, Pivot, ProgressBar
)

# ==================== 常量与全局配置 ====================
APP_NAME = "ffmpeg_smzase"

# 硬件与编码列表
HARDWARE_LIST = ["CPU", "QSV", "NVENC", "VCN"]
CODEC_LIST = ["X264", "X265", "X266", "AV1"]

# 基础默认参数生成器
def get_default_param_matrix():
    """生成 4x4 的默认参数矩阵，值为空"""
    matrix = {}
    for hw in HARDWARE_LIST:
        matrix[hw] = {}
        for codec in CODEC_LIST:
            matrix[hw][codec] = {
                "crf": "",
                "pass1": "",
                "pass2": "",
                "enable_2pass": False  # [修改] 2-Pass 状态下沉到具体参数中
            }
    return matrix

# 默认参数模板
DEFAULT_PARAMS_TEMPLATE = get_default_param_matrix()

# ==================== 自定义控件 ====================
class PlainLineEdit(LineEdit):
    """重写粘贴行为，只接受纯文本"""
    def insertFromMimeData(self, source: QMimeData):
        if source.hasText():
            self.insert(source.text())

class PlainTextEdit(TextEdit):
    """重写粘贴行为，只接受纯文本"""
    def insertFromMimeData(self, source: QMimeData):
        if source.hasText():
            self.insertPlainText(source.text())

class DraggableListWidget(ListWidget):
    """[新增] 支持拖拽重排并自动恢复 ItemWidget 的列表控件"""
    itemDropped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def dropEvent(self, event):
        super().dropEvent(event)
        # 触发信号以便主窗口重新绑定 Widget
        self.itemDropped.emit()

class BatchAddPopup(QDialog):
    """[新增] 批量添加集数弹窗 (Popup 模式: 无边框, 点击外部关闭, 不可拖拽)"""
    tasks_added = pyqtSignal(list, str) # episodes_list, mode

    def __init__(self, parent=None):
        super().__init__(parent)
        # 设置为 Popup 属性：无标题栏，点击窗口外自动关闭
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(320, 220)
        
        # 简单样式
        self.setStyleSheet("""
            QDialog { 
                background-color: #ffffff; 
                border: 1px solid #dcdcdc; 
                border-radius: 8px; 
            }
            QLabel { font-size: 14px; color: #333; }
        """)
        if parent and parent.is_dark:
             self.setStyleSheet("""
                QDialog { 
                    background-color: #2b2b2b; 
                    border: 1px solid #454545; 
                    border-radius: 8px; 
                }
                QLabel { font-size: 14px; color: #ffffff; }
            """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 标题
        title = StrongBodyLabel("批量添加多集")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # 范围输入 [ ] ~ [ ]
        range_layout = QHBoxLayout()
        self.input_start = LineEdit()
        self.input_start.setPlaceholderText("01")
        self.input_start.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        lbl_to = StrongBodyLabel("~")
        
        self.input_end = LineEdit()
        self.input_end.setPlaceholderText("12")
        self.input_end.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        range_layout.addWidget(self.input_start)
        range_layout.addWidget(lbl_to)
        range_layout.addWidget(self.input_end)
        layout.addLayout(range_layout)

        # 后缀输入
        suffix_layout = QHBoxLayout()
        lbl_suf = CaptionLabel("后缀(可选):")
        self.input_suffix = LineEdit()
        self.input_suffix.setPlaceholderText("[V2]")
        suffix_layout.addWidget(lbl_suf)
        suffix_layout.addWidget(self.input_suffix)
        layout.addLayout(suffix_layout)

        layout.addStretch(1)

        # 按钮组
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        
        btn_sc = PushButton("简体")
        btn_sc.clicked.connect(lambda: self._confirm("SC"))
        
        btn_tc = PushButton("繁体")
        btn_tc.clicked.connect(lambda: self._confirm("TC"))
        
        btn_both = PrimaryPushButton("简繁")
        btn_both.clicked.connect(lambda: self._confirm("BOTH"))

        btn_layout.addWidget(btn_sc)
        btn_layout.addWidget(btn_tc)
        btn_layout.addWidget(btn_both)
        layout.addLayout(btn_layout)

    def _confirm(self, mode):
        start_str = self.input_start.text().strip()
        end_str = self.input_end.text().strip()
        
        if not start_str or not end_str:
            InfoBar.warning(title="输入错误", content="请输入起始和结束集数", parent=self.parent())
            return

        try:
            start = int(start_str)
            end = int(end_str)
        except ValueError:
            InfoBar.error(title="输入错误", content="集数必须为数字", parent=self.parent())
            return

        if start > end:
            start, end = end, start

        # 确定填充长度，以输入的最长字符串为准，例如输入 01，则长度为2
        pad_len = max(len(start_str), len(end_str))
        
        ep_list = []
        for i in range(start, end + 1):
            ep_list.append(str(i).zfill(pad_len))

        # 这里的 parent() 是 MainWindow，通过信号或者直接调用方法
        # 为了解耦，使用自定义信号，但这里为了方便直接调用父级方法，或者在父级 connect
        if self.parent():
            self.parent()._batch_add_callback(ep_list, mode, self.input_suffix.text().strip())
        
        self.close()

class TaskItemWidget(QWidget):
    """自定义任务列表项 Widget"""
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text
        self.progress = 0.0
        self.pass_type = 0 # 0: Normal/CRF, 1: Pass 1, 2: Pass 2
        self.status_code = 0 # 0: Pending, 1: Running, 2: Done, 3: Error
        
        # 布局设置为0，确保 Widget 填满 Item 区域
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # 使用普通的 QLabel 以获得更好的换行控制
        self.lbl_title = QLabel(text)
        self.lbl_title.setWordWrap(True)
        # 给 Label 设置 Margin，确保文字不会贴在进度条背景的边缘
        # 这里的值 (16, 12, 16, 12) 是为了让文字在 visually adjusted 的矩形内部
        self.lbl_title.setContentsMargins(16, 12, 16, 12)
        
        # 继承父级字体样式并调整
        font = self.lbl_title.font()
        font.setBold(False)
        font.setPointSize(10)
        self.lbl_title.setFont(font)
        self.lbl_title.setStyleSheet("background: transparent; border: none;")
        
        layout.addWidget(self.lbl_title)
        
    def set_status(self, progress: float = 0.0, pass_idx=0, status_code=0):
        self.progress = max(0.0, min(1.0, progress))
        self.pass_type = pass_idx
        self.status_code = status_code
        self.update() # 触发重绘

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 获取控件区域
        full_rect = self.rect()
        
        # 【关键修改】调整绘制区域以匹配 Fluent UI 列表项选中态的背景大小
        # 通常 Fluent List Item 有左右 4px，上下 2px 的边距
        draw_rect = full_rect.adjusted(4, 2, -4, -2)
        
        # 定义圆角路径 (圆角半径通常为 4 或 6)
        path = QPainterPath()
        path.addRoundedRect(draw_rect.x(), draw_rect.y(), draw_rect.width(), draw_rect.height(), 6, 6)
        
        # 1. 绘制完成或失败的背景
        if self.status_code == 2: # Done
             painter.fillPath(path, QColor(0, 200, 0, 40)) # 绿色背景
        elif self.status_code == 3: # Error
             painter.fillPath(path, QColor(255, 0, 0, 40)) # 红色背景
        
        # 2. 绘制进度条 (作为动态背景)
        elif self.status_code == 1 and self.progress > 0: # Running
            # 进度条颜色区分
            if self.pass_type == 1:
                # Pass 1: 蓝色系
                color_start = QColor(0, 120, 215, 90)
                color_end = QColor(0, 120, 215, 40)
            else:
                # CRF / Pass 2: 绿色系
                color_start = QColor(0, 180, 60, 90)
                color_end = QColor(0, 180, 60, 40)
            
            # 计算进度宽度 (基于调整后的 draw_rect)
            prog_width = int(draw_rect.width() * self.progress)
            
            # 创建裁剪区域，确保进度条不超出圆角矩形
            painter.setClipPath(path)
            
            # 绘制渐变
            prog_rect = QRect(draw_rect.x(), draw_rect.y(), prog_width, draw_rect.height())
            gradient = QLinearGradient(draw_rect.x(), 0, draw_rect.x() + prog_width, 0)
            gradient.setColorAt(0, color_start)
            gradient.setColorAt(1, color_end)
            painter.fillRect(prog_rect, QBrush(gradient))

        super().paintEvent(event)

# ==================== 辅助组件：参数编辑器 Widget ====================
class ParamEditorPage(QWidget):
    """用于设置对话框中的单页参数编辑器"""
    def __init__(self, hardware_name: str, initial_data: Dict, parent=None):
        super().__init__(parent)
        self.hardware_name = hardware_name
        self.data = initial_data 
        self.current_codec = "X264"
        self._init_ui()
        
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(15)
        
        # 顶部：编码选择 (居左)
        nav_container = QHBoxLayout()
        self.seg_codec = SegmentedWidget()
        for codec in CODEC_LIST:
            self.seg_codec.addItem(routeKey=codec, text=codec)
        self.seg_codec.setCurrentItem("X264")
        self.seg_codec.currentItemChanged.connect(self._on_codec_changed)
        
        nav_container.addWidget(self.seg_codec)
        nav_container.addStretch(1) 
        layout.addLayout(nav_container)
        
        layout.addWidget(SubtitleLabel(f"{self.hardware_name} 参数配置"))
        
        # 参数输入区
        self.input_crf = PlainTextEdit()
        self.input_crf.setMinimumHeight(100)
        self.input_crf.textChanged.connect(self._save_current_to_memory)
        
        self.input_pass1 = PlainTextEdit()
        self.input_pass1.setMinimumHeight(100)
        self.input_pass1.textChanged.connect(self._save_current_to_memory)
        
        self.input_pass2 = PlainTextEdit()
        self.input_pass2.setMinimumHeight(100)
        self.input_pass2.textChanged.connect(self._save_current_to_memory)
        
        layout.addWidget(StrongBodyLabel("CRF / 单次模式:"))
        layout.addWidget(self.input_crf)
        
        layout.addWidget(StrongBodyLabel("2-Pass - Step 1:"))
        layout.addWidget(self.input_pass1)
        
        layout.addWidget(StrongBodyLabel("2-Pass - Step 2:"))
        layout.addWidget(self.input_pass2)
        
        self._load_codec_data("X264")

    def _on_codec_changed(self, route_key):
        self._save_current_to_memory()
        self.current_codec = route_key
        self._load_codec_data(route_key)

    def _load_codec_data(self, codec):
        self.input_crf.blockSignals(True)
        self.input_pass1.blockSignals(True)
        self.input_pass2.blockSignals(True)
        
        params = self.data.get(self.hardware_name, {}).get(codec, {"crf": "", "pass1": "", "pass2": ""})
        self.input_crf.setPlainText(params.get("crf", ""))
        self.input_pass1.setPlainText(params.get("pass1", ""))
        self.input_pass2.setPlainText(params.get("pass2", ""))
        
        self.input_crf.blockSignals(False)
        self.input_pass1.blockSignals(False)
        self.input_pass2.blockSignals(False)

    def _save_current_to_memory(self):
        if self.hardware_name not in self.data:
            self.data[self.hardware_name] = {}
        if self.current_codec not in self.data[self.hardware_name]:
            self.data[self.hardware_name][self.current_codec] = {}
            
        target = self.data[self.hardware_name][self.current_codec]
        target["crf"] = self.input_crf.toPlainText()
        target["pass1"] = self.input_pass1.toPlainText()
        target["pass2"] = self.input_pass2.toPlainText()

# ==================== 设置对话框 ====================
class SettingsDialog(QDialog):
    def __init__(self, parent=None, current_settings=None, is_dark=False):
        super().__init__(parent)
        self.setWindowTitle("全局设置")
        self.resize(1000, 700)
        
        self.raw_settings = copy.deepcopy(current_settings) if current_settings else {}
        defaults = self.raw_settings.get("default_params_matrix", DEFAULT_PARAMS_TEMPLATE)
        self.param_matrix = copy.deepcopy(defaults)
        
        self.is_dark = is_dark
        self.parent_window = parent
        
        self._apply_dialog_style()

        self.input_base_path = PlainLineEdit()
        self.chk_use_folder = CheckBox("在目录下创建独立文件夹")
        self.input_folder_name = PlainLineEdit()

        self._init_ui()
        self._load_general_data()

    def _apply_dialog_style(self):
        if self.is_dark:
            self.setStyleSheet("""
                QDialog { background-color: #303030; color: white; }
                QLabel { color: white; }
                QListWidget { background-color: #2b2b2b; border: none; border-right: 1px solid #3d3d3d; outline: none; }
                QListWidget::item { height: 36px; padding-left: 10px; color: #dddddd; border: none; }
                QListWidget::item:selected { background-color: #404040; color: #FF9900; border-left: 3px solid #FF9900; }
                QListWidget::item:hover { background-color: #383838; }
                QListWidget::item:disabled { color: #808080; background-color: transparent; font-weight: bold; }
                QWidget#PageContent { background-color: transparent; }
            """)
        else:
            self.setStyleSheet("""
                QDialog { background-color: #f9f9f9; }
                QListWidget { background-color: #f0f0f0; border: none; border-right: 1px solid #e0e0e0; outline: none; }
                QListWidget::item { height: 36px; padding-left: 10px; border: none; }
                QListWidget::item:selected { background-color: #ffffff; color: #0078D7; border-left: 3px solid #0078D7; }
                QListWidget::item:hover { background-color: #e5e5e5; }
                QListWidget::item:disabled { color: #505050; background-color: transparent; font-weight: bold; }
            """)

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.nav_list = ListWidget()
        self.nav_list.setFixedWidth(200)
        
        self._add_nav_item("常规设置", 0)
        header_item = QListWidgetItem("压制参数")
        header_item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.nav_list.addItem(header_item)
        
        self._add_nav_item("    CPU", 1)
        self._add_nav_item("    QSV", 2)
        self._add_nav_item("    NVENC", 3)
        self._add_nav_item("    VCN", 4)
        
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self._switch_page)
        main_layout.addWidget(self.nav_list)

        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(20, 20, 20, 20)
        
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.addWidget(self._create_general_page())
        
        for hw in HARDWARE_LIST:
            page = ParamEditorPage(hw, self.param_matrix)
            self.stacked_widget.addWidget(page)
        
        right_layout.addWidget(self.stacked_widget)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        
        self.btn_apply = PushButton("应用")
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_ok = PrimaryPushButton("确定")
        self.btn_ok.clicked.connect(self._on_accept)
        self.btn_cancel = PushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(self.btn_apply)
        btn_layout.addWidget(self.btn_ok)
        btn_layout.addWidget(self.btn_cancel)
        
        right_layout.addLayout(btn_layout)
        main_layout.addWidget(right_container)

    def _add_nav_item(self, text, page_idx):
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, page_idx)
        self.nav_list.addItem(item)

    def _create_general_page(self):
        page = QWidget()
        page.setObjectName("PageContent")
        layout = QVBoxLayout(page)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        layout.addWidget(SubtitleLabel("存储路径配置"))
        
        card = CardWidget()
        grid = QGridLayout(card)
        grid.setContentsMargins(20, 20, 20, 20)
        
        grid.addWidget(StrongBodyLabel("配置文件根目录"), 0, 0)
        grid.addWidget(self.input_base_path, 1, 0)
        btn_browse = PushButton(FIF.FOLDER, "浏览")
        btn_browse.clicked.connect(self._browse_path)
        grid.addWidget(btn_browse, 1, 1)
        
        grid.addWidget(self.chk_use_folder, 2, 0, 1, 2)
        grid.addWidget(CaptionLabel("独立文件夹名称:"), 3, 0)
        grid.addWidget(self.input_folder_name, 4, 0, 1, 2)
        
        self.chk_use_folder.stateChanged.connect(
            lambda: self.input_folder_name.setEnabled(self.chk_use_folder.isChecked())
        )
        
        layout.addWidget(card)
        return page

    def _switch_page(self, row):
        item = self.nav_list.item(row)
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is not None:
            self.stacked_widget.setCurrentIndex(idx)

    def _browse_path(self):
        folder = QFileDialog.getExistingDirectory(self, "选择配置存储根目录")
        if folder:
            self.input_base_path.setText(folder.replace("/", "\\"))

    def _load_general_data(self):
        self.input_base_path.setText(self.raw_settings.get("base_path", ""))
        self.chk_use_folder.setChecked(self.raw_settings.get("use_sub_folder", True))
        self.input_folder_name.setText(self.raw_settings.get("folder_name", "ffmpeg smzase"))
        self.input_folder_name.setEnabled(self.chk_use_folder.isChecked())

    def get_current_settings_dict(self) -> Dict:
        # 保留原有的 theme_mode 和 window_geometry
        settings = copy.deepcopy(self.raw_settings)
        settings.update({
            "base_path": self.input_base_path.text(),
            "use_sub_folder": self.chk_use_folder.isChecked(),
            "folder_name": self.input_folder_name.text(),
            "default_params_matrix": self.param_matrix
        })
        return settings

    def _on_apply(self):
        new_settings = self.get_current_settings_dict()
        if self.parent_window:
            self.parent_window.update_settings(new_settings)
        InfoBar.success(title="已应用", content="设置已保存", parent=self)

    def _on_accept(self):
        self._on_apply()
        self.accept()

# ==================== 进度显示面板 ====================
class ProgressDashboard(CardWidget):
    """FFmpeg 进度显示面板 - 紧凑型"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(70) 
        self.start_timestamp = 0
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 8, 15, 8)
        
        self.lbl_elapsed = self._create_metric(layout, "已用时间", "00:00:00")
        layout.addStretch(1)
        self.lbl_time = self._create_metric(layout, "视频位置", "00:00:00.00")
        layout.addStretch(1)
        self.lbl_speed = self._create_metric(layout, "压制速度", "0.00x")
        layout.addStretch(1)
        self.lbl_fps = self._create_metric(layout, "FPS", "0")
        layout.addStretch(1)
        self.lbl_bitrate = self._create_metric(layout, "实时码率", "0 kbits/s")

    def _create_metric(self, parent_layout: QHBoxLayout, title: str, default_val: str) -> SubtitleLabel:
        container = QFrame()
        v_layout = QVBoxLayout(container)
        v_layout.setContentsMargins(0, 0, 0, 0)
        v_layout.setSpacing(0)
        v_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        title_label = CaptionLabel(title)
        title_label.setTextColor(QColor(100, 100, 100), QColor(150, 150, 150))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        value_label = StrongBodyLabel(default_val)
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_label.setObjectName("MetricValue")
        
        v_layout.addWidget(title_label)
        v_layout.addWidget(value_label)
        parent_layout.addWidget(container)
        return value_label

    def start_timer(self):
        self.start_timestamp = time.time()

    def update_data(self, ffmpeg_output: str):
        if self.start_timestamp > 0:
            elapsed = int(time.time() - self.start_timestamp)
            self.lbl_elapsed.setText(str(timedelta(seconds=elapsed)))

        if match := re.search(r'time=(\d+:\d+:\d+\.\d+)', ffmpeg_output):
            self.lbl_time.setText(match.group(1))
        if match := re.search(r'speed=\s*([\d\.]+)x', ffmpeg_output):
            self.lbl_speed.setText(f"{match.group(1)}x")
        if match := re.search(r'fps=\s*(\d+)', ffmpeg_output):
            self.lbl_fps.setText(match.group(1))
        if match := re.search(r'bitrate=\s*([\d\.]+kbits/s)', ffmpeg_output):
            self.lbl_bitrate.setText(match.group(1))

    def reset(self):
        self.start_timestamp = 0
        self.lbl_elapsed.setText("00:00:00")
        self.lbl_time.setText("00:00:00.00")
        self.lbl_speed.setText("0.00x")
        self.lbl_fps.setText("0")
        self.lbl_bitrate.setText("0 kbits/s")

# ==================== 后台工作线程 ====================
class Worker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(str) # 原始输出
    percent_signal = pyqtSignal(float, int) # 进度百分比, pass_index
    finished_signal = pyqtSignal(bool, str, str)
    
    def __init__(self, commands: List[str], work_dir: str, output_file: str, 
                 temp_files: Optional[List[str]] = None):
        super().__init__()
        self.commands = commands
        self.work_dir = work_dir
        self.output_file = output_file
        self.temp_files = temp_files or []
        self.process: Optional[subprocess.Popen] = None
        self.is_killed = False
        self.last_update_time = 0
        self.start_time = 0
        self.total_duration_sec = 0.0

    def run(self):
        self.start_time = time.time()
        self.log_signal.emit(f"Working Directory: {self.work_dir}")
        
        total_steps = len(self.commands)
        
        for index, cmd in enumerate(self.commands):
            if self.is_killed: break
            
            step_num = 0
            if total_steps > 1:
                step_num = index + 1 # 1 for Pass 1, 2 for Pass 2
                step_name = 'Pass 1' if index == 0 else 'Pass 2'
                self.log_signal.emit(f"🔄 正在执行 [Step {index + 1}/{total_steps}]: {step_name}")
            else:
                step_num = 2 # treat single pass as "final" pass for color purpose (Green)
            
            self.log_signal.emit(f"Executing: {cmd}")
            
            if not self._execute_command(cmd, step_num):
                self._cleanup()
                duration = self._get_duration_str()
                self.finished_signal.emit(False, self.output_file, duration)
                return

        self._cleanup()
        duration = self._get_duration_str()
        
        if self.is_killed:
            self.log_signal.emit("🔴 [用户操作] 进程已强制终止")
            self.finished_signal.emit(False, self.output_file, duration)
        else:
            self.log_signal.emit(f"🟢 [完成] FFmpeg 进程成功退出，耗时: {duration}")
            self.finished_signal.emit(True, self.output_file, duration)

    def _get_duration_str(self) -> str:
        if self.start_time == 0: return "0s"
        elapsed = int(time.time() - self.start_time)
        return str(timedelta(seconds=elapsed))

    def _parse_duration(self, time_str):
        """Parse HH:MM:SS.mm into seconds"""
        try:
            h, m, s = time_str.split(':')
            return float(h) * 3600 + float(m) * 60 + float(s)
        except:
            return 0.0

    def _execute_command(self, cmd: str, pass_index: int) -> bool:
        try:
            self.process = subprocess.Popen(
                cmd, cwd=self.work_dir, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding='utf-8', errors='replace', text=True, bufsize=1
            )
            
            # 重置当前命令的时长解析
            self.total_duration_sec = 0.0
            
            while True:
                if self.is_killed: break
                line = self.process.stdout.readline()
                if not line and self.process.poll() is not None: break
                if line: self._process_output_line(line.strip(), pass_index)

            return_code = self.process.poll()
            if self.is_killed: return False
            if return_code == 0: return True
            self.log_signal.emit(f"🔴 [错误] FFmpeg 异常退出，代码 {return_code}")
            return False
        except Exception as e:
            self.log_signal.emit(f"🔴 [系统异常] 无法启动进程: {str(e)}")
            return False

    def _process_output_line(self, line: str, pass_index: int):
        # 尝试获取总时长
        if "Duration:" in line and self.total_duration_sec == 0:
            if match := re.search(r"Duration: (\d+:\d+:\d+\.\d+)", line):
                self.total_duration_sec = self._parse_duration(match.group(1))

        if "frame=" in line and "time=" in line:
            current_time = time.time()
            if current_time - self.last_update_time > 0.5:
                self.progress_signal.emit(line)
                
                # 计算百分比
                if self.total_duration_sec > 0:
                    if match := re.search(r'time=(\d+:\d+:\d+\.\d+)', line):
                        current_sec = self._parse_duration(match.group(1))
                        percent = current_sec / self.total_duration_sec
                        self.percent_signal.emit(percent, pass_index)
                
                self.last_update_time = current_time
        elif any(keyword in line for keyword in ["Error", "error", "Input #", "Output #"]):
            self.log_signal.emit(line)

    def _cleanup(self):
        for pattern in self.temp_files:
            for file_path in glob.glob(pattern):
                try: os.remove(file_path)
                except: pass

    def stop(self):
        self.is_killed = True
        if self.process:
            try: subprocess.run(f"taskkill /F /T /PID {self.process.pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except: pass


# ==================== 主窗口 ====================
class EncodeManager(QMainWindow):
    """压制管理主窗口"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("smzase 自用压制工具")
        
        # 1. 初始化基础路径逻辑
        default_docs = os.path.join(os.path.expanduser("~"), "Documents")
        default_folder = "ffmpeg smzase"
        self.default_config_base = os.path.join(default_docs, default_folder)
        
        if not os.path.exists(self.default_config_base):
            try:
                os.makedirs(self.default_config_base, exist_ok=True)
            except:
                self.default_config_base = os.getcwd()

        self.app_settings_file = os.path.join(self.default_config_base, "app_settings.json")
        self.app_settings = self._load_app_settings()
        self.config_dir = self._get_config_dir()
        self.profile_file = os.path.join(self.config_dir, "profiles.json")
        self.app_settings_file = os.path.join(self.config_dir, "app_settings.json")
        self._save_app_settings()
        self._restore_window_geometry()
        
        self.profiles: Dict = {}
        self.current_profile_name: Optional[str] = None
        self.completed_history: set = set()
        self.force_stop_flag = False
        
        self.worker: Optional[Worker] = None
        self.is_running = False
        self.current_item: Optional[QListWidgetItem] = None
        
        # UI 组件引用
        self.btn_confirm_stop = None

        # 从设置中加载主题配置
        theme_mode = self.app_settings.get("theme_mode", "light")
        self.is_dark = (theme_mode == "dark")
        
        # 【关键修改】先创建 UI，再应用主题
        self._init_ui()
        # self._apply_theme(self.is_dark)
        
        self._load_profiles()

    def _restore_window_geometry(self):
        """恢复窗口几何信息，如果不存在则使用默认"""
        geometry = self.app_settings.get("window_geometry")
        if geometry and len(geometry) == 4:
            try:
                self.setGeometry(*geometry)
            except:
                self.resize(1300, 950)
        else:
            self.resize(1300, 950)

    def _get_config_dir(self) -> str:
        base = self.app_settings.get("base_path", "")
        if not base:
            base = os.path.join(os.path.expanduser("~"), "Documents")
        
        use_folder = self.app_settings.get("use_sub_folder", True)
        folder_name = self.app_settings.get("folder_name", "ffmpeg smzase")
        
        target = os.path.join(base, folder_name) if use_folder else base
        if not os.path.exists(target):
            try: os.makedirs(target)
            except: return os.getcwd() 
        return target

    def _load_app_settings(self) -> Dict:
        paths_to_try = [
            self.app_settings_file,
            "app_settings.json" # CWD fallback
        ]

        loaded_settings = {}
        file_found = False

        for path in paths_to_try:
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        loaded_settings = json.load(f)
                        file_found = True
                        break
                except: pass
        
        default_docs = os.path.join(os.path.expanduser("~"), "Documents")
        
        default_settings = {
            "base_path": default_docs,
            "use_sub_folder": True,
            "folder_name": "ffmpeg smzase",
            "default_params_matrix": DEFAULT_PARAMS_TEMPLATE,
            "window_geometry": None,
            "theme_mode": "light"
        }
        
        if file_found:
            for k, v in loaded_settings.items():
                default_settings[k] = v
                
        return default_settings

    def update_settings(self, new_settings: Dict):
        path_changed = (
            new_settings["base_path"] != self.app_settings.get("base_path") or
            new_settings["use_sub_folder"] != self.app_settings.get("use_sub_folder") or
            new_settings["folder_name"] != self.app_settings.get("folder_name")
        )
        
        if "window_geometry" not in new_settings:
            new_settings["window_geometry"] = self.app_settings.get("window_geometry")
        if "theme_mode" not in new_settings:
            new_settings["theme_mode"] = self.app_settings.get("theme_mode", "light")
            
        self.app_settings = new_settings
        
        if path_changed:
            self.config_dir = self._get_config_dir()
            self.app_settings_file = os.path.join(self.config_dir, "app_settings.json")
            self.profile_file = os.path.join(self.config_dir, "profiles.json")
            
        self._save_app_settings()
        if path_changed:
             QMessageBox.information(self, "路径变更", "路径设置已修改，配置文件将保存至新路径。\n注意：旧路径下的文件不会自动移动，请手动迁移 Profiles。")

    def _save_app_settings(self):
        try:
            parent_dir = os.path.dirname(self.app_settings_file)
            if not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
            with open(self.app_settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.app_settings, f, ensure_ascii=False, indent=4)
        except Exception as e:
            try:
                InfoBar.error(title="错误", content=f"保存设置失败: {e}", parent=self)
            except:
                print(f"Error saving settings: {e}")

    # ==================== UI 初始化 ====================
    def _init_ui(self):
        container = QWidget()
        self.setCentralWidget(container)
        
        main_layout = QHBoxLayout(container)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        main_layout.addWidget(self._create_profile_panel())
        main_layout.addWidget(self._create_main_panel())

    def _create_profile_panel(self) -> CardWidget:
        panel = CardWidget()
        panel.setFixedWidth(240)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        
        add_btn = PushButton(FIF.ADD, "新建配置")
        add_btn.clicked.connect(self._add_profile_dialog)
        layout.addWidget(add_btn)
        
        self.profile_list_widget = ListWidget()
        self.profile_list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.profile_list_widget.customContextMenuRequested.connect(self._show_profile_context_menu)
        self.profile_list_widget.itemClicked.connect(self._on_profile_selected)
        layout.addWidget(self.profile_list_widget)
        
        hint = CaptionLabel("右键可删除或重命名")
        hint.setTextColor(QColor(128, 128, 128), QColor(128, 128, 128))
        layout.addWidget(hint)
        
        layout.addSpacing(10)
        
        bottom_tools = QHBoxLayout()
        bottom_tools.setSpacing(5)
        
        self.btn_theme = ToolButton(FIF.CONSTRACT)
        self.btn_theme.setToolTip("切换深浅色模式")
        self.btn_theme.clicked.connect(self._toggle_theme)
        
        self.btn_settings = ToolButton(FIF.SETTING)
        self.btn_settings.setToolTip("全局设置")
        self.btn_settings.clicked.connect(self._open_settings_dialog)
        
        bottom_tools.addWidget(self.btn_theme)
        bottom_tools.addWidget(self.btn_settings)
        bottom_tools.addStretch(1)
        
        layout.addLayout(bottom_tools)
        return panel

    def _create_main_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        
        layout.addWidget(self._create_settings_card())
        layout.addWidget(self._create_control_card())
        
        # 4. 替换 QSplitter 为 QHBoxLayout，按比例固定宽度
        bottom_container = QWidget()
        bottom_layout = QHBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(10)
        
        queue_panel = self._create_queue_panel()
        log_panel = self._create_log_panel()
        
        # 设置拉伸因子 38 : 62
        bottom_layout.addWidget(queue_panel, 38)
        bottom_layout.addWidget(log_panel, 62)
        
        layout.addWidget(bottom_container, 1)
        return panel

    def _create_settings_card(self) -> CardWidget:
        card = CardWidget()
        layout = QGridLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setVerticalSpacing(10)
        layout.setHorizontalSpacing(15)
        
        # 5. 强制设置第一列（标签列）的最小宽度，防止界面抖动并对齐
        layout.setColumnMinimumWidth(0, 90)
        
        self.entry_source_dir = self._add_folder_row(layout, 0, "源视频目录")
        self.entry_sub_dir = self._add_folder_row(layout, 1, "字幕目录")
        self.entry_output_dir = self._add_folder_row(layout, 2, "输出目录")
        
        layout.addWidget(self._create_template_group(), 3, 0, 1, 3)
        self._add_params_section(layout)
        
        for widget in [self.entry_source_dir, self.entry_sub_dir, self.entry_output_dir,
                      self.entry_source_template, self.entry_sub_sc, self.entry_sub_tc,
                      self.entry_out_sc, self.entry_out_tc]:
            widget.textChanged.connect(self._save_current_profile_meta)
        
        return card

    def _add_folder_row(self, layout: QGridLayout, row: int, label: str) -> PlainLineEdit:
        layout.addWidget(StrongBodyLabel(label), row, 0)
        entry = PlainLineEdit()
        layout.addWidget(entry, row, 1)
        btn = PushButton(FIF.FOLDER, "浏览")
        btn.clicked.connect(lambda: self._browse_folder(entry))
        layout.addWidget(btn, row, 2)
        return entry

    def _create_template_group(self) -> QFrame:
        group = QFrame()
        layout = QGridLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setColumnMinimumWidth(0, 90) # 对齐内部网格的第一列
        
        # Row 0: Source Template (Wider)
        layout.addWidget(StrongBodyLabel("源视频文件"), 0, 0)
        self.entry_source_template = PlainLineEdit()
        self.entry_source_template.setPlaceholderText("Title - S01E<ep>.mkv")
        layout.addWidget(self.entry_source_template, 0, 1, 1, 3) # Span 3 cols
        
        # Row 1: SC
        layout.addWidget(StrongBodyLabel("简体字幕"), 1, 0)
        self.entry_sub_sc = PlainLineEdit()
        self.entry_sub_sc.setFixedWidth(140)
        layout.addWidget(self.entry_sub_sc, 1, 1)
        
        lbl_out_sc = StrongBodyLabel("输出名称")
        lbl_out_sc.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(lbl_out_sc, 1, 2)
        self.entry_out_sc = PlainLineEdit()
        layout.addWidget(self.entry_out_sc, 1, 3)
        
        # Row 2: TC
        layout.addWidget(StrongBodyLabel("繁体字幕"), 2, 0)
        self.entry_sub_tc = PlainLineEdit()
        self.entry_sub_tc.setFixedWidth(140)
        layout.addWidget(self.entry_sub_tc, 2, 1)
        
        lbl_out_tc = StrongBodyLabel("输出名称")
        lbl_out_tc.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(lbl_out_tc, 2, 2)
        self.entry_out_tc = PlainLineEdit()
        layout.addWidget(self.entry_out_tc, 2, 3)
        
        return group

    def _add_params_section(self, layout: QGridLayout):
        control_bar_layout = QHBoxLayout()
        
        self.seg_hardware = SegmentedWidget()
        for hw in HARDWARE_LIST:
            self.seg_hardware.addItem(routeKey=hw, text=hw)
        self.seg_hardware.setCurrentItem("CPU")
        self.seg_hardware.currentItemChanged.connect(self._on_selector_changed)
        control_bar_layout.addWidget(self.seg_hardware)
        
        control_bar_layout.addSpacing(20)
        
        self.seg_codec = SegmentedWidget()
        for codec in CODEC_LIST:
            self.seg_codec.addItem(routeKey=codec, text=codec)
        self.seg_codec.setCurrentItem("X264")
        self.seg_codec.currentItemChanged.connect(self._on_selector_changed)
        control_bar_layout.addWidget(self.seg_codec)
        
        control_bar_layout.addStretch(1) 
        
        self.chk_2pass = CheckBox("启用 2-Pass Mode")
        self.chk_2pass.stateChanged.connect(self._toggle_2pass_ui)
        # [修改] 2-Pass 状态保存到具体的参数矩阵中，而不是 Profile 元数据
        self.chk_2pass.stateChanged.connect(self._save_current_params) 
        control_bar_layout.addWidget(self.chk_2pass)
        
        layout.addLayout(control_bar_layout, 4, 0, 1, 3)

        self.lbl_crf = StrongBodyLabel("CRF/单次参数:")
        layout.addWidget(self.lbl_crf, 5, 0, Qt.AlignmentFlag.AlignTop)
        
        self.text_params_crf = PlainTextEdit()
        self.text_params_crf.setFixedHeight(80)
        self.text_params_crf.textChanged.connect(self._save_current_params)
        layout.addWidget(self.text_params_crf, 5, 1, 1, 2)
        
        self.lbl_pass1 = StrongBodyLabel("Pass 1 参数:")
        layout.addWidget(self.lbl_pass1, 6, 0, Qt.AlignmentFlag.AlignTop)
        
        self.text_params_pass1 = PlainTextEdit()
        self.text_params_pass1.setFixedHeight(80)
        self.text_params_pass1.textChanged.connect(self._save_current_params)
        layout.addWidget(self.text_params_pass1, 6, 1, 1, 2)
        
        self.lbl_pass2 = StrongBodyLabel("Pass 2 参数:")
        layout.addWidget(self.lbl_pass2, 7, 0, Qt.AlignmentFlag.AlignTop)
        
        self.text_params_pass2 = PlainTextEdit()
        self.text_params_pass2.setFixedHeight(80)
        self.text_params_pass2.textChanged.connect(self._save_current_params)
        layout.addWidget(self.text_params_pass2, 7, 1, 1, 2)
        
        self._toggle_2pass_ui(0)

    def _create_control_card(self) -> CardWidget:
        card = CardWidget()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(20, 15, 20, 15)
        
        layout.addWidget(SubtitleLabel("集数"))
        self.entry_ep = PlainLineEdit()
        self.entry_ep.setFixedWidth(80)
        self.entry_ep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.entry_ep.setPlaceholderText("01")
        self.entry_ep.textChanged.connect(self._save_current_profile_meta)
        layout.addWidget(self.entry_ep)
        
        # [新增] 批量添加按钮
        btn_batch = PushButton(FIF.TILES, "批量多集")
        btn_batch.clicked.connect(self._open_batch_dialog)
        layout.addWidget(btn_batch)
        
        layout.addSpacing(15)
        
        self.chk_suffix = CheckBox("添加后缀")
        layout.addWidget(self.chk_suffix)
        
        self.entry_suffix = PlainLineEdit()
        self.entry_suffix.setPlaceholderText("[V2]")
        self.entry_suffix.setText("[V2]")
        self.entry_suffix.setFixedWidth(80)
        self.entry_suffix.setEnabled(False)
        self.chk_suffix.stateChanged.connect(
            lambda s: self.entry_suffix.setEnabled(s == 2)
        )
        layout.addWidget(self.entry_suffix)
        
        layout.addStretch(1)
        
        # [修改] 按钮名称简化
        btn_add_sc = PushButton(FIF.ADD, "简体")
        btn_add_sc.clicked.connect(lambda: self._add_tasks_from_ui("SC"))
        layout.addWidget(btn_add_sc)
        
        btn_add_tc = PushButton(FIF.ADD, "繁体")
        btn_add_tc.clicked.connect(lambda: self._add_tasks_from_ui("TC"))
        layout.addWidget(btn_add_tc)
        
        btn_add_both = PrimaryPushButton(FIF.ADD, "简繁")
        btn_add_both.clicked.connect(lambda: self._add_tasks_from_ui("BOTH"))
        layout.addWidget(btn_add_both)
        return card

    def _create_queue_panel(self) -> CardWidget:
        panel = CardWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        
        header = QHBoxLayout()
        header.addWidget(StrongBodyLabel("任务队列"))
        header.addStretch(1)
        btn_clear = PushButton(FIF.DELETE, "清空")
        btn_clear.clicked.connect(self._clear_queue)
        header.addWidget(btn_clear)
        layout.addLayout(header)
        
        # [修改] 使用自定义的 DraggableListWidget
        self.queue_list = DraggableListWidget()
        self.queue_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue_list.customContextMenuRequested.connect(self._show_queue_context_menu)
        self.queue_list.itemDropped.connect(self._on_queue_item_dropped)
        
        # 允许 ItemWidget 自动调整大小以支持换行
        self.queue_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.queue_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        
        layout.addWidget(self.queue_list)
        return panel

    def _create_log_panel(self) -> CardWidget:
        panel = CardWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        
        header = QHBoxLayout()
        header.addWidget(StrongBodyLabel("执行控制"))
        header.addStretch(1)
        self.btn_start = PrimaryPushButton(FIF.PLAY, "开始压制")
        self.btn_start.clicked.connect(self._start_processing)
        header.addWidget(self.btn_start)
        
        # 3. 强制终止逻辑：初始按钮
        self.btn_stop = PushButton(FIF.CLOSE, "强制终止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._show_stop_confirm)
        header.addWidget(self.btn_stop)
        
        # 3. 强制终止逻辑：确认容器 (默认隐藏)
        self.stop_confirm_widget = QWidget()
        stop_layout = QHBoxLayout(self.stop_confirm_widget)
        stop_layout.setContentsMargins(0, 0, 0, 0)
        stop_layout.setSpacing(5)
        
        # 保存为类成员，以便 Styling
        self.btn_confirm_stop = PrimaryPushButton("确认")
        # 样式表将在 _apply_theme 中设置，以确保 theme toggle 时正确
        self.btn_confirm_stop.clicked.connect(self._force_stop)
        
        btn_cancel_stop = PushButton("取消")
        btn_cancel_stop.clicked.connect(self._hide_stop_confirm)
        
        stop_layout.addWidget(self.btn_confirm_stop)
        stop_layout.addWidget(btn_cancel_stop)
        header.addWidget(self.stop_confirm_widget)
        self.stop_confirm_widget.hide()
        
        layout.addLayout(header)
        
        self.progress_dashboard = ProgressDashboard()
        layout.addWidget(self.progress_dashboard)
        
        self.text_log = PlainTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setStyleSheet("font-family: Consolas; font-size: 12px;")
        self.text_log.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.text_log.customContextMenuRequested.connect(self._show_log_context_menu)
        layout.addWidget(self.text_log)
        return panel

    def _show_stop_confirm(self):
        self.btn_stop.hide()
        self.stop_confirm_widget.show()

    def _hide_stop_confirm(self):
        self.stop_confirm_widget.hide()
        self.btn_stop.show()

    # ==================== 逻辑功能实现 ====================
    def _toggle_theme(self):
        self.is_dark = not self.is_dark
        self.app_settings["theme_mode"] = "dark" if self.is_dark else "light"
        self._save_app_settings()
        self._apply_theme(self.is_dark)
        
    def _apply_theme(self, is_dark: bool):
        # 强制修正确认按钮的样式，确保红底白字
        stop_btn_style = "QPushButton { background-color: #d13438; border: 1px solid #d13438; color: white; border-radius: 4px; }"
        if self.btn_confirm_stop:
            self.btn_confirm_stop.setStyleSheet(stop_btn_style)

        # 【修改】延迟调用 setTheme 和 setThemeColor
        try:
            if is_dark:
                setTheme(Theme.DARK)
                setThemeColor("#FF9900")
            else:
                setTheme(Theme.LIGHT)
                setThemeColor("#009FAA")
        except Exception as e:
            print(f"Theme setting error: {e}")

        if is_dark:
            self.setStyleSheet("""
                QMainWindow { background-color: #303030; }
                QListWidget { background-color: #383838; border: 1px solid #454545; color: white; }
                QListWidget::item:selected { background-color: #454545; }
                QTextEdit, QPlainTextEdit, LineEdit, PlainLineEdit { 
                    background-color: #383838; color: white; border: 1px solid #454545; border-radius: 4px;
                }
                SubtitleLabel#MetricValue { color: #FF9900; }
                QLabel { color: white; }
            """)
            if hasattr(self, 'lbl_pass1'): 
                self.lbl_pass1.setStyleSheet("color: #FF9900; font-weight: bold;")
            if hasattr(self, 'lbl_pass2'): 
                self.lbl_pass2.setStyleSheet("color: #FF9900; font-weight: bold;")
        else:
            self.setStyleSheet("""
                QMainWindow { background-color: #f3f3f3; }
                SubtitleLabel#MetricValue { color: #0078D7; }
                QLabel { color: black; }
            """)
            if hasattr(self, 'lbl_pass1'): 
                self.lbl_pass1.setStyleSheet("color: #0078D7; font-weight: bold;")
            if hasattr(self, 'lbl_pass2'): 
                self.lbl_pass2.setStyleSheet("color: #0078D7; font-weight: bold;")

        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                value = c_int(1 if is_dark else 0)
                windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, byref(value), sizeof(value))
                self.repaint()
            except Exception:
                pass

    def _open_settings_dialog(self):
        dialog = SettingsDialog(self, self.app_settings, self.is_dark)
        dialog.exec()

    def _toggle_2pass_ui(self, state: int):
        is_2pass = (state == 2)
        self.lbl_crf.setVisible(not is_2pass)
        self.text_params_crf.setVisible(not is_2pass)
        self.lbl_pass1.setVisible(is_2pass)
        self.text_params_pass1.setVisible(is_2pass)
        self.lbl_pass2.setVisible(is_2pass)
        self.text_params_pass2.setVisible(is_2pass)

    def _browse_folder(self, line_edit: PlainLineEdit):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder: line_edit.setText(folder.replace("/", "\\"))

    # ==================== 参数切换与保存核心逻辑 ====================
    def _on_selector_changed(self, _=None):
        if not self.current_profile_name: return
        
        hw = self.seg_hardware.currentItem().text()
        codec = self.seg_codec.currentItem().text()
        
        self.profiles[self.current_profile_name]["selected_hw"] = hw
        self.profiles[self.current_profile_name]["selected_codec"] = codec
        self._save_profiles_to_disk()
        
        self._refresh_text_fields(hw, codec)

    def _refresh_text_fields(self, hw: str, codec: str):
        profile = self.profiles[self.current_profile_name]
        params_matrix = profile.get("param_matrix", DEFAULT_PARAMS_TEMPLATE)
        params = params_matrix.get(hw, {}).get(codec, {"crf": "", "pass1": "", "pass2": "", "enable_2pass": False})
        
        self.text_params_crf.blockSignals(True)
        self.text_params_pass1.blockSignals(True)
        self.text_params_pass2.blockSignals(True)
        self.chk_2pass.blockSignals(True)
        
        self.text_params_crf.setPlainText(params.get("crf", ""))
        self.text_params_pass1.setPlainText(params.get("pass1", ""))
        self.text_params_pass2.setPlainText(params.get("pass2", ""))
        
        # [修改] 从矩阵中读取 2-Pass 状态
        enable_2pass = params.get("enable_2pass", False)
        self.chk_2pass.setChecked(enable_2pass)
        self._toggle_2pass_ui(2 if enable_2pass else 0)
        
        self.text_params_crf.blockSignals(False)
        self.text_params_pass1.blockSignals(False)
        self.text_params_pass2.blockSignals(False)
        self.chk_2pass.blockSignals(False)

    def _save_current_params(self):
        """[修改] 保存当前参数文本以及 2-Pass 状态到矩阵"""
        if not self.current_profile_name: return
        
        hw = self.seg_hardware.currentItem().text()
        codec = self.seg_codec.currentItem().text()
        
        profile = self.profiles[self.current_profile_name]
        if "param_matrix" not in profile:
            profile["param_matrix"] = copy.deepcopy(DEFAULT_PARAMS_TEMPLATE)
            
        if hw not in profile["param_matrix"]: profile["param_matrix"][hw] = {}
        if codec not in profile["param_matrix"][hw]: profile["param_matrix"][hw][codec] = {}
        
        target = profile["param_matrix"][hw][codec]
        target["crf"] = self.text_params_crf.toPlainText()
        target["pass1"] = self.text_params_pass1.toPlainText()
        target["pass2"] = self.text_params_pass2.toPlainText()
        # [新增] 保存 2-Pass 状态
        target["enable_2pass"] = self.chk_2pass.isChecked()
        
        self._save_profiles_to_disk()

    def _save_current_profile_meta(self):
        if not self.current_profile_name: return
        
        profile = self.profiles[self.current_profile_name]
        profile.update({
            "source_dir": self.entry_source_dir.text(),
            "sub_dir": self.entry_sub_dir.text(),
            "output_dir": self.entry_output_dir.text(),
            "source_temp": self.entry_source_template.text(),
            "sub_sc": self.entry_sub_sc.text(),
            "sub_tc": self.entry_sub_tc.text(),
            "out_sc": self.entry_out_sc.text(),
            "out_tc": self.entry_out_tc.text(),
            "last_ep": self.entry_ep.text(),
            # [移除] enable_2pass 不再在元数据中全局保存，而是通过 matrix 保存
            # "enable_2pass": self.chk_2pass.isChecked() 
        })
        self._save_profiles_to_disk()

    # ==================== 配置管理 ====================
    def _add_profile_dialog(self):
        text, ok = QInputDialog.getText(self, "新建配置", "请输入配置名称:")
        if not ok or not text: return
        name = text.strip()
        if name in self.profiles:
            InfoBar.error(title="错误", content="配置名称已存在", parent=self)
            return
        
        defaults = self.app_settings.get("default_params_matrix", DEFAULT_PARAMS_TEMPLATE)
        profile_matrix = copy.deepcopy(defaults)
        
        self.profiles[name] = {
            "source_dir": "", "sub_dir": "", "output_dir": "",
            "source_temp": "Title - S01E<ep>.mkv",
            "sub_sc": "<ep>.zh-hans.ass", "sub_tc": "<ep>.zh-hant.ass",
            "out_sc": "Title - S01E<ep> - SC.mp4", "out_tc": "Title - S01E<ep> - TC.mp4",
            "param_matrix": profile_matrix, 
            "selected_hw": "CPU",
            "selected_codec": "X264",
            "last_ep": "01"
        }
        self._refresh_profile_list()
        self._select_profile(name)
        self._save_profiles_to_disk()
        InfoBar.success(title='成功', content=f"已创建: {name}", parent=self)

    def _show_profile_context_menu(self, pos):
        item = self.profile_list_widget.itemAt(pos)
        if not item: return
        menu = RoundMenu(parent=self)
        rename_action = Action(FIF.EDIT, '重命名')
        rename_action.triggered.connect(lambda: self._rename_profile(item.text()))
        menu.addAction(rename_action)
        delete_action = Action(FIF.DELETE, '删除配置')
        delete_action.triggered.connect(lambda: self._delete_profile(item.text()))
        menu.addAction(delete_action)
        menu.exec(self.profile_list_widget.mapToGlobal(pos))

    def _rename_profile(self, old_name: str):
        text, ok = QInputDialog.getText(self, "重命名配置", "请输入新名称:", text=old_name)
        if not ok or not text: return
        new_name = text.strip()
        if new_name == old_name: return
        if new_name in self.profiles:
            InfoBar.error(title="错误", content="该名称已存在", parent=self)
            return
        self.profiles[new_name] = self.profiles.pop(old_name)
        self.current_profile_name = new_name
        self._save_profiles_to_disk()
        self._refresh_profile_list()
        self._select_profile(new_name)

    def _delete_profile(self, name: str):
        msg = QMessageBox(self)
        msg.setWindowTitle("确认删除")
        msg.setText(f"确定要删除配置 [{name}] 吗？")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if msg.exec() != QMessageBox.StandardButton.Yes: return
        del self.profiles[name]
        self._refresh_profile_list()
        if self.current_profile_name == name:
            self.current_profile_name = None
            if self.profiles: self._select_profile(list(self.profiles.keys())[0])
            else: self._clear_inputs()
        self._save_profiles_to_disk()

    def _on_profile_selected(self, item: QListWidgetItem):
        self._select_profile(item.text())

    def _select_profile(self, name: str):
        self.current_profile_name = name
        data = self.profiles[name]
        
        self._block_signals(True)
        
        self.entry_source_dir.setText(data.get("source_dir", ""))
        self.entry_sub_dir.setText(data.get("sub_dir", ""))
        self.entry_output_dir.setText(data.get("output_dir", ""))
        self.entry_source_template.setText(data.get("source_temp", ""))
        self.entry_sub_sc.setText(data.get("sub_sc", ""))
        self.entry_sub_tc.setText(data.get("sub_tc", ""))
        self.entry_out_sc.setText(data.get("out_sc", ""))
        self.entry_out_tc.setText(data.get("out_tc", ""))
        self.entry_ep.setText(data.get("last_ep", "01"))
        
        sel_hw = data.get("selected_hw", "CPU")
        sel_codec = data.get("selected_codec", "X264")
        self.seg_hardware.setCurrentItem(sel_hw)
        self.seg_codec.setCurrentItem(sel_codec)
        
        # 2-Pass 状态现在由 _refresh_text_fields 从 matrix 中加载
        
        self._block_signals(False)
        self._refresh_text_fields(sel_hw, sel_codec)
        
        items = self.profile_list_widget.findItems(name, Qt.MatchFlag.MatchExactly)
        if items: self.profile_list_widget.setCurrentItem(items[0])

    def _block_signals(self, block: bool):
        widgets = [
            self.entry_source_dir, self.entry_sub_dir, self.entry_output_dir,
            self.entry_source_template, self.entry_sub_sc, self.entry_sub_tc,
            self.entry_out_sc, self.entry_out_tc, self.entry_ep,
            self.text_params_crf, self.text_params_pass1, self.text_params_pass2,
            self.chk_2pass
        ]
        for widget in widgets: widget.blockSignals(block)

    def _refresh_profile_list(self):
        self.profile_list_widget.clear()
        for name in self.profiles: self.profile_list_widget.addItem(QListWidgetItem(name))

    def _clear_inputs(self):
        self._block_signals(True)
        self.entry_source_dir.clear()
        self.entry_sub_dir.clear()
        self.entry_output_dir.clear()
        self.entry_source_template.clear()
        self.entry_sub_sc.clear()
        self.entry_sub_tc.clear()
        self.entry_out_sc.clear()
        self.entry_out_tc.clear()
        self.entry_ep.clear()
        self.text_params_crf.clear()
        self.text_params_pass1.clear()
        self.text_params_pass2.clear()
        self.chk_2pass.setChecked(False)
        self._block_signals(False)

    def _load_profiles(self):
        if not os.path.exists(self.profile_file): return
        try:
            with open(self.profile_file, 'r', encoding='utf-8') as f:
                self.profiles = json.load(f)
            
            for pname, pdata in self.profiles.items():
                if "param_matrix" not in pdata:
                    new_matrix = copy.deepcopy(DEFAULT_PARAMS_TEMPLATE)
                    if "params_crf" in pdata:
                        # 迁移旧数据
                        new_matrix["CPU"]["X264"] = {
                            "crf": pdata.get("params_crf", ""),
                            "pass1": pdata.get("params_pass1", ""),
                            "pass2": pdata.get("params_pass2", ""),
                            "enable_2pass": pdata.get("enable_2pass", False)
                        }
                    pdata["param_matrix"] = new_matrix
                    pdata["selected_hw"] = "CPU"
                    pdata["selected_codec"] = "X264"
                else:
                    # 确保现有 matrix 结构包含 enable_2pass
                    for hw in pdata["param_matrix"]:
                        for codec in pdata["param_matrix"][hw]:
                            if "enable_2pass" not in pdata["param_matrix"][hw][codec]:
                                pdata["param_matrix"][hw][codec]["enable_2pass"] = False

            self._refresh_profile_list()
            if self.profiles: self._select_profile(list(self.profiles.keys())[0])
        except Exception as e:
            InfoBar.error(title="错误", content=f"加载配置失败: {e}", parent=self)

    def _save_profiles_to_disk(self):
        try:
            with open(self.profile_file, 'w', encoding='utf-8') as f:
                json.dump(self.profiles, f, ensure_ascii=False, indent=4)
        except Exception as e:
            InfoBar.error(title="错误", content=f"保存配置失败: {e}", parent=self)

    # ==================== 队列管理 ====================
    
    # [新增] 批量添加弹窗调用
    def _open_batch_dialog(self):
        popup = BatchAddPopup(self)
        # 获取按钮位置以便对齐（可选，或者直接显示）
        # 这里直接 exec (虽然是 Popup 属性，但 QDialog.exec 会阻塞)
        # 由于要求无 Cancel/OK 且点外关闭，使用 show() 并依赖 WindowModality 或 Popup 行为
        popup.show()
        # 移动到鼠标附近或屏幕中心
        center = self.geometry().center()
        popup.move(center.x() - popup.width() // 2, center.y() - popup.height() // 2)

    # [新增] 批量回调
    def _batch_add_callback(self, ep_list, mode, suffix):
        if not ep_list: return
        for ep in ep_list:
            self._add_task_internal(ep, mode, suffix)
        self.queue_list.scrollToBottom()

    def _add_tasks_from_ui(self, mode: str):
        """UI 按钮触发的单集添加"""
        ep = self.entry_ep.text().strip()
        if not ep:
            InfoBar.warning(title="警告", content="请输入集数", parent=self)
            return
        
        use_suffix = self.chk_suffix.isChecked()
        suffix_text = self.entry_suffix.text().strip() if use_suffix else ""
        
        self._add_task_internal(ep, mode, suffix_text)
        self.queue_list.scrollToBottom()

    def _add_task_internal(self, ep: str, mode: str, suffix_text: str):
        if not self.current_profile_name:
            InfoBar.warning(title="警告", content="请先选择一个配置", parent=self)
            return
            
        profile = copy.deepcopy(self.profiles[self.current_profile_name])
        
        tasks = []
        if mode in ["SC", "BOTH"]: tasks.append({"type": "SC", "ep": ep, "profile": profile, "suffix": suffix_text})
        if mode in ["TC", "BOTH"]: tasks.append({"type": "TC", "ep": ep, "profile": profile, "suffix": suffix_text})
        
        for task in tasks:
            display_suffix = f" {task['suffix']}" if task['suffix'] else ""
            
            # 这里的 2-Pass 状态从选定的 HW/Codec matrix 中获取，而不是全局
            hw = profile.get("selected_hw", "CPU")
            codec = profile.get("selected_codec", "X264")
            p_matrix = profile.get("param_matrix", DEFAULT_PARAMS_TEMPLATE)
            is_2pass = p_matrix.get(hw, {}).get(codec, {}).get("enable_2pass", False)
            
            mode_tag = " [2-Pass]" if is_2pass else ""
            desc = f"[{task['type']}] EP{task['ep']}{display_suffix}{mode_tag} - {self.current_profile_name}"
            
            item_data = {
                "id": f"{datetime.now().timestamp()}",
                "desc": desc, "status": "pending", "raw_task": task
            }
            
            list_item = QListWidgetItem()
            list_item.setData(Qt.ItemDataRole.UserRole, item_data)
            self.queue_list.addItem(list_item)
            
            item_widget = TaskItemWidget(desc)
            list_item.setSizeHint(item_widget.sizeHint())
            self.queue_list.setItemWidget(list_item, item_widget)
            
            if not task['suffix']:
                key = (task['ep'], task['type'])
                self.completed_history.discard(key)

    # [新增] 处理列表项拖拽放置事件
    def _on_queue_item_dropped(self):
        """列表项位置改变后，重建 Widget"""
        for i in range(self.queue_list.count()):
            item = self.queue_list.item(i)
            # 检查是否有 Widget，如果没有则重建
            if self.queue_list.itemWidget(item) is None:
                data = item.data(Qt.ItemDataRole.UserRole)
                if data:
                    widget = TaskItemWidget(data['desc'])
                    # 恢复状态
                    if data['status'] == 'done':
                        widget.set_status(progress=1.0, status_code=2)
                    elif data['status'] == 'error':
                         widget.set_status(progress=0.0, status_code=3)
                    elif data['status'] == 'running':
                        # 如果正在运行却被拖动了，理论上应该禁止，但这里做恢复
                        # 实际上运行中的任务被拖动可能会导致索引错乱，
                        # 但由于我们基于 current_item 对象操作，只要 current_item 引用还在就行
                        widget.set_status(progress=0.0, status_code=1) # 进度可能丢失显示，直到下次刷新
                    
                    item.setSizeHint(widget.sizeHint())
                    self.queue_list.setItemWidget(item, widget)

    def _show_queue_context_menu(self, pos):
        item = self.queue_list.itemAt(pos)
        if not item: return
        menu = RoundMenu(parent=self)
        delete_action = Action(FIF.DELETE, '移除任务')
        delete_action.triggered.connect(lambda: self._remove_queue_item(item))
        menu.addAction(delete_action)
        menu.exec(self.queue_list.mapToGlobal(pos))

    def _show_log_context_menu(self, pos):
        menu = RoundMenu(parent=self)
        clear_action = Action(FIF.DELETE, '清空日志')
        clear_action.triggered.connect(self.text_log.clear)
        menu.addAction(clear_action)
        menu.exec(self.text_log.mapToGlobal(pos))

    def _remove_queue_item(self, item: QListWidgetItem):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data['status'] == 'running':
            InfoBar.error(title="错误", content="无法移除正在运行的任务，请先强制终止", parent=self)
            return
        self.queue_list.takeItem(self.queue_list.row(item))

    def _clear_queue(self):
        if self.is_running:
            InfoBar.error(title="错误", content="压制进行中，无法清空", parent=self)
            return
        self.queue_list.clear()

    # ==================== 压制流程 ====================
    def _start_processing(self):
        if self.is_running: return
        if self.queue_list.count() == 0:
            InfoBar.info(title="提示", content="队列为空", parent=self)
            return
        self.is_running = True
        self.force_stop_flag = False
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._hide_stop_confirm()
        self.progress_dashboard.reset()
        self._process_next_task()

    def _process_next_task(self):
        if self.force_stop_flag: return
        target_item = None
        for i in range(self.queue_list.count()):
            item = self.queue_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data['status'] == 'pending':
                target_item = item
                break
        if not target_item:
            self._all_tasks_finished()
            return
        
        self.current_item = target_item
        data = target_item.data(Qt.ItemDataRole.UserRole)
        data['status'] = 'running'
        target_item.setData(Qt.ItemDataRole.UserRole, data)
        
        # 更新 Widget 状态 (Running, status_code=1)
        widget = self.queue_list.itemWidget(target_item)
        if isinstance(widget, TaskItemWidget):
            widget.set_status(progress=0.0, pass_idx=0, status_code=1)
            
        self.queue_list.scrollToItem(target_item)
        self._run_ffmpeg(data['raw_task'])

    def _run_ffmpeg(self, task: Dict):
        profile = task['profile']
        ep = task['ep']
        task_type = task['type']
        suffix = task.get('suffix', "")
        
        hw = profile.get("selected_hw", "CPU")
        codec = profile.get("selected_codec", "X264")
        params_matrix = profile.get("param_matrix", DEFAULT_PARAMS_TEMPLATE)
        params_set = params_matrix.get(hw, {}).get(codec, {"crf": "", "pass1": "", "pass2": "", "enable_2pass": False})
        
        src_name = profile['source_temp'].replace('<ep>', ep)
        full_src = os.path.join(profile['source_dir'], src_name)
        
        if task_type == "SC":
            sub_name = profile['sub_sc'].replace('<ep>', ep)
            out_name = profile['out_sc'].replace('<ep>', ep)
        else:
            sub_name = profile['sub_tc'].replace('<ep>', ep)
            out_name = profile['out_tc'].replace('<ep>', ep)
        
        if suffix:
            root, ext = os.path.splitext(out_name)
            out_name = f"{root}{suffix}{ext}"
        
        full_out = os.path.join(profile['output_dir'], out_name)
        full_sub = os.path.join(profile['sub_dir'], sub_name)
        
        if not os.path.exists(full_src) or not os.path.exists(full_sub):
            self._log_to_ui(f"❌ 文件缺失: {src_name} 或 {sub_name}")
            self._task_finished_callback(False, full_out, "0s")
            return
        
        # [修改] 读取 matrix 中的 2-Pass 设置
        is_2pass = params_set.get("enable_2pass", False)
        
        commands = self._build_ffmpeg_commands(
            params_set, full_src, full_sub, sub_name, full_out, is_2pass, profile['sub_dir']
        )
        temp_files = self._get_temp_files(profile['sub_dir']) if is_2pass else []
        self.progress_dashboard.start_timer()
        self.worker = Worker(commands, profile['sub_dir'], full_out, temp_files)
        self.worker.log_signal.connect(self._log_to_ui)
        self.worker.progress_signal.connect(self._update_progress_text)
        self.worker.percent_signal.connect(self._update_progress_percent)
        self.worker.finished_signal.connect(self._task_finished_callback)
        self.worker.start()

    def _build_ffmpeg_commands(self, params_set: Dict, full_src: str, 
                               full_sub: str, sub_name: str, full_out: str, is_2pass: bool, sub_dir: str) -> List[str]:
        commands = []
        if is_2pass:
            params_1 = self._sanitize_params(params_set.get("pass1", ""))
            params_2 = self._sanitize_params(params_set.get("pass2", ""))
            has_pass1 = "-pass 1" in params_1 or "-pass=1" in params_1
            has_pass2 = "-pass 2" in params_2 or "-pass=2" in params_2
            has_passlog1 = "-passlogfile" in params_1
            has_passlog2 = "-passlogfile" in params_2
            pass_log_path = os.path.join(sub_dir, "ffmpeg2pass")
            pass1_prefix = "" if has_pass1 else "-pass 1 "
            passlog1_prefix = "" if has_passlog1 else f'-passlogfile "{pass_log_path}" '
            cmd1 = (f'ffmpeg -y -i "{full_src}" -vf "subtitles=\'{sub_name}\'" '
                   f'{passlog1_prefix}{pass1_prefix}{params_1}')
            commands.append(cmd1)
            pass2_prefix = "" if has_pass2 else "-pass 2 "
            passlog2_prefix = "" if has_passlog2 else f'-passlogfile "{pass_log_path}" '
            cmd2 = (f'ffmpeg -y -i "{full_src}" -vf "subtitles=\'{sub_name}\'" '
                   f'{passlog2_prefix}{pass2_prefix}{params_2} "{full_out}"')
            commands.append(cmd2)
        else:
            params_crf = self._sanitize_params(params_set.get("crf", ""))
            cmd = f'ffmpeg -y -i "{full_src}" -vf "subtitles=\'{sub_name}\'" {params_crf} "{full_out}"'
            commands.append(cmd)
        return commands

    def _sanitize_params(self, param_text: str) -> str:
        if not param_text: return ""
        return param_text.replace('\n', ' ').replace('\r', '').strip()

    def _get_temp_files(self, sub_dir: str) -> List[str]:
        pass_log_path = os.path.join(sub_dir, "ffmpeg2pass")
        return [f"{pass_log_path}-0.log", f"{pass_log_path}-0.log.mbtree"]

    def _log_to_ui(self, text: str):
        self.text_log.append(text)
        scrollbar = self.text_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _update_progress_text(self, text: str):
        self.progress_dashboard.update_data(text)

    def _update_progress_percent(self, percent: float, pass_idx: int):
        if self.current_item:
            widget = self.queue_list.itemWidget(self.current_item)
            if isinstance(widget, TaskItemWidget):
                # 更新进度条 (Running status_code=1)
                widget.set_status(progress=percent, pass_idx=pass_idx, status_code=1)

    def _task_finished_callback(self, success: bool, output_file: str, duration: str):
        if self.force_stop_flag:
            self._handle_forced_cleanup(output_file)
            return
        if not self.current_item: return
        data = self.current_item.data(Qt.ItemDataRole.UserRole)
        task_info = data['raw_task']
        
        widget = self.queue_list.itemWidget(self.current_item)
        
        if success:
            data['status'] = 'done'
            if isinstance(widget, TaskItemWidget):
                # Done status_code=2
                widget.set_status(progress=1.0, status_code=2)
            
            if not task_info.get('suffix'):
                self.completed_history.add((task_info['ep'], task_info['type']))
                self._check_auto_increment(task_info['ep'])
        else:
            data['status'] = 'error'
            if isinstance(widget, TaskItemWidget):
                # Error status_code=3
                widget.set_status(progress=0.0, status_code=3)
                
        self.current_item.setData(Qt.ItemDataRole.UserRole, data)
        self.current_item = None
        self.worker = None
        self.progress_dashboard.reset()
        if self.is_running: self._process_next_task()

    def _check_auto_increment(self, ep: str):
        if (ep, 'SC') in self.completed_history and (ep, 'TC') in self.completed_history:
            self._log_to_ui(f"ℹ️ 集数 {ep} 双语完成，自动 +1")
            current = self.entry_ep.text()
            if current == ep:
                try:
                    next_ep = str(int(ep) + 1).zfill(len(ep))
                    self.entry_ep.setText(next_ep)
                except ValueError: pass

    def _all_tasks_finished(self):
        self.is_running = False
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._hide_stop_confirm()
        self._log_to_ui("=== 队列完成 ===")
        InfoBar.success(title="完成", content="所有任务已结束", parent=self)

    def _force_stop(self):
        self._hide_stop_confirm()
        if self.worker and self.is_running:
            self._log_to_ui("⚠️ 正在强制终止进程...")
            self.force_stop_flag = True
            self.worker.stop()

    def _handle_forced_cleanup(self, output_file: str):
        self.is_running = False
        self.worker = None
        if os.path.exists(output_file):
            try:
                os.remove(output_file)
                self._log_to_ui(f"🗑️ 已删除未完成文件: {output_file}")
            except: pass
        if self.current_item:
            try:
                data = self.current_item.data(Qt.ItemDataRole.UserRole)
                data['status'] = 'error'
                widget = self.queue_list.itemWidget(self.current_item)
                if isinstance(widget, TaskItemWidget):
                    widget.set_status(progress=0.0, status_code=3) # Error color
            except: pass
        self.current_item = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress_dashboard.reset()
        self.queue_list.clear()
        self._log_to_ui("=== 队列已清空 ===")

    def closeEvent(self, event):
        # 保存窗口几何信息
        rect = self.geometry().getRect()
        self.app_settings["window_geometry"] = rect
        self._save_app_settings()
        
        if self.is_running:
            reply = QMessageBox.question(
                self, '退出', '任务运行中，确定强制退出？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._force_stop()
                event.accept()
            else: event.ignore()
        else:
            self._save_current_profile_meta()
            event.accept()

# ==================== 程序入口 ====================
def main():
    app = QApplication(sys.argv)
    window = EncodeManager()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
