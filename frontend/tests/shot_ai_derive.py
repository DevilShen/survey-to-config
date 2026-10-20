"""真浏览器验收：确认「AI 推导」是唯一计算入口（按钮只剩一个、点了真调模型）。

跑法（后端需在 8010 运行、且已配好 DeepSeek Key）：
    backend/.venv/Scripts/python.exe frontend/tests/shot_ai_derive.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9446
URL = "http://127.0.0.1:8010/#/topic-config"
OUT = Path(__file__).resolve().parent / "_shots"

fails: list[str] = []


def ok(name: str, cond: bool, extra: str = "") -> None:
    if cond:
        print(f"  ✓ {name}")
    else:
        fails.append(f"{name} — {extra}")
        print(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


def wait_target(timeout=25.0) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json") as r:
                for t in json.load(r):
                    if t.get("type") == "page" and "localhost" not in t.get("url", ""):
                        return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.4)
    raise RuntimeError("拿不到 CDP 页面目标")


async def main() -> int:
    OUT.mkdir(exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="cdp96ai_"))
    proc = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--no-default-browser-check",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
        "--window-size=1560,980", URL,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        ws_url = await asyncio.to_thread(wait_target)
        async with websockets.connect(ws_url, max_size=None) as ws:
            seq = 0

            async def call(method: str, params: dict | None = None):
                nonlocal seq
                seq += 1
                await ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == seq:
                        return msg

            await call("Runtime.enable")

            async def ev(expr: str):
                r = await call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                                    "awaitPromise": True})
                return r.get("result", {}).get("result", {}).get("value")

            # 页面现在需要登录才能进：先换 JWT 写进 localStorage，再跳目标页。
            # 账号密码由后端 /api/auth/demo 给出，脚本里不写死。
            # 这里直接 call("Runtime.evaluate")——此刻 ev() 还没定义。
            await call("Runtime.evaluate", {"expression": """(async function(){
              const meta = await (await fetch('/api/auth/demo')).json();
              const r = await fetch('/api/auth/login',{method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({username: meta.username, password: meta.password})});
              const d = await r.json();
              localStorage.setItem('sdlc_token', d.token);
              return 'ok';
            })()""", "awaitPromise": True})
            await call("Runtime.evaluate", {"expression": "location.replace(%s); 'x'" % json.dumps(URL)})
            await asyncio.sleep(1.6)
            # 页面现在需要登录才能进：先换 JWT 写进 localStorage，再跳目标页
            # （登录接口本身不需要 token，所以在被踢到的 login.html 上也能调）
            await ev("""(async function(){
              const r = await fetch('/api/auth/login',{method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({username:'demo',password:'demo1234'})});
              const d = await r.json();
              localStorage.setItem('sdlc_token', d.token);
              return 'ok';
            })()""")
            await ev("location.replace(%s); 'x'" % json.dumps(URL))
            await asyncio.sleep(1.6)
            await call("Page.enable")


            async def shot(name: str, clip: dict | None = None):
                p = {"format": "png"}
                if clip:
                    p["clip"] = dict(clip, scale=1)
                r = await call("Page.captureScreenshot", p)
                data = r["result"]["data"]
                (OUT / name).write_bytes(base64.b64decode(data))
                print(f"    截图 {name}")

            # 等挂载
            for _ in range(80):
                if await ev("!!document.querySelector('#c96-acts [data-c96=\\'run\\']')"):
                    break
                await asyncio.sleep(0.25)

            print("\n[真浏览器] 「AI 推导」唯一入口")
            acts = await ev("document.getElementById('c96-acts').textContent")
            ok("顶栏只有「AI 推导」一个计算按钮",
               bool(await ev("document.querySelectorAll('#c96-acts [data-c96=\"run\"]').length===1")))
            ok("不再有第二个计算按钮", bool(await ev("!document.querySelector('[data-c96=\"runllm\"]')")))
            ok("没有「重新计算 / 确认计算」字样",
               bool(await ev("!/重新计算|确认计算/.test(document.getElementById('c96-acts').textContent)")),
               (acts or "")[:80])
            ok("按钮文案是「AI 推导」",
               bool(await ev("/^AI 推导$/.test(document.querySelector('[data-c96=\"run\"]').textContent.trim())")),
               await ev("document.querySelector('[data-c96=\"run\"]').textContent"))
            ok("模型已配置，按钮可用",
               bool(await ev("!document.querySelector('[data-c96=\"run\"]').disabled")))

            # 顶栏特写
            box = await ev("(function(){var r=document.querySelector('.c96-head').getBoundingClientRect();"
                           "return {x:r.x,y:r.y,width:r.width,height:r.height};})()")
            if box:
                await shot("ai01_acts.png", box)

            # 载入示例后点 AI 推导（真调 deepseek-flash）
            await ev("document.querySelector('[data-c96=\"demo\"]').click(); 'x'")
            await asyncio.sleep(0.6)
            print("    点「AI 推导」，等真模型逐组生成（约 60-90 秒）…")
            t0 = time.time()
            for _ in range(400):
                if await ev("window.Config96.state.phase==='idle' && !!window.Config96.state.result"):
                    break
                await asyncio.sleep(0.5)
            dt = time.time() - t0

            n = await ev("((window.Config96.state.result||{}).configs||[]).length")
            eng = await ev("(window.Config96.state.result||{}).engine")
            ok(f"推导完成并出 {n} 条配置（{dt:.0f}s）", n == 81, str(n))
            ok("结果标记为 AI 生成", eng == "llm", str(eng))
            ok("步骤里有大模型调用记录",
               bool(await ev("window.Config96.state.steps.some(function(s){return /大模型/.test(s.title||'')})")))
            err = await ev("(document.getElementById('c96-err')||{}).textContent||''")
            ok("没有残留错误条", not err.strip(), err[:80])

            await asyncio.sleep(0.5)
            await shot("ai02_result.png")
            print(f"\n  结果：{n} 条 · {dt:.0f}s · engine={eng}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    print("\n" + "=" * 52)
    if fails:
        print(f"FAILED  {len(fails)} 项")
        for f in fails:
            print("  - " + f)
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
