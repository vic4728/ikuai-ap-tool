# -*- coding: utf-8 -*-
"""
4.x 回归验证（离线，不需要真机登录）

用真实的 4.x DOM 结构造一个假 page，喂给 _read_ap_rows()，
确认新的 schema 分支没有破坏 4.x 的解析。
同时验证 3.x / 4.x 两条分支互不干扰。
"""
import sys
sys.path.insert(0, r"E:\Visual\ikuai")
import ikuai_service as S

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK]   %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, extra))


# ---- 假的 4.x 单元格/行/表 ----
class FakeCell:
    def __init__(self, t):
        self._t = t
    def inner_text(self):
        return self._t


class FakeCells:
    """row.locator('.ant-table-cell') 返回的东西"""
    def __init__(self, texts):
        self._texts = texts
    def count(self):
        return len(self._texts)
    def nth(self, i):
        return FakeCell(self._texts[i])


class FakeRow:
    def __init__(self, texts):
        self._texts = texts
    def locator(self, sel):
        return FakeCells(self._texts)
    def inner_text(self):
        return " ".join(self._texts)


class FakeRows:
    def __init__(self, rows):
        self._rows = rows
    def count(self):
        return len(self._rows)
    def nth(self, i):
        return self._rows[i]


class FakeHeads:
    """4.x 表头：.ant-table-thead th"""
    def __init__(self, names):
        self._names = [FakeCell(n) for n in names]
    def count(self):
        return len(self._names)
    def nth(self, i):
        return self._names[i]


class FakePage4x:
    """4.x：Ant Design 表格，列顺序 = 实测 4.x 的表头"""
    HEADERS = ["名称", "型号", "MAC地址", "IP地址", "分组", "在线时长",
               "运行状态", "当前版本", "操作"]

    def __init__(self, rows):
        self._rows = rows
        self._heads = FakeHeads(self.HEADERS)
    def locator(self, sel):
        if "ant-table-row" in sel:
            return FakeRows(self._rows)
        if "ant-table-thead" in sel:
            return self._heads
        return FakeRows([])


def test_4x_regression():
    print("\n[A] 4.x 解析回归（确保没被 3.x 改动破坏）")
    svc = S.IKuaiService(log_cb=lambda m: None, headless=True)
    try:
        svc._schema = dict(S.SCHEMA_4X)
        # 4.x 实测的一行：名称/型号/MAC/IP/分组/时长/状态/版本/操作
        rows = [
            ["AP-办公区", "IK-H17V2", "08:9b:4b:47:a4:82",
             "192.168.50.111", "默认分组", "3天7时2分", "正常", "1.7.0", "重启"],
            ["AP-会议室", "IK-H17V2", "08:9b:4b:47:a4:83",
             "192.168.50.112", "默认分组", "1天2时3分", "正常", "1.7.0", "重启"],
        ]
        svc._page = FakePage4x([FakeRow(r) for r in rows])
        aps = svc._read_ap_rows()
        check("4.x 读到 2 台", len(aps) == 2, "got %d" % len(aps))
        if aps:
            a = aps[0]
            check("4.x 名称正确", a.name == "AP-办公区", "got %r" % a.name)
            check("4.x 型号正确", a.model == "IK-H17V2", "got %r" % a.model)
            check("4.x MAC 正确", a.mac == "08:9b:4b:47:a4:82",
                  "got %r" % a.mac)
            check("4.x IP 正确", a.ip == "192.168.50.111", "got %r" % a.ip)
            check("4.x 分组正确", a.group == "默认分组", "got %r" % a.group)
            check("4.x 时长正确", a.uptime == "3天7时2分", "got %r" % a.uptime)
            check("4.x 状态存活", a.is_alive() is True)
    finally:
        svc._page = None
        svc.stop()


