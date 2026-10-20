@echo off
chcp 65001 >nul
cd /d %~dp0

if not exist .venv (
  echo [setup] 首次运行，创建虚拟环境并安装依赖...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -q --upgrade pip
  .venv\Scripts\python.exe -m pip install -q -r requirements.txt
)

echo.
echo   软件开发全流程决策系统
echo   页面  http://127.0.0.1:8010/
echo   文档  http://127.0.0.1:8010/docs
echo.
.venv\Scripts\python.exe run.py
pause
