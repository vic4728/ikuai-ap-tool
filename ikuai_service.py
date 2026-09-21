#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
爱快路由器 GUI —— 核心服务层

把 Playwright 的所有浏览器操作封装在后台线程里，
通过队列与 UI 通信，避免阻塞界面。

设计要点：
  - 浏览器只在 __init__ 时启动一次，复用整个生命周期
  - 所有公开方法都是线程安全的（内部加锁）
  - 通过 callback 回传进度，不直接操作 UI 控件
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# 运行日志与截图输出目录
# 输出目录（截图/CSV）：跟程序走。
# 打包成 exe 后 __file__ 在临时解压目录 _MEIPASS 里，输出会写丢；
# 用 sys.executable 定位 exe 所在目录（与 ikuai_config.app_dir 同策略）。
if getattr(sys, "frozen", False):
    OUTPUT_DIR = Path(sys.executable).resolve().parent / "output"
else:
    OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------
# Playwright 浏览器路径修正（exe 打包场景关键）
# ------------------------------------------------------------------
# 现象：exe 里 playwright 的 driver 被解压到 _MEIxxx\playwright\，
# 它按「驱动旁 local-browsers\」相对布局找浏览器 -> 报
#   Executable doesn't exist at _MEIxxx\playwright\driver\package\
#   local-browsers\chromium_headless_shell-1243\...
# 而浏览器实际装在 %LOCALAPPDATA%\ms-playwright（脚本版一直用这里）。
# 修正：环境变量 PLAYWRIGHT_BROWSERS_PATH 是 playwright 官方的
# 浏览器目录覆盖开关（优先级最高），启动前指到用户缓存目录即可。
def _fix_playwright_browsers_path() -> None:
    """浏览器目录探测顺序（命中即设 PLAYWRIGHT_BROWSERS_PATH）：

    1. 用户已设有效值（非空、非 "0"）-> 尊重，不动
       （"0" 是 playwright 的特殊值 = 包内 .local-browsers 布局，
        会让 exe 找错地方，视为未设）
    2. exe/脚本同目录的 ms-playwright\\     <- 便携部署（拷整个目录即用）
    3. %LOCALAPPDATA%\\ms-playwright          <- 官方安装位置
    4. %USERPROFILE%\\AppData\\Local\\ms-playwright  <- LOCALAPPDATA 缺失时兜底
    5. 都没有 -> 不设（playwright 官方提示安装）
    """
    import os
    cur = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if cur and cur != "0":
        return                      # 用户设了有效值，尊重
    base = Path(sys.executable if getattr(sys, "frozen", False)
                else __file__).resolve().parent
    candidates = [base / "ms-playwright"]
    lad = os.environ.get("LOCALAPPDATA")
    if lad:
        candidates.append(Path(lad) / "ms-playwright")
    up = os.environ.get("USERPROFILE")
    if up:
        candidates.append(Path(up) / "AppData" / "Local" / "ms-playwright")
    for c in candidates:
        # 有效判定：目录存在且里面有 chromium 系目录（防空目录误命中）
        if c.is_dir():
            has_chromium = any(p.name.startswith("chromium")
                               for p in c.iterdir() if p.is_dir())
            if has_chromium:
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(c)
                return


def browser_missing_hint(err_text: str) -> str | None:
    """连接报错里出现『浏览器可执行文件不存在』时，返回中文部署指引。

    供 GUI 在连接失败弹窗里附上（比 playwright 官方英文提示友好）。
    自动诊断常见摆放错误：
      - exe 旁有"便携浏览器包"之类的外层目录（拷贝时多带了一层）
      - ms-playwright 里直接是 chrome-headless-shell-win64（少了一层）
      - 拷的是 chromium_headless_shell-1243 但没放进 ms-playwright 里
    不是这类错误返回 None。
    """
    if ("Executable doesn't exist" not in (err_text or "")
            and "executable doesn't exist" not in (err_text or "").lower()):
        return None
    base = Path(sys.executable if getattr(sys, "frozen", False)
                else __file__).resolve().parent
    lines = [
        "未找到 Playwright 浏览器内核（首次使用需准备，二选一）：",
        "",
        "方式 A（便携，无需装 Python）：",
        "  在 exe 旁按此结构摆放（拷贝时注意层级！）：",
        "  %s" % (base / "ms-playwright" / "chromium_headless_shell-1243"),
        "  （从已可用机器拷贝 chromium_headless_shell-1243 文件夹，约 271 MB）",
        "",
        "方式 B（本机安装，需 Python）：",
        "  pip install playwright",
        "  playwright install chromium",
        "",
    ]
    # ---- 自动诊断摆放错误 ----
    diags = []
    msp = base / "ms-playwright"
    if not msp.is_dir():
        # exe 旁有没有疑似摆错的外层目录（含 chromium* 的目录）
        for p in base.iterdir():
            if p.is_dir() and any(c.name.startswith("chromium")
                                  for c in p.iterdir() if c.is_dir()):
                diags.append(
                    "检测到 %r 里就有浏览器 —— 它应该是 ms-playwright，"
                    "请把文件夹改名为 ms-playwright" % p.name)
                break
            if p.is_dir() and p.name != "output":
                inner = p / "chromium_headless_shell-1243"
                inner2 = (p / "ms-playwright" /
                          "chromium_headless_shell-1243")
                if inner2.is_dir():
                    diags.append(
                        "浏览器在 %r\\ms-playwright\\ 里 —— 多包了一层 %r，"
                        "请把里面的 ms-playwright 移到 exe 旁"
                        % (p.name, p.name))
                    break
                if inner.is_dir():
                    diags.append(
                        "chromium_headless_shell-1243 在 %r 里 —— "
                        "应放入 ms-playwright\\ 子目录" % p.name)
                    break
    else:
        subs = [c.name for c in msp.iterdir()]
        if subs and not any(s.startswith("chromium") for s in subs):
            diags.append("ms-playwright 里是 %r —— 缺少 "
                         "chromium_headless_shell-1243 这一层" % subs[:3])
        elif not subs:
            diags.append("ms-playwright 是空目录，浏览器没拷进来")
    if diags:
        lines.append("── 自动诊断 ──")
        lines.extend("  ⚠ " + d for d in diags)
        lines.append("")
    lines.append("当前搜索位置：")
    lines.append("  %s" % (base / "ms-playwright"))
    lines.append("  %LOCALAPPDATA%\\ms-playwright")
    return "\n".join(lines)


_fix_playwright_browsers_path()


# ------------------------------------------------------------------
# 固件版本差异（重要）
# ------------------------------------------------------------------
# 爱快后台存在两代前端，DOM 与接口都不一样，必须分别适配：
#
#   3.x（实测 3.7.26 x64 Build202609111742，机型 IK-M100）
#     前端：Vue 2 + webpack（manifest/vendor/app.js）
#     登录页：独立的 /login#/login 页面
#       - 用户名  input#usernameIpt
#       - 密码    input[type=password].password.inptText
#       - 按钮    button.btn.btn_green
#     登录接口：POST /Action/login
#       {"username","passwd"(md5明文),"pass"(base64("salt_11"+明文)),
#        "remember_password"}
#     成功返回：{"Result":10000,"ErrMsg":"Success"}
#     未登录：  {"Result":10014,"ErrMsg":"no login authentication"}
#     AP 页路由：#/ac/ap-config
#     AP 列表接口：func_name=ac_server, action=show,
#                  param {"TYPE":"total,data","limit":"0,20",...}
#     AP 表格：<table class="table table_MAX checkbox_checked">
#              行是 tr，单元格是 td（**不是** Ant Design 的 div）
#     超时/离线时 IP 显示 "- -"
#
#   4.x（实测 4.0.301 x64 ARM）
#     前端：Vue 3 + Vite SPA
#     AP 页路由：#/wirelessService/apManagement
#     AP 表格：Ant Design 虚拟滚动，div.ant-table-row / .ant-table-cell
#     页面内可重放 /Action/call 做轻量刷新
#
# 因此本模块用 VERSION_SCHEMA 把差异集中到一处，connect() 时探测并选定。
# ------------------------------------------------------------------

