"""真浏览器回归：登录页 → JWT 登录 → 进入系统 → 退出。

覆盖四件容易做假的事：
1. 未登录访问业务页必须被挡到登录页（且带回跳地址）；
2. demo 账号一键填入真能登进去，token 落在 localStorage；
3. 密码错时停在登录页并给明确提示，不跳转；
4. 退出登录真的清掉凭证，刷新也进不去（不是只切了个界面）。

跑法（后端需在 8010 运行）：
    backend/.venv/Scripts/python.exe frontend/tests/e2e_login.py
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
PORT = 9502
URL = "http://127.0.0.1:8010/#/overview"
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
    profile = Path(tempfile.mkdtemp(prefix="cdplogin_"))
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
                        return msg.get("result", {})

            await call("Runtime.enable")
            await call("Page.enable")

            async def ev(expr: str):
                r = await call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                                    "awaitPromise": True})
                return r.get("result", {}).get("value")

            async def wait_js(expr: str, ms: int = 8000) -> bool:
                t0 = time.time()
                while (time.time() - t0) * 1000 < ms:
                    if await ev(expr):
                        return True
                    await asyncio.sleep(0.15)
                return False

            async def shot(name: str):
                r = await call("Page.captureScreenshot", {"format": "png"})
                (OUT / name).write_bytes(base64.b64decode(r["data"]))
                print(f"    截图 {name}")

            # ---------- 1. 未登录被挡 ----------
            print("\n[真浏览器] 登录流程")
            landed = await wait_js("/login\\.html/.test(location.pathname)", 10000)
            ok("未登录访问业务页被挡到登录页", landed,
               await ev("location.href"))
            ok("回跳地址带上了原来要去的页面",
               bool(await ev("/next=/.test(location.search)")), await ev("location.search"))
            ok("没有把业务数据渲染出来（#view 不存在）",
               bool(await ev("!document.getElementById('view')")))

            # ---------- 2. 登录页内容 ----------
            await wait_js("!!document.getElementById('lgFill')")
            demo_user = await ev("document.getElementById('lgDemoUser').textContent")
            ok("登录页展示了演示账号", bool(demo_user) and demo_user != "", str(demo_user))
            await shot("lg01_login.png")

            # ---------- 3. 密码错 ----------
            await ev("""(function(){
              var u=document.getElementById('lgUser'), p=document.getElementById('lgPass');
              u.value='demo'; p.value='wrong-password';
              document.getElementById('lgForm').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
              return 'x';
            })()""")
            shown = await wait_js("document.getElementById('lgErr').classList.contains('show')", 6000)
            msg = await ev("document.getElementById('lgErr').textContent")
            ok("密码错时给出错误提示", shown and "不正确" in (msg or ""), str(msg))
            ok("密码错时没有跳转", bool(await ev("/login\\.html/.test(location.pathname)")))
            ok("错误提示不是红色以外的兜底文案（是后端 detail）",
               bool(msg) and "HTTP" not in msg, str(msg))
            await shot("lg02_error.png")

            # ---------- 4. 一键填入 + 正确登录 ----------
            await ev("document.getElementById('lgFill').click(); 'x'")
            await asyncio.sleep(0.5)
            filled = await ev("[document.getElementById('lgUser').value, document.getElementById('lgPass').value]")
            ok("一键填入把账号密码都填上了",
               isinstance(filled, list) and filled[0] == demo_user and bool(filled[1]),
               str(filled))
            ok("重新输入后错误提示自动收起",
               bool(await ev("!document.getElementById('lgErr').classList.contains('show')")))

            await ev("document.getElementById('lgForm').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})); 'x'")
            inside = await wait_js("!!document.getElementById('view') && !!document.querySelector('.sidebar')", 12000)
            ok("登录后进入系统主界面", inside, await ev("location.pathname + location.hash"))

            token = await ev("(function(){try{return localStorage.getItem('sdlc_token')||sessionStorage.getItem('sdlc_token')}catch(e){return null}})()")
            ok("凭证存进了浏览器", bool(token) and str(token).count(".") == 2,
               str(token)[:28] + "…")
            ok("凭证能解开看到角色（JWT 标准三段）", bool(token) and str(token).startswith("eyJ"),
               str(token)[:16])

            # ---------- 5. 顶栏身份 ----------
            await asyncio.sleep(0.8)
            av = await ev("document.getElementById('avatar').textContent")
            ok("头像显示当前用户首字", av == "演", str(av))
            await ev("document.getElementById('avatar').click(); 'x'")
            await asyncio.sleep(0.3)
            menu = await ev("(document.getElementById('whoMenu')||{}).textContent||''")
            ok("头像菜单显示账号与角色", "演示账号" in menu and "管理员" in menu, menu[:40])
            ok("菜单里有退出登录", "退出登录" in menu, menu[:40])
            await shot("lg03_inside.png")

            # ---------- 6. 已登录再访问登录页 ----------
            await ev("document.getElementById('whoMenu').remove(); location.href='login.html'; 'x'")
            await asyncio.sleep(1.8)
            ok("已登录访问登录页会自动回业务页",
               bool(await ev("/login\\.html/.test(location.pathname)===false")),
               await ev("location.pathname"))

            # ---------- 7. 退出登录 ----------
            await wait_js("!!document.getElementById('avatar')", 8000)
            await ev("document.getElementById('avatar').click(); 'x'")
            await asyncio.sleep(0.3)
            await ev("document.getElementById('whoOut').click(); 'x'")
            back = await wait_js("/login\\.html/.test(location.pathname)", 8000)
            ok("退出后回到登录页", back, await ev("location.pathname"))
            left = await ev("(function(){try{return !(localStorage.getItem('sdlc_token')||sessionStorage.getItem('sdlc_token'))}catch(e){return true}})()")
            ok("退出后凭证已清除", bool(left))

            # ---------- 8. 坏 token ----------
            await ev("""(function(){
              try{localStorage.setItem('sdlc_token','eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJVLTk5OSIsImV4cCI6OTk5OTk5OTk5OX0.zzzz');}catch(e){}
              location.href='/'; return 'x';
            })()""")
            kicked = await wait_js("/login\\.html/.test(location.pathname)", 10000)
            ok("被篡改的 token 会被后端拒掉并踢回登录页", kicked, await ev("location.pathname + location.search"))
            await shot("lg04_kicked.png")
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
