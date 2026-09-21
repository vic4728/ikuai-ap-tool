#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PySide6 版 GUI 回归测试（离屏，不连真机）

锁定与 Tk 版一致的关键行为：
  [A] 模型结构：9 列、表头、UserRole 取整行
  [B] 渲染：备注列显示 comment；状态文案（在线/断开/重启中）优先级
  [C] 行着色：重启中黄底、离线红底、状态列彩色文字
  [D] 排序：首点降序/再点升序、IP 数值序、时长语义序、空值垫底
  [E] 双击备注列 -> 修改备注路径；双击其他列 -> 重启选中
  [F] 操作列真实按钮：文本/禁用状态随监视变化
  [G] 三态监视状态机：waiting->seen_offline->recovered
  [H] 窗口标题与版本
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, r"E:\Visual\ikuai")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from ikuai_service import APInfo
import ikuai_gui_qt as Q

FAILS: list[str] = []
COUNT = [0]


def chk(cond, msg=""):
    COUNT[0] += 1
    if not cond:
        FAILS.append(msg or "assertion failed")
    print("  %s  %s" % ("[OK]  " if cond else "[FAIL]", msg))


def mk(name, mac, ip, comment="", uptime="3天", status="正常"):
    return APInfo(group="AP-1", name=name, model="IK-SW5", ip=ip,
                  mac=mac, uptime=uptime, status=status, comment=comment)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    win = Q.MainWindow()
    win.show()
    QTest.qWaitForWindowExposed(win)

    m = win.model
    NC = m.columnCount()

    print("\n[H] 标题")
    t = win.windowTitle()
    chk("爱快路由-AP终端工具" in t, "标题含程序名：%r" % t)
    chk("Jcsit" in t and "viclai" in t, "标题含版权")
    chk("1.0" in t, "版本 1.0")

    print("\n[A] 模型结构")
    chk(NC == 9, "9 列（got %d）" % NC)
    chk(m.headerData(7, Qt.Horizontal) == "备注", "第 8 列表头=备注")
    chk(m.headerData(8, Qt.Horizontal) == "操作", "第 9 列表头=操作")

    print("\n[B] 渲染与状态文案")
    A = mk("AP-A", "08:9b:4b:4c:74:b9", "192.168.9.222", comment="三楼-平台")
    B = mk("AP-B", "08:9b:4b:4c:75:c4", "", comment="", uptime="- -")
    win._render_aps([A, B])
    chk(m.rowCount() == 2, "2 行")
    chk(m.data(m.index(0, 7), Qt.DisplayRole) == "三楼-平台", "备注列显示")
    chk(m.data(m.index(1, 6), Qt.DisplayRole) == "断开", "离线显示断开")
    chk(m.data(m.index(0, 6), Qt.DisplayRole) not in ("", None), "在线有状态")
    ur = m.data(m.index(0, 0), Qt.UserRole)
    chk(isinstance(ur, APInfo) and ur.mac == A.mac, "UserRole 取整行")

    print("\n[C] 行着色")
    win.monitor_targets = {A.key(): {"state": "waiting"}}
    win._render_aps([A, B])
    bg = m.data(m.index(0, 0), Qt.BackgroundRole)
    chk(bg is not None and bg.name().lower() == "#fff7e0", "重启中黄底")
    fg = m.data(m.index(0, 6), Qt.ForegroundRole)
    chk(fg is not None, "状态列有前景色")
    bg2 = m.data(m.index(1, 0), Qt.BackgroundRole)
    chk(bg2 is not None and bg2.name().lower() == "#fdecec", "离线红底")
    win.monitor_targets = {}

    print("\n[D] 排序")
    rows = [
        mk("AP-A", "aa:aa:aa:aa:aa:01", "192.168.9.220", uptime="3天7时"),
        mk("AP-B", "aa:aa:aa:aa:aa:02", "192.168.9.231", uptime="14天22时"),
        mk("AP-C", "aa:aa:aa:aa:aa:03", "192.168.9.9", uptime="10分"),
        mk("AP-D", "aa:aa:aa:aa:aa:04", "192.168.9.10", uptime="2时"),
        mk("AP-E", "aa:aa:aa:aa:aa:05", "", uptime="- -"),
    ]
    win._render_aps(rows)

    win._on_header_clicked(m.COL_IP)              # 首点 = 降序
    ips = [m.data(m.index(i, 3), Qt.DisplayRole) for i in range(m.rowCount())]
    chk(ips == ["192.168.9.231", "192.168.9.220", "192.168.9.10",
                "192.168.9.9", ""],
        "IP 降序数值排、空垫底：got=%s" % ips)
    chk(m.sort_desc is True, "首点降序")

    win._on_header_clicked(m.COL_IP)              # 再点 = 升序
    ips = [m.data(m.index(i, 3), Qt.DisplayRole) for i in range(m.rowCount())]
    chk(ips == ["192.168.9.9", "192.168.9.10", "192.168.9.220",
                "192.168.9.231", ""],
        "IP 升序数值排、空仍垫底：got=%s" % ips)

    win._on_header_clicked(m.COL_UPTIME)
    ups = [m.data(m.index(i, 5), Qt.DisplayRole) for i in range(m.rowCount())]
    chk(ups == ["14天22时", "3天7时", "2时", "10分", "- -"],
        "时长降序语义排、'- -' 垫底：got=%s" % ups)

    win._on_header_clicked(m.COL_KEYS.index("name"))   # 换列重置降序
    chk(m.sort_col == m.COL_KEYS.index("name") and m.sort_desc is True,
        "换列重置降序")

    # 排序后刷新保持（名称降序 = E->A）
    win._render_aps(list(reversed(rows)))
    names = [m.data(m.index(i, 1), Qt.DisplayRole) for i in range(m.rowCount())]
    chk(names == ["AP-E", "AP-D", "AP-C", "AP-B", "AP-A"],
        "刷新后排序保持（名称降序）：got=%s" % names)

    print("\n[G] 三态监视状态机")
    win.monitor_targets = {A.key(): {"state": "waiting"}}
    live = mk("AP-A", A.mac, "192.168.9.222")
    win._check_monitor([live])                    # 还在线 -> 不动
    chk(win.monitor_targets[A.key()]["state"] == "waiting", "在线不动")
    dead = mk("AP-A", A.mac, "", uptime="- -")
    win._check_monitor([dead])                    # 掉线 -> seen_offline
    chk(win.monitor_targets[A.key()]["state"] == "seen_offline", "掉线转 seen_offline")
    win._check_monitor([live])                    # 回来 -> recovered
    chk(win.monitor_targets.get(A.key(), {}).get("state") == "recovered"
        or A.key() not in win.monitor_targets,
        "回线转 recovered（或已随全部恢复清空）")
    chk(A.key() not in win.monitor_targets, "全部恢复清空监视")

    print("\n[F] 操作列按钮")
    win._render_aps([A])
    btn = win.view.indexWidget(m.index(0, m.COL_ACTION))
    chk(btn is not None and btn.text() == "重启", "有按钮且文本=重启")
    win.monitor_targets = {A.key(): {"state": "waiting"}}
    win._render_aps([A])
    btn2 = win.view.indexWidget(m.index(0, m.COL_ACTION))
    chk(btn2 is not None and btn2.text() == "重启中...", "监视中按钮变重启中")
    chk(not btn2.isEnabled(), "监视中按钮禁用")
    win.monitor_targets = {}

    print("\n[E] 双击路由")
    edits = []
    restarts = []
    win._edit_comment = lambda ap: edits.append(ap.key())
    win.on_restart_selected = lambda: restarts.append(1)

    win._on_double_click(m.index(0, m.COL_COMMENT))
    chk(edits == [A.key()], "双击备注列 -> 修改备注")
    chk(not restarts, "双击备注列不触发重启")
    win._on_double_click(m.index(0, 1))
    chk(restarts == [1], "双击名称列 -> 重启选中")
    win._on_double_click(m.index(0, m.COL_ACTION))
    chk(len(restarts) == 1, "双击操作列不重复触发")

    print("\n" + "=" * 60)
    if FAILS:
        print("结果：%d 通过 / %d 失败" % (COUNT[0] - len(FAILS), len(FAILS)))
        for f in FAILS:
            print("  [FAIL] %s" % f)
        sys.exit(1)
    print("结果：%d 通过 / 0 失败" % COUNT[0])
    print("=" * 60)
    win.close()


if __name__ == "__main__":
    main()
