#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
线程模型回归测试 —— 专治 "cannot switch to a different thread"。

背景：Playwright 的 sync API 对象绑定在创建它的线程上。之前的 bug 是
浏览器在后台线程 A 里 launch，UI 线程 B 却直接调 page.xxx()，于是
每次都抛：
    cannot switch to a different thread (which happens to have exited)

本测试用**假浏览器**（记录自己运行在哪个线程）来验证：
  1. 所有公开方法最终都在同一个（工作）线程里操作浏览器对象
  2. 从任意线程并发调用都安全，且不会死锁
  3. restart_aps 内部重入不会自己等自己
  4. stop() 之后工作线程确实退出

不需要真实路由器，纯离线可跑。
"""

import sys
import threading
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import ikuai_service as S

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK]   %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, detail))


# ------------------------------------------------------------------
# 假 Playwright —— 每次访问都记下当前线程 id
# ------------------------------------------------------------------
class FakeThreadRecorder:
    """记录所有 Playwright 调用发生在哪个线程。"""

    def __init__(self):
        self.threads = set()      # 所有发生浏览器调用的线程 id
        self.owner = None         # 创建它的线程 id
        self.calls = 0

    def touch(self, what):
        tid = threading.get_ident()
        self.threads.add(tid)
        self.calls += 1
        return tid


class FakeLocator:
    def __init__(self, rec, n=0):
        self._rec = rec
        self._n = n

    def count(self):
        self._rec.touch("count")
        return self._n

    def first(self):
        self._rec.touch("first")
        return self

    def nth(self, i):
        self._rec.touch("nth")
        return FakeLocator(self._rec, 1)

    def inner_text(self):
        self._rec.touch("inner_text")
        return "fake"

    def fill(self, v):
        self._rec.touch("fill")

    def click(self):
        self._rec.touch("click")

    def locator(self, sel):
        self._rec.touch("locator")
        return FakeLocator(self._rec, 0)


class FakePage:
    def __init__(self, rec):
        self._rec = rec

    def set_default_timeout(self, t):
        self._rec.touch("set_default_timeout")

    def goto(self, *a, **k):
        self._rec.touch("goto")

    def wait_for_timeout(self, t):
        self._rec.touch("wait_for_timeout")

    def wait_for_selector(self, *a, **k):
        self._rec.touch("wait_for_selector")

    def locator(self, sel):
        self._rec.touch("locator")
        return FakeLocator(self._rec, 0)

    def inner_text(self, sel):
        self._rec.touch("inner_text")
        return ""

    def screenshot(self, **k):
        self._rec.touch("screenshot")

    @property
    def keyboard(self):
        self._rec.touch("keyboard")
        return self


class FakeBrowser:
    def __init__(self, rec):
        self._rec = rec

    def close(self):
        self._rec.touch("close")

    def new_context(self, **k):
        self._rec.touch("new_context")
        return FakeContext(self._rec)


class FakeContext:
    def __init__(self, rec):
        self._rec = rec

    def new_page(self):
        self._rec.touch("new_page")
        return FakePage(self._rec)


# ------------------------------------------------------------------
# 1. 直接验证 _submit 的执行线程
# ------------------------------------------------------------------
def test_submit_runs_on_worker():
    print("\n[1] _submit 把任务放到工作线程执行")
    svc = S.IKuaiService()

    caller = threading.get_ident()
    seen = {}

    def job():
        seen["tid"] = threading.get_ident()
        return 42

    r = svc._submit(job, timeout=5)
    check("返回值正确传递", r == 42, "got %r" % r)
    check("执行线程 != 调用线程", seen["tid"] != caller,
          "caller=%s worker=%s" % (caller, seen["tid"]))
    check("执行线程 == 工作线程", seen["tid"] == svc._worker_id,
          "seen=%s worker_id=%s" % (seen["tid"], svc._worker_id))

    svc.stop()


# ------------------------------------------------------------------
# 2. 并发调用同一服务 —— 全部落在同一线程
# ------------------------------------------------------------------
def test_concurrent_calls():
    print("\n[2] 多线程并发调用，Playwright 调用始终在同一线程")
    svc = S.IKuaiService()
    rec = FakeThreadRecorder()

    # 手动把假浏览器挂上去，绕过真实 launch
    svc._rec = rec

    def fake_op():
        return rec.touch("op")

    results = []
    lock = threading.Lock()
    errors = []

    def worker():
        for _ in range(15):
            try:
                tid = svc._submit(fake_op, timeout=10)
                with lock:
                    results.append(tid)
            except Exception as ex:
                with lock:
                    errors.append(ex)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    check("无异常", not errors, "; ".join(str(e) for e in errors[:3]))
    check("调用次数 = 4*15 = 60", len(results) == 60, "got %d" % len(results))
    check("所有调用都发生在一个线程里", len(rec.threads) == 1,
          "threads=%s" % rec.threads)
    check("该线程正是工作线程", rec.threads == {svc._worker_id},
          "rec=%s worker_id=%s" % (rec.threads, svc._worker_id))

    svc.stop()


# ------------------------------------------------------------------
# 3. 重入保护：工作线程内部调用 _submit 不会死锁
# ------------------------------------------------------------------
def test_reentrancy():
    print("\n[3] 工作线程内重入调用不会死锁")
    svc = S.IKuaiService()
    tids = []

    def inner():
        tids.append(threading.get_ident())
        return "inner"

    def outer():
        tids.append(threading.get_ident())
        # 在工作线程里再投一个任务 —— 如果没做重入保护，这里会卡死
        return svc._submit(inner, timeout=5)

    t0 = time.time()
    r = svc._submit(outer, timeout=10)
    elapsed = time.time() - t0

    check("重入调用正常返回", r == "inner", "got %r" % r)
    check("内层外层同线程", len(set(tids)) == 1, "tids=%s" % tids)
    check("没有卡死（< 3 秒）", elapsed < 3, "%.2fs" % elapsed)

    svc.stop()


# ------------------------------------------------------------------
# 4. restart_aps 内部的嵌套调用不会自锁
# ------------------------------------------------------------------
def test_restart_aps_no_deadlock():
    print("\n[4] restart_aps 批量重启不会自锁")
    svc = S.IKuaiService()
    rec = FakeThreadRecorder()

    svc._browser = object()   # 让 browser_started 为真
    svc._connected = True

    order = []

    def fake_restart_ap(ap, progress_cb=None):
        order.append(("restart", ap.name, threading.get_ident()))
        return True

    def fake_fetch(force_reload=False):
        order.append(("fetch", threading.get_ident()))
        return []

    svc.restart_ap = fake_restart_ap
    svc.fetch_ap_list = fake_fetch

    aps = [S.APInfo(name="ap%d" % i, mac="aa:bb:cc:00:00:0%d" % i)
           for i in range(3)]

    t0 = time.time()
    ok, total = svc.restart_aps(aps)
    elapsed = time.time() - t0

    check("成功数 3 / 总数 3", (ok, total) == (3, 3), "got %s" % ((ok, total),))
    check("没有卡死（< 15 秒）", elapsed < 15, "%.2fs" % elapsed)
    check("3 次 restart 全部执行",
          len([o for o in order if o[0] == "restart"]) == 3)
    check("2 次 fetch（最后一台后不刷）",
          len([o for o in order if o[0] == "fetch"]) == 2)
    check("全部在同一线程",
          len({o[-1] for o in order}) == 1,
          "threads=%s" % {o[-1] for o in order})

    svc.stop()


# ------------------------------------------------------------------
# 5. stop() 之后工作线程退出
# ------------------------------------------------------------------
def test_stop_joins_worker():
    print("\n[5] stop() 正确回收工作线程")
    svc = S.IKuaiService()
    w = svc._worker
    check("初始有工作线程", w is not None and w.is_alive())

    svc.stop()
    check("stop 后工作线程已退出", not w.is_alive())
    check("stop 后 _worker 置空", svc._worker is None)
    check("stop 后 _worker_id 置空", svc._worker_id is None)
    check("stop 后 _closed = True", svc._closed is True)

    # 关闭后调用应该抛错，而不是静默挂起
    err = None
    try:
        svc._submit(lambda: 1, timeout=2)
    except Exception as ex:
        err = ex
    check("关闭后调用会快速报错而非挂起", err is not None, "no error raised")


# ------------------------------------------------------------------
# 6. 服务方法清单体检 —— 确保没有漏改的旧写法
# ------------------------------------------------------------------
def test_no_stale_lock_patterns():
    print("\n[6] 静态检查：确保浏览器操作都在 _submit 内")
    import ast
    src = open(S.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    cls = [n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "IKuaiService"][0]

    # 这些方法必须使用 _submit（即不能裸访问 self._page）
    must_submit = ["start", "stop", "connect", "fetch_ap_list",
                   "restart_ap", "restart_aps"]
    names = {f.name: f for f in cls.body if isinstance(f, ast.FunctionDef)}

    for nm in must_submit:
        fn = names.get(nm)
        if fn is None:
            check("方法存在: %s" % nm, False)
            continue
        seg = ast.get_source_segment(src, fn) or ""
        check("%s 使用 _submit" % nm, "_submit(" in seg,
              "未找到 _submit 调用")

    # 确认没有重复定义
    allnames = [f.name for f in cls.body if isinstance(f, ast.FunctionDef)]
    dupes = {n for n in allnames if allnames.count(n) > 1}
    check("无重复方法定义", not dupes, "重复: %s" % dupes)

    # restart_ap / restart_aps 不应再直接 with self._lock 包住浏览器操作
    for nm in ("restart_ap", "restart_aps"):
        seg = ast.get_source_segment(src, names[nm]) or ""
        check("%s 不再裸用 with self._lock" % nm,
              "with self._lock:" not in seg,
              "仍存在 with self._lock:")

    # _read_ap_rows / _click_refresh_button 属于"必须在工作线程内调用"的
    # 私有方法，它们不该自己再 _submit（否则与调用方嵌套）。
    for nm in ("_read_ap_rows", "_click_refresh_button", "_read_header_map"):
        fn = names.get(nm)
        if fn is None:
            check("方法存在: %s" % nm, False)
            continue
        seg = ast.get_source_segment(src, fn) or ""
        check("%s 不自行 _submit（依赖调用方已在工作线程）" % nm,
              "_submit(" not in seg)


# ------------------------------------------------------------------
# 7. 刷新走轻量路径 —— 不反复 goto 造成"断开重连"
# ------------------------------------------------------------------
def test_light_refresh_path():
    print("\n[7] 刷新走轻量路径，不反复重载 SPA")
    svc = S.IKuaiService()

    gotos = []
    clicks = []
    fetch_payloads = []

    class Btn:
        def count(self):
            return 1

        def is_visible(self):
            return True

        @property
        def first(self):
            return self

        def click(self):
            clicks.append(1)

        def evaluate(self, js):
            clicks.append(1)

    class Row:
        def count(self):
            return 0

    class Pg:
        def __init__(self):
            self.url = "http://x/login#/wirelessService/apManagement"

        def goto(self, *a, **k):
            gotos.append(a[0] if a else "")

        def wait_for_selector(self, *a, **k):
            pass

        def wait_for_timeout(self, *a, **k):
            pass

        def evaluate(self, js, arg=None):
            # 记录重放的数据请求；返回 True 表示"成功"
            if arg is not None and isinstance(arg, dict) and "body" in arg:
                fetch_payloads.append(arg["body"])
                return True
            return ""

        def locator(self, sel):
            return Btn()

    svc._page = Pg()
    svc._connected = True
    svc._read_ap_rows = lambda: []

    # 第 1 次：必须 goto（页面还没加载过）
    svc.fetch_ap_list()
    check("首次刷新会 goto 打开 AP 页", len(gotos) == 1, "gotos=%d" % len(gotos))

    # 第 2、3 次：应走轻量路径，不再 goto
    svc.fetch_ap_list()
    svc.fetch_ap_list()
    check("后续刷新不再 goto（共 1 次）", len(gotos) == 1, "gotos=%d" % len(gotos))
    check("后续刷新重放了数据请求", len(fetch_payloads) >= 2,
          "payloads=%d" % len(fetch_payloads))
    check("重放的请求是 ac_server/show",
          bool(fetch_payloads) and fetch_payloads[0].get("func_name") == "ac_server",
          "got %r" % (fetch_payloads[:1],))
    check("请求参数含 total,data",
          bool(fetch_payloads)
          and "total" in str(fetch_payloads[0].get("param"))
          and "data" in str(fetch_payloads[0].get("param")),
          "got %r" % (fetch_payloads[:1],))

    # force_reload=True 应强制整页加载
    svc.fetch_ap_list(force_reload=True)
    check("force_reload 强制 goto", len(gotos) == 2, "gotos=%d" % len(gotos))

    # 页面被导航走了 -> 应自动退回 goto
    svc._page.url = "http://x/login#/homepage"
    svc.fetch_ap_list()
    check("页面不在 AP 页时自动 goto", len(gotos) == 3, "gotos=%d" % len(gotos))

    svc.stop()


# ------------------------------------------------------------------
# 7b. 轻量刷新失败时应逐级降级，而不是直接崩
# ------------------------------------------------------------------
def test_refresh_fallback_chain():
    print("\n[7b] 轻量刷新失败时的降级链")
    svc = S.IKuaiService()

    gotos = []

    class Pg:
        def __init__(self):
            self.url = "http://x/login#/wirelessService/apManagement"

        def goto(self, *a, **k):
            gotos.append(1)

        def wait_for_selector(self, *a, **k):
            pass

        def wait_for_timeout(self, *a, **k):
            pass

        def evaluate(self, js, arg=None):
            # 模拟"重放请求失败"
            if arg is not None:
                return False
            return ""

        def locator(self, sel):
            class L:
                def count(self):
                    return 0
            return L()

    svc._page = Pg()
    svc._connected = True
    svc._read_ap_rows = lambda: []
    svc._ap_page_ready = True

    svc.fetch_ap_list()
    check("重放失败 + 无按钮 -> 降级到整页 goto",
          len(gotos) == 1, "gotos=%d" % len(gotos))

    svc.stop()


# ------------------------------------------------------------------
# 8. 断开后标志位正确复位
# ------------------------------------------------------------------
def test_disconnect_resets_flags():
    print("\n[8] stop/connect 正确复位页面状态")
    svc = S.IKuaiService()
    svc._ap_page_ready = True
    svc._connected = True

    svc.stop()
    check("stop 后 _ap_page_ready 复位", svc._ap_page_ready is False,
          "got %r" % svc._ap_page_ready)
    check("stop 后 _connected 复位", svc._connected is False,
          "got %r" % svc._connected)


def test_schema_3x_helpers():
    """
    [9] 3.x 适配：单元格拆分逻辑

    3.x 与 4.x 最大的差别是**多个字段塞在同一个 td 里**（用换行分隔）：
      td[0] = "MAC\\nIP"
      td[1] = "状态\\n在线时长"
      td[3] = "备注名\\nSSID"
    这些解析函数是纯函数，用真机抓到的原始字符串直接验证。
    """
    print("\n[9] 3.x 适配：单元格拆分")

    # ---- MAC/IP 合并单元格 ----
    mac, ip = S.IKuaiService._split_mac_ip("08:9b:4b:4c:75:c4\n192.168.9.220")
    check("MAC/IP 拆分：MAC 正确", mac == "08:9b:4b:4c:75:c4", "got %r" % mac)
    check("MAC/IP 拆分：IP 正确", ip == "192.168.9.220", "got %r" % ip)

    mac2, ip2 = S.IKuaiService._split_mac_ip("08:9b:4b:4c:75:c4")
    check("只有 MAC 时：MAC 正确", mac2 == "08:9b:4b:4c:75:c4", "got %r" % mac2)
    check("只有 MAC 时：IP 为空", ip2 == "", "got %r" % ip2)

    mac3, ip3 = S.IKuaiService._split_mac_ip("")
    check("空单元格不崩", mac3 == "" and ip3 == "", "got %r,%r" % (mac3, ip3))

    # 离线设备爱快把 IP 显示成 "- -"
    mac4, ip4 = S.IKuaiService._split_mac_ip("08:9b:4b:4c:75:c4\n- -")
    check("离线设备：MAC 仍能取到", mac4 == "08:9b:4b:4c:75:c4", "got %r" % mac4)
    check("离线设备：IP 不会误抓 MAC 片段", "9b" not in ip4, "got %r" % ip4)

    # ---- 状态/在线时长合并单元格 ----
    st, up = S.IKuaiService._split_status_uptime("正常\n3天7时7分")
    check("状态/时长拆分：状态归一化为在线", st == "在线", "got %r" % st)
    check("状态/时长拆分：时长正确", up == "3天7时7分", "got %r" % up)

    st2, up2 = S.IKuaiService._split_status_uptime("未连接\n-")
    check("未连接 -> 断开", st2 == "断开", "got %r" % st2)

    st3, _ = S.IKuaiService._split_status_uptime("升级中\n1分")
    check("升级中文案保留", st3 == "升级中", "got %r" % st3)

    st4, up4 = S.IKuaiService._split_status_uptime("正常")
    check("只有状态时：状态正确", st4 == "在线", "got %r" % st4)
    check("只有状态时：时长为空", up4 == "", "got %r" % up4)

    # ---- 备注名/SSID 合并单元格 ----
    nm, ssid = S.IKuaiService._split_name_ssid("小花玩舍\nXiaoHuaWS")
    check("名称/SSID 拆分：名称正确", nm == "小花玩舍", "got %r" % nm)
    check("名称/SSID 拆分：SSID 正确", ssid == "XiaoHuaWS", "got %r" % ssid)

    nm2, ssid2 = S.IKuaiService._split_name_ssid("XiaoHuaWS")
    check("只有一段时当名称", nm2 == "XiaoHuaWS", "got %r" % nm2)
    check("只有一段时 SSID 为空", ssid2 == "", "got %r" % ssid2)


def test_schema_selection():
    """
    [10] 版本探测：能区分 3.x / 4.x 并选对 schema
    """
    print("\n[10] 版本探测与 schema 选择")

    # 3.x 的 URL 特征
    svc = S.IKuaiService(log_cb=lambda m: None, headless=True)
    try:
        class FakePage:
            def __init__(self, url, ant_rows=0, table_rows=0):
                self.url = url
                self._ant = ant_rows
                self._tab = table_rows
            def locator(self, sel):
                return FakeLoc(self._ant if "ant-table-row" in sel else self._tab)
            def evaluate(self, *a, **k):
                return ""
        class FakeLoc:
            def __init__(self, n):
                self._n = n
            def count(self):
                return self._n

        svc._page = FakePage("http://x/#/ac/ap-config")
        svc._detect_schema(quiet=True)
        check("URL 含 ac/ap-config -> 3.x", svc._schema["name"] == "3.x",
              "got %s" % svc._schema["name"])

        svc._page = FakePage("http://x/#/wirelessService/apManagement",
                             ant_rows=5)
        svc._detect_schema(quiet=True)
        check("URL 含 apManagement -> 4.x", svc._schema["name"] == "4.x",
              "got %s" % svc._schema["name"])

        # DOM 判据：没有 URL 线索时看表格结构
        svc._page = FakePage("http://x/#/", table_rows=13)
        svc._detect_schema(quiet=True)
        check("有 table.table -> 3.x", svc._schema["name"] == "3.x",
              "got %s" % svc._schema["name"])

        svc._page = FakePage("http://x/#/", ant_rows=7)
        svc._detect_schema(quiet=True)
        check("有 .ant-table-row -> 4.x", svc._schema["name"] == "4.x",
              "got %s" % svc._schema["name"])
    finally:
        svc._page = None
        svc.stop()

    # 两套 schema 的关键字段必须都在
    for key in ("login_path", "ap_url", "ap_route", "row_sel", "cell_sel",
                "user_sel", "pwd_sel", "btn_sels", "row_skip_head", "cols"):
        check("SCHEMA_3X 含 %s" % key, key in S.SCHEMA_3X, "missing")
        check("SCHEMA_4X 含 %s" % key, key in S.SCHEMA_4X, "missing")

    check("3.x 用 tr 作为行选择器", "tr" in S.SCHEMA_3X["row_sel"],
          "got %r" % S.SCHEMA_3X["row_sel"])
    check("4.x 用 .ant-table-row", S.SCHEMA_4X["row_sel"] == ".ant-table-row",
          "got %r" % S.SCHEMA_4X["row_sel"])
    check("3.x 需要跳过表头行", S.SCHEMA_3X["row_skip_head"] == 1,
          "got %r" % S.SCHEMA_3X["row_skip_head"])
    check("3.x 有固定列索引 cols_idx", "cols_idx" in S.SCHEMA_3X, "missing")


def test_login_page_detection():
    """
    [11] 登录页判定不能依赖 password 输入框数量

    实测坑（最关键的回归项）：
      iKuai 3.7.26 后台登录**成功后**，页面上仍有 4 个
      input[type=password]（改密码、WiFi 密码等）。
      旧代码用 count() > 0 判断"是否仍在登录页"，导致登录明明成功
      却报错「登录后仍停留在登录页，请检查账号密码」。
    这里锁死新逻辑的正确行为。
    """
    print("\n[11] 登录页判定（不依赖 password 数量）")

    svc = S.IKuaiService(log_cb=lambda m: None, headless=True)
    try:
        class FakePage:
            def __init__(self, url, body="", user_ipt=0):
                self.url = url
                self._body = body
                self._user = user_ipt
            def locator(self, sel):
                return FakeLoc(self._user if "#usernameIpt" in sel else 0)
            def inner_text(self, sel):
                return self._body
        class FakeLoc:
            def __init__(self, n):
                self._n = n
            def count(self):
                return self._n

        # 后台页（3.x 实测 URL），即使有 password 框也不算登录页
        svc._page = FakePage("http://x/#/system-overview",
                             body="系统概况 状态监控 AC管理")
        check("后台页 URL -> 不在登录页", svc._on_login_page() is False)

        # 后台页但 body 里有"记住密码"（其他页面可能残留字样）
        svc._page = FakePage("http://x/#/system-overview",
                             body="记住密码 登录 系统概况 AC管理")
        check("后台页 + 菜单特征 -> 不在登录页",
              svc._on_login_page() is False)

        # 真正的登录页
        svc._page = FakePage("http://x/login#/login",
                             body="登录 记住密码")
        check("登录页 URL -> 在登录页", svc._on_login_page() is True)

        # URL 没有 /login 但页面是登录页
        svc._page = FakePage("http://x/", body="登录 记住密码")
        check("无 /login 但正文是登录页 -> 判定在登录页",
              svc._on_login_page() is True)

        # 有 usernameIpt 说明还在登录页
        svc._page = FakePage("http://x/", body="登录", user_ipt=1)
        check("存在 #usernameIpt -> 判定在登录页",
              svc._on_login_page() is True)
    finally:
        svc._page = None
        svc.stop()

    # 静态确认：旧的危险写法已消失
    src = open(S.__file__, encoding="utf-8").read()
    check("不再用 password 数量判断是否停留登录页",
          'if self._page.locator(\'input[type="password"]\').count() > 0:'
          not in src,
          "旧写法仍在")


def test_3x_table_not_ready_too_early():
    """
    [12] 3.x 表格等待：不能只等行选择器

    实测坑：3.x 的**表头本身就是一个 tr**，`table.table tr` 在数据
    还没回来时就已经匹配到 1 个元素。用 wait_for_selector 会立刻返回，
    随后读到 0 台 AP。
    必须等到「行数 > 表头行数」才算就绪。
    """
    print("\n[12] 3.x 表格就绪判定（前几轮实测踩过的坑）")

    svc = S.IKuaiService(log_cb=lambda m: None, headless=True)
    try:
        class FakePage:
            def __init__(self, counts):
                # counts: 每次问 count() 依次返回的值
                self._counts = list(counts)
                self._i = 0
            def locator(self, sel):
                return FakeLoc(self)
            def wait_for_timeout(self, ms):
                pass
        class FakeLoc:
            def __init__(self, parent):
                self._p = parent
            def count(self):
                p = self._p
                if p._i < len(p._counts):
                    v = p._counts[p._i]
                    p._i += 1
                    return v
                return p._counts[-1] if p._counts else 0

        # 表头先出现(1) -> 稍后数据到齐(13) -> 应判为就绪
        svc._schema = dict(S.SCHEMA_3X)
        svc._page = FakePage([1, 1, 13])
        check("表头先到、数据后到 -> 最终判为就绪",
              svc._wait_ap_table(timeout_ms=2000) is True)

        # 永远只有表头 -> 应超时返回 False
        svc._page = FakePage([1])
        check("永远只有表头 -> 超时判为未就绪",
              svc._wait_ap_table(timeout_ms=800) is False)

        # 4.x 没有表头行，出现 1 行就算就绪
        svc._schema = dict(S.SCHEMA_4X)
        svc._page = FakePage([1])
        check("4.x 出现 1 行即为就绪",
              svc._wait_ap_table(timeout_ms=800) is True)
    finally:
        svc._page = None
        svc.stop()


def main():
    print("=" * 62)
    print("ikuai_service.py 线程模型回归测试")
    print("=" * 62)

    cases = [
        test_submit_runs_on_worker,
        test_concurrent_calls,
        test_reentrancy,
        test_restart_aps_no_deadlock,
        test_stop_joins_worker,
        test_no_stale_lock_patterns,
        test_light_refresh_path,
        test_refresh_fallback_chain,
        test_disconnect_resets_flags,
        test_schema_3x_helpers,
        test_schema_selection,
        test_login_page_detection,
        test_3x_table_not_ready_too_early,
    ]
    for c in cases:
        try:
            c()
        except Exception:
            print("  [ERROR] %s 抛出异常：" % c.__name__)
            traceback.print_exc()

    print("\n" + "=" * 62)
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
