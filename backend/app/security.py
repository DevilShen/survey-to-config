"""登录凭证：HS256 JWT + PBKDF2 密码哈希。

两条硬约束：
1. **不引第三方库**。JWT 只有 header.payload.signature 三段，HS256 用标准库 hmac 就能签，
   引 PyJWT 只是多一个部署依赖；产出的是标准 JWT，jwt.io 可验签。
2. **不存明文密码**。PBKDF2-HMAC-SHA256 + 随机盐 + 迭代次数，校验用 compare_digest 防时序侧信道。

密钥来源：环境变量 SDLC_JWT_SECRET 优先；否则在 data/ 下生成一次并落盘，
这样后端重启后已发出的 token 不会集体失效（演示现场最忌讳"重启掉线"）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

# ---------------------------------------------------------------- 配置

TOKEN_TTL = int(os.environ.get("SDLC_JWT_TTL", 8 * 3600))   # 默认 8 小时
PBKDF2_ROUNDS = 120_000
_ALG = "HS256"


class JWTError(Exception):
    """token 不合法（格式错 / 签名不符 / 已过期）。"""


def _secret() -> bytes:
    env = os.environ.get("SDLC_JWT_SECRET")
    if env:
        return env.encode("utf-8")
    from .db import DATA_DIR            # 延迟导入，避免循环依赖

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = Path(DATA_DIR) / ".jwt_secret"
    if f.exists():
        raw = f.read_text(encoding="utf-8").strip()
        if raw:
            return raw.encode("utf-8")
    raw = secrets.token_hex(32)
    f.write_text(raw, encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass                            # Windows 上 chmod 语义有限，忽略
    return raw.encode("utf-8")


# ---------------------------------------------------------------- base64url

def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ---------------------------------------------------------------- JWT

def sign(payload: dict) -> str:
    """签发 token。payload 里必须有 sub；自动补 iat / exp。"""
    now = int(time.time())
    body = dict(payload)
    body.setdefault("iat", now)
    body.setdefault("exp", now + TOKEN_TTL)
    head = _b64e(json.dumps({"alg": _ALG, "typ": "JWT"},
                            separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    part = _b64e(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = hmac.new(_secret(), f"{head}.{part}".encode("ascii"), hashlib.sha256).digest()
    return f"{head}.{part}.{_b64e(sig)}"


def verify(token: str) -> dict:
    """校验并返回 payload；任何问题都抛 JWTError（调用方统一转 401）。"""
    if not token or token.count(".") != 2:
        raise JWTError("token 格式不对")
    head, part, sig = token.split(".")
    expect = hmac.new(_secret(), f"{head}.{part}".encode("ascii"), hashlib.sha256).digest()
    try:
        got = _b64d(sig)
    except Exception:                    # noqa: BLE001
        raise JWTError("签名段无法解码") from None
    if not hmac.compare_digest(expect, got):
        raise JWTError("签名校验失败")

    try:
        head_obj = json.loads(_b64d(head))
        body = json.loads(_b64d(part))
    except Exception:                    # noqa: BLE001
        raise JWTError("token 内容不是合法 JSON") from None
    if head_obj.get("alg") != _ALG:
        raise JWTError("不支持的签名算法")

    exp = body.get("exp")
    if not isinstance(exp, (int, float)) or int(exp) < int(time.time()):
        raise JWTError("登录已过期，请重新登录")
    if not body.get("sub"):
        raise JWTError("token 缺少主体")
    return body


def remaining(token: str) -> int:
    """剩余有效秒数（前端用来决定是否还续期）。"""
    try:
        return max(0, int(verify(token)["exp"]) - int(time.time()))
    except JWTError:
        return 0


# ---------------------------------------------------------------- 密码

def hash_password(pw: str, rounds: int = PBKDF2_ROUNDS) -> str:
    """返回 pbkdf2_sha256$轮数$盐$哈希。"""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${_b64e(salt)}${_b64e(dk)}"


def verify_password(pw: str, stored: str | None) -> bool:
    if not stored or not pw:
        return False
    try:
        algo, rounds, salt, want = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), _b64d(salt), int(rounds))
    except Exception:                    # noqa: BLE001
        return False                     # 库里存了坏数据 → 一律当校验失败
    return hmac.compare_digest(_b64e(dk), want)
