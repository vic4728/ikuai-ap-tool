# -*- coding: utf-8 -*-
"""
重启流程回归测试（离线，用假 page，不需要真机）

锁定两个真实 bug：

【Bug 1】3.x 点「重启」后弹的是 **Element UI** 的 `.el-message-box__wrapper`，
而代码里的弹窗选择器全是 4.x 的 Ant Design（`.ant-modal` / `[class*=dialog]` …），
一个都匹配不到 -> 直接 return False，日志打「未出现确认弹窗」，
用户看到的现象就是「弹窗内也确认了，但没有重启生效」。

【Bug 2】`_locate_row()` 逐行判断：MAC 不中就退回名称匹配。
现场 12 台 AP **备注名全都叫「小花玩舍」**，于是第一行就靠名称命中，
**永远重启到第 1 台**。必须改为先全表扫 MAC，全不中才用名称，
且名称重名时拒绝误操作。
"""
import sys

sys.path.insert(0, r"E:\Visual\ikuai")
import ikuai_service as S


PASS = [0]
FAIL = []


def check(name, cond, extra=""):
    if cond:
        PASS[0] += 1
        print("  [OK]   %s" % name)
    else:
        FAIL.append(name)
        print("  [FAIL] %s %s" % (name, extra))


# ------------------------------------------------------------------
# 假 page / row，用来驱动 _locate_row
# ------------------------------------------------------------------
class FakeRow:
    def __init__(self, text):
        self._t = text

    def inner_text(self):
        return self._t


class FakeRows:
    def __init__(self, texts):
        self._texts = texts

    def count(self):
        return len(self._texts)

    def nth(self, i):
        return FakeRow(self._texts[i])


class FakePage:
    def __init__(self, texts, row_sel):
        self._rows = FakeRows(texts)
        self._row_sel = row_sel

    def locator(self, sel):
        if sel == self._row_sel:
            return self._rows
        raise AssertionError("意外的选择器: %r" % sel)


def mk_svc_with_rows(schema, texts):
    """造一个只有 _page/_schema 的假 service，够 _locate_row 用。"""
    svc = S.IKuaiService.__new__(S.IKuaiService)
    svc._schema = schema
    svc._page = FakePage(texts, schema["row_sel"])
    return svc


def ap(mac="", name="", ip=""):
    return S.APInfo(name=name, mac=mac, ip=ip)