class FakePage3x:
    """3.x：table.table tr/td，首行是表头（td/th 混合）"""
    def __init__(self, rows):
        # rows[0] 是表头行（无数据），后面是数据行
        self._rows = rows
    def locator(self, sel):
        if sel.startswith("table.table tr"):
            return FakeRows(self._rows)
        if "first-child" in sel:
            return FakeHeads(["", "MAC/IP", "状态", "分组名称", "2.4G SSID",
                              "信道", "型号", "备注", "操作"])
        return FakeRows([])


def test_3x_parse():
    print("\n[B] 3.x 解析（用真机抓到的原始单元格）")
    svc = S.IKuaiService(log_cb=lambda m: None, headless=True)
    try:
        svc._schema = dict(S.SCHEMA_3X)
        header = FakeRow([])   # 表头行（0 个 td，靠 row_skip_head 跳过）
        r1 = FakeRow(["08:9b:4b:4c:75:c4\n192.168.9.220",
                      "正常\n3天7时7分",
                      "AP-1",
                      "小花玩舍\nXiaoHuaWS",
                      "2.4G: 1(手动 1)\n5G：36(手动 36)",
                      "IK-SW5",
                      "",
                      " \n\n终端详情 查看配置详情编辑 修改备注 移出分组 定位 重启 周边信道",
                      " "])
        r2 = FakeRow(["08:9b:4b:4c:72:ac\n- -",
                      "未连接\n-",
                      "AP-1",
                      "小花玩舍\nXiaoHuaWS",
                      "--",
                      "IK-SW5",
                      "",
                      "重启",
                      " "])
        svc._page = FakePage3x([header, r1, r2])
        aps = svc._read_ap_rows()
        check("3.x 读到 2 台（表头行被跳过）", len(aps) == 2,
              "got %d" % len(aps))
        if len(aps) >= 1:
            a = aps[0]
            check("3.x MAC 正确", a.mac == "08:9b:4b:4c:75:c4", "got %r" % a.mac)
            check("3.x IP 正确", a.ip == "192.168.9.220", "got %r" % a.ip)
            check("3.x 状态=在线", a.status == "在线", "got %r" % a.status)
            check("3.x 时长正确", a.uptime == "3天7时7分", "got %r" % a.uptime)
            check("3.x 分组正确", a.group == "AP-1", "got %r" % a.group)
            check("3.x 名称正确", a.name == "小花玩舍", "got %r" % a.name)
            check("3.x 型号正确", a.model == "IK-SW5", "got %r" % a.model)
        if len(aps) >= 2:
            b = aps[1]
            check("3.x 离线 AP：状态=断开", b.status == "断开",
                  "got %r" % b.status)
            check("3.x 离线 AP：IP 为空而非 MAC",
                  b.ip == "", "got %r" % b.ip)
            check("3.x 离线 AP：is_alive=False", b.is_alive() is False)
    finally:
        svc._page = None
        svc.stop()


def test_schemas_dont_cross():
    print("\n[C] 两套 schema 互不干扰")
    check("3.x row_sel 不含 ant",
          "ant" not in S.SCHEMA_3X["row_sel"], S.SCHEMA_3X["row_sel"])
    check("4.x row_sel 不含 table.tr",
          S.SCHEMA_4X["row_sel"] != S.SCHEMA_3X["row_sel"])
    check("3.x 与 4.x 的 ap_url 不同",
          S.SCHEMA_3X["ap_url"] != S.SCHEMA_4X["ap_url"])
    check("3.x 与 4.x 的 user_sel 不同",
          S.SCHEMA_3X["user_sel"] != S.SCHEMA_4X["user_sel"])
    check("3.x 有 cols_idx（3.x 专用）", "cols_idx" in S.SCHEMA_3X)
    check("4.x 无 cols_idx（走表头映射）", "cols_idx" not in S.SCHEMA_4X)


if __name__ == "__main__":
    print("=" * 62)
    print("3.x / 4.x 解析分支验证")
    print("=" * 62)
    test_4x_regression()
    test_3x_parse()
    test_schemas_dont_cross()
    print("\n" + "=" * 62)
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    print("=" * 62)
    sys.exit(1 if FAIL else 0)
