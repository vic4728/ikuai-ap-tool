#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
关闭窗口不崩溃 / 不卡死 回归测试（Tkinter，不启动浏览器）

背景：
  用户反馈「关闭时程序崩溃」。

  根因有两条，都在 on_close 路径上：

  1) _pump_queue() 末尾**无条件**再排期 after(120)，且处理消息时
     没有"已关闭"判断。窗口 destroy 之后：
       - _append_log -> TclError: invalid command name ".!text"
       - _render_aps -> TclError: NULL main window
     这些 TclError 会从 after 回调里抛出去，Tk 直接打成崩溃信息。
     （实测确实抛错，见下）

  2) on_close() 在 **UI 线程里同步调用** service.stop()，而它会去关
     Chromium（内部投递到浏览器工作线程再等待，实测可达数秒到十几秒）。
     窗口在这期间完全无响应，用户看到的就是"关闭时卡死"。

  修法：
  - 加 _closing 标志；_pump_queue 看到就立刻返回/停止排期，
    并吞掉 TclError
  - on_close 先停所有定时器、存配置，然后**立刻 destroy**，
    浏览器丢到后台线程关
  - service.stop() 的等待时间收紧（15s 投递 / 3s join），
    避免退出时长时间挂住

跑法：python _closetest.py
"""
from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from tkinter import Tk

import ikuai_gui


FAILS: list[str] = []
COUNT = [0]


def chk(cond, msg=""):
    COUNT[0] += 1
    if not cond:
        FAILS.append(msg or "assertion failed")
    print("  %s  %s" % ("[OK]  " if cond else "[FAIL]", msg))


def main():
    print("\n[A] 先证明确实会崩（旧行为的机制）")
    r = Tk()
    txt = tk.Text(r)
    txt.pack()
    r.update()
    r.destroy()
    # destroy 之后碰控件 -> TclError。这就是旧代码崩溃的来源。
    crashed = False
    try:
        txt.insert("end", "late message\n")
    except tk.TclError:
        crashed = True
    chk(crashed, "destroy 后写控件确实抛 TclError（即旧代码崩溃原因）")

    print("\n[B] 修复后：关闭路径")
    root = Tk()
    root.geometry("1280x720")
    app = ikuai_gui.IKuaiGUI(root)
    app._append_log = lambda s: None
    root.update()

    chk(app._closing is False, "_closing 初始为 False")
    chk(app._pump_job is not None, "队列轮询已排期")

    # 把 stop 换成"慢速版"，验证 on_close 不会被它拖住
    def slow_stop():
        time.sleep(1.5)
    app.service.stop = slow_stop

    t0 = time.time()
    app.on_close()
    dt = time.time() - t0
    chk(dt < 0.5, "on_close 立即返回（实测 %.3fs，应 <0.5s）" % dt)
    chk(app._closing is True, "_closing 已置 True")
    chk(app._pump_job is None, "轮询定时器已取消")
    chk(app.refresh_job is None, "刷新定时器已取消")
    chk(app.monitor_job is None, "监视定时器已取消")

    print("\n[C] 销毁后后台仍在投递消息 -> 不得崩溃")
    for i in range(50):
        app.ui_queue.put(("log", "late %d" % i))
        app.ui_queue.put(("aps", []))
        app.ui_queue.put(("status", "x"))
        app.ui_queue.put(("busy", True))
    try:
        app._pump_queue()
        chk(True, "销毁后调用 _pump_queue 安静返回，不抛异常")
    except Exception as ex:
        chk(False, "销毁后 _pump_queue 抛异常：%s" % ex)

    print("\n[D] 重复 close 幂等")
    try:
        app.on_close()
        chk(True, "重复 on_close 不抛异常")
    except Exception as ex:
        chk(False, "重复 on_close 抛异常：%s" % ex)

    print("\n[E] 关闭瞬间正在读取 + 高频投递 -> 不得崩溃")
    root2 = Tk()
    root2.geometry("900x600")
    app2 = ikuai_gui.IKuaiGUI(root2)
    app2._append_log = lambda s: None
    root2.update()
    app2._refresh_inflight = True

    stop_flag = threading.Event()

    def late_sender():
        i = 0
        while not stop_flag.is_set() and i < 500:
            try:
                app2.ui_queue.put(("log", "x%d" % i))
                app2.ui_queue.put(("aps", []))
                app2.ui_queue.put(("log", "y%d" % i))
            except Exception:
                pass
            i += 1
            time.sleep(0.001)

    threading.Thread(target=late_sender, daemon=True).start()
    app2.service.stop = lambda: None
    app2.on_close()
    time.sleep(0.3)
    try:
        app2._pump_queue()
        chk(True, "关闭期间高频投递 + 轮询调用，无异常")
    except Exception as ex:
        chk(False, "关闭期间抛异常：%s" % ex)
    stop_flag.set()

    print("\n[F] 关闭后 _handle_message 不再被调用")
    called = []
    root3 = Tk()
    app3 = ikuai_gui.IKuaiGUI(root3)
    app3._append_log = lambda s: None
    root3.update()
    app3.service.stop = lambda: None
    app3._handle_message = lambda k, p: called.append(k)
    app3.on_close()
    app3.ui_queue.put(("log", "should be ignored"))
    app3._pump_queue()
    chk(called == [], "关闭后消息被丢弃（实际处理了 %r）" % called)

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
