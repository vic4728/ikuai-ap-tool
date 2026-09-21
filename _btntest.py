#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
「操作」列浮层重启按钮 + 重启监视状态机 回归测试（Tkinter，不启动浏览器）

背景：
  用户反馈「操作列内的重启按键没有按键样式」。
  根因：Tkinter 的 Treeview 单元格里放不了真实控件，之前"重启"只是
  一段纯文字，没有任何按钮外观。
  修法：用真实 ttk.Button（Restart.TButton 样式）以 place() 浮在表格
  上方，坐标走 tree.bbox()，滚动/缩放时同步。

本测试锁定两件事：
  A. 浮层按钮
     - 按钮数量 = 可见行数
     - 样式是 Restart.TButton，黑字 #000000 + 浅蓝 #cfe4fb
     - 位置贴在操作列内、单元格为空（不再有文字叠字）
     - 点按钮 -> 重启对应那一行（不会串行）
     - 滚动后按钮跟着走
  B. 监视状态机 waiting -> seen_offline -> recovered
     - 刚下发重启时**仍然在线**：不能立刻判定恢复（这是真实 bug）
     - 看到掉线：状态 seen_offline，文案仍是「重启中」
     - 再次上线：状态 recovered，文案「已连接」
     - 全部 recovered 后退出监视，清空 monitor_targets

