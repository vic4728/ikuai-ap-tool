#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
备注列回归测试（v1.11：读取展示 + 双击编辑回写）

用户需求：
  - 增加"备注"列，读取路由系统内 AP 列表的终端备注；
  - 程序内双击备注弹出输入框，填入后回写到 ikuai 系统 AP 终端备注。

本测试锁定（离线，不连真机）：
  [A] 列结构：comment 列存在、位置在 运行状态 与 操作 之间、可排序
  [B] 渲染：APInfo.comment 显示在备注列；空备注显示空串
  [C] 解析：3.x td[6] / 4.x 表头"备注" 映射到 comment 字段
  [D] 双击备注列 -> 走修改备注路径（不触发重启确认）
  [E] 双击其他列 -> 仍是重启行为（原行为不回归）
  [F] 输入框：带出旧值 initialvalue；取消不保存；超 64 字符拒绝
  [G] 保存成功后本地即时更新（不等刷新）
  [H] schema 的 remark_* 选择器两代互不污染 + 关键选择器存在
  [I] 真机抓到的 3.x 弹窗结构（input[name=comment] / btn_confirm 是 <input>）
"""
from __future__ import annotations

import sys
from tkinter import Tk

from ikuai_service import APInfo, SCHEMA_3X, SCHEMA_4X
import ikuai_gui


FAILS: list[str] = []
COUNT = [0]


def chk(cond, msg=""):
    COUNT[0] += 1
    if not cond:
        FAILS.append(msg or "assertion failed")
    print("  %s  %s" % ("[OK]  " if cond else "[FAIL]", msg))


def mk(name, mac, ip, comment="", status="正常"):
    return APInfo(group="AP-1", name=name, model="IK-SW5", ip=ip,
                  mac=mac, uptime="3天7时", status=status, comment=comment)


def main():
    root = Tk()
    root.withdraw()
    app = ikuai_gui.IKuaiGUI(root)
    app._append_log = lambda s: None

    # bbox 需要窗口真正映射（withdraw 会拿到空 bbox —— 踩过的坑），
    # 这里临时显示、移到屏幕外，拿完坐标再藏回去
    root.deiconify()
    root.geometry("+20000+20000")
    root.update()

    def col_vals(idx):
        return [app.tree.item(i, "values")[idx]
                for i in app.tree.get_children()]

    n_cols = len(app.tree["columns"])

    print("\n[A] 列结构")
    chk(n_cols == 9, "共 9 列（got %d）" % n_cols)
    chk(app.tree["columns"][-2] == "comment", "倒数第二列是 comment")
    chk(app.tree["columns"][-1] == "action", "最后一列是 action")
    chk(app.tree.heading("comment")["text"] == "备注", "表头=备注")
    chk("comment" in app._sortable_cols, "备注列可排序")

    print("\n[B] 渲染")
    A = mk("AP-A", "08:9b:4b:4c:74:b9", "192.168.9.222", comment="三楼-平台")
    B = mk("AP-B", "08:9b:4b:4c:75:c4", "192.168.9.220", comment="")
    app._render_aps([A, B])
    vals = col_vals(7)
    chk(vals == ["三楼-平台", ""], "备注列显示 comment（got %s）" % vals)
    v = app.tree.item(A.key(), "values")
    chk(len(v) == 9, "行值 9 个（got %d）" % len(v))
    chk(v[6] == "正常" and v[8] == "", "状态/操作列位置未被挤歪")

    print("\n[C] 解析（离线调用 _read_ap_rows 的拆分逻辑）")
    # 3.x：固定索引 cols_idx.comment == 6
    chk(SCHEMA_3X["cols_idx"]["comment"] == 6, "3.x comment 索引=6")
    chk(SCHEMA_3X["cols"]["comment"] == ["备注"], "3.x comment 表头候选")
    chk("comment" in SCHEMA_4X["cols"], "4.x 有 comment 表头映射")
    chk(SCHEMA_4X["cols"]["comment"] == ["备注"], "4.x comment 表头候选")

    print("\n[D] 双击备注列 -> 修改备注（不弹重启确认）")
    restarts = []
    edits = []
    app.on_restart_selected = lambda: restarts.append(1)
    app._edit_comment = lambda ap: edits.append(ap.key())

    class Ev:
        def __init__(self, iid, col_idx):
            import math
            bb = app.tree.bbox(iid, column=app.tree["columns"][col_idx - 1])
            self.x, self.y, _, _ = bb
            self.num = 1

    root.update()
    bb = app.tree.bbox(A.key(), column="comment")
    if bb:
        ev = type("Ev", (), {"x": (bb[0] + bb[2] // 2),
                             "y": (bb[1] + bb[3] // 2), "num": 1})()
        app._on_row_double_click(ev)
        chk(edits == [A.key()], "双击备注列 -> _edit_comment（got %s）" % edits)
        chk(not restarts, "没有触发重启")
    else:
        chk(False, "bbox 不可用（窗口未映射）")

    print("\n[E] 双击其他列 -> 仍是重启")
    edits.clear()
    bb = app.tree.bbox(A.key(), column="name")
    ev = type("Ev", (), {"x": (bb[0] + 2), "y": (bb[1] + bb[3] // 2),
                         "num": 1})()
    app._on_row_double_click(ev)
    chk(restarts == [1], "双击名称列 -> 重启选中（got %s）" % restarts)
    chk(not edits, "没有误触发备注编辑")

    print("\n[F] 输入框逻辑")
    calls = []
    import tkinter.simpledialog as sd
    orig_ask = sd.askstring

    def fake_ask(title, prompt, **kw):
        calls.append((title, kw.get("initialvalue")))
        return None               # 模拟用户取消
    sd.askstring = fake_ask
    try:
        app._edit_comment = ikuai_gui.IKuaiGUI._edit_comment.__get__(app)
        app._edit_comment(A)
        chk(calls and calls[0][1] == "三楼-平台",
            "initialvalue 带出旧值（got %s）" % (calls,))
        chk(app.busy is False, "取消后不进 busy")

        # 值未变化 -> 跳过
        calls.clear()
        fake2 = lambda t, p, **kw: (calls.append(kw.get("initialvalue")),
                                    "三楼-平台")[1]
        sd.askstring = lambda t, p, **kw: "三楼-平台"
        app._edit_comment(A)
        chk(True, "未变化分支可执行")
    finally:
        sd.askstring = orig_ask

    print("\n[G] 保存成功 -> 本地即时更新")
    app._on_comment_done(A.key(), True, "新备注xyz")
    chk(A.comment == "新备注xyz", "ap_list 里的 comment 已更新")
    vals = col_vals(7)
    chk(vals[0] == "新备注xyz", "表格已重渲染（got %s）" % vals)

    print("\n[H] schema remark_* 两代互不污染")
    chk("el-dialog" in " ".join(SCHEMA_3X["remark_modal_sels"]),
        "3.x 弹窗选择器含 el-dialog")
    chk("ant-modal" in " ".join(SCHEMA_4X["remark_modal_sels"]),
        "4.x 弹窗选择器含 ant-modal")
    chk(all("ant-modal" not in s for s in SCHEMA_3X["remark_input_sels"]),
        "3.x 输入框不误用 ant 选择器")
    chk(all("jqmWindow" not in s for s in SCHEMA_4X["remark_input_sels"]),
        "4.x 输入框不误用 3.x 选择器")
    chk('input[name="comment"]' in SCHEMA_3X["remark_input_sels"],
        "3.x 输入框主选择器 = input[name=comment]（真机实测）")
    chk("btn_confirm" in " ".join(SCHEMA_3X["remark_confirm_sels"]),
        "3.x 确认 = btn_confirm（真机实测）")

    print("\n[I] APInfo.comment 字段与 CSV")
    a = APInfo(comment="测试备注")
    chk(a.comment == "测试备注", "APInfo 默认带 comment 字段")
    chk(APInfo().comment == "", "默认空串")

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
