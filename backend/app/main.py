"""软件开发全流程决策系统 · 后端入口。

启动：  python run.py         （或 uvicorn app.main:app --reload）
文档：  http://127.0.0.1:8000/docs
前端：  http://127.0.0.1:8000/      与 API 同源，避免跨域
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db import init_db
from . import auth_guard
from .routers import api, auth, dialogue, pricing

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db(reset=os.environ.get("SDLC_RESET") == "1")
    yield


app = FastAPI(
    title="软件开发全流程决策系统 API",
    version="1.0.0",
    description="九个流程模块 + 四个决策专题。指标全部由原始记录现算，配置由规则引擎确定性推导。",
    lifespan=lifespan,
)

# 全站登录网关：/api/* 需要 Bearer JWT（白名单见 auth_guard）
# 必须在 CORS 之前 add —— Starlette 后加的先执行，这样 CORS 在最外层，
# 401 响应也能带上跨域头（双击 index.html 打开时是跨源的）。
auth_guard.install(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 演示环境；生产应收敛到具体域名
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(api.router)
app.include_router(pricing.router)
app.include_router(dialogue.router)


@app.get("/api")
def api_root():
    return {
        "name": "软件开发全流程决策系统 API",
        "docs": "/docs",
        "endpoints": [
            "/api/bootstrap", "/api/overview", "/api/projects", "/api/modules/{key}",
            "/api/topics/variance", "/api/topics/lineage", "/api/topics/version",
            "/api/config/derive", "/api/metrics", "/api/audit",
            "/api/pricing/overview", "/api/pricing/requests/{id}/run",
            "/api/pricing/runs/{id}", "/api/pricing/runs/{id}/review",
            "/api/pricing/rules", "/api/pricing/rules/simulate",
        ],
    }


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