SCHEMA_3X = {
    "name": "3.x",
    "login_path": "/login#/login",
    "home_path": "/",
    "ap_route": "ac/ap-config",          # 命中 URL 里的片段
    "ap_url": "/#/ac/ap-config",
    "user_sel": "#usernameIpt",
    "user_fallback": 'input[type="text"]',
    "pwd_sel": 'input[type="password"]',
    "btn_sels": ['button.btn_green', 'button:has-text("登录")',
                 'button:has-text("登陆")', 'button[type="submit"]'],
    # 表格：tr/td，首行是表头
    "row_sel": "table.table tr",
    "cell_sel": "td",
    "head_sel": "table.table tr:first-child td, table.table tr:first-child th",
    "row_skip_head": 1,
    #
    # 3.x 实测的真实列布局（表头看似 8 列，实际数据行只有 9 个 td，
    # 且多个字段被塞进同一个 td，用换行分隔）：
    #
    #   td[0] = "08:9b:4b:4c:75:c4\n192.168.9.220"   MAC + IP
    #   td[1] = "正常\n3天7时7分"                     状态 + 在线时长
    #   td[2] = "AP-1"                                分组名称
    #   td[3] = "小花玩舍\nXiaoHuaWS"                 备注名 + SSID
    #   td[4] = "2.4G: 1(手动 1)\n5G：36(手动 36)"    信道
    #   td[5] = "IK-SW5"                              型号
    #   td[6] = ""                                    备注
    #   td[7] = "终端详情 ... 重启 ..."                操作
    #   td[8] = " "                                   占位
    #
    # 所以 3.x 不能按表头名取值（表头名与实际 td 不对应），
    # 必须按固定索引 + 单元格内拆分。下面的 cols 用 "idx" 表达固定列。
    "cols_idx": {
        "mac_ip": 0,
        "status_uptime": 1,
        "group": 2,
        "name_ssid": 3,
        "channel": 4,
        "model": 5,
        "comment": 6,
        "op": 7,
    },
    "cols": {
        "mac": ["MAC/IP", "MAC", "MAC地址"],
        "ip": ["MAC/IP", "IP", "IP地址"],
        "status": ["状态", "运行状态"],
        "group": ["分组名称", "分组"],
        "ssid": ["2.4G SSID", "SSID"],
        "channel": ["信道"],
        "model": ["型号"],
        "comment": ["备注"],
        "name": ["备注", "分组名称"],
    },
    # 3.x 前端是 **Element UI**（不是 4.x 的 Ant Design），
    # 确认弹窗结构完全不同（实测 xh.jcsit.cn / 3.7.26）：
    #
    #   <div class="el-message-box__wrapper" style="z-index:2001">
    #     <div class="el-message-box">
    #       <div class="el-message-box__header">提示</div>
    #       <div class="el-message-box__content">
    #         重启将导致连接的终端断开，确认继续？
    #       </div>
    #       <button class="el-button el-button--primary">确定</button>
    #       <button class="el-button">取消</button>
    #     </div>
    #   </div>
    #
    # 所以 4.x 的 .ant-modal / .ant-modal-footer 在这里**一个都匹配不到**，
    # 导致"未出现确认弹窗"直接 return False（历史 bug）。
    "modal_sels": [".el-message-box__wrapper", ".el-message-box",
                   ".el-dialog__wrapper", ".el-dialog", ".v-modal"],
    "confirm_sels": [".el-message-box__btns .el-button--primary",
                     ".el-message-box button.el-button--primary",
                     ".el-dialog__footer button.el-button--primary",
                     "button.el-button--primary"],
    # 点「重启」入口时，3.x 操作列里有 8 个同级 <a>，
    # 必须先精确匹配文本，避免点到「立即重启」以外的链接
    "restart_link_sels": ['a:has-text("重启")',
                          'a:text-is("重启")'],
    #
    # 「修改备注」（3.x 实测 xh.jcsit.cn / 3.7.26）：
    #   操作列有 <a>修改备注</a>；点开后的弹窗结构是"el 容器 + jqm 内容"混合体：
    #   容器  .el-dialog__wrapper (z=2001)
    #   输入  <input name="comment" class="inptText"
    #          placeholder="最多可填写64个字符">  位于 .jqmWindow_box 内
    #   确定  <input type="button" class="btn btn_green btn_confirm"
    #          value="确定">                       位于 .jqmWindow_foot 内
    #   取消  <input type="button" class="btn btn_cancel ... jqmClose"
    #          value="取消">
    #   注意：确认/取消是 <input> 不是 <button>！
    "remark_link_sels": ['a:text-is("修改备注")', 'a:has-text("修改备注")'],
    "remark_modal_sels": [".el-dialog__wrapper", ".jqmWindow_box"],
    "remark_input_sels": ['input[name="comment"]',
                          '.jqmWindow_box input.inptText',
                          '.el-dialog__wrapper input.inptText'],
    "remark_confirm_sels": ['.jqmWindow_foot input.btn_confirm',
                            'input.btn_green.btn_confirm',
                            '.el-dialog__wrapper input.btn_confirm'],
    "remark_cancel_sels": ['input.jqmClose', '.jqmWindow_foot input.btn_cancel'],
}


SCHEMA_4X = {
    "name": "4.x",
    "login_path": "/",
    "home_path": "/",
    "ap_route": "apManagement",
    "ap_url": "/login#/wirelessService/apManagement",
    "user_sel": 'input[type="text"]',
    "user_fallback": 'input[type="text"]',
    "pwd_sel": 'input[type="password"]',
    "btn_sels": ['button:has-text("登录")', 'button:has-text("登陆")',
                 'button[type="submit"]'],
    "row_sel": ".ant-table-row",
    "cell_sel": ".ant-table-cell",
    "head_sel": ".ant-table-thead th",
    "row_skip_head": 0,
    "cols": {
        "mac": ["MAC地址", "MAC"],
        "ip": ["IP地址", "IP"],
        "status": ["状态", "运行状态"],
        "group": ["分组"],
        "ssid": ["SSID"],
        "channel": ["信道"],
        "model": ["型号"],
        "comment": ["备注"],
        "name": ["名称"],
    },
    # 4.x 是 Ant Design，确认弹窗是 .ant-modal + .ant-modal-footer
    # （实测 192.168.50.1 / 4.0.301，见 output/restart_dialog_*.png）
    "modal_sels": [".ant-modal", ".ant-modal-confirm"],
    "confirm_sels": ['.ant-modal-footer button:has-text("重启")',
                     ".ant-modal-footer button.ant-btn-primary",
                     '.ant-modal-footer button:has-text("确定")'],
    "restart_link_sels": ['button:has-text("重启")'],
    # 「修改备注」（4.x 新固件实测 2026-09-22，192.168.50.1）：
    #   没有独立的"修改备注"入口！备注藏在「编辑」抽屉里：
    #     1. 行内 button:has-text("编辑") -> 打开 .ant-drawer（标题"编辑"）
    #     2. 抽屉内有 3 个 tab（2.4G / 5G / 其他设置），备注在「其他设置」
    #     3. 找 .ant-form-item label 含"备注"的那个 -> 里面的 input
    #        （placeholder="请输入AP备注"，无 name 无 maxlength）
    #     4. 保存 = 抽屉底部 button:has-text("保存")（ant-btn-primary）
    #   注意保存的是整个编辑表单（不止备注），所以填入时**绝不碰其他字段**。
    "remark_link_sels": ['button:has-text("编辑")'],
    "remark_modal_sels": [".ant-drawer:visible", ".ant-drawer",
                          ".ant-modal:visible", ".ant-modal"],
    # 备注输入框：由 set_ap_comment 动态定位（label 含"备注"的表单项），
    # 这里只放兜底选择器
    "remark_input_sels": ['.ant-form-item:has(.ant-form-item-label:has-text("备注")) input',
                          'input[placeholder="请输入AP备注"]',
                          'input[placeholder*="AP备注"]'],
    "remark_confirm_sels": ['.ant-drawer-footer button:has-text("保存")',
                            '.ant-drawer button.ant-btn-primary:has-text("保存")',
                            '.ant-modal-footer button.ant-btn-primary'],
    "remark_cancel_sels": ['.ant-drawer button:has-text("取消")',
                           '.ant-modal button:has-text("取消")'],
    # 4.x 编辑抽屉里备注所在的 tab 名（set_ap_comment 会先切到该 tab）
    "remark_tab_name": "其他设置",
    "remark_tab_sels": ['.ant-tabs-tab:has-text("其他设置")',
                        '.ant-tabs-tab:nth-child(3)'],
    # ★ 4.x 备注回写走 API 整传路线（不是 UI）：
    #   新固件把编辑抽屉整个表单 disabled（Vue 持续重渲染，JS 删属性
    #   会被立刻加回，fill/click/原生 setter 全部不可行），
    #   而 ac_server/edit 是全量更新接口 —— 把 show 返回的整条记录
    #   （实测 570 个字段）原样回传、只改 comment，即前端「保存」按钮
    #   的真实行为。真机验证：零副作用（其余 569 字段逐项对比无差异）。
    "remark_mode": "api_full_record",
}


# ------------------------------------------------------------------
# 数据结构
# ------------------------------------------------------------------
@dataclass
class APInfo:
    """一台 AP 的信息，字段对应 GUI 表格的列。"""
    group: str = ""      # 分组
    name: str = ""       # 名称
    model: str = ""      # 型号
    ip: str = ""         # 终端IP
    mac: str = ""        # 终端MAC
    uptime: str = ""     # 运行时间
    status: str = ""     # 运行状态
    comment: str = ""    # 备注（路由器系统内 AP 终端备注）

    # 内部用的额外字段（不在表格显示）
    row_index: int = -1
    version: str = ""    # 当前版本（内部用，不进表格）
    raw_cells: list = field(default_factory=list)

    def key(self) -> str:
        """唯一标识，优先用 MAC。"""
        norm = self.mac.lower().replace(":", "").replace("-", "").strip()
        return norm or self.name

    def is_alive(self) -> bool:
        """
        判断设备当前是否在线。

        爱快对离线/重启中的设备会把 IP、在线时长显示为 "- -"，
        IP 为空即认为不在线。
        """
        ip = (self.ip or "").strip()
        return bool(ip) and ip not in ("- -", "--", "-", "—", "－")


