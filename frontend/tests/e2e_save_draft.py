"""真浏览器回归：「仅保存」只落盘，不计算；刷新后草稿能捡回来。

关键看两件事：
1. 点「仅保存」时网络里只有 POST /api/config/draft，**没有** /api/config/derive；
2. 刷新页面（不带 demo 参数）后答案被恢复，且不会自动跑推导。

跑法（后端需在 8010 运行）：
    .venv/Scripts/python.exe frontend/tests/e2e_save_draft.py
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
PORT = 9499
URL = "http://127.0.0.1:8010/?demo=1&tab=issues#/topic-config"
PLAIN = "http://127.0.0.1:8010/#/topic-config"

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
                    if t.get("type") == "page":
                        return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.4)
    raise RuntimeError("拿不到 CDP 页面目标")


async def main() -> int:
    profile = Path(tempfile.mkdtemp(prefix="cdpdraft_"))
    proc = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--no-default-browser-check",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
        "--window-size=1560,1000", URL,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        ws_url = await asyncio.to_thread(wait_target)
        hits: list[str] = []
        async with websockets.connect(ws_url, max_size=None) as ws:
            seq = 0

            async def call(method, params=None):
                nonlocal seq
                seq += 1
                mid = seq
                await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == mid:
                        return msg.get("result", {})
                    if msg.get("method") == "Network.requestWillBeSent":
                        u = msg["params"]["request"]["url"]
                        m = msg["params"]["request"]["method"]
                        if "/api/config/derive" in u and not u.endswith("/derive/stream") and not u.endswith("/derive/llm-stream"):
                            hits.append(m + " " + u)
                        if u.endswith("/api/config/draft") and m == "POST":
                            hits.append("DRAFT")

            await call("Network.enable")
            await call("Runtime.enable")

            async def ev(expr):
                r = await call("Runtime.evaluate",
                               {"expression": expr, "returnByValue": True, "awaitPromise": True})
                return r.get("result", {}).get("value")

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


            async def wait_idle(rounds=600):   # AI 推导逐组调模型，约 60-90 秒
                for _ in range(rounds):
                    if await ev("window.Config96 && window.Config96.state.phase==='idle'"):
                        return True
                    await asyncio.sleep(0.25)
                return False

            # ?demo=1 自动跑一次 AI 推导（逐组调模型，约 70 秒）
            for _ in range(300):
                if await ev("!!(window.Config96 && window.Config96.state.result)"):
                    break
                await asyncio.sleep(0.4)

            print("\n[真浏览器] 仅保存草稿")
            ok("示例已跑完并出结果", bool(await ev("!!window.Config96.state.result")))

            # 先清掉历史草稿，保证从干净状态开始
            await ev("window.API.clearConfigDraft(); 'x'")
            await asyncio.sleep(0.3)

            await ev("document.querySelector('[data-c96=\"tab\"][data-k=\"issues\"]').click(); 'x'")
            await asyncio.sleep(0.4)
            await ev("""(function(){
              var rows=document.querySelectorAll('.c96-issue');
              for (var i=0;i<rows.length;i++){ if(rows[i].textContent.indexOf('A10')>=0){ rows[i].click(); return i; } }
              return -1;
            })()""")
            await asyncio.sleep(0.5)
            ok("打开 A10 弹窗", bool(await ev("/评价时限单位口径/.test(document.querySelector('.c96-mdl').textContent)")))

            async def set_q66():
                await ev("""(function(){
                  var n=document.querySelector('.c96-mdl .c96-ein.num');
                  n.value='30'; n.dispatchEvent(new Event('input',{bubbles:true}));
                  var u=document.querySelector('.c96-mdl .c96-ein.unit');
                  u.value='day'; u.dispatchEvent(new Event('change',{bubbles:true}));
                  return 'x';
                })()""")
                await asyncio.sleep(0.4)

            await set_q66()
            ok("弹窗里有「仅保存」按钮", bool(await ev("!!document.querySelector('[data-c96m=\"saveonly\"]')")))

            hits.clear()
            await ev("document.querySelector('[data-c96m=\"saveonly\"]').click(); 'x'")
            await asyncio.sleep(1.0)

            ok("发出了一次草稿保存请求", "DRAFT" in hits, str(hits))
            ok("没有触发任何一次性推导请求", not [h for h in hits if "derive" in h], str(hits))
            ok("弹窗关闭", not bool(await ev("!!document.querySelector('.c96-mdl')")))
            ok("顶栏出现草稿标记",
               bool(await ev("/草稿 \\d\\d:\\d\\d 已存/.test(document.getElementById('c96-acts').textContent)")),
               str(await ev("document.getElementById('c96-acts').textContent.slice(0,50)")))
            ok("state 里记录了保存时间", bool(await ev("!!window.Config96.state.savedAt")))

            # 后端真的存住了吗
            draft = await ev("""(async function(){
              var t=(function(){try{return localStorage.getItem('sdlc_token')||sessionStorage.getItem('sdlc_token')}catch(e){return null}})();
              var r = await fetch('/api/config/draft',{headers:t?{'Authorization':'Bearer '+t}:{}});
              return await r.json();
            })()""")
            ok("后端草稿存在且含改后的答案",
               bool(draft and draft.get("exists") and draft["answers"].get("Q66") == {"v": 30, "unit": "day"}),
               json.dumps(draft.get("answers", {}).get("Q66") if draft else None, ensure_ascii=False))

            # ---------- 刷新页面（不带 demo）----------
            hits.clear()
            await call("Page.navigate", {"url": PLAIN})
            await asyncio.sleep(1.0)
            for _ in range(80):
                if await ev("!!(window.Config96 && window.Config96.state.ans.Q66)"):
                    break
                await asyncio.sleep(0.25)

            ok("刷新后草稿被恢复到表单",
               bool(await ev("(function(){var v=window.Config96.state.ans.Q66; return v&&v.v===30&&v.unit==='day';})()")),
               str(await ev("JSON.stringify(window.Config96.state.ans.Q66)")))
            ok("刷新后没有自动跑推导",
               bool(await ev("window.Config96.state.result===null && window.Config96.state.phase==='idle'")))
            ok("刷新后没有发推导请求", not [h for h in hits if "derive" in h], str(hits))
            ok("右栏仍是未计算空态",
               bool(await ev("/等待计算/.test(document.getElementById('c96-right').textContent)")))
            ok("顶栏提示草稿已存未计算",
               bool(await ev("/草稿/.test(document.getElementById('c96-acts').textContent)")))

            # 点「AI 推导」（唯一计算入口）→ 草稿作废
            await ev("document.querySelector('[data-c96=\"run\"]').click(); 'x'")
            await wait_idle()
            await asyncio.sleep(0.6)
            ok("点「AI 推导」后出 81 条配置",
               bool(await ev("(window.Config96.state.result||{}).configs && window.Config96.state.result.configs.length===81")),
               str(await ev("((window.Config96.state.result||{}).configs||[]).length")))
            ok("计算后草稿标记清掉", bool(await ev("window.Config96.state.savedAt===null")))
            left = await ev("""(async function(){
              var t=(function(){try{return localStorage.getItem('sdlc_token')||sessionStorage.getItem('sdlc_token')}catch(e){return null}})();
              var r = await fetch('/api/config/draft',{headers:t?{'Authorization':'Bearer '+t}:{}});
              return await r.json();
            })()""")
            ok("计算后后端的草稿也清了", bool(left and left.get("exists") is False), str(left))
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
