#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
列排序回归测试（v1.9：名称/型号/IP/MAC/运行时间/状态 点击表头排序）

用户需求：
  - 点一次列表标题 = 降序，再点一次 = 升序
  - 自动刷新后排序必须保持（不能被后台数据冲回原始顺序）
  - 程序标题栏带版权信息 | © Jcsit&viclai

本测试锁定：
  [A] 点击表头：首点降序、再点升序、点其他列重置降序
  [B] IP 数值排序：192.168.9.9 < 192.168.9.10（不能按字典序）
  [C] 运行时间排序：'3天7时7分' > '14天22时'（不能按字典序）
  [D] 排序状态下刷新（数据顺序被打乱重投）-> 排序保持
  [E] 排序状态下行内容变化 -> 只动内容不乱序，选中不丢
  [F] 空值（离线 AP 的空 IP / '- -'）始终排最后，不因升降序漂移
  [G] 未排序（初始状态）-> 保持路由器返回的原始顺序
  [H] 表头箭头指示：降序 ↓ / 升序 ↑ / 其他列无箭头
  [I] MAC 列排序（十六进制字符串比较，与现场 12 台同名 AP 相关）
  [J] 标题栏含版权信息
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


def mk(name, mac, ip, uptime, status, model="IK-SW5", group="g"):
    return APInfo(group=group, name=name, model=model, ip=ip,
                  mac=mac, uptime=uptime, status=status)


