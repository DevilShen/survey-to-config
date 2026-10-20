"""全站鉴权网关：除白名单外的 /api/* 都要求有效 JWT。

为什么用中间件而不是逐个端点加 Depends：
  这里有 60+ 个端点，漏加一个就是数据裸奔。默认拒绝（deny by default）比默认放行安全。

白名单：
  /api/auth/login、/api/auth/demo、/api/auth/logout  —— 登录本身不能要登录
  /api                                              —— 服务自述，无业务数据
  非 /api 路径（静态页、/docs、/openapi.json）        —— 由 StaticFiles 处理

开关：
  SDLC_AUTH=off 关闭校验（后端单元测试用，避免每个用例都造 token）。
  默认 on —— 部署即受保护，不会因为忘记配环境变量而敞开。

这里只验签与过期，不查库（中间件是 async，查 SQLite 会阻塞事件循环）；
角色是否仍然有效由 require_roles 在写操作处查库兜底。
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import security

PUBLIC_PATHS = {"/api", "/api/auth/login", "/api/auth/demo", "/api/auth/logout"}


def enabled() -> bool:
    return os.environ.get("SDLC_AUTH", "on").lower() not in ("off", "0", "false", "no")


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        if not enabled():
            return await call_next(request)

        path = request.url.path.rstrip("/") or "/"
        if not path.startswith("/api") or path in PUBLIC_PATHS:
            return await call_next(request)

        raw = request.headers.get("authorization") or ""
        token = raw.split(None, 1)[1].strip() if raw.lower().startswith("bearer ") else raw.strip()
        try:
            security.verify(token)
        except security.JWTError as e:
            return JSONResponse({"detail": str(e), "code": "unauthorized"}, status_code=401)

        return await call_next(request)
