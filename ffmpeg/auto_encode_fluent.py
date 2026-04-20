import sys
import os
import json
import subprocess
import re
import time
import glob
import copy
import ctypes
import logging
import traceback
from ctypes import windll, byref, c_int, sizeof
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QMimeData, QRect, QSize, QPoint, qInstallMessageHandler
from PyQt6.QtGui import QColor, QAction, QPalette, QPainter, QBrush, QLinearGradient, QPainterPath, QCursor
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QFileDialog, QFrame, QListWidgetItem, QAbstractItemView,
    QSplitter, QGridLayout, QMessageBox, QInputDialog, QDialog, 
    QStackedWidget, QDialogButtonBox, QLabel, QButtonGroup,
    QSizePolicy, QListView, QSystemTrayIcon, QMenu, QScrollArea
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

# 默认工具路径
DEFAULT_TOOL_PATHS = {
    "mkvmerge_path": "",
    "assfontsubset_path": ""
}

# ==================== 日志系统 ====================
def setup_logger(config_dir: str, log_dir_name: str = "log") -> logging.Logger:
    """初始化日志系统，返回logger对象"""
    log_dir = os.path.join(config_dir, log_dir_name)
    if not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception:
            pass
    
    log_file = os.path.join(log_dir, f"encode_{datetime.now().strftime('%Y%m%d')}.log")
    
    logger = logging.getLogger("EncodeManager")
    logger.setLevel(logging.DEBUG)
    
    if logger.handlers:
        return logger
    
    try:
        file_handler = logging.FileHandler(log_file, encoding='utf-8', mode='a')
        file_handler.setLevel(logging.DEBUG)
        
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
    except Exception:
        pass
    
    return logger

def get_logger() -> logging.Logger:
    """获取logger对象"""
    return logging.getLogger("EncodeManager")

# ==================== 单实例检测 ====================
SINGLE_INSTANCE_KEY = "smzase_encode_single_instance_2024"

class SingleInstance:
    """Windows单实例检测类，使用互斥体"""
    def __init__(self):
        self.mutex = None
        self._is_running = False
        
    def try_lock(self):
        """尝试获取单实例锁，返回True表示是第一个实例"""
        try:
            self.mutex = windll.kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_KEY)
            last_error = windll.kernel32.GetLastError()
            if last_error == 183:
                self._is_running = True
                return False
            self._is_running = False
            return True
        except Exception:
            return True
    
    def is_running(self):
        return self._is_running
    
    def release(self):
        if self.mutex:
            windll.kernel32.ReleaseMutex(self.mutex)
            windll.kernel32.CloseHandle(self.mutex)
            self.mutex = None

class SingleInstanceServer:
    """单实例服务端，用于接收其他实例的通知"""
    def __init__(self, callback):
        self.server = QLocalServer()
        self.callback = callback
        QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
        if self.server.listen(SINGLE_INSTANCE_KEY):
            self.server.newConnection.connect(self._on_new_connection)
    
    def _on_new_connection(self):
        socket = self.server.nextPendingConnection()
        if socket:
            socket.waitForReadyRead(1000)
            data = socket.readAll().data()
            if data == b"SHOW_WINDOW":
                self.callback()
            socket.disconnectFromServer()
    
    def close(self):
        self.server.close()