# ------------------------------------------------------------------
# 线程模型说明（重要）
# ------------------------------------------------------------------
# Playwright 的 sync API 对象**绑定在创建它的线程上**。
# 如果在线程 A 里 launch 浏览器，却在线程 B 里调用 page.xxx()，
# 就会抛：
#     cannot switch to a different thread (which happens to have exited)
#
# 因此本模块采用「单一线程 + 任务队列」模型：
#   - 有一个专属工作线程，浏览器在该线程创建，且**只在该线程使用**
#   - 外部（UI 线程、其他后台线程）调用公开方法时，把任务投入队列，
#     然后阻塞等待结果
#   - 队列由工作线程串行消费，天然保证不会并发操作浏览器
#
# 这样无论调用方是谁、在哪个线程，Playwright 调用始终发生在同一线程内。
# ------------------------------------------------------------------


# 任务队列里的一项
class _Task:
    __slots__ = ("fn", "done", "result", "error")

    def __init__(self, fn: Callable):
        self.fn = fn
        self.done = threading.Event()
        self.result = None
        self.error: Optional[BaseException] = None


class NotConnectedError(RuntimeError):
    """未连接路由器时调用操作抛出的异常。"""


# ------------------------------------------------------------------
# 路由器服务
# ------------------------------------------------------------------
class IKuaiService:
    """
    封装对爱快 Web 后台的所有操作。

    线程模型：单一线程 + 任务队列（见文件顶部说明）。
    浏览器只在内部工作线程创建和使用，外部调用一律经过队列投递。
    因此本类的公开方法**可以从任意线程安全调用**。
    """

    def __init__(self, log_cb: Optional[Callable[[str], None]] = None,
                 headless: bool = True):
        """
        log_cb   : 日志回调，实现方需自行切回 UI 线程
        headless : 是否无头模式。默认 True —— 后台静默运行，不弹浏览器窗口。
        """
        self._lock = threading.RLock()
        self._log_cb = log_cb
        self._headless = headless

        # 浏览器对象（只在工作线程内访问）
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

        # 连接状态（跨线程读取，加锁保护）
        self._connected = False
        self._base_url = ""
        self._host = ""
        self._port = 0
        self._scheme = "http"
        self._username = ""
        self._password = ""
        # AP 管理页是否已加载过。为 True 时刷新走轻量路径（点页面内刷新按钮），
        # 不重新 goto —— 避免 SPA 重建导致的"断开重连"抖动。
        self._ap_page_ready = False

        # 固件版本适配：connect() 时探测并选定 SCHEMA_3X / SCHEMA_4X。
        # 默认按 4.x 走，探测到 3.x 会自动切换。
        self._schema = dict(SCHEMA_4X)
        self._firmware = ""        # 例 "3.7.26" / "4.0.301"
        self._device_model = ""    # 例 "IK-M100"
        self._device_name = ""     # 例 "XHWS-AC"（hostname）

        # 任务队列与工作线程
        self._queue: "queue.Queue[Optional[_Task]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._worker_id: Optional[int] = None   # 当前工作线程的 ident
        self._closed = False
        self._ensure_worker()

    # ---------------- 工作线程 ----------------
    def _on_worker_thread(self) -> bool:
        """当前是否就在浏览器工作线程上执行。"""
        wid = self._worker_id
        return wid is not None and threading.get_ident() == wid

    def _ensure_worker(self) -> None:
        """惰性启动工作线程（只启动一次）。"""
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            if self._closed:
                return
            self._worker = threading.Thread(
                target=self._worker_loop, name="ikuai-browser", daemon=True)
            self._worker.start()
            self._worker_id = self._worker.ident

    def _worker_loop(self) -> None:
        """
        工作线程主循环：串行消费任务队列。

        Playwright 的所有调用都发生在这个线程内，这从根本上
        消除了 "cannot switch to a different thread" 错误。
        """
        while True:
            task = self._queue.get()
            if task is None:            # 停止信号
                break
            try:
                task.result = task.fn()
            except BaseException as ex:  # 捕获全部，原样回传给调用方
                task.error = ex
            finally:
                task.done.set()

    def _submit(self, fn: Callable, timeout: float = 180.0):
        """
        把任务投递到工作线程并等待结果。

        若在工作线程内直接调用（重入），则同步执行，避免死锁。
        """
        if self._on_worker_thread():
            return fn()

        self._ensure_worker()
        task = _Task(fn)
        self._queue.put(task)
        if not task.done.wait(timeout):
            raise RuntimeError("操作超时（%d 秒）" % int(timeout))
        if task.error is not None:
            raise task.error
        return task.result

    # ---------------- 日志 ----------------
    def _log(self, msg: str) -> None:
        line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
        if self._log_cb:
            try:
                self._log_cb(line)
            except Exception:
                pass

    # ---------------- 生命周期 ----------------
    @property
    def headless(self) -> bool:
        return self._headless

    @property
    def browser_started(self) -> bool:
        """浏览器是否已启动（跨线程安全查询）。"""
        with self._lock:
            return self._browser is not None

    def set_headless(self, value: bool) -> None:
        """
        设置是否无头模式。

        浏览器已经启动时不允许切换 —— Chromium 不支持运行中切换显示状态。
        """
        with self._lock:
            if self._browser is not None:
                raise RuntimeError("浏览器已启动，无法切换显示模式，请先断开连接")
            self._headless = bool(value)

    def start(self) -> None:
        """启动浏览器（只需一次）。"""

        def _do():
            if self._browser is not None:
                return
            mode = "无头（不显示窗口）" if self._headless else "可见（显示窗口）"
            self._log("启动浏览器内核（%s）..." % mode)
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=self._headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            self._context = self._browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1920, "height": 1080},
            )
            self._page = self._context.new_page()
            self._page.set_default_timeout(25000)
            self._log("浏览器就绪")

        self._submit(_do, timeout=120)

    def stop(self) -> None:
        """关闭浏览器，释放资源。"""

        def _do():
            try:
                if self._browser:
                    self._browser.close()
            except Exception:
                pass
            try:
                if self._pw:
                    self._pw.stop()
            except Exception:
                pass
            self._browser = None
            self._context = None
            self._page = None
            self._pw = None
            with self._lock:
                self._connected = False
                self._ap_page_ready = False
            self._log("浏览器已关闭")

        # 关闭时不要久等：可能有一个 fetch 排在前面（它自己最长 180 秒）。
        # 超时了也继续往下走 —— 进程即将退出，Chromium 会随之结束。
        try:
            self._submit(_do, timeout=15)
        except Exception:
            pass

        # 停掉工作线程
        with self._lock:
            self._closed = True
            w = self._worker
            self._worker = None
            self._worker_id = None
        if w is not None and w.is_alive():
            self._queue.put(None)
            w.join(timeout=3)

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    # ---------------- 连接 ----------------
    def connect(self, host: str, port: int, use_https: bool,
                username: str, password: str) -> bool:
        """
        连接并登录路由器。

        返回 True 表示登录成功。
        """

        def _do():
            scheme = "https" if use_https else "http"
            # 用户可能填了带协议的地址，清理一下
            h = host.strip()
            for prefix in ("http://", "https://"):
                if h.startswith(prefix):
                    h = h[len(prefix):]
            h = h.rstrip("/")

            # 【IPv6 支持】形如 [240e:350:...]:80 或 240e:350:... 的地址
            # 方括号形式：直接取括号内
            if h.startswith("["):
                end = h.find("]")
                if end > 0:
                    h = h[1:end]
            elif h.count(":") >= 2:
                # 裸 IPv6（多个冒号），整串都是地址，不能按冒号切端口
                pass
            elif ":" in h:
                # "主机:端口" 形式，切掉端口
                h = h.split(":")[0]

            h = h.strip()

            # 【IPv6 支持】URL 里 IPv6 必须加方括号
            host_in_url = "[%s]" % h if ":" in h else h
            self._base_url = "%s://%s:%d" % (scheme, host_in_url, port)
            self._host = h
            self._port = port
            self._scheme = scheme
            self._username = username
            self._password = password
            # 新会话，AP 页尚未加载
            with self._lock:
                self._ap_page_ready = False

            self._log("连接 %s" % self._base_url)

            if self._page is None:
                raise RuntimeError("浏览器未启动")

            try:
                self._page.goto(self._base_url + "/",
                                wait_until="domcontentloaded", timeout=25000)
                self._page.wait_for_timeout(5000)
            except PWTimeout:
                raise RuntimeError("连接超时，请检查 IP、端口和网络连通性")
            except Exception as ex:
                raise RuntimeError("无法访问 %s：%s" % (self._base_url, ex))

            # ---- 探测固件版本，选定适配 schema ----
            self._detect_schema()
            self._log("识别到爱快 %s 后台（%s）" % (
                self._schema["name"],
                (self._device_model + " " + self._firmware).strip() or "未知型号"))

            # 是否需要登录：用「登录页特征」判断，而不是"有没有 password 框"。
            # 原因：4.x/3.x 后台内部也有 password 输入框（改密码等），
            # 用数量判断会在登录成功后误判为"仍在登录页"。
            if not self._on_login_page():
                with self._lock:
                    self._connected = True
                self._log("已处于登录状态")
                self._enter_ap_page_first_time()
                return True

            self._log("填入登录凭据...")
            try:
                user_input = self._page.locator(self._schema["user_sel"])
                if user_input.count() == 0:
                    user_input = self._page.locator(self._schema["user_fallback"])
                user_input.first.fill(username)
                self._page.locator(self._schema["pwd_sel"]).first.fill(password)
                self._page.wait_for_timeout(400)
            except Exception as ex:
                raise RuntimeError("填写登录表单失败：%s" % ex)

            # 点登录：优先用 JS click 绕过可能的遮挡层
            clicked = False
            for sel in self._schema["btn_sels"]:
                b = self._page.locator(sel)
                if b.count() > 0:
                    try:
                        b.first.evaluate("el => el.click()")
                    except Exception:
                        b.first.click()
                    clicked = True
                    break
            if not clicked:
                raise RuntimeError("未找到登录按钮")

            # 等登录落地：轮询 URL 是否离开登录页，最多等 20 秒
            ok = False
            deadline = time.time() + 20
            while time.time() < deadline:
                self._page.wait_for_timeout(500)
                if not self._on_login_page():
                    ok = True
                    break
            self._page.wait_for_timeout(1500)

            # 校验结果
            try:
                body = self._page.inner_text("body")[:3000]
            except Exception:
                body = ""
            for bad in ("密码错误", "用户名或密码错误", "登录失败"):
                if bad in body:
                    raise RuntimeError("登录失败：账号或密码错误")

            if not ok:
                cur = ""
                try:
                    cur = self._page.url
                except Exception:
                    pass
                raise RuntimeError("登录后仍停留在登录页（当前 %s），"
                                   "请检查账号密码" % cur)

            with self._lock:
                self._connected = True
            self._log("登录成功")
            # 登录后刷新一次设备信息（此时才拿得到型号/固件）
            self._detect_schema(quiet=True)
            # 顺手进一次 AP 页并标记就绪，省掉首次刷新的整页加载等待。
            # 进不去也无所谓 —— fetch_ap_list 会自己重试。
            self._enter_ap_page_first_time()
            return True

        return self._submit(_do, timeout=180)

    # ---------------- 版本探测与登录页判定 ----------------
    def _on_login_page(self) -> bool:
        """
        判断当前是否仍在登录页。

        **不能用 input[type=password] 的数量判断** —— 爱快后台登录成功后，
        页面内部仍保留若干 password 输入框（改密码、WiFi 密码等），
        实测 4.x/3.x 都会让"数量>0"恒成立，从而误报"仍停留在登录页"。

        改用两条更可靠的信号：
          1. URL 里含 /login 或路由为 #/login
          2. 页面上能同时看到用户名框和明显的"记住密码"字样
        """
        if self._page is None:
            return False
        try:
            url = (self._page.url or "").lower()
        except Exception:
            url = ""
        if "/login" in url:
            return True
        try:
            # 登录页特征：用户名框 id（3.x）或"记住密码"文案
            if self._page.locator("#usernameIpt").count() > 0:
                return True
            body = self._page.inner_text("body")[:500]
            if "记住密码" in body and "登录" in body:
                # 再确认没有后台菜单特征
                for menu in ("系统概况", "状态监控", "AC管理", "系统设置"):
                    if menu in body:
                        return False
                return True
        except Exception:
            pass
        return False

    def _detect_schema(self, quiet: bool = False) -> None:
        """
        探测固件版本，选定 SCHEMA_3X / SCHEMA_4X。

        判据（按可靠性排序）：
          1. URL 里是否出现 ac/ap-config -> 3.x
          2. 页面上是否存在 <table class="table ..."> 且行数>0 -> 3.x
          3. 页面上是否存在 .ant-table-row -> 4.x
          4. 通过 /Action/call 询问 sysstat 拿 verstring
          5. 静态资源指纹（新固件 sysstat 接口不可用时兜底）：
             - /static/js/polyfills-<hash>.js（Vite）-> 4.x 新固件
             - manifest.<hash>.js（webpack）-> 3.x

        【真机踩坑 2026-09-22】192.168.50.1 固件升级后（Vite 资源 +
        posthog），sysstat 的 TYPE 全部返回 {"code":2007,"unknown TYPE"}，
        版本接口失效；而登录后 SPA 会把 URL 改写成 #/ac/ap-config
        之类的死路由（404 页），URL 判据也会被污染 —— 结果 4.x 被误判
        成 3.x，跳去 404 页读到 0 台 AP。
        资源指纹来自 <script src>，任何路由下都稳定存在。
        """
        page = self._page
        if page is None:
            return

        chosen = None
        try:
            url = (page.url or "").lower()
        except Exception:
            url = ""

        # 0) 资源指纹（最稳：不受路由/接口变化影响）
        try:
            assets = page.evaluate(
                """() => [...document.querySelectorAll('script[src],link[href]')]
                       .map(e => e.src || e.href).join('\\n')""") or ""
        except Exception:
            assets = ""
        if "polyfills-" in assets or "assets/index-" in assets:
            chosen = SCHEMA_4X          # Vite 构建 = 4.x 新固件
        elif "manifest." in assets and "vendor." in assets:
            chosen = SCHEMA_3X           # webpack 经典组合 = 3.x

        # 1) URL 判据（次之：可能被 SPA 死路由污染，仅在指纹缺席时用）
        if chosen is None:
            if "ac/ap-config" in url or "/ac/" in url:
                chosen = SCHEMA_3X
            elif "apmanagement" in url:
                chosen = SCHEMA_4X

        # 2/3) DOM 判据
        if chosen is None:
            try:
                if page.locator(".ant-table-row").count() > 0:
                    chosen = SCHEMA_4X
                elif page.locator("table.table").count() > 0:
                    chosen = SCHEMA_3X
            except Exception:
                pass

        # 4) 问后端要版本号
        ver = ""
        try:
            ver = self._probe_firmware_version() or ""
        except Exception:
            pass
        if ver:
            self._firmware = ver
            if chosen is None:
                chosen = SCHEMA_3X if ver.startswith("3.") else SCHEMA_4X
        else:
            # 拿不到版本就按已有判据推断，全无线索时保守用 3.x 的表格选择器
            # （tr/td 更通用，Ant 的 div 表格另有 .ant-table-row 兜底）
            if chosen is None:
                chosen = SCHEMA_3X

        self._schema = dict(chosen)
        if not quiet:
            self._log("适配模式：%s（固件 %s）" % (self._schema["name"],
                                                  self._firmware or "未知"))

    def _probe_firmware_version(self) -> str:
        """
        通过 /Action/call 查询 sysstat，拿固件版本字符串。

        3.x 与 4.x 的这个接口一致，返回形如：
          {"Result":30000,...,"Data":{"sysstat":{...,"verinfo":
              {"modelname":"IK-M100","verstring":"3.7.26 x64 Build...",
               "version":"3.7.26"}}}}
        """
        page = self._page
        if page is None:
            return ""
        try:
            res = page.evaluate("""async () => {
                const body = {func_name:'sysstat', action:'show',
                              param:{TYPE:'verinfo'}};
                try {
                    const r = await fetch('/Action/call', {
                        method:'POST',
                        headers:{'Content-Type':'application/json'},
                        body: JSON.stringify(body),
                        credentials:'include',
                    });
                    return await r.text();
                } catch(e) { return ''; }
            }""")
        except Exception:
            return ""
        if not res:
            return ""
        try:
            import json as _json
            data = _json.loads(res)
            d = (data.get("Data") or {})
            vi = d.get("verinfo") or {}
            if not vi:
                # 有些版本把 verinfo 直接放在 Data 下
                vi = d
            self._device_model = vi.get("modelname", "") or self._device_model
            v = vi.get("version") or ""
            vs = vi.get("verstring") or ""
            # 版本号优先取 version 字段，退回从 verstring 里抠
            if not v and vs:
                import re as _re
                m = _re.match(r"([0-9]+\.[0-9]+\.[0-9]+)", vs)
                if m:
                    v = m.group(1)
            if vs:
                self._firmware = v or self._firmware
            return v
        except Exception:
            return ""

    def _enter_ap_page_first_time(self) -> None:
        """
        已处于登录状态时（上次会话还在），直接进 AP 页并标记就绪。

        不抛异常 —— 进不去也没关系，后面 fetch_ap_list 会自己 goto。
        """
        try:
            sch = self._schema
            self._page.goto(self._base_url + sch["ap_url"],
                            wait_until="domcontentloaded", timeout=20000)
            self._wait_ap_table(12000)
            self._ap_page_ready = True
        except Exception:
            self._ap_page_ready = False


    def disconnect(self) -> None:
        """断开连接（仅标记状态，不关浏览器）。"""
        with self._lock:
            self._connected = False
        self._log("已断开连接")

    # ---------------- 获取 AP 列表 ----------------
    def fetch_ap_list(self, force_reload: bool = False) -> list[APInfo]:
        """
        读取 AP 列表。

        性能与稳定性要点（重要）：
          首次调用用 goto 打开 AP 管理页；**之后每次刷新只点 SPA 页面里的
          「刷新」按钮**，不再 goto。

        为什么不能每次都 goto：
          goto 会让整个 SPA 重新加载，登录态、菜单、路由全部重建，
          然后在 1~10 秒的刷新间隔里反复发生。表现为列表频繁闪成
          "断开/空"，甚至被踢回登录页 —— 用户看到的"断开重连"就是这个。
          点页面内刷新按钮只重新拉一次数据，DOM 不重建，快且稳。

        注意：不同固件的 AP 表格实现不同（见文件顶部 VERSION_SCHEMA 说明）：
          - 4.x 用 Ant Design 虚拟滚动表格，行是 div.ant-table-row
          - 3.x 用普通 <table class="table">，行是 tr、单元格是 td
        """

        def _do():
            if not self.connected:
                raise NotConnectedError("尚未连接路由器")
            if self._page is None:
                raise NotConnectedError("浏览器未启动")

            sch = self._schema
            url = self._base_url + sch["ap_url"]

            need_goto = force_reload or not self._ap_page_ready
            if not need_goto:
                # 二次确认：可能用户手动导航走了，或页面被刷新过
                try:
                    cur = (self._page.url or "")
                except Exception:
                    cur = ""
                if sch["ap_route"] not in cur:
                    need_goto = True

            if need_goto:
                self._page.goto(url, wait_until="domcontentloaded")
                self._ap_page_ready = True
                # 等表格渲染出数据行（最多等 15 秒）
                self._wait_ap_table(15000)
            else:
                # 走轻量路径：在页面内重新拉取 AP 数据，不重载整个 SPA。
                # 这一步是解决"刷新频繁断开重连"的关键 —— goto 会重建
                # 整个 SPA（登录态/菜单/路由全重来），1~10 秒一次的刷新
                # 反复触发就会看到列表闪断甚至被踢回登录页。
                reloaded = self._reload_ap_data()
                if not reloaded:
                    # 重放请求失败（会话过期之类），退一步点页面内图标
                    reloaded = self._click_refresh_button()
                if not reloaded:
                    # 都失败才退回整页加载，保证功能可用
                    self._log("轻量刷新失败，回退为整页加载")
                    self._page.goto(url, wait_until="domcontentloaded")
                    self._wait_ap_table(15000)
                else:
                    # 数据是异步回来的，等表格稳定
                    self._wait_ap_table(8000)

            return self._read_ap_rows()

        return self._submit(_do, timeout=180)

    # 轻量刷新的实现说明（实测结论，iKuai OS V4.0.301 / 真机已连线验证）
    #
    # 这个页面**没有文字为「刷新」的按钮**，工具条里的图标按钮也不能点：
    #   _headerRight_ 容器内 4 个按钮，其中 3 个被 `_formItemContainer_`
    #   浮层拦截了指针事件 —— 用 Playwright 的 click() 会让它重试 25 秒后
    #   超时（这正是"刷新卡住 50 秒"的来源）。
    #
    # 实测真正会重新拉取 AP 列表的，是搜索框后缀里的那个「清空」图标：
    #   点它发出的请求是
    #     {"func_name":"ac_server","action":"show",
    #      "param":{"TYPE":"total,data,ac_summary","limit":"0,500",...}}
    #   即完整重新查询 AP 列表（含总数/数据/AC 汇总）。
    #
    # 但依赖图标位置很脆弱（DOM 一变就失效）。所以这里采取
    # **更稳的等价做法：直接重放 SPA 自己的数据请求**（见下面
    # _reload_ap_data_by_fetch）。点图标只作为兜底。

    # 兜底用的图标按钮选择器（按可靠性排序）
    _REFRESH_SELECTORS_4X = (
        # 搜索框清空键：实测点它会触发完整的 ac_server/show 查询
        '.ant-input-suffix button',
        '.ant-input-group-addon button',
        '.anticon-reload',
        '.anticon-sync',
        '.ant-btn:has-text("刷新")',
        'button:has-text("刷新")',
    )

    # 3.x 的刷新入口：工具条上的刷新图标 / 「刷新」按钮
    _REFRESH_SELECTORS_3X = (
        'button:has-text("刷新")',
        'a:has-text("刷新")',
        '.refresh',
        '[class*="refresh"]',
        '.icon-refresh',
        '.fa-refresh',
        '[title="刷新"]',
        '[title*="刷新"]',
    )

    def _click_refresh_button(self) -> bool:
        """
        兜底方案：点页面里某个会触发列表重查的图标按钮。

        成功返回 True。点击统一走 JS `el.click()`，绕过浮层对指针事件的
        拦截 —— 否则 Playwright 会重试 25 秒才超时，表现为"刷新卡死"。
        """
        selectors = (self._REFRESH_SELECTORS_3X
                     if self._schema["name"] == "3.x"
                     else self._REFRESH_SELECTORS_4X)
        for sel in selectors:
            try:
                loc = self._page.locator(sel)
                n = loc.count()
                if n == 0:
                    continue
                el = loc.first
                if not el.is_visible():
                    continue
                # 用 JS 点击，避开 pointer-events 拦截
                el.evaluate("el => el.click()")
                return True
            except Exception:
                continue
        return False

    def _wait_ap_table(self, timeout_ms: int = 15000) -> bool:
        """
        等待 AP 表格真正渲染出数据行。

        **不能只用 wait_for_selector(row_sel)** —— 3.x 的表头本身就是一个
        tr，`table.table tr` 在数据还没回来时就已匹配到 1 个元素（表头），
        wait_for_selector 会立刻返回，随后读到 0 台 AP。
        实测踩过这个坑。

        这里改为轮询：等到行数 > 表头行数，或超时。
        返回 True 表示等到了数据行。
        """
        sch = self._schema
        need = sch.get("row_skip_head", 0) + 1
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            try:
                if self._page.locator(sch["row_sel"]).count() >= need:
                    return True
            except Exception:
                pass
            self._page.wait_for_timeout(300)
        return False

    def _reload_ap_data(self) -> bool:
        """
        轻量刷新：在页面内重新发起 AP 列表查询，不重载整个 SPA。

        iKuai 的 SPA 是通过 `/Action/call` 拉数据的。直接重放同一个请求
        最稳、最快，也完全不依赖 DOM 结构 —— 不怕它改版。

        注意：**请求本身能成功，不代表表格会更新**。
        表格是前端自己的数据在渲染，重放 fetch 只是让后台重新查一遍，
        前端并不知情。因此这里分两步：
          1. 重放请求（保证后台数据是最新的，且顺带验证会话有效）
          2. 触发前端自己重新渲染（点页面内的刷新/重查入口）

        成功返回 True。失败则调用方退回整页 goto。
        """
        sch = self._schema

        payload = {
            "func_name": "ac_server",
            "action": "show",
            "param": {"TYPE": "total,data", "limit": "0,500"},
        }

        # 1) 重放请求：既刷新后台缓存，也用来探测会话是否还有效
        ok = self._page.evaluate(
            """async (args) => {
                const body = args.body;
                try {
                    const r = await fetch('/Action/call', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(body),
                        credentials: 'include',
                    });
                    if (!r.ok) return false;
                    const t = await r.text();
                    // 4.x/3.x 未登录都会返回 Result:10014
                    return t.indexOf('10014') < 0;
                } catch (e) {
                    return false;
                }
            }""",
            {"body": payload},
        )

        if not ok:
            return False

        # 2) 让前端重新渲染表格
        if sch["name"] == "3.x":
            # 3.x 是 Vue 2 SPA：切走再切回当前路由即可让它重新拉数据
            try:
                cur = self._page.url or ""
                route = sch["ap_route"]
                if route in cur:
                    base = cur.split("#")[0]
                    # 先跳到另一个已存在的路由，再跳回来
                    self._page.evaluate(
                        "() => { window.location.hash = '#/system-overview'; }")
                    self._page.wait_for_timeout(600)
                    self._page.goto(self._base_url + sch["ap_url"],
                                    wait_until="domcontentloaded")
                    self._wait_ap_table(10000)
                    return True
            except Exception:
                pass

        # 4.x（或 3.x 路由切换失败）：点页面内触发重查的控件
        reloaded = self._click_refresh_button()
        if not reloaded:
            # 直接重新进入 AP 页（SPA 内部跳转，不重载浏览器）
            try:
                self._page.goto(self._base_url + sch["ap_url"],
                                wait_until="domcontentloaded")
                self._wait_ap_table(10000)
                return True
            except Exception:
                return False

        # 给 SPA 一点时间把数据渲染进表格
        self._wait_ap_table(5000)
        return True

    def _read_ap_rows(self, write_csv: bool = False) -> list[APInfo]:
        """
        把当前页面上的 AP 表格读成 APInfo 列表。

        同时支持两种表格实现（由 self._schema 决定）：
          - 3.x：<table class="table"> + tr/td，首行是表头
          - 4.x：div.ant-table-row + .ant-table-cell

        3.x 的坑：MAC 和 IP 挤在同一列（表头"MAC/IP"，单元格形如
          "08:9b:4b:4c:75:c4\n192.168.9.220"），需要在读完后拆开。

        write_csv=True 时，顺便把解析结果落盘成 CSV（带时间戳文件名），
        便于事后核对"页面上的原始文本"和"程序解析出的字段"是否一致。
        """
        sch = self._schema
        rows = self._page.locator(sch["row_sel"])
        total = rows.count()
        col_map = self._read_header_map()

        skip = sch.get("row_skip_head", 0)
        aps: list[APInfo] = []
        idx_out = 0
        for i in range(total):
            if i < skip:
                continue
            row = rows.nth(i)
            try:
                cells = row.locator(sch["cell_sel"])
                n = cells.count()
                if n < 3:
                    continue
                texts = []
                for j in range(n):
                    try:
                        texts.append(cells.nth(j).inner_text().strip())
                    except Exception:
                        texts.append("")
            except Exception:
                continue

            ap = APInfo(row_index=idx_out, raw_cells=texts)

            if sch["name"] == "3.x":
                # 3.x：按固定索引取（表头名与实际 td 不对应，不能用表头映射）
                ci = sch["cols_idx"]

                def cell(key, default=""):
                    j = ci.get(key, -1)
                    if 0 <= j < len(texts):
                        return texts[j]
                    return default

                # td[0] = "MAC\nIP"
                ap.mac, ap.ip = self._split_mac_ip(cell("mac_ip"))
                # td[1] = "状态\n在线时长"
                st, up = self._split_status_uptime(cell("status_uptime"))
                ap.status = st
                ap.uptime = up
                # td[2] = 分组名称
                ap.group = cell("group")
                # td[3] = "备注名\nSSID"
                nm, ssid = self._split_name_ssid(cell("name_ssid"))
                ap.name = nm or ssid
                ap.version = ""
                # td[5] = 型号
                ap.model = cell("model").strip()
                if not ap.model:
                    ap.model = self._resolve_model(texts, col_map)
                # td[6] = 备注（实测表头就叫"备注"；3.x 有专门的备注列）
                ap.comment = cell("comment").strip()
            else:
                # 4.x：按表头名取值，取不到再退回固定索引
                cols = sch["cols"]
                ap.group = self._pick(texts, col_map, cols["group"], 4)
                ap.name = self._pick(texts, col_map, cols["name"], 1)
                ap.ip = self._pick(texts, col_map, cols["ip"], 3)
                ap.mac = self._pick(texts, col_map, cols["mac"], 2)
                ap.uptime = self._pick(texts, col_map,
                                       ["在线时长", "运行时间"], 5)
                ap.status = self._pick(texts, col_map, cols["status"], 6)
                ap.version = self._pick(texts, col_map,
                                        ["当前版本", "版本", "固件版本"], -1)
                ap.model = self._resolve_model(texts, col_map)
                # 备注列：4.x 表头名"备注"（cols.comment），映射不到留空
                ap.comment = self._pick(texts, col_map, cols["comment"], -1).strip()

            # 离线/重启中：爱快把 IP 显示为 "- -"，统一显示"断开"
            if not ap.is_alive():
                ap.status = "断开"
            elif ap.status in ("- -", "", "正常\n"):
                ap.status = "已连接"

            aps.append(ap)
            idx_out += 1

        self._log("读取到 %d 台 AP" % len(aps))

        if write_csv:
            try:
                path = self._dump_ap_csv(aps)
                self._log("已导出 CSV：%s" % path)
            except Exception as ex:
                self._log("导出 CSV 失败：%s" % ex)

        return aps

    def _dump_ap_csv(self, aps: list[APInfo]) -> Path:
        """
        把本次解析结果导成 CSV，方便逐字段核对。

        【为什么不能直接 join(raw_cells)】
        3.x/4.x 的"备注"列里含换行（比如运维备注是多行文本），
        直接拼会串行。所以每个单元格先把内部换行折叠成空格再写。
        """
        import csv as _csv

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path(OUTPUT_DIR) / ("ap_%s_%s.csv" % (self._schema["name"], ts))
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", newline="", encoding="utf-8-sig") as f:
            w = _csv.writer(f)
            w.writerow(["分组", "名称", "型号", "终端IP", "终端MAC",
                        "运行时间", "运行状态", "原始行(折叠)"])
            for ap in aps:
                raw = " | ".join(
                    " ".join(str(c).split()) for c in (ap.raw_cells or []))
                w.writerow([ap.group, ap.name, ap.model, ap.ip, ap.mac,
                            ap.uptime, ap.status, raw])
        return path

    @staticmethod
    def _split_mac_ip(raw: str) -> tuple[str, str]:
        """
        拆开 3.x 里合并的 "MAC/IP" 单元格。

        实测形如：
          "08:9b:4b:4c:75:c4\n192.168.9.220"
          "08:9b:4b:4c:75:c4 192.168.9.220"
        也可能只有 MAC（离线时没有 IP）。
        """
        import re as _re
        if not raw:
            return "", ""
        s = raw.replace("\r", " ").replace("\n", " ").strip()
        # 抓 MAC
        mac_re = r"[0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5}"
        m = _re.search(r"(%s)" % mac_re, s)
        mac = m.group(1) if m else ""

        # 抓 IP：先把 MAC 从串里剔除，避免"IP 的正则"误吞 MAC 片段。
        # 【实测坑】MAC 形如 08:9b:4b:4c:75:c4，本身就满足"十六进制+冒号"
        # 的结构，IPv6 的正则会连它一起匹配 —— 结果离线 AP 的 IP 会
        # 变成它自己的 MAC。必须先摘掉 MAC 再找 IP。
        rest = _re.sub(mac_re, " ", s)

        ip = ""
        m4 = _re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", rest)
        if m4:
            ip = m4.group(1)
        else:
            # IPv6：至少两段冒号，且不能是刚才那个 MAC
            m6 = _re.search(r"([0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{0,4}){2,7})",
                            rest)
            if m6:
                cand = m6.group(1)
                if cand.count(":") >= 2 and cand.strip(": "):
                    ip = cand
        # 爱快的"- -"/"--"表示离线，归一化为空
        if ip.strip() in ("- -", "--", "-", "—", "－"):
            ip = ""
        return mac, ip

    @staticmethod
    def _split_status_uptime(raw: str) -> tuple[str, str]:
        """
        拆开 3.x 里合并的"状态/在线时长"单元格。

        实测形如：
          "正常\n3天7时7分"      -> ("在线", "3天7时7分")
          "未连接\n-"            -> ("断开", "-")
          "升级中\n..."          -> ("升级中", "...")
        """
        if not raw:
            return "", ""
        parts = [p.strip() for p in raw.replace("\r", "\n").split("\n") if p.strip()]
        if not parts:
            return "", ""
        st_raw = parts[0]
        uptime = parts[1] if len(parts) > 1 else ""

        # 归一化状态文案：GUI 里统一用"在线/断开/升级中"
        st = st_raw
        if st_raw in ("正常", "在线", "已连接"):
            st = "在线"
        elif st_raw in ("未连接", "离线", "断开"):
            st = "断开"
        elif "升级" in st_raw:
            st = "升级中"
        return st, uptime

    @staticmethod
    def _split_name_ssid(raw: str) -> tuple[str, str]:
        """
        拆开 3.x 里合并的"备注名/SSID"单元格。

        实测形如：
          "小花玩舍\nXiaoHuaWS"   -> ("小花玩舍", "XiaoHuaWS")
          "XiaoHuaWS"            -> ("", "XiaoHuaWS")
        """
        if not raw:
            return "", ""
        parts = [p.strip() for p in raw.replace("\r", "\n").split("\n") if p.strip()]
        if not parts:
            return "", ""
        if len(parts) == 1:
            # 只有一段：无法区分名称和 SSID，当作名称
            return parts[0], ""
        return parts[0], parts[1]

    def _read_header_map(self) -> dict:
        """读表头，返回 {列名: 索引}。按 schema 选用合适的选择器。"""
        col_map = {}
        sch = self._schema
        try:
            heads = self._page.locator(sch["head_sel"])
            for i in range(heads.count()):
                try:
                    t = heads.nth(i).inner_text().strip().replace("\n", "")
                    if t:
                        col_map[t] = i
                except Exception:
                    pass
        except Exception:
            pass
        # 4.x 的 Ant 表头用 .ant-table-thead th，兜底再试一次
        if not col_map and sch["name"] != "4.x":
            try:
                heads = self._page.locator(".ant-table-thead th")
                for i in range(heads.count()):
                    try:
                        t = heads.nth(i).inner_text().strip().replace("\n", "")
                        if t:
                            col_map[t] = i
                    except Exception:
                        pass
            except Exception:
                pass
        return col_map


    @staticmethod
    def _pick(texts: list, col_map: dict, names: list, fallback: int) -> str:
        """按候选列名取值，取不到就用 fallback 索引。"""
        for nm in names:
            if nm in col_map:
                idx = col_map[nm]
                if 0 <= idx < len(texts):
                    val = texts[idx].strip()
                    if val:
                        return val
        if fallback >= 0 and 0 <= fallback < len(texts):
            return texts[fallback].strip()
        return ""

    @staticmethod
    def _resolve_model(texts: list, col_map: dict) -> str:
        """
        求设备的型号。

        实测坑：爱快 AP 管理表里名为"型号"的那一列，内容经常是速率
        （例："1000 Mbps"）之类的信息，而真正的型号混在名称或备注里。
        因此按下面的优先级取：
          1. "型号"列的值，只要它看起来像个硬件型号
          2. 从整行里搜 IK-XXXX 样式的文本
          3. "型号"列的原值（哪怕不规范，也比空着好）
        """
        raw = ""
        idx = col_map.get("型号")
        if idx is not None and 0 <= idx < len(texts):
            raw = texts[idx].strip()

        if raw and IKuaiService._looks_like_model(raw):
            return raw

        guessed = IKuaiService._guess_model(texts)
        if guessed:
            return guessed

        return raw

    @staticmethod
    def _looks_like_model(val: str) -> bool:
        """
        粗判一个字符串是不是设备型号。

        排除速率（"1000 Mbps"）、纯数字、百分比这类明显不是型号的值。
        """
        v = val.strip()
        if not v or v in ("- -", "--"):
            return False
        # 含 "Mbps"/"Kbps"/"%" 的一律不是型号
        low = v.lower()
        for noise in ("mbps", "kbps", "gbps", "%", "dbm", "db"):
            if noise in low:
                return False
        # 纯数字或纯符号也不是
        if not any(c.isalpha() for c in v):
            return False
        # 型号一般不超过 30 字符
        return len(v) <= 30

    @staticmethod
    def _guess_model(texts: list) -> str:
        """型号通常长得像 IK-XXXX，从单元格里搜一下。"""
        for t in texts:
            t = t.strip()
            if t.upper().startswith("IK-") and 4 < len(t) < 20:
                return t
        return ""

    # ---------------- 重启单个 AP ----------------

    def restart_ap(self, ap: APInfo,
                   progress_cb: Optional[Callable[[str], None]] = None) -> bool:
        """
        重启指定 AP。

        返回 True 表示重启指令下发成功。
        整个操作投递到浏览器工作线程执行，因此可从任意线程调用。
        """

        def _do():
            if not self._connected:
                raise NotConnectedError("尚未连接路由器")
            page = self._page
            if page is None:
                raise NotConnectedError("浏览器未启动")

            def note(msg):
                self._log(msg)
                if progress_cb:
                    try:
                        progress_cb(msg)
                    except Exception:
                        pass

            # 重新定位该行（行号可能因刷新而变，用 MAC 重新匹配更可靠）
            target_row = self._locate_row(ap)
            if target_row is None:
                note("找不到 AP %s，可能列表已变化" % ap.name)
                return False

            note("定位到 %s，点击重启" % ap.name)

            # 找重启入口。各代选择器由 schema 给出：
            #   4.x = Ant Design，操作列是 <button>重启</button>
            #   3.x = Element UI，操作列是 8 个同级 <a>，其中一个是 <a>重启</a>
            sch = self._schema
            btn = None
            for sel in sch.get("restart_link_sels",
                               ['button:has-text("重启")',
                                'a:has-text("重启")']):
                cand = target_row.locator(sel)
                try:
                    if cand.count() > 0:
                        btn = cand
                        break
                except Exception:
                    continue
            if btn is None:
                # 兜底：按精确文本找
                cand = target_row.get_by_text("重启", exact=True)
                if cand.count() > 0:
                    btn = cand
            if btn is None:
                # 设备已离线时操作列里会变成"删除"，说明正在重启中
                note("%s 当前没有重启入口（可能已在重启中）" % ap.name)
                return False

            try:
                btn.first.evaluate("el => el.click()")
            except Exception:
                btn.first.click()

            # 等确认弹窗。**必须按 schema 找**：
            #   4.x = .ant-modal
            #   3.x = .el-message-box__wrapper（Element UI，4.x 的选择器一个都不中）
            modal_sel = None
            for msel in sch.get("modal_sels",
                                [".ant-modal", ".modal"]):
                try:
                    page.wait_for_selector(msel, timeout=3000,
                                           state="visible")
                    modal_sel = msel
                    break
                except PWTimeout:
                    continue
            if modal_sel is None:
                note("未出现确认弹窗")
                return False

            page.wait_for_timeout(800)

            # 截图留证
            ts = datetime.now().strftime("%H%M%S")
            safe_name = "".join(c for c in ap.name if c.isalnum() or c in "-_")
            shot = OUTPUT_DIR / ("restart_%s_%s.png" % (safe_name, ts))
            try:
                page.screenshot(path=str(shot), full_page=True)
            except Exception:
                pass

            # 在弹窗**内部**找确认按钮，避免误点到页面里别的同名按钮
            modal = page.locator(modal_sel).first
            confirm = None
            for csel in sch.get("confirm_sels",
                                ['.ant-modal-footer button:has-text("确定")']):
                cand = page.locator(csel)
                try:
                    if cand.count() > 0:
                        confirm = cand
                        break
                except Exception:
                    continue
            if confirm is None:
                # 兜底 1：弹窗内的 primary 按钮
                cand = modal.locator("button.el-button--primary, "
                                     "button.ant-btn-primary")
                if cand.count() > 0:
                    confirm = cand
            if confirm is None:
                # 兜底 2：弹窗内文本含"确定/确认/重启/是"的按钮
                cand = modal.locator(
                    'button:has-text("确定"), button:has-text("确认"), '
                    'button:has-text("重启"), button:has-text("是")')
                if cand.count() > 0:
                    confirm = cand
            if confirm is None:
                note("未找到确认按钮，已取消")
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                return False

            # 排除「取消」按钮被误选（保险）
            try:
                if confirm.count() == 1:
                    txt = confirm.first.inner_text().strip()
                    if txt in ("取消", "否", "Cancel"):
                        note("选中的是「取消」按钮，放弃本次重启")
                        return False
            except Exception:
                pass

            try:
                confirm.first.evaluate("el => el.click()")
            except Exception:
                confirm.first.click()
            note("已确认重启 %s" % ap.name)

            # 等弹窗消失，且**确认这次重启真的下发成功**：
            # 弹窗没关掉 = 确认没点到，不能当成功。
            for _ in range(20):          # 最多等 10 秒
                page.wait_for_timeout(500)
                try:
                    if page.locator(modal_sel).count() == 0:
                        break
                except Exception:
                    break
            else:
                note("%s 的确认弹窗未关闭，重启可能未下发" % ap.name)
                return False

            return True

        return self._submit(_do, timeout=120)

    def _locate_row(self, ap: APInfo):
        """
        在当前页面上重新定位某台 AP 所在的行。按 schema 选用行选择器。

        【为什么必须分两轮扫】
        3.x 现场 12 台 AP 的备注名**全都叫「小花玩舍」**，只有 MAC/IP 不同。
        原实现是「逐行判断：MAC 不中就退回名称」，于是第一行就靠名称命中，
        结果**永远重启到第 1 台** —— 用户选第 12 台却重启了第 1 台。
        所以必须：先全表扫一遍 MAC，全都不中才用名称兜底。
        """
        page = self._page
        sch = self._schema
        rows = page.locator(sch["row_sel"])
        count = rows.count()
        norm_mac = ap.mac.lower().replace(":", "").replace("-", "").strip()
        skip = sch.get("row_skip_head", 0)

        def _rows_iter():
            for i in range(count):
                if i < skip:
                    continue
                row = rows.nth(i)
                try:
                    yield row, row.inner_text()
                except Exception:
                    continue

        # ---- 第 1 轮：全表按 MAC 精确匹配（唯一可靠）----
        if norm_mac:
            for row, txt in _rows_iter():
                flat = (txt.lower().replace(":", "")
                        .replace("-", "").replace("\n", ""))
                if norm_mac in flat:
                    return row

        # ---- 第 2 轮：才退到名称匹配 ----
        # 名称可能重复（现场 12 台同名），因此先收集所有候选；
        # 只有唯一命中才返回，否则宁可返回 None 让调用方报错，
        # 也不能瞎猜一台去重启。
        if ap.name:
            hits = [(row, txt) for row, txt in _rows_iter() if ap.name in txt]
            if len(hits) == 1:
                return hits[0][0]
            if len(hits) > 1:
                # 名称重名，尝试用 IP 再区分一次
                ip = (ap.ip or "").strip()
                if ip and ip not in ("- -", "-", ""):
                    for row, txt in hits:
                        if ip in txt:
                            return row
                return None      # 无法唯一确定，拒绝误操作
        return None

    # ---------------- 修改备注 ----------------
    def _set_comment_by_api(self, ap: APInfo, comment: str) -> bool:
        """
        4.x 新固件的备注回写：API 整传路线（在工作线程内调用）。

        背景：新固件把「编辑」抽屉的整个表单 disabled（AP名称/SSID/备注
        全部不可编辑，Vue 持续重渲染，JS 移除属性会被立即加回），
        UI 自动化无法填值。而 ac_server/edit 是**全量更新**接口：
        少传一个必填字段就报「参数错误: hide_ssid1」。

        正确做法 = 前端「保存」按钮的真实行为：
          1. ac_server/show 拿该 AP 的完整记录（实测 570 个字段）
          2. 深拷贝后只改 comment
          3. 整条回传 ac_server/edit
        真机验证：其余字段逐项对比零差异（2026-09-22，192.168.50.1）。
        """
        page = self._page
        mac = (ap.mac or "").lower()

        def _call(body):
            return page.evaluate("""async (b) => {
                const r = await fetch('/Action/call', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(b),
                    credentials: 'include',
                });
                return await r.text();
            }""", body)

        # 1) 拉完整记录
        raw = _call({"func_name": "ac_server", "action": "show",
                     "param": {"TYPE": "data", "limit": "0,500"}})
        try:
            import json as _json
            items = _json.loads(raw).get("results", {}).get("data", []) or []
        except Exception:
            self._log("备注回写：解析 AP 记录失败")
            return False
        rec = None
        for r in items:
            if (r.get("mac") or "").lower() == mac:
                rec = r
                break
        if rec is None:
            self._log("备注回写：API 返回里找不到 %s" % ap.mac)
            return False

        old = rec.get("comment", "")

        # 2) 只改 comment，整条回传
        rec2 = dict(rec)
        rec2["comment"] = comment
        resp = _call({"func_name": "ac_server", "action": "edit",
                      "param": rec2})
        ok = '"code":0' in resp
        if not ok:
            self._log("备注回写：edit 接口返回 %s" % resp[:150])
            return False

        # 3) 回读确认（防「说成功实际没写」）
        raw3 = _call({"func_name": "ac_server", "action": "show",
                      "param": {"TYPE": "data", "limit": "0,500"}})
        try:
            items3 = _json.loads(raw3).get("results", {}).get("data", [])
            rec3 = next((x for x in items3
                         if (x.get("mac") or "").lower() == mac), None)
            if rec3 is not None and rec3.get("comment", "") != comment:
                self._log("备注回写：回读值 %r 与目标 %r 不符"
                          % (rec3.get("comment"), comment))
                return False
        except Exception:
            pass    # 回读失败不阻断（edit 已返回成功）

        self._log("已保存 %s 的备注：%s（原值：%s，API 整传）"
                  % (ap.name, comment or "（空）", old or "（空）"))
        return True

    def set_ap_comment(self, ap: APInfo, comment: str,
                       progress_cb: Optional[Callable[[str], None]] = None
                       ) -> bool:
        """
        修改指定 AP 在路由器系统内的「终端备注」。

        流程（选择器全部来自 schema 的 remark_* 系列键）：
          1. _locate_row() 按 MAC 定位行（同名 AP 也安全）
          2. 点该行操作列的「修改备注」入口
          3. 等弹窗出现 -> 在备注输入框里填入新值
          4. 点「确定」-> 等弹窗关闭（关闭才算成功，与重启同一标准）
          5. 失败/中途异常时优先按取消/Escape，尽量不留脏弹窗

        返回 True 表示保存成功。整个操作投递到浏览器工作线程执行。
        """
        comment = (comment or "").strip()

        def _do():
            if not self._connected:
                raise NotConnectedError("尚未连接路由器")
            page = self._page
            if page is None:
                raise NotConnectedError("浏览器未启动")

            sch = self._schema

            # ---- 4.x 新固件：API 整传路线 ----
            # 编辑抽屉被固件整体 disabled（Vue 持续重渲染），UI 路线不可行；
            # ac_server/edit 是全量更新接口：show 拿整条记录 -> 只改
            # comment -> 原样回传，即前端保存按钮的真实行为。
            if sch.get("remark_mode") == "api_full_record":
                return self._set_comment_by_api(ap, comment)

            def note(msg):
                self._log(msg)
                if progress_cb:
                    try:
                        progress_cb(msg)
                    except Exception:
                        pass

            def _cancel_and_return(msg, ok=False):
                # 收尾：尽量关掉弹窗，避免影响后续操作
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                except Exception:
                    pass
                note(msg)
                return ok

            sch = self._schema

            target_row = self._locate_row(ap)
            if target_row is None:
                note("找不到 AP %s，无法修改备注" % ap.name)
                return False

            # 1) 点「修改备注」入口
            link = None
            for sel in sch.get("remark_link_sels", []):
                cand = target_row.locator(sel)
                try:
                    if cand.count() > 0:
                        link = cand.first
                        break
                except Exception:
                    continue
            if link is None:
                note("%s 的操作列没有「修改备注」入口" % ap.name)
                return False
            try:
                link.click(timeout=5000)
            except Exception as ex:
                note("点击修改备注失败：%s" % ex)
                return False

            # 2) 等弹窗
            modal = None
            modal_sel = None
            for ms in sch.get("remark_modal_sels", []):
                try:
                    page.wait_for_selector(ms, timeout=3000, state="visible")
                    modal = page.locator(ms).first
                    modal_sel = ms
                    break
                except Exception:
                    continue
            if modal is None:
                return _cancel_and_return("%s 的修改备注弹窗未出现" % ap.name)

            # 2.5) 4.x：备注在编辑抽屉的「其他设置」tab 里，先切 tab
            tab_name = sch.get("remark_tab_name")
            if tab_name:
                for tsel in sch.get("remark_tab_sels", []):
                    try:
                        tab = modal.locator(tsel)
                        if tab.count() > 0:
                            tab.first.click(timeout=3000)
                            page.wait_for_timeout(800)
                            break
                    except Exception:
                        continue

            # 3) 找备注输入框并填值
            # 【范围策略】先在弹窗容器内找；找不到再回退全页。
            # 原因：3.x 的弹窗是"Element UI 容器 + jQuery jqmWindow 内容"
            # 的混合体 —— 输入框(.jqmWindow_box)在 .el-dialog__wrapper
            # 里面，但确定/取消(.jqmWindow_foot)不在！只查容器会漏掉按钮
            # （真机踩坑：报「找不到确定按钮」）。
            inp = None
            for scope in (modal, page):
                for s in sch.get("remark_input_sels", []):
                    cand = scope.locator(s)
                    try:
                        if cand.count() > 0:
                            inp = cand.first
                            break
                    except Exception:
                        continue
                if inp is not None:
                    break
            if inp is None:
                return _cancel_and_return("修改备注弹窗里找不到输入框")

            old_val = ""
            try:
                old_val = inp.input_value() or ""
            except Exception:
                pass
            try:
                inp.fill("")
                inp.fill(comment)     # fill 两次：先清后填，防止追加
                # 触发 input 事件，让 Vue 认识到值变了（只 fill 可能不生效）
                inp.evaluate(
                    "el => el.dispatchEvent(new Event('input', {bubbles: true}))")
            except Exception as ex:
                return _cancel_and_return("填入备注失败：%s" % ex)

            # 4) 点「确定/保存」——同样先容器内、再回退全页
            # （3.x 的 .jqmWindow_foot 在容器外，见上面 [范围策略]）
            confirm = None
            for scope in (modal, page):
                for s in sch.get("remark_confirm_sels", []):
                    cand = scope.locator(s)
                    try:
                        if cand.count() > 0:
                            confirm = cand.first
                            break
                    except Exception:
                        continue
                if confirm is not None:
                    break
            if confirm is None:
                return _cancel_and_return("找不到确定按钮")

            # 保险：确认按钮文本若是「取消/否」则放弃（与重启同一防线）
            try:
                btxt = (confirm.inner_text() or "").strip()
                if not btxt:
                    btxt = (confirm.evaluate("e => e.value || ''") or "").strip()
                if btxt in ("取消", "否", "Cancel", "cancle", "Cancel"):
                    return _cancel_and_return("抓到了取消按钮，放弃保存")
            except Exception:
                pass

            try:
                confirm.click(timeout=5000)
            except Exception as ex:
                return _cancel_and_return("点击确定失败：%s" % ex)

            # 5) 弹窗必须关闭才算成功（最多 10 秒）
            #    抽屉(.ant-drawer)关闭后元素可能仍在 DOM（动画/隐藏），
            #    所以除了 count==0 还接受「不可见」。
            for _ in range(20):
                page.wait_for_timeout(500)
                try:
                    loc = page.locator(modal_sel)
                    if loc.count() == 0 or not loc.first.is_visible():
                        break
                except Exception:
                    break
            else:
                return _cancel_and_return(
                    "%s 的修改备注弹窗未关闭，保存可能未下发" % ap.name)

            note("已保存 %s 的备注：%s（原值：%s）"
                 % (ap.name, comment or "（空）", old_val or "（空）"))
            return True

        return self._submit(_do, timeout=90)


    # ---------------- 批量重启 ----------------
    def restart_aps(self, aps: list[APInfo],
                    progress_cb: Optional[Callable[[str, int, int], None]] = None
                    ) -> tuple[int, int]:
        """
        批量重启多台 AP。

        返回 (成功数, 总数)。
        每台之间留间隔，避免后台处理不过来。

        整个批处理作为**一个任务**投递到工作线程，
        内部的 restart_ap() / fetch_ap_list() 会被 _submit 判定为同线程重入，
        直接同步执行，不会自己等自己导致死锁。
        """

        def _do():
            total = len(aps)
            ok = 0
            for idx, ap in enumerate(aps, start=1):
                if progress_cb:
                    try:
                        progress_cb(ap.name, idx, total)
                    except Exception:
                        pass
                try:
                    if self.restart_ap(ap):
                        ok += 1
                except Exception as ex:
                    self._log("重启 %s 失败: %s" % (ap.name, ex))
                # 间隔，并刷新列表（轻量路径，只点页面内刷新按钮）
                # 再处理下一台。设备刚重启时行会短暂消失/变"- -"，
                # 用 force 轻刷一次即可，无需重载整个页面。
                if idx < total:
                    time.sleep(2)
                    try:
                        self.fetch_ap_list(force_reload=False)
                    except Exception:
                        pass
            return ok, total

        # 超时按每台 60 秒估算，至少给 180 秒
        timeout = max(180.0, 60.0 * len(aps))
        return self._submit(_do, timeout=timeout)
