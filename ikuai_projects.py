# -*- coding: utf-8 -*-
"""多项目管理模块。

存储结构（单个 JSON，路径与主配置同目录）：
  projects.json
  {
    "version": 1,
    "active": "项目id",           # 当前选中
    "projects": [
      {
        "id": "p1726...",          # 唯一 id（时间戳+随机）
        "name": "花舍车间",
        "host": "xh.jcsit.cn",
        "port": 80,
        "scheme": "HTTP",
        "user": "admin",
        "password_enc": "...",     # AES 机器绑定加密（勾记住密码才存）
        "remember": true,
        "autologin": false,
        "headless": true,
      }, ...
    ]
  }

日志：logs/<项目名安全化>.log，每项目一个文件，追加写，
带时间戳，自动轮转（单文件超 5MB 换 .1 备份）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

import ikuai_config


def _base_dir() -> Path:
    """数据目录：环境变量 IKUAI_DATA_DIR 优先（测试隔离/便携部署用），
    其次 exe/脚本所在目录（与 ikuai_config.app_dir 同策略）。"""
    env = os.environ.get("IKUAI_DATA_DIR")
    if env:
        return Path(env)
    return ikuai_config.app_dir()


PROJECTS_FILE = _base_dir() / "projects.json"
LOGS_DIR = _base_dir() / "logs"

_SAFE_NAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(name: str) -> str:
    """项目名 -> 文件名安全串（日志文件用）。"""
    s = _SAFE_NAME.sub("_", (name or "").strip())
    return s[:40] or "未命名"


# ------------------------------------------------------------------
# 项目数据
# ------------------------------------------------------------------
def _default_project(name: str = "") -> dict:
    return {
        "id": "p%s%s" % (int(time.time() * 1000), uuid.uuid4().hex[:6]),
        "name": name or "新项目",
        "host": "",
        "port": 80,
        "scheme": "HTTP",
        "user": "admin",
        "password": "",          # 运行态（明文，仅内存/解密后）
        "remember": False,
        "autologin": False,
        "headless": True,
    }


def load() -> dict:
    """读 projects.json -> {active, projects[]}（含解密密码）。"""
    data = {"version": 1, "active": "", "projects": []}
    if not PROJECTS_FILE.exists():
        return data
    try:
        raw = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return data
    data["active"] = raw.get("active", "")
    for p in raw.get("projects", []):
        proj = _default_project()
        proj.update({k: p[k] for k in proj if k in p and k != "password"})
        enc = p.get("password_enc", "")
        proj["password"] = ikuai_config.decrypt(enc) if enc else ""
        data["projects"].append(proj)
    return data


def save(data: dict) -> None:
    """写 projects.json（勾记住密码才加密落盘）。"""
    out = {"version": 1, "active": data.get("active", ""),
           "projects": []}
    for p in data.get("projects", []):
        row = {k: v for k, v in p.items() if k != "password"}
        if p.get("remember") and p.get("password"):
            row["password_enc"] = ikuai_config.encrypt(p["password"])
        out["projects"].append(row)
    try:
        PROJECTS_FILE.write_text(
            json.dumps(out, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError:
        pass


# ------------------------------------------------------------------
# 项目日志（每项目一个文件）
# ------------------------------------------------------------------
_loggers: dict[str, logging.Logger] = {}


def get_logger(proj_name: str) -> logging.Logger:
    """取项目的专属 logger（logs/<安全名>.log，5MB 轮转）。"""
    key = safe_name(proj_name)
    if key in _loggers:
        return _loggers[key]
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger("proj." + key)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    if not lg.handlers:                      # 防 addNHandler 重复
        h = RotatingFileHandler(
            LOGS_DIR / ("%s.log" % key), maxBytes=5 * 1024 * 1024,
            backupCount=1, encoding="utf-8")
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        lg.addHandler(h)
    _loggers[key] = lg
    return lg


def close_logger(proj_name: str) -> None:
    key = safe_name(proj_name)
    lg = _loggers.pop(key, None)
    if lg:
        for h in lg.handlers[:]:
            h.close()
            lg.removeHandler(h)