def notify_existing_instance():
    """通知已存在的实例显示窗口"""
    socket = QLocalSocket()
    socket.connectToServer(SINGLE_INSTANCE_KEY)
    if socket.waitForConnected(1000):
        socket.write(b"SHOW_WINDOW")
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return True
    return False

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
        
        btn_no_sub = PushButton("无字幕")
        btn_no_sub.clicked.connect(lambda: self._confirm("NO_SUB"))

        btn_layout.addWidget(btn_sc)
        btn_layout.addWidget(btn_tc)
        btn_layout.addWidget(btn_both)
        btn_layout.addWidget(btn_no_sub)
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
        try:
            painter = QPainter(self)
            if not painter.isActive():
                return
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
            
            painter.end()
        except RuntimeError:
            # Widget 的 C++ 对象已被销毁
            pass

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
        self.setMinimumHeight(400)
        self.setMinimumWidth(800)
        
        saved_size = current_settings.get("settings_dialog_size", None) if current_settings else None
        if saved_size and len(saved_size) == 2:
            self.resize(saved_size[0], saved_size[1])
        else:
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
        self.chk_auto_ep_encode = CheckBox("压制完成后集数自动 +1")
        self.chk_auto_ep_mux = CheckBox("封装完成后集数自动 +1")
        self.chk_ep_not_shared = CheckBox("两边集数不共享")
        
        from qfluentwidgets import RadioButton
        self.radio_close_to_tray = RadioButton("最小化到托盘")
        self.radio_close_exit = RadioButton("关闭工具")

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
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.addWidget(self._create_general_page())
        
        for hw in HARDWARE_LIST:
            page = ParamEditorPage(hw, self.param_matrix)
            self.stacked_widget.addWidget(page)
        
        scroll_area.setWidget(self.stacked_widget)
        right_layout.addWidget(scroll_area)
        
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
        
        # 工具路径配置
        layout.addWidget(SubtitleLabel("工具路径配置"))
        
        tool_card = CardWidget()
        tool_grid = QGridLayout(tool_card)
        tool_grid.setContentsMargins(20, 20, 20, 20)
        
        tool_grid.addWidget(StrongBodyLabel("MKVToolNix 路径"), 0, 0)
        self.input_mkvmerge_path = PlainLineEdit()
        tool_grid.addWidget(self.input_mkvmerge_path, 1, 0)
        btn_browse_mkv = PushButton(FIF.FOLDER, "浏览")
        btn_browse_mkv.clicked.connect(lambda: self._browse_file(self.input_mkvmerge_path, "mkvmerge.exe"))
        tool_grid.addWidget(btn_browse_mkv, 1, 1)
        
        tool_grid.addWidget(StrongBodyLabel("AssFontSubset 路径"), 2, 0)
        self.input_assfontsubset_path = PlainLineEdit()
        tool_grid.addWidget(self.input_assfontsubset_path, 3, 0)
        btn_browse_ass = PushButton(FIF.FOLDER, "浏览")
        btn_browse_ass.clicked.connect(lambda: self._browse_file(self.input_assfontsubset_path, "AssFontSubset.Console.exe"))
        tool_grid.addWidget(btn_browse_ass, 3, 1)
        
        layout.addWidget(tool_card)
        
        layout.addWidget(SubtitleLabel("自动化设置"))
        
        auto_card = CardWidget()
        auto_layout = QVBoxLayout(auto_card)
        auto_layout.setContentsMargins(20, 15, 20, 15)
        auto_layout.setSpacing(10)
        
        self.chk_auto_ep_encode.setChecked(True)
        auto_layout.addWidget(self.chk_auto_ep_encode)
        
        self.chk_auto_ep_mux.setChecked(True)
        auto_layout.addWidget(self.chk_auto_ep_mux)
        
        self.chk_ep_not_shared.setToolTip("启用后，\"有字幕\"和\"无字幕\"模式的集数独立计数；禁用时，一方压制完成会同步更新另一方的集数")
        auto_layout.addWidget(self.chk_ep_not_shared)
        
        layout.addWidget(auto_card)
        
        # 关闭行为设置
        layout.addWidget(SubtitleLabel("关闭行为"))
        
        close_card = CardWidget()
        close_layout = QVBoxLayout(close_card)
        close_layout.setContentsMargins(20, 15, 20, 15)
        close_layout.setSpacing(10)
        
        self.close_btn_group = QButtonGroup(self)
        self.close_btn_group.addButton(self.radio_close_to_tray, 0)
        self.close_btn_group.addButton(self.radio_close_exit, 1)
        
        close_layout.addWidget(self.radio_close_to_tray)
        close_layout.addWidget(self.radio_close_exit)
        
        layout.addWidget(close_card)
        
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

    def _browse_file(self, line_edit: PlainLineEdit, file_filter: str):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", f"{file_filter} (*.exe)")
        if file_path:
            line_edit.setText(file_path.replace("/", "\\"))

    def _load_general_data(self):
        self.input_base_path.setText(self.raw_settings.get("base_path", ""))
        self.chk_use_folder.setChecked(self.raw_settings.get("use_sub_folder", True))
        self.input_folder_name.setText(self.raw_settings.get("folder_name", "ffmpeg smzase"))
        self.input_folder_name.setEnabled(self.chk_use_folder.isChecked())
        self.input_mkvmerge_path.setText(self.raw_settings.get("mkvmerge_path", ""))
        self.input_assfontsubset_path.setText(self.raw_settings.get("assfontsubset_path", ""))
        self.chk_auto_ep_encode.setChecked(self.raw_settings.get("auto_ep_encode", True))
        self.chk_auto_ep_mux.setChecked(self.raw_settings.get("auto_ep_mux", True))
        self.chk_ep_not_shared.setChecked(self.raw_settings.get("ep_not_shared", False))
        
        close_behavior = self.raw_settings.get("close_behavior", "tray")
        if close_behavior == "exit":
            self.radio_close_exit.setChecked(True)
        else:
            self.radio_close_to_tray.setChecked(True)

    def get_current_settings_dict(self) -> Dict:
        settings = copy.deepcopy(self.raw_settings)
        settings.update({
            "base_path": self.input_base_path.text(),
            "use_sub_folder": self.chk_use_folder.isChecked(),
            "folder_name": self.input_folder_name.text(),
            "default_params_matrix": self.param_matrix,
            "mkvmerge_path": self.input_mkvmerge_path.text(),
            "assfontsubset_path": self.input_assfontsubset_path.text(),
            "auto_ep_encode": self.chk_auto_ep_encode.isChecked(),
            "auto_ep_mux": self.chk_auto_ep_mux.isChecked(),
            "ep_not_shared": self.chk_ep_not_shared.isChecked(),
            "close_behavior": "exit" if self.radio_close_exit.isChecked() else "tray",
            "settings_dialog_size": [self.width(), self.height()]
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
        self.logger = get_logger()

    def run(self):
        self.start_time = time.time()
        self.logger.info(f"[Worker] 开始压制任务 - 输出文件: {self.output_file}")
        self.logger.info(f"[Worker] 工作目录: {self.work_dir}")
        self.logger.debug(f"[Worker] 命令列表: {self.commands}")
        self.log_signal.emit(f"Working Directory: {self.work_dir}")
        
        total_steps = len(self.commands)
        
        for index, cmd in enumerate(self.commands):
            if self.is_killed: break
            
            step_num = 0
            if total_steps > 1:
                step_num = index + 1 # 1 for Pass 1, 2 for Pass 2
                step_name = 'Pass 1' if index == 0 else 'Pass 2'
                self.logger.info(f"[Worker] 执行步骤 {index + 1}/{total_steps}: {step_name}")
                self.log_signal.emit(f"🔄 正在执行 [Step {index + 1}/{total_steps}]: {step_name}")
            else:
                step_num = 2 # treat single pass as "final" pass for color purpose (Green)
            
            self.logger.debug(f"[Worker] 执行命令: {cmd}")
            self.log_signal.emit(f"Executing: {cmd}")
            
            if not self._execute_command(cmd, step_num):
                self._cleanup()
                duration = self._get_duration_str()
                self.logger.error(f"[Worker] 压制失败 - 输出文件: {self.output_file}, 耗时: {duration}")
                self.finished_signal.emit(False, self.output_file, duration)
                return

        self._cleanup()
        duration = self._get_duration_str()
        
        if self.is_killed:
            self.logger.warning(f"[Worker] 任务被用户终止 - 输出文件: {self.output_file}, 耗时: {duration}")
            self.log_signal.emit("🔴 [用户操作] 进程已强制终止")
            self.finished_signal.emit(False, self.output_file, duration)
        else:
            self.logger.info(f"[Worker] 压制完成 - 输出文件: {self.output_file}, 耗时: {duration}")
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
            
            self.total_duration_sec = 0.0
            
            while True:
                if self.is_killed: break
                try:
                    line = self.process.stdout.readline()
                except (OSError, ValueError):
                    break
                if not line and self.process.poll() is not None: break
                if line:
                    try:
                        self._process_output_line(line.strip(), pass_index)
                    except Exception as e:
                        self.logger.debug(f"[Worker] 解析输出行异常: {e}")

            try:
                return_code = self.process.poll()
            except Exception:
                return False
            if self.is_killed: return False
            if return_code == 0: return True
            self.logger.error(f"[Worker] FFmpeg 异常退出，代码: {return_code}")
            self.log_signal.emit(f"🔴 [错误] FFmpeg 异常退出，代码 {return_code}")
            return False
        except Exception as e:
            self.logger.error(f"[Worker] 无法启动进程: {str(e)}\n{traceback.format_exc()}")
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


# ==================== MKV 提取工作线程 ====================
class MKVExtractWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)
    
    def __init__(self, mkvmerge_path: str, command: str, work_dir: str):
        super().__init__()
        self.mkvmerge_path = mkvmerge_path
        self.command = command
        self.work_dir = work_dir
        self.process: Optional[subprocess.Popen] = None
        self.is_killed = False
        self.logger = get_logger()

    def run(self):
        self.logger.info(f"[MKVExtract] 开始MKV提取 - 工作目录: {self.work_dir}")
        self.logger.debug(f"[MKVExtract] 命令: {self.command}")
        self.log_signal.emit(f"Working Directory: {self.work_dir}")
        self.log_signal.emit(f"Executing: {self.command}")
        
        if not self._execute_command():
            self.logger.error(f"[MKVExtract] MKV提取失败")
            self.finished_signal.emit(False, "MKV 提取失败")
            return
        
        self.logger.info(f"[MKVExtract] MKV提取完成")
        self.log_signal.emit("🟢 MKV 提取完成")
        self.finished_signal.emit(True, "MKV 提取成功")

    def _execute_command(self) -> bool:
        try:
            full_cmd = f'"{self.mkvmerge_path}" {self.command}'
            self.process = subprocess.Popen(
                full_cmd, cwd=self.work_dir, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding='utf-8', errors='replace', text=True, bufsize=1
            )
            
            while True:
                if self.is_killed: break
                line = self.process.stdout.readline()
                if not line and self.process.poll() is not None: break
                if line: self.log_signal.emit(line.strip())

            return_code = self.process.poll()
            if self.is_killed:
                self.logger.warning(f"[MKVExtract] 任务被用户终止")
                return False
            if return_code != 0:
                self.logger.error(f"[MKVExtract] 进程异常退出，代码: {return_code}")
            return return_code == 0
        except Exception as e:
            self.logger.error(f"[MKVExtract] 系统异常: {str(e)}\n{traceback.format_exc()}")
            self.log_signal.emit(f"🔴 [系统异常] {str(e)}")
            return False

    def stop(self):
        self.is_killed = True
        if self.process:
            try: subprocess.run(f"taskkill /F /T /PID {self.process.pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except: pass


# ==================== 字体子集化工作线程 ====================
class FontSubsetWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)
    
    def __init__(self, assfontsubset_path: str, subtitle_dir: str, font_dir: str):
        super().__init__()
        self.assfontsubset_path = assfontsubset_path
        self.subtitle_dir = subtitle_dir
        self.font_dir = font_dir
        self.process: Optional[subprocess.Popen] = None
        self.is_killed = False
        self.logger = get_logger()

    def run(self):
        self.logger.info(f"[FontSubset] 开始字体子集化 - 字幕目录: {self.subtitle_dir}")
        self.logger.info(f"[FontSubset] 字体目录: {self.font_dir}")
        self.log_signal.emit(f"字幕目录: {self.subtitle_dir}")
        self.log_signal.emit(f"字体目录: {self.font_dir}")
        
        if not self._execute_command():
            self.logger.error(f"[FontSubset] 字体子集化失败")
            self.finished_signal.emit(False, "字体子集化失败")
            return
        
        self.logger.info(f"[FontSubset] 字体子集化完成")
        self.log_signal.emit("🟢 字体子集化完成")
        self.finished_signal.emit(True, "字体子集化成功")

    def _execute_command(self) -> bool:
        try:
            ass_files = glob.glob(os.path.join(self.subtitle_dir, "*.ass"))
            
            if not ass_files:
                self.logger.warning(f"[FontSubset] 未找到 .ass 文件")
                self.log_signal.emit("⚠️ 未找到 .ass 文件")
                return False
            
            cmd = [self.assfontsubset_path] + ass_files + ["--fonts", self.font_dir]
            self.logger.debug(f"[FontSubset] 处理 {len(ass_files)} 个字幕文件")
            self.log_signal.emit(f"处理 {len(ass_files)} 个字幕文件...")
            
            self.process = subprocess.Popen(
                cmd,
                cwd=self.subtitle_dir,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding='utf-8', errors='replace', text=True, bufsize=1
            )
            
            while True:
                if self.is_killed:
                    self.logger.warning(f"[FontSubset] 任务被用户终止")
                    self.process.terminate()
                    return False
                line = self.process.stdout.readline()
                if not line and self.process.poll() is not None:
                    break
                if line:
                    self.log_signal.emit(line.strip())
            
            return_code = self.process.poll()
            if return_code != 0:
                self.logger.error(f"[FontSubset] 处理失败，返回码: {return_code}")
                self.log_signal.emit(f"⚠️ 处理失败，返回码: {return_code}")
                return False
            
            return True
        except Exception as e:
            self.logger.error(f"[FontSubset] 系统异常: {str(e)}\n{traceback.format_exc()}")
            self.log_signal.emit(f"🔴 [系统异常] {str(e)}")
            return False

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
        self._load_app_icon()
        
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
        
        self.logger = setup_logger(self.config_dir)
        self.logger.info("=" * 50)
        self.logger.info("应用程序启动")
        self.logger.info(f"配置目录: {self.config_dir}")
        
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
        
        # 初始化系统托盘
        self._init_tray_icon()
        
        # 初始化单实例服务端
        self._single_instance_server = SingleInstanceServer(self._tray_show_window)
        
        self._load_profiles()

    def _load_app_icon(self):
        """加载应用程序图标"""
        from PyQt6.QtGui import QIcon
        icon_paths = [
            os.path.join(os.path.dirname(__file__), "konoha.ico"),
            os.path.join(os.getcwd(), "konoha.ico"),
            "konoha.ico"
        ]
        for icon_path in icon_paths:
            if os.path.exists(icon_path):
                icon = QIcon(icon_path)
                if not icon.isNull():
                    self.setWindowIcon(icon)
                    return
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor(0, 159, 170))
        self.setWindowIcon(QIcon(pixmap))

    def _init_tray_icon(self):
        """初始化系统托盘图标"""
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.windowIcon())
        self.tray_icon.setToolTip("smzase 自用压制工具")
        
        # 托盘菜单
        tray_menu = QMenu(self)
        action_show = QAction("显示主窗口", self)
        action_show.triggered.connect(self._tray_show_window)
        tray_menu.addAction(action_show)
        
        tray_menu.addSeparator()
        
        action_quit = QAction("退出", self)
        action_quit.triggered.connect(self._tray_quit)
        tray_menu.addAction(action_quit)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)

    def _tray_show_window(self):
        """从托盘恢复主窗口"""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _on_tray_activated(self, reason):
        """托盘图标被激活（双击或单击）"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # 单击托盘图标
            self._tray_show_window()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._tray_show_window()

    def _tray_quit(self):
        """从托盘菜单退出程序"""
        self._force_quit = True
        self.close()

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
            "theme_mode": "light",
            "mkvmerge_path": "",
            "assfontsubset_path": "",
            "last_workflow": "encode",
            "close_behavior": "tray"
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
        
        # 堆叠窗口用于切换工作流程
        self.stacked_workflow = QStackedWidget()
        self.stacked_workflow.addWidget(self._create_encode_panel())  # Index 0
        self.stacked_workflow.addWidget(self._create_mux_panel())     # Index 1
        main_layout.addWidget(self.stacked_workflow, 1)
        
        # 恢复上次的工作流程页面
        last_workflow = self.app_settings.get("last_workflow", "encode")
        self.seg_workflow.setCurrentItem(last_workflow)
        self._on_workflow_changed(last_workflow)

    def _on_workflow_changed(self, route_key):
        if route_key == "encode":
            self.stacked_workflow.setCurrentIndex(0)
            self.seg_subtitle_mode.show()
        else:
            self.stacked_workflow.setCurrentIndex(1)
            self.seg_subtitle_mode.hide()
        self.app_settings["last_workflow"] = route_key

    def _create_profile_panel(self) -> CardWidget:
        panel = CardWidget()
        panel.setFixedWidth(240)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 工作流程切换按钮（在新建配置上方）
        self.seg_workflow = SegmentedWidget()
        self.seg_workflow.addItem(routeKey="encode", text="压制")
        self.seg_workflow.addItem(routeKey="mux", text="封装")
        self.seg_workflow.setCurrentItem("encode")
        self.seg_workflow.currentItemChanged.connect(self._on_workflow_changed)
        layout.addWidget(self.seg_workflow)
        
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

    def _create_encode_panel(self) -> QWidget:
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
        
        layout.setColumnMinimumWidth(0, 90)
        
        subtitle_mode_layout = QHBoxLayout()
        self.seg_subtitle_mode = SegmentedWidget()
        self.seg_subtitle_mode.addItem(routeKey="with_sub", text="有字幕")
        self.seg_subtitle_mode.addItem(routeKey="no_sub", text="无字幕")
        self.seg_subtitle_mode.setCurrentItem("with_sub")
        self.seg_subtitle_mode.currentItemChanged.connect(self._on_subtitle_mode_changed)
        subtitle_mode_layout.addWidget(self.seg_subtitle_mode)
        subtitle_mode_layout.addStretch(1)
        layout.addLayout(subtitle_mode_layout, 0, 0, 1, 3)
        
        self.entry_source_dir = self._add_folder_row(layout, 1, "源视频目录")
        
        self.label_source_no_sub = StrongBodyLabel("源视频文件")
        layout.addWidget(self.label_source_no_sub, 2, 0)
        self.entry_source_no_sub = PlainLineEdit()
        self.entry_source_no_sub.setPlaceholderText("Title - S01E<ep>.mkv")
        layout.addWidget(self.entry_source_no_sub, 2, 1, 1, 2)
        self.label_source_no_sub.hide()
        self.entry_source_no_sub.hide()
        
        self.label_sub_dir = StrongBodyLabel("字幕目录")
        layout.addWidget(self.label_sub_dir, 3, 0)
        self.entry_sub_dir = PlainLineEdit()
        layout.addWidget(self.entry_sub_dir, 3, 1)
        self.btn_sub_dir = PushButton(FIF.FOLDER, "浏览")
        self.btn_sub_dir.clicked.connect(lambda: self._browse_folder(self.entry_sub_dir))
        layout.addWidget(self.btn_sub_dir, 3, 2)
        
        self.entry_output_dir = self._add_folder_row(layout, 4, "输出目录")
        
        self.template_group = self._create_template_group()
        layout.addWidget(self.template_group, 5, 0, 1, 3)
        
        self.no_sub_group = self._create_no_sub_group()
        layout.addWidget(self.no_sub_group, 5, 0, 1, 3)
        self.no_sub_group.hide()
        
        self._add_params_section(layout)
        
        for widget in [self.entry_source_dir, self.entry_sub_dir, self.entry_output_dir,
                      self.entry_source_template, self.entry_sub_sc, self.entry_sub_tc,
                      self.entry_out_sc, self.entry_out_tc, self.entry_source_no_sub,
                      self.entry_out_no_sub]:
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
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setVerticalSpacing(10)
        layout.setColumnMinimumWidth(0, 90)
        
        layout.addWidget(StrongBodyLabel("源视频文件"), 0, 0)
        self.entry_source_template = PlainLineEdit()
        self.entry_source_template.setPlaceholderText("Title - S01E<ep>.mkv")
        layout.addWidget(self.entry_source_template, 0, 1, 1, 3)
        
        layout.addWidget(StrongBodyLabel("简体字幕"), 1, 0)
        self.entry_sub_sc = PlainLineEdit()
        self.entry_sub_sc.setFixedWidth(140)
        layout.addWidget(self.entry_sub_sc, 1, 1)
        
        lbl_out_sc = StrongBodyLabel("输出名称")
        lbl_out_sc.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(lbl_out_sc, 1, 2)
        self.entry_out_sc = PlainLineEdit()
        self.entry_out_sc.setPlaceholderText("Title - S01E<ep> - [CHS_JP].mp4")
        layout.addWidget(self.entry_out_sc, 1, 3)
        
        layout.addWidget(StrongBodyLabel("繁体字幕"), 2, 0)
        self.entry_sub_tc = PlainLineEdit()
        self.entry_sub_tc.setFixedWidth(140)
        layout.addWidget(self.entry_sub_tc, 2, 1)
        
        lbl_out_tc = StrongBodyLabel("输出名称")
        lbl_out_tc.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(lbl_out_tc, 2, 2)
        self.entry_out_tc = PlainLineEdit()
        self.entry_out_tc.setPlaceholderText("Title - S01E<ep> - [CHT_JP].mp4")
        layout.addWidget(self.entry_out_tc, 2, 3)
        
        return group

    def _create_no_sub_group(self) -> QFrame:
        group = QFrame()
        layout = QHBoxLayout(group)
        layout.setContentsMargins(0, 10, 0, 0)
        
        label = StrongBodyLabel("输出名称")
        label.setFixedWidth(90)
        layout.addWidget(label)
        
        self.entry_out_no_sub = PlainLineEdit()
        self.entry_out_no_sub.setPlaceholderText("Title - S01E<ep>.mp4")
        layout.addWidget(self.entry_out_no_sub, 1)
        
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
        
        layout.addLayout(control_bar_layout, 6, 0, 1, 3)

        self.lbl_crf = StrongBodyLabel("CRF/单次参数:")
        layout.addWidget(self.lbl_crf, 7, 0, Qt.AlignmentFlag.AlignTop)
        
        self.text_params_crf = PlainTextEdit()
        self.text_params_crf.setFixedHeight(80)
        self.text_params_crf.textChanged.connect(self._save_current_params)
        layout.addWidget(self.text_params_crf, 7, 1, 1, 2)
        
        self.lbl_pass1 = StrongBodyLabel("Pass 1 参数:")
        layout.addWidget(self.lbl_pass1, 8, 0, Qt.AlignmentFlag.AlignTop)
        
        self.text_params_pass1 = PlainTextEdit()
        self.text_params_pass1.setFixedHeight(80)
        self.text_params_pass1.textChanged.connect(self._save_current_params)
        layout.addWidget(self.text_params_pass1, 8, 1, 1, 2)
        
        self.lbl_pass2 = StrongBodyLabel("Pass 2 参数:")
        layout.addWidget(self.lbl_pass2, 9, 0, Qt.AlignmentFlag.AlignTop)
        
        self.text_params_pass2 = PlainTextEdit()
        self.text_params_pass2.setFixedHeight(80)
        self.text_params_pass2.textChanged.connect(self._save_current_params)
        layout.addWidget(self.text_params_pass2, 9, 1, 1, 2)
        
        self._toggle_2pass_ui(0)

    def _create_control_card(self) -> CardWidget:
        card = CardWidget()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(20, 15, 20, 15)
        
        self.label_ep = SubtitleLabel("集数")
        layout.addWidget(self.label_ep)
        self.entry_ep = PlainLineEdit()
        self.entry_ep.setFixedWidth(80)
        self.entry_ep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.entry_ep.setPlaceholderText("01")
        self.entry_ep.textChanged.connect(self._save_current_profile_meta)
        layout.addWidget(self.entry_ep)
        
        self.entry_ep_no_sub = PlainLineEdit()
        self.entry_ep_no_sub.setFixedWidth(80)
        self.entry_ep_no_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.entry_ep_no_sub.setPlaceholderText("01")
        self.entry_ep_no_sub.textChanged.connect(self._save_current_profile_meta)
        layout.addWidget(self.entry_ep_no_sub)
        self.entry_ep_no_sub.hide()
        
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
        
        self.btn_add_sc = PushButton(FIF.ADD, "简体")
        self.btn_add_sc.clicked.connect(lambda: self._add_tasks_from_ui("SC"))
        layout.addWidget(self.btn_add_sc)
        
        self.btn_add_tc = PushButton(FIF.ADD, "繁体")
        self.btn_add_tc.clicked.connect(lambda: self._add_tasks_from_ui("TC"))
        layout.addWidget(self.btn_add_tc)
        
        self.btn_add_both = PrimaryPushButton(FIF.ADD, "简繁")
        self.btn_add_both.clicked.connect(lambda: self._add_tasks_from_ui("BOTH"))
        layout.addWidget(self.btn_add_both)
        
        self.btn_add_no_sub = PrimaryPushButton(FIF.ADD, "添加任务")
        self.btn_add_no_sub.clicked.connect(lambda: self._add_tasks_from_ui("NO_SUB"))
        layout.addWidget(self.btn_add_no_sub)
        self.btn_add_no_sub.hide()
        
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

    def _on_subtitle_mode_changed(self, route_key: str):
        if not self.current_profile_name: return
        
        profile = self.profiles[self.current_profile_name]
        old_has_subtitle = profile.get("has_subtitle_mode", True)
        
        if old_has_subtitle:
            profile.update({
                "source_dir_with_sub": self.entry_source_dir.text(),
                "output_dir_with_sub": self.entry_output_dir.text(),
            })
        else:
            profile.update({
                "source_dir_no_sub": self.entry_source_dir.text(),
                "output_dir_no_sub": self.entry_output_dir.text(),
            })
        
        profile.update({
            "sub_dir": self.entry_sub_dir.text(),
            "sub_sc": self.entry_sub_sc.text(),
            "sub_tc": self.entry_sub_tc.text(),
            "out_sc": self.entry_out_sc.text(),
            "out_tc": self.entry_out_tc.text(),
            "last_ep": self.entry_ep.text(),
            "source_no_sub": self.entry_source_no_sub.text(),
            "out_no_sub": self.entry_out_no_sub.text(),
            "last_ep_no_sub": self.entry_ep_no_sub.text(),
        })
        
        new_has_subtitle = (route_key == "with_sub")
        profile["has_subtitle_mode"] = new_has_subtitle
        self._save_profiles_to_disk()
        
        self._update_subtitle_mode_ui(new_has_subtitle)
        
        if new_has_subtitle:
            self.entry_source_dir.setText(profile.get("source_dir_with_sub", profile.get("source_dir", "")))
            self.entry_output_dir.setText(profile.get("output_dir_with_sub", profile.get("output_dir", "")))
            self.entry_sub_dir.setText(profile.get("sub_dir", ""))
            self.entry_sub_sc.setText(profile.get("sub_sc", ""))
            self.entry_sub_tc.setText(profile.get("sub_tc", ""))
            self.entry_out_sc.setText(profile.get("out_sc", ""))
            self.entry_out_tc.setText(profile.get("out_tc", ""))
            self.entry_ep.setText(profile.get("last_ep", "01"))
        else:
            self.entry_source_dir.setText(profile.get("source_dir_no_sub", profile.get("source_dir", "")))
            self.entry_output_dir.setText(profile.get("output_dir_no_sub", profile.get("output_dir", "")))
            self.entry_source_no_sub.setText(profile.get("source_no_sub", ""))
            self.entry_out_no_sub.setText(profile.get("out_no_sub", ""))
            self.entry_ep_no_sub.setText(profile.get("last_ep_no_sub", "01"))

    def _update_subtitle_mode_ui(self, has_subtitle: bool):
        if has_subtitle:
            self.label_source_no_sub.hide()
            self.entry_source_no_sub.hide()
            self.label_sub_dir.show()
            self.entry_sub_dir.show()
            self.btn_sub_dir.show()
            self.template_group.show()
            self.no_sub_group.hide()
            
            self.entry_ep.show()
            self.entry_ep_no_sub.hide()
            
            self.btn_add_sc.show()
            self.btn_add_tc.show()
            self.btn_add_both.show()
            self.btn_add_no_sub.hide()
        else:
            self.label_source_no_sub.show()
            self.entry_source_no_sub.show()
            self.label_sub_dir.hide()
            self.entry_sub_dir.hide()
            self.btn_sub_dir.hide()
            self.template_group.hide()
            self.no_sub_group.show()
            
            self.entry_ep.hide()
            self.entry_ep_no_sub.show()
            
            self.btn_add_sc.hide()
            self.btn_add_tc.hide()
            self.btn_add_both.hide()
            self.btn_add_no_sub.show()

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
        has_subtitle = profile.get("has_subtitle_mode", True)
        
        if has_subtitle:
            profile.update({
                "source_dir_with_sub": self.entry_source_dir.text(),
                "output_dir_with_sub": self.entry_output_dir.text(),
            })
        else:
            profile.update({
                "source_dir_no_sub": self.entry_source_dir.text(),
                "output_dir_no_sub": self.entry_output_dir.text(),
            })
        
        profile["source_temp"] = self.entry_source_template.text()
        
        profile.update({
            "sub_dir": self.entry_sub_dir.text(),
            "sub_sc": self.entry_sub_sc.text(),
            "sub_tc": self.entry_sub_tc.text(),
            "out_sc": self.entry_out_sc.text(),
            "out_tc": self.entry_out_tc.text(),
            "last_ep": self.entry_ep.text(),
            "source_no_sub": self.entry_source_no_sub.text(),
            "out_no_sub": self.entry_out_no_sub.text(),
            "last_ep_no_sub": self.entry_ep_no_sub.text(),
        })
        
        profile.update({
            "extract_source_dir": self.extract_source_dir.text(),
            "extract_source_name": self.extract_source_name.text(),
            "extract_output_dir": self.extract_output_dir.text(),
            "extract_output_name": self.extract_output_name.text(),
            "extract_ep": self.extract_ep.text(),
            "extract_chapter_file": self.extract_chapter_file.text(),
            "extract_no_subs": self.extract_no_subs.isChecked(),
            "extract_no_fonts": self.extract_no_fonts.isChecked(),
            "extract_no_chapters": self.extract_no_chapters.isChecked(),
            "extract_keep_only_av": self.extract_keep_only_av.isChecked(),
            "extract_remove_names": self.extract_remove_names.isChecked(),
            "subset_sub_dir": self.subset_sub_dir.text(),
            "subset_font_dir": self.subset_font_dir.text(),
            "subset_ep": self.subset_ep.text(),
            "subset_sub_sc": self.subset_sub_sc.text(),
            "subset_sub_tc": self.subset_sub_tc.text(),
            "mux_video_dir": self.mux_video_dir.text(),
            "mux_video_name": self.mux_video_name.text(),
            "mux_subset_dir": self.mux_subset_dir.text(),
            "mux_output_dir": self.mux_output_dir.text(),
            "mux_output_name": self.mux_output_name.text(),
            "mux_ep": self.mux_ep.text(),
            "mux_sub_sc": self.mux_sub_sc.text(),
            "mux_sub_sc_name": self.mux_sub_sc_name.text(),
            "mux_sub_sc_default": self.mux_sub_sc_default.isChecked(),
            "mux_sub_tc": self.mux_sub_tc.text(),
            "mux_sub_tc_name": self.mux_sub_tc_name.text(),
            "mux_sub_tc_default": self.mux_sub_tc_default.isChecked()
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
            # 压制配置
            "source_dir": "", "sub_dir": "", "output_dir": "",
            "source_temp": "",
            "sub_sc": "<ep>.zh-hans.ass", "sub_tc": "<ep>.zh-hant.ass",
            "out_sc": "", "out_tc": "",
            "param_matrix": profile_matrix, 
            "selected_hw": "CPU",
            "selected_codec": "X264",
            "last_ep": "01",
            "last_ep_no_sub": "01",
            "has_subtitle_mode": True,
            "out_no_sub": "",
            # 封装配置
            "extract_source_dir": "", "extract_source_name": "",
            "extract_output_dir": "", "extract_output_name": "",
            "extract_ep": "01", "extract_chapter_file": "",
            "extract_no_subs": False, "extract_no_fonts": False, "extract_no_chapters": False,
            "extract_keep_only_av": False, "extract_remove_names": False,
            "subset_sub_dir": "", "subset_font_dir": "", "subset_ep": "01",
            "subset_sub_sc": "<ep>.zh-hans.ass", "subset_sub_tc": "<ep>.zh-hant.ass",
            "mux_video_dir": "", "mux_video_name": "", "mux_subset_dir": "", "mux_output_dir": "", "mux_output_name": "",
            "mux_ep": "01", "mux_sub_sc": "<ep>.zh-hans.ass", "mux_sub_sc_name": "简体中文&日语",
            "mux_sub_sc_default": True, "mux_sub_tc": "<ep>.zh-hant.ass", "mux_sub_tc_name": "繁體中文&日語",
            "mux_sub_tc_default": False
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
        
        has_subtitle = data.get("has_subtitle_mode", True)
        self._update_subtitle_mode_ui(has_subtitle)
        
        if has_subtitle:
            self.entry_source_dir.setText(data.get("source_dir_with_sub", data.get("source_dir", "")))
            self.entry_output_dir.setText(data.get("output_dir_with_sub", data.get("output_dir", "")))
        else:
            self.entry_source_dir.setText(data.get("source_dir_no_sub", data.get("source_dir", "")))
            self.entry_output_dir.setText(data.get("output_dir_no_sub", data.get("output_dir", "")))
        self.entry_source_template.setText(data.get("source_temp", ""))
        
        self.entry_sub_dir.setText(data.get("sub_dir", ""))
        self.entry_sub_sc.setText(data.get("sub_sc", ""))
        self.entry_sub_tc.setText(data.get("sub_tc", ""))
        self.entry_out_sc.setText(data.get("out_sc", ""))
        self.entry_out_tc.setText(data.get("out_tc", ""))
        self.entry_ep.setText(data.get("last_ep", "01"))
        
        self.entry_source_no_sub.setText(data.get("source_no_sub", ""))
        self.entry_out_no_sub.setText(data.get("out_no_sub", ""))
        self.entry_ep_no_sub.setText(data.get("last_ep_no_sub", "01"))
        
        self.seg_subtitle_mode.setCurrentItem("with_sub" if has_subtitle else "no_sub")
        
        self.extract_source_dir.setText(data.get("extract_source_dir", ""))
        self.extract_source_name.setText(data.get("extract_source_name", ""))
        self.extract_output_dir.setText(data.get("extract_output_dir", ""))
        self.extract_output_name.setText(data.get("extract_output_name", ""))
        self.extract_ep.setText(data.get("extract_ep", "01"))
        self.extract_chapter_file.setText(data.get("extract_chapter_file", ""))
        self.extract_no_subs.setChecked(data.get("extract_no_subs", False))
        self.extract_no_fonts.setChecked(data.get("extract_no_fonts", False))
        self.extract_no_chapters.setChecked(data.get("extract_no_chapters", False))
        self.extract_keep_only_av.setChecked(data.get("extract_keep_only_av", False))
        self.extract_remove_names.setChecked(data.get("extract_remove_names", False))
        
        if data.get("extract_keep_only_av", False):
            self.extract_no_subs.setEnabled(False)
            self.extract_no_fonts.setEnabled(False)
            self.extract_no_chapters.setEnabled(False)
        else:
            self.extract_no_subs.setEnabled(True)
            self.extract_no_fonts.setEnabled(True)
            self.extract_no_chapters.setEnabled(True)
        
        self.subset_sub_dir.setText(data.get("subset_sub_dir", ""))
        self.subset_font_dir.setText(data.get("subset_font_dir", ""))
        self.subset_ep.setText(data.get("subset_ep", "01"))
        self.subset_sub_sc.setText(data.get("subset_sub_sc", "<ep>.zh-hans.ass"))
        self.subset_sub_tc.setText(data.get("subset_sub_tc", "<ep>.zh-hant.ass"))
        
        self.mux_video_dir.setText(data.get("mux_video_dir", ""))
        self.mux_video_name.setText(data.get("mux_video_name", ""))
        self.mux_subset_dir.setText(data.get("mux_subset_dir", ""))
        self.mux_output_dir.setText(data.get("mux_output_dir", ""))
        self.mux_output_name.setText(data.get("mux_output_name", ""))
        self.mux_ep.setText(data.get("mux_ep", "01"))
        self.mux_sub_sc.setText(data.get("mux_sub_sc", "<ep>.zh-hans.ass"))
        self.mux_sub_sc_name.setText(data.get("mux_sub_sc_name", "简体中文&日语"))
        self.mux_sub_sc_default.setChecked(data.get("mux_sub_sc_default", True))
        self.mux_sub_tc.setText(data.get("mux_sub_tc", "<ep>.zh-hant.ass"))
        self.mux_sub_tc_name.setText(data.get("mux_sub_tc_name", "繁體中文&日語"))
        self.mux_sub_tc_default.setChecked(data.get("mux_sub_tc_default", False))
        
        sel_hw = data.get("selected_hw", "CPU")
        sel_codec = data.get("selected_codec", "X264")
        self.seg_hardware.setCurrentItem(sel_hw)
        self.seg_codec.setCurrentItem(sel_codec)
        
        self._block_signals(False)
        self._refresh_text_fields(sel_hw, sel_codec)
        
        items = self.profile_list_widget.findItems(name, Qt.MatchFlag.MatchExactly)
        if items: self.profile_list_widget.setCurrentItem(items[0])

    def _block_signals(self, block: bool):
        widgets = [
            self.seg_subtitle_mode,
            self.entry_source_dir, self.entry_sub_dir, self.entry_output_dir,
            self.entry_source_template, self.entry_sub_sc, self.entry_sub_tc,
            self.entry_out_sc, self.entry_out_tc, self.entry_ep, self.entry_ep_no_sub,
            self.entry_source_no_sub, self.entry_out_no_sub,
            self.text_params_crf, self.text_params_pass1, self.text_params_pass2,
            self.chk_2pass,
            self.extract_source_dir, self.extract_source_name, self.extract_output_dir,
            self.extract_output_name, self.extract_ep, self.extract_chapter_file,
            self.extract_no_subs, self.extract_no_fonts, self.extract_no_chapters,
            self.extract_keep_only_av, self.extract_remove_names,
            self.subset_sub_dir, self.subset_font_dir, self.subset_ep,
            self.subset_sub_sc, self.subset_sub_tc,
            self.mux_video_dir, self.mux_video_name, self.mux_subset_dir, self.mux_output_dir,
            self.mux_output_name, self.mux_ep, self.mux_sub_sc, self.mux_sub_sc_name,
            self.mux_sub_sc_default, self.mux_sub_tc, self.mux_sub_tc_name,
            self.mux_sub_tc_default
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
        self.entry_source_no_sub.clear()
        self.entry_out_no_sub.clear()
        self.entry_ep.clear()
        self.entry_ep_no_sub.clear()
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
                    for hw in pdata["param_matrix"]:
                        for codec in pdata["param_matrix"][hw]:
                            if "enable_2pass" not in pdata["param_matrix"][hw][codec]:
                                pdata["param_matrix"][hw][codec]["enable_2pass"] = False
                
                if "has_subtitle_mode" not in pdata:
                    pdata["has_subtitle_mode"] = True
                if "last_ep_no_sub" not in pdata:
                    pdata["last_ep_no_sub"] = pdata.get("last_ep", "01")
                if "out_no_sub" not in pdata:
                    pdata["out_no_sub"] = ""

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

    def _batch_add_callback(self, ep_list, mode, suffix):
        if not ep_list: return
        for ep in ep_list:
            self._add_task_internal(ep, mode, suffix)
        self.queue_list.scrollToBottom()
        
        if mode == "NO_SUB" and not self.app_settings.get("ep_not_shared", False):
            if ep_list:
                last_ep = ep_list[-1]
                try:
                    next_ep = str(int(last_ep) + 1).zfill(len(last_ep))
                    self.entry_ep_no_sub.setText(next_ep)
                    self.entry_ep.setText(next_ep)
                except ValueError: pass
        elif mode != "NO_SUB" and not self.app_settings.get("ep_not_shared", False):
            if ep_list:
                last_ep = ep_list[-1]
                try:
                    next_ep = str(int(last_ep) + 1).zfill(len(last_ep))
                    self.entry_ep.setText(next_ep)
                    self.entry_ep_no_sub.setText(next_ep)
                except ValueError: pass

    def _add_tasks_from_ui(self, mode: str):
        """UI 按钮触发的单集添加"""
        if mode == "NO_SUB":
            ep = self.entry_ep_no_sub.text().strip()
        else:
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
        if mode == "NO_SUB":
            tasks.append({"type": "NO_SUB", "ep": ep, "profile": profile, "suffix": suffix_text})
        else:
            if mode in ["SC", "BOTH"]: tasks.append({"type": "SC", "ep": ep, "profile": profile, "suffix": suffix_text})
            if mode in ["TC", "BOTH"]: tasks.append({"type": "TC", "ep": ep, "profile": profile, "suffix": suffix_text})
        
        for task in tasks:
            display_suffix = f" {task['suffix']}" if task['suffix'] else ""
            
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
        
        self.logger.info(f"[EncodeManager] 开始压制任务 - 集数: {ep}, 类型: {task_type}, 硬件: {hw}, 编码: {codec}")
        
        if task_type == "NO_SUB":
            src_template = profile.get('source_no_sub', '')
            out_name = profile.get('out_no_sub', '').replace('<ep>', ep)
            if not out_name:
                out_name = f"output_{ep}.mp4"
            full_sub = None
            source_dir = profile.get('source_dir_no_sub', profile.get('source_dir', ''))
            output_dir = profile.get('output_dir_no_sub', profile.get('output_dir', ''))
        elif task_type == "SC":
            src_template = profile['source_temp']
            sub_template = profile['sub_sc']
            out_name = profile['out_sc'].replace('<ep>', ep)
            full_sub = self._resolve_file_with_wildcard(profile['sub_dir'], sub_template, ep)
            source_dir = profile.get('source_dir_with_sub', profile.get('source_dir', ''))
            output_dir = profile.get('output_dir_with_sub', profile.get('output_dir', ''))
        else:
            src_template = profile['source_temp']
            sub_template = profile['sub_tc']
            out_name = profile['out_tc'].replace('<ep>', ep)
            full_sub = self._resolve_file_with_wildcard(profile['sub_dir'], sub_template, ep)
            source_dir = profile.get('source_dir_with_sub', profile.get('source_dir', ''))
            output_dir = profile.get('output_dir_with_sub', profile.get('output_dir', ''))
        
        full_src = self._resolve_file_with_wildcard(source_dir, src_template, ep)
        
        if suffix:
            root, ext = os.path.splitext(out_name)
            out_name = f"{root}{suffix}{ext}"
        
        full_out = os.path.join(output_dir, out_name)
        
        if not full_src:
            src_display = src_template.replace('<ep>', ep)
            self._log_to_ui(f"❌ 文件缺失: {src_display}")
            self._task_finished_callback(False, full_out, "0s")
            return
        
        if task_type != "NO_SUB" and not full_sub:
            sub_display = sub_template.replace('<ep>', ep)
            self._log_to_ui(f"❌ 字幕文件缺失: {sub_display}")
            self._task_finished_callback(False, full_out, "0s")
            return
        
        sub_name = os.path.basename(full_sub) if full_sub else None
        
        is_2pass = params_set.get("enable_2pass", False)
        
        if task_type == "NO_SUB":
            commands = self._build_ffmpeg_commands_no_sub(params_set, full_src, full_out, is_2pass, output_dir)
            temp_files = self._get_temp_files(output_dir) if is_2pass else []
            work_dir = output_dir
        else:
            commands = self._build_ffmpeg_commands(
                params_set, full_src, full_sub, sub_name, full_out, is_2pass, profile['sub_dir']
            )
            temp_files = self._get_temp_files(profile['sub_dir']) if is_2pass else []
            work_dir = profile['sub_dir']
        
        self.progress_dashboard.start_timer()
        self.worker = Worker(commands, work_dir, full_out, temp_files)
        self.worker.log_signal.connect(self._log_to_ui)
        self.worker.progress_signal.connect(self._update_progress_text)
        self.worker.percent_signal.connect(self._update_progress_percent)
        self.worker.finished_signal.connect(self._task_finished_callback)
        self.worker.start()

    def _build_ffmpeg_commands_no_sub(self, params_set: Dict, full_src: str, 
                                       full_out: str, is_2pass: bool, work_dir: str) -> List[str]:
        commands = []
        if is_2pass:
            params_1 = self._sanitize_params(params_set.get("pass1", ""))
            params_2 = self._sanitize_params(params_set.get("pass2", ""))
            has_pass1 = "-pass 1" in params_1 or "-pass=1" in params_1
            has_pass2 = "-pass 2" in params_2 or "-pass=2" in params_2
            has_passlog1 = "-passlogfile" in params_1
            has_passlog2 = "-passlogfile" in params_2
            pass_log_path = os.path.join(work_dir, "ffmpeg2pass")
            pass1_prefix = "" if has_pass1 else "-pass 1 "
            passlog1_prefix = "" if has_passlog1 else f'-passlogfile "{pass_log_path}" '
            cmd1 = f'ffmpeg -y -i "{full_src}" {passlog1_prefix}{pass1_prefix}{params_1}'
            commands.append(cmd1)
            pass2_prefix = "" if has_pass2 else "-pass 2 "
            passlog2_prefix = "" if has_passlog2 else f'-passlogfile "{pass_log_path}" '
            cmd2 = f'ffmpeg -y -i "{full_src}" {passlog2_prefix}{pass2_prefix}{params_2} "{full_out}"'
            commands.append(cmd2)
        else:
            params_crf = self._sanitize_params(params_set.get("crf", ""))
            cmd = f'ffmpeg -y -i "{full_src}" {params_crf} "{full_out}"'
            commands.append(cmd)
        return commands

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

    def _resolve_file_with_wildcard(self, directory: str, name_template: str, ep: str) -> Optional[str]:
        """
        解析带有通配符和<ep>占位符的文件名模板，返回实际文件路径。
        
        支持的占位符：
        - <ep> : 替换为集数
        - <hash> : 匹配一个或多个连续的方括号内容，如 [DAFF3540] 或 [1080p][MultiSub][3B94EB84]
        - * : 匹配任意字符（除了路径分隔符）
        
        示例：
        - "[SubsPlease] Title - <ep> (1080p) <hash>.mkv" 
          匹配 "[SubsPlease] Title - 01 (1080p) [F2821ACD].mkv"
        - "[Erai-raws] Title - <ep> <hash>.mkv"
          匹配 "[Erai-raws] Title - 01 [1080p CR WEB-DL AVC AAC][MultiSub][3B94EB84].mkv"
        """
        if not directory or not name_template:
            return None
        
        name_pattern = name_template.replace('<ep>', ep)
        
        # 使用正则表达式匹配文件
        if '<hash>' in name_pattern or '*' in name_pattern:
            # 将模板转换为正则表达式
            regex_pattern = name_pattern
            
            # 先处理 <hash> 占位符（在转义之前），用临时占位符替换
            hash_placeholder = '\x00HASH\x00'
            regex_pattern = regex_pattern.replace('<hash>', hash_placeholder)
            
            # 先处理 * 通配符（在转义之前），用临时占位符替换
            star_placeholder = '\x00STAR\x00'
            regex_pattern = regex_pattern.replace('*', star_placeholder)
            
            special_chars = r'\.^$+?{}|()[]'
            for char in special_chars:
                regex_pattern = regex_pattern.replace(char, '\\' + char)
            
            regex_pattern = regex_pattern.replace(hash_placeholder, r'(?:\[[^\]]+\])+')
            
            regex_pattern = regex_pattern.replace(star_placeholder, '.*')
            
            # 编译正则表达式
            try:
                pattern = re.compile(f'^{regex_pattern}$', re.IGNORECASE)
            except re.error:
                return None
            
            # 遍历目录查找匹配的文件
            try:
                for filename in os.listdir(directory):
                    if pattern.match(filename):
                        return os.path.join(directory, filename)
            except OSError:
                return None
            
            return None
        else:
            direct_path = os.path.join(directory, name_pattern)
            if os.path.exists(direct_path):
                return direct_path
            return None

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
        try:
            item = self.current_item
            if item is None:
                return
            widget = self.queue_list.itemWidget(item)
            if widget is not None and isinstance(widget, TaskItemWidget):
                widget.set_status(progress=percent, pass_idx=pass_idx, status_code=1)
        except RuntimeError:
            # Widget 已被 C++ 侧销毁（wrapped C/C++ object has been deleted）
            pass

    def _task_finished_callback(self, success: bool, output_file: str, duration: str):
        self.logger.info(f"[EncodeManager] 任务完成回调 - 成功: {success}, 输出文件: {output_file}, 耗时: {duration}")
        
        if self.force_stop_flag:
            self._handle_forced_cleanup(output_file)
            return
        if not self.current_item: return
        data = self.current_item.data(Qt.ItemDataRole.UserRole)
        task_info = data['raw_task']
        
        try:
            widget = self.queue_list.itemWidget(self.current_item)
        except RuntimeError:
            widget = None
        
        if success:
            data['status'] = 'done'
            self.logger.info(f"[EncodeManager] 任务成功 - 集数: {task_info['ep']}, 类型: {task_info['type']}")
            if widget is not None and isinstance(widget, TaskItemWidget):
                widget.set_status(progress=1.0, status_code=2)
            
            if not task_info.get('suffix'):
                self.completed_history.add((task_info['ep'], task_info['type']))
                self._check_auto_increment(task_info['ep'], task_info['type'])
        else:
            data['status'] = 'error'
            self.logger.error(f"[EncodeManager] 任务失败 - 集数: {task_info['ep']}, 类型: {task_info['type']}")
            if widget is not None and isinstance(widget, TaskItemWidget):
                widget.set_status(progress=0.0, status_code=3)
                
        self.current_item.setData(Qt.ItemDataRole.UserRole, data)
        self.current_item = None
        self.worker = None
        self.progress_dashboard.reset()
        if self.is_running: self._process_next_task()

    def _check_auto_increment(self, ep: str, task_type: str = None):
        if not self.app_settings.get("auto_ep_encode", True):
            return
        
        ep_not_shared = self.app_settings.get("ep_not_shared", False)
        
        if task_type == "NO_SUB":
            self._log_to_ui(f"ℹ️ 无字幕压制完成，集数自动 +1")
            current = self.entry_ep_no_sub.text()
            if current == ep:
                try:
                    next_ep = str(int(ep) + 1).zfill(len(ep))
                    self.entry_ep_no_sub.setText(next_ep)
                    if not ep_not_shared:
                        self.entry_ep.setText(next_ep)
                except ValueError: pass
        else:
            if (ep, 'SC') in self.completed_history and (ep, 'TC') in self.completed_history:
                self._log_to_ui(f"ℹ️ 集数 {ep} 双语完成，自动 +1")
                current = self.entry_ep.text()
                if current == ep:
                    try:
                        next_ep = str(int(ep) + 1).zfill(len(ep))
                        self.entry_ep.setText(next_ep)
                        if not ep_not_shared:
                            self.entry_ep_no_sub.setText(next_ep)
                    except ValueError: pass

    def _all_tasks_finished(self):
        self.logger.info("[EncodeManager] 所有队列任务完成")
        self.is_running = False
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._hide_stop_confirm()
        self._log_to_ui("=== 队列完成 ===")
        InfoBar.success(title="完成", content="所有任务已结束", parent=self)

    def _force_stop(self):
        self.logger.warning("[EncodeManager] 用户请求强制停止")
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
                if widget is not None and isinstance(widget, TaskItemWidget):
                    widget.set_status(progress=0.0, status_code=3) # Error color
            except (RuntimeError, AttributeError):
                pass
        self.current_item = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress_dashboard.reset()
        self.queue_list.clear()
        self._log_to_ui("=== 队列已清空 ===")

    def _create_mux_panel(self) -> QWidget:
        """创建封装工作流程面板"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        
        # ========== 上区域：MKV 提取 ==========
        extract_card = self._create_extract_section()
        layout.addWidget(extract_card)
        
        # ========== 中区域：字体子集化 ==========
        subset_card = self._create_subset_section()
        layout.addWidget(subset_card)
        
        # ========== 下区域：混流 ==========
        mux_card = self._create_mux_section()
        layout.addWidget(mux_card)
        
        # ========== 底部：任务列表和日志 ==========
        bottom_container = QWidget()
        bottom_layout = QHBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(10)
        
        # 左边：任务队列
        task_panel = CardWidget()
        task_layout = QVBoxLayout(task_panel)
        task_layout.setContentsMargins(10, 10, 10, 10)
        
        task_header = QHBoxLayout()
        task_header.addWidget(StrongBodyLabel("任务队列"))
        task_header.addStretch(1)
        btn_start_mux = PrimaryPushButton(FIF.PLAY, "开始")
        btn_start_mux.clicked.connect(self._start_mux_tasks)
        task_header.addWidget(btn_start_mux)
        btn_clear_mux = PushButton(FIF.DELETE, "清空")
        btn_clear_mux.clicked.connect(self._clear_mux_queue)
        task_header.addWidget(btn_clear_mux)
        task_layout.addLayout(task_header)
        
        self.mux_queue_list = DraggableListWidget()
        self.mux_queue_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.mux_queue_list.customContextMenuRequested.connect(self._show_mux_queue_context_menu)
        task_layout.addWidget(self.mux_queue_list)
        
        bottom_layout.addWidget(task_panel, 38)
        
        # 右边：日志（上下布局：混流日志 + 子集日志）
        log_panel = CardWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(10, 10, 10, 10)
        log_layout.setSpacing(6)
        
        # 混流日志
        mux_log_header = QHBoxLayout()
        mux_log_header.addWidget(StrongBodyLabel("混流日志"))
        mux_log_header.addStretch(1)
        log_layout.addLayout(mux_log_header)
        
        self.mux_log_text = PlainTextEdit()
        self.mux_log_text.setReadOnly(True)
        self.mux_log_text.setStyleSheet("font-family: Consolas; font-size: 11px;")
        self.mux_log_text.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.mux_log_text.customContextMenuRequested.connect(self._show_mux_log_context_menu)
        log_layout.addWidget(self.mux_log_text, 1)
        
        # 子集日志
        subset_log_header = QHBoxLayout()
        subset_log_header.addWidget(StrongBodyLabel("子集日志"))
        subset_log_header.addStretch(1)
        log_layout.addLayout(subset_log_header)
        
        self.subset_log_text = PlainTextEdit()
        self.subset_log_text.setReadOnly(True)
        self.subset_log_text.setStyleSheet("font-family: Consolas; font-size: 11px;")
        self.subset_log_text.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.subset_log_text.customContextMenuRequested.connect(self._show_subset_log_context_menu)
        log_layout.addWidget(self.subset_log_text, 1)
        
        bottom_layout.addWidget(log_panel, 62)
        
        layout.addWidget(bottom_container, 1)
        
        # 连接所有封装配置控件的信号以自动保存
        mux_widgets = [
            self.extract_source_dir, self.extract_source_name, self.extract_output_dir, self.extract_output_name,
            self.extract_ep, self.extract_chapter_file, self.extract_no_subs, self.extract_no_fonts,
            self.extract_no_chapters, self.extract_keep_only_av, self.extract_remove_names,
            self.subset_sub_dir, self.subset_font_dir, self.subset_ep, self.subset_sub_sc, self.subset_sub_tc,
            self.mux_video_dir, self.mux_video_name, self.mux_subset_dir, self.mux_output_dir, self.mux_output_name, self.mux_ep,
            self.mux_sub_sc, self.mux_sub_sc_name, self.mux_sub_sc_default, self.mux_sub_tc, self.mux_sub_tc_name,
            self.mux_sub_tc_default
        ]
        for widget in mux_widgets:
            if isinstance(widget, (PlainLineEdit, PlainTextEdit)):
                widget.textChanged.connect(self._save_current_profile_meta)
            elif isinstance(widget, CheckBox):
                widget.stateChanged.connect(self._save_current_profile_meta)
        
        return panel

    def _create_extract_section(self) -> CardWidget:
        """MKV 提取配置区"""
        card = CardWidget()
        layout = QGridLayout(card)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setColumnMinimumWidth(0, 50)
        layout.setSpacing(6)
        
        # 第一行：原视频目录 + 视频名
        layout.addWidget(StrongBodyLabel("原视频"), 0, 0)
        self.extract_source_dir = PlainLineEdit()
        layout.addWidget(self.extract_source_dir, 0, 1)
        btn = ToolButton(FIF.FOLDER)
        btn.clicked.connect(lambda: self._browse_folder(self.extract_source_dir))
        layout.addWidget(btn, 0, 2)
        
        layout.addWidget(StrongBodyLabel("视频名"), 0, 3)
        self.extract_source_name = PlainLineEdit()
        self.extract_source_name.setPlaceholderText("[Erai-raws] Title - <ep> <hash>.mkv")
        layout.addWidget(self.extract_source_name, 0, 4)
        
        # 第二行：输出目录 + 输出名
        layout.addWidget(StrongBodyLabel("输出"), 1, 0)
        self.extract_output_dir = PlainLineEdit()
        layout.addWidget(self.extract_output_dir, 1, 1)
        btn = ToolButton(FIF.FOLDER)
        btn.clicked.connect(lambda: self._browse_folder(self.extract_output_dir))
        layout.addWidget(btn, 1, 2)
        
        layout.addWidget(StrongBodyLabel("输出名"), 1, 3)
        self.extract_output_name = PlainLineEdit()
        self.extract_output_name.setPlaceholderText("Title - S01E<ep>.mkv")
        layout.addWidget(self.extract_output_name, 1, 4)
        
        # 第三行：集数 + 章节（用 HBoxLayout 避免撑宽 grid 列）
        row3_layout = QHBoxLayout()
        row3_layout.addWidget(StrongBodyLabel("集数"))
        self.extract_ep = PlainLineEdit()
        self.extract_ep.setFixedWidth(80)
        self.extract_ep.setPlaceholderText("01")
        row3_layout.addWidget(self.extract_ep)
        
        row3_layout.addSpacing(15)
        row3_layout.addWidget(StrongBodyLabel("章节"))
        self.extract_chapter_file = PlainLineEdit()
        self.extract_chapter_file.setPlaceholderText("C:\\Folder\\Kiyoubinbou\\<ep>\\CHAPTER<ep>.txt")
        row3_layout.addWidget(self.extract_chapter_file)
        btn = ToolButton(FIF.FOLDER)
        btn.clicked.connect(lambda: self._browse_file_dialog(self.extract_chapter_file))
        row3_layout.addWidget(btn)
        
        layout.addLayout(row3_layout, 2, 0, 1, 5)
        
        # 第四行：移除选项
        remove_layout = QHBoxLayout()
        self.extract_no_subs = CheckBox("字幕")
        self.extract_no_fonts = CheckBox("字体")
        self.extract_no_chapters = CheckBox("章节")
        self.extract_keep_only_av = CheckBox("仅保留音视频")
        self.extract_keep_only_av.stateChanged.connect(self._on_extract_keep_only_av_changed)
        self.extract_remove_names = CheckBox("名称")
        
        remove_layout.addWidget(StrongBodyLabel("去除"))
        remove_layout.addWidget(self.extract_no_subs)
        remove_layout.addWidget(self.extract_no_fonts)
        remove_layout.addWidget(self.extract_no_chapters)
        remove_layout.addWidget(self.extract_keep_only_av)
        remove_layout.addWidget(self.extract_remove_names)
        remove_layout.addStretch(1)
        
        btn_add_extract = PrimaryPushButton(FIF.ADD, "添加任务")
        btn_add_extract.clicked.connect(self._add_extract_task)
        remove_layout.addWidget(btn_add_extract)
        
        btn_start_extract = PrimaryPushButton(FIF.PLAY, "开始")
        btn_start_extract.clicked.connect(self._start_extract)
        remove_layout.addWidget(btn_start_extract)
        
        layout.addLayout(remove_layout, 3, 0, 1, 5)
        
        return card

    def _create_subset_section(self) -> CardWidget:
        """字体子集化配置区"""
        card = CardWidget()
        layout = QGridLayout(card)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setColumnMinimumWidth(0, 50)
        layout.setSpacing(6)
        
        layout.addWidget(StrongBodyLabel("字幕目录"), 0, 0)
        self.subset_sub_dir = PlainLineEdit()
        self.subset_sub_dir.setPlaceholderText("C:\\Folder\\Kiyoubinbou\\<ep>")
        layout.addWidget(self.subset_sub_dir, 0, 1)
        btn = ToolButton(FIF.FOLDER)
        btn.clicked.connect(lambda: self._browse_folder(self.subset_sub_dir))
        layout.addWidget(btn, 0, 2)
        
        layout.addWidget(StrongBodyLabel("字体目录"), 0, 3)
        self.subset_font_dir = PlainLineEdit()
        self.subset_font_dir.setPlaceholderText("C:\\Folder\\Kiyoubinbou\\fonts")
        layout.addWidget(self.subset_font_dir, 0, 4)
        btn = ToolButton(FIF.FOLDER)
        btn.clicked.connect(lambda: self._browse_folder(self.subset_font_dir))
        layout.addWidget(btn, 0, 5)
        
        # 集数和字幕
        row2_layout = QHBoxLayout()
        row2_layout.addWidget(StrongBodyLabel("集数"))
        self.subset_ep = PlainLineEdit()
        self.subset_ep.setFixedWidth(80)
        self.subset_ep.setPlaceholderText("01")
        row2_layout.addWidget(self.subset_ep)
        
        row2_layout.addSpacing(10)
        row2_layout.addWidget(StrongBodyLabel("简体"))
        self.subset_sub_sc = PlainLineEdit()
        self.subset_sub_sc.setPlaceholderText("<ep>.zh-hans.ass")
        row2_layout.addWidget(self.subset_sub_sc)
        
        row2_layout.addSpacing(10)
        row2_layout.addWidget(StrongBodyLabel("繁体"))
        self.subset_sub_tc = PlainLineEdit()
        self.subset_sub_tc.setPlaceholderText("<ep>.zh-hant.ass")
        row2_layout.addWidget(self.subset_sub_tc)
        
        btn_add_subset = PrimaryPushButton(FIF.ADD, "添加任务")
        btn_add_subset.clicked.connect(self._add_subset_task)
        row2_layout.addWidget(btn_add_subset)
        
        btn_start_subset = PrimaryPushButton(FIF.PLAY, "开始")
        btn_start_subset.clicked.connect(self._start_subset)
        row2_layout.addWidget(btn_start_subset)
        
        layout.addLayout(row2_layout, 1, 0, 1, 6)
        
        return card

    def _create_mux_section(self) -> CardWidget:
        """混流配置区"""
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(6)
        
        # 第一行：视频目录 + 视频名
        row1 = QHBoxLayout()
        row1.addWidget(StrongBodyLabel("视频目录"))
        self.mux_video_dir = PlainLineEdit()
        row1.addWidget(self.mux_video_dir, 1)
        btn = ToolButton(FIF.FOLDER)
        btn.clicked.connect(lambda: self._browse_folder(self.mux_video_dir))
        row1.addWidget(btn)
        row1.addSpacing(20)
        row1.addWidget(StrongBodyLabel("视频名"))
        self.mux_video_name = PlainLineEdit()
        self.mux_video_name.setPlaceholderText("Title - S01E<ep>.mkv")
        row1.addWidget(self.mux_video_name, 2)
        layout.addLayout(row1)
        
        # 第二行：子集目录
        row2 = QHBoxLayout()
        row2.addWidget(StrongBodyLabel("子集目录"))
        self.mux_subset_dir = PlainLineEdit()
        self.mux_subset_dir.setPlaceholderText("C:\\Folder\\Kiyoubinbou\\<ep>")
        row2.addWidget(self.mux_subset_dir)
        btn = ToolButton(FIF.FOLDER)
        btn.clicked.connect(lambda: self._browse_folder(self.mux_subset_dir))
        row2.addWidget(btn)
        layout.addLayout(row2)
        
        # 第三行：输出目录 + 输出名
        row3 = QHBoxLayout()
        row3.addWidget(StrongBodyLabel("输出目录"))
        self.mux_output_dir = PlainLineEdit()
        row3.addWidget(self.mux_output_dir, 1)
        btn = ToolButton(FIF.FOLDER)
        btn.clicked.connect(lambda: self._browse_folder(self.mux_output_dir))
        row3.addWidget(btn)
        row3.addSpacing(20)
        row3.addWidget(StrongBodyLabel("输出名"))
        self.mux_output_name = PlainLineEdit()
        self.mux_output_name.setPlaceholderText("Title - S01E<ep>.mkv")
        row3.addWidget(self.mux_output_name, 2)
        layout.addLayout(row3)
        
        # 第三行：简体字幕
        sc_layout = QHBoxLayout()
        sc_layout.addWidget(StrongBodyLabel("简体"))
        self.mux_sub_sc = PlainLineEdit()
        self.mux_sub_sc.setPlaceholderText("<ep>.zh-hans.ass")
        self.mux_sub_sc.setFixedWidth(120)
        sc_layout.addWidget(self.mux_sub_sc)
        
        sc_layout.addWidget(StrongBodyLabel("名称"))
        self.mux_sub_sc_name = PlainLineEdit()
        self.mux_sub_sc_name.setText("简体中文&日语")
        self.mux_sub_sc_name.setFixedWidth(120)
        sc_layout.addWidget(self.mux_sub_sc_name)
        
        self.mux_sub_sc_default = CheckBox("默认轨")
        self.mux_sub_sc_default.setChecked(True)
        sc_layout.addWidget(self.mux_sub_sc_default)
        sc_layout.addStretch(1)
        
        layout.addLayout(sc_layout)
        
        # 第五行：繁体字幕
        tc_layout = QHBoxLayout()
        tc_layout.addWidget(StrongBodyLabel("繁体"))
        self.mux_sub_tc = PlainLineEdit()
        self.mux_sub_tc.setPlaceholderText("<ep>.zh-hant.ass")
        self.mux_sub_tc.setFixedWidth(120)
        tc_layout.addWidget(self.mux_sub_tc)
        
        tc_layout.addWidget(StrongBodyLabel("名称"))
        self.mux_sub_tc_name = PlainLineEdit()
        self.mux_sub_tc_name.setText("繁體中文&日語")
        self.mux_sub_tc_name.setFixedWidth(120)
        tc_layout.addWidget(self.mux_sub_tc_name)
        
        self.mux_sub_tc_default = CheckBox("默认轨")
        tc_layout.addWidget(self.mux_sub_tc_default)
        tc_layout.addStretch(1)
        
        layout.addLayout(tc_layout)
        
        # 第六行：集数 + 添加任务 + 开始
        bottom_layout = QHBoxLayout()
        bottom_layout.addWidget(StrongBodyLabel("集数"))
        self.mux_ep = PlainLineEdit()
        self.mux_ep.setFixedWidth(80)
        self.mux_ep.setPlaceholderText("01")
        bottom_layout.addWidget(self.mux_ep)
        bottom_layout.addStretch(1)
        
        btn_add_mux = PrimaryPushButton(FIF.ADD, "添加任务")
        btn_add_mux.clicked.connect(self._add_mux_task)
        bottom_layout.addWidget(btn_add_mux)
        
        btn_start_mux = PrimaryPushButton(FIF.PLAY, "开始")
        btn_start_mux.clicked.connect(self._start_mux)
        bottom_layout.addWidget(btn_start_mux)
        
        layout.addLayout(bottom_layout)
        
        return card

    def _browse_file_dialog(self, line_edit: PlainLineEdit):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", "文本文件 (*.txt);;所有文件 (*)")
        if file_path:
            line_edit.setText(file_path.replace("/", "\\"))

    def _on_extract_keep_only_av_changed(self, state):
        """仅保留音视频时，禁用其他选项（名称除外）"""
        is_checked = state == 2
        self.extract_no_subs.setEnabled(not is_checked)
        self.extract_no_fonts.setEnabled(not is_checked)
        self.extract_no_chapters.setEnabled(not is_checked)
        
        if is_checked:
            self.extract_no_subs.setChecked(True)
            self.extract_no_fonts.setChecked(True)
            self.extract_no_chapters.setChecked(True)

    def _add_extract_task(self):
        if not self.current_profile_name:
            InfoBar.warning(title="警告", content="请先选择一个配置", parent=self)
            return
        ep = self.extract_ep.text().strip()
        if not ep:
            InfoBar.warning(title="警告", content="请输入集数", parent=self)
            return
        
        profile = copy.deepcopy(self.profiles[self.current_profile_name])
        task_data = {
            "type": "extract",
            "ep": ep,
            "profile": profile,
            "desc": f"[提取] EP{ep} - {self.current_profile_name}"
        }
        
        list_item = QListWidgetItem()
        list_item.setData(Qt.ItemDataRole.UserRole, task_data)
        self.mux_queue_list.addItem(list_item)
        
        item_widget = TaskItemWidget(task_data["desc"])
        list_item.setSizeHint(item_widget.sizeHint())
        self.mux_queue_list.setItemWidget(list_item, item_widget)
        self.mux_queue_list.scrollToBottom()

    def _add_subset_task(self):
        if not self.current_profile_name:
            InfoBar.warning(title="警告", content="请先选择一个配置", parent=self)
            return
        ep = self.subset_ep.text().strip()
        if not ep:
            InfoBar.warning(title="警告", content="请输入集数", parent=self)
            return
        
        profile = copy.deepcopy(self.profiles[self.current_profile_name])
        task_data = {
            "type": "subset",
            "ep": ep,
            "profile": profile,
            "desc": f"[子集] EP{ep} - {self.current_profile_name}"
        }
        
        list_item = QListWidgetItem()
        list_item.setData(Qt.ItemDataRole.UserRole, task_data)
        self.mux_queue_list.addItem(list_item)
        
        item_widget = TaskItemWidget(task_data["desc"])
        list_item.setSizeHint(item_widget.sizeHint())
        self.mux_queue_list.setItemWidget(list_item, item_widget)
        self.mux_queue_list.scrollToBottom()

    def _add_mux_task(self):
        if not self.current_profile_name:
            InfoBar.warning(title="警告", content="请先选择一个配置", parent=self)
            return
        ep = self.mux_ep.text().strip()
        if not ep:
            InfoBar.warning(title="警告", content="请输入集数", parent=self)
            return
        
        profile = copy.deepcopy(self.profiles[self.current_profile_name])
        task_data = {
            "type": "mux",
            "ep": ep,
            "profile": profile,
            "desc": f"[混流] EP{ep} - {self.current_profile_name}"
        }
        
        list_item = QListWidgetItem()
        list_item.setData(Qt.ItemDataRole.UserRole, task_data)
        self.mux_queue_list.addItem(list_item)
        
        item_widget = TaskItemWidget(task_data["desc"])
        list_item.setSizeHint(item_widget.sizeHint())
        self.mux_queue_list.setItemWidget(list_item, item_widget)
        self.mux_queue_list.scrollToBottom()

    def _show_mux_queue_context_menu(self, pos):
        item = self.mux_queue_list.itemAt(pos)
        if not item: return
        menu = RoundMenu(parent=self)
        delete_action = Action(FIF.DELETE, '移除任务')
        delete_action.triggered.connect(lambda: self._remove_mux_queue_item(item))
        menu.addAction(delete_action)
        menu.exec(self.mux_queue_list.mapToGlobal(pos))

    def _show_mux_log_context_menu(self, pos):
        menu = RoundMenu(parent=self)
        clear_action = Action(FIF.DELETE, '清空日志')
        clear_action.triggered.connect(self.mux_log_text.clear)
        menu.addAction(clear_action)
        menu.exec(self.mux_log_text.mapToGlobal(pos))

    def _show_subset_log_context_menu(self, pos):
        menu = RoundMenu(parent=self)
        clear_action = Action(FIF.DELETE, '清空日志')
        clear_action.triggered.connect(self.subset_log_text.clear)
        menu.addAction(clear_action)
        menu.exec(self.subset_log_text.mapToGlobal(pos))

    def _remove_mux_queue_item(self, item: QListWidgetItem):
        self.mux_queue_list.takeItem(self.mux_queue_list.row(item))

    def _start_mux_tasks(self):
        if self.mux_queue_list.count() == 0:
            InfoBar.info(title="提示", content="队列为空", parent=self)
            return
        
        for i in range(self.mux_queue_list.count()):
            item = self.mux_queue_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                self._execute_mux_task(data, item)

    def _execute_mux_task(self, task_data: Dict, list_item: QListWidgetItem):
        task_type = task_data["type"]
        ep = task_data["ep"]
        profile = task_data["profile"]
        
        if task_type == "extract":
            self._do_extract_task(ep, profile, list_item)
        elif task_type == "subset":
            self._do_subset_task(ep, profile, list_item)
        elif task_type == "mux":
            self._do_mux_task(ep, profile, list_item)

    def _do_extract_task(self, ep: str, profile: Dict, list_item: QListWidgetItem):
        mkvmerge_path = self.app_settings.get("mkvmerge_path", "")
        if not mkvmerge_path or not os.path.exists(mkvmerge_path):
            InfoBar.error(title="错误", content="请先在设置中配置 mkvmerge.exe 路径", parent=self)
            return
        
        source_template = profile.get("extract_source_name", "")
        output_name = profile.get("extract_output_name", "").replace("<ep>", ep)
        source_dir = profile.get("extract_source_dir", "")
        output_dir = profile.get("extract_output_dir", "")
        
        if not source_dir or not output_dir:
            InfoBar.error(title="错误", content="请配置原视频目录和输出目录", parent=self)
            return
        
        source_file = self._resolve_file_with_wildcard(source_dir, source_template, ep)
        output_file = os.path.join(output_dir, output_name)
        
        if not source_file:
            src_display = source_template.replace("<ep>", ep)
            self.mux_log_text.append(f"❌ 源文件不存在: {src_display}")
            return
        
        cmd_parts = ['-o', f'"{output_file}"']
        
        if profile.get("extract_keep_only_av", False):
            cmd_parts.extend(["--no-subtitles", "--no-attachments", "--no-chapters"])
        else:
            if profile.get("extract_no_subs", False):
                cmd_parts.append("--no-subtitles")
            if profile.get("extract_no_fonts", False):
                cmd_parts.append("--no-attachments")
            if profile.get("extract_no_chapters", False):
                cmd_parts.append("--no-chapters")
        
        if profile.get("extract_remove_names", False):
            cmd_parts.extend(['--track-name', '0:', '--track-name', '1:'])
        
        cmd_parts.extend(['(', f'"{source_file}"', ')'])
        
        chapter_file = profile.get("extract_chapter_file", "")
        if chapter_file:
            chapter_resolved = chapter_file.replace("<ep>", ep)
            if os.path.exists(chapter_resolved):
                cmd_parts.extend(['--chapter-language', 'zh', '--chapter-charset', 'UTF-8', '--chapters', f'"{chapter_resolved}"'])
        
        cmd_parts.extend(['--track-order', '0:0,0:1'])
        
        command = " ".join(cmd_parts)
        
        self._mux_worker = MKVExtractWorker(mkvmerge_path, command, source_dir)
        self._mux_worker.log_signal.connect(self.mux_log_text.append)
        self._mux_worker.finished_signal.connect(lambda s, m: self._on_extract_finished(s, list_item))
        self._mux_worker.start()

    def _on_extract_finished(self, success: bool, list_item: QListWidgetItem):
        try:
            if list_item is not None:
                widget = self.mux_queue_list.itemWidget(list_item)
                if widget is not None and isinstance(widget, TaskItemWidget):
                    widget.set_status(progress=1.0, status_code=2 if success else 3)
        except RuntimeError:
            pass
        
        if success and self.app_settings.get("auto_ep_mux", True):
            ep = self.extract_ep.text().strip()
            if ep:
                try:
                    next_ep = str(int(ep) + 1).zfill(len(ep))
                    self.extract_ep.setText(next_ep)
                except ValueError: pass

    def _do_subset_task(self, ep: str, profile: Dict, list_item: QListWidgetItem):
        assfontsubset_path = self.app_settings.get("assfontsubset_path", "")
        if not assfontsubset_path or not os.path.exists(assfontsubset_path):
            InfoBar.error(title="错误", content="请先在设置中配置 AssFontSubset.Console.exe 路径", parent=self)
            return
        
        sub_dir_template = profile.get("subset_sub_dir", "")
        font_dir = profile.get("subset_font_dir", "")
        
        # 支持 <ep> 占位符
        sub_dir = sub_dir_template.replace("<ep>", ep)
        
        if not sub_dir or not font_dir:
            InfoBar.error(title="错误", content="请配置字幕目录和字体目录", parent=self)
            return
        
        self._subset_worker = FontSubsetWorker(assfontsubset_path, sub_dir, font_dir)
        self._subset_worker.log_signal.connect(self.subset_log_text.append)
        self._subset_worker.finished_signal.connect(lambda s, m: self._on_subset_finished(s, list_item))
        self._subset_worker.start()

    def _on_subset_finished(self, success: bool, list_item: QListWidgetItem):
        try:
            if list_item is not None:
                widget = self.mux_queue_list.itemWidget(list_item)
                if widget is not None and isinstance(widget, TaskItemWidget):
                    widget.set_status(progress=1.0, status_code=2 if success else 3)
        except RuntimeError:
            pass
        
        if success and self.app_settings.get("auto_ep_mux", True):
            ep = self.subset_ep.text().strip()
            if ep:
                try:
                    next_ep = str(int(ep) + 1).zfill(len(ep))
                    self.subset_ep.setText(next_ep)
                except ValueError: pass

    def _do_mux_task(self, ep: str, profile: Dict, list_item: QListWidgetItem):
        mkvmerge_path = self.app_settings.get("mkvmerge_path", "")
        if not mkvmerge_path or not os.path.exists(mkvmerge_path):
            InfoBar.error(title="错误", content="请先在设置中配置 mkvmerge.exe 路径", parent=self)
            return
        
        video_dir = profile.get("mux_video_dir", "")
        video_name = profile.get("mux_video_name", "").replace("<ep>", ep)
        subset_dir = profile.get("mux_subset_dir", "").replace("<ep>", ep)
        output_dir = profile.get("mux_output_dir", "")
        output_name = profile.get("mux_output_name", "").replace("<ep>", ep)
        
        if not video_dir or not video_name or not output_dir:
            InfoBar.error(title="错误", content="请配置视频目录、视频名和输出目录", parent=self)
            return
        
        output_file = os.path.join(output_dir, output_name)
        video_file = os.path.join(video_dir, video_name)
        
        if not os.path.exists(video_file):
            self.mux_log_text.append(f"❌ 视频文件不存在: {video_file}")
            return
        
        cmd_parts = ['-o', f'"{output_file}"', f'"{video_file}"']
        track_order_parts = ['0:0', '0:1']
        track_index = 1
        
        if subset_dir:
            actual_subset_dir = subset_dir
            
            if subset_dir.endswith("output") or subset_dir.endswith("output" + os.sep) or subset_dir.endswith("output\\"):
                pass
            else:
                potential_output_dir = os.path.join(subset_dir, "output")
                if os.path.exists(potential_output_dir):
                    actual_subset_dir = potential_output_dir
            
            if os.path.exists(actual_subset_dir):
                sc_sub_name = profile.get("mux_sub_sc", "").replace("<ep>", ep)
                tc_sub_name = profile.get("mux_sub_tc", "").replace("<ep>", ep)
                sc_sub_path = os.path.join(actual_subset_dir, sc_sub_name) if sc_sub_name else ""
                tc_sub_path = os.path.join(actual_subset_dir, tc_sub_name) if tc_sub_name else ""
                
                if sc_sub_path and os.path.exists(sc_sub_path):
                    sc_name = profile.get("mux_sub_sc_name", "简体中文")
                    sc_default = "yes" if profile.get("mux_sub_sc_default", True) else "no"
                    cmd_parts.extend(['--language', '0:zh-Hans', '--track-name', f'0:"{sc_name}"', '--default-track', f'0:{sc_default}', '(', f'"{sc_sub_path}"', ')'])
                    track_order_parts.append(f'{track_index}:0')
                    track_index += 1
                
                if tc_sub_path and os.path.exists(tc_sub_path):
                    tc_name = profile.get("mux_sub_tc_name", "繁體中文")
                    tc_default = "yes" if profile.get("mux_sub_tc_default", False) else "no"
                    cmd_parts.extend(['--language', '0:zh-Hant', '--track-name', f'0:"{tc_name}"', '--default-track-flag', f'0:no', '(', f'"{tc_sub_path}"', ')'])
                    track_order_parts.append(f'{track_index}:0')
                    track_index += 1
                
                font_files = []
                for ext in ["*.ttf", "*.otf", "*.ttc"]:
                    font_files.extend(glob.glob(os.path.join(actual_subset_dir, ext)))
                
                for font_file in font_files:
                    font_name = os.path.basename(font_file)
                    ext_lower = os.path.splitext(font_file)[1].lower()
                    mime_type = 'application/vnd.ms-opentype' if ext_lower == '.otf' else 'application/x-truetype-font'
                    cmd_parts.extend(['--attachment-name', font_name, '--attachment-mime-type', mime_type, '--attach-file', f'"{font_file}"'])
        
        cmd_parts.extend(['--track-order', ','.join(track_order_parts)])
        
        command = " ".join(cmd_parts)
        
        self._mux_worker = MKVExtractWorker(mkvmerge_path, command, video_dir)
        self._mux_worker.log_signal.connect(self.mux_log_text.append)
        self._mux_worker.finished_signal.connect(lambda s, m: self._on_mux_finished(s, list_item))
        self._mux_worker.start()

    def _on_mux_finished(self, success: bool, list_item: QListWidgetItem):
        try:
            if list_item is not None:
                widget = self.mux_queue_list.itemWidget(list_item)
                if widget is not None and isinstance(widget, TaskItemWidget):
                    widget.set_status(progress=1.0, status_code=2 if success else 3)
        except RuntimeError:
            pass
        
        if success and self.app_settings.get("auto_ep_mux", True):
            ep = self.mux_ep.text().strip()
            if ep:
                try:
                    next_ep = str(int(ep) + 1).zfill(len(ep))
                    self.mux_ep.setText(next_ep)
                except ValueError: pass

    def _clear_mux_queue(self):
        self.mux_queue_list.clear()

    def _start_extract(self):
        ep = self.extract_ep.text().strip()
        if not ep:
            InfoBar.warning(title="警告", content="请输入集数", parent=self)
            return
        if not self.current_profile_name:
            InfoBar.warning(title="警告", content="请先选择一个配置", parent=self)
            return
        
        profile = copy.deepcopy(self.profiles[self.current_profile_name])
        
        list_item = QListWidgetItem()
        task_data = {
            "type": "extract",
            "ep": ep,
            "profile": profile,
            "desc": f"[提取] EP{ep} - {self.current_profile_name}"
        }
        list_item.setData(Qt.ItemDataRole.UserRole, task_data)
        
        item_widget = TaskItemWidget(task_data["desc"])
        list_item.setSizeHint(item_widget.sizeHint())
        
        self._do_extract_task(ep, profile, list_item)

    def _start_subset(self):
        ep = self.subset_ep.text().strip()
        if not ep:
            InfoBar.warning(title="警告", content="请输入集数", parent=self)
            return
        if not self.current_profile_name:
            InfoBar.warning(title="警告", content="请先选择一个配置", parent=self)
            return
        
        profile = copy.deepcopy(self.profiles[self.current_profile_name])
        self._do_subset_task(ep, profile, None)

    def _start_mux(self):
        ep = self.mux_ep.text().strip()
        if not ep:
            InfoBar.warning(title="警告", content="请输入集数", parent=self)
            return
        if not self.current_profile_name:
            InfoBar.warning(title="警告", content="请先选择一个配置", parent=self)
            return
        
        profile = copy.deepcopy(self.profiles[self.current_profile_name])
        self._do_mux_task(ep, profile, None)

    def closeEvent(self, event):
        self.logger.info("[EncodeManager] 触发关闭事件")
        
        rect = self.geometry().getRect()
        self.app_settings["window_geometry"] = rect
        self._save_app_settings()
        
        close_behavior = self.app_settings.get("close_behavior", "tray")
        
        if close_behavior == "tray" and not getattr(self, '_force_quit', False):
            self.logger.info("[EncodeManager] 最小化到系统托盘")
            event.ignore()
            self.hide()
            self.tray_icon.show()
            self.tray_icon.showMessage(
                "smzase 压制工具",
                "程序已最小化到系统托盘，双击图标可恢复窗口。",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
            return
        
        if self.is_running:
            reply = QMessageBox.question(
                self, '退出', '任务运行中，确定强制退出？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.logger.info("[EncodeManager] 用户确认强制退出，任务运行中")
                self._force_stop()
                self.tray_icon.hide()
                if hasattr(self, '_single_instance_server'):
                    self._single_instance_server.close()
                self.logger.info("[EncodeManager] 应用程序退出")
                event.accept()
                QApplication.quit()
            else: event.ignore()
        else:
            self._save_current_profile_meta()
            self.tray_icon.hide()
            if hasattr(self, '_single_instance_server'):
                self._single_instance_server.close()
            self.logger.info("[EncodeManager] 应用程序正常退出")
            event.accept()
            QApplication.quit()

# ==================== 程序入口 ====================
def global_exception_handler(exc_type, exc_value, exc_traceback):
    """全局异常处理器，捕获未处理的异常并记录到日志"""
    logger = get_logger()
    error_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logger.critical(f"未捕获的异常导致程序崩溃:\n{error_msg}")
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

def qt_message_handler(mode, context, message):
    """Qt 消息处理器，捕获 Qt 内部消息"""
    logger = get_logger()
    if mode == 0:
        logger.debug(f"[Qt] {message}")
    elif mode == 1:
        logger.warning(f"[Qt Warning] {message}")
    elif mode == 2:
        logger.error(f"[Qt Critical] {message}")
    elif mode == 3:
        logger.critical(f"[Qt Fatal] {message}")

def main():
    sys.excepthook = global_exception_handler
    qInstallMessageHandler(qt_message_handler)
    
    single_instance = SingleInstance()
    
    if not single_instance.try_lock():
        if notify_existing_instance():
            sys.exit(0)
        else:
            sys.exit(0)
    
    app = QApplication(sys.argv)
    window = EncodeManager()
    window._single_instance = single_instance
    window.show()
    result = app.exec()
    single_instance.release()
    sys.exit(result)

if __name__ == "__main__":
    main()
