#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
爱快路由-AP终端工具 —— PySide6 版图形界面

架构：
  - 服务层 ikuai_service.py / 配置层 ikuai_config.py 与 UI 框架解耦，原样复用
  - 本模块只做 UI：QTableView + QAbstractTableModel（MVC）
  - 跨线程通信用 Qt 信号（自动排队到 UI 线程），替代 Tk 版的队列轮询
  - 「操作」列用 setIndexWidget 放真实 QPushButton（替代 Tk 的浮层 hack）

Qt 版相对 Tk 版的界面增强：
  - Fusion 风格 + QSS：统一字体/圆角/配色，现代观感
  - 表格交替行色、行高加大、状态列彩色文字
  - 表头点击排序（首点降序、空值垫底 —— 行为与 Tk 版一致）
  - 日志面板自动限 500 行（QPlainTextEdit maxBlockCount）
"""

from __future__ import annotations

import ipaddress
import re
import sys
import threading
import webbrowser
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, Qt, QTimer,
                            QUrl, Signal, QObject)
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame,
                               QGroupBox, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QMainWindow,
                               QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                               QTableView, QVBoxLayout, QWidget)

import ikuai_service
from ikuai_service import (IKuaiService, APInfo, NotConnectedError,
                           OUTPUT_DIR)
import ikuai_config

APP_TITLE = "爱快路由-AP终端工具"
APP_VERSION = "1.0"
APP_COPYRIGHT = "| © Jcsit&viclai"

# GitHub 项目入口（状态栏右侧按钮）
GITHUB_REPO_URL = "https://github.com/vic4728/ikuai-ap-tool"
GITHUB_RELEASES_URL = GITHUB_REPO_URL + "/releases"

_APP_DIR = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
APP_ICON = _APP_DIR / "ikuai-R-logo.ico"


def load_app_icon() -> QIcon:
    """加载窗口图标（三种来源按优先级）：

    1. exe 同目录的 ico（用户可自行替换，绿色）
    2. PyInstaller 打包时 --add-data 嵌入的 ico（frozen 场景主来源：
       exe 文件图标是 Windows 资源，QIcon 读不到；把 ico 一并打进
       包内，从 sys._MEIPASS 临时解压目录读）
    3. 都没有 -> 返回空 QIcon（Qt 用默认图标，不报错）
    """
    # 1) exe/脚本同目录
    if APP_ICON.is_file():
        return QIcon(str(APP_ICON))
    # 2) 打包内嵌（_MEIPASS）
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            inner = Path(meipass) / "ikuai-R-logo.ico"
            if inner.is_file():
                return QIcon(str(inner))
    # 3) 源码运行且同目录没 ico（几乎不发生）
    return QIcon()

REFRESH_OPTIONS = ["1s", "3s", "5s", "10s"]

# ---- 配色（浅色主题，集中定义便于统一调整） ----
C_PRIMARY = "#2563eb"        # 主色（连接按钮）
C_PRIMARY_H = "#1d4ed8"
C_DANGER = "#dc2626"         # 危险（批量重启）
C_DANGER_H = "#b91c1c"
C_OK = "#16a34a"             # 在线/成功
C_ERR = "#dc2626"            # 离线/失败
C_WARN = "#d97706"           # 重启中/读取中
C_BG = "#eef1f5"             # 窗口底
C_CARD = "#ffffff"           # 卡片底
C_BORDER = "#e2e6ec"
C_ROW_RESTART_BG = "#fff7e0"  # 重启中行底色（柔和版黄）
C_ROW_OFFLINE_BG = "#fdecec"  # 离线行底色（柔和版红）

QSS = """
* { font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 13px; }
QMainWindow, QWidget#root { background: %(bg)s; }
QGroupBox {
    background: %(card)s; border: 1px solid %(border)s; border-radius: 10px;
    margin-top: 9px; padding: 16px 12px 12px 12px;
    font-weight: bold; color: #374151;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 14px; padding: 0 8px;
    background: %(card)s; color: #2563eb;
}
/* ---- 带标题栏卡片（标题完整包进边框） ---- */
QFrame#card {
    background: %(card)s; border: 1px solid %(border)s;
    border-radius: 10px;
}
QFrame#cardHeader {
    background: #f0f4fa; border: none;
    border-top-left-radius: 9px; border-top-right-radius: 9px;
    border-bottom: 1px solid %(border)s;
}
QFrame#cardAccent {
    background: %(primary)s; border: none; border-radius: 2px;
}
QLabel#cardTitle {
    background: transparent; border: none;
    font-weight: bold; font-size: 13px; color: #1e3a8a;
    letter-spacing: 1px;
}
QWidget#cardBody { background: transparent; border: none; }
QLabel { color: #374151; }
QLineEdit, QComboBox {
    border: 1px solid #d3d9e0; border-radius: 6px; padding: 4px 8px;
    background: white; selection-background-color: #bfdbfe;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid %(primary)s; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    border: 1px solid %(border)s; background: white;
    selection-background-color: #dbeafe; selection-color: #111827;
}
QPushButton {
    border: 1px solid transparent; border-radius: 6px;
    padding: 5px 16px; background: #e8ecf1; color: #1f2937;
}
QPushButton:hover { background: #dde3ea; }
QPushButton:disabled { background: #eef0f3; color: #9ca3af; }
QPushButton#primary {
    background: %(primary)s; color: white; font-weight: bold;
}
QPushButton#primary:hover { background: %(primaryh)s; }
QPushButton#primary:disabled { background: #a5c3f7; }
QPushButton#danger {
    background: %(danger)s; color: white; font-weight: bold;
}
QPushButton#danger:hover { background: %(dangerh)s; }
QPushButton#danger:disabled { background: #f2a8a8; }
QPushButton#rowRestart {
    background: #eaf1fe; color: #1d4ed8; border: 1px solid #c3d8fb;
    border-radius: 5px; padding: 2px 12px; font-weight: bold;
}
QPushButton#rowRestart:hover { background: #d8e7fd; }
QPushButton#rowRestart:disabled { color: #93a9c8; background: #f1f4f8;
    border-color: #dfe5ec; }
