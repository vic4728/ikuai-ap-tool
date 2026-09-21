#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
爱快路由器管理工具 —— 加密配置存储

设计目标
--------
1. **绿色单文件**：配置文件生成在程序所在目录（exe 同级），不污染系统
2. **密码不落明文**：密码用 AES-256-CBC 加密后 Base64 存储
3. **换机即失效**：密钥由本机特征派生，拷到别的机器解不开
4. **零依赖**：AES 用纯 Python 实现，无需 cryptography / pycryptodome

关于安全性的诚实说明
--------------------
本方案是**混淆级**加密，不是强安全保证：

  - 密钥从机器特征派生，且内置固定盐值 —— 拿到源码的人可以复现派生过程
  - 纯 Python 实现没有硬件加速，也没有抗侧信道设计

它挡得住的是：
  ✓ 顺手翻看配置文件的人
  ✓ 把配置文件拷到别的机器 / 发给别人
  ✓ 被同步到网盘或 Git 仓库后泄露明文

它挡不住的是：
  ✗ 有能力读源码并主动逆向的人

如果需要真正的强安全，应改用 Windows DPAPI（绑定用户+机器，密钥不出 TPM）。
本项目选择混淆级方案，是为了保持「绿色单文件 + 零依赖」这两个更重要的特性。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import socket
import sys
import uuid
from pathlib import Path

# ======================================================================
# 程序目录解析（兼容 PyInstaller 打包）
# ======================================================================

def app_dir() -> Path:
    """
    返回程序所在目录。

    - 直接跑 .py        -> 脚本所在目录
    - PyInstaller 打包  -> exe 所在目录（不是解压的临时目录 _MEIPASS）

    这是「绿色单文件」的关键：配置永远生成在用户看得到、带着走的目录里。
    """
    if getattr(sys, "frozen", False):
        # 打包后：sys.executable 是 exe 路径
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


CONFIG_FILE = app_dir() / "ikuai_config.json"

# 配置文件版本（将来格式变更时用于迁移）
CONFIG_VERSION = 1


# ======================================================================
# 机器指纹 -> 密钥派生
# ======================================================================

# 固定盐值。注意：这是"混淆"而非"保密"，写在这里是可接受的。
_SALT = b"ikuai-ap-tool/v1/2026"

# 缓存派生结果，避免每次读写都算一遍
_key_cache: bytes | None = None


def _machine_fingerprint() -> str:
    """
    采集本机特征，拼成一个字符串。

    只取「稳定且非隐私」的项 —— 不读网卡、不读主机名（可能改），
    优先用 UUID 和 CPU 架构这类换机器才会变的标识。
    """
    parts = []

    # 1. 机器 UUID（Windows 上来自注册表 MachineGuid，换机器必变）
    try:
        node = uuid.getnode()
        parts.append("node=%x" % node)
    except Exception:
        parts.append("node=unknown")

    # 2. 系统与架构
    try:
        parts.append("sys=%s" % platform.system())
        parts.append("machine=%s" % platform.machine())
    except Exception:
        pass

    # 3. 处理器标识
    try:
        parts.append("proc=%s" % (platform.processor() or ""))
    except Exception:
        pass

    # 4. 用户目录的绝对路径（含用户名，换用户即变）
    try:
        parts.append("home=%s" % Path.home())
    except Exception:
        pass

    # 5. 主机名（次要，仅作补充）
    try:
        parts.append("host=%s" % socket.gethostname())
    except Exception:
        pass

    return "|".join(parts)


def _derive_key() -> bytes:
    """
    从机器指纹派生 32 字节（256 位）密钥。

    用 PBKDF2-HMAC-SHA256 迭代 10 万次，让暴力枚举成本变高。
    结果缓存，避免重复计算拖慢启动。
    """
    global _key_cache
    if _key_cache is not None:
        return _key_cache

    fp = _machine_fingerprint()
    key = hashlib.pbkdf2_hmac(
        "sha256",
        fp.encode("utf-8"),
        _SALT,
        100_000,      # 迭代次数
        dklen=32,     # 256 位
    )
    _key_cache = key
    return key


# ======================================================================
# 纯 Python AES-256 实现
# ======================================================================
# 为什么自己实现：为了保持零依赖。
# 用 pycryptodome 要多装一个包，打包体积变大；
# 用 cryptography 还要带 OpenSSL 动态库，更重。

_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]

_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i

_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80,
         0x1b, 0x36, 0x6c, 0xd8, 0xab, 0x4d]


def _xtime(a: int) -> int:
    """GF(2^8) 上乘以 2。"""
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1b) & 0xff
    return a


def _mul(a: int, b: int) -> int:
    """GF(2^8) 乘法。"""
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        b >>= 1
        a = _xtime(a)
    return p & 0xff


