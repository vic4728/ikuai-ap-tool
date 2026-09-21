#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
爱快(iKuai)路由器 Web 自动化脚本

功能：
  1. 自动打开路由器管理页面
  2. 自动填入账号密码并登录
  3. 登录后按配置执行动作序列（跳转页面 / 按名称点击 / 等待 / 截图）

适用场景：后台静默定时执行
用法：
  python ikuai_auto.py                  # 使用默认 config.yaml
  python ikuai_auto.py -c my.yaml       # 指定配置文件
  python ikuai_auto.py --show           # 临时强制有头模式（调试用）
  python ikuai_auto.py --dry-run        # 只登录不执行后续动作
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 第三方依赖缺失时给出明确指引，而不是抛一堆 traceback
try:
    import yaml
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError as exc:  # pragma: no cover
    print(f"[错误] 缺少依赖: {exc.name}")
    print("请先执行:  pip install playwright pyyaml")
    print("并安装浏览器内核:  playwright install chromium")
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

# Windows 控制台默认 GBK，输出非 ASCII 字符可能抛 UnicodeEncodeError。
# 强制切成 UTF-8 并容错替换，保证脚本稳定运行。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ------------------------------------------------------------------
# 日志
# ------------------------------------------------------------------
def setup_logging(cfg: dict) -> logging.Logger:
    """按配置初始化日志：同时输出到控制台和文件。"""
    log_cfg = cfg.get("logging", {}) or {}
    level_name = str(log_cfg.get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("ikuai")
    logger.setLevel(level)
    logger.handlers.clear()

    # 统一格式：时间 + 级别 + 消息
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # 文件
    log_file = BASE_DIR / str(log_cfg.get("file", "output/ikuai_auto.log"))
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ------------------------------------------------------------------
# 登录
# ------------------------------------------------------------------
def do_login(page, cfg: dict, log: logging.Logger) -> None:
    """
    在已打开的页面上完成登录。

    策略说明：
      爱快后台的密码在部分固件版本里会由前端 JS 处理后提交，
      自己逆向加密算法既麻烦又容易随版本失效。
      这里直接让浏览器加载真实登录页、填表、点按钮，
      由页面自身的 JS 完成加密与提交，兼容性最好。
    """
    router = cfg["router"]
    base_url = router["base_url"].rstrip("/")
    login_path = router.get("login_path", "/")
    login_url = f"{base_url}/{login_path.lstrip('/')}"
    timeout = int(cfg.get("browser", {}).get("timeout_ms", 20000))
    settle = int(cfg.get("browser", {}).get("settle_ms", 2000))

    log.info("打开登录页: %s", login_url)
    page.goto(login_url, timeout=timeout, wait_until="domcontentloaded")

    # --- 定位用户名输入框 -------------------------------------------
    # 爱快的输入框 name 通常是 username / passwd / password
    # 这里按多个候选依次尝试，兼容不同固件
    username_box = None
    for sel in (
        'input[name="username"]',
        'input[name="user"]',
        '#username',
        'input[type="text"]',
    ):
        loc = page.locator(sel).first
        if loc.count() > 0:
            username_box = loc
            log.debug("用户名输入框定位成功: %s", sel)
            break
    if username_box is None:
        raise RuntimeError("未找到用户名输入框，请检查 login_path 是否正确")

    # --- 定位密码输入框 ---------------------------------------------
    password_box = None
    for sel in (
        'input[name="passwd"]',
        'input[name="password"]',
        'input[type="password"]',
    ):
        loc = page.locator(sel).first
        if loc.count() > 0:
            password_box = loc
            log.debug("密码输入框定位成功: %s", sel)
            break
    if password_box is None:
        raise RuntimeError("未找到密码输入框")

    log.info("填入账号: %s", router["username"])
    username_box.fill(router["username"])
    password_box.fill(router["password"])

    # --- 点登录按钮 -------------------------------------------------
    # 爱快的登录按钮文案常见为「登录」「登陆」
    clicked = False
    for name in ("登录", "登陆", "登 录", "Login", "login"):
        btn = page.get_by_role("button", name=name)
        if btn.count() > 0:
            log.info("点击登录按钮: %s", name)
            btn.first.click(timeout=timeout)
            clicked = True
            break

    if not clicked:
        # 兜底：按钮可能不是 <button> 而是 <a> 或 <input type=submit>
        for sel in (
            'input[type="submit"]',
            'button:has-text("登录")',
            'a:has-text("登录")',
            '.login-btn',
        ):
            loc = page.locator(sel).first
            if loc.count() > 0:
                log.info("回退定位登录按钮: %s", sel)
                loc.click(timeout=timeout)
                clicked = True
                break

    if not clicked:
        raise RuntimeError("未找到登录按钮")

    # --- 等待登录完成 -----------------------------------------------
    page.wait_for_timeout(settle)

    # 简单校验：URL 变化 或 出现登出字样，都视为登录成功
    log.info("登录后地址栏: %s", page.url)
    body_text = page.inner_text("body")[:3000]
    if "密码错误" in body_text or "登录失败" in body_text:
        raise RuntimeError("登录失败：账号或密码错误")
    log.info("登录流程完成")


# ------------------------------------------------------------------
# 动作执行
# ------------------------------------------------------------------
def run_actions(page, cfg: dict, log: logging.Logger) -> None:
    """按配置顺序执行动作序列。"""
    router = cfg["router"]
    base_url = router["base_url"].rstrip("/")
    browser_cfg = cfg.get("browser", {}) or {}
    timeout = int(browser_cfg.get("timeout_ms", 20000))

    actions = cfg.get("actions") or []
    if not actions:
        log.warning("配置中没有任何 actions，跳过后续操作")
        return

    for idx, act in enumerate(actions, start=1):
        atype = (act.get("type") or "").lower()
        prefix = f"[动作 {idx}/{len(actions)}]"

        if atype == "goto":
            url = act["url"]
            if not url.startswith("http"):
                url = f"{base_url}/{url.lstrip('/')}"
            log.info("%s 跳转页面: %s", prefix, url)
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            page.wait_for_timeout(int(browser_cfg.get("settle_ms", 2000)))

        elif atype == "click":
            text = act["text"]
            log.info("%s 点击「%s」", prefix, text)
            # 优先按按钮角色匹配，其次按可见文本匹配
            loc = page.get_by_role("button", name=text)
            if loc.count() == 0:
                loc = page.get_by_text(text, exact=False)
            if loc.count() == 0:
                log.warning("%s 未找到可点击元素「%s」，已跳过", prefix, text)
                continue
            # 命中多个时取第一个可见的，避免 strict mode 报错
            target = loc.first
            try:
                target.scroll_into_view_if_needed(timeout=timeout)
            except Exception:
                pass
            target.click(timeout=timeout)
            page.wait_for_timeout(800)

        elif atype == "wait":
            ms = int(act.get("ms", 1000))
            log.info("%s 等待 %s 毫秒", prefix, ms)
            page.wait_for_timeout(ms)

        elif atype == "screenshot":
            name = act.get("name", f"shot_{idx}")
            path = OUTPUT_DIR / f"{name}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=True)
            log.info("%s 截图已保存: %s", prefix, path)

        elif atype == "assert_text":
            # 校验页面上是否出现预期文字，用于确认操作真的成功了
            # 例：点完重启后应出现「设备正在重启」
            expect = act["expect"]
            if page.get_by_text(expect, exact=False).count() > 0:
                log.info("%s 校验通过，页面出现「%s」", prefix, expect)
            else:
                log.warning("%s 校验未通过，页面未出现「%s」", prefix, expect)

        else:
            log.warning("%s 未知动作类型「%s」，已跳过", prefix, atype)


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="爱快路由器 Web 自动化")
    parser.add_argument(
        "-c", "--config", default=str(BASE_DIR / "config.yaml"),
        help="配置文件路径，默认 config.yaml",
    )
    parser.add_argument(
        "--show", action="store_true",
        help="强制显示浏览器窗口（覆盖配置里的 headless）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只登录，不执行 actions",
    )
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"[错误] 配置文件不存在: {cfg_path}")
        return 1

    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    log = setup_logging(cfg)
    log.info("=" * 56)
    log.info("爱快路由器自动化 启动")

    router = cfg.get("router", {}) or {}
    if not router.get("base_url"):
        log.error("配置缺少 router.base_url")
        return 1

    headless = bool(cfg.get("browser", {}).get("headless", True))
    if args.show:
        headless = False
    log.info("运行模式: %s", "静默无头" if headless else "显示窗口")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            # 路由器后台多为自签名证书，忽略 HTTPS 报错
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1440, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(int(cfg.get("browser", {}).get("timeout_ms", 20000)))

            do_login(page, cfg, log)

            if args.dry_run:
                log.info("--dry-run 已启用，跳过 actions")
            else:
                run_actions(page, cfg, log)

            browser.close()

        log.info("执行完成")
        log.info("=" * 56)
        return 0

    except PWTimeout as exc:
        log.error("操作超时: %s", exc)
        return 2
    except Exception as exc:
        log.error("执行出错: %s", exc, exc_info=True)
        return 3


if __name__ == "__main__":
    sys.exit(main())