def main():
    root = Tk()
    root.withdraw()
    app = ikuai_gui.IKuaiGUI(root)
    app._append_log = lambda s: None

    def col_vals(col_idx):
        return [app.tree.item(i, "values")[col_idx]
                for i in app.tree.get_children()]

    # 列索引：0=group 1=name 2=model 3=ip 4=mac 5=uptime 6=status
    A = mk("AP-A", "08:9b:4b:4c:75:c4", "192.168.9.220", "3天7时7分", "正常")
    B = mk("AP-B", "08:9b:4b:4c:75:5e", "192.168.9.231", "14天22时", "正常")
    C = mk("AP-C", "08:9b:4b:4c:75:aa", "192.168.9.9",   "10分",     "正常")
    D = mk("AP-D", "08:9b:4b:4c:75:0f", "192.168.9.10",  "2时",      "正常")
    E = mk("AP-E", "",                   "",              "- -",      "断开")

    print("\n[G] 初始状态：未排序 -> 原始顺序")
    app._render_aps([A, B, C, D, E])
    chk(app.sort_col is None, "初始 sort_col 为 None")
    chk(col_vals(1) == ["AP-A", "AP-B", "AP-C", "AP-D", "AP-E"],
        "未排序保持原始顺序")

    print("\n[A] 名称列：首点降序、再点升序")
    app._on_heading_click("name")
    chk(app.sort_col == "name" and app.sort_desc is True, "首点 = 降序")
    chk(col_vals(1) == ["AP-E", "AP-D", "AP-C", "AP-B", "AP-A"],
        "名称降序（空名排最后）")
    app._on_heading_click("name")
    chk(app.sort_desc is False, "再点 = 升序")
    chk(col_vals(1) == ["AP-A", "AP-B", "AP-C", "AP-D", "AP-E"],
        "名称升序（空名仍最后）")
    app._on_heading_click("status")
    chk(app.sort_col == "status" and app.sort_desc is True,
        "点其他列 -> 重置为降序")

    print("\n[B] IP 数值排序（不是字典序）")
    app._on_heading_click("ip")
    ips = col_vals(3)
    chk(ips == ["192.168.9.231", "192.168.9.220", "192.168.9.10",
                "192.168.9.9", ""],
        "IP 降序按数值（231>220>10>9），空 IP 最后：got=%s" % ips)
    app._on_heading_click("ip")
    ips = col_vals(3)
    chk(ips == ["192.168.9.9", "192.168.9.10", "192.168.9.220",
                "192.168.9.231", ""],
        "IP 升序按数值，空 IP 仍最后：got=%s" % ips)

    print("\n[C] 运行时间排序（时长语义，不是字典序）")
    app._on_heading_click("uptime")
    ups = col_vals(5)
    chk(ups == ["14天22时", "3天7时7分", "2时", "10分", "- -"],
        "时长降序（14天>3天7时>2时>10分），'- -' 最后：got=%s" % ups)
    app._on_heading_click("uptime")
    ups = col_vals(5)
    chk(ups == ["10分", "2时", "3天7时7分", "14天22时", "- -"],
        "时长升序，'- -' 仍最后：got=%s" % ups)

    print("\n[I] MAC 列排序")
    app._on_heading_click("mac")
    macs = col_vals(4)
    chk(macs == ["08:9b:4b:4c:75:c4", "08:9b:4b:4c:75:aa",
                 "08:9b:4b:4c:75:5e", "08:9b:4b:4c:75:0f", ""],
        "MAC 降序为字典序（c4>aa>5e>0f），空值最后：got=%s" % macs)
    app._on_heading_click("mac")
    macs = col_vals(4)
    chk(macs[-1] == "", "MAC 升序时空值仍最后")
    chk(macs == sorted(macs[:4], key=str.lower) + [""],
        "MAC 升序为字典序：got=%s" % macs)

    print("\n[D] 排序保持：模拟后台刷新（原始顺序被打乱重投）")
    app._on_heading_click("ip")            # IP 降序
    app._render_aps([C, A, E, D, B])       # 后台乱序回来
    ips = col_vals(3)
    chk(ips == ["192.168.9.231", "192.168.9.220", "192.168.9.10",
                "192.168.9.9", ""],
        "刷新后排序保持（不被原始顺序冲掉）：got=%s" % ips)
    chk(app.tree.index(E.key() or E.name) == 4, "空 IP 行仍在最后")

    print("\n[E] 排序中内容变化 -> 只改内容不乱序，选中不丢")
    app.tree.selection_set(A.key())
    B2 = mk("AP-B", B.mac, "192.168.9.231", "15天1时", "正常")
    app._render_aps([C, A, E, D, B2])
    ips = col_vals(3)
    chk(ips == ["192.168.9.231", "192.168.9.220", "192.168.9.10",
                "192.168.9.9", ""],
        "IP 值未变（B 的 IP 相同），顺序稳定")
    chk(list(app.tree.selection()) == [A.key()], "选中未丢")
    B3 = mk("AP-B", B.mac, "192.168.9.99", "15天1时", "正常")   # IP 变小
    app._render_aps([C, A, E, D, B3])
    ips = col_vals(3)
    chk(ips == ["192.168.9.220", "192.168.9.99", "192.168.9.10",
                "192.168.9.9", ""],
        "IP 变化后按新值归位（99 从第 1 行落至第 2 行）：got=%s" % ips)

    print("\n[F] 空值在升降序切换间始终垫底")
    app._on_heading_click("ip")            # 切回升序
    chk(col_vals(3)[-1] == "", "升序下空 IP 最后")
    app._on_heading_click("ip")            # 再切降序
    chk(col_vals(3)[-1] == "", "降序下空 IP 仍最后")
    app._on_heading_click("uptime")
    chk(col_vals(5)[-1] == "- -", "时长降序 '- -' 最后")
    app._on_heading_click("uptime")
    chk(col_vals(5)[-1] == "- -", "时长升序 '- -' 仍最后")

    print("\n[H] 表头箭头指示")
    app._on_heading_click("name")          # name 降序
    chk("↓" in app.tree.heading("name")["text"], "排序列带 ↓")
    chk("↑" not in app.tree.heading("ip")["text"]
        and "↓" not in app.tree.heading("ip")["text"],
        "非排序列无箭头")
    app._on_heading_click("name")          # 升序
    chk("↑" in app.tree.heading("name")["text"], "切换后带 ↑")
    chk(app.tree.heading("name")["text"].startswith("名称"),
        "表头文字保留原标签")

    print("\n[操作] 操作列表头不可排序")
    cmd = app.tree.heading("action")
    chk(not cmd.get("command"), "操作列未绑排序命令")

    print("\n[J] 标题栏版权信息")
    title = app.root.title()
    chk("Jcsit" in title and "viclai" in title, "标题含 Jcsit&viclai：got=%r" % title)
    chk("爱快路由-AP终端工具" in title, "标题保留程序名")
    chk("v1." in title and title.split("v")[1].split(" ")[0].count(".") == 1,
        "标题保留版本号：%r" % title)

    print("\n" + "=" * 60)
    if FAILS:
        print("结果：%d 通过 / %d 失败" % (COUNT[0] - len(FAILS), len(FAILS)))
        for f in FAILS:
            print("  [FAIL] %s" % f)
        sys.exit(1)
    print("结果：%d 通过 / 0 失败" % COUNT[0])
    print("=" * 60)
    root.destroy()


if __name__ == "__main__":
    main()
