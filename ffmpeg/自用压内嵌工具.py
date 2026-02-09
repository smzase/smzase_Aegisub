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

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QMimeData, QRect
from PyQt6.QtGui import QColor, QAction, QPalette
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QFileDialog, QFrame, QListWidgetItem, QAbstractItemView,
    QSplitter, QGridLayout, QMessageBox, QInputDialog, QDialog, 
    QStackedWidget, QDialogButtonBox, QLabel, QButtonGroup
)

from qfluentwidgets import (
    SubtitleLabel, LineEdit, PushButton, PrimaryPushButton, TextEdit,
    CardWidget, StrongBodyLabel, CaptionLabel, InfoBar,
    setTheme, Theme, ListWidget, FluentIcon as FIF,
    Action, RoundMenu, CheckBox, ToolButton, setThemeColor,
    SegmentedWidget, Pivot
)

# ==================== 常量与全局配置 ====================
APP_NAME = "ffmpeg_smzase"

# 硬件与编码列表
HARDWARE_LIST = ["CPU", "QSV", "NVENC", "VCN"]
CODEC_LIST = ["X264", "X265", "X266", "AV1"]

# 基础默认参数生成器 (默认为空)
def get_default_param_matrix():
    """生成 4x4 的默认参数矩阵，值为空"""
    matrix = {}
    for hw in HARDWARE_LIST:
        matrix[hw] = {}
        for codec in CODEC_LIST:
            matrix[hw][codec] = {
                "crf": "",
                "pass1": "",
                "pass2": ""
            }
    return matrix

# 默认参数模板
DEFAULT_PARAMS_TEMPLATE = get_default_param_matrix()