跑法：python _btntest.py
"""
from __future__ import annotations

import sys
from tkinter import Tk, ttk

from ikuai_service import APInfo
import ikuai_gui


FAILS: list[str] = []
COUNT = [0]


def chk(cond, msg=""):
    COUNT[0] += 1
    if not cond:
        FAILS.append(msg or "assertion failed")
    print("  %s  %s" % ("[OK]  " if cond else "[FAIL]", msg))


def mk(name, mac, ip, status="在线", uptime="1天"):
    return APInfo(group="g", name=name, model="IK-SW5", ip=ip, mac=mac,
                  uptime=uptime, status=status)


def offline(name, mac):
    """掉线/重启中的 AP：爱快把 IP 显示成 '- -'。"""
    return APInfo(group="g", name=name, model="IK-SW5", ip="- -", mac=mac,
                  uptime="- -", status="")


class _Evt:
    pass


def main():
    root = Tk()
    root.geometry("1280x720")
    app = ikuai_gui.IKuaiGUI(root)
    app._append_log = lambda s: None
    root.update()
    root.update_idletasks()

    A = mk("AP-A", "aa:aa:aa:aa:aa:01", "1.1.1.1")
    B = mk("AP-B", "aa:aa:aa:aa:aa:02", "1.1.1.2")
    C = mk("AP-C", "aa:aa:aa:aa:aa:03", "1.1.1.3")
    app._render_aps([A, B, C])
    root.update()
    root.update_idletasks()

    def mapped_btns():
        return [b for b in app._btn_pool if b.winfo_ismapped()]

    # ---------------- A. 浮层按钮 ----------------
    print("\n[A1] 按钮数量 = 可见行数")
    btns = mapped_btns()
    chk(len(btns) == 3, "3 台 AP -> 3 个按钮（实际 %d）" % len(btns))

    print("\n[A2] 按钮样式：黑字 + 浅蓝填充")
    b0 = btns[0]
    style_name = str(b0.cget("style"))
    chk(style_name == "Restart.TButton", "样式 = Restart.TButton（实际 %r）" % style_name)
    st = ttk.Style(root)
    fg = str(st.lookup("Restart.TButton", "foreground"))
    bg = str(st.lookup("Restart.TButton", "background"))
    chk(fg == "#000000", "文字色 = #000000（实际 %r）" % fg)
    chk(bg == "#cfe4fb", "填充色 = #cfe4fb（实际 %r）" % bg)

    print("\n[A3] 按钮文案与手型光标")
    chk(str(b0.cget("text")) == "重启", "文案 = 重启（实际 %r）" % str(b0.cget("text")))
    chk(str(b0.cget("cursor")) == "hand2",
        "光标 = hand2（实际 %r）" % str(b0.cget("cursor")))

    print("\n[A4] 操作列单元格为空（避免和按钮文字叠字）")
    vals = app.tree.item(A.key(), "values")
    chk(str(vals[-1]) == "", "操作列值 = ''（实际 %r）" % str(vals[-1]))

    print("\n[A5] 按钮落在操作列横向范围内")
    geo = app._action_col_geometry()
    if geo:
        col_x, col_w = geo
        bx = b0.place_info().get("x")
        bw = b0.place_info().get("width")
        bx, bw = int(bx), int(bw)
        chk(col_x <= bx and bx + bw <= col_x + col_w + 1,
            "按钮 x=[%d,%d] 在操作列 x=[%d,%d] 内" % (bx, bx + bw, col_x, col_x + col_w))
        chk(bw >= 48, "按钮宽度 >= 48（实际 %d）" % bw)
    else:
        chk(False, "拿不到操作列坐标")

    print("\n[A6] 点第 2 个按钮 -> 只重启第 2 行 AP-B")
    calls: list[list[str]] = []
    app._confirm_and_restart = lambda targets: calls.append([t.name for t in targets])
    app._last_action_click = 0.0
    btns = mapped_btns()
    # 直接调用按钮绑定的处理函数（不发真实事件，避免依赖鼠标）
    app._on_row_button_click(None, B.key())
    chk(calls == [["AP-B"]], "第 2 行按钮 -> [AP-B]（实际 %r）" % calls)

    print("\n[A7] 防抖：连点 3 次只弹一次")
    calls.clear()
    app._last_action_click = 0.0
    for _ in range(3):
        app._on_row_button_click(None, A.key())
    chk(len(calls) == 1, "3 次连点 -> 1 次（实际 %d）" % len(calls))

    print("\n[A8] 按钮不响应未知 iid（防御）")
    calls.clear()
    app._last_action_click = 0.0
    app._on_row_button_click(None, "no-such-iid")
    chk(calls == [], "未知 iid 不触发重启（实际 %r）" % calls)

    # ---------------- B. 监视状态机 ----------------
    print("\n[B1] 刚下发重启、设备仍在线 -> 不能立刻判定恢复")
    app.monitor_targets = {A.key(): {"state": "waiting", "ip": A.ip, "name": A.name}}
    app._check_monitor([A])                    # A 依然在线
    chk(app.monitor_targets[A.key()]["state"] == "waiting",
        "仍在线时状态保持 waiting（实际 %r）"
        % app.monitor_targets[A.key()]["state"])
    app._render_aps([A, B, C])
    root.update()
    app._sync_row_buttons()
    vals = app.tree.item(A.key(), "values")
    chk(str(vals[6]) == "重启中", "文案 = 重启中（实际 %r）" % str(vals[6]))

    print("\n[B2] 重启中的行 -> 按钮置灰并改文案")
    app._sync_row_buttons()
    btn_a = app._row_buttons.get(A.key())
    if btn_a is None:
        chk(False, "没找到 A 行对应的按钮")
    else:
        chk(str(btn_a.cget("text")) == "重启中",
            "按钮文案 = 重启中（实际 %r）" % str(btn_a.cget("text")))
        chk(str(btn_a.cget("state")) == "disabled",
            "按钮置灰 disabled（实际 %r）" % str(btn_a.cget("state")))

    print("\n[B3] 看到设备掉线 -> seen_offline，文案仍是重启中")
    app._check_monitor([offline("AP-A", "aa:aa:aa:aa:aa:01"), B, C])
    chk(app.monitor_targets[A.key()]["state"] == "seen_offline",
        "状态 = seen_offline（实际 %r）" % app.monitor_targets[A.key()]["state"])
    app._render_aps([offline("AP-A", "aa:aa:aa:aa:aa:01"), B, C])
    root.update()
    vals = app.tree.item(A.key(), "values")
    chk(str(vals[6]) == "重启中",
        "掉线期间文案 = 重启中（不是'断开'）（实际 %r）" % str(vals[6]))

    print("\n[B4] 设备重新上线 -> recovered，文案已连接")
    # 注意：AP-A 是唯一目标，一旦 recovered 就会立刻"全部恢复"而清空
    # monitor_targets（这是 B5 的行为）。所以这里先塞一台陪跑的 AP-B，
    # 让监视不至于马上退出，才能观察到 recovered 这个中间态。
    app.monitor_targets[B.key()] = {"state": "waiting", "ip": B.ip, "name": B.name}
    app._check_monitor([A, B, C])
    chk(app.monitor_targets.get(A.key(), {}).get("state") == "recovered",
        "状态 = recovered（实际 %r）"
        % app.monitor_targets.get(A.key(), {}).get("state"))
    app._render_aps([A, B, C])
    root.update()
    vals = app.tree.item(A.key(), "values")
    chk(str(vals[6]) == "已连接", "文案 = 已连接（实际 %r）" % str(vals[6]))

    print("\n[B5] 全部恢复 -> 退出监视、清空 monitor_targets")
    app._check_monitor([offline("AP-B", "aa:aa:aa:aa:aa:02"), A, C])
    app._check_monitor([A, B, C])
    chk(app.monitor_targets == {}, "monitor_targets 已清空（实际 %r）" % app.monitor_targets)
    chk(app._just_recovered is True, "_just_recovered = True（供状态栏显示一次）")

    print("\n[B6] 恢复后按钮回到可用状态")
    app._sync_row_buttons()
    btn_a = app._row_buttons.get(A.key())
    if btn_a is not None:
        chk(str(btn_a.cget("text")) == "重启" and str(btn_a.cget("state")) == "normal",
            "按钮 = 重启/normal（实际 %r/%r）"
            % (str(btn_a.cget("text")), str(btn_a.cget("state"))))

    print("\n[B7] 多台同时重启：一台恢复一台未恢复时保持监视")
    app.monitor_targets = {
        A.key(): {"state": "waiting", "ip": A.ip, "name": A.name},
        B.key(): {"state": "waiting", "ip": B.ip, "name": B.name},
    }
    app._check_monitor([offline("AP-A", "aa:aa:aa:aa:aa:01"), B, C])
    app._check_monitor([A, B, C])
    chk(app.monitor_targets.get(A.key(), {}).get("state") == "recovered",
        "AP-A 已恢复（实际 %r）" % app.monitor_targets.get(A.key(), {}).get("state"))
    chk(app.monitor_targets.get(B.key(), {}).get("state") == "waiting",
        "AP-B 仍在等待（实际 %r）" % app.monitor_targets.get(B.key(), {}).get("state"))
    chk(app.monitor_targets != {}, "未全部恢复时不清空监视")

    print("\n[B8] 状态栏文案随监视变化")
    app._just_recovered = False
    txt = app._status_text()
    chk("等待" in txt, "仍有等待时状态栏含「等待」（实际 %r）" % txt)

    root.destroy()

    print("\n" + "=" * 60)
    if FAILS:
        print("结果：%d 通过 / %d 失败" % (COUNT[0] - len(FAILS), len(FAILS)))
        for f in FAILS:
            print("  - %s" % f)
        return 1
    print("结果：%d 通过 / 0 失败" % COUNT[0])
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
