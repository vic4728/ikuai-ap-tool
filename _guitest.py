#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GUI 冒烟测试（离屏，不连真机）—— 验证控件、列、状态机全链路。"""
import sys
import tkinter as tk
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ikuai_gui import IKuaiGUI
import ikuai_gui
from ikuai_service import APInfo

OUT = Path(__file__).resolve().parent / "output"
OUT.mkdir(exist_ok=True)
LOG = OUT / "gui_test.txt"
lines = []


def say(s=""):
    lines.append(str(s))


def check(label, cond, extra=""):
    say("   %-34s %s  %s" % (label, "PASS" if cond else "FAIL", extra))
    return cond


all_ok = True

say("=" * 64)
say("GUI SMOKE TEST (offscreen)  -- final")
say("=" * 64)
say()

root = tk.Tk()
root.withdraw()
app = IKuaiGUI(root)
say("GUI 构建成功")
say()

# ---------- 1. 表格列 ----------
say("[1] 表格列定义")
cols = app.tree["columns"]
all_ok &= check("columns 元组", cols == ("group", "name", "model", "ip",
                                          "mac", "uptime", "status",
                                          "comment", "action"),
                str(cols))
for col, expect in [("group", "分组"), ("name", "名称"), ("model", "型号"),
                    ("ip", "终端IP"), ("mac", "终端MAC"),
                    ("uptime", "运行时间"), ("status", "运行状态"),
                    ("comment", "备注"), ("action", "操作")]:
    got = app.tree.heading(col)["text"]
    all_ok &= check("%s heading" % col, got == expect, "got=%r" % got)
say()

# ---------- 2. 控件存在性 ----------
say("[2] 控件存在性")
for attr, label in [("btn_connect", "连接按钮"), ("btn_disconnect", "断开按钮"),
                    ("btn_refresh", "刷新按钮"), ("btn_batch", "批量重启按钮"),
                    ("cmb_refresh", "刷新时间下拉"), ("_pwd_entry", "密码框"),
                    ("tree", "表格"), ("txt_log", "日志框")]:
    all_ok &= check(label, hasattr(app, attr))
all_ok &= check("刷新选项", app.cmb_refresh["values"] == ("1s", "3s", "5s", "10s"),
                str(app.cmb_refresh["values"]))
say()

# ---------- 3. 渲染在线 + 离线数据 ----------
say("[3] 渲染 AP 列表")
def mk(name, mac, ip, uptime, status):
    a = APInfo(name=name, mac=mac, ip=ip, uptime=uptime, status=status)
    a.group, a.model = "VIC", "IK-H17V2"
    return a

fake = [
    mk("IK-H17V2_a482", "08:9b:4b:47:a4:82", "192.168.50.111", "14天22时", "已连接"),
    mk("IK-H17V2_b111", "08:9b:4b:47:b1:11", "- -", "- -", "已连接"),
]
app.service._connected = True
app._render_aps(fake)
rows = app.tree.get_children()
all_ok &= check("渲染行数 == 2", len(rows) == 2, "got=%d" % len(rows))
v0 = app.tree.item(rows[0], "values")
v1 = app.tree.item(rows[1], "values")
all_ok &= check("在线行 状态=已连接", v0[6] == "已连接", "got=%r" % v0[6])
all_ok &= check("离线行 状态=断开", v1[6] == "断开", "got=%r" % v1[6])
all_ok &= check("离线行 有 offline 标签",
                "offline" in app.tree.item(rows[1], "tags"),
                str(app.tree.item(rows[1], "tags")))
all_ok &= check("iid 用 MAC 归一化", rows[0] == "089b4b47a482", rows[0])
say()

# ---------- 4. 监视状态机：重启中 -> 断开 -> 已连接 ----------
say("[4] 监视状态机（重启后 断开 -> 已连接）")
target = fake[0]
app.monitor_targets = {target.key(): {"state": "waiting",
                                       "ip": target.ip, "name": target.name}}

# 4.1 重启中：设备已下线（IP = - -），仍在监视 -> 显示"重启中"
offline_version = mk(target.name, target.mac, "- -", "- -", "已连接")
app._render_aps([offline_version])
rows = app.tree.get_children()
v = app.tree.item(rows[0], "values")
all_ok &= check("阶段1 显示 '重启中'", v[6] == "重启中", "got=%r" % v[6])
all_ok &= check("阶段1 有 restarting 标签",
                "restarting" in app.tree.item(rows[0], "tags"),
                str(app.tree.item(rows[0], "tags")))
