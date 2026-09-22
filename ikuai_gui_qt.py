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

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QPoint, QSize,
                            Qt, QTimer, QUrl, Signal, QObject)
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFrame,
                               QGroupBox, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow,
                               QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                               QStyle, QTableView, QVBoxLayout, QWidget)

import ikuai_service
from ikuai_service import (IKuaiService, APInfo, NotConnectedError,
                           OUTPUT_DIR)
import ikuai_projects
import ikuai_config

APP_TITLE = "爱快路由-AP终端工具"
APP_VERSION = "1.2"
APP_COPYRIGHT = "| © Jcsit&viclai"

# GitHub 项目入口（状态栏右侧按钮）
GITHUB_REPO_URL = "https://github.com/vic4728/ikuai-ap-tool"
GITHUB_RELEASES_URL = GITHUB_REPO_URL + "/releases"
# 自有下载站（版本检查源，versions.json 由下载页同源提供）
UPDATE_CHECK_URL = "https://svr.jcsit.cn/ikuai/versions.json"
UPDATE_OPEN_URL = "https://svr.jcsit.cn/ikuai/"

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
    selection-color: #111827; font-size: 12px;
}
QHeaderView::section {
    background: #f2f4f7; color: #4b5563; font-weight: bold;
    border: none; border-bottom: 1px solid %(border)s;
    border-right: 1px solid #edf0f4; padding: 5px 6px; font-size: 12px;
}
QTableView::item { padding: 3px 6px; }
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
/* ---- 新版本提示（标题栏紧凑胶囊，仅有新版时显示） ---- */
QPushButton#updateLabel {
    background: #eaf2ff; border: 1px solid #a8c8f8;
    border-radius: 7px; padding: 0px 9px;
    color: #1d4ed8; font-size: 12px; font-weight: bold;
    min-height: 12px; max-height: 12px;
}
QPushButton#updateLabel:hover {
    background: #d8e8ff; border-color: #2563eb;
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
/* 标题栏信息胶囊：紧凑样式（总高 14px = 12 内容 + 2 边框，边框紧贴文字） */
QLabel#titlePill {
    background: #e8f0fe; border: 1px solid #b6d0f7;
    border-radius: 7px; padding: 0px 7px;
    color: #1e50c8; font-size: 11px; font-weight: bold;
    min-height: 12px; max-height: 12px;
}
QLabel#titlePillMuted {
    background: #f1f3f6; border: 1px solid #dde1e8;
    border-radius: 7px; padding: 0px 7px;
    color: #6b7280; font-size: 11px;
    min-height: 12px; max-height: 12px;
}
QWidget#content { background: %(bg)s; }
/* ---- 项目侧边栏（结构与 CardWidget 同构：cardHeader + cardBody） ---- */
QWidget#sidebar {
    background: %(card)s; border: 1px solid %(border)s;
    border-radius: 10px;
}
QPushButton#sideNewBtn, QPushButton#sideDelBtn {
    background: #eaf1fe; color: #1d4ed8; border: 1px solid #c3d8fb;
    border-radius: 5px; font-weight: bold; font-size: 13px;
    padding: 0px;
}
QPushButton#sideNewBtn:hover, QPushButton#sideDelBtn:hover {
    background: #d8e7fd;
}
QListWidget#projList {
    background: transparent; border: none; outline: none;
    font-size: 12px;
}
QListWidget#projList::item {
    background: #f6f8fb; border: 1px solid #e5eaf1; border-radius: 8px;
    padding: 8px 10px; margin: 3px 6px;
}
QListWidget#projList::item:hover { background: #eef3fa; }
QListWidget#projList::item:selected {
    background: #dbeafe; border-color: #93c5fd;
}
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
        """整表替换（带排序保持）。数据量 <=500，整表刷新足够快。

        注意：排序发生在 self._aps 上，调用方（会话）的 ap_list 也必须
        同步为排序后的顺序 —— 否则操作按钮按 ap_list[row] 放置时会
        【错位到别的设备】（行号是排序后的、对象是排序前的）。
        """
        self.beginResetModel()
        self._aps = list(aps)
        self._monitor = monitor
        if self.sort_col is not None:
            self._apply_sort()
        self.endResetModel()
        self.sync_back()

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

    @staticmethod
    def _nat_key(v: str):
        """自然排序：数字段按数值比较（a1 < a5 < a10，而非字典序 a1<a10<a5）。

        AP 名称普遍带数字后缀（IK-SW5_a1..a12），字典序会排成
        a1,a10,a11,a12,a2... 对运维选型非常反直觉。
        """
        parts = re.split(r"(\d+)", (v or "").lower())
        return tuple((1, int(p)) if p.isdigit() else (0, p)
                     for p in parts if p != "")

    def sort(self, column: int, order=Qt.DescendingOrder):
        """点击表头（QTableView 排序入口）。首点降序由调用方控制 order。"""
        if not 0 <= column < len(self.COL_KEYS) - 1:   # 操作列不可排序
            return
        self.sort_col = column
        self.sort_desc = (order == Qt.DescendingOrder)
        self.beginResetModel()
        self._apply_sort()
        self.endResetModel()
        self.sync_back()

    def sync_back(self):
        """把（可能已排序的）行序写回数据源持有者。

        MainWindow._render_aps 里 self.ap_list 是会话 ap_list 的代理
        （setter 转发 current），排序后必须同步，否则操作按钮/
        双击取行/选中恢复全部按旧行序取对象 —— 重启错设备的隐患。
        """
        try:
            win = self.parent()
            if win is not None and getattr(win, "ap_list", None) is not None:
                win.ap_list = list(self._aps)
        except RuntimeError:      # 窗口已销毁（关闭中）
            pass

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
            return self._nat_key(v)      # 文本列自然序（a1<a5<a10）

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

        # 程序图标 + 标题（主名 + 版本胶囊 + 版权胶囊）
        self.lbl_icon = QLabel()
        ic = load_app_icon()
        if not ic.isNull():
            self.lbl_icon.setPixmap(ic.pixmap(20, 20))
        self.lbl_title = QLabel(APP_TITLE, objectName="titleText")
        self.lbl_ver = QLabel("v%s" % APP_VERSION, objectName="titlePill")
        self.lbl_cr = QLabel(APP_COPYRIGHT.strip("| "),
                             objectName="titlePillMuted")
        lay.addWidget(self.lbl_icon)
        lay.addWidget(self.lbl_title)
        lay.addWidget(self.lbl_ver)
        lay.addWidget(self.lbl_cr)
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

        # 新版本提示（默认隐藏；检查到新版本时显示蓝色文字，点击打开下载页）
        self.lbl_update = QPushButton(objectName="updateLabel")
        self.lbl_update.setToolTip("发现新版本，点击打开下载页")
        self.lbl_update.setCursor(Qt.PointingHandCursor)
        self.lbl_update.hide()
        # 紧凑胶囊：QSS min/max-height 控内容高（+2px 边框 = 总高 14px）
        self.lbl_update.clicked.connect(
            lambda: webbrowser.open(UPDATE_OPEN_URL))
        lay.addWidget(self.lbl_update)

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
# 项目会话（每项目一套：service + bridge + 状态 + 定时器 + 日志）
# ======================================================================
class ProjectSession:
    """一个项目 = 一条独立的路由器连接上下文。

    - service：独立 IKuaiService（独立 Playwright/浏览器实例，可多开并行）
    - bridge：独立信号桥（信号带 project_id 路由回 UI）
    - 状态：ap_list / monitor / busy / inflight 等（原 MainWindow 上的那套）
    - 日志：logs/<项目名>.log 独立文件（ikuai_projects.get_logger）
    """

    def __init__(self, proj: dict):
        self.proj = proj                      # 项目配置 dict（含 id/name/...）
        self.pid = proj["id"]
        self.name = proj.get("name", "")
        self.service = IKuaiService(
            log_cb=self._svc_log, headless=proj.get("headless", True))
        self.bridge = UiBridge()
        self.ap_list: list[APInfo] = []
        self.busy = False
        self.auto_refresh = False
        self.refresh_inflight = False
        self.monitor_targets: dict[str, dict] = {}
        self.just_recovered = False
        self.last_aps_count = -1          # 首轮必记「检测到 N 台」
        self.timer_refresh: QTimer | None = None
        self.timer_monitor: QTimer | None = None
        self.selected_backup: list = []

    # ---- 状态文案（连接状态给侧边栏显示） ----
    def state_text(self) -> str:
        if self.busy and not self.service.connected:
            return "连接中"
        if self.service.connected:
            n = len(self.ap_list)
            if self.monitor_targets:
                pending = sum(1 for v in self.monitor_targets.values()
                              if v.get("state") in ("waiting", "seen_offline"))
                if pending:
                    return "重启中"
                return "已恢复"
            if self.just_recovered:
                return "已恢复"
            return "已连接" + ("·%d台" % n if n else "")
        return "未连接"

    def _svc_log(self, line: str) -> None:
        """service 工作线程日志 -> 项目日志文件（直接写，线程安全）。"""
        try:
            ikuai_projects.get_logger(self.name).info(
                line.strip()[:500])
        except Exception:
            pass


