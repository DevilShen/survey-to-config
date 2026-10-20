"""登录与权限：JWT（HS256）签发 / 校验，角色校验依赖。

与旧版的区别：
- 旧版「只填邮箱就发一个内存随机 token」——谁都能冒充 admin，且重启就掉线；
- 现在必须账号 + 密码，密码用 PBKDF2 校验，token 是标准 JWT（带 exp，重启不失效）。

角色：admin > pm > finance > viewer
"""
from __future__ import annotations

from typing import Iterable

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from .. import security
from ..db import DEMO_LOGIN, DEMO_PASSWORD, DEMO_NAME, connect, ensure_accounts

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 登录失败统一用这句话：区分「账号不存在」与「密码错」等于送枚举接口
BAD_LOGIN = "账号或密码不正确"


class LoginIn(BaseModel):
    username: str | None = None
    email: str | None = None          # 兼容旧调用方
    password: str | None = None


def _public(row) -> dict:
    return {"id": row["id"], "name": row["name"], "email": row["email"],
            "role": row["role"], "login_name": row["login_name"]}


@router.post("/login")
def login(body: LoginIn):
    account = (body.username or body.email or "").strip()
    if not account:
        raise HTTPException(status_code=422, detail="请填写账号")
    # 没带密码一律拒绝：这条分支只为把错误提示说清楚，不提供免密登录后门
    if not body.password:
        raise HTTPException(status_code=422, detail="请填写密码")

    conn = connect()
    try:
        ensure_accounts(conn)        # 老库首次登录时兜底补齐密码
        row = conn.execute(
            "SELECT * FROM users WHERE login_name=? OR email=?", (account, account)).fetchone()
    finally:
        conn.close()

    if row is None or not security.verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail=BAD_LOGIN)

    token = security.sign({
        "sub": row["id"], "name": row["name"], "role": row["role"],
        "login_name": row["login_name"],
    })
    return {
        "token": token, "token_type": "Bearer", "expires_in": security.TOKEN_TTL,
        "user": _public(row),
    }


@router.get("/me")
def me(authorization: str | None = Header(None)):
    user = resolve(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user


@router.post("/logout")
def logout():
    """JWT 无状态，服务端没有会话可销毁；前端清掉 token 即可。

    仍然保留这个端点，是为了让前端的「退出」有一个明确的服务端往返，
    将来换成黑名单 / refresh token 时前端不用改。
    """
    return {"ok": True}


@router.get("/demo")
def demo_account():
    """登录页的演示账号提示（生产环境应删掉）。"""
    return {"username": DEMO_LOGIN, "password": DEMO_PASSWORD, "name": DEMO_NAME,
            "note": "演示账号，admin 角色，可直接体验全部写操作"}


@router.get("/users")
def users():
    """演示用账号清单，不含密码与哈希。"""
    conn = connect()
    try:
        return [
            {"name": r["name"], "login_name": r["login_name"], "email": r["email"],
             "role": r["role"]}
            for r in conn.execute("SELECT * FROM users ORDER BY id")
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------- 校验

def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()          # 容错：老前端可能只传裸 token


def resolve(authorization: str | None) -> dict | None:
    """解析 Authorization 头，返回当前用户（token 无效返回 None）。

    每次都回查库：角色被改 / 用户被停用要立刻生效，不能只信 token 里的旧角色。
    """
    token = _bearer(authorization)
    if not token:
        return None
    try:
        payload = security.verify(token)
    except security.JWTError:
        return None

    conn = connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (payload["sub"],)).fetchone()
    finally:
        conn.close()
    return _public(row) if row is not None else None


def require_roles(*roles: Iterable[str]):
    """依赖注入：拿到当前用户并校验角色。"""
    def dep(authorization: str | None = Header(None)) -> dict:
        user = resolve(authorization)
        if user is None:
            raise HTTPException(status_code=401, detail="需要登录后才能执行写操作")
        if user["role"] not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"角色 {user['role']} 无权执行该操作，需要 {'/'.join(roles)}")
        return user
    return dep