all_ok &= check("阶段1 监视仍在", len(app.monitor_targets) == 1,
                "len=%d" % len(app.monitor_targets))
say()

# 4.2 已恢复：设备重新上线 -> 显示"已连接"，退出监视
# 注意：_render_aps 现在只改表格，不再顺手写状态栏（那是"频繁刷新"的来源之一）。
# 状态栏由 _handle_message("aps") 在渲染后统一刷新，这里显式调一次。
recovered = mk(target.name, target.mac, "192.168.50.111", "0天0时1分", "已连接")
app._render_aps([recovered])
app._set_connected_status()
rows = app.tree.get_children()
v = app.tree.item(rows[0], "values")
all_ok &= check("阶段2 显示 '已连接'", v[6] == "已连接", "got=%r" % v[6])
all_ok &= check("阶段2 无 offline 标签",
                "offline" not in app.tree.item(rows[0], "tags"),
                str(app.tree.item(rows[0], "tags")))
all_ok &= check("阶段2 监视已清空", len(app.monitor_targets) == 0,
                "len=%d" % len(app.monitor_targets))
all_ok &= check("阶段2 状态栏文案", "全部恢复" in app.var_status.get(),
                app.var_status.get())
say()

# ---------- 5. 多目标：部分恢复不应退出监视 ----------
say("[5] 多目标部分恢复")
a1 = mk("AP-1", "aa:aa:aa:aa:aa:01", "1.1.1.1", "1天", "已连接")
a2 = mk("AP-2", "aa:aa:aa:aa:aa:02", "1.1.1.2", "1天", "已连接")
app.monitor_targets = {a1.key(): {"state": "waiting", "ip": a1.ip, "name": a1.name},
                       a2.key(): {"state": "waiting", "ip": a2.ip, "name": a2.name}}
# 先让两台都掉线（重启生效），此时都只是 seen_offline，不算恢复
app._render_aps([mk("AP-1", "aa:aa:aa:aa:aa:01", "- -", "- -", "已连接"),
                 mk("AP-2", "aa:aa:aa:aa:aa:02", "- -", "- -", "已连接")])
all_ok &= check("两台都掉线时仍监视", len(app.monitor_targets) == 2,
                "len=%d" % len(app.monitor_targets))
all_ok &= check("AP-1 掉线 -> seen_offline",
                app.monitor_targets[a1.key()]["state"] == "seen_offline",
                app.monitor_targets[a1.key()]["state"])
# 只有 AP-1 回来 -> AP-1 恢复，AP-2 还没回来，监视必须保留
app._render_aps([a1, mk("AP-2", "aa:aa:aa:aa:aa:02", "- -", "- -", "已连接")])
all_ok &= check("仅 1 台恢复时仍监视", len(app.monitor_targets) == 2,
                "len=%d" % len(app.monitor_targets))
all_ok &= check("AP-1 标记 recovered",
                app.monitor_targets[a1.key()]["state"] == "recovered",
                app.monitor_targets[a1.key()]["state"])
# AP-2 也回来 -> 全部恢复
app._render_aps([a1, a2])
all_ok &= check("全部恢复后清空监视", len(app.monitor_targets) == 0,
                "len=%d" % len(app.monitor_targets))
say()

# ---------- 5.5 列表差量更新（不能每次清空重建） ----------
say("[5.5] 列表差量更新（用户反馈：列表一直闪、选不中设备）")
b1 = mk("AP-A", "bb:bb:bb:bb:bb:01", "2.2.2.1", "1天", "已连接")
b2 = mk("AP-B", "bb:bb:bb:bb:bb:02", "2.2.2.2", "1天", "已连接")
b3 = mk("AP-C", "bb:bb:bb:bb:bb:03", "2.2.2.3", "1天", "已连接")
app.monitor_targets = {}
app._render_sig = {}
app._render_aps([b1, b2, b3])
rows0 = app.tree.get_children()
all_ok &= check("首次渲染 3 行", len(rows0) == 3, "len=%d" % len(rows0))

# 内容完全没变 -> 不应有任何重写
app._render_aps([b1, b2, b3])
all_ok &= check("内容未变时不重写行（无闪动）",
                app.tree.get_children() == rows0,
                "rows changed")

