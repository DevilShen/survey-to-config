#!/usr/bin/env bash
# macOS / Linux / WSL 启动脚本
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "[setup] 创建虚拟环境并安装依赖…"
  python3 -m venv .venv
  ./.venv/bin/python -m pip install -q --upgrade pip
  ./.venv/bin/python -m pip install -q -r requirements.txt
fi

echo
echo "  软件开发全流程决策系统"
echo "  页面  http://127.0.0.1:8010/"
echo "  文档  http://127.0.0.1:8010/docs"
echo
exec ./.venv/bin/python run.py