# ======================================================================
# 项目新建/编辑弹窗（用户指定布局）
# ======================================================================
class ProjectDialog(QDialog):
    """新建/编辑项目。

    布局（用户指定）：
      项目名称
      IP/域名 | 端口 | 协议
      用户 | 密码
      记住密码 | 自动登录 | 隐藏浏览窗口
      取消 | 保存
    """

    def __init__(self, parent=None, title="新建项目", proj: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.proj_data: dict | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        # 项目名称
        row0 = QHBoxLayout()
        row0.addWidget(QLabel("项目名称"))
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("例如：花舍车间 / 办公室")
        row0.addWidget(self.ed_name, 1)
        lay.addLayout(row0)

        # IP/域名 | 端口 | 协议
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("IP/域名"))
        self.ed_host = QLineEdit()
        self.ed_host.setPlaceholderText("IPv4 / IPv6 / 域名")
        self.ed_host.setMinimumWidth(160)
        row1.addWidget(self.ed_host, 1)
        row1.addWidget(QLabel("端口"))
        self.ed_port = QLineEdit()
        self.ed_port.setFixedWidth(56)
        self.ed_port.setText("80")
        row1.addWidget(self.ed_port)
        self.cmb_scheme = QComboBox()
        self.cmb_scheme.addItems(["HTTP", "HTTPS"])
        self.cmb_scheme.setFixedWidth(78)
        self.cmb_scheme.currentTextChanged.connect(self._scheme_change)
        row1.addWidget(self.cmb_scheme)
        lay.addLayout(row1)

        # 用户 | 密码
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("用户"))
        self.ed_user = QLineEdit()
        self.ed_user.setFixedWidth(120)
        self.ed_user.setText("admin")
        row2.addWidget(self.ed_user)
        row2.addWidget(QLabel("密码"))
        self.ed_pwd = QLineEdit()
        self.ed_pwd.setEchoMode(QLineEdit.Password)
        row2.addWidget(self.ed_pwd, 1)
        self.chk_show = QCheckBox("显示")
        self.chk_show.toggled.connect(
            lambda on: self.ed_pwd.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password))
        row2.addWidget(self.chk_show)
        lay.addLayout(row2)

        # 记住密码 | 自动登录 | 隐藏浏览窗口
        row3 = QHBoxLayout()
        self.chk_remember = QCheckBox("记住密码")
        self.chk_remember.setChecked(True)
        row3.addWidget(self.chk_remember)
        self.chk_auto = QCheckBox("自动登录")
        row3.addWidget(self.chk_auto)
        self.chk_headless = QCheckBox("隐藏浏览窗口")
        self.chk_headless.setChecked(True)
        row3.addWidget(self.chk_headless)
        row3.addStretch(1)
        lay.addLayout(row3)

        # 取消 | 保存
        btns = QDialogButtonBox()
        btn_cancel = btns.addButton("取消", QDialogButtonBox.RejectRole)
        btn_save = btns.addButton("保存", QDialogButtonBox.AcceptRole)
        btn_save.setObjectName("primary")
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        # 编辑模式回填
        if proj:
            self.ed_name.setText(proj.get("name", ""))
            self.ed_host.setText(proj.get("host", ""))
            self.ed_port.setText(str(proj.get("port", 80)))
            self.cmb_scheme.setCurrentText(proj.get("scheme", "HTTP"))
            self.ed_user.setText(proj.get("user", "admin"))
            if proj.get("remember"):
                self.ed_pwd.setText(proj.get("password", ""))
            self.chk_remember.setChecked(bool(proj.get("remember")))
            self.chk_auto.setChecked(bool(proj.get("autologin")))
            self.chk_headless.setChecked(bool(proj.get("headless", True)))

    def _scheme_change(self, txt):
        if txt == "HTTPS" and self.ed_port.text() == "80":
            self.ed_port.setText("443")
        elif txt == "HTTP" and self.ed_port.text() == "443":
            self.ed_port.setText("80")

    def _on_save(self):
        name = self.ed_name.text().strip()
        host = self.ed_host.text().strip()
        user = self.ed_user.text().strip()
        pwd = self.ed_pwd.text()
        if not name:
            QMessageBox.warning(self, "提示", "请填写项目名称")
            return
        if not host:
            QMessageBox.warning(self, "提示", "请填写 IP/域名")
            return
        if not user or not pwd:
            QMessageBox.warning(self, "提示", "请填写用户和密码")
            return
        try:
            port = int(self.ed_port.text())
        except ValueError:
            QMessageBox.warning(self, "提示", "端口必须是数字")
            return
        self.proj_data = {
            "name": name,
            "host": host,
            "port": port,
            "scheme": self.cmb_scheme.currentText(),
            "user": user,
            "password": pwd,
            "remember": self.chk_remember.isChecked(),
            "autologin": self.chk_auto.isChecked(),
            "headless": self.chk_headless.isChecked(),
        }
        self.accept()


