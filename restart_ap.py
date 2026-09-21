#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
爱快(iKuai) AP 自动重启脚本

功能：
  登录爱快 Web 后台 → 进入 AP 管理页 → 定位指定 AP → 点击重启 → 处理确认弹窗

安全设计：
  - 默认是「演练模式」：只走到弹出确认框就停下并截图，不会真的重启。
  - 必须显式加 --confirm 参数才会真正执行重启。
  - 支持按 AP 名称或 MAC 精确定位，避免点错设备。

用法：
  python restart_ap.py                      # 演练：显示会重启哪台 AP
  python restart_ap.py --confirm            # 真正执行重启
  python restart_ap.py --ap IK-H17V2_a482   # 指定 AP 名称
  python restart_ap.py --show               # 显示浏览器窗口，便于观察

配置：见 config.yaml 的 ssh / router 段
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError as exc:
    print("[错误] 缺少依赖: %s" % exc.name)
    print("请执行: pip install playwright pyyaml")
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

# Windows 控制台编码兜底
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ------------------------------------------------------------------
# 日志
# ------------------------------------------------------------------
def setup_logging(level_name: str = "INFO") -> logging.Logger:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ikuai_ap")
    logger.setLevel(getattr(logging, level_name.upper(), logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    log_file = OUTPUT_DIR / "restart_ap.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ------------------------------------------------------------------
# 登录
# ------------------------------------------------------------------
def do_login(page, cfg: dict, log: logging.Logger) -> None:
    router = cfg["router"]
    base = router["base_url"].rstrip("/")
    timeout = int(cfg.get("browser", {}).get("timeout_ms", 25000))

    log.info("打开后台: %s", base)
    page.goto(base + "/", timeout=timeout, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)

    # iKuai OS V4 登录页表单
    if page.locator('input[type="password"]').count() == 0:
        log.info("未检测到登录表单，可能已处于登录状态")
        return

    log.info("填入账号: %s", router["username"])
    ubox = page.locator('input[type="text"]').first
    pbox = page.locator('input[type="password"]').first
    ubox.fill(router["username"])
    pbox.fill(router["password"])
    page.wait_for_timeout(400)

    # 点登录
    clicked = False
    for sel in ['button:has-text("登录")', 'button:has-text("登陆")',
                'button[type="submit"]']:
        b = page.locator(sel)
        if b.count() > 0:
            log.info("点击登录按钮")
            b.first.click(timeout=timeout)
            clicked = True
            break
    if not clicked:
        raise RuntimeError("未找到登录按钮")

    page.wait_for_timeout(9000)

    # 校验
    body = page.inner_text("body")[:3000]
    for bad in ("密码错误", "用户名或密码", "登录失败"):
        if bad in body:
            raise RuntimeError("登录失败：账号或密码错误")
    log.info("登录成功，当前页面: %s", page.title())


# ------------------------------------------------------------------
# 进入 AP 管理页
# ------------------------------------------------------------------
def goto_ap_page(page, cfg: dict, log: logging.Logger) -> None:
    router = cfg["router"]
    base = router["base_url"].rstrip("/")
    url = base + "/login#/wirelessService/apManagement"
    log.info("进入 AP 管理页")
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(9000)
    log.info("当前页面: %s", page.title())


# ------------------------------------------------------------------
# 读取 AP 列表
# ------------------------------------------------------------------
def read_ap_list(page, log: logging.Logger) -> list[dict]:
    """
    从 AP 列表表格中读出每台 AP 的信息。

    注意：iKuai OS V4 用的是 Ant Design 虚拟滚动表格，
    渲染出来的是 <div class="ant-table-row"> 而不是 <tr>，
    所以不能用 table/tbody/tr 选择器，必须用 .ant-table-row。
    """
    aps = []
    rows = page.locator(".ant-table-row")
    cnt = rows.count()
    log.info("表格中找到 %d 台 AP", cnt)

    for i in range(cnt):
        row = rows.nth(i)
        try:
            # 单元格是 div.ant-table-cell，顺序：
            # [0]=勾选框 [1]=名称 [2]=MAC [3]=IP [4]=分组 ...
            cells = row.locator(".ant-table-cell")
            n = cells.count()
            if n < 4:
                continue
            name = cells.nth(1).inner_text().strip()
            mac = cells.nth(2).inner_text().strip()
            ip = cells.nth(3).inner_text().strip()

            # 再取几个附加信息，便于日志辨认
            extra = {}
            try:
                extra["uptime"] = cells.nth(5).inner_text().strip()
                extra["status"] = cells.nth(6).inner_text().strip()
            except Exception:
                pass

            rec = {"index": i, "name": name, "mac": mac, "ip": ip, "extra": extra}
            aps.append(rec)
            log.info("  AP[%d]: %s | MAC %s | IP %s | 状态 %s",
                     i, name, mac, ip, extra.get("status", "?"))
        except Exception as ex:
            log.debug("  行 %d 解析失败: %s", i, ex)

    return aps


# ------------------------------------------------------------------
# 重启 AP
# ------------------------------------------------------------------
def restart_ap(page, ap: dict, log: logging.Logger,
               confirm: bool = False,
               tag: str = "") -> bool:
    """
    重启指定的 AP。
    confirm=False 时只打开确认弹窗并截图，然后取消。
    """
    rows = page.locator(".ant-table-row")
    row = rows.nth(ap["index"])

    log.info("-" * 56)
    log.info("目标 AP: %s (MAC %s, IP %s)", ap["name"], ap["mac"], ap["ip"])

    # 定位该行操作列里的「重启」按钮（Ant Design 用 button 承载操作项）
    restart_btn = row.locator('button:has-text("重启")')
    if restart_btn.count() == 0:
        # 兜底：可能是 a 标签
        restart_btn = row.locator('a:has-text("重启")')
    if restart_btn.count() == 0:
        log.error("该行未找到「重启」按钮")
        return False

    log.info("点击「重启」按钮")
    restart_btn.first.click()

    # 等确认弹窗
    try:
        page.wait_for_selector(".ant-modal", timeout=8000)
    except PWTimeout:
        log.warning("未出现确认弹窗，可能操作方式有变")
        return False

    page.wait_for_timeout(1200)

    # 弹窗文案
    try:
        modal_text = page.locator(".ant-modal-body").inner_text().strip()
        log.info("弹窗提示: %s", modal_text)
    except Exception:
        modal_text = ""

    # 截图留证
    shot = OUTPUT_DIR / ("restart_dialog_%s.png"
                         % (tag or datetime.now().strftime("%H%M%S")))
    page.screenshot(path=str(shot), full_page=True)
    log.info("弹窗截图: %s", shot)

    if not confirm:
        # 演练模式：取消
        log.warning("【演练模式】不执行重启，点击取消")
        cancel = page.locator('.ant-modal-footer button:has-text("取消")')
        if cancel.count() > 0:
            cancel.first.click()
            page.wait_for_timeout(1000)
        else:
            page.keyboard.press("Escape")
        return False

    # 真正执行重启
    log.warning("【正式执行】确认重启 %s", ap["name"])
    confirm_btn = page.locator('.ant-modal-footer button:has-text("重启")')
    if confirm_btn.count() == 0:
        confirm_btn = page.locator(".ant-modal-footer button.ant-btn-primary")
    if confirm_btn.count() == 0:
        log.error("未找到确认按钮，已中止")
        page.keyboard.press("Escape")
        return False

    confirm_btn.first.click()
    log.info("已点击确认，等待路由器处理...")
    page.wait_for_timeout(5000)

    # 结果截图
    shot2 = OUTPUT_DIR / ("restart_result_%s.png"
                          % (tag or datetime.now().strftime("%H%M%S")))
    page.screenshot(path=str(shot2), full_page=True)
    log.info("结果截图: %s", shot2)

    # 看是否有提示信息
    try:
        body = page.inner_text("body")
        for kw in ("重启成功", "操作成功", "已下发", "失败", "错误"):
            if kw in body:
                log.info("页面提示包含: %s", kw)
    except Exception:
        pass

    log.info("重启指令已下发完成")
    return True


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="爱快 AP 自动重启")
    parser.add_argument("-c", "--config", default=str(BASE_DIR / "config.yaml"))
    parser.add_argument("--ap", help="目标 AP 名称（不指定则重启全部）")
    parser.add_argument("--mac", help="目标 AP MAC 地址")
    parser.add_argument("--confirm", action="store_true",
                        help="真正执行重启（不加此参数则只是演练）")
    parser.add_argument("--show", action="store_true",
                        help="显示浏览器窗口")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print("[错误] 配置文件不存在: %s" % cfg_path)
        return 1
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    log = setup_logging(str((cfg.get("logging") or {}).get("level", "INFO")))
    log.info("=" * 56)
    log.info("爱快 AP 自动重启")
    log.info("模式: %s", "正式执行" if args.confirm else "演练（不会真重启）")
    log.info("=" * 56)

    base = (cfg.get("router") or {}).get("base_url")
    if not base:
        log.error("配置缺少 router.base_url")
        return 1

    headless = not args.show
    exit_code = 0

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(ignore_https_errors=True,
                                      viewport={"width": 1600, "height": 1000})
            page = ctx.new_page()
            page.set_default_timeout(
                int((cfg.get("browser") or {}).get("timeout_ms", 25000)))

            do_login(page, cfg, log)
            goto_ap_page(page, cfg, log)

            aps = read_ap_list(page, log)
            if not aps:
                log.error("没有读到任何 AP")
                browser.close()
                return 2

            # 选出目标
            targets = aps
            if args.ap:
                targets = [a for a in aps if args.ap.lower() in a["name"].lower()]
                if not targets:
                    log.error("没有找到名称包含「%s」的 AP", args.ap)
                    log.info("当前可用 AP: %s",
                             ", ".join(a["name"] for a in aps))
                    browser.close()
                    return 3
            if args.mac:
                norm = args.mac.lower().replace(":", "").replace("-", "")
                targets = [a for a in aps
                           if norm in a["mac"].lower().replace(":", "").replace("-", "")]
                if not targets:
                    log.error("没有找到 MAC 匹配「%s」的 AP", args.mac)
                    browser.close()
                    return 3

            log.info("-" * 56)
            log.info("本次将处理 %d 台 AP", len(targets))

            success = 0
            for ap in targets:
                try:
                    if restart_ap(page, ap, log, confirm=args.confirm):
                        success += 1
                    page.wait_for_timeout(2000)
                except Exception as ex:
                    log.error("处理 %s 时出错: %s", ap["name"], ex, exc_info=True)

            log.info("=" * 56)
            if args.confirm:
                log.info("执行完成，成功重启 %d / %d 台", success, len(targets))
                if success < len(targets):
                    exit_code = 6
            else:
                log.info("演练完成。确认无误后加 --confirm 真正执行。")
            log.info("=" * 56)

            browser.close()

    except PWTimeout as ex:
        log.error("操作超时: %s", ex)
        exit_code = 4
    except Exception as ex:
        log.error("执行出错: %s", ex, exc_info=True)
        exit_code = 5

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
