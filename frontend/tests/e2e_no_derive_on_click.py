"""真浏览器点击回归：点问卷选项不许打 /api/config/derive，也不许重建 DOM。

为什么要这个：jsdom 只加载 config96.js，看不到 app.js 那套全局 `closest('[data-q]')`
委托。曾经就是它把 v4 选项按钮的点击全截走，导致「点一下选项，页面刷新一次，
还多发一发 POST /api/config/derive」。

跑法（后端需在 8010 运行）：
    .venv/Scripts/python.exe frontend/tests/e2e_no_derive_on_click.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9444
URL = "http://127.0.0.1:8010/#/topic-config"
BAD = "/api/config/derive"

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
    profile = Path(tempfile.mkdtemp(prefix="cdp96_"))
    proc = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--no-default-browser-check",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
        "--window-size=1560,980", URL,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        ws_url = await asyncio.to_thread(wait_target)
        hits: list[str] = []
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
                    if msg.get("method") == "Network.requestWillBeSent":
                        u = msg["params"]["request"]["url"]
                        # /derive/stream 与 /derive/llm-stream 是正常推导通道，只有裸 /derive 才算违规
                        if BAD in u and not u.endswith("/derive/stream") and not u.endswith("/derive/llm-stream"):
                            hits.append(u)

            await call("Network.enable")
            await call("Runtime.enable")

            async def ev(expr: str):
                r = await call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                                    "awaitPromise": True})
                return r.get("result", {}).get("result", {}).get("value")

            # 等首帧加载完再换 token：headless 起得慢，origin 没就绪时相对路径 fetch 直接失败，
            # 表现是「注入完 token 跳过去又被踢回登录页」。
            for _ in range(80):
                rr = await call("Runtime.evaluate", {"expression":
                    "location.origin.indexOf('127.0.0.1')>=0 && document.readyState==='complete'",
                    "returnByValue": True})
                if rr.get("result", {}).get("result", {}).get("value"):
                    break
                await asyncio.sleep(0.25)

            LOGIN_JS = """(async function(){
              try {
                const meta = await (await fetch('/api/auth/demo')).json();
                const r = await fetch('/api/auth/login',{method:'POST',
                  headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({username: meta.username, password: meta.password})});
                const d = await r.json();
                if (!d.token) return 'no-token:' + JSON.stringify(d).slice(0,80);
                localStorage.setItem('sdlc_token', d.token);
                return 'ok';
              } catch (e) { return 'err:' + e.message; }
            })()"""
            got = None
            for _ in range(20):
                rr = await call("Runtime.evaluate", {"expression": LOGIN_JS,
                                                    "returnByValue": True, "awaitPromise": True})
                got = rr.get("result", {}).get("result", {}).get("value")
                if got == 'ok':
                    break
                await asyncio.sleep(0.4)
            if got != 'ok':
                print('    [info] 登录注入返回值:', got, '（以页面是否挂载为准）')
            await call("Runtime.evaluate", {"expression": "location.replace(%s); 'x'" % json.dumps(URL)})
            await asyncio.sleep(1.2)


            # 先清掉可能残留的草稿，再从空表单开始
            # （草稿自动恢复是产品特性，会干扰这里"初始只有 1 行"的断言）
            await ev("fetch('/api/config/draft',{method:'DELETE'}).then(function(){location.reload();});'x'")
            await asyncio.sleep(1.5)

            # 等页面挂好问卷
            for _ in range(80):
                if await ev("!!document.querySelector('[data-c96=\\'pick\\'][data-q=\\'Q5\\']')"):
                    break
                await asyncio.sleep(0.25)

            print("\n[真浏览器] 专题④ 问卷点击行为")
            ok("问卷已挂载", bool(await ev("!!document.getElementById('c96-left')")))
            ok("右栏是未计算空态", bool(await ev("/等待计算/.test(document.getElementById('c96-right').textContent)")))

            # 打点：记录 Q1 那个问题块的 DOM 引用，重建就会失联
            await ev("window.__q1=document.querySelector('.c96-q[data-qq=\\'Q1\\']');"
                     "window.__h1=document.getElementById('c96-root').innerHTML.length; 'ok'")

            hits.clear()
            await ev("document.querySelector('[data-c96=\\'pick\\'][data-q=\\'Q5\\']').click(); 'clicked'")
            await asyncio.sleep(1.2)

            same = await ev("document.querySelector('.c96-q[data-qq=\\'Q1\\']')===window.__q1")
            ans = await ev("window.Config96.state.ans.Q5||null")
            ok("点选项后问题块没被重建", bool(same))
            ok("选项值进了 state", bool(ans), str(ans))
            ok(f"没有请求 {BAD}", not hits, str(hits))

            # 再连点多个选项，确认不会积累请求
            hits.clear()
            for q in ("Q6", "Q7", "Q5"):
                await ev(f"var e=document.querySelector('[data-c96=\\'pick\\'][data-q=\\'{q}\\']');"
                         f"if(e)e.click(); 'x'")
                await asyncio.sleep(0.45)
            ok("连续点击也不请求推导", not hits, str(hits))
            ok("还是没有自动推导（结果仍是空态）",
               bool(await ev("/等待计算/.test(document.getElementById('c96-right').textContent)")))

            # 表格题：点单元格 / 打字 / 加行，都不许请求推导
            hits.clear()
            await ev("""(function(){
              var c=document.querySelector('.c96-q[data-qq="Q3"] .c96-cell');
              window.__c3=c; c.focus(); c.value='南通某物业';
              c.dispatchEvent(new Event('input',{bubbles:true}));
              return 'x';
            })()""")
            await asyncio.sleep(0.8)
            ok("单元格输入不触发推导请求", not hits, str(hits))
            ok("单元格没被重建（不重绘）",
               bool(await ev("document.querySelector('.c96-q[data-qq=\\'Q3\\'] .c96-cell')===window.__c3")))
            ok("表格值进了 state",
               bool(await ev("(window.Config96.state.ans.Q3||[])[0]['所属公司/部门']==='南通某物业'")))
            ok("在最后一行落笔后自动补一行",
               bool(await ev("document.querySelectorAll('.c96-q[data-qq=\\'Q3\\'] tbody tr').length===2")))

            hits.clear()
            await ev("document.querySelector('.c96-q[data-qq=\\'Q3\\'] [data-c96=\\'rowadd\\']').click(); 'x'")
            await asyncio.sleep(0.6)
            ok("「+ 添加一行」也不请求推导", not hits, str(hits))
            ok("加行后 3 行",
               bool(await ev("document.querySelectorAll('.c96-q[data-qq=\\'Q3\\'] tbody tr').length===3")))
            ok("加行后序号列跟着排（Q39 展开可见三列）",
               bool(await ev("document.querySelector('.c96-q[data-qq=\\'Q3\\'] thead').textContent.includes('项目业态')")))

            # 只有点「AI 推导」（唯一计算入口）才允许请求
            hits.clear()
            await ev("document.querySelector('[data-c96=\\'run\\']').click(); 'run'")
            await asyncio.sleep(0.25)
            ok("点「AI 推导」进了推导态",
               bool(await ev("/AI 推导中|推导中|Agent/.test(document.getElementById('c96-right').textContent)")))
            # 真模型逐组推导约 60-90 秒，给足 120 秒
            for _ in range(300):
                if await ev("window.Config96.state.phase==='idle' && !!window.Config96.state.result"):
                    break
                await asyncio.sleep(0.4)
            ok("推导完成并出结果",
               bool(await ev("document.querySelectorAll('.c96-cfg').length>0")))
            ok("过程中没有回落到一次性接口", not hits, str(hits))
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
