"""本地启动脚本。首次运行会自动建库并灌入演示数据。"""
from __future__ import annotations

import os
import sys

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8010"))
    reset = os.environ.get("SDLC_RESET") == "1"
    if reset:
        print("[reset] 将重建数据库…")
    print(f"[start] http://127.0.0.1:{port}/  文档 /docs")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=False, log_level="info")
    sys.exit(0)