# ======================================================================
# 主窗口
# ======================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("%s v%s %s" % (APP_TITLE, APP_VERSION, APP_COPYRIGHT))
        self.resize(1220, 800)
        self.setMinimumSize(1080, 660)
        self.setWindowIcon(load_app_icon())
        # 自定义标题栏：无边框 + 自绘标题条（GitHub 入口 + 窗口按钮）
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)

        # ---- 多项目状态 ----
        self._closing = False
        self._pdata = ikuai_projects.load()          # {active, projects[]}
        self._migrate_v1_config()                    # 1.0 连接配置 -> 项目
        self.sessions: dict[str, ProjectSession] = {}
        self.current: ProjectSession | None = None   # 当前选中的会话
        # 兼容层：旧代码用 self.service/self.bridge/... —— 全部代理到 current
        # （property 定义见类尾部）

        self._build_ui()
        self._connect_all_session_signals()
        self._load_config_into_ui()

        # 恢复项目：为每个项目建会话 + 侧边栏项
        for proj in self._pdata["projects"]:
            self._add_session(proj, select=False)
        # 选中上次活跃的（或第一个）
        act = self._pdata.get("active", "")
        if act not in self.sessions and self.sessions:
            act = next(iter(self.sessions))
        if act:
            self._select_session(act)
            # 1.0 配置迁移提示（UI/日志已就绪）
            if getattr(self, "_pending_v1_migrate_note", None):
                self._append_log("已自动导入 1.0 版连接配置为项目「%s」"
                                 % self._pending_v1_migrate_note)
                self._pending_v1_migrate_note = None
            # 勾了自动登录且记住密码的项目 -> 自动连接（全部并行）
            for s in self.sessions.values():
                if (s.proj.get("autologin") and s.proj.get("password")
                        and s.proj.get("remember")):
                    QTimer.singleShot(600, lambda ss=s: self._connect_session(ss))

        # 启动 2 秒后检查新版本（后台静默，失败无感知）
        QTimer.singleShot(2000, self._check_update)

    # ---------- 属性代理：旧代码无缝访问当前会话 ----------
    @property
    def service(self):
        return self.current.service if self.current else None

    @property
    def bridge(self):
        return self.current.bridge if self.current else None

    @property
    def ap_list(self):
        return self.current.ap_list if self.current else []

    @ap_list.setter
    def ap_list(self, v):
        if self.current:
            self.current.ap_list = v

    @property
    def busy(self):
        return self.current.busy if self.current else False

    @busy.setter
    def busy(self, v):
        if self.current:
            self.current.busy = v

    @property
    def auto_refresh(self):
        return self.current.auto_refresh if self.current else False

    @auto_refresh.setter
    def auto_refresh(self, v):
        if self.current:
            self.current.auto_refresh = v

    @property
    def _refresh_inflight(self):
        return self.current.refresh_inflight if self.current else False

    @_refresh_inflight.setter
    def _refresh_inflight(self, v):
        if self.current:
            self.current.refresh_inflight = v

    @property
    def monitor_targets(self):
        return self.current.monitor_targets if self.current else {}

    @monitor_targets.setter
    def monitor_targets(self, v):
        if self.current:
            self.current.monitor_targets = v

    @property
    def _just_recovered(self):
        return self.current.just_recovered if self.current else False

    @_just_recovered.setter
    def _just_recovered(self, v):
        if self.current:
            self.current.just_recovered = v

    @property
    def timer_refresh(self):
        return self.current.timer_refresh if self.current else None

    @timer_refresh.setter
    def timer_refresh(self, v):
        if self.current:
            self.current.timer_refresh = v

    @property
    def timer_monitor(self):
        return self.current.timer_monitor if self.current else None

    @timer_monitor.setter
    def timer_monitor(self, v):
        if self.current:
            self.current.timer_monitor = v

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

        # ---------- 内容区：左侧项目边栏 + 右侧工作区 ----------
        content = QWidget(objectName="content")
        lay.addWidget(content, 1)
        body = QHBoxLayout(content)
        body.setContentsMargins(14, 8, 14, 12)
        body.setSpacing(10)

        # ---- 左：项目侧边栏 ----
        # 结构与右侧 CardWidget 完全同构：cardHeader 标题条（含强调蓝条
        # + 功能按钮）+ cardBody 列表区 —— 样式/圆角/对齐与内容区一致
        side = QWidget(objectName="sidebar")
        side.setFixedWidth(200)
        slay = QVBoxLayout(side)
        # 顶边 3px：与右侧卡片（border1+clay1+cardmargin1）标题条基线对齐
        slay.setContentsMargins(1, 3, 1, 1)
        slay.setSpacing(0)

        head = QFrame(objectName="cardHeader")
        hlay = QHBoxLayout(head)
        # (12,4,8,4)：按钮 20px + 8 = 28px，与卡片 header（标题+12）同高
        hlay.setContentsMargins(12, 4, 8, 4)
        hlay.setSpacing(8)
        accent = QFrame(objectName="cardAccent")
        accent.setFixedSize(4, 16)
        hlay.addWidget(accent)
        hlay.addWidget(QLabel("项 目", objectName="cardTitle"))
        hlay.addStretch(1)
        btn_new = QPushButton(objectName="sideNewBtn")
        btn_new.setFixedSize(24, 20)
        btn_new.setToolTip("新建项目")
        btn_new.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_FileDialogNewFolder))
        btn_new.clicked.connect(self.on_new_project)
        hlay.addWidget(btn_new)
        btn_del = QPushButton(objectName="sideDelBtn")
        btn_del.setFixedSize(24, 20)
        btn_del.setToolTip("删除选中项目")
        btn_del.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_TrashIcon))
        btn_del.clicked.connect(self.on_delete_project)
        hlay.addWidget(btn_del)
        slay.addWidget(head)

        list_wrap = QWidget(objectName="cardBody")
        lw_lay = QVBoxLayout(list_wrap)
        lw_lay.setContentsMargins(4, 6, 4, 6)
        lw_lay.setSpacing(0)

        self.proj_list = QListWidget(objectName="projList")
        self.proj_list.setIconSize(QSize(1, 1))     # 纯文字项
        self.proj_list.itemClicked.connect(self._on_proj_item_clicked)
        self.proj_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.proj_list.customContextMenuRequested.connect(
            self._on_proj_context_menu)
        lw_lay.addWidget(self.proj_list)
        slay.addWidget(list_wrap, 1)
        body.addWidget(side)

        # ---- 右：工作区（原有三卡片） ----
        work = QWidget()
        body.addWidget(work, 1)
        clay = QVBoxLayout(work)
        clay.setContentsMargins(0, 1, 0, 0)   # 与侧栏 cardHeader 顶部基线对齐
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
        hh.setSortIndicatorShown(True)          # 排序箭头（用户可见反馈）
        hh.setSortIndicator(-1, Qt.DescendingOrder)
        hh.setSectionsMovable(False)            # 列顺序固定，避免与排序状态错乱
        # 列宽：合计 ~950px，视口 960 目标内全 9 列可见
        widths = [78, 142, 100, 112, 140, 100, 80, 130, 68]
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
        # 缩放说明：无边框窗口的边缘缩放由 nativeEvent 的 WM_NCHITTEST
        # 提供（所有边缘+四角可拖），状态栏不再放 grip（曾有双 grip 问题）。
        self.statusBar().setSizeGripEnabled(False)

    def _open_github(self):
        """打开 GitHub 发行版下载页（本项目的 Releases）。"""
        url = GITHUB_RELEASES_URL
        try:
            webbrowser.open(url)
            self._append_log("已打开浏览器: %s" % url)
        except Exception as ex:
            self._append_log("打开失败：%s（手动访问 %s）" % (ex, url))

    # ==============================================================
    # 新版本检查（自有下载站 versions.json，静默失败不打扰）
    # ==============================================================
    def _check_update(self):
        """后台线程拉版本清单，比当前版本新 -> 信号回 UI 显示提示。"""
        import urllib.request, ssl, json as _json

        def worker():
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                req = urllib.request.Request(UPDATE_CHECK_URL,
                                             headers={"User-Agent": "ikuai-ap-tool"})
                with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
                    data = _json.loads(r.read().decode("utf-8"))
                # versions.json：按版本倒序，第一条即最新
                latest = (data[0].get("version") or "").strip()
                if latest and self._ver_newer(latest, APP_VERSION):
                    QTimer.singleShot(0, lambda: self._on_update_found(latest))
            except Exception:
                pass          # 内网/断网/格式异常 -> 静默，不打扰用户

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _ver_newer(remote: str, local: str) -> bool:
        """语义化版本比较：remote > local 才提示（1.10 > 1.9）。"""
        def key(v):
            import re
            nums = re.findall(r"\d+", v)
            return tuple(int(n) for n in nums[:3])
        try:
            return key(remote) > key(local)
        except Exception:
            return False

    def _on_update_found(self, version: str):
        """UI 线程：标题栏显示蓝色「新版本vxx，请更新」。"""
        if self._closing:
            return
        lbl = self.title_bar.lbl_update
        lbl.setText("新版本v%s，请更新" % version)
        lbl.show()
        self._append_log("发现新版本 v%s（点击标题栏提示可打开下载页 %s）"
                         % (version, UPDATE_OPEN_URL))

    # ==============================================================
    # 信号（多会话：每会话的 bridge 都接同一组槽，槽内按 pid 路由）
    # ==============================================================
    def _connect_all_session_signals(self):
        """已有会话 + 未来新建会话统一接线。"""
        for s in self.sessions.values():
            self._wire_session(s)

    def _wire_session(self, s: "ProjectSession"):
        b = s.bridge
        # 避免重复接线（receivers 用信号签名字符串；已接线则跳过）
        try:
            if b.receivers("2sig_log(QString)"):
                return
        except TypeError:
            pass
        b.sig_log.connect(lambda m, ss=s: self._route_log(ss, m))
        b.sig_aps.connect(lambda l, ss=s: self._route_aps(ss, l))
        b.sig_refresh_done.connect(lambda _=None, ss=s: self._route_refresh_done(ss))
        b.sig_connected.connect(lambda ss=s: self._route_connected(ss))
        b.sig_disconnected.connect(lambda ss=s: self._route_disconnected(ss))
        b.sig_connect_failed.connect(lambda m, ss=s: self._route_failed(ss, m))
        b.sig_busy.connect(lambda v, ss=s: self._route_busy(ss, v))
        b.sig_batch_done.connect(lambda ok, tot, tg, ss=s: self._route_batch(ss, ok, tot, tg))
        b.sig_comment_done.connect(lambda k, ok, v, ss=s: self._route_comment(ss, k, ok, v))
        # 每会话独立定时器（parent=self，回调按 pid 分发）
        s.timer_refresh = QTimer(self)
        s.timer_refresh.setSingleShot(True)
        s.timer_refresh.timeout.connect(lambda ss=s: self._session_refresh_tick(ss))
        s.timer_monitor = QTimer(self)
        s.timer_monitor.setSingleShot(True)
        s.timer_monitor.timeout.connect(lambda ss=s: self._session_monitor_tick(ss))

    # ---- 路由槽：只处理"当前显示会话"的 UI 更新，其余只动侧边栏状态 ----
    def _route_log(self, s, msg):
        if self.current is s:
            self._append_log(msg)
        self._update_proj_item(s)

    def _route_aps(self, s, aps):
        s.ap_list = list(aps)
        self._check_monitor_session(s, aps)
        # 数量变化才记日志（首轮必记；自动刷新同数不刷屏）
        n = len(aps)
        if n != s.last_aps_count:
            if self.current is s:
                self._append_log("检测到 %d 台 AP 终端" % n)
            else:
                self._append_log("[%s] 检测到 %d 台 AP 终端" % (s.name, n))
            s.last_aps_count = n
        if self.current is s:
            self._render_aps(aps)
            self._set_status(self._status_text(), C_OK)
        self._update_proj_item(s)

    def _route_refresh_done(self, s):
        s.refresh_inflight = False
        if self.current is s:
            self._set_status(self._status_text(), C_OK)
            s.just_recovered = False

    def _route_connected(self, s):
        s.busy = False
        # 无论是否在前台，都拉一轮列表（后台项目也要有数据）
        self._refresh_session(s, quiet=True)
        if self.current is s:
            # 【修复】成功路径也要复位工具栏 —— 原来只复位 s.busy 漏了
            # _set_busy(False)，导致连完第一个项目后「连接/刷新/断开」
            # 永久灰死，第二个项目点连接无响应（右键菜单绕过按钮所以能用）
            self._set_busy(False)
            self._append_log("登录成功，已进入后台")
            s.auto_refresh = self.chk_autorefresh.isChecked()
            if s.auto_refresh:
                self._schedule_refresh()
        self._update_proj_item(s)

    def _route_disconnected(self, s):
        s.busy = False
        s.ap_list = []
        if self.current is s:
            self._set_busy(False)
            self._set_status("未连接", C_ERR)
            self._render_aps([])
            self._append_log("已断开连接")
        self._update_proj_item(s)

    def _route_failed(self, s, msg):
        s.busy = False
        if self.current is s:
            self._set_busy(False)
            self._set_status("连接失败", C_ERR)
            self._append_log("[连接失败] %s" % msg)
            hint = ikuai_service.browser_missing_hint(msg)
            if hint:
                self._append_log(hint)
                QMessageBox.warning(self, "缺少浏览器内核", hint)
            else:
                QMessageBox.critical(self, "连接失败", msg)
        self._update_proj_item(s)

    def _route_busy(self, s, v):
        s.busy = v
        if self.current is s:
            self._set_busy(v)

    def _route_batch(self, s, ok, total, targets):
        if self.current is s:
            self._on_batch_done(ok, total, targets)
        self._update_proj_item(s)

    def _route_comment(self, s, key, ok, val):
        if self.current is s:
            self._on_comment_done(key, ok, val)

    # ---- 每会话定时器 tick ----
    def _session_refresh_tick(self, s):
        if not s.auto_refresh:
            return
        s.selected_backup = [a.key() for a in self._selected_aps_session(s)]
        if not s.refresh_inflight and s.service.connected:
            self._refresh_session(s, quiet=True)
        self._schedule_refresh_session(s)

    def _session_monitor_tick(self, s):
        if not s.monitor_targets:
            return
        if not s.refresh_inflight and s.service.connected:
            self._refresh_session(s, quiet=True)
        self._schedule_monitor_session(s)

    # ---- 会话级操作 ----
    def _refresh_session(self, s, quiet=True):
        if s.refresh_inflight or not s.service.connected:
            return
        s.refresh_inflight = True
        import threading

        def worker():
            b = s.bridge
            try:
                aps = s.service.fetch_ap_list()
                b.sig_aps.emit(aps)
            except Exception as ex:
                b.sig_log.emit("刷新失败：%s" % ex)
            finally:
                b.sig_refresh_done.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _connect_session(self, s):
        """连接指定项目（从项目配置取参数）。"""
        if s.busy:
            return
        p = s.proj
        host = (p.get("host") or "").strip()
        user = (p.get("user") or "").strip()
        pwd = p.get("password") or ""
        if not host or not user or not pwd:
            if self.current is s:
                QMessageBox.warning(self, "提示",
                                    "请先在项目里填写地址、用户和密码")
            return
        port = p.get("port") or 80
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 80
        s.busy = True
        if self.current is s:
            self._set_busy(True)
            self._set_status("连接中...", C_WARN)
            self._append_log("── 开始连接 %s ──" % host)
        import threading
        use_https = (p.get("scheme") == "HTTPS")
        headless = p.get("headless", True)

        def worker():
            b = s.bridge
            try:
                try:
                    s.service.set_headless(headless)
                except RuntimeError as ex:
                    b.sig_log.emit("提示：%s（将继续用当前模式连接）" % ex)
                s.service.start()
                s.service.connect(host, port, use_https, user, pwd)
                b.sig_connected.emit()
            except Exception as ex:
                b.sig_busy.emit(False)
                b.sig_connect_failed.emit(str(ex))

        threading.Thread(target=worker, daemon=True).start()

    def _selected_aps_session(self, s) -> list:
        """当前视图的选中（仅对 current 有意义）。"""
        if self.current is not s:
            return []
        return self._selected_aps()

    # ---- 监视（会话级状态机，逻辑同单项目版） ----
    def _check_monitor_session(self, s, aps):
        if not s.monitor_targets:
            return
        newly_offline, newly_recovered = [], []
        for ap in aps:
            info = s.monitor_targets.get(ap.key())
            if info is None:
                continue
            st = info.get("state")
            if st == "waiting":
                if not ap.is_alive():
                    info["state"] = "seen_offline"
                    newly_offline.append(ap.name)
            elif st == "seen_offline":
                if ap.is_alive():
                    info["state"] = "recovered"
                    newly_recovered.append(ap.name)
        for nm in newly_offline:
            if self.current is s:
                self._append_log("[已断开] %s" % nm)
        for nm in newly_recovered:
            if self.current is s:
                self._append_log("[已恢复] %s" % nm)
        if newly_recovered:
            if all(v.get("state") == "recovered"
                   for v in s.monitor_targets.values()):
                s.monitor_targets.clear()
                s.just_recovered = True

    def _schedule_refresh_session(self, s):
        if not s.auto_refresh:
            return
        if s.timer_refresh is None:
            self._wire_session(s)
        s.timer_refresh.start(
            int(self.cmb_refresh.currentText().rstrip("s")) * 1000)

    def _schedule_monitor_session(self, s):
        if not s.monitor_targets:
            return
        if s.timer_monitor is None:
            self._wire_session(s)
        s.timer_monitor.start(
            int(self.cmb_refresh.currentText().rstrip("s")) * 1000)

    # ==============================================================
    # 项目管理（新建/删除/切换/侧边栏）
    # ==============================================================
    def _migrate_v1_config(self):
        """1.0 版连接配置（ikuai_config.json）自动迁移为本版项目。

        仅当 projects.json 还没有任何项目、且 1.0 配置文件真实存在时
        迁移（load_config 的默认值里预填了演示地址 192.168.50.1，
        不能当作"用户数据"——必须以文件存在为准）。
        一次性：迁移后源文件改名 .migrated 防止重复导入。
        """
        try:
            if self._pdata["projects"]:
                return                      # 已有项目，无需迁移
            if not ikuai_config.CONFIG_FILE.is_file():
                return                      # 无 1.0 配置文件（全新用户）
            old = ikuai_config.load_config()
            host = (old.get("host") or "").strip()
            if not host:
                return                      # 1.0 无可用连接数据
            remember = bool(old.get("remember"))
            proj = ikuai_projects._default_project(host)
            proj.update(
                host=host,
                port=old.get("https_port" if old.get("scheme") == "HTTPS"
                             else "http_port") or 80,
                scheme=old.get("scheme", "HTTP"),
                user=old.get("user", "admin"),
                # 密码：勾了记住密码才有（AES 机器绑定，本机可解）
                password=(old.get("password") or "") if remember else "",
                remember=remember,
                autologin=bool(old.get("autologin")),
                headless=bool(old.get("headless", True)),
            )
            self._pdata["projects"].append(proj)
            self._pdata["active"] = proj["id"]
            ikuai_projects.save(self._pdata)
            # 源文件改名，防止删 projects.json 后重复迁移
            src = ikuai_config.CONFIG_FILE
            if src.is_file():
                src.rename(src.with_suffix(".json.migrated"))
            self._pending_v1_migrate_note = host   # UI 就绪后写日志
        except Exception:
            pass                                # 迁移失败不阻断启动

    def _add_session(self, proj: dict, select=True):
        s = ProjectSession(proj)
        self.sessions[s.pid] = s
        self._wire_session(s)
        self._render_proj_list()
        if select:
            self._select_session(s.pid)
        return s

    def _select_session(self, pid: str):
        if pid not in self.sessions:
            return
        # 保存当前 UI 表单值到旧项目（用户可能改了输入框）
        self._sync_form_to_current_proj()
        self.current = self.sessions[pid]
        self._pdata["active"] = pid
        # 表单回填
        self._fill_form_from_proj(self.current.proj)
        # 渲染该会话的现有数据
        self.model.set_aps(self.current.ap_list, self.current.monitor_targets)
        self._sync_action_buttons()
        self._set_status(self._status_text(),
                         C_OK if self.current.service.connected else C_ERR)
        # 日志面板清空（各项目日志在文件里，界面显示当前项目会话内新日志）
        self.txt_log.clear()
        self._append_log("── 切换到项目「%s」 ──" % self.current.name)
        # 【修复】按钮可用性跟随新会话的 busy 状态（原：连着的会话把按钮
        # 禁用后切走再切回，按钮状态不刷新）
        self._set_busy(self.current.busy)
        self._render_proj_list()
        ikuai_projects.save(self._pdata)

    def _fill_form_from_proj(self, p: dict):
        self.edit_host.setText(str(p.get("host", "")))
        self.edit_port.setText(str(p.get("port", 80)))
        self.cmb_scheme.setCurrentText(p.get("scheme", "HTTP"))
        self.edit_user.setText(str(p.get("user", "admin")))
        self.chk_remember.setChecked(bool(p.get("remember", False)))
        self.chk_autologin.setChecked(bool(p.get("autologin", False)))
        self.chk_headless.setChecked(bool(p.get("headless", True)))
        pw = p.get("password") or ""
        self.edit_password.setText(pw if p.get("remember") else "")

    def _sync_form_to_current_proj(self):
        """UI 表单 -> 当前项目配置（切换/保存时调用）。"""
        if self.current is None:
            return
        p = self.current.proj
        p["host"] = self.edit_host.text().strip()
        try:
            p["port"] = int(self.edit_port.text())
        except ValueError:
            p["port"] = 80
        p["scheme"] = self.cmb_scheme.currentText()
        p["user"] = self.edit_user.text().strip()
        p["remember"] = self.chk_remember.isChecked()
        p["autologin"] = self.chk_autologin.isChecked()
        p["headless"] = self.chk_headless.isChecked()
        if p["remember"]:
            p["password"] = self.edit_password.text()
        ikuai_projects.save(self._pdata)

    def _render_proj_list(self):
        self.proj_list.blockSignals(True)
        self.proj_list.clear()
        for pid, s in self.sessions.items():
            p = s.proj
            title = p.get("name", "未命名")
            state = s.state_text()
            sub = "%s|%s|%s" % (p.get("scheme", "HTTP"),
                                p.get("host", "") or "-",
                                p.get("port", ""))
            it = QListWidgetItem()
            it.setText("%s\n%s\n%s" % (title, state, sub))
            it.setData(Qt.UserRole, pid)
            # 状态色
            color = (C_OK if "已连接" in state or "已恢复" in state
                     else C_WARN if "连接中" in state or "重启中" in state
                     else C_ERR)
            it.setForeground(QColor(color))
            self.proj_list.addItem(it)
            if self.current is s:
                self.proj_list.setCurrentItem(it)
        self.proj_list.blockSignals(False)

    def _update_proj_item(self, s):
        """某会话状态变化 -> 刷新侧边栏（轻量：全量重绘，项目数少）。"""
        self._render_proj_list()

    def _on_proj_item_clicked(self, item):
        pid = item.data(Qt.UserRole)
        if pid:
            self._select_session(pid)

    def _on_proj_context_menu(self, pos):
        item = self.proj_list.itemAt(pos)
        if not item:
            return
        pid = item.data(Qt.UserRole)
        s = self.sessions.get(pid)
        if not s:
            return
        menu = QMenu(self)
        if s.service.connected:
            menu.addAction("断开此项目", lambda: self._disconnect_session(s))
        else:
            menu.addAction("连接此项目", lambda: self._connect_session(s))
        menu.addAction("编辑项目设置", lambda: self._edit_project_dialog(s.proj))
        menu.addSeparator()
        menu.addAction("删除项目", lambda: self.on_delete_project())
        menu.exec(self.proj_list.viewport().mapToGlobal(pos))

    def _disconnect_session(self, s):
        s.busy = True
        if s.timer_refresh:
            s.timer_refresh.stop()
        if s.timer_monitor:
            s.timer_monitor.stop()
        s.monitor_targets = {}
        s.refresh_inflight = False
        if self.current is s:
            self._set_status("正在断开...", C_WARN)
            self._append_log("正在断开连接...")
            self._set_busy(True)
        import threading

        def worker():
            try:
                s.service.stop()
            except Exception as ex:
                s.bridge.sig_log.emit("关闭浏览器时出错：%s" % ex)
            s.bridge.sig_disconnected.emit()

        threading.Thread(target=worker, daemon=True).start()

    # ---- 新建项目弹窗 ----
    def on_new_project(self):
        dlg = ProjectDialog(self, title="新建项目")
        if dlg.exec() == QDialog.Accepted and dlg.proj_data:
            proj = ikuai_projects._default_project()
            proj.update(dlg.proj_data)
            self._pdata["projects"].append(proj)
            ikuai_projects.save(self._pdata)
            self._add_session(proj, select=True)
            self._append_log("已新建项目「%s」" % proj["name"])

    def _edit_project_dialog(self, proj: dict):
        dlg = ProjectDialog(self, title="编辑项目", proj=proj)
        if dlg.exec() == QDialog.Accepted and dlg.proj_data:
            old_name = proj.get("name")
            proj.update(dlg.proj_data)
            ikuai_projects.save(self._pdata)
            if self.current and self.current.proj is proj:
                self._fill_form_from_proj(proj)
            self._render_proj_list()
            self._append_log("项目「%s」设置已更新" % proj["name"])

    def on_delete_project(self):
        if not self.current:
            QMessageBox.information(self, "提示", "没有可删除的项目")
            return
        s = self.current
        name = s.name
        btn = QMessageBox.question(
            self, "删除项目",
            "确定删除项目「%s」吗？\n\n若该项目已连接将同时断开；\n"
            "日志文件会保留在 logs 目录。" % name,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if btn != QMessageBox.Yes:
            return
        # 断开
        if s.service.connected or s.service.browser_started:
            try:
                s.service.stop()
            except Exception:
                pass
        if s.timer_refresh:
            s.timer_refresh.stop()
        if s.timer_monitor:
            s.timer_monitor.stop()
        # 移除
        self.sessions.pop(s.pid, None)
        self._pdata["projects"] = [p for p in self._pdata["projects"]
                                   if p["id"] != s.pid]
        if self._pdata.get("active") == s.pid:
            self._pdata["active"] = ""
        ikuai_projects.save(self._pdata)
        ikuai_projects.close_logger(name)
        # 切换到剩余项目
        self.current = None
        if self.sessions:
            self._select_session(next(iter(self.sessions)))
        else:
            self.model.set_aps([], {})
            self._sync_action_buttons()
            self._set_status("未连接", C_ERR)
            self.txt_log.clear()
        self._render_proj_list()

    def _svc_log(self, line: str):
        """service 在工作线程调用 -> 经信号投递到 UI 线程。"""
        if not self._closing:
            self.bridge.sig_log.emit(line)

    # ==============================================================
    # 配置
    # ==============================================================
    def _load_config_into_ui(self):
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
        """连接按钮：无项目时按表单自动建「快速连接」项目；有项目则同步表单后连接。"""
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
        if self.current is None:
            # 【免建项目直连】连接区输入地址即可连 —— 以地址为名自动
            # 创建项目（选不选项目都能连，也便于保存这次连接的配置）
            try:
                port = int(self.edit_port.text())
            except ValueError:
                QMessageBox.warning(self, "提示", "端口必须是数字")
                return
            remember = self.chk_remember.isChecked()
            proj = ikuai_projects._default_project(host)
            proj.update(host=host, port=port,
                        scheme=self.cmb_scheme.currentText(),
                        user=user,
                        password=password if remember else "",
                        remember=remember,
                        autologin=self.chk_autologin.isChecked(),
                        headless=self.chk_headless.isChecked())
            self._pdata["projects"].append(proj)
            self._add_session(proj, select=True)
            self._append_log("已创建快速连接项目「%s」" % host)
        # 表单 -> 项目（密码也要带上）
        self._sync_form_to_current_proj()
        self.current.proj["password"] = password
        self._connect_session(self.current)

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
        if not self.current:
            return
        if self.current.busy:
            return
        self._disconnect_session(self.current)

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
        if not self.current:
            return
        s = self.current
        if s.refresh_inflight:
            return
        if not s.service.connected:
            self._append_log("请先连接路由器")
            return
        s.refresh_inflight = True
        if not quiet:
            self._set_status("读取中...", C_WARN)

        def worker():
            b = s.bridge
            try:
                aps = s.service.fetch_ap_list()
                b.sig_aps.emit(aps)
            except NotConnectedError as ex:
                b.sig_log.emit("未连接：%s" % ex)
            except Exception as ex:
                b.sig_log.emit("刷新失败：%s" % ex)
            finally:
                b.sig_refresh_done.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _render_aps(self, aps: list):
        self.ap_list = list(aps)
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
    # 监视（转发到当前会话）
    # ==============================================================
    def _schedule_monitor(self):
        if self.current and self.current.monitor_targets:
            self._schedule_monitor_session(self.current)

    def _monitor_tick(self):
        if self.current:
            self._session_monitor_tick(self.current)

    def _check_monitor(self, aps: list[APInfo]):
        if self.current:
            self._check_monitor_session(self.current, aps)

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
    # 自动刷新开关（作用于当前会话）
    # ==============================================================
    def _on_autorefresh_toggle(self, on: bool):
        if not self.current:
            return
        self.current.auto_refresh = on
        if on:
            self._schedule_refresh_session(self.current)
            self._append_log("已开启自动刷新（%s）" % self.cmb_refresh.currentText())
        else:
            if self.current.timer_refresh:
                self.current.timer_refresh.stop()
            self._append_log("已停止自动刷新，列表已静止（可放心勾选）")

    def _on_refresh_change(self, _txt):
        if self.current and self.current.auto_refresh:
            if self.current.timer_refresh:
                self.current.timer_refresh.stop()
            self._schedule_refresh_session(self.current)

    def _schedule_refresh(self):
        if self.current:
            self._schedule_refresh_session(self.current)

    def _auto_refresh_tick(self):
        if self.current:
            self._session_refresh_tick(self.current)

    # ==============================================================
    # 无边框窗口：边缘拖拽缩放（Windows 原生 WM_NCHITTEST）
    # ==============================================================
    _RESIZE_MARGIN = 6          # 命中边缘的判定宽度（px）

    def nativeEvent(self, eventType, message):
        """把窗口边缘 6px 映射为系统的 resize 区：所有边+四角可拖缩放。

        QSizeGrip 方案的问题：放状态栏会与系统自带 grip 双显；放表格
        角部又随滚动条显隐时隐时现。原生命中测试不依赖任何可见控件，
        行为与普通窗口一致（光标也会变成 ↔↕↘ 形）。
        """
        if eventType != "windows_generic_MSG":
            return super().nativeEvent(eventType, message)
        try:
            import ctypes
            from ctypes import wintypes
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message != 0x0084:            # WM_NCHITTEST
                return super().nativeEvent(eventType, message)
            # 鼠标屏幕坐标
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            gx = self.mapToGlobal(QPoint(0, 0))
            w, h = self.width(), self.height()
            m = self._RESIZE_MARGIN
            left = x < gx.x() + m
            right = x >= gx.x() + w - m
            top = y < gx.y() + m
            bottom = y >= gx.y() + h - m
            HIT = {
                (True, False, False, False): 10,   # HTLEFT
                (False, True, False, False): 11,   # HTRIGHT
                (False, False, True, False): 12,   # HTTOP
                (False, False, False, True): 15,   # HTBOTTOM
                (True, False, True, False): 13,    # HTTOPLEFT
                (False, True, True, False): 14,    # HTTOPRIGHT
                (True, False, False, True): 16,    # HTBOTTOMLEFT
                (False, True, False, True): 17,    # HTBOTTOMRIGHT
            }
            hit = HIT.get((left, right, top, bottom))
            if hit:
                return True, hit
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    # ==============================================================
    # 关闭（断开全部会话）
    # ==============================================================
    def closeEvent(self, event):
        if self._closing:
            event.accept()
            return
        self._closing = True
        self._sync_form_to_current_proj()
        services = [s.service for s in self.sessions.values()]
        for s in self.sessions.values():
            if s.timer_refresh:
                s.timer_refresh.stop()
            if s.timer_monitor:
                s.timer_monitor.stop()
            try:
                ikuai_projects.close_logger(s.name)
            except Exception:
                pass

        def bg_stop_all():
            for svc in services:
                try:
                    svc.stop()
                except Exception:
                    pass

        threading.Thread(target=bg_stop_all, daemon=True).start()
        event.accept()


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


def _ensure_portable_browsers() -> bool:
    """首启自释放浏览器内核（自解压方案）。

    打包时把 ms-playwright.zip 拼在 exe 尾部（打包脚本完成）。
    本函数在主窗口前执行：
      - exe 旁 ms-playwright\\ 已有 chromium* -> 直接返回（秒开）
      - exe 尾部有捆绑数据 -> 弹进度窗释放到 exe 旁 -> 返回
      - 都没有（普通打包）-> 返回，走原有探测/提示逻辑
    返回 False 表示释放失败（缺空间/损坏），弹错误后仍继续启动
    （用户还能用方式 B 自装）。
    """
    import zipfile
    import struct

    exe = Path(sys.executable)
    if not getattr(sys, "frozen", False):
        return True                      # 源码运行不处理
    base = exe.parent
    msp = base / "ms-playwright"

    # 已就绪？（有 chromium* 子目录即认为可用）
    if msp.is_dir() and any(p.name.startswith("chromium")
                            for p in msp.iterdir() if p.is_dir()):
        return True

    # 读 exe 尾部魔数
    MARK = b"IKUAI_BROWSER_BUNDLE_V1"
    try:
        with open(exe, "rb") as f:
            f.seek(0, 2)
            total = f.tell()
            f.seek(total - len(MARK) - 12)
            tail = f.read(12 + len(MARK))
        if len(tail) < 12 + len(MARK) or tail[12:] != MARK:
            return True                  # 无捆绑 -> 走提示逻辑
        zsize, = struct.unpack("<Q", tail[:8])
        magic4 = tail[8:12]
        if magic4 != b"IKPL":
            return True
        offset = total - len(MARK) - 12 - zsize
        if offset <= 0:
            return True
    except OSError:
        return True

    # ---- 有捆绑：弹窗 + 进度条释放 ----
    app = QApplication.instance()
    owns_app = app is None
    if owns_app:
        app = QApplication(sys.argv)
        app.setStyle("Fusion")

    from PySide6.QtWidgets import QProgressDialog

    dlg = QProgressDialog(
        "首次启动：正在释放浏览器内核到程序目录…\n"
        "（约 275 MB，仅此一次，之后秒启动）",
        None, 0, 100)
    dlg.setWindowTitle("初始化")
    dlg.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint |
                       Qt.WindowTitleHint)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)

    ok_extract = True
    try:
        import io
        with open(exe, "rb") as f:
            f.seek(offset)
            zf = zipfile.ZipFile(io.BytesIO(f.read(zsize)))
            names = zf.namelist()
            total_bytes = sum(i.file_size for i in zf.infolist())
            done = 0
            for i in zf.infolist():
                if i.is_dir():
                    continue
                target = base / i.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(i) as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                        done += len(chunk)
                        dlg.setValue(int(done * 100 / max(total_bytes, 1)))
                        QApplication.processEvents()
        dlg.setValue(100)
        dlg.hide()
    except Exception as ex:
        ok_extract = False
        dlg.hide()
        if owns_app:
            pass
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(
            None, "释放失败",
            "浏览器内核释放失败：%s\n\n"
            "程序仍将启动。请手动把 ms-playwright 文件夹放到 exe 旁，"
            "或用 pip 安装 playwright。" % ex)

    # 释放成功后补设环境变量（本次进程立即生效）
    if ok_extract and any(p.name.startswith("chromium")
                          for p in msp.iterdir() if p.is_dir()):
        import os
        import ikuai_service
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(msp)

    # 【关键】销毁临时 QApplication —— shiboken 全局单例约束：
    # 不销毁的话 main() 里再建 QApplication 直接 RuntimeError 崩溃
    # （真机踩坑：libshiboken: Please destroy the QApplication
    #  singleton before creating a new QApplication instance）
    dlg.deleteLater()
    dlg = None
    if owns_app:
        app.quit()
        app.deleteLater()
        del app
        import shiboken6
        if shiboken6.isValid(QApplication.instance()):
            shiboken6.delete(QApplication.instance())
    return ok_extract


def main():
    _ensure_portable_browsers()
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