# 只有 1 台状态变了 -> 其他行的 iid 必须保持不变（选中才不会丢）
b2_off = mk("AP-B", "bb:bb:bb:bb:bb:02", "- -", "- -", "已连接")
app.tree.selection_set(b1.key())          # 模拟用户选中第一行
app._render_aps([b1, b2_off, b3])
rows1 = app.tree.get_children()
all_ok &= check("行顺序与 iid 稳定", rows1 == rows0, str(rows1))
all_ok &= check("单台状态变化不影响选中",
                app.tree.selection() == (b1.key(),),
                str(app.tree.selection()))
v_b = app.tree.item(b2.key(), "values")
all_ok &= check("变化的那台已更新为断开", v_b[6] == "断开", "got=%r" % v_b[6])
all_ok &= check("变化行带 offline 标签",
                "offline" in app.tree.item(b2.key(), "tags"),
                str(app.tree.item(b2.key(), "tags")))

# 设备消失 -> 只删一行
app._render_aps([b1, b3])
all_ok &= check("设备离线消失后只删该行",
                len(app.tree.get_children()) == 2,
                "len=%d" % len(app.tree.get_children()))
all_ok &= check("消失的行确实没了", not app.tree.exists(b2.key()))
say()

# ---------- 5.6 读取不再禁用批量重启按钮 ----------
say("[5.6] 读取期间批量重启按钮必须可用（用户反馈：点不动）")
app._set_busy(False)
all_ok &= check("空闲时批量重启可用",
                str(app.btn_batch["state"]) != "disabled",
                str(app.btn_batch["state"]))
# 模拟"读取中"：只置 _refresh_inflight，不再走 busy
app._refresh_inflight = True
all_ok &= check("读取中批量重启仍可用",
                str(app.btn_batch["state"]) != "disabled",
                str(app.btn_batch["state"]))
app._refresh_inflight = False

# 自动刷新开关
app.var_auto_refresh.set(False)
app._on_autorefresh_toggle()
all_ok &= check("关掉自动刷新后 auto_refresh=False",
                app.auto_refresh is False)
app.var_auto_refresh.set(True)
app._on_autorefresh_toggle()
all_ok &= check("打开自动刷新后 auto_refresh=True",
                app.auto_refresh is True)
app.auto_refresh = False
app._cancel_refresh()
say()

# ---------- 6. 配置读写（AES 加密存储） ----------
say("[6] 配置读写（AES 加密）")
import json as _json
import ikuai_config
from ikuai_gui import CONFIG_FILE

app.var_host.set("10.0.0.9")
app.var_http_port.set("8080")
app.var_user.set("tester")
app.var_password.set("secret")
app.var_remember.set(True)
app.var_headless.set(True)
app._save_config()

raw = CONFIG_FILE.read_text(encoding="utf-8")
all_ok &= check("配置文件已生成", CONFIG_FILE.exists(), CONFIG_FILE.name)
all_ok &= check("配置在程序目录内",
                CONFIG_FILE.parent == ikuai_config.app_dir(),
                str(CONFIG_FILE.parent))
# 关键安全断言：明文密码绝不能出现在文件里
all_ok &= check("明文密码未落盘", "secret" not in raw)
all_ok &= check("存在加密字段 password_enc", "password_enc" in raw)

cfg_raw = _json.loads(raw)
enc = cfg_raw.get("password_enc", "")
all_ok &= check("password_enc 非空", bool(enc))
all_ok &= check("密文能解回明文",
                ikuai_config.decrypt(enc) == "secret",
                ikuai_config.decrypt(enc))
say()

# 回读确认设置能还原
app.var_host.set("")
app.var_user.set("")
app.var_password.set("")
app.var_headless.set(False)
app._load_config()
all_ok &= check("回读 host", app.var_host.get() == "10.0.0.9", app.var_host.get())
all_ok &= check("回读 user", app.var_user.get() == "tester", app.var_user.get())
all_ok &= check("回读 password", app.var_password.get() == "secret",
                repr(app.var_password.get()))
all_ok &= check("回读 headless == True", app.var_headless.get() is True,
                repr(app.var_headless.get()))
say()

# 不勾「记住密码」时，密码不应落盘
app.var_remember.set(False)
app.var_password.set("should-not-save")
app._save_config()
raw2 = CONFIG_FILE.read_text(encoding="utf-8")
all_ok &= check("未勾记住密码 -> 明文不落盘", "should-not-save" not in raw2)
cfg2 = _json.loads(raw2)
all_ok &= check("未勾记住密码 -> enc 为空",
                cfg2.get("password_enc", "") == "", repr(cfg2.get("password_enc", "")))