# ==================== 自定义控件 (修复粘贴格式问题) ====================
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
    """FFmpeg 进度显示面板"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(90)
        self.start_timestamp = 0
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)
        
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
        v_layout.setSpacing(2)
        
        title_label = CaptionLabel(title)
        title_label.setTextColor(QColor(100, 100, 100), QColor(150, 150, 150))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        value_label = SubtitleLabel(default_val)
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
    progress_signal = pyqtSignal(str)
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

    def run(self):
        self.start_time = time.time()
        self.log_signal.emit(f"Working Directory: {self.work_dir}")
        
        total_steps = len(self.commands)
        
        for index, cmd in enumerate(self.commands):
            if self.is_killed: break
            if total_steps > 1:
                step_name = 'Pass 1' if index == 0 else 'Pass 2'
                self.log_signal.emit(f"🔄 正在执行 [Step {index + 1}/{total_steps}]: {step_name}")
            self.log_signal.emit(f"Executing: {cmd}")
            
            if not self._execute_command(cmd):
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

    def _execute_command(self, cmd: str) -> bool:
        try:
            self.process = subprocess.Popen(
                cmd, cwd=self.work_dir, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding='utf-8', errors='replace', text=True, bufsize=1
            )
            while True:
                if self.is_killed: break
                line = self.process.stdout.readline()
                if not line and self.process.poll() is not None: break
                if line: self._process_output_line(line.strip())

            return_code = self.process.poll()
            if self.is_killed: return False
            if return_code == 0: return True
            self.log_signal.emit(f"🔴 [错误] FFmpeg 异常退出，代码 {return_code}")
            return False
        except Exception as e:
            self.log_signal.emit(f"🔴 [系统异常] 无法启动进程: {str(e)}")
            return False

    def _process_output_line(self, line: str):
        if "frame=" in line and "time=" in line:
            current_time = time.time()
            if current_time - self.last_update_time > 0.5:
                self.progress_signal.emit(line)
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
        # 默认使用 Documents/ffmpeg smzase 作为配置目录
        default_docs = os.path.join(os.path.expanduser("~"), "Documents")
        default_folder = "ffmpeg smzase"
        self.default_config_base = os.path.join(default_docs, default_folder)
        
        # 尝试创建默认目录（如果不存在）
        if not os.path.exists(self.default_config_base):
            try:
                os.makedirs(self.default_config_base, exist_ok=True)
            except:
                self.default_config_base = os.getcwd() # 回退到当前目录

        # 2. 预设设置文件路径 (默认在 default_config_base 下)
        self.app_settings_file = os.path.join(self.default_config_base, "app_settings.json")
        
        # 3. 加载设置
        # 注意：如果加载的设置中定义了 custom base_path，_get_config_dir 会改变
        self.app_settings = self._load_app_settings()
        
        # 4. 确定最终的 config_dir
        # _get_config_dir 会基于加载后的 settings 判断路径
        self.config_dir = self._get_config_dir()
        self.profile_file = os.path.join(self.config_dir, "profiles.json")
        
        # 5. 修正 app_settings_file 的位置
        # 确保 app_settings.json 和 profiles.json 在同一个目录下
        # 这样下次启动时，如果 config_dir 变了，我们需要在 default_config_base 下保留一个指针
        # 或者简化为：必须把 settings 文件放在实际的 config_dir 下
        self.app_settings_file = os.path.join(self.config_dir, "app_settings.json")
        
        # 保存一次设置，确保文件存在于目标目录
        self._save_app_settings()
        
        # 6. 恢复窗口状态和主题
        self._restore_window_geometry()
        
        self.profiles: Dict = {}
        self.current_profile_name: Optional[str] = None
        self.completed_history: set = set()
        self.force_stop_flag = False
        
        self.worker: Optional[Worker] = None
        self.is_running = False
        self.current_item: Optional[QListWidgetItem] = None
        
        # 从设置中加载主题配置
        theme_mode = self.app_settings.get("theme_mode", "light")
        self.is_dark = (theme_mode == "dark")
        self._apply_theme(self.is_dark)
        
        self._init_ui()
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
        # 优先尝试从 self.app_settings_file 加载（该路径在 __init__ 中被初始化为默认文档路径）
        # 如果当前目录下也有，作为备用或迁移逻辑
        
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
        
        # 默认配置
        default_settings = {
            "base_path": default_docs,
            "use_sub_folder": True,
            "folder_name": "ffmpeg smzase",
            "default_params_matrix": DEFAULT_PARAMS_TEMPLATE,
            "window_geometry": None,
            "theme_mode": "light" # 默认浅色
        }
        
        # 合并配置（保留默认值中存在但文件中没有的键）
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
        
        # 保持 geometry 和 theme_mode 不被设置页覆盖
        if "window_geometry" not in new_settings:
            new_settings["window_geometry"] = self.app_settings.get("window_geometry")
        if "theme_mode" not in new_settings:
            new_settings["theme_mode"] = self.app_settings.get("theme_mode", "light")
            
        self.app_settings = new_settings
        
        # 如果路径改变，需要重新计算 config_dir 并移动 settings 文件指针
        if path_changed:
            self.config_dir = self._get_config_dir()
            self.app_settings_file = os.path.join(self.config_dir, "app_settings.json")
            self.profile_file = os.path.join(self.config_dir, "profiles.json")
            
        self._save_app_settings()
        if path_changed:
             QMessageBox.information(self, "路径变更", "路径设置已修改，配置文件将保存至新路径。\n注意：旧路径下的文件不会自动移动，请手动迁移 Profiles。")

    def _save_app_settings(self):
        try:
            # 确保目录存在
            parent_dir = os.path.dirname(self.app_settings_file)
            if not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
                
            with open(self.app_settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.app_settings, f, ensure_ascii=False, indent=4)
        except Exception as e:
            # 在 UI 初始化前可能调用，需做保护
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
        
        layout.addWidget(SubtitleLabel("番剧配置列表"))
        
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
        layout.addWidget(self._create_bottom_splitter(), 1)
        return panel

    def _create_settings_card(self) -> CardWidget:
        card = CardWidget()
        layout = QGridLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setVerticalSpacing(10)
        layout.setHorizontalSpacing(15)
        
        self.entry_source_dir = self._add_folder_row(layout, 0, "视频源目录")
        self.entry_sub_dir = self._add_folder_row(layout, 1, "字幕目录 (CD)")
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
        
        layout.addWidget(StrongBodyLabel("源文件模板:"), 0, 0)
        self.entry_source_template = PlainLineEdit()
        self.entry_source_template.setPlaceholderText("Title - S01E<ep>.mkv")
        layout.addWidget(self.entry_source_template, 0, 1)
        
        layout.addWidget(StrongBodyLabel("简中字幕模板:"), 1, 0)
        self.entry_sub_sc = PlainLineEdit()
        layout.addWidget(self.entry_sub_sc, 1, 1)
        
        layout.addWidget(StrongBodyLabel("繁中字幕模板:"), 1, 2)
        self.entry_sub_tc = PlainLineEdit()
        layout.addWidget(self.entry_sub_tc, 1, 3)
        
        layout.addWidget(StrongBodyLabel("简中输出模板:"), 2, 0)
        self.entry_out_sc = PlainLineEdit()
        layout.addWidget(self.entry_out_sc, 2, 1)
        
        layout.addWidget(StrongBodyLabel("繁中输出模板:"), 2, 2)
        self.entry_out_tc = PlainLineEdit()
        layout.addWidget(self.entry_out_tc, 2, 3)
        return group

    def _add_params_section(self, layout: QGridLayout):
        control_bar_layout = QHBoxLayout()
        
        control_bar_layout.addWidget(CaptionLabel("硬件:"))
        self.seg_hardware = SegmentedWidget()
        for hw in HARDWARE_LIST:
            self.seg_hardware.addItem(routeKey=hw, text=hw)
        self.seg_hardware.setCurrentItem("CPU")
        self.seg_hardware.currentItemChanged.connect(self._on_selector_changed)
        control_bar_layout.addWidget(self.seg_hardware)
        
        control_bar_layout.addSpacing(20)
        
        control_bar_layout.addWidget(CaptionLabel("编码:"))
        self.seg_codec = SegmentedWidget()
        for codec in CODEC_LIST:
            self.seg_codec.addItem(routeKey=codec, text=codec)
        self.seg_codec.setCurrentItem("X264")
        self.seg_codec.currentItemChanged.connect(self._on_selector_changed)
        control_bar_layout.addWidget(self.seg_codec)
        
        control_bar_layout.addStretch(1) 
        
        self.chk_2pass = CheckBox("启用 2-Pass Mode")
        self.chk_2pass.stateChanged.connect(self._toggle_2pass_ui)
        self.chk_2pass.stateChanged.connect(self._save_current_profile_meta)
        control_bar_layout.addWidget(self.chk_2pass)
        
        layout.addLayout(control_bar_layout, 4, 0, 1, 3)

        self.lbl_crf = StrongBodyLabel("CRF/单次参数:")
        layout.addWidget(self.lbl_crf, 5, 0, Qt.AlignmentFlag.AlignTop)
        
        self.text_params_crf = PlainTextEdit()
        self.text_params_crf.setFixedHeight(80)
        self.text_params_crf.textChanged.connect(self._save_current_param_text)
        layout.addWidget(self.text_params_crf, 5, 1, 1, 2)
        
        self.lbl_pass1 = StrongBodyLabel("Pass 1 参数:")
        layout.addWidget(self.lbl_pass1, 6, 0, Qt.AlignmentFlag.AlignTop)
        
        self.text_params_pass1 = PlainTextEdit()
        self.text_params_pass1.setFixedHeight(80)
        self.text_params_pass1.textChanged.connect(self._save_current_param_text)
        layout.addWidget(self.text_params_pass1, 6, 1, 1, 2)
        
        self.lbl_pass2 = StrongBodyLabel("Pass 2 参数:")
        layout.addWidget(self.lbl_pass2, 7, 0, Qt.AlignmentFlag.AlignTop)
        
        self.text_params_pass2 = PlainTextEdit()
        self.text_params_pass2.setFixedHeight(80)
        self.text_params_pass2.textChanged.connect(self._save_current_param_text)
        layout.addWidget(self.text_params_pass2, 7, 1, 1, 2)
        
        self._toggle_2pass_ui(0)

    def _create_control_card(self) -> CardWidget:
        card = CardWidget()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(20, 15, 20, 15)
        
        layout.addWidget(SubtitleLabel("当前集数 <ep>:"))
        self.entry_ep = PlainLineEdit()
        self.entry_ep.setFixedWidth(80)
        self.entry_ep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.entry_ep.setPlaceholderText("01")
        self.entry_ep.textChanged.connect(self._save_current_profile_meta)
        layout.addWidget(self.entry_ep)
        
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
        
        btn_add_sc = PushButton(FIF.ADD, "添加简体")
        btn_add_sc.clicked.connect(lambda: self._add_to_queue("SC"))
        layout.addWidget(btn_add_sc)
        
        btn_add_tc = PushButton(FIF.ADD, "添加繁体")
        btn_add_tc.clicked.connect(lambda: self._add_to_queue("TC"))
        layout.addWidget(btn_add_tc)
        
        btn_add_both = PrimaryPushButton(FIF.ADD, "添加简繁双语")
        btn_add_both.clicked.connect(lambda: self._add_to_queue("BOTH"))
        layout.addWidget(btn_add_both)
        return card

    def _create_bottom_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._create_queue_panel())
        splitter.addWidget(self._create_log_panel())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        return splitter

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
        
        self.queue_list = ListWidget()
        self.queue_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.queue_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue_list.customContextMenuRequested.connect(self._show_queue_context_menu)
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
        self.btn_stop = PushButton(FIF.CLOSE, "强制终止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet("QPushButton { color: red; }")
        self.btn_stop.clicked.connect(self._force_stop)
        header.addWidget(self.btn_stop)
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

    # ==================== 逻辑功能实现 ====================
    def _toggle_theme(self):
        self.is_dark = not self.is_dark
        # 保存主题设置
        self.app_settings["theme_mode"] = "dark" if self.is_dark else "light"
        self._save_app_settings()
        # 应用
        self._apply_theme(self.is_dark)
        
    def _apply_theme(self, is_dark: bool):
            # === 原有逻辑：设置 QFluentWidgets 主题和内部样式 ===
            if is_dark:
                setTheme(Theme.DARK)
                setThemeColor("#FF9900")
                self.setStyleSheet("""
                    QMainWindow { background-color: #303030; }
                    QListWidget { background-color: #383838; border: 1px solid #454545; color: white; }
                    QListWidget::item:selected { background-color: #454545; }
                    QTextEdit, QPlainTextEdit, LineEdit, PlainLineEdit { 
                        background-color: #383838; color: white; border: 1px solid #454545; border-radius: 4px;
                    }
                    QSplitter::handle { background-color: #505050; height: 2px; width: 2px; }
                    SubtitleLabel#MetricValue { color: #FF9900; }
                """)
                if hasattr(self, 'lbl_pass1'): self.lbl_pass1.setStyleSheet("color: #FF9900; font-weight: bold;")
                if hasattr(self, 'lbl_pass2'): self.lbl_pass2.setStyleSheet("color: #FF9900; font-weight: bold;")
            else:
                setTheme(Theme.LIGHT)
                setThemeColor("#009FAA")
                self.setStyleSheet("""
                    QMainWindow { background-color: #f3f3f3; }
                    QSplitter::handle { background-color: #d0d0d0; }
                    SubtitleLabel#MetricValue { color: #0078D7; }
                """)
                if hasattr(self, 'lbl_pass1'): self.lbl_pass1.setStyleSheet("color: #0078D7; font-weight: bold;")
                if hasattr(self, 'lbl_pass2'): self.lbl_pass2.setStyleSheet("color: #0078D7; font-weight: bold;")

            # === 新增逻辑：强制刷新 Windows 标题栏颜色 ===
            if sys.platform == "win32":
                try:
                    # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (适用于 Win10 20H1+ 和 Win11)
                    hwnd = int(self.winId())
                    value = c_int(1 if is_dark else 0)
                    windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, 20, byref(value), sizeof(value)
                    )
                    # 强制触发重绘以立即更新标题栏
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
        params = params_matrix.get(hw, {}).get(codec, {"crf": "", "pass1": "", "pass2": ""})
        
        self.text_params_crf.blockSignals(True)
        self.text_params_pass1.blockSignals(True)
        self.text_params_pass2.blockSignals(True)
        
        self.text_params_crf.setPlainText(params.get("crf", ""))
        self.text_params_pass1.setPlainText(params.get("pass1", ""))
        self.text_params_pass2.setPlainText(params.get("pass2", ""))
        
        self.text_params_crf.blockSignals(False)
        self.text_params_pass1.blockSignals(False)
        self.text_params_pass2.blockSignals(False)

    def _save_current_param_text(self):
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
            "enable_2pass": self.chk_2pass.isChecked()
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
            "enable_2pass": False,
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
        
        enable_2pass = data.get("enable_2pass", False)
        self.chk_2pass.setChecked(enable_2pass)
        self._toggle_2pass_ui(2 if enable_2pass else 0)
        
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
                        new_matrix["CPU"]["X264"] = {
                            "crf": pdata.get("params_crf", ""),
                            "pass1": pdata.get("params_pass1", ""),
                            "pass2": pdata.get("params_pass2", "")
                        }
                    pdata["param_matrix"] = new_matrix
                    pdata["selected_hw"] = "CPU"
                    pdata["selected_codec"] = "X264"

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
    def _add_to_queue(self, mode: str):
        if not self.current_profile_name:
            InfoBar.warning(title="警告", content="请先选择一个配置", parent=self)
            return
        ep = self.entry_ep.text().strip()
        if not ep:
            InfoBar.warning(title="警告", content="请输入集数", parent=self)
            return
        
        profile = copy.deepcopy(self.profiles[self.current_profile_name])
        
        use_suffix = self.chk_suffix.isChecked()
        suffix_text = self.entry_suffix.text().strip() if use_suffix else ""
        
        tasks = []
        if mode in ["SC", "BOTH"]: tasks.append({"type": "SC", "ep": ep, "profile": profile, "suffix": suffix_text})
        if mode in ["TC", "BOTH"]: tasks.append({"type": "TC", "ep": ep, "profile": profile, "suffix": suffix_text})
        
        for task in tasks:
            display_suffix = f" {task['suffix']}" if task['suffix'] else ""
            mode_tag = " [2-Pass]" if profile.get('enable_2pass') else ""
            desc = f"[{task['type']}] EP{task['ep']}{display_suffix}{mode_tag} - {self.current_profile_name}"
            
            item_data = {
                "id": f"{datetime.now().timestamp()}",
                "desc": desc, "status": "pending", "raw_task": task
            }
            list_item = QListWidgetItem(desc)
            list_item.setData(Qt.ItemDataRole.UserRole, item_data)
            self.queue_list.addItem(list_item)
            if not task['suffix']:
                key = (task['ep'], task['type'])
                self.completed_history.discard(key)
        self.queue_list.scrollToBottom()

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
        target_item.setBackground(QColor(0, 120, 215, 50))
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
        params_set = params_matrix.get(hw, {}).get(codec, {"crf": "", "pass1": "", "pass2": ""})
        
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
        
        commands = self._build_ffmpeg_commands(
            profile, params_set, full_src, full_sub, sub_name, full_out
        )
        temp_files = self._get_temp_files(profile) if profile.get('enable_2pass') else []
        self.progress_dashboard.start_timer()
        self.worker = Worker(commands, profile['sub_dir'], full_out, temp_files)
        self.worker.log_signal.connect(self._log_to_ui)
        self.worker.progress_signal.connect(self._update_progress)
        self.worker.finished_signal.connect(self._task_finished_callback)
        self.worker.start()

    def _build_ffmpeg_commands(self, profile: Dict, params_set: Dict, full_src: str, 
                               full_sub: str, sub_name: str, full_out: str) -> List[str]:
        is_2pass = profile.get('enable_2pass', False)
        commands = []
        if is_2pass:
            params_1 = self._sanitize_params(params_set.get("pass1", ""))
            params_2 = self._sanitize_params(params_set.get("pass2", ""))
            has_pass1 = "-pass 1" in params_1 or "-pass=1" in params_1
            has_pass2 = "-pass 2" in params_2 or "-pass=2" in params_2
            has_passlog1 = "-passlogfile" in params_1
            has_passlog2 = "-passlogfile" in params_2
            pass_log_path = os.path.join(profile['sub_dir'], "ffmpeg2pass")
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

    def _get_temp_files(self, profile: Dict) -> List[str]:
        pass_log_path = os.path.join(profile['sub_dir'], "ffmpeg2pass")
        return [f"{pass_log_path}-0.log", f"{pass_log_path}-0.log.mbtree"]

    def _log_to_ui(self, text: str):
        self.text_log.append(text)
        scrollbar = self.text_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _update_progress(self, text: str):
        self.progress_dashboard.update_data(text)

    def _task_finished_callback(self, success: bool, output_file: str, duration: str):
        if self.force_stop_flag:
            self._handle_forced_cleanup(output_file)
            return
        if not self.current_item: return
        data = self.current_item.data(Qt.ItemDataRole.UserRole)
        task_info = data['raw_task']
        if success:
            data['status'] = 'done'
            self.current_item.setBackground(QColor(0, 255, 0, 30))
            self.current_item.setText(f"✅ {data['desc']} [耗时: {duration}]")
            if not task_info.get('suffix'):
                self.completed_history.add((task_info['ep'], task_info['type']))
                self._check_auto_increment(task_info['ep'])
        else:
            data['status'] = 'error'
            self.current_item.setBackground(QColor(255, 0, 0, 30))
            self.current_item.setText(f"❌ {data['desc']} [失败]")
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
        self._log_to_ui("=== 队列完成 ===")
        InfoBar.success(title="完成", content="所有任务已结束", parent=self)

    def _force_stop(self):
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
                self.current_item.setText(f"⏹ {data['desc']} (已终止)")
                self.current_item.setBackground(QColor(255, 0, 0, 30))
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
    setTheme(Theme.LIGHT)
    window = EncodeManager()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