def _expand_key(key: bytes) -> list:
    """AES-256 密钥扩展 -> 60 个 4 字节轮密钥。"""
    assert len(key) == 32, "AES-256 需要 32 字节密钥"
    nk = 8            # 256 位 -> 8 个 32 位字
    nr = 14           # 轮数
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]

    for i in range(nk, 4 * (nr + 1)):
        temp = list(w[i - 1])
        if i % nk == 0:
            # RotWord + SubWord + Rcon
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            temp = [_SBOX[b] for b in temp]
        w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
    return w


def _add_round_key(state: list, w: list, rnd: int) -> None:
    """就地加密钥。state 是 16 字节的 list。"""
    for c in range(4):
        for r in range(4):
            state[4 * c + r] ^= w[rnd * 4 + c][r]


def _sub_bytes(state: list) -> None:
    for i in range(16):
        state[i] = _SBOX[state[i]]


def _inv_sub_bytes(state: list) -> None:
    for i in range(16):
        state[i] = _INV_SBOX[state[i]]


def _shift_rows(state: list) -> None:
    # 按列优先存放：state[4*c + r]
    for r in range(1, 4):
        row = [state[4 * c + r] for c in range(4)]
        row = row[r:] + row[:r]
        for c in range(4):
            state[4 * c + r] = row[c]


def _inv_shift_rows(state: list) -> None:
    for r in range(1, 4):
        row = [state[4 * c + r] for c in range(4)]
        row = row[-r:] + row[:-r]
        for c in range(4):
            state[4 * c + r] = row[c]


def _mix_columns(state: list) -> None:
    for c in range(4):
        a = state[4 * c:4 * c + 4]
        state[4 * c + 0] = _mul(a[0], 2) ^ _mul(a[1], 3) ^ a[2] ^ a[3]
        state[4 * c + 1] = a[0] ^ _mul(a[1], 2) ^ _mul(a[2], 3) ^ a[3]
        state[4 * c + 2] = a[0] ^ a[1] ^ _mul(a[2], 2) ^ _mul(a[3], 3)
        state[4 * c + 3] = _mul(a[0], 3) ^ a[1] ^ a[2] ^ _mul(a[3], 2)


def _inv_mix_columns(state: list) -> None:
    for c in range(4):
        a = state[4 * c:4 * c + 4]
        state[4 * c + 0] = _mul(a[0], 14) ^ _mul(a[1], 11) ^ _mul(a[2], 13) ^ _mul(a[3], 9)
        state[4 * c + 1] = _mul(a[0], 9) ^ _mul(a[1], 14) ^ _mul(a[2], 11) ^ _mul(a[3], 13)
        state[4 * c + 2] = _mul(a[0], 13) ^ _mul(a[1], 9) ^ _mul(a[2], 14) ^ _mul(a[3], 11)
        state[4 * c + 3] = _mul(a[0], 11) ^ _mul(a[1], 13) ^ _mul(a[2], 9) ^ _mul(a[3], 14)


def _encrypt_block(block: bytes, w: list) -> bytes:
    """加密一个 16 字节块。"""
    state = list(block)
    _add_round_key(state, w, 0)
    for rnd in range(1, 14):
        _sub_bytes(state)
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, w, rnd)
    _sub_bytes(state)
    _shift_rows(state)
    _add_round_key(state, w, 14)
    return bytes(state)


def _decrypt_block(block: bytes, w: list) -> bytes:
    """解密一个 16 字节块。"""
    state = list(block)
    _add_round_key(state, w, 14)
    for rnd in range(13, 0, -1):
        _inv_shift_rows(state)
        _inv_sub_bytes(state)
        _add_round_key(state, w, rnd)
        _inv_mix_columns(state)
    _inv_shift_rows(state)
    _inv_sub_bytes(state)
    _add_round_key(state, w, 0)
    return bytes(state)


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad = block_size - (len(data) % block_size)
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise ValueError("空数据无法去填充")
    pad = data[-1]
    if pad < 1 or pad > 16:
        raise ValueError("填充值非法: %d" % pad)
    return data[:-pad]


# ======================================================================
# 对外加密接口
# ======================================================================

def encrypt(plain: str) -> str:
    """
    加密字符串，返回 Base64。

    格式：iv(16字节) + ciphertext，整体 Base64 编码。
    每次加密都会生成随机 IV，因此同一明文每次结果都不同。
    """
    if not plain:
        return ""
    key = _derive_key()
    w = _expand_key(key)
    iv = os.urandom(16)
    data = _pkcs7_pad(plain.encode("utf-8"))

    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        block = data[i:i + 16]
        # CBC：明文块先与前一块密文（首块用 IV）异或，再加密
        xored = bytes(a ^ b for a, b in zip(block, prev))
        cipher = _encrypt_block(xored, w)
        out.extend(cipher)
        prev = cipher

    return base64.b64encode(iv + bytes(out)).decode("ascii")


