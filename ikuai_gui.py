#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
爱快路由器管理工具 —— GUI

布局：
  ┌───────────────────────────────────────────────────────────┐
  │ IP/域名  [____]  HTTP端口 [__]  HTTP/HTTPS [v]             │
  │ 登录用户 [____]  登录密码 [____]  □记住密码 □自动登录 [连接]│
  ├───────────────────────────────────────────────────────────┤
  │ AP终端列表   刷新时间[1s/3s/5s/10s] [刷新] [批量重启]      │
  ├───────────────────────────────────────────────────────────┤
  │ 分组│名称│型号│终端IP│终端MAC│运行时间│运行状态│操作        │
  └───────────────────────────────────────────────────────────┘

技术说明：
  - 所有浏览器操作在后台线程执行，通过队列回传结果给 UI 线程
  - Tkinter 不是线程安全的，UI 更新一律通过 after() 轮询队列完成
"""

from __future__ import annotations

import ipaddress
import json
import queue
import re
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import (Tk, StringVar, BooleanVar, IntVar, END, N, S, E, W,
                     ttk, messagebox, filedialog, simpledialog)
import tkinter as tk

# 控制台可能是 GBK 代码页，输出中文日志前先兜底
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from ikuai_service import IKuaiService, APInfo, NotConnectedError, OUTPUT_DIR
import ikuai_config

APP_TITLE = "爱快路由-AP终端工具"
APP_VERSION = "1.12"
APP_COPYRIGHT = "| © Jcsit&viclai"

# 程序图标：跟程序走（绿色软件设计）
# 打包成 exe 后 __file__ 指向临时解压目录 _MEIPASS，图标已由打包器
# 内嵌为 exe 资源（--icon），窗口 iconbitmap 用 exe 自身即可；
# 源码运行时仍找同目录的 ico/png。
_APP_DIR = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
APP_ICON = _APP_DIR / "ikuai-R-logo.ico"      # Windows 首选
APP_ICON_PNG = _APP_DIR / "ikuai-R-logo.png"  # 非 Windows / ico 失败时兜底

# 凭据保存位置：程序目录下的加密配置文件
# 绿色单文件设计 —— 配置跟着程序走，密码经 AES 加密存储
CONFIG_FILE = ikuai_config.CONFIG_FILE

# 刷新间隔选项
REFRESH_OPTIONS = ["1s", "3s", "5s", "10s"]


class IKuaiGUI:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("%s v%s %s" % (APP_TITLE, APP_VERSION, APP_COPYRIGHT))
        self._apply_window_icon()
        self.root.geometry("1280x720")
        self.root.minsize(1080, 600)

        # ---------- 状态 ----------
        # headless 初始值跟随界面勾选框，避免"界面显示勾选但实际弹窗"的错位
        self.service = IKuaiService(log_cb=self._queue_log, headless=True)
        self.ui_queue: queue.Queue = queue.Queue()
        self.ap_list: list[APInfo] = []
        self.busy = False                    # 是否有后台任务在跑
        self.auto_refresh = False
        self.refresh_job = None              # after() 的任务 id
        self.monitor_mode = False            # 是否处于重启后的监视状态
        self.monitor_targets: dict[str, dict] = {}   # key -> 状态信息
        self.monitor_job = None              # 监视模式的排队 id
        self._just_recovered = False         # 刚全部恢复（状态栏用一次）
        self._closing = False                # 正在关闭：停止队列轮询/定时器
        self._pump_job = None                # 队列轮询的 after id
        self._refresh_inflight = False       # 是否已有一次读取在飞
        self._row_keys: list[str] = []       # iid 顺序，便于按行号对账
        self._render_sig = {}                # iid -> 渲染指纹，用于跳过无变化行
        # 列排序：sort_col=None 表示未排序（按路由器返回的原始顺序）
        self.sort_col: str | None = None     # 当前排序列
        self.sort_desc: bool = True          # 首次点击降序，再点切换升序

        # ---------- 变量 ----------
        self.var_host = StringVar(value="192.168.50.1")
        self.var_http_port = StringVar(value="80")
        self.var_https_port = StringVar(value="443")
        self.var_scheme = StringVar(value="HTTP")
        self.var_user = StringVar(value="admin")
        self.var_password = StringVar(value="")
        self.var_remember = BooleanVar(value=True)
        self.var_autologin = BooleanVar(value=False)
        self.var_headless = BooleanVar(value=True)   # 默认不显示浏览器窗口
        self.var_refresh = StringVar(value="5s")
        self.var_auto_refresh = BooleanVar(value=True)   # 默认开自动刷新
        self.var_status = StringVar(value="未连接")

        self._build_ui()
        self._load_config()

        # 启动队列轮询
        self.root.after(100, self._pump_queue)

        # 自动登录
        if self.var_autologin.get() and self.var_password.get():
            self.root.after(500, self.on_connect)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _apply_window_icon(self):
        """给主窗口设置程序图标（ikuai-R-logo.ico）。

        - Windows：root.iconbitmap(ico)，系统按 DPI 自动选 16/24/32/48 档；
        - 其他平台 / ico 加载失败：退回 iconphoto(PNG)，保证不因缺图标崩溃。
        - 图标文件不存在时静默跳过（绿色软件：文件被删也不影响启动）。
        """
        try:
            if sys.platform == "win32" and APP_ICON.is_file():
                self.root.iconbitmap(str(APP_ICON))
                return
        except tk.TclError:
            pass                      # ico 损坏等 -> 走 PNG 兜底
        try:
            if APP_ICON_PNG.is_file():
                icon = tk.PhotoImage(file=str(APP_ICON_PNG))
                # tag 防止 PhotoImage 被垃圾回收后图标失效（Tk 常见坑）
                self.root.iconphoto(True, icon)
                self._icon_image = icon
        except tk.TclError:
            pass                      # PNG 也不行就放弃，不影响功能

    # ==============================================================
    # 界面构建
    # ==============================================================
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", rowheight=30)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Conn.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Danger.TButton", foreground="#c62828")

        # 操作列的「重启」按钮样式：黑字 + 浅蓝填充
        # 这是 ttk 的全局样式，用 "clam" 主题时 background/foreground 才生效。
        # 需要同时给出 map()，否则鼠标悬停/按下会被主题默认色盖掉。
        style.configure(
            "Restart.TButton",
            font=("Microsoft YaHei UI", 9, "bold"),
            foreground="#000000",            # 黑色文字
            background="#cfe4fb",            # 浅蓝填充
            relief="raised",
            borderwidth=1,
            padding=(10, 2),
        )
        style.map(
            "Restart.TButton",
            foreground=[("disabled", "#9e9e9e"), ("pressed", "#000000"),
                        ("active", "#000000")],
            background=[("disabled", "#eceff1"), ("pressed", "#a9cdf5"),
                        ("active", "#bcd9f8")],
        )

        # ---------- 顶部：连接区 ----------
        top = ttk.LabelFrame(self.root, text=" 路由器连接 ")
        top.pack(fill="x", padx=10, pady=(10, 6))

        # 第一行：IP / 端口 / 协议
        r1 = ttk.Frame(top)
        r1.pack(fill="x", padx=10, pady=(10, 6))

        # IP/域名：支持 IPv4、IPv6、域名。
        # IPv6 直接填裸地址即可（例：240e:350:6e00:964c:7108:66bf:75e2:762c），
        # 也可以带方括号（例：[240e:350:...]:80）—— 两种写法都会自动识别。
        ttk.Label(r1, text="IP/域名：").pack(side="left")
        ttk.Entry(r1, textvariable=self.var_host, width=30).pack(side="left", padx=(0, 14))

        ttk.Label(r1, text="HTTP端口：").pack(side="left")
        ttk.Entry(r1, textvariable=self.var_http_port, width=7).pack(side="left")

        ttk.Label(r1, text="  HTTPS端口：").pack(side="left")
        ttk.Entry(r1, textvariable=self.var_https_port, width=7).pack(side="left", padx=(0, 14))

        ttk.Label(r1, text="协议：").pack(side="left")
        scheme = ttk.Combobox(r1, textvariable=self.var_scheme,
                              values=["HTTP", "HTTPS"], width=8, state="readonly")
        scheme.pack(side="left")
        scheme.bind("<<ComboboxSelected>>", self._on_scheme_change)

        # IP 行下方的提示（讲清 IPv6 怎么填）
        ttk.Label(top, foreground="#888",
                  text="支持 IPv4 / IPv6 / 域名。IPv6 直接填裸地址，"
                       "如 240e:350:6e00:964c:7108:66bf:75e2:762c；"
                       "也可写成 [地址]:端口。").pack(anchor="w", padx=10)

        # 第二行：账号密码 + 选项 + 连接按钮
        r2 = ttk.Frame(top)
        r2.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Label(r2, text="登录用户：").pack(side="left")
        ttk.Entry(r2, textvariable=self.var_user, width=16).pack(side="left", padx=(0, 14))

        ttk.Label(r2, text="登录密码：").pack(side="left")
        self._pwd_entry = ttk.Entry(r2, textvariable=self.var_password, width=16,
                                    show="●")
        self._pwd_entry.pack(side="left", padx=(0, 6))

        self.chk_show_pwd = ttk.Checkbutton(r2, text="显示", command=self._toggle_pwd)
        self.chk_show_pwd.pack(side="left", padx=(0, 14))

        ttk.Checkbutton(r2, text="记住密码",
                        variable=self.var_remember).pack(side="left", padx=(0, 10))
        ttk.Checkbutton(r2, text="进入界面后自动连接",
                        variable=self.var_autologin).pack(side="left", padx=(0, 10))
        self.chk_headless = ttk.Checkbutton(r2, text="隐藏浏览器窗口",
                                            variable=self.var_headless,
                                            command=self._on_headless_toggle)
        self.chk_headless.pack(side="left", padx=(0, 16))

        self.btn_connect = ttk.Button(r2, text="连接", width=12,
                                      style="Conn.TButton",
                                      command=self.on_connect)
        self.btn_connect.pack(side="left")
        self.btn_disconnect = ttk.Button(r2, text="断开", width=8,
                                         command=self.on_disconnect)
        self.btn_disconnect.pack(side="left", padx=(6, 0))
        # ---------- 中部：AP 列表工具条 ----------
        mid = ttk.LabelFrame(self.root, text=" AP终端列表 ")
        mid.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        bar = ttk.Frame(mid)
        bar.pack(fill="x", padx=10, pady=(8, 6))

        ttk.Label(bar, text="刷新时间：").pack(side="left")
        self.cmb_refresh = ttk.Combobox(bar, textvariable=self.var_refresh,
                                        values=REFRESH_OPTIONS, width=6,
                                        state="readonly")
        self.cmb_refresh.pack(side="left")
        self.cmb_refresh.bind("<<ComboboxSelected>>", self._on_refresh_change)

        self.btn_refresh = ttk.Button(bar, text="刷新", width=8,
                                      command=self.on_refresh)
        self.btn_refresh.pack(side="left", padx=(10, 0))

        # 自动刷新开关：
        # 用户反馈"列表一直在刷、选不中设备"，所以给一个能真正停下来的开关。
        # 关掉后后台不再轮询，列表保持静止，可以安心勾选/多选。
        self.chk_autorefresh = ttk.Checkbutton(
            bar, text="自动刷新", variable=self.var_auto_refresh,
            command=self._on_autorefresh_toggle)
        self.chk_autorefresh.pack(side="left", padx=(10, 0))

        self.btn_batch = ttk.Button(bar, text="批量重启", width=10,
                                    style="Danger.TButton",
                                    command=self.on_batch_restart)
        self.btn_batch.pack(side="left", padx=(8, 0))

        # 状态标签（靠右）
        self.lbl_status = ttk.Label(bar, textvariable=self.var_status,
                                    foreground="#1b5e20")
        self.lbl_status.pack(side="right")

        # ---------- 表格 ----------
        table_frame = ttk.Frame(mid)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        columns = ("group", "name", "model", "ip", "mac", "uptime",
                   "status", "comment", "action")
        headings = ("分组", "名称", "型号", "终端IP", "终端MAC",
                    "运行时间", "运行状态", "备注", "操作")

        self.tree = ttk.Treeview(table_frame, columns=columns,
                                 show="headings", selectmode="extended")

        widths = {"group": 80, "name": 130, "model": 90, "ip": 115,
                  "mac": 150, "uptime": 110, "status": 80,
                  "comment": 150, "action": 96}
        # 可点击排序的列（用户要求：名称/型号/IP/MAC/运行时间/状态；
        # 分组、备注一并支持，操作列没有可排序内容）
        self._sortable_cols = ("group", "name", "model", "ip",
                               "mac", "uptime", "status", "comment")
        for col, head in zip(columns, headings):
            if col in self._sortable_cols:
                self.tree.heading(col, text=head,
                                  command=lambda c=col: self._on_heading_click(c))
            else:
                self.tree.heading(col, text=head)
            anchor = "center" if col in ("group", "model", "status",
                                         "action") else "w"
            self.tree.column(col, width=widths[col], anchor=anchor, stretch=False)

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # 行样式：重启中的行标黄
        self.tree.tag_configure("restarting", background="#fff3cd",
                                foreground="#8a6d3b")
        self.tree.tag_configure("offline", background="#f8d7da",
                                foreground="#721c24")

        # ---- 操作列的真实按钮（浮层） ----
        # Treeview 的单元格放不了控件、也不支持单格底色，所以"重启按钮"
        # 只能做成**浮在表格上方的真实 ttk.Button**。
        # 用 place() 精确贴到每一行操作列的位置，并跟随滚动同步。
        self._row_buttons: dict[str, ttk.Button] = {}   # iid -> Button
        self._btn_pool: list[ttk.Button] = []           # 复用池，避免频繁创建
        # 行高固定，浮层按钮的高度取行高减去上下留白
        self._row_height = 30
        self._btn_height = 24

        # 单击「操作」列 = 重启该 AP
        # （浮层按钮覆盖了大部分区域，但边缘/间隙仍可能点到表格本体，
        #  所以这条兜底处理保留）
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_row_double_click)
        # 鼠标移到操作列时变手型，提示"这里可以点"
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", lambda e: self._set_cursor(""))
        # 滚动时浮层按钮必须跟着走，否则会和行错位
        self.tree.bind("<Configure>", lambda e: self._sync_row_buttons())
        vsb.configure(command=self._on_yview)
        hsb.configure(command=self._on_xview)
        # 右键菜单
        self._build_context_menu()

        # ---------- 底部：日志 ----------
        bottom = ttk.LabelFrame(self.root, text=" 运行日志 ")
        bottom.pack(fill="x", padx=10, pady=(0, 10))

        log_frame = ttk.Frame(bottom)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(6, 8))

        self.txt_log = tk.Text(log_frame, height=7, wrap="none",
                               font=("Consolas", 9), state="disabled",
                               background="#fafafa")
        log_vsb = ttk.Scrollbar(log_frame, orient="vertical",
                                command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=log_vsb.set)
        self.txt_log.grid(row=0, column=0, sticky="nsew")
        log_vsb.grid(row=0, column=1, sticky="ns")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

    def _build_context_menu(self):
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="重启选中的 AP", command=self.on_restart_selected)
        self.menu.add_command(label="修改备注", command=self._on_menu_edit_comment)
        self.menu.add_command(label="复制 MAC 地址", command=self._copy_mac)
        self.menu.add_separator()
        self.menu.add_command(label="刷新列表", command=self.on_refresh)
        self.menu.add_command(label="打开截图目录", command=self._open_output_dir)
        self.menu.add_command(label="查看配置文件位置", command=self._show_config_path)
        self.tree.bind("<Button-3>", self._on_show_menu)

    def _on_menu_edit_comment(self):
        """右键菜单入口：对选中（或右键所在）行修改备注。"""
        sel = self.tree.selection()
        if not sel:
            return
        key = sel[0]
        for ap in self.ap_list:
            if (ap.key() or ap.name) == key:
                self._edit_comment(ap)
                return

    # ==============================================================
    # 线程安全：队列通信
    # ==============================================================
    def _queue_log(self, line: str):
        """供 service 在后台线程调用。"""
        self.ui_queue.put(("log", line))

    def _pump_queue(self):
        """
        在 UI 线程轮询队列，处理后台线程的消息。

        【关闭时必须停下来】
        这个函数会不断用 after() 重新排期。窗口 destroy 之后再排期，
        Tk 会抛 `invalid command name` / TclError；更糟的是此时如果
        还有后台线程在投递消息，_handle_message 会去操作已经销毁的
        控件，直接崩到控制台。所以：
          - 用 _closing 标志让循环自己退出
          - destroy 之后再收到消息就丢弃
        """
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if self._closing:
                    break
                try:
                    self._handle_message(kind, payload)
                except tk.TclError:
                    # 控件已被销毁（正在关闭），丢弃剩余消息即可
                    return
        except queue.Empty:
            pass
        if not self._closing:
            try:
                self._pump_job = self.root.after(120, self._pump_queue)
            except tk.TclError:
                # 窗口已销毁，停止排期即可
                self._closing = True

    def _handle_message(self, kind: str, payload):
        if kind == "log":
            self._append_log(payload)
        elif kind == "aps":
            self._render_aps(payload)
        elif kind == "refresh_done":
            self._refresh_inflight = False
            self._set_connected_status()
        elif kind == "connected":
            self._on_connected_ok()
        elif kind == "disconnected":
            self._on_disconnected()
        elif kind == "connect_failed":
            self._on_connect_failed(payload)
        elif kind == "busy":
            self._set_busy(payload)
        elif kind == "status":
            self.var_status.set(payload)
        elif kind == "restart_done":
            self._on_restart_done(**payload)
        elif kind == "batch_done":
            self._on_batch_done(**payload)
        elif kind == "comment_done":
            key, ok, new_val = payload
            self._on_comment_done(key, ok, new_val)
        elif kind == "busy_done":
            self._set_busy(False)
        elif kind == "error":
            messagebox.showerror("出错", payload)

    def _append_log(self, line: str):
        self.txt_log.configure(state="normal")
        self.txt_log.insert(END, line + "\n")
        # 只保留最近 500 行
        line_count = int(self.txt_log.index("end-1c").split(".")[0])
        if line_count > 500:
            self.txt_log.delete("1.0", "%d.0" % (line_count - 500))
        self.txt_log.see(END)
        self.txt_log.configure(state="disabled")

    def _set_busy(self, busy: bool):
        """
        禁止并发操作时置灰按钮。

        【注意】不要在这里禁用「批量重启」：
        列表每 2~3 秒自动读一次，如果读取也走 busy，批量重启按钮会
        几乎一直是灰的，用户根本点不到（历史 bug）。
        批量重启真正冲突的是「已经在重启中」，由 _confirm_and_restart
        自己判断，不依赖这里的 busy。
        """
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.btn_connect, self.btn_refresh, self.btn_disconnect):
            try:
                btn.configure(state=state)
            except Exception:
                pass
        # 浏览器运行中不允许改显示模式（改了也没用），置灰
        try:
            self.chk_headless.configure(
                state="disabled" if self.service.browser_started else "normal")
        except Exception:
            pass

    # ==============================================================
    # 操作列浮层按钮
    # ==============================================================
    def _on_yview(self, *args):
        """竖向滚动：滚动表格后让浮层按钮跟上。"""
        self.tree.yview(*args)
        self._sync_row_buttons()

    def _on_xview(self, *args):
        """横向滚动：同理。"""
        self.tree.xview(*args)
        self._sync_row_buttons()

    def _action_col_geometry(self):
        """
        算出「操作」列在 tree 控件内的 x 起点与宽度（像素）。

        注意：ttk.Treeview 的 column() **不支持查询 x 偏移**
        （只有 width/minwidth/stretch/anchor），别写 column("action","x")。
        x 偏移只能自己累加：把操作列之前所有列的宽度加起来，
        再减去横向滚动量（xview 给出的左边缘位置）。

        返回 (x, width)；拿不到返回 None。
        """
        try:
            cols = list(self.tree["columns"])
            if "action" not in cols:
                return None
            act_w = int(self.tree.column("action", "width"))
            if act_w <= 0:
                return None
            # 累加操作列之前各列的宽度
            offset = 0
            for name in cols:
                if name == "action":
                    break
                offset += int(self.tree.column(name, "width"))
            # 横向滚动偏移：xview()[0] 是"第一个可见列占总宽的比例"
            total = offset + act_w
            try:
                frac = float(self.tree.xview()[0])
            except Exception:
                frac = 0.0
            offset -= int(frac * total)
            return offset, act_w
        except tk.TclError:
            return None

    def _get_row_button(self, idx: int) -> ttk.Button:
        """取（或新建）第 idx 个浮层按钮。复用池避免每次刷新都建控件。"""
        while len(self._btn_pool) <= idx:
            btn = ttk.Button(self.tree, text="重启", width=6,
                             style="Restart.TButton", cursor="hand2")
            self._btn_pool.append(btn)
        return self._btn_pool[idx]

    def _hide_row_buttons(self):
        for btn in self._btn_pool:
            btn.place_forget()

    def _sync_row_buttons(self):
        """
        把浮层「重启」按钮贴到每一行的操作列位置上。

        要点：
          - 只对**当前可见**的行放按钮，不可见的隐藏（性能 + 避免错位）
          - place() 的坐标相对 tree 控件；bbox 给的 y 已经是含滚动偏移的
            可见坐标，所以直接用即可
          - 重启中的行按钮置灰且改文案，避免重复点击
        """
        if getattr(self, "_closing", False):
            return
        tree = getattr(self, "tree", None)
        if tree is None or not hasattr(self, "_btn_pool"):
            return

        geo = self._action_col_geometry()
        if geo is None:
            return
        col_x, col_w = geo
        if col_w <= 0:
            return

        btn_w = max(48, col_w - 12)      # 左右各留 6px 呼吸位
        btn_x = col_x + (col_w - btn_w) // 2

        visible = tree.get_children()
        used = 0
        for iid in visible:
            try:
                bb = tree.bbox(iid, column="action")
            except tk.TclError:
                return
            if not bb:
                continue                  # 该行滚出可视区，跳过
            _rx, ry, _rw, rh = bb
            if rh <= 0:
                continue
            btn = self._get_row_button(used)
            used += 1

            values = tree.item(iid, "values")
            status = str(values[6]) if len(values) > 6 else ""
            if status == "重启中":
                label, state = "重启中", "disabled"
            else:
                label, state = "重启", "normal"

            try:
                btn.configure(text=label, state=state)
                btn.place(x=btn_x, y=ry + 3, width=btn_w,
                          height=max(18, rh - 6))
            except tk.TclError:
                return

            # 绑定重启（先解绑再绑，避免复用后指向旧行）
            self._row_buttons[iid] = btn
            btn.unbind("<Button-1>")
            btn.bind("<Button-1>",
                     lambda e, k=iid: self._on_row_button_click(e, k))

        # 多余出来的按钮藏起来
        for btn in self._btn_pool[used:]:
            try:
                btn.place_forget()
            except tk.TclError:
                pass

    def _on_row_button_click(self, _event, iid: str):
        """浮层按钮被点击 -> 重启该行对应的 AP。"""
        target = None
        for ap in self.ap_list:
            if (ap.key() or ap.name) == iid:
                target = ap
                break
        if target is None:
            return
        # 和单击处理共用同一个防抖时间戳
        now = time.time()
        if now - getattr(self, "_last_action_click", 0.0) < 0.8:
            return
        self._last_action_click = now
        self._confirm_and_restart([target])
        return "break"          # 阻止事件继续冒泡到 tree

    # ==============================================================
    # 配置读写
    # ==============================================================
    def _load_config(self):
        """从加密配置文件读取设置。"""
        try:
            data = ikuai_config.load_config()
        except Exception:
            return
        self.var_host.set(str(data.get("host", "192.168.50.1")))
        self.var_http_port.set(str(data.get("http_port", "80")))
        self.var_https_port.set(str(data.get("https_port", "443")))
        self.var_scheme.set(data.get("scheme", "HTTP"))
        self.var_user.set(str(data.get("user", "admin")))
        self.var_remember.set(bool(data.get("remember", True)))
        self.var_autologin.set(bool(data.get("autologin", False)))
        self.var_headless.set(bool(data.get("headless", True)))
        self.var_refresh.set(data.get("refresh", "5s"))
        self.var_auto_refresh.set(bool(data.get("auto_refresh", True)))
        # 密码已由 config 模块解密；解不开时会是空串
        pw = data.get("password") or ""
        if data.get("remember") and pw:
            self.var_password.set(pw)

    def _save_config(self):
        """写入加密配置文件。"""
        try:
            data = {
                "host": self.var_host.get(),
                "http_port": self.var_http_port.get(),
                "https_port": self.var_https_port.get(),
                "scheme": self.var_scheme.get(),
                "user": self.var_user.get(),
                "remember": self.var_remember.get(),
                "autologin": self.var_autologin.get(),
                "headless": self.var_headless.get(),
                "refresh": self.var_refresh.get(),
                "auto_refresh": self.var_auto_refresh.get(),
                # 只有勾了「记住密码」才落盘，否则清空
                "password": self.var_password.get() if self.var_remember.get() else "",
            }
            ikuai_config.save_config(data)
        except Exception:
            pass

    # ==============================================================
    # 事件处理
    # ==============================================================
    def _on_scheme_change(self, _evt=None):
        """切换协议时自动填对应端口。"""
        if self.var_scheme.get() == "HTTPS":
            if self.var_http_port.get() == "80":
                self.var_http_port.set("443")
        else:
            if self.var_http_port.get() == "443":
                self.var_http_port.set("80")

    def _toggle_pwd(self):
        """切换密码显示/隐藏。"""
        show_char = "" if self.chk_show_pwd.instate(["selected"]) else "●"
        self._pwd_entry.configure(show=show_char)

    def on_connect(self):
        if self.busy:
            return
        host = self.var_host.get().strip()
        if not host:
            messagebox.showwarning("提示", "请填写 IP 或域名")
            return
        user = self.var_user.get().strip()
        password = self.var_password.get()
        if not user or not password:
            messagebox.showwarning("提示", "请填写登录用户和密码")
            return

        try:
            if self.var_scheme.get() == "HTTPS":
                port = int(self.var_https_port.get())
            else:
                port = int(self.var_http_port.get())
        except ValueError:
            messagebox.showwarning("提示", "端口必须是数字")
            return

        self._save_config()
        self._set_busy(True)
        self.var_status.set("连接中...")
        self._append_log("── 开始连接 %s ──" % host)

        headless = self.var_headless.get()

        def worker():
            try:
                # 显示模式必须在浏览器启动前设定。
                # 若浏览器已在运行（比如上一次连接失败残留），切换无效但不该阻断连接。
                try:
                    self.service.set_headless(headless)
                except RuntimeError as ex:
                    self.ui_queue.put(
                        ("log", "提示：%s（将继续用当前模式连接）" % ex))
                self.service.start()
                self.service.connect(host, port,
                                     self.var_scheme.get() == "HTTPS",
                                     user, password)
                self.ui_queue.put(("connected", None))
            except Exception as ex:
                self.ui_queue.put(("busy", False))
                self.ui_queue.put(("status", "连接失败"))
                self.ui_queue.put(("connect_failed", str(ex)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_headless_toggle(self):
        """
        显示模式切换。

        浏览器已启动时无法切换（Chromium 不支持运行中改显示状态），
        此时把勾选框恢复成实际生效的值，并给出提示，避免「界面骗人」。
        """
        want = self.var_headless.get()
        if self.service.browser_started:
            actual = self.service.headless
            if want != actual:
                self.var_headless.set(actual)
                mode = "隐藏" if actual else "显示"
                messagebox.showinfo(
                    "提示",
                    "浏览器已在运行，无法切换显示模式。\n\n"
                    "当前为「%s浏览器窗口」，将在下次连接时生效。\n"
                    "如需立即切换，请先点「断开」再重新连接。" % mode)
            return
        try:
            self.service.set_headless(want)
        except RuntimeError:
            pass

    def _on_connected_ok(self):
        self._set_busy(False)
        self.var_status.set("已连接")
        self.lbl_status.configure(foreground="#1b5e20")
        self._append_log("[连接成功] 正在获取 AP 列表...")
        # 连接后立刻拉取列表
        self.on_refresh()
        # 自动刷新按勾选框来（用户可能不想让它一直刷）
        self.auto_refresh = self.var_auto_refresh.get()
        if self.auto_refresh:
            self._schedule_refresh()

    def _on_connect_failed(self, msg: str):
        self._set_busy(False)
        self.var_status.set("连接失败")
        self.lbl_status.configure(foreground="#c62828")
        self._append_log("[连接失败] %s" % msg)
        messagebox.showerror("连接失败", msg)

    def on_disconnect(self):
        if self.busy:
            return
        self._cancel_refresh()
        self._cancel_monitor()
        self.monitor_targets = {}
        self.ap_list = []
        self._render_sig = {}
        self._refresh_inflight = False
        self._render_aps([])
        self._hide_row_buttons()
        self.var_status.set("正在断开...")
        self._append_log("正在断开连接...")
        self._set_busy(True)

        def worker():
            # 真正关闭浏览器，这样：
            #   1. 显示模式可以重新切换（下次连接生效）
            #   2. 释放 Chromium 进程和内存
            #   3. 下次连接是全新会话，不会残留登录态
            try:
                self.service.stop()
            except Exception as ex:
                self.ui_queue.put(("log", "关闭浏览器时出错：%s" % ex))
            self.ui_queue.put(("disconnected", None))

        threading.Thread(target=worker, daemon=True).start()

    def _on_disconnected(self):
        self._set_busy(False)
        self.var_status.set("未连接")
        self.lbl_status.configure(foreground="#c62828")
        self._append_log("已断开连接")

    def on_refresh(self, quiet: bool = False):
        """
        读取一次 AP 列表（非阻塞、防重入）。

        【为什么不再 _set_busy(True)】
        busy 会把「批量重启」按钮置灰。而列表每次读取要 2~3 秒，在自动刷新
        开着的时候按钮几乎一直是灰的 —— 用户根本点不到，这是"批量重启
        按键不能使用"的直接原因。

        所以这里把「列表读取」和「busy（禁止并发操作）」彻底解耦：
          - 列表读取用独立的 _refresh_inflight 标志防重入
          - 只有真正会改设备状态的操作（重启）才走 busy

        quiet=True（后台自动刷新）时不把状态栏打成"读取中..." ——
        否则右上角会在"读取中"和"已连接·N 台 AP"之间来回跳，
        正是用户反馈的"不断切换"。后台刷新静默进行即可。
        """
        if self._refresh_inflight:
            return
        if not self.service.connected:
            self._append_log("请先连接路由器")
            return

        self._refresh_inflight = True
        if not quiet:
            self.var_status.set("读取中...")
            self.lbl_status.configure(foreground="#f9a825")

        def worker():
            try:
                aps = self.service.fetch_ap_list()
                self.ui_queue.put(("aps", aps))
            except NotConnectedError as ex:
                self.ui_queue.put(("status", "未连接"))
                self.ui_queue.put(("log", "未连接：%s" % ex))
            except Exception as ex:
                self.ui_queue.put(("log", "刷新失败：%s" % ex))
                self.ui_queue.put(("status", "刷新失败"))
            finally:
                self.ui_queue.put(("refresh_done", None))

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- 状态文案 ----------------
    def _status_text(self) -> str:
        """统一生成右上角状态文案，避免各处格式不一致。"""
        n = len(self.ap_list)
        if getattr(self, "_just_recovered", False):
            return "已连接 · 全部恢复（%d 台 AP）" % n
        if self.monitor_targets:
            # waiting = 还没掉线；seen_offline = 已掉线、等它回来
            pending = sum(1 for v in self.monitor_targets.values()
                          if v.get("state") in ("waiting", "seen_offline"))
            if pending:
                return "重启中，等待 %d 台恢复..." % pending
            return "已连接 · 全部恢复"
        return "已连接 · %d 台 AP" % n

    def _set_connected_status(self):
        self.var_status.set(self._status_text())
        self.lbl_status.configure(foreground="#1b5e20")
        # "全部恢复"只显示一次，随后的普通刷新就回到常规文案
        self._just_recovered = False

    # ---------------- 渲染（差量更新） ----------------
    def _row_values(self, ap: APInfo):
        """算出某一行在表格里应该显示的值 + 样式标签。

        状态文案的优先级：监视中 > 离线 > 设备自身状态。

        注意「操作」列的值是**空的**：真正的重启按钮是浮在表格上方的
        真实 ttk.Button（见 _sync_row_buttons）。如果这里还写"重启"，
        文字会和按钮重叠，看起来像两层字。
        """
        status_txt = ap.status or "未知"
        tag = ()

        info = self.monitor_targets.get(ap.key())
        if info is not None and info.get("state") in ("waiting", "seen_offline"):
            # seen_offline 时设备确实掉线了，但仍处于"重启流程"中，
            # 文案保持"重启中"，不要显示成"断开"吓用户。
            status_txt = "重启中"
            tag = ("restarting",)
        elif info is not None:
            status_txt = "已连接"
        elif not ap.is_alive():
            status_txt = "断开"
            tag = ("offline",)

        return (ap.group, ap.name, ap.model, ap.ip,
                ap.mac, ap.uptime, status_txt, ap.comment or "", ""), tag

    # ---------------- 列排序 ----------------
    # 排序字段取值（空值统一处理，避免 None 比较崩溃）
    @staticmethod
    def _sort_value(ap: APInfo, col: str):
        """取某列的排序键。空 IP/MAC 统一放最后，不受升降序影响。"""
        raw = {"group": ap.group, "name": ap.name, "model": ap.model,
               "ip": ap.ip, "mac": ap.mac, "uptime": ap.uptime,
               "status": ap.status, "comment": ap.comment}.get(col, "")
        return (raw or "").strip()

    @staticmethod
    def _is_empty_cell(v: str) -> bool:
        return v in ("", "- -", "--", "-", "—", "－", "未知")

    @classmethod
    def _ip_sort_key(cls, v: str):
        """IP 按数值排序：'192.168.9.9' < '192.168.9.10'。
        解析失败（非 IP 文本）退回字符串，且排在所有真 IP 之后。"""
        s = (v or "").strip()
        try:
            return (0, int(ipaddress.ip_address(s)))
        except ValueError:
            return (1, s)

    @classmethod
    def _uptime_sort_key(cls, v: str):
        """运行时间转秒数排序：'3天7时7分' -> 276420。
        解析失败退回 -1（排最前，方便人工识别异常值）。"""
        s = (v or "").strip()
        total = 0
        matched = False
        for num, unit in re.findall(r"(\d+)\s*(天|日|时|小时|分|分钟|秒)", s):
            matched = True
            n = int(num)
            if unit in ("天", "日"):
                total += n * 86400
            elif unit in ("时", "小时"):
                total += n * 3600
            elif unit in ("分", "分钟"):
                total += n * 60
            else:
                total += n
        if not matched:
            return -1
        return total

    def _sorted_ap_list(self, aps: list[APInfo]) -> list[APInfo]:
        """按当前排序状态整理列表；未排序时原样返回（保持设备原始顺序）。

        空值（离线 AP 的空 IP / '- -' 等）**无论升降序都固定排在最后**：
        sorted(reverse=True) 会连垫底标志一起反转，降序时空值会蹿到最前，
        所以这里拆成两段：先排非空行，空行固定追加在尾部。
        """
        col = self.sort_col
        if not col:
            return aps
        desc = self.sort_desc

        def row_key(ap):
            v = self._sort_value(ap, col)
            if col == "ip":
                part = self._ip_sort_key(v)
            elif col == "uptime":
                part = self._uptime_sort_key(v)
            else:
                part = v.lower()
            return (part, ap.key())

        non_empty = [a for a in aps
                     if not self._is_empty_cell(self._sort_value(a, col))]
        empty = [a for a in aps
                 if self._is_empty_cell(self._sort_value(a, col))]
        return sorted(non_empty, key=row_key, reverse=desc) + empty

    def _update_heading_arrows(self):
        """在表头文字上标注当前排序列与方向（↓ 降序 / ↑ 升序）。"""
        base = {"group": "分组", "name": "名称", "model": "型号",
                "ip": "终端IP", "mac": "终端MAC", "uptime": "运行时间",
                "status": "运行状态", "comment": "备注", "action": "操作"}
        for col, text in base.items():
            if col == self.sort_col:
                arrow = "↓" if self.sort_desc else "↑"
                self.tree.heading(col, text="%s %s" % (text, arrow))
            else:
                self.tree.heading(col, text=text)

    def _on_heading_click(self, col: str):
        """点击表头：第一次降序，再点升序；点其他列重置为降序。"""
        if col == self.sort_col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col = col
            self.sort_desc = True          # 用户要求：首点降序
        self._update_heading_arrows()
        self._render_aps(self._sorted_ap_list(list(self.ap_list)))

    def _render_aps(self, aps: list[APInfo]):
        """
        按「差量」更新表格，不再每次清空重建。

        【为什么必须改】
        原来每个刷新周期都 `delete` 全部行再 `insert` 一遍，导致：
          - 用户正在选的设备，下一次刷新就被"取消选中"
          - 列表视觉上不停闪动
        改成：先按 iid 对账，只改内容真的变了的单元格；
        行数或顺序变化时才做最小化的增删/移动。
        """
        self.ap_list = aps

        # 刷新后保持用户的排序选择（未排序时保持设备原始顺序）
        if self.sort_col:
            aps = self._sorted_ap_list(aps)

        # 监视中的 AP 是否已恢复（会更新 monitor_targets）
        self._check_monitor(aps)

        # ---- 1. 先清理掉本周期已被移除的设备 ----
        incoming = [ap.key() or ap.name for ap in aps]
        keep = set(incoming)
        for iid in self.tree.get_children():
            if iid not in keep:
                self.tree.delete(iid)
                self._render_sig.pop(iid, None)

        # ---- 2. 保证顺序与数量一致 ----
        for pos, ap in enumerate(aps):
            iid = ap.key() or ap.name
            children = self.tree.get_children()
            if pos >= len(children):
                self.tree.insert("", END, iid=iid, values=self._row_values(ap)[0],
                                 tags=self._row_values(ap)[1])
                self._render_sig.pop(iid, None)
            elif children[pos] != iid:
                if self.tree.exists(iid):
                    # 已存在但位置不对 -> 移动，避免删了再建（保住选中状态）
                    self.tree.move(iid, "", pos)
                else:
                    self.tree.insert("", pos, iid=iid,
                                     values=self._row_values(ap)[0],
                                     tags=self._row_values(ap)[1])
                    self._render_sig.pop(iid, None)

        # ---- 3. 只更新内容发生变化的行 ----
        changed = 0
        for ap in aps:
            iid = ap.key() or ap.name
            if not self.tree.exists(iid):
                continue
            values, tag = self._row_values(ap)
            sig = (tuple(values), tag)
            if self._render_sig.get(iid) == sig:
                continue          # 完全没变，一个字节都不动
            self.tree.item(iid, values=values, tags=tag)
            self._render_sig[iid] = sig
            changed += 1

        self._row_keys = incoming
        if changed:
            self._append_log("界面更新 %d 行" % changed)

        # ---- 4. 同步操作列的浮层「重启」按钮 ----
        # 表格容器布局可能还没算完（首次渲染时 place 会用错坐标），
        # 所以让 Tk 先处理一轮几何，再贴按钮。
        try:
            self.tree.update_idletasks()
        except tk.TclError:
            return
        self._sync_row_buttons()

    def _on_autorefresh_toggle(self):
        """自动刷新开关：关掉后立即停止轮询，让列表静止下来。"""
        self.auto_refresh = self.var_auto_refresh.get()
        if self.auto_refresh:
            self._schedule_refresh()
            self._append_log("已开启自动刷新（%s）" % self.var_refresh.get())
        else:
            self._cancel_refresh()
            self._append_log("已停止自动刷新，列表已静止（可放心勾选）")

    def _on_refresh_change(self, _evt=None):
        if self.auto_refresh:
            self._cancel_refresh()
            self._schedule_refresh()

    def _schedule_refresh(self):
        """按选定的间隔安排下一次刷新。"""
        if not self.auto_refresh:
            return
        seconds = int(self.var_refresh.get().rstrip("s"))
        self.refresh_job = self.root.after(seconds * 1000, self._auto_refresh_tick)

    def _cancel_refresh(self):
        if self.refresh_job:
            try:
                self.root.after_cancel(self.refresh_job)
            except Exception:
                pass
            self.refresh_job = None

    def _auto_refresh_tick(self):
        if not self.auto_refresh:
            return
        # 上一次还没读完就跳过本轮，避免请求越堆越多（这也是后期
        # 刷新越来越慢、最后读到 0 台的一个重要诱因）
        # quiet=True：后台静默刷新，不闪状态栏
        if not self._refresh_inflight and self.service.connected:
            self.on_refresh(quiet=True)
        self._schedule_refresh()

    # ==============================================================
    # 重启操作
    # ==============================================================
    def _on_show_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _copy_mac(self):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        if len(vals) >= 5:
            self.root.clipboard_clear()
            self.root.clipboard_append(vals[4])
            self._append_log("已复制 MAC: %s" % vals[4])

    def _open_output_dir(self):
        try:
            webbrowser.open(OUTPUT_DIR.as_uri())
        except Exception:
            self._append_log("截图目录: %s" % OUTPUT_DIR)

    def _show_config_path(self):
        """显示配置文件位置，并说明密码是加密的。"""
        p = ikuai_config.config_path()
        exists = "已存在" if p.exists() else "尚未生成"
        msg = (
            "配置文件位置：\n%s\n\n状态：%s\n\n"
            "密码字段经 AES-256 加密后存储，\n"
            "密钥由本机特征派生，拷贝到其他机器无法解密。"
            % (p, exists)
        )
        messagebox.showinfo("配置文件", msg)
        self._append_log("配置文件: %s（%s）" % (p, exists))

    def _set_cursor(self, name: str):
        try:
            self.tree.configure(cursor=name)
        except Exception:
            pass

    def _on_tree_motion(self, event):
        """
        鼠标划过表格时，若停在「操作」列就把光标换成手型。

        这是本工具能给用户的最直接的"可点击"提示 —— Treeview 单元格
        没法画成真正的按钮，手型光标是最低成本的可用性补偿。
        """
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        want = ""
        if row:
            try:
                n = len(self.tree["columns"])
                idx = int(str(col).lstrip("#"))
                # 操作列（最后一列）与备注列（倒数第二列，双击可改）给手型
                if idx in (n, n - 1):
                    want = "hand2"
            except ValueError:
                pass
        if getattr(self, "_cur_cursor", None) != want:
            self._cur_cursor = want
            self._set_cursor(want)

    def _on_tree_click(self, event):
        """
        处理表格单击。

        用途：点「操作」列里的"重启"文字 = 重启这一行对应的 AP。

        【为什么需要它】
        Tkinter 的 Treeview 单元格里放不了真实控件，所以"重启"只是
        该列的一段文字，不会自己响应点击。之前只绑了 <Double-1>，
        但双击会先触发选中、再触发重启，用户按直觉单击那两个字时
        毫无反应。这里显式判断列号，让单击"重启"就能用。

        列号：#1 起算，操作列是最后一列（第 8 列）。
        """
        # 只处理左键
        if getattr(event, "num", 1) != 1:
            return
        row = self.tree.identify_row(event.y)
        if not row:
            return
        col = self.tree.identify_column(event.x)     # 形如 "#8"
        try:
            col_idx = int(col.lstrip("#"))
        except ValueError:
            return

        n_cols = len(self.tree["columns"])
        if col_idx != n_cols:                        # 只在最后一列（操作）响应
            return

        # 说明：操作列的单元格内容现在是空的（真正的按钮是浮层控件），
        # 所以这里不再校验单元格文字，点这一列的空白处也等同点按钮。

        # 找到对应的 AP 并直接重启这一台
        key = row
        target = None
        for ap in self.ap_list:
            if (ap.key() or ap.name) == key:
                target = ap
                break
        if target is None:
            return

        # 防抖：极快的连点/双击会在同一瞬间投递多次 <Button-1>，
        # 上一句的 _confirm_and_restart 又会弹出模态框，这里再兜一层，
        # 保证短时间内只弹一次确认框。
        now = time.time()
        if now - getattr(self, "_last_action_click", 0.0) < 0.8:
            return
        self._last_action_click = now

        self._confirm_and_restart([target])

    def _on_row_double_click(self, event):
        """
        双击处理。

        - 双击**备注列** -> 打开「修改备注」对话框（保存回路由器）；
        - 双击**操作列** -> 跳过（单击处理已触发过确认框，避免连环弹）；
        - 双击其他列 -> 按"重启选中"处理（原有行为）。
        """
        row = self.tree.identify_row(event.y)
        if not row:
            return
        col = self.tree.identify_column(event.x)
        try:
            col_idx = int(str(col).lstrip("#"))
        except ValueError:
            col_idx = -1

        n_cols = len(self.tree["columns"])
        if col_idx == n_cols:
            return          # 操作列：交给单击处理，避免重复弹框
        if col_idx == n_cols - 1:
            # 备注列（倒数第二列）：双击 = 修改备注
            target = None
            for ap in self.ap_list:
                if (ap.key() or ap.name) == row:
                    target = ap
                    break
            if target is not None:
                self._edit_comment(target)
            return

        self.tree.selection_set(row)
        self.on_restart_selected()

    # ---------------- 修改备注 ----------------
    def _edit_comment(self, ap: APInfo):
        """弹出输入框修改备注，确认后回写到路由器 AP 终端备注。"""
        if self.busy:
            messagebox.showinfo("提示", "当前有任务在执行，请稍后再试")
            return

        new_val = simpledialog.askstring(
            "修改备注",
            "AP：%s\nMAC：%s\n\n备注（最多 64 个字符）：" % (ap.name, ap.mac),
            initialvalue=ap.comment or "",
            parent=self.root)
        if new_val is None:
            return                      # 用户点了取消
        new_val = new_val.strip()
        if new_val == (ap.comment or "").strip():
            self._append_log("%s 的备注未变化，跳过保存" % ap.name)
            return
        if len(new_val) > 64:
            messagebox.showwarning(
                "备注过长",
                "备注最多 64 个字符（当前 %d 个），请精简后再保存。"
                % len(new_val))
            return

        self._append_log("开始保存 %s 的备注：%s" % (ap.name, new_val or "（清空）"))
        self._set_busy(True)
        self.var_status.set("正在保存备注...")

        def worker():
            try:
                ok = self.service.set_ap_comment(ap, new_val)
                self.ui_queue.put(("comment_done",
                                   (ap.key() or ap.name, ok, new_val)))
            except Exception as ex:
                self._queue_log("保存备注失败：%s" % ex)
                self.ui_queue.put(("comment_done", (ap.key() or ap.name,
                                                    False, new_val)))
            finally:
                self.ui_queue.put(("busy_done", None))

        threading.Thread(target=worker, daemon=True).start()

    def _on_comment_done(self, key: str, ok: bool, new_val: str):
        """后台保存备注完成（UI 线程执行）。"""
        if ok:
            self._append_log("备注已保存：%s" % (new_val or "（空）"))
            # 本地立即更新（不等下一轮刷新），界面即时反馈
            for ap in self.ap_list:
                if (ap.key() or ap.name) == key:
                    ap.comment = new_val
                    break
            # 触发一次差量渲染（只重写受影响的行）
            self._render_aps(list(self.ap_list))
        else:
            self._append_log("备注保存失败，请查看日志")

    def _selected_aps(self) -> list[APInfo]:
        """返回当前选中的 AP 对象。"""
        keys = set(self.tree.selection())
        return [a for a in self.ap_list if (a.key() or a.name) in keys]

    def on_restart_selected(self):
        targets = self._selected_aps()
        if not targets:
            messagebox.showinfo("提示", "请先选中要重启的 AP")
            return
        self._confirm_and_restart(targets)

    def on_batch_restart(self):
        if not self.service.connected:
            messagebox.showinfo("提示", "请先连接路由器")
            return
        if not self.ap_list:
            messagebox.showinfo("提示", "AP 列表为空，请先刷新")
            return
        self._confirm_and_restart(list(self.ap_list))

    def _confirm_and_restart(self, targets: list[APInfo]):
        if self.busy:
            return
        names = "\n".join("  · %s (%s)" % (a.name, a.ip) for a in targets[:12])
        if len(targets) > 12:
            names += "\n  ... 以及另外 %d 台" % (len(targets) - 12)

        ok = messagebox.askyesno(
            "确认重启",
            "即将重启以下 %d 台 AP：\n\n%s\n\n"
            "重启期间连接到这些 AP 的无线终端会短暂断开。\n"
            "确定继续吗？" % (len(targets), names),
            icon="warning")
        if not ok:
            return

        self._set_busy(True)
        self.var_status.set("重启中...")
        self._append_log("── 开始重启 %d 台 AP ──" % len(targets))

        def worker():
            try:
                ok_count, total = self.service.restart_aps(
                    targets,
                    progress_cb=lambda nm, i, t: self.ui_queue.put(
                        ("log", "  [%d/%d] 处理 %s" % (i, t, nm))))
                self.ui_queue.put(("batch_done", {
                    "ok": ok_count, "total": total, "targets": targets}))
            except Exception as ex:
                self.ui_queue.put(("log", "批量重启出错：%s" % ex))
                self.ui_queue.put(("batch_done", {
                    "ok": 0, "total": len(targets), "targets": targets}))
            finally:
                self.ui_queue.put(("busy", False))

        threading.Thread(target=worker, daemon=True).start()

    def _on_restart_done(self, name: str, ok: bool):
        self._append_log("%s %s" % ("[重启成功]" if ok else "[重启失败]", name))

    def _on_batch_done(self, ok: int, total: int, targets: list):
        self._append_log("── 批量重启完成：成功 %d / %d 台 ──" % (ok, total))
        if ok > 0:
            # 进入监视模式：观察这些 AP 何时恢复
            self.monitor_targets = {}
            for ap in targets:
                self.monitor_targets[ap.key()] = {"state": "waiting",
                                                  "ip": ap.ip,
                                                  "name": ap.name}
            self._append_log("已进入监视状态，将持续检测设备恢复（断开 -> 已连接）")
            self.var_status.set(self._status_text())
            # 监视是独立于"自动刷新"的：即使关了自动刷新，也必须能
            # 看到重启结果，否则用户无法确认设备是否恢复。
            self._schedule_monitor()
        else:
            self._set_connected_status()

    # ---------------- 监视（重启后等待恢复） ----------------
    def _schedule_monitor(self):
        """安排下一次监视探测。与自动刷新互相独立。"""
        if not self.monitor_targets:
            return
        self._cancel_monitor()
        seconds = int(self.var_refresh.get().rstrip("s"))
        self.monitor_job = self.root.after(seconds * 1000, self._monitor_tick)

    def _cancel_monitor(self):
        if self.monitor_job:
            try:
                self.root.after_cancel(self.monitor_job)
            except Exception:
                pass
            self.monitor_job = None

    def _monitor_tick(self):
        if not self.monitor_targets:
            return
        # 监视也走静默刷新，状态栏由 _status_text() 统一给出
        if not self._refresh_inflight and self.service.connected:
            self.on_refresh(quiet=True)
        self._schedule_monitor()

    def _check_monitor(self, aps: list[APInfo]):
        """
        检查监视中的 AP 是否已恢复。

        【状态机】waiting -> seen_offline -> recovered

        为什么不能"一看到在线就算恢复"：
        重启指令下发后，设备要过几秒才真正掉线，而刷新间隔恰好是
        2~5 秒。于是**第一轮探测几乎必然还能读到 IP**，如果直接判定
        恢复，界面会立刻显示"已恢复"，用户以为重启没生效。
        所以必须先亲眼看到它掉线（is_alive() 为 False）一次，
        之后再次读到在线，才算真正恢复。
        """
        if not self.monitor_targets:
            return

        newly_offline = []
        newly_recovered = []
        for ap in aps:
            k = ap.key()
            info = self.monitor_targets.get(k)
            if info is None:
                continue
            state = info.get("state")

            if state == "waiting":
                if not ap.is_alive():
                    # 第一次看到掉线：重启确实生效了
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

        # 全部恢复后退出监视模式
        if newly_recovered and all(
                v.get("state") == "recovered"
                for v in self.monitor_targets.values()):
            self._append_log("[完成] 所有目标 AP 已恢复")
            self.monitor_targets = {}
            self._cancel_monitor()
            # 用一个短暂标志让状态栏能显示"全部恢复"，
            # 但监视字典已清空（下次刷新即恢复普通文案）。
            self._just_recovered = True
            self._append_log("已退出监视状态")

    # ==============================================================
    # 关闭
    # ==============================================================
    def on_close(self):
        """
        关闭窗口。

        【为什么不能在这里直接 service.stop()】
        service.stop() 会去关 Chromium，实测可能要好几秒（极端情况十几秒）。
        它内部是投递到浏览器工作线程再等待的，如果直接在 UI 线程调用，
        整个窗口会卡死不动 —— 用户看到的就是"关闭时卡死/无响应"，
        甚至被 Windows 判定为程序无响应而弹框。

        正确做法：
          1. 先把所有 UI 层面的定时器和队列轮询停掉（避免继续产生回调）
          2. 立刻销毁窗口，让用户感觉"秒退"
          3. 浏览器在后台线程里关，关完进程自然退出
        """
        if self._closing:
            return
        self._closing = True

        # 停掉所有 UI 定时器
        self.auto_refresh = False
        try:
            self._hide_row_buttons()
        except Exception:
            pass
        for cancel in (self._cancel_refresh, self._cancel_monitor):
            try:
                cancel()
            except Exception:
                pass
        if self._pump_job is not None:
            try:
                self.root.after_cancel(self._pump_job)
            except Exception:
                pass
            self._pump_job = None

        # 保存配置（纯本地文件，很快）
        try:
            self._save_config()
        except Exception:
            pass

        # 浏览器在后台关，不阻塞窗口关闭
        def _shutdown():
            try:
                self.service.stop()
            except Exception:
                pass

        threading.Thread(target=_shutdown, daemon=True,
                         name="ikuai-shutdown").start()

        # 立刻销毁窗口
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main():
    root = Tk()
    app = IKuaiGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