def main():
    print("=" * 62)
    print("重启流程回归测试（离线）")
    print("=" * 62)

    # ==============================================================
    print()
    print("[A] schema 必须带两代各自的弹窗/确认选择器")
    for sch in (S.SCHEMA_3X, S.SCHEMA_4X):
        nm = sch["name"]
        check("%s 有 modal_sels" % nm, bool(sch.get("modal_sels")))
        check("%s 有 confirm_sels" % nm, bool(sch.get("confirm_sels")))
        check("%s 有 restart_link_sels" % nm,
              bool(sch.get("restart_link_sels")))

    # 核心断言：3.x 必须包含 Element UI 的类名
    m3 = " ".join(S.SCHEMA_3X.get("modal_sels", []))
    c3 = " ".join(S.SCHEMA_3X.get("confirm_sels", []))
    check("3.x modal_sels 含 el-message-box",
          "el-message-box" in m3, m3)
    check("3.x confirm_sels 含 el-button--primary",
          "el-button--primary" in c3, c3)

    # 4.x 必须仍是 Ant Design
    m4 = " ".join(S.SCHEMA_4X.get("modal_sels", []))
    c4 = " ".join(S.SCHEMA_4X.get("confirm_sels", []))
    check("4.x modal_sels 含 ant-modal", "ant-modal" in m4, m4)
    check("4.x confirm_sels 含 ant-modal-footer",
          "ant-modal-footer" in c4, c4)

    # 两代不能互相污染
    check("3.x 不误用 ant-modal", "ant-modal" not in m3)
    check("4.x 不误用 el-message-box", "el-message-box" not in m4)

    # ==============================================================
    print()
    print("[B] _locate_row：同名 12 台，必须靠 MAC 精确定位（Bug 2）")
    # 现场真实数据：12 台都叫「小花玩舍」，只有 MAC/IP 不同
    real = [
        ("08:9b:4b:4c:75:c4", "192.168.9.220"),
        ("08:9b:4b:4c:72:ac", "192.168.9.221"),
        ("08:9b:4b:4c:74:b9", "192.168.9.222"),
        ("08:9b:4b:4c:79:45", "192.168.9.223"),
        ("08:9b:4b:4c:74:bf", "192.168.9.224"),
        ("08:9b:4b:4c:7a:0b", "192.168.9.225"),
        ("08:9b:4b:4c:71:92", "192.168.9.226"),
        ("08:9b:4b:4c:78:df", "192.168.9.227"),
        ("08:9b:4b:4c:70:f9", "192.168.9.228"),
        ("08:9b:4b:4c:72:dc", "192.168.9.229"),
        ("08:9b:4b:4c:75:0d", "192.168.9.230"),
        ("08:9b:4b:4c:75:5e", "192.168.9.231"),
    ]
    # 3.x 的行文本形态：MAC + IP 在第一格，名称混杂在后面
    texts = ["%s\n%s\n\n正常\n3天8时56分\tAP-1\t小花玩舍\nXiaoHuaWS\t"
             "2.4G: 1(手动 1)\n5G：36(手动 36)\t\nIK-SW5\n\t\n\t\n \n\n"
             "终端详情 查看配置详情编辑 重启 删除" % (m, ip)
             for m, ip in real]
    # 注意 row_skip_head=1，第 0 行是表头
    texts = ["MAC/IP\t状态\t分组名称" ] + texts
    svc = mk_svc_with_rows(S.SCHEMA_3X, texts)

    # 逐个 AP：每台都必须定位到**自己那一行**
    ok_all = True
    mismatch = []
    for idx, (m, ip) in enumerate(real):
        target = ap(mac=m, name="小花玩舍", ip=ip)
        row = svc._locate_row(target)
        if row is None:
            ok_all = False
            mismatch.append("%s -> None" % m)
        elif ip not in row.inner_text():
            ok_all = False
            mismatch.append("%s -> 定位到 %s" % (m, ip))
    check("12 台同名 AP 各自定位到自己那行", ok_all,
          "；".join(mismatch[:3]))

    # 关键回归：最后一台绝不能定位到第一台
    last = ap(mac="08:9b:4b:4c:75:5e", name="小花玩舍", ip="192.168.9.231")
    row = svc._locate_row(last)
    check("最后一台不会串到第一台",
          row is not None and "192.168.9.231" in row.inner_text()
          and "192.168.9.220" not in row.inner_text(),
          "row=%r" % (row.inner_text()[:40] if row else None))

    # ==============================================================
    print()
    print("[C] _locate_row：MAC 全不中才退到名称")
    # MAC 不在表里 + 名称唯一 -> 应能靠名称找到
    texts2 = ["表头"] + [
        "aa:aa:aa:aa:aa:01\n10.0.0.1\n正常\tAP-1\t唯一别名A\tX\nIK-SW5\t\t\t\t\n重启",
        "aa:aa:aa:aa:aa:02\n10.0.0.2\n正常\tAP-2\t唯一别名B\tY\nIK-SW5\t\t\t\t\n重启",
    ]
    svc2 = mk_svc_with_rows(S.SCHEMA_3X, texts2)
    r = svc2._locate_row(ap(mac="ff:ff:ff:ff:ff:ff", name="唯一别名B"))
    check("MAC 不中 + 名称唯一 -> 靠名称命中",
          r is not None and "10.0.0.2" in r.inner_text())

    # 名称重名 + MAC 不在 -> 必须返回 None（拒绝瞎猜）
    texts3 = ["表头"] + [
        "aa:aa:aa:aa:aa:01\n10.0.0.1\n正常\tAP-1\t同名\tX\nIK-SW5\t\t\t\t\n重启",
        "aa:aa:aa:aa:aa:02\n10.0.0.2\n正常\tAP-2\t同名\tY\nIK-SW5\t\t\t\t\n重启",
    ]
    svc3 = mk_svc_with_rows(S.SCHEMA_3X, texts3)
    r = svc3._locate_row(ap(mac="ff:ff:ff:ff:ff:ff", name="同名"))
    check("名称重名且 MAC 不中 -> 返回 None（不误操作）", r is None)

    # 名称重名 + 有 IP -> 用 IP 消歧
    r = svc3._locate_row(ap(mac="ff:ff:ff:ff:ff:ff", name="同名",
                            ip="10.0.0.2"))
    check("名称重名 -> 用 IP 消歧",
          r is not None and "10.0.0.2" in r.inner_text())

    # ==============================================================
    print()
    print("[D] _locate_row：3.x 必须跳过表头行")
    svc4 = mk_svc_with_rows(S.SCHEMA_3X, [
        "MAC/IP\tIP\t分组名称\t备注",
        "aa:aa:aa:aa:aa:09\n10.9.9.9\n正常\tAP-9\t目标名\tX\nIK-SW5\t\t\t\t\n重启",
    ])
    r = svc4._locate_row(ap(mac="aa:aa:aa:aa:aa:09", name="目标名"))
    check("跳过表头，命中数据行",
          r is not None and "10.9.9.9" in r.inner_text())

    # 表头文本不该被当数据行返回
    r = svc4._locate_row(ap(mac="", name="MAC/IP"))
    check("表头不会被当数据行返回", r is None
          or "10.9.9.9" in r.inner_text())

    # ==============================================================
    print()
    print("[E] 空 MAC / 空名称 的防御")
    svc5 = mk_svc_with_rows(S.SCHEMA_3X, [
        "表头",
        "aa:aa:aa:aa:aa:01\n10.0.0.1\n正常\tAP-1\t名\tX\nIK-SW5\t\t\t\t\n重启",
    ])
    check("空 MAC + 空名称 -> None", svc5._locate_row(ap()) is None)
    check("空 MAC + 不存在的名称 -> None",
          svc5._locate_row(ap(name="不存在")) is None)

    # ==============================================================
    print()
    print("=" * 62)
    if FAIL:
        print("结果：%d 通过 / %d 失败" % (PASS[0], len(FAIL)))
        for f in FAIL:
            print("  - %s" % f)
        return 1
    print("结果：%d 通过 / 0 失败" % PASS[0])
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