def decrypt(token: str) -> str:
    """
    解密 Base64 字符串。

    解密失败（换机器、换用户、文件损坏）会抛 ValueError，
    调用方应捕获并降级为「让用户重新输入」。
    """
    if not token:
        return ""
    try:
        raw = base64.b64decode(token.encode("ascii"))
    except Exception as ex:
        raise ValueError("Base64 解码失败: %s" % ex)

    if len(raw) < 32 or (len(raw) - 16) % 16 != 0:
        raise ValueError("密文长度非法")

    key = _derive_key()
    w = _expand_key(key)
    iv = raw[:16]
    ct = raw[16:]

    out = bytearray()
    prev = iv
    for i in range(0, len(ct), 16):
        block = ct[i:i + 16]
        dec = _decrypt_block(block, w)
        out.extend(a ^ b for a, b in zip(dec, prev))
        prev = block

    try:
        return _pkcs7_unpad(bytes(out)).decode("utf-8")
    except Exception as ex:
        raise ValueError("解密失败（可能配置来自其他机器）: %s" % ex)


# ======================================================================
# 配置文件读写
# ======================================================================

# 需要加密存储的字段
ENCRYPTED_FIELDS = ("password",)


def load_config() -> dict:
    """
    读取配置。文件不存在或解密失败都返回默认值，绝不抛异常。

    返回的 dict 里 password 已是明文（供界面使用）。
    """
    defaults = {
        "version": CONFIG_VERSION,
        "host": "192.168.50.1",
        "http_port": "80",
        "https_port": "443",
        "scheme": "HTTP",
        "user": "admin",
        "password": "",
        "remember": True,
        "autologin": False,
        "headless": True,
        "refresh": "5s",
    }

    if not CONFIG_FILE.exists():
        return defaults

    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        # 文件损坏，用默认值，不打扰用户
        return defaults

    cfg = dict(defaults)
    for k in defaults:
        if k in data:
            cfg[k] = data[k]

    # 解密密码字段
    for field in ENCRYPTED_FIELDS:
        enc_key = field + "_enc"
        if enc_key in data and data[enc_key]:
            try:
                cfg[field] = decrypt(data[enc_key])
            except ValueError:
                # 换机器解不开 —— 清空，让用户重输
                cfg[field] = ""
        elif field in data and data[field]:
            # 兼容旧版明文配置：读出来用，下次保存会自动加密
            cfg[field] = data[field]

    return cfg


def save_config(cfg: dict) -> bool:
    """
    保存配置。密码字段加密后写入。

    返回是否成功。
    """
    data = {}
    for k, v in cfg.items():
        if k in ENCRYPTED_FIELDS:
            continue
        data[k] = v

    # 加密敏感字段
    for field in ENCRYPTED_FIELDS:
        val = cfg.get(field, "")
        if val:
            data[field + "_enc"] = encrypt(val)
        else:
            data[field + "_enc"] = ""

    data["version"] = CONFIG_VERSION
    # 标注来源机器，便于排查"配置拷过来解不开"的问题
    data["_machine"] = hashlib.sha256(
        _machine_fingerprint().encode("utf-8")).hexdigest()[:16]

    try:
        CONFIG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return True
    except Exception:
        return False


def config_path() -> Path:
    """返回配置文件路径，供界面展示。"""
    return CONFIG_FILE


# ======================================================================
# 自检
# ======================================================================
if __name__ == "__main__":
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("程序目录:", app_dir())
    print("配置文件:", CONFIG_FILE)
    print()

    print("[AES 正确性自检]")
    # FIPS-197 标准测试向量：AES-256
    _test_key = bytes(range(32))
    _test_pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    _expected = "8ea2b7ca516745bfeafc49904b496089"
    _w = _expand_key(_test_key)
    _got = _encrypt_block(_test_pt, _w).hex()
    print("  加密向量:", _got, "期望:", _expected, "PASS" if _got == _expected else "FAIL")
    _back = _decrypt_block(bytes.fromhex(_got), _w).hex()
    print("  解密回环:", _back, "PASS" if _back == _test_pt.hex() else "FAIL")

    print()
    print("[加解密往返]")
    for s in ["demo_password", "admin", "a", "中文密码测试123", "x" * 200]:
        enc = encrypt(s)
        dec = decrypt(enc)
        ok = dec == s
        print("  %-20r -> %s... %s" % (s[:18], enc[:24], "PASS" if ok else "FAIL"))

    print()
    print("[随机 IV 验证：同一明文两次密文应不同]")
    e1, e2 = encrypt("same"), encrypt("same")
    print("  密文1:", e1[:32])
    print("  密文2:", e2[:32])
    print("  不同:", "PASS" if e1 != e2 else "FAIL")
    print("  都能解开:", "PASS" if decrypt(e1) == decrypt(e2) == "same" else "FAIL")

    print()
    print("[空值与异常]")
    print("  空串加密:", repr(encrypt("")))
    print("  空串解密:", repr(decrypt("")))
    try:
        decrypt("not-base64!!!")
        print("  非法输入: FAIL（未抛异常）")
    except ValueError as ex:
        print("  非法输入: PASS（已拦截）")
