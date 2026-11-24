"""PySide6 图形界面。

给不想碰命令行的人用：拖进来、选格式、点开始。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPalette, QShortcut  # noqa: E402
from app.single_instance import SingleInstance  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QStyle,
    QTableWidgetItem,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import ffbin, formats  # noqa: E402
from core.formats import EncodeOptions  # noqa: E402
from core.pipeline import JobResult, process  # noqa: E402
from platforms import base as platform_base  # noqa: E402

APP_NAME = "音乐解锁"
PLATFORM_LABELS = {
    "qqmusic": "QQ 音乐",
    "netease": "网易云",
    "kugou": "酷狗",
    "kuwo": "酷我",
}
COLUMNS = ["文件", "平台", "源格式", "时长", "目标格式", "状态"]

FORMAT_CHOICES: list[tuple[str | None, str]] = [(None, "跟随源格式")] + [
    (f.key, f.label) for f in formats.ALL
]
BITRATE_CHOICES = [(None, "自动"), (128, "128 kbps"), (192, "192 kbps"),
                   (256, "256 kbps"), (320, "320 kbps")]

#: 这些容器本身就是无损的，转成无损格式才有意义
LOSSLESS_CONTAINERS = frozenset({
    "flac", "wav", "aiff", "alac", "ape", "wavpack", "wv", "dff", "dsf",
})
WARN_LOSSY = "提示：源是有损格式"

# --------------------------------------------------------------------------- #
# 主题
# --------------------------------------------------------------------------- #

#: 点关闭按钮时的行为
CLOSE_CHOICES: list[tuple[str, str]] = [
    ("ask", "每次询问"),
    ("exit", "直接退出"),
    ("minimize", "缩到托盘（后台运行）"),
]

#: 界面主题选项（存进 QSettings 的是 key）
THEME_CHOICES: list[tuple[str, str]] = [
    ("light", "浅色"),
    ("dark", "深色"),
    ("system", "跟随系统"),
]

#: 浅色是白底黑字，深色是深灰底白字。语义色（成功/失败/警告）单独调过，
#: 保证在两种背景下都有足够对比度。
PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#ffffff",
        "panel": "#ffffff",
        "border": "#dcdfe4",
        "text": "#1a1d21",
        "muted": "#6b7280",
        "btn": "#f5f6f8",
        "btn_hover": "#ebedf0",
        "btn_disabled": "#a8adb5",
        "header": "#f7f8fa",
        "grid": "#eff1f4",
        "accent": "#2f6fed",
        "accent_hover": "#2a63d4",
        "accent_dim": "#b9c8ea",
        "ok": "#1a7f37",
        "error": "#c0392b",
        "warn": "#a06a00",
    },
    "dark": {
        "bg": "#232427",
        "panel": "#2b2d31",
        "border": "#3b3e44",
        "text": "#f2f3f5",
        "muted": "#9aa1ab",
        "btn": "#33363b",
        "btn_hover": "#3f434a",
        "btn_disabled": "#6b7178",
        "header": "#2f3237",
        "grid": "#363940",
        "accent": "#4d8dff",
        "accent_hover": "#3f7cea",
        "accent_dim": "#33415c",
        "ok": "#5fd07f",
        "error": "#ff6b6b",
        "warn": "#f0b429",
    },
}


def _diag(text: str) -> None:
    """把启动期的诊断信息追加到日志文件，方便排查打包后的问题。"""
    import sys as _sys

    try:
        base = (
            Path(_sys.executable).resolve().parent
            if getattr(_sys, "frozen", False)
            else Path(__file__).resolve().parent.parent
        )
        with open(base / "tray_diag.log", "a", encoding="utf-8") as handle:
            handle.write(text + chr(10))
    except Exception:
        pass


def tray_shell_state() -> str:
    """用 Shell API 查托盘图标是否真的被系统接受（Qt 的 isVisible 不一定可信）。"""
    import ctypes
    from ctypes import wintypes

    try:
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        pid = ctypes.windll.kernel32.GetCurrentProcessId()

        class RECT(ctypes.Structure):
            _fields_ = [("l", wintypes.LONG), ("t", wintypes.LONG),
                        ("r", wintypes.LONG), ("b", wintypes.LONG)]

        class NID(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                        ("uID", wintypes.UINT), ("guid", ctypes.c_byte * 16)]

        found: list[int] = []

        def visit(hwnd, _):
            wpid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == pid:
                buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, buf, 256)
                if "TrayIcon" in buf.value:
                    found.append(hwnd)
            return True

        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
        user32.EnumWindows(callback, 0)
        for hwnd in found:
            for uid in range(8):
                nid = NID()
                nid.cbSize = ctypes.sizeof(NID)
                nid.hWnd = hwnd
                nid.uID = uid
                rect = RECT()
                if shell32.Shell_NotifyIconGetRect(ctypes.byref(nid), ctypes.byref(rect)) == 0:
                    return f"已注册 uid={uid} 位置=({rect.l},{rect.t})"
        return f"未注册（托盘窗口 {len(found)} 个）"
    except Exception as exc:  # pragma: no cover - 诊断用
        return f"检查异常: {type(exc).__name__}: {exc}"


def asset_path(relative: str) -> Path | None:
    """找随程序分发的资源（开发时在项目根，打包后在 _internal 下）。"""
    import sys as _sys

    roots: list[Path] = []
    if getattr(_sys, "frozen", False):
        exe_dir = Path(_sys.executable).resolve().parent
        roots.append(exe_dir / "_internal")
        roots.append(exe_dir)
        meipass = getattr(_sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass))
    roots.append(Path(__file__).resolve().parent.parent)
    for root in roots:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def build_palette(dark: bool) -> QPalette:
    """给 Qt 原生绘制的东西（下拉箭头、勾选框等）用的调色板。

    只设样式表不够：下拉框的小三角是 style 画出来的，颜色取自 QPalette，
    深色主题下不一起改就会变成"深色箭头配深色背景"，等于看不见。
    """
    c = PALETTES["dark" if dark else "light"]
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(c["bg"]))
    p.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    p.setColor(QPalette.ColorRole.Base, QColor(c["panel"]))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(c["header"]))
    p.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    p.setColor(QPalette.ColorRole.Button, QColor(c["btn"]))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    p.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["panel"]))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
    try:
        p.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["muted"]))
    except AttributeError:
        pass
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["muted"]))
    return p


def build_style(dark: bool) -> str:
    """按主题生成整份样式表。"""
    c = PALETTES["dark" if dark else "light"]
    return f"""