QTableView {
    border: none; background: white; alternate-background-color: #fafbfd;
    gridline-color: #edf0f4; selection-background-color: #dbeafe;
    selection-color: #111827;
}
QHeaderView::section {
    background: #f2f4f7; color: #4b5563; font-weight: bold;
    border: none; border-bottom: 1px solid %(border)s;
    border-right: 1px solid #edf0f4; padding: 6px 8px;
}
QTableView::item { padding: 4px 8px; }
QPlainTextEdit {
    background: #0f172a; color: #d1d9e6; border: none; border-radius: 6px;
    font-family: 'Consolas', 'Microsoft YaHei UI', monospace; font-size: 12px;
    padding: 6px; selection-background-color: #334155;
}
QCheckBox { spacing: 6px; }
QCheckBox::indicator {
    width: 15px; height: 15px; border: 1px solid #cbd5e1;
    border-radius: 4px; background: white;
}
QCheckBox::indicator:checked { background: %(primary)s; border-color: %(primary)s;
    image: url(none); }
/* 状态栏右侧 GitHub 入口按钮 */
QStatusBar { background: transparent; color: #6b7280; }
QStatusBar::item { border: none; }
QPushButton#githubBtn {
    background: transparent; border: none; border-radius: 5px;
    color: #24292f; padding: 1px 8px; font-size: 12px;
}
QPushButton#githubBtn:hover {
    background: #e8ecf1; color: #2563eb;
}
/* ---- 自定义标题栏（无边框窗口） ---- */
QMainWindow { border-radius: 0; }
QWidget#titleBar {
    background: #ffffff; border: none;
    border-bottom: 1px solid %(border)s;
}
QLabel#titleText {
    color: #374151; font-weight: bold; font-size: 13px;
    letter-spacing: 0.5px;
}
QWidget#content { background: %(bg)s; }
QPushButton#winBtn {
    background: transparent; border: none; border-radius: 5px;
    color: #6b7280; font-size: 13px; font-weight: bold;
}
QPushButton#winBtn:hover { background: #e8ecf1; color: #1f2937; }
QPushButton#winCloseBtn {
    background: transparent; border: none; border-radius: 5px;
    color: #6b7280; font-size: 13px; font-weight: bold;
}
QPushButton#winCloseBtn:hover { background: #dc2626; color: white; }
""" % {"bg": C_BG, "card": C_CARD, "border": C_BORDER,
       "primary": C_PRIMARY, "primaryh": C_PRIMARY_H,
       "danger": C_DANGER, "dangerh": C_DANGER_H}


# GitHub octocat 官方 16x16 栅格路径（bootstrap-icons octorcat / github-mark，
# CC-BY 4.0 —— https://github.com/twbs/icons）
_GITHUB_SVG_PATH = ("M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-"
                    "17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-"
                    ".09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58"
                    " 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-"
                    ".2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02."
                    "08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 ."
                    "27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82"
                    " 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48"
                    " 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16"
                    " 8c0-4.42-3.58-8-8-8Z")


def _github_icon(color: str = "#24292f") -> QIcon:
    """生成 GitHub octocat 图标（SVG 内嵌，无需图片文件）。

    用 QSvgRenderer 渲染官方路径到 32x32 透明画布，
    再生成多尺寸 QIcon（16/24/32 适配状态栏缩放）。
    """
    from PySide6.QtCore import QByteArray, QRectF, Qt as _Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
           '<path fill="%s" fill-rule="evenodd" d="%s"/></svg>'
           % (color, _GITHUB_SVG_PATH))
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()
    icon = QIcon()
    for size in (16, 24, 32):
        pm = QPixmap(size, size)
        pm.fill(_Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        icon.addPixmap(pm)
    return icon


# ======================================================================
# 带标题栏的卡片（标题完整包进边框：顶部通栏标题条 + 左侧强调块）
# ======================================================================
class CardWidget(QFrame):
    """工业仪表盘风格卡片。

    结构（全部被圆角边框包住）：
      ┌────────────────────────────────┐
      │ ▎连 接                          │  <- 标题条：浅蓝灰底 + 左侧 4px 蓝条
      ├────────────────────────────────┤  <- 发丝分隔线
      │  内容区（body）                 │
      └────────────────────────────────┘
    比 QGroupBox 的骑线标题更"完整"：标题本身就是卡片的一部分，
    上下都有边界，视觉上被边框完整包含。
    """

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        card_lay = QVBoxLayout(self)
        card_lay.setContentsMargins(1, 1, 1, 1)
        card_lay.setSpacing(0)

        # 标题条（objectName 驱动 QSS）
        self._header = QFrame(objectName="cardHeader")
        hlay = QHBoxLayout(self._header)
        hlay.setContentsMargins(12, 6, 12, 6)
        hlay.setSpacing(8)
        self._accent = QFrame(objectName="cardAccent")
        self._accent.setFixedSize(4, 16)
        hlay.addWidget(self._accent)
        self._title_lbl = QLabel(title, objectName="cardTitle")
        hlay.addWidget(self._title_lbl)
        hlay.addStretch(1)
        card_lay.addWidget(self._header)

        # 内容区
        self.body = QWidget(objectName="cardBody")
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(12, 10, 12, 12)
        self.body_lay.setSpacing(8)
        card_lay.addWidget(self.body, 1)

    def title(self) -> str:
        return self._title_lbl.text()

    def set_title(self, t: str):
        self._title_lbl.setText(t)


# ======================================================================
# 表格模型
# ======================================================================
class APTableModel(QAbstractTableModel):
    """AP 列表模型。

    列：分组/名称/型号/终端IP/终端MAC/运行时间/运行状态/备注/操作
    - data()：DisplayRole 文本；BackgroundRole/ForegroundRole 按状态着色
    - sort()：空值（离线 AP 的空 IP / '- -'）无论升降序固定垫底；
      IP 按数值、运行时间按时长语义排序（与 Tk 版行为一致）
    """

    HEADERS = ["分组", "名称", "型号", "终端IP", "终端MAC",
               "运行时间", "运行状态", "备注", "操作"]
    COL_KEYS = ["group", "name", "model", "ip", "mac",
                "uptime", "status", "comment", "action"]
    COL_IP = COL_KEYS.index("ip")
    COL_UPTIME = COL_KEYS.index("uptime")
    COL_STATUS = COL_KEYS.index("status")
    COL_COMMENT = COL_KEYS.index("comment")
    COL_ACTION = COL_KEYS.index("action")

    EMPTY_CELLS = {"", "- -", "--", "-", "—", "－", "未知"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._aps: list[APInfo] = []
        self._monitor: dict = {}          # key -> {"state": ...}
        # 排序状态（与 Tk 版一致：None=原始顺序；首点降序）
        self.sort_col = None
        self.sort_desc = True

    # ---------- Qt 必需接口 ----------
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._aps)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def headerData(self, sec, orient, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orient == Qt.Horizontal \
                and 0 <= sec < len(self.HEADERS):
            return self.HEADERS[sec]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        ap = self._aps[index.row()]
        col = index.column()
        key = self.COL_KEYS[col]

        if role in (Qt.DisplayRole, Qt.EditRole):
            if key == "action":
                return ""                      # 按钮由 setIndexWidget 提供
            if key == "status":
                return self._status_text(ap)
            return getattr(ap, key, "") or ""

        if role == Qt.BackgroundRole:
            info = self._monitor.get(ap.key())
            if info and info.get("state") in ("waiting", "seen_offline"):
                return QColor(C_ROW_RESTART_BG)
            if not ap.is_alive():
                return QColor(C_ROW_OFFLINE_BG)
            return None

        if role == Qt.ForegroundRole and col == self.COL_STATUS:
            info = self._monitor.get(ap.key())
            if info and info.get("state") in ("waiting", "seen_offline"):
                return QColor(C_WARN)
            if not ap.is_alive():
                return QColor(C_ERR)
            return QColor(C_OK)

        if role == Qt.TextAlignmentRole:
            if key in ("group", "model", "status", "action"):
                return int(Qt.AlignCenter)
            return int(Qt.AlignVCenter | Qt.AlignLeft)

        if role == Qt.UserRole:               # 供按钮/双击取整行对象
            return ap
        return None

    # ---------- 业务 ----------
    def _status_text(self, ap: APInfo) -> str:
        info = self._monitor.get(ap.key())
        if info is not None and info.get("state") in ("waiting", "seen_offline"):
            return "重启中"
        if info is not None:
            return "已连接"
        if not ap.is_alive():
            return "断开"
        return ap.status or "未知"

    def aps(self) -> list[APInfo]:
        return list(self._aps)

    def set_aps(self, aps: list[APInfo], monitor: dict):
        """整表替换（带排序保持）。数据量 <=500，整表刷新足够快。"""
        self.beginResetModel()
        self._aps = list(aps)
        self._monitor = monitor
        if self.sort_col is not None:
            self._apply_sort()
        self.endResetModel()

    # ---------- 排序 ----------
    @staticmethod
    def _empty(v: str) -> bool:
        return v in APTableModel.EMPTY_CELLS

    @staticmethod
    def _ip_key(v: str):
        try:
            return (0, int(ipaddress.ip_address(v.strip())))
        except ValueError:
            return (1, v.lower())

    @staticmethod
    def _uptime_key(v: str):
        total, matched = 0, False
        for num, unit in re.findall(r"(\d+)\s*(天|日|时|小时|分|分钟|秒)", v or ""):
            matched = True
            n = int(num)
            total += n * {"天": 86400, "日": 86400, "时": 3600,
                          "小时": 3600, "分": 60, "分钟": 60, "秒": 1}[unit]
        return total if matched else -1

    def sort(self, column: int, order=Qt.DescendingOrder):
        """点击表头（QTableView 排序入口）。首点降序由调用方控制 order。"""
        if not 0 <= column < len(self.COL_KEYS) - 1:   # 操作列不可排序
            return
        self.sort_col = column
        self.sort_desc = (order == Qt.DescendingOrder)
        self.beginResetModel()
        self._apply_sort()
        self.endResetModel()

    def _apply_sort(self):
        col = self.sort_col
        if col is None:
            return
        key = self.COL_KEYS[col]
        col_key = key

        def row_key(ap):
            v = (getattr(ap, key, "") or "").strip()
            if col_key == "ip":
                return self._ip_key(v)
            if col_key == "uptime":
                return self._uptime_key(v)
            return v.lower()

        non_empty = [a for a in self._aps
                     if not self._empty((getattr(a, key, "") or "").strip())]
        empty = [a for a in self._aps
                 if self._empty((getattr(a, key, "") or "").strip())]
        # sorted(reverse=) 会连垫底标志一起反转 —— 空值必须拆出来追加尾部
        self._aps = sorted(non_empty, key=row_key,
                           reverse=self.sort_desc) + empty

    def clear_sort(self):
        self.sort_col = None
        self.beginResetModel()
        self.endResetModel()


# ======================================================================
# 自定义标题栏（无边框窗口）：程序图标 + 标题 + GitHub 入口 + 窗口按钮
# ======================================================================
class TitleBar(QWidget):
    """无边框窗口的自绘标题栏。

    左：程序图标 + 标题文字
    右：GitHub 猫标（github-ico.png / SVG 兜底）+ 发布页按钮 + 最小化 + 关闭

    - 整条可拖动移动窗口；双击标题区最大化/还原
    - 不做"最大化"按钮（运维工具通常固定尺寸用）；保留最小化和关闭
    """

    def __init__(self, parent: "MainWindow"):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(38)
        self._win = parent
        self._press_pos = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 6, 4)
        lay.setSpacing(6)

        # 程序图标 + 标题
        self.lbl_icon = QLabel()
        ic = load_app_icon()
        if not ic.isNull():
            self.lbl_icon.setPixmap(ic.pixmap(20, 20))
        self.lbl_title = QLabel(parent.windowTitle(), objectName="titleText")
        lay.addWidget(self.lbl_icon)
        lay.addWidget(self.lbl_title)
        lay.addStretch(1)

        # GitHub 猫标（优先 github-ico.png，其次 SVG 渲染）
        self.btn_github = QPushButton()
        self.btn_github.setObjectName("githubBtn")
        self.btn_github.setToolTip("GitHub 项目主页\n点击打开发行版（exe）下载页")
        self.btn_github.setCursor(Qt.PointingHandCursor)
        self.btn_github.setFixedSize(34, 26)
        gh_icon = _github_png_icon()
        if gh_icon is not None and not gh_icon.isNull():
            self.btn_github.setIcon(gh_icon)
        else:
            self.btn_github.setIcon(_github_icon())
        self.btn_github.clicked.connect(parent._open_github)
        lay.addWidget(self.btn_github)

        self.btn_release = QPushButton("发布页 ▾")
        self.btn_release.setObjectName("githubBtn")
        self.btn_release.setToolTip("打开 GitHub Releases 下载页（exe）")
        self.btn_release.setCursor(Qt.PointingHandCursor)
        self.btn_release.setFixedHeight(26)
        self.btn_release.clicked.connect(parent._open_github)
        lay.addWidget(self.btn_release)

        lay.addSpacing(8)

        # 最小化 / 关闭
        self.btn_min = QPushButton("—", objectName="winBtn")
        self.btn_min.setFixedSize(38, 26)
        self.btn_min.setToolTip("最小化")
        self.btn_min.clicked.connect(parent.showMinimized)
        lay.addWidget(self.btn_min)
        self.btn_close = QPushButton("✕", objectName="winCloseBtn")
        self.btn_close.setFixedSize(38, 26)
        self.btn_close.setToolTip("关闭")
        self.btn_close.clicked.connect(parent.close)
        lay.addWidget(self.btn_close)

    # ---- 拖动移动 / 双击最大化 ----
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._press_pos = ev.globalPosition().toPoint() - self._win.pos()
            ev.accept()

    def mouseMoveEvent(self, ev):
        if self._press_pos is not None:
            if self._win.isMaximized():
                self._win.showNormal()
            self._win.move(ev.globalPosition().toPoint() - self._press_pos)
            ev.accept()

    def mouseReleaseEvent(self, ev):
        self._press_pos = None
        ev.accept()

    def mouseDoubleClickEvent(self, ev):
        # 双击标题区：最大化/还原
        if self._win.isMaximized():
            self._win.showNormal()
        else:
            self._win.showMaximized()
        ev.accept()


def _github_png_icon() -> QIcon | None:
    """github-ico.png（用户提供的图标，优先用）；不存在返回 None。

    查找顺序：exe/脚本同目录 -> PyInstaller _MEIPASS 内嵌。
    """
    cands = [_APP_DIR / "github-ico.png"]
    if getattr(sys, "frozen", False):
        mp = getattr(sys, "_MEIPASS", "")
        if mp:
            cands.append(Path(mp) / "github-ico.png")
    for p in cands:
        if p.is_file():
            ic = QIcon(str(p))
            if not ic.isNull():
                return ic
    return None


# ======================================================================
# 跨线程信号桥（worker 线程 emit -> 自动排队到 UI 线程）
# ======================================================================
class UiBridge(QObject):
    sig_log = Signal(str)
    sig_aps = Signal(list)
    sig_refresh_done = Signal()
    sig_connected = Signal()
    sig_disconnected = Signal()
    sig_connect_failed = Signal(str)
    sig_busy = Signal(bool)
    sig_status = Signal(str)
    sig_batch_done = Signal(int, int, list)
    sig_comment_done = Signal(str, bool, str)


# ======================================================================
# 主窗口
# ======================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("%s v%s %s" % (APP_TITLE, APP_VERSION, APP_COPYRIGHT))
        self.resize(1133, 760)
        self.setMinimumSize(1000, 640)
        self.setWindowIcon(load_app_icon())
        # 自定义标题栏：无边框 + 自绘标题条（GitHub 入口 + 窗口按钮）
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)

        # ---- 状态 ----
        self.service = IKuaiService(log_cb=self._svc_log, headless=True)
        self.bridge = UiBridge()
        self.ap_list: list[APInfo] = []
        self.busy = False
        self.auto_refresh = False
        self._refresh_inflight = False
        self.monitor_targets: dict[str, dict] = {}
        self._just_recovered = False
        self._closing = False

        self._build_ui()
        self._connect_signals()
        self._load_config()

        # 定时器（对应 Tk 版的 after 循环）
        self.timer_refresh = QTimer(self)
        self.timer_refresh.setSingleShot(True)
        self.timer_refresh.timeout.connect(self._auto_refresh_tick)
        self.timer_monitor = QTimer(self)
        self.timer_monitor.setSingleShot(True)
        self.timer_monitor.timeout.connect(self._monitor_tick)

        if self.chk_autologin.isChecked() and self.edit_password.text():
            QTimer.singleShot(500, self.on_connect)

    # ==============================================================
    # 界面
    # ==============================================================
    def _build_ui(self):
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ---------- 自定义标题栏（无边框窗口的顶部标题条） ----------
        self.title_bar = TitleBar(self)
        lay.addWidget(self.title_bar)

        # ---------- 内容区（保留原边距） ----------
        content = QWidget(objectName="content")
        lay.addWidget(content, 1)
        clay = QVBoxLayout(content)
        clay.setContentsMargins(14, 8, 14, 12)
        clay.setSpacing(10)

        # ---------- 连接卡片（两行布局） ----------
        # 第一行：IP/域名 | 端口 | 协议（地址组）
        # 第二行：用户 | 密码 | 记住密码 | 自动登录 | 隐藏浏览器 | 连接 | 断开
        gb = CardWidget("连 接")

        row1 = QHBoxLayout()
        gb.body_lay.addLayout(row1)
        row1.setSpacing(8)

        row1.addWidget(QLabel("IP/域名"))
        self.edit_host = QLineEdit()
        self.edit_host.setMinimumWidth(240)
        self.edit_host.setPlaceholderText("IPv4 / IPv6 / 域名")
        row1.addWidget(self.edit_host)

        row1.addWidget(QLabel("端口"))
        self.edit_port = QLineEdit()
        self.edit_port.setFixedWidth(56)
        self.edit_port.setText("80")
        row1.addWidget(self.edit_port)

        row1.addWidget(QLabel("协议"))
        self.cmb_scheme = QComboBox()
        self.cmb_scheme.addItems(["HTTP", "HTTPS"])
        self.cmb_scheme.setFixedWidth(78)
        self.cmb_scheme.currentTextChanged.connect(self._on_scheme_change)
        row1.addWidget(self.cmb_scheme)

        row1.addStretch(1)

        row2 = QHBoxLayout()
        gb.body_lay.addLayout(row2)
        row2.setSpacing(8)

        row2.addWidget(QLabel("用户"))
        self.edit_user = QLineEdit()
        self.edit_user.setFixedWidth(110)
        self.edit_user.setText("admin")
        row2.addWidget(self.edit_user)

        row2.addWidget(QLabel("密码"))
        self.edit_password = QLineEdit()
        self.edit_password.setEchoMode(QLineEdit.Password)
        self.edit_password.setFixedWidth(150)
        row2.addWidget(self.edit_password)

        self.chk_show_pwd = QCheckBox("显示")
        self.chk_show_pwd.toggled.connect(self._toggle_pwd)
        row2.addWidget(self.chk_show_pwd)

        row2.addSpacing(12)
        self.chk_remember = QCheckBox("记住密码")
        self.chk_remember.setChecked(True)
        row2.addWidget(self.chk_remember)
        self.chk_autologin = QCheckBox("自动登录")
        row2.addWidget(self.chk_autologin)
        self.chk_headless = QCheckBox("隐藏浏览器窗口")
        self.chk_headless.setChecked(True)
        self.chk_headless.toggled.connect(self._on_headless_toggle)
        row2.addWidget(self.chk_headless)

        row2.addStretch(1)

        self.btn_connect = QPushButton("连接")
        self.btn_connect.setObjectName("primary")
        self.btn_connect.clicked.connect(self.on_connect)
        row2.addWidget(self.btn_connect)
        self.btn_disconnect = QPushButton("断开")
        self.btn_disconnect.clicked.connect(self.on_disconnect)
        row2.addWidget(self.btn_disconnect)
        clay.addWidget(gb)

        # ---------- AP 列表卡片 ----------
        gb2 = CardWidget("AP 终端列表")
        v2 = QVBoxLayout()
        gb2.body_lay.addLayout(v2)
        v2.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("刷新间隔"))
        self.cmb_refresh = QComboBox()
        self.cmb_refresh.addItems(REFRESH_OPTIONS)
        self.cmb_refresh.setFixedWidth(70)
        self.cmb_refresh.currentTextChanged.connect(self._on_refresh_change)
        bar.addWidget(self.cmb_refresh)
        self.chk_autorefresh = QCheckBox("自动刷新")
        self.chk_autorefresh.setChecked(True)
        self.chk_autorefresh.toggled.connect(self._on_autorefresh_toggle)
        bar.addWidget(self.chk_autorefresh)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(lambda: self.on_refresh(quiet=False))
        bar.addWidget(self.btn_refresh)
        self.btn_batch = QPushButton("批量重启")
        self.btn_batch.setObjectName("danger")
        self.btn_batch.clicked.connect(self.on_batch_restart)
        bar.addWidget(self.btn_batch)
        bar.addStretch(1)
        self.lbl_status = QLabel("未连接")
        self.lbl_status.setStyleSheet(
            "color: %s; font-weight: bold; padding-right: 4px;" % C_ERR)
        bar.addWidget(self.lbl_status)
        v2.addLayout(bar)

        self.model = APTableModel(self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QTableView.SelectRows)
        self.view.setSelectionMode(QTableView.ExtendedSelection)
        self.view.setEditTriggers(QTableView.NoEditTriggers)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(34)
        self.view.setShowGrid(True)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._on_context_menu)
        self.view.doubleClicked.connect(self._on_double_click)

        hh = self.view.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.sectionClicked.connect(self._on_header_clicked)
        hh.setStretchLastSection(False)
        widths = [90, 150, 100, 120, 150, 110, 90, 150, 96]
        for i, w in enumerate(widths):
            self.view.setColumnWidth(i, w)
        self.view.horizontalScrollBar().setSizePolicy(
            self.view.horizontalScrollBar().sizePolicy())
        v2.addWidget(self.view)
        clay.addWidget(gb2, 1)

        # ---------- 日志卡片 ----------
        gb3 = CardWidget("运行日志")
        gb3.body_lay.setContentsMargins(8, 6, 8, 8)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(500)   # 自动限 500 行
        gb3.body_lay.addWidget(self.txt_log)
        gb3.setMinimumHeight(210)
        gb3.setMaximumHeight(230)
        clay.addWidget(gb3)

        # ---------- 状态栏：操作提示（GitHub 入口已移至顶部标题栏） ----------
        self.statusBar().showMessage(
            "双击「备注」可修改并回写路由器 · 双击行重启选中 · 点击表头排序")

        # 右下角拖拽缩放手柄（无边框窗口不能靠系统边框缩放）
        from PySide6.QtWidgets import QSizeGrip
        self.statusBar().addPermanentWidget(QSizeGrip(self))

    def _open_github(self):
        """打开 GitHub 发行版下载页（本项目的 Releases）。"""
        url = GITHUB_RELEASES_URL
        try:
            webbrowser.open(url)
            self._append_log("已打开浏览器: %s" % url)
        except Exception as ex:
            self._append_log("打开失败：%s（手动访问 %s）" % (ex, url))

    # ==============================================================
    # 信号
    # ==============================================================
    def _connect_signals(self):
        b = self.bridge
        b.sig_log.connect(self._append_log)
        b.sig_aps.connect(self._render_aps)
        b.sig_refresh_done.connect(self._on_refresh_done)
        b.sig_connected.connect(self._on_connected_ok)
        b.sig_disconnected.connect(self._on_disconnected)
        b.sig_connect_failed.connect(self._on_connect_failed)
        b.sig_busy.connect(self._set_busy)
        b.sig_status.connect(self._set_status)
        b.sig_batch_done.connect(self._on_batch_done)
        b.sig_comment_done.connect(self._on_comment_done)

    def _svc_log(self, line: str):
        """service 在工作线程调用 -> 经信号投递到 UI 线程。"""
        if not self._closing:
            self.bridge.sig_log.emit(line)

    # ==============================================================
    # 配置
    # ==============================================================
    def _load_config(self):
        try:
            data = ikuai_config.load_config()
        except Exception:
            return
        self.edit_host.setText(str(data.get("host", "192.168.50.1")))
        self.edit_port.setText(str(data.get("http_port", "80")))
        if data.get("scheme") == "HTTPS":
            self.cmb_scheme.setCurrentText("HTTPS")
        self.edit_user.setText(str(data.get("user", "admin")))
        self.chk_remember.setChecked(bool(data.get("remember", True)))
        self.chk_autologin.setChecked(bool(data.get("autologin", False)))
        self.chk_headless.setChecked(bool(data.get("headless", True)))
        self.cmb_refresh.setCurrentText(data.get("refresh", "5s"))
        self.chk_autorefresh.setChecked(bool(data.get("auto_refresh", True)))
        pw = data.get("password") or ""
        if data.get("remember") and pw:
            self.edit_password.setText(pw)

    def _save_config(self):
        try:
            ikuai_config.save_config({
                "host": self.edit_host.text(),
                "http_port": self.edit_port.text(),
                "https_port": self.edit_port.text(),
                "scheme": self.cmb_scheme.currentText(),
                "user": self.edit_user.text(),
                "remember": self.chk_remember.isChecked(),
                "autologin": self.chk_autologin.isChecked(),
                "headless": self.chk_headless.isChecked(),
                "refresh": self.cmb_refresh.currentText(),
                "auto_refresh": self.chk_autorefresh.isChecked(),
                "password": (self.edit_password.text()
                             if self.chk_remember.isChecked() else ""),
            })
        except Exception:
            pass

    # ==============================================================
    # 小交互
    # ==============================================================
    def _on_scheme_change(self, txt):
        if txt == "HTTPS" and self.edit_port.text() == "80":
            self.edit_port.setText("443")
        elif txt == "HTTP" and self.edit_port.text() == "443":
            self.edit_port.setText("80")

    def _toggle_pwd(self, show: bool):
        self.edit_password.setEchoMode(
            QLineEdit.Normal if show else QLineEdit.Password)

    def _on_headless_toggle(self, want: bool):
        if self.service.browser_started:
            actual = self.service.headless
            if want != actual:
                self.chk_headless.blockSignals(True)
                self.chk_headless.setChecked(actual)
                self.chk_headless.blockSignals(False)
                QMessageBox.information(
                    self, "提示",
                    "浏览器已在运行，无法切换显示模式。\n\n"
                    "请先断开连接，再切换后重新连接。")

    def _set_status(self, txt: str, color: str = C_OK):
        self.lbl_status.setText(txt)
        self.lbl_status.setStyleSheet(
            "color: %s; font-weight: bold; padding-right: 4px;" % color)

    def _append_log(self, line: str):
        self.txt_log.appendPlainText("[%s] %s" % (_now(), line))

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = not busy
        for w in (self.btn_connect, self.btn_refresh, self.btn_disconnect):
            w.setEnabled(state)
        self.chk_headless.setEnabled(
            not self.service.browser_started and state)

    # ==============================================================
    # 连接 / 断开
    # ==============================================================
    def on_connect(self):
        if self.busy:
            return
        host = self.edit_host.text().strip()
        if not host:
            QMessageBox.warning(self, "提示", "请填写 IP 或域名")
            return
        user = self.edit_user.text().strip()
        password = self.edit_password.text()
        if not user or not password:
            QMessageBox.warning(self, "提示", "请填写登录用户和密码")
            return
        try:
            port = int(self.edit_port.text())
        except ValueError:
            QMessageBox.warning(self, "提示", "端口必须是数字")
            return

        self._save_config()
        self._set_busy(True)
        self._set_status("连接中...", C_WARN)
        self._append_log("── 开始连接 %s ──" % host)
        use_https = self.cmb_scheme.currentText() == "HTTPS"
        headless = self.chk_headless.isChecked()

        def worker():
            b = self.bridge
            try:
                try:
                    self.service.set_headless(headless)
                except RuntimeError as ex:
                    b.sig_log.emit("提示：%s（将继续用当前模式连接）" % ex)
                self.service.start()
                self.service.connect(host, port, use_https, user, password)
                b.sig_connected.emit()
            except Exception as ex:
                b.sig_busy.emit(False)
                b.sig_status.emit("连接失败")
                b.sig_connect_failed.emit(str(ex))

        threading.Thread(target=worker, daemon=True).start()

    def _on_connected_ok(self):
        self._set_busy(False)
        self._append_log("登录成功，已进入后台")
        self.on_refresh(quiet=False)
        self.auto_refresh = self.chk_autorefresh.isChecked()
        if self.auto_refresh:
            self._schedule_refresh()

    def _on_connect_failed(self, msg):
        self._set_busy(False)
        self._set_status("连接失败", C_ERR)
        self._append_log("[连接失败] %s" % msg)
        # 浏览器内核缺失时附中文部署指引（比 playwright 英文提示友好）
        hint = ikuai_service.browser_missing_hint(msg)
        if hint:
            self._append_log(hint)
            QMessageBox.warning(self, "缺少浏览器内核", hint)
        else:
            QMessageBox.critical(self, "连接失败", msg)

    def on_disconnect(self):
        if self.busy:
            return
        self.timer_refresh.stop()
        self.timer_monitor.stop()
        self.monitor_targets = {}
        self.ap_list = []
        self._refresh_inflight = False
        self._set_status("正在断开...", C_WARN)
        self._append_log("正在断开连接...")
        self._set_busy(True)

        def worker():
            try:
                self.service.stop()
            except Exception as ex:
                self.bridge.sig_log.emit("关闭浏览器时出错：%s" % ex)
            self.bridge.sig_disconnected.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _on_disconnected(self):
        self._set_busy(False)
        self._set_status("未连接", C_ERR)
        self.ap_list = []
        self._render_aps([])
        self._append_log("已断开连接")

    # ==============================================================
    # 刷新
    # ==============================================================
    def on_refresh(self, quiet: bool = False):
        if self._refresh_inflight:
            return
        if not self.service.connected:
            self._append_log("请先连接路由器")
            return
        self._refresh_inflight = True
        if not quiet:
            self._set_status("读取中...", C_WARN)

        def worker():
            b = self.bridge
            try:
                aps = self.service.fetch_ap_list()
                b.sig_aps.emit(aps)
            except NotConnectedError as ex:
                b.sig_status.emit("未连接")
                b.sig_log.emit("未连接：%s" % ex)
            except Exception as ex:
                b.sig_log.emit("刷新失败：%s" % ex)
                b.sig_status.emit("刷新失败")
            finally:
                b.sig_refresh_done.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _on_refresh_done(self):
        self._refresh_inflight = False
        self._set_status(self._status_text(), C_OK)
        self._just_recovered = False

    def _render_aps(self, aps: list):
        self.ap_list = list(aps)
        self._check_monitor(aps)
        self.model.set_aps(aps, self.monitor_targets)
        self._sync_action_buttons()
        self._set_status(self._status_text(), C_OK)

    # ---------- 排序交互（首点降序） ----------
    def _on_header_clicked(self, col: int):
        if col == APTableModel.COL_ACTION:
            self.model.clear_sort()
            self.view.horizontalHeader().setSortIndicator(-1, Qt.DescendingOrder)
            return
        if col == self.model.sort_col:
            order = Qt.AscendingOrder if self.model.sort_desc else Qt.DescendingOrder
        else:
            order = Qt.DescendingOrder                 # 首点降序
        self.view.horizontalHeader().setSortIndicator(col, order)
        self.model.sort(col, order)
        self._sync_action_buttons()

    # ---------- 操作列真实按钮 ----------
    def _sync_action_buttons(self):
        # 清掉旧按钮
        for row in range(self.model.rowCount()):
            idx = self.model.index(row, APTableModel.COL_ACTION)
            w = self.view.indexWidget(idx)
            if w is not None:
                self.view.setIndexWidget(idx, None)
                w.deleteLater()
        # 重新放置
        for row in range(self.model.rowCount()):
            idx = self.model.index(row, APTableModel.COL_ACTION)
            ap = self.ap_list[row] if row < len(self.ap_list) else None
            if ap is None:
                continue
            info = self.monitor_targets.get(ap.key())
            restarting = bool(info and info.get("state")
                              in ("waiting", "seen_offline"))
            btn = QPushButton("重启中..." if restarting else "重启")
            btn.setObjectName("rowRestart")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(26)
            if restarting:
                btn.setEnabled(False)
            else:
                btn.clicked.connect(lambda _=False, a=ap: self._confirm_and_restart([a]))
            self.view.setIndexWidget(idx, btn)
        # 保持选中（整表 reset 会清空选中）
        self._restore_selection()

    _selected_keys_backup: list = []

    def _restore_selection(self):
        sel = self.view.selectionModel()
        sel.clearSelection()
        for row, ap in enumerate(self.ap_list):
            if ap.key() in self._selected_keys_backup:
                sel.select(self.model.index(row, 0),
                           self.model.index(row, self.model.columnCount() - 1),
                           sel.Select | sel.Rows)

    # ==============================================================
    # 表格交互：双击 / 右键
    # ==============================================================
    def _on_double_click(self, index: QModelIndex):
        col = index.column()
        if col == APTableModel.COL_ACTION:
            return                       # 按钮自己处理
        if col == APTableModel.COL_COMMENT:
            ap = self.ap_list[index.row()] if index.row() < len(self.ap_list) else None
            if ap is not None:
                self._edit_comment(ap)
            return
        # 其他列：双击 = 重启选中行
        self.on_restart_selected()

    def _on_context_menu(self, pos):
        index = self.view.indexAt(pos)
        if index.isValid():
            if not self.view.selectionModel().isRowSelected(index.row()):
                self.view.selectRow(index.row())
        menu = QMenu(self)
        menu.addAction("重启选中的 AP", self.on_restart_selected)
        menu.addAction("修改备注", self._on_menu_edit_comment)
        menu.addAction("复制 MAC 地址", self._copy_mac)
        menu.addSeparator()
        menu.addAction("刷新列表", lambda: self.on_refresh(quiet=False))
        menu.addAction("打开截图目录", self._open_output_dir)
        menu.addAction("查看配置文件位置", self._show_config_path)
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _selected_aps(self) -> list[APInfo]:
        rows = self.view.selectionModel().selectedRows()
        keys = {self.model.index(i.row(), 0).data(Qt.UserRole).key()
                for i in rows}
        return [a for a in self.ap_list if a.key() in keys]

    def _on_menu_edit_comment(self):
        aps = self._selected_aps()
        if aps:
            self._edit_comment(aps[0])

    def _copy_mac(self):
        aps = self._selected_aps()
        if not aps:
            return
        QApplication.clipboard().setText(aps[0].mac)
        self._append_log("已复制 MAC: %s" % aps[0].mac)

    def _open_output_dir(self):
        try:
            webbrowser.open(OUTPUT_DIR.as_uri())
        except Exception:
            self._append_log("截图目录: %s" % OUTPUT_DIR)

    def _show_config_path(self):
        p = ikuai_config.config_path()
        exists = "已存在" if p.exists() else "尚未生成"
        QMessageBox.information(
            self, "配置文件",
            "配置文件位置：\n%s\n\n状态：%s\n\n"
            "密码字段经 AES-256 加密后存储，\n"
            "密钥由本机特征派生，拷贝到其他机器无法解密。" % (p, exists))
        self._append_log("配置文件: %s（%s）" % (p, exists))

    # ==============================================================
    # 重启
    # ==============================================================
    def on_restart_selected(self):
        if not self.service.connected:
            QMessageBox.information(self, "提示", "请先连接路由器")
            return
        aps = self._selected_aps()
        if not aps:
            QMessageBox.information(self, "提示", "请先选择要重启的 AP")
            return
        self._confirm_and_restart(aps)

    def on_batch_restart(self):
        if not self.service.connected:
            QMessageBox.information(self, "提示", "请先连接路由器")
            return
        if not self.ap_list:
            QMessageBox.information(self, "提示", "AP 列表为空，请先刷新")
            return
        self._confirm_and_restart(list(self.ap_list))

    def _confirm_and_restart(self, targets: list[APInfo]):
        if self.busy:
            return
        names = "\n".join("  · %s (%s)" % (a.name, a.ip or "-")
                          for a in targets[:12])
        if len(targets) > 12:
            names += "\n  ... 以及另外 %d 台" % (len(targets) - 12)
        btn = QMessageBox.question(
            self, "确认重启",
            "即将重启以下 %d 台 AP：\n\n%s\n\n"
            "重启期间连接到这些 AP 的无线终端会短暂断开。\n"
            "确定继续吗？" % (len(targets), names),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if btn != QMessageBox.Yes:
            return

        self._set_busy(True)
        self._set_status("重启中...", C_WARN)
        self._append_log("── 开始重启 %d 台 AP ──" % len(targets))

        def worker():
            b = self.bridge
            try:
                ok_count, total = self.service.restart_aps(
                    targets,
                    progress_cb=lambda nm, i, t: b.sig_log.emit(
                        "  [%d/%d] 处理 %s" % (i, t, nm)))
                b.sig_batch_done.emit(ok_count, total, targets)
            except Exception as ex:
                b.sig_log.emit("批量重启出错：%s" % ex)
                b.sig_batch_done.emit(0, len(targets), targets)
            finally:
                b.sig_busy.emit(False)

        threading.Thread(target=worker, daemon=True).start()

    def _on_batch_done(self, ok: int, total: int, targets: list):
        self._append_log("── 批量重启完成：成功 %d / %d 台 ──" % (ok, total))
        if ok > 0:
            self.monitor_targets = {}
            for ap in targets:
                self.monitor_targets[ap.key()] = {"state": "waiting",
                                                  "ip": ap.ip,
                                                  "name": ap.name}
            self._append_log("已进入监视状态，将持续检测设备恢复（断开 -> 已连接）")
            self._set_status(self._status_text(), C_WARN)
            self._schedule_monitor()
            self._sync_action_buttons()      # 按钮变「重启中...」
        else:
            self._set_status(self._status_text(), C_OK)

    # ==============================================================
    # 监视（三态状态机，与 Tk 版一致）
    # ==============================================================
    def _schedule_monitor(self):
        if not self.monitor_targets:
            return
        self.timer_monitor.stop()
        self.timer_monitor.start(int(self.cmb_refresh.currentText().rstrip("s")) * 1000)

    def _monitor_tick(self):
        if not self.monitor_targets:
            return
        if not self._refresh_inflight and self.service.connected:
            self.on_refresh(quiet=True)
        self._schedule_monitor()

    def _check_monitor(self, aps: list[APInfo]):
        if not self.monitor_targets:
            return
        newly_offline, newly_recovered = [], []
        for ap in aps:
            info = self.monitor_targets.get(ap.key())
            if info is None:
                continue
            state = info.get("state")
            if state == "waiting":
                if not ap.is_alive():
                    info["state"] = "seen_offline"
                    newly_offline.append(ap.name)
            elif state == "seen_offline":
                if ap.is_alive():
                    info["state"] = "recovered"
                    newly_recovered.append(ap.name)
        for nm in newly_offline:
            self._append_log("[已断开] %s" % nm)
        for nm in newly_recovered:
            self._append_log("[已恢复] %s" % nm)
        if newly_recovered:
            all_done = all(v.get("state") == "recovered"
                           for v in self.monitor_targets.values())
            if all_done:
                self.monitor_targets.clear()
                self._just_recovered = True

    def _status_text(self) -> str:
        n = len(self.ap_list)
        if self._just_recovered:
            return "已连接 · 全部恢复（%d 台 AP）" % n
        if self.monitor_targets:
            pending = sum(1 for v in self.monitor_targets.values()
                          if v.get("state") in ("waiting", "seen_offline"))
            if pending:
                return "重启中，等待 %d 台恢复..." % pending
            return "已连接 · 全部恢复"
        return "已连接 · %d 台 AP" % n

    # ==============================================================
    # 修改备注（回写路由器）
    # ==============================================================
    def _edit_comment(self, ap: APInfo):
        if self.busy:
            QMessageBox.information(self, "提示", "当前有任务在执行，请稍后再试")
            return
        new_val, ok = QInputDialog.getText(
            self, "修改备注",
            "AP：%s\nMAC：%s\n\n备注（最多 64 个字符）：" % (ap.name, ap.mac),
            QLineEdit.Normal, ap.comment or "")
        if not ok:
            return
        new_val = new_val.strip()
        if new_val == (ap.comment or "").strip():
            self._append_log("%s 的备注未变化，跳过保存" % ap.name)
            return
        if len(new_val) > 64:
            QMessageBox.warning(
                self, "备注过长",
                "备注最多 64 个字符（当前 %d 个），请精简后再保存。"
                % len(new_val))
            return

        self._append_log("开始保存 %s 的备注：%s" % (ap.name, new_val or "（清空）"))
        self._set_busy(True)
        self._set_status("正在保存备注...", C_WARN)

        def worker():
            b = self.bridge
            try:
                ok2 = self.service.set_ap_comment(ap, new_val)
                b.sig_comment_done.emit(ap.key(), ok2, new_val)
            except Exception as ex:
                b.sig_log.emit("保存备注失败：%s" % ex)
                b.sig_comment_done.emit(ap.key(), False, new_val)
            finally:
                b.sig_busy.emit(False)

        threading.Thread(target=worker, daemon=True).start()

    def _on_comment_done(self, key: str, ok: bool, new_val: str):
        if ok:
            self._append_log("备注已保存：%s" % (new_val or "（空）"))
            for ap in self.ap_list:                 # 本地即时更新
                if ap.key() == key:
                    ap.comment = new_val
                    break
            self.model.set_aps(self.ap_list, self.monitor_targets)
            self._sync_action_buttons()
            self._set_status(self._status_text(), C_OK)
        else:
            self._append_log("备注保存失败，请查看日志")
            self._set_status("备注保存失败", C_ERR)

    # ==============================================================
    # 自动刷新开关
    # ==============================================================
    def _on_autorefresh_toggle(self, on: bool):
        self.auto_refresh = on
        if on:
            self._schedule_refresh()
            self._append_log("已开启自动刷新（%s）" % self.cmb_refresh.currentText())
        else:
            self.timer_refresh.stop()
            self._append_log("已停止自动刷新，列表已静止（可放心勾选）")

    def _on_refresh_change(self, _txt):
        if self.auto_refresh:
            self.timer_refresh.stop()
            self._schedule_refresh()

    def _schedule_refresh(self):
        if not self.auto_refresh:
            return
        self.timer_refresh.start(
            int(self.cmb_refresh.currentText().rstrip("s")) * 1000)

    def _auto_refresh_tick(self):
        if not self.auto_refresh:
            return
        # 备份选中，刷新后恢复（整表 reset 会丢选中）
        self._selected_keys_backup = [a.key() for a in self._selected_aps()]
        if not self._refresh_inflight and self.service.connected:
            self.on_refresh(quiet=True)
        self._schedule_refresh()

    # ==============================================================
    # 关闭
    # ==============================================================
    def closeEvent(self, event):
        if self._closing:
            event.accept()
            return
        self._closing = True
        self.timer_refresh.stop()
        self.timer_monitor.stop()
        self._save_config()
        # 立即关窗口；浏览器由守护线程在后台收尾（不卡 UI，Tk v1.6 的教训）
        threading.Thread(target=self._bg_stop, daemon=True).start()
        event.accept()

    def _bg_stop(self):
        try:
            self.service.stop()
        except Exception:
            pass


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    font = QFont("Microsoft YaHei UI", 9)
    app.setFont(font)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
