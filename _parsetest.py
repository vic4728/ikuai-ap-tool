#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""字段解析测试（不启动浏览器）—— 用实测表格数据验证 ikuai_service 的解析逻辑。"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ikuai_service import APInfo, IKuaiService

OUT = Path(__file__).resolve().parent / "output"
OUT.mkdir(exist_ok=True)
LOG = OUT / "parse_test.txt"
lines = []


def say(s=""):
    lines.append(str(s))


# ---------------- 实测表头（来自 table_structure.txt） ----------------
HEADERS = [
    "", "名称", "MAC地址", "IP地址", "分组", "在线时长", "状态",
    "2.4G SSID", "5G R1 SSID", "5G R2 SSID", "当前版本",
    "上行链路协商速率", "信道", "最大带机量", "型号", "信道利用率",
    "信道底噪", "最低接入信号", "上行速率", "下行速率", "备注", "操作",
]

# 实测在线设备行
ROW_ONLINE = [
    "", "IK-H17V2_a482", "08:9b:4b:47:a4:82", "192.168.50.111", "VIC",
    "14天22时11分5秒", "已连接", "VIC-HOME", "VIC-HOME-5G", "VIC-HOME-R2",
    "1.7.6", "1000 Mbps", "11", "50", "1000 Mbps", "23",
    "-95", "-75", "86.5 Mbps", "120.3 Mbps", "", "重启 | 删除",
]

# 实测离线/重启中设备行（IP 与在线时长变成 "- -"）
ROW_OFFLINE = [
    "", "IK-H17V2_a482", "08:9b:4b:47:a4:82", "- -", "VIC",
    "- -", "已连接", "VIC-HOME", "VIC-HOME-5G", "VIC-HOME-R2",
    "1.7.6", "- -", "11", "50", "1000 Mbps", "23",
    "-95", "-75", "", "", "", "删除",
]


def parse(texts, col_map):
    ap = APInfo(raw_cells=texts)
    ap.group = IKuaiService._pick(texts, col_map, ["分组"], 4)
    ap.name = IKuaiService._pick(texts, col_map, ["名称"], 1)
    ap.ip = IKuaiService._pick(texts, col_map, ["IP地址", "IP"], 3)
    ap.mac = IKuaiService._pick(texts, col_map, ["MAC地址", "MAC"], 2)
    ap.uptime = IKuaiService._pick(texts, col_map, ["在线时长", "运行时间"], 5)
    ap.status = IKuaiService._pick(texts, col_map, ["状态", "运行状态"], 6)
    ap.version = IKuaiService._pick(texts, col_map, ["当前版本", "版本"], -1)
    ap.model = IKuaiService._resolve_model(texts, col_map)
    if not ap.is_alive():
        ap.status = "断开"
    elif ap.status in ("- -", ""):
        ap.status = "已连接"
    return ap


say("=" * 64)
say("FIELD PARSING TEST (no browser)  -- v2")
say("=" * 64)
say()
say("[表头映射]")
col_map = {}
for i, h in enumerate(HEADERS):
    if h:
        col_map[h] = i
for k, v in col_map.items():
    say("   %-16s -> 索引 %d" % (k, v))

say()
say("[在线设备解析]")
ap = parse(ROW_ONLINE, col_map)
for label, val in [("分组", ap.group), ("名称", ap.name), ("型号", ap.model),
                   ("终端IP", ap.ip), ("终端MAC", ap.mac),
                   ("运行时间", ap.uptime), ("运行状态", ap.status),
                   ("版本(内部)", ap.version), ("key()", ap.key()),
                   ("is_alive()", ap.is_alive())]:
    say("   %-12s: %r" % (label, val))

say()
say("[离线设备解析]")
ap_off = parse(ROW_OFFLINE, col_map)
for label, val in [("终端IP", ap_off.ip), ("运行时间", ap_off.uptime),
                   ("运行状态", ap_off.status), ("is_alive()", ap_off.is_alive())]:
    say("   %-12s: %r" % (label, val))

say()
say("[断言]")
checks = [
    ("名称 == IK-H17V2_a482", ap.name, "IK-H17V2_a482"),
    ("MAC  == 08:9b:4b:47:a4:82", ap.mac, "08:9b:4b:47:a4:82"),
    ("IP   == 192.168.50.111", ap.ip, "192.168.50.111"),
    ("分组 == VIC", ap.group, "VIC"),
    ("运行时间 == 14天22时11分5秒", ap.uptime, "14天22时11分5秒"),
    ("运行状态 == 已连接", ap.status, "已连接"),
    ("key == 089b4b47a482", ap.key(), "089b4b47a482"),
    ("版本 == 1.7.6", ap.version, "1.7.6"),
]
all_pass = True
for label, got, expect in checks:
    ok = got == expect
    all_pass = all_pass and ok
    say("   %-28s %s  got=%r" % (label, "PASS" if ok else "FAIL", got))

# 型号不应是速率
m_ok = ap.model != "1000 Mbps"
all_pass = all_pass and m_ok
say("   %-28s %s  model=%r (不应是速率)" % ("型号不是 Mbps 值", "PASS" if m_ok else "FAIL", ap.model))

# 型号应能识别出 IK- 前缀
m2_ok = ap.model.upper().startswith("IK-")
all_pass = all_pass and m2_ok
say("   %-28s %s  model=%r" % ("型号含 IK- 前缀", "PASS" if m2_ok else "FAIL", ap.model))

# 离线判定
o_ok = (not ap_off.is_alive()) and ap_off.status == "断开"
all_pass = all_pass and o_ok
say("   %-28s %s  alive=%s status=%r" % ("离线设备 -> 断开", "PASS" if o_ok else "FAIL",
                                          ap_off.is_alive(), ap_off.status))

# key 归一化
k_ok = ap.key() == ap_off.key() == "089b4b47a482"
all_pass = all_pass and k_ok
say("   %-28s %s" % ("在线/离线 key 一致", "PASS" if k_ok else "FAIL"))

# _looks_like_model 边界
lm = [
    ("1000 Mbps", False), ("23", False), ("- -", False),
    ("IK-H17V2", True), ("AirEngine 5761-11", True),
]
say()
say("[_looks_like_model 边界]")
for v, expect in lm:
    got = IKuaiService._looks_like_model(v)
    ok = got == expect
    all_pass = all_pass and ok
    say("   %-24r -> %-5s %s" % (v, got, "PASS" if ok else "FAIL"))

say()
say("=" * 64)
say("RESULT: %s" % ("ALL PASS" if all_pass else "HAS FAILURES"))
say("=" * 64)

LOG.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
sys.exit(0 if all_pass else 1)