QMainWindow, QWidget {{ background: {c['bg']}; color: {c['text']}; }}
QPushButton {{
    background: {c['btn']}; border: 1px solid {c['border']}; border-radius: 6px;
    padding: 6px 14px; color: {c['text']};
}}
QPushButton:hover {{ background: {c['btn_hover']}; }}
QPushButton:disabled {{ color: {c['btn_disabled']}; border-color: {c['border']}; }}
QPushButton#primary {{
    background: {c['accent']}; border: none; color: #ffffff; font-weight: 600;
    padding: 9px 20px;
}}
QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
QPushButton#primary:disabled {{ background: {c['accent_dim']}; }}
QTableWidget {{
    background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 8px;
    gridline-color: {c['grid']}; color: {c['text']};
}}
QHeaderView::section {{
    background: {c['header']}; border: none; border-bottom: 1px solid {c['border']};
    padding: 7px 6px; color: {c['muted']}; font-weight: 600;
}}
QLineEdit, QComboBox, QTextEdit {{
    background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px;
    padding: 5px 8px; color: {c['text']};
}}
QComboBox QAbstractItemView {{
    background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']};
    selection-background-color: {c['accent']}; selection-color: #ffffff;
}}
QProgressBar {{
    border: 1px solid {c['border']}; border-radius: 5px; background: {c['panel']};
    text-align: center; height: 14px; color: {c['text']};
}}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {c['border']}; border-radius: 5px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QToolTip {{
    background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']};
}}
"""


class Worker(QThread):
    """在后台线程里按顺序处理队列，任何结果都不直接碰界面。"""

    row_stage = Signal(int, str)
    row_progress = Signal(int, float)
    row_done = Signal(int, object)
    all_done = Signal()

    def __init__(self, jobs: list[tuple[int, Path, str | None]], out_dir: Path,
                 opts: EncodeOptions, parent=None) -> None:
        super().__init__(parent)
        self.jobs = jobs
        self.out_dir = out_dir
        self.opts = opts
        self.cancel_flag = threading.Event()

    def run(self) -> None:
        for row, path, fmt_key in self.jobs:
            if self.cancel_flag.is_set():
                break
            target = formats.get(fmt_key) if fmt_key else None
            result: JobResult = process(
                path, self.out_dir, target=target, opts=self.opts,
                on_stage=lambda text, r=row: self.row_stage.emit(r, text),
                on_progress=lambda value, r=row: self.row_progress.emit(r, value),
                cancel=self.cancel_flag,
            )
            self.row_done.emit(row, result)
        self.all_done.emit()

    def stop(self) -> None:
        self.cancel_flag.set()


class SettingsDialog(QDialog):
    """设置面板：目前放主题和关闭行为，以后可以继续往里加。"""

    def __init__(self, parent, theme_mode: str, close_action: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(18, 18, 18, 14)
        form.setSpacing(12)

        self.cmb_theme = QComboBox()
        for key, label in THEME_CHOICES:
            self.cmb_theme.addItem(label, key)
        self.cmb_theme.setCurrentIndex(max(0, self.cmb_theme.findData(theme_mode)))
        form.addRow("界面主题", self.cmb_theme)

        self.cmb_close = QComboBox()
        for key, label in CLOSE_CHOICES:
            self.cmb_close.addItem(label, key)
        self.cmb_close.setCurrentIndex(max(0, self.cmb_close.findData(close_action)))
        form.addRow("关闭窗口时", self.cmb_close)

        hint = QLabel(
            "选「缩到托盘」后，点关闭按钮窗口会隐藏到右下角托盘，任务栏不再显示，"
            "转换任务继续运行；双击托盘图标可以切回主界面。选「直接退出」则关闭即结束程序。"
        )
        hint.setWordWrap(True)
        muted = parent.color("muted").name() if hasattr(parent, "color") else "#7a828e"
        hint.setStyleSheet(f"color: {muted};")
        form.addRow(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[str, str]:
        return self.cmb_theme.currentData(), self.cmb_close.currentData()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("music-unlock", "MusicUnlock")
        self.worker: Worker | None = None
        self.rows: list[Path] = []
        self.theme_mode: str = str(self.settings.value("theme", "system"))
        self.close_action: str = str(self.settings.value("close_action", "ask"))
        self.dark: bool = False

        self.setWindowTitle(f"{APP_NAME} · QQ音乐 / 网易云 / 酷狗 / 酷我")
        self.resize(1040, 660)
        self.setAcceptDrops(True)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addLayout(self._build_toolbar())
        layout.addWidget(self._build_table(), 1)
        layout.addWidget(self._build_footer())

        self.tray: QSystemTrayIcon | None = None
        self._tray_hint_shown = False
        self._force_quit = False
        self._check_environment()
        self.apply_theme()
        # 等事件循环跑起来再建托盘，避免窗口还没显示就被 shell 拒绝
        QTimer.singleShot(0, self._setup_tray)

        # 系统主题变化时，"跟随系统"要立刻跟上
        try:
            QApplication.instance().styleHints().colorSchemeChanged.connect(
                self._on_system_scheme_changed
            )
        except (AttributeError, TypeError):
            pass  # 老版本 Qt 没有这个信号，静默降级

    # -- 主题 ------------------------------------------------------------ #

    def _resolve_dark(self) -> bool:
        if self.theme_mode == "dark":
            return True
        if self.theme_mode == "light":
            return False
        try:
            return QApplication.instance().styleHints().colorScheme() == Qt.ColorScheme.Dark
        except (AttributeError, TypeError):
            return False  # 判断不出来就当浅色

    def color(self, name: str) -> QColor:
        """取当前主题下的语义色。"""
        return QColor(PALETTES["dark" if self.dark else "light"][name])

    def apply_theme(self) -> None:
        self.dark = self._resolve_dark()
        palette = PALETTES["dark" if self.dark else "light"]
        app = QApplication.instance()
        if app is not None:
            # Fusion 是 Qt 自带的样式，完全尊重 QPalette；Windows 原生样式会
            # 忽略调色板，导致深色下下拉框的小三角仍然是深色的（等于看不见）。
            app.setStyle("Fusion")
            app.setPalette(build_palette(self.dark))
        self.setStyleSheet(build_style(self.dark))

        # 这几个控件用了行内样式，得跟着主题重设
        self.lb_status.setStyleSheet(f"color:{palette['muted']};")
        self.log.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 12px;"
            f" color:{palette['text']};"
        )
        self._recolor_rows()

    def _on_system_scheme_changed(self, _scheme=None) -> None:
        if self.theme_mode == "system":
            self.apply_theme()

    def set_theme(self, mode: str) -> None:
        """切换主题并记住（设置面板和测试都走这里）。"""
        self.theme_mode = mode if mode in ("light", "dark", "system") else "system"
        self.settings.setValue("theme", self.theme_mode)
        self.apply_theme()

    def _status_color(self, text: str) -> QColor:
        """按状态文字给颜色，换主题时也能正确重刷。"""
        if text == "等待":
            return self.color("muted")
        if text.startswith("提示"):
            return self.color("warn")
        if text.startswith("完成"):
            return self.color("ok")
        if text.startswith("失败"):
            return self.color("error")
        return self.color("accent")

    def _recolor_rows(self) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 5)
            if item is not None:
                item.setForeground(self._status_color(item.text()))

    # -- 界面搭建 -------------------------------------------------------- #

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.btn_add_files = QPushButton("添加文件")
        self.btn_add_files.clicked.connect(self.pick_files)
        self.btn_add_dir = QPushButton("添加文件夹")
        self.btn_add_dir.clicked.connect(self.pick_folder)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self.clear_all)
        self.btn_delete = QPushButton("删除")
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_delete.setToolTip("删除选中的行（也可以按 Delete 键）")

        for button in (self.btn_add_files, self.btn_add_dir, self.btn_clear, self.btn_delete):
            bar.addWidget(button)

        bar.addStretch(1)

        self.btn_settings = QPushButton("设置")
        self.btn_settings.clicked.connect(self.open_settings)
        bar.addWidget(self.btn_settings)

        bar.addSpacing(12)
        bar.addWidget(QLabel("全部设为"))
        self.cmb_all = QComboBox()
        for key, label in FORMAT_CHOICES:
            self.cmb_all.addItem(label, key)
        self.cmb_all.currentIndexChanged.connect(self.apply_format_to_all)
        bar.addWidget(self.cmb_all)
        return bar

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(COLUMNS))
        table.setHorizontalHeaderLabels(COLUMNS)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # 多选：Ctrl 点选 / Shift 连选 / 在空白处按住左键拖出选框
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setDragEnabled(False)
        table.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(False)
        table.setShowGrid(False)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setMinimumSectionSize(90)
        self.table = table
        QShortcut(QKeySequence(Qt.Key.Key_Delete), table, activated=self.delete_selected)
        return table

    def _build_footer(self) -> QWidget:
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        line = QHBoxLayout()
        line.addWidget(QLabel("输出到"))
        self.ed_output = QLineEdit(str(self.settings.value("output", str(Path.cwd() / "output"))))
        line.addWidget(self.ed_output, 1)
        btn_browse = QPushButton("选择…")
        btn_browse.clicked.connect(self.pick_output)
        line.addWidget(btn_browse)

        line.addWidget(QLabel("有损码率"))
        self.cmb_bitrate = QComboBox()
        for value, label in BITRATE_CHOICES:
            self.cmb_bitrate.addItem(label, value)
        self.cmb_bitrate.setCurrentIndex(4)  # 默认 320
        line.addWidget(self.cmb_bitrate)
        outer.addLayout(line)

        action = QHBoxLayout()
        self.btn_start = QPushButton("开始转换")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self.start)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_cancel.setEnabled(False)
        action.addWidget(self.btn_start)
        action.addWidget(self.btn_cancel)
        self.pb_total = QProgressBar()
        self.pb_total.setTextVisible(False)
        action.addWidget(self.pb_total, 1)
        self.lb_status = QLabel("等待添加文件")
        action.addWidget(self.lb_status)
        outer.addLayout(action)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(96)
        outer.addWidget(self.log)
        return box

    # -- 环境检查 -------------------------------------------------------- #

    def _check_environment(self) -> None:
        try:
            version = ffbin.version(ffbin.ffmpeg())
            self.append_log(f"ffmpeg 就绪：{version}")
        except ffbin.FFmpegNotFound as exc:
            QMessageBox.critical(
                self, "缺少 ffmpeg",
                f"{exc}\n\n没有 ffmpeg 就无法转码。请把 ffmpeg.exe 和 ffprobe.exe "
                f"放到程序目录下的 bin\\ 文件夹里。",
            )
            self.btn_start.setEnabled(False)

        self.append_log(f"支持的加密格式：{', '.join(platform_base.known_extensions())}")
        plain = ', '.join(sorted(platform_base.PLAIN_AUDIO_EXTS))
        self.append_log(f"也可以直接拖入普通音频：{plain}")

    # -- 文件管理 -------------------------------------------------------- #

    def add_paths(self, paths: list[Path]) -> int:
        known = set(platform_base.addable_extensions())
        added = 0
        existing = {p.resolve() for p in self.rows}
        for path in paths:
            candidates: list[Path] = []
            if path.is_dir():
                candidates = [f for f in sorted(path.rglob("*")) if f.is_file()]
            elif path.is_file():
                candidates = [path]
            for file in candidates:
                if file.suffix.lower().lstrip(".") not in known:
                    continue
                if file.resolve() in existing:
                    continue
                existing.add(file.resolve())
                self._append_row(file)
                added += 1
        self._refresh_status()
        return added

    def _append_row(self, file: Path) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.rows.append(file)

        decoder = platform_base.decoder_for(file)
        code = getattr(decoder, "name", "")
        name = "普通音频" if decoder is None else PLATFORM_LABELS.get(code, code)
        suffix = file.suffix.lower().lstrip(".").upper()

        cells = [file.name, name, suffix, "—"]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setToolTip(str(file))
            self.table.setItem(row, col, item)

        combo = QComboBox()
        for key, label in FORMAT_CHOICES:
            combo.addItem(label, key)
        combo.currentIndexChanged.connect(self._refresh_warnings)
        self.table.setCellWidget(row, 4, combo)

        status = QTableWidgetItem("等待")
        status.setForeground(self.color("muted"))
        self.table.setItem(row, 5, status)

    def pick_files(self) -> None:
        patterns = " ".join(f"*.{ext}" for ext in platform_base.addable_extensions())
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择加密音频", str(Path.home()), f"加密音频 ({patterns});;所有文件 (*.*)"
        )
        if files:
            self.add_paths([Path(f) for f in files])

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹", str(Path.home()))
        if folder:
            count = self.add_paths([Path(folder)])
            self.append_log(f"从 {folder} 添加了 {count} 个文件")

    def pick_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self.ed_output.text())
        if folder:
            self.ed_output.setText(folder)
            self.settings.setValue("output", folder)

    def clear_all(self) -> None:
        self.table.setRowCount(0)
        self.rows.clear()
        self.pb_total.setValue(0)
        self._refresh_status()

    def delete_selected(self) -> None:
        """删除选中的行。转换过程中禁用，免得行号和任务队列错位。"""
        if self.worker is not None and self.worker.isRunning():
            return
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            self.lb_status.setText("先选中要删除的行")
            return
        for row in rows:
            self.table.removeRow(row)
            if 0 <= row < len(self.rows):
                del self.rows[row]
        self.pb_total.setValue(0)
        self._refresh_status()
        self.append_log(f"已移除 {len(rows)} 个文件")

    def apply_format_to_all(self) -> None:
        key = self.cmb_all.currentData()
        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, 4)
            if isinstance(combo, QComboBox):
                index = combo.findData(key)
                if index >= 0:
                    combo.setCurrentIndex(index)

    def _refresh_warnings(self) -> None:
        """选了「有损转无损」时在状态列先提醒，别等转完才说。"""
        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, 4)
            status = self.table.item(row, 5)
            if not isinstance(combo, QComboBox) or status is None:
                continue
            if status.text() not in ("等待", WARN_LOSSY):
                continue  # 正在跑或已跑完的行不要覆盖

            key = combo.currentData()
            container = platform_base.guess_container(self.rows[row])
            if key and container and formats.get(key).lossless \
                    and container not in LOSSLESS_CONTAINERS:
                status.setText(WARN_LOSSY)
                status.setForeground(self.color("warn"))
            else:
                status.setText("等待")
                status.setForeground(self.color("muted"))

    # -- 运行 ------------------------------------------------------------ #

    def _current_opts(self) -> EncodeOptions:
        bitrate = self.cmb_bitrate.currentData()
        return EncodeOptions(bitrate_kbps=bitrate)

    def start(self) -> None:
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "没有文件", "先把加密音频拖进来，或者点「添加文件」。")
            return

        out_dir = Path(self.ed_output.text().strip() or "output")
        self.settings.setValue("output", str(out_dir))

        jobs = [
            (row, self.rows[row], self.table.cellWidget(row, 4).currentData())
            for row in range(self.table.rowCount())
        ]

        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_add_files.setEnabled(False)
        self.btn_add_dir.setEnabled(False)
        self.btn_delete.setEnabled(False)
        self.pb_total.setRange(0, len(jobs))
        self.pb_total.setValue(0)
        self.finished_count = 0

        self.worker = Worker(jobs, out_dir, self._current_opts(), self)
        self.worker.row_stage.connect(self._on_stage)
        self.worker.row_progress.connect(self._on_progress)
        self.worker.row_done.connect(self._on_done)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.start()

        self.append_log(f"开始处理 {len(jobs)} 个文件 → {out_dir}")

    def cancel(self) -> None:
        if self.worker:
            self.worker.stop()
            self.append_log("正在取消…")
            self.btn_cancel.setEnabled(False)

    def _on_stage(self, row: int, text: str) -> None:
        item = self.table.item(row, 5)
        if item:
            item.setText(text)
            item.setForeground(self.color("accent"))

    def _on_progress(self, row: int, value: float) -> None:
        item = self.table.item(row, 5)
        if item:
            item.setText(f"编码 {int(value * 100)}%")

    def _on_done(self, row: int, result: JobResult) -> None:
        self.finished_count += 1
        self.pb_total.setValue(self.finished_count)

        status = self.table.item(row, 5)
        if result.ok:
            if status:
                status.setText(f"完成 {result.elapsed:.1f}s" + ("（免重编码）" if result.copied else ""))
                status.setForeground(self.color("ok"))
            if result.info:
                origin = self.table.item(row, 2)
                if origin:
                    origin.setText(result.info.codec.upper())
                duration = self.table.item(row, 3)
                if duration:
                    duration.setText(result.info.duration_hms)
            for warning in result.warnings:
                self.append_log(f"  ⚠ {result.src.name}：{warning}")
        else:
            if status:
                status.setText("失败")
                status.setForeground(self.color("error"))
                status.setToolTip(result.message)
            self.append_log(f"  ✘ {result.src.name}：{result.message}")

    def _on_all_done(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_add_files.setEnabled(True)
        self.btn_add_dir.setEnabled(True)
        self.btn_delete.setEnabled(True)
        self.append_log("全部结束")

    # -- 杂项 ------------------------------------------------------------ #

    def append_log(self, text: str) -> None:
        self.log.append(text)

    def _refresh_status(self) -> None:
        total = self.table.rowCount()
        self.lb_status.setText(f"{total} 个文件" if total else "等待添加文件")

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        if self.add_paths(paths):
            self.append_log(f"拖入 {len(paths)} 个路径")

    def _ask_close_action(self) -> tuple[str | None, bool]:
        """关闭时问一句：退出还是最小化。返回 (选择, 是否记住)。"""
        box = QMessageBox(self)
        box.setWindowTitle("关闭窗口")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("要退出程序，还是缩到托盘继续后台运行？")
        if self.worker is not None and self.worker.isRunning():
            box.setInformativeText("注意：现在有任务正在转换，退出会中断。")
        else:
            box.setInformativeText("缩到托盘后任务栏不显示图标，双击右下角托盘图标可以切回来。")

        btn_exit = box.addButton("退出应用", QMessageBox.ButtonRole.AcceptRole)
        btn_min = box.addButton("最小化", QMessageBox.ButtonRole.ActionRole)
        btn_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        remember = QCheckBox("不再提示，记住我的选择")
        box.setCheckBox(remember)

        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_exit:
            return "exit", remember.isChecked()
        if clicked is btn_min:
            return "minimize", remember.isChecked()
        if clicked is btn_cancel:
            return None, False
        return None, False   # 直接关掉对话框 = 取消关闭

    def open_settings(self) -> None:
        dialog = SettingsDialog(self, self.theme_mode, self.close_action)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        theme, close_action = dialog.values()

        if close_action != self.close_action:
            self.close_action = close_action
            self.settings.setValue("close_action", close_action)
            label = dict(CLOSE_CHOICES).get(close_action, close_action)
            self.append_log(f"关闭行为已改为：{label}")

        if theme != self.theme_mode:
            self.set_theme(theme)

    # -- 托盘 ------------------------------------------------------------ #

    def _load_icon(self) -> QIcon:
        """按 PNG → ICO → 系统标准图标的顺序找一个能用的图标。

        打包后 Qt 的 ICO 插件不一定被注册，QIcon 会拿到空图标，
        而空图标传给托盘会导致 Windows 直接忽略这个托盘项。
        """
        for name in ("assets/icon.png", "assets/icon.ico"):
            path = asset_path(name)
            if path is None:
                _diag(f"图标未找到: {name}")
                continue
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
            _diag(f"图标加载失败（可能缺插件）: {path}")
        _diag("改用系统标准图标兜底")
        return self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume)

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            self.append_log("[!] 系统托盘不可用，关闭窗口将退化为普通最小化")
            return
        icon = self._load_icon()
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(APP_NAME)

        menu = QMenu(self)
        show_action = menu.addAction("显示主界面")
        show_action.triggered.connect(self.restore_from_tray)
        menu.addSeparator()
        quit_action = menu.addAction("退出程序")
        quit_action.triggered.connect(self.quit_from_tray)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()   # 启动就显示，用户随时能在托盘找到它
        QTimer.singleShot(1200, self._verify_tray)

    def _verify_tray(self) -> None:
        """确认图标真的进了系统托盘；没进就重试一次并记日志。"""
        state = tray_shell_state()
        if "未注册" in state and self.tray is not None:
            self.tray.hide()
            self.tray.show()
            _diag(f"托盘首次注册失败，已重试: {state} -> {tray_shell_state()}")

    def _retry_tray_show(self) -> None:
        """某些环境下首次 show() 会被 shell 拒绝，重试一次。"""
        if self.tray is None:
            return
        self.tray.hide()
        self.tray.show()
        _diag(f"重试 tray.show() -> isVisible={self.tray.isVisible()}")

    def _on_tray_activated(self, reason) -> None:
        # 单击（Trigger）就还原；双击在不同 Windows 上行为不一致，一并处理
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.restore_from_tray()

    def restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_from_tray(self) -> None:
        """从托盘菜单退出，不再询问。"""
        self._force_quit = True
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        if self.tray is not None:
            self.tray.hide()
        QApplication.quit()

    def hide_to_tray(self) -> None:
        """缩到托盘：任务栏不显示，程序继续跑。"""
        if self.tray is None or not self.tray.isVisible():
            self.showMinimized()      # 托盘不可用就退回普通最小化
            self.lb_status.setText("系统托盘不可用，已改为最小化")
            return
        self.hide()
        if not self._tray_hint_shown:
            self.tray.showMessage(
                APP_NAME,
                "已缩到托盘，转换任务继续运行。双击托盘图标可以切回来。",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
            self._tray_hint_shown = True

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._force_quit:
            event.accept()
            return

        action = self.close_action
        if action == "ask":
            choice, remember = self._ask_close_action()
            if choice is None:
                event.ignore()
                return
            action = choice
            if remember:
                self.close_action = action
                self.settings.setValue("close_action", action)

        if action == "minimize":
            event.ignore()
            self.hide_to_tray()
            self.append_log("已缩到托盘，转换任务继续运行")
            return

        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        if self.tray is not None:
            self.tray.hide()
        event.accept()


def _bring_to_front(window) -> None:
    """把窗口从托盘或最小化状态叫出来，并放到最前面。"""
    window.restore_from_tray()
    window.showNormal()
    window.raise_()
    window.activateWindow()
    # Windows 的前台切换限制有时会拦住 activateWindow，再补一枪
    try:
        import ctypes

        ctypes.windll.user32.SetForegroundWindow(int(window.winId()))
    except Exception:
        pass


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    # 先抢锁再建窗口：启动瞬间连点也不会漏出第二个实例
    guard = SingleInstance()
    if guard.already_running():
        guard.broadcast_activate()   # 让已有实例把窗口显示出来
        return 0

    window = MainWindow()
    window.show()
    guard.listen(app, lambda: _bring_to_front(window))

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
