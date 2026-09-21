#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
列表差量渲染回归测试（Tkinter Treeview，不启动浏览器）

背景：
  用户反馈「前端一直频繁刷新、已连接XX台AP 和 读取中 来回切、
  选不中终端列表里的设备、批量重启按钮点不动」。

  根因是两件事叠加：
    1. on_refresh() 里调了 _set_busy(True)，把「批量重启」按钮置灰；
       而读取要 2~3 秒，自动刷新开着时按钮几乎一直是灰的。
    2. _render_aps() 每个周期都 delete 全部行再 insert，
       用户刚选中的设备立刻被取消选中，列表视觉上不停闪。
    3. 没有"自动刷新"开关，auto_refresh 在连接后被强制置 True。

本测试锁定修复后的行为：
  - 内容未变的行不得被重写（避免闪动）
  - 行 iid 必须稳定（否则选中会丢）
  - 行数/顺序变化时不得出现重复行或丢行
  - 读取期间「批量重启」按钮必须保持可用

跑法：python _rendertest.py
"""
from __future__ import annotations

import random
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


def mk(name, mac, ip, uptime, status, group="g"):
    return APInfo(group=group, name=name, model="IK-SW5", ip=ip,
                  mac=mac, uptime=uptime, status=status)


def main():
    root = Tk()
    root.withdraw()
    app = ikuai_gui.IKuaiGUI(root)
    app._append_log = lambda s: None          # 静音，别刷屏

    def rows():
        return list(app.tree.get_children())

    def names():
        return [app.tree.item(i, "values")[1] for i in rows()]

    A = mk("AP-A", "aa:aa:aa:aa:aa:01", "1.1.1.1", "1天", "在线")
    B = mk("AP-B", "aa:aa:aa:aa:aa:02", "1.1.1.2", "1天", "在线")
    C = mk("AP-C", "aa:aa:aa:aa:aa:03", "1.1.1.3", "1天", "在线")
    D = mk("AP-D", "aa:aa:aa:aa:aa:04", "1.1.1.4", "1天", "在线")
    E = mk("AP-E", "aa:aa:aa:aa:aa:05", "1.1.1.5", "1天", "在线")
    F = mk("AP-F", "aa:aa:aa:aa:aa:06", "1.1.1.6", "1天", "在线")

    print("\n[1] 首次渲染")
    app._render_aps([A, B, C, D])
    chk(len(rows()) == 4, "渲染出 4 行")
    chk(len(set(rows())) == 4, "iid 无重复")

    print("\n[2] 连续 10 轮内容完全不变 -> 不得重写（防闪动）")
    before = rows()
    for _ in range(10):
        app._render_aps([A, B, C, D])
    chk(rows() == before, "10 轮后行与顺序完全一致")
    chk(app._render_sig.get(A.key()) is not None, "渲染指纹已记录")

    print("\n[3] 单台变离线 -> 只影响该行，选中不丢")
    app.tree.selection_set(A.key())
    app._render_aps([A, mk("AP-B", B.mac, "", "-", "断开"), C, D])
    chk(rows() == before, "行顺序与 iid 稳定")
    chk(list(app.tree.selection()) == [A.key()], "选中未被清掉")
    chk(app.tree.item(B.key(), "values")[6] == "断开", "B 显示为断开")
    chk("offline" in app.tree.item(B.key(), "tags"), "B 带 offline 标签")

    print("\n[4] 顺序反转 -> 不得重复、不得丢行")
    B_off = mk("AP-B", B.mac, "", "-", "断开")
    app._render_aps([D, C, B_off, A])
    chk(names() == ["AP-D", "AP-C", "AP-B", "AP-A"], "顺序 = [D,C,B,A]")
    chk(len(set(rows())) == 4, "换序后无重复 iid")

    print("\n[5] 新增一台")
    app._render_aps([D, C, B_off, A, E])
    chk(len(rows()) == 5, "变为 5 行")
    chk(names()[-1] == "AP-E", "E 在末位")

    print("\n[6] 删除两台")
    app._render_aps([A, C])
    chk(len(rows()) == 2, "变为 2 行")
    chk(not app.tree.exists(B.key()), "B 已移除")
    chk(not app.tree.exists(E.key()), "E 已移除")
    chk(names() == ["AP-A", "AP-C"], "剩余顺序正确")

    print("\n[7] 乱序重建（2 台 -> 全新 4 台）")
    app._render_aps([C, F, A, D])
    chk(names() == ["AP-C", "AP-F", "AP-A", "AP-D"], "顺序 = [C,F,A,D]")
    chk(len(set(rows())) == 4, "无重复")

    print("\n[8] 清空")
    app._render_aps([])
    chk(len(rows()) == 0, "空表")

    print("\n[9] 30 轮随机抖动压力测试")
    random.seed(7)
    pool = [A, B, C, D, E, F]
    bad = None
    for i in range(30):
        subset = random.sample(pool, random.randint(1, len(pool)))
        random.shuffle(subset)
        app._render_aps(subset)
        r = rows()
        if len(r) != len(subset) or len(set(r)) != len(r):
            bad = "第 %d 轮：行数 %d（期望 %d）" % (i, len(r), len(subset))
            break
    chk(bad is None, bad or "30 轮随机抖动行数与唯一性始终正确")

    print("\n[10] 读取期间「批量重启」保持可用（用户核心痛点）")
    app._set_busy(False)
    chk(str(app.btn_batch["state"]) != "disabled", "空闲时可点")
    app._refresh_inflight = True          # 模拟后台正在读取
    chk(str(app.btn_batch["state"]) != "disabled", "读取中仍可点")
    app._refresh_inflight = False

    print("\n[11] 自动刷新开关可真正停止轮询")
    app.var_auto_refresh.set(True)
    app._on_autorefresh_toggle()
    chk(app.auto_refresh is True, "开启后 auto_refresh=True")
    app.var_auto_refresh.set(False)
    app._on_autorefresh_toggle()
    chk(app.auto_refresh is False, "关闭后 auto_refresh=False")
    chk(app.refresh_job is None, "定时器已取消，列表静止")
    app.auto_refresh = False
    app._cancel_refresh()

    print("\n[12] 状态文案随监视状态变化")
    app.ap_list = [A, B]
    app.monitor_targets = {}
    chk("2 台 AP" in app._status_text(), "普通状态 = 已连接 · 2 台 AP")
    app.monitor_targets = {A.key(): {"state": "waiting"}}
    chk("等待 1 台" in app._status_text(), "监视中 = 等待 1 台恢复")

    print("\n[13] 后台静默刷新不得闪状态栏")
    app.monitor_targets = {}
    app.ap_list = [A, B]
    app.var_status.set("已连接 · 2 台 AP")
    app.service._connected = True          # 伪装已连接，让 on_refresh 继续
    app._refresh_inflight = True           # 卡住 worker，只看同步部分
    app.on_refresh(quiet=True)             # quiet 分支：不应改写状态栏
    chk(app.var_status.get() == "已连接 · 2 台 AP",
        "quiet 刷新不改状态文案（实际 %r）" % app.var_status.get())
    app._refresh_inflight = False
    app.service._connected = False

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