say()

# 清理测试配置
try:
    CONFIG_FILE.unlink()
except Exception:
    pass

# ---------- 8. 无头模式开关 ----------
say("[8] 无头模式开关")
all_ok &= check("默认 headless=True（不显示窗口）",
                app.var_headless.get() is True, repr(app.var_headless.get()))
app.service.set_headless(False)
all_ok &= check("未启动时可切到 False", app.service.headless is False,
                repr(app.service.headless))
app.service.set_headless(True)
all_ok &= check("切回 True", app.service.headless is True, repr(app.service.headless))
# 模拟浏览器已启动：应拒绝切换
app.service._browser = object()
try:
    app.service.set_headless(False)
    all_ok &= check("已启动时拒绝切换", False, "未抛异常")
except RuntimeError as ex:
    all_ok &= check("已启动时拒绝切换", "无法切换" in str(ex), str(ex))

# 8b. 界面不该在"浏览器已启动"时抛错阻断（曾经的 bug）
# 以前 on_connect 里 set_headless 抛 RuntimeError 会直接导致连接失败
try:
    app._on_headless_toggle()
    all_ok &= check("已启动时点勾选框不抛错（界面自愈）", True)
except Exception as ex:
    all_ok &= check("已启动时点勾选框不抛错（界面自愈）", False, repr(ex))
all_ok &= check("界面勾选框被复位为实际值",
                app.var_headless.get() == app.service.headless,
                "var=%s actual=%s" % (app.var_headless.get(),
                                      app.service.headless))
app.service._browser = None

# 8c. busy 时应把"断开"按钮也禁用（避免中途断开导致状态错乱）
app._set_busy(True)
all_ok &= check("busy 时断开按钮被禁用",
                str(app.btn_disconnect.cget("state")) == "disabled",
                repr(app.btn_disconnect.cget("state")))
app._set_busy(False)
all_ok &= check("空闲时断开按钮恢复",
                str(app.btn_disconnect.cget("state")) == "normal",
                repr(app.btn_disconnect.cget("state")))

# 8d. 浏览器运行中 -> 隐藏窗口勾选框应被禁用（改了也没用）
app.service._browser = object()
app._set_busy(False)
all_ok &= check("浏览器运行中 -> 勾选框禁用",
                str(app.chk_headless.cget("state")) == "disabled",
                repr(app.chk_headless.cget("state")))
app.service._browser = None
app._set_busy(False)
all_ok &= check("浏览器未运行 -> 勾选框可用",
                str(app.chk_headless.cget("state")) == "normal",
                repr(app.chk_headless.cget("state")))
say()

# 清理测试配置
try:
    CONFIG_FILE.unlink()
except Exception:
    pass

# ---------- 7. 密码显示切换 ----------
say("[7] 密码显示切换")
app.chk_show_pwd.state(["!selected"])
app._toggle_pwd()
all_ok &= check("未勾选 -> show='●'", app._pwd_entry.cget("show") == "●",
                repr(app._pwd_entry.cget("show")))
app.chk_show_pwd.state(["selected"])
app._toggle_pwd()
all_ok &= check("已勾选 -> show=''", app._pwd_entry.cget("show") == "",
                repr(app._pwd_entry.cget("show")))
say()

# ---------- 9. 程序图标（v1.10） ----------
say("[9] 程序图标")
all_ok &= check("ico 文件存在", ikuai_gui.APP_ICON.is_file(),
                str(ikuai_gui.APP_ICON))
all_ok &= check("png 兜底文件存在", ikuai_gui.APP_ICON_PNG.is_file(),
                str(ikuai_gui.APP_ICON_PNG))
try:
    app.root.iconbitmap(str(ikuai_gui.APP_ICON))
    icon_ok = True
except Exception:
    icon_ok = False
all_ok &= check("iconbitmap 可加载", icon_ok)
all_ok &= check("版本号 1.12", ikuai_gui.APP_VERSION == "1.12",
                ikuai_gui.APP_VERSION)
all_ok &= check("标题为 AP 终端工具", "爱快路由-AP终端工具" in app.root.title(),
                app.root.title())
all_ok &= check("标题含版权", "Jcsit" in app.root.title()
                and "viclai" in app.root.title(), app.root.title())
say()

root.destroy()

say("=" * 64)
say("RESULT: %s" % ("ALL PASS" if all_ok else "HAS FAILURES"))
say("=" * 64)

LOG.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
sys.exit(0 if all_ok else 1)
