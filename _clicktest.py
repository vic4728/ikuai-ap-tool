#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
「操作」列单击重启回归测试（Tkinter，不启动浏览器）

背景：
  用户反馈「操作列内的"重启"按键点击无效」。

  根因：Tkinter 的 Treeview 单元格里放不了真实控件，"重启"只是
  该列里的一段文字，不会自己响应点击。代码里当时只绑定了
  <Double-1>（双击整行 = 重启选中项），**没有单击处理**，
  所以用户按直觉单击那两个字时毫无反应。

  修法：绑定 <Button-1>，用 identify_column() 判断点的是不是
  最后一列（操作），是就重启该行对应的那一台 AP。

本测试锁定以下行为：
  - 单击操作列 -> 只重启该行的 AP
  - 单击其他列 -> 不重启
  - 快速连点 -> 只弹一次确认框（防抖）
  - 双击操作列 -> 不重复弹框（单击已处理）
  - 双击非操作列 -> 仍按"重启选中项"工作
  - 点空白处 -> 不抛异常

跑法：python _clicktest.py
"""
from __future__ import annotations

import sys
from tkinter import Tk

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


class _Evt:
    """模拟 Tk 事件对象（只需要 x / y / num 三个字段）。"""
    pass


def main():
    root = Tk()
    root.geometry("1280x720")
    app = ikuai_gui.IKuaiGUI(root)
    app._append_log = lambda s: None
    root.update()                      # 真正布局并映射窗口，bbox 才有坐标
    root.update_idletasks()

    def click_at(bbox, num=1):
        x, y, w, h = bbox
        ev = _Evt()
        ev.x = x + w // 2
        ev.y = y + h // 2
        ev.num = num
        return ev

    A = mk("AP-A", "aa:aa:aa:aa:aa:01", "1.1.1.1")
    B = mk("AP-B", "aa:aa:aa:aa:aa:02", "1.1.1.2")
    app._render_aps([A, B])
    root.update()

    print("\n[1] 表格列定义")
    cols = app.tree["columns"]
    chk(len(cols) == 9, "共 9 列（实际 %d）" % len(cols))
    chk(cols[-1] == "action", "最后一列是操作列")
    chk(cols[-2] == "comment", "倒数第二列是备注列")

    bbox_a = app.tree.bbox(A.key(), column="action")
    bbox_b = app.tree.bbox(B.key(), column="action")
    bbox_n = app.tree.bbox(A.key(), column="name")
    if not (bbox_a and bbox_b and bbox_n):
        print("\n[跳过] 窗口未映射，拿不到单元格坐标（无 GUI 环境）")
        root.destroy()
        return 0

    print("     坐标：A/操作=%s  A/名称=%s" % (bbox_a, bbox_n))

    # 拦截确认框，只观察"要重启谁"
    calls: list[list[str]] = []
    app._confirm_and_restart = lambda targets: calls.append([t.name for t in targets])

    print("\n[2] 单击操作列 -> 重启该行")
    app._last_action_click = 0.0
    app._on_tree_click(click_at(bbox_a))
    chk(calls == [["AP-A"]], "单击 AP-A 操作列 -> [AP-A]（实际 %r）" % calls)

    print("\n[3] 单击非操作列 -> 不重启")
    calls.clear()
    app._last_action_click = 0.0
    app._on_tree_click(click_at(bbox_n))
    chk(calls == [], "点名称列不重启（实际 %r）" % calls)

    print("\n[4] 防抖：连续 3 次连点只触发一次")
    calls.clear()
    app._last_action_click = 0.0
    for _ in range(3):
        app._on_tree_click(click_at(bbox_a))
    chk(len(calls) == 1, "3 次连点 -> 1 次（实际 %d）" % len(calls))

    print("\n[5] 单击第二行操作列 -> 对应 AP-B（不能串行）")
    calls.clear()
    app._last_action_click = 0.0
    app._on_tree_click(click_at(bbox_b))
    chk(calls == [["AP-B"]], "单击 AP-B 操作列 -> [AP-B]（实际 %r）" % calls)

    print("\n[6] 双击操作列 -> 不重复弹框")
    calls.clear()
    app._last_action_click = 0.0
    app._on_row_double_click(click_at(bbox_a))
    chk(calls == [], "双击操作列不额外触发（实际 %r）" % calls)

    print("\n[7] 双击非操作列 -> 仍按选中项重启")
    calls.clear()
    app._last_action_click = 0.0
    app.tree.selection_set(A.key())
    app._on_row_double_click(click_at(bbox_n))
    chk(calls == [["AP-A"]], "双击名称列 -> 选中项 [AP-A]（实际 %r）" % calls)

    print("\n[8] 边界：点空白处不抛异常")
    try:
        ev = _Evt()
        ev.x = 5
        ev.y = 9999
        ev.num = 1
        app._on_tree_click(ev)
        chk(True, "空白处点击不抛异常")
    except Exception as ex:
        chk(False, "空白处点击抛异常：%s" % ex)

    print("\n[9] 边界：右键不应触发重启")
    calls.clear()
    app._last_action_click = 0.0
    app._on_tree_click(click_at(bbox_a, num=3))
    chk(calls == [], "右键不触发重启（实际 %r）" % calls)

    print("\n[10] 鼠标划过操作列 -> 手型光标（可点击提示）")
    def cursor():
        return str(app.tree.cget("cursor"))

    app._on_tree_motion(click_at(bbox_a))
    chk(cursor() == "hand2", "操作列上光标 = hand2（实际 %r）" % cursor())
    app._on_tree_motion(click_at(bbox_n))
    chk(cursor() == "", "离开操作列恢复普通光标（实际 %r）" % cursor())

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
