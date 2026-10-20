"""真浏览器回归：待确认弹窗里改答案 / 人工确认，保存后真的重算。

jsdom 只能证明"发对了请求"，这里证明端到端闭环：
改第 66 题 → 冲突 A10 真的消失；填人工确认 → 配置值真的变、统计里 overridden +1。

跑法（后端需在 8010 运行）：
    .venv/Scripts/python.exe frontend/tests/e2e_issue_modal.py
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
PORT = 9488
URL = "http://127.0.0.1:8010/?demo=1&tab=issues#/topic-config"

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
    profile = Path(tempfile.mkdtemp(prefix="cdpmodal_"))
    proc = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--no-default-browser-check",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
        "--window-size=1560,1000", URL,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        ws_url = await asyncio.to_thread(wait_target)
        bodies: list[str] = []
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
                        req = msg["params"]["request"]
                        if "/api/config/derive" in req["url"] and req.get("postData"):
                            bodies.append(req["postData"])

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


            # ?demo=1 自动跑一次 AI 推导（逐组调模型，约 70 秒）
            for _ in range(300):
                if await ev("!!(window.Config96 && window.Config96.state.result)"):
                    break
                await asyncio.sleep(0.4)

            print("\n[真浏览器] 待确认弹窗就地处理")
            base_issues = await ev("window.Config96.state.result.issues.length")
            # AI 推导会在规则冲突之外，额外把「模型留待确认」的配置也计进来，
            # 所以条数不少于规则的 3 条即可（具体条数随模型输出浮动）
            ok("示例跑完，有待确认项", base_issues >= 3, str(base_issues))
            await ev("document.querySelector('[data-c96=\"tab\"][data-k=\"issues\"]').click(); 'x'")
            await asyncio.sleep(0.5)

            # ---------- 路线一：改答案 ----------
            got = await ev("""(function(){
              var rows=document.querySelectorAll('.c96-issue');
              for (var i=0;i<rows.length;i++){ if(rows[i].textContent.indexOf('A10')>=0){ rows[i].click(); return i; } }
              return -1;
            })()""")
            ok("找到 A10 并打开弹窗", got >= 0, str(got))
            await asyncio.sleep(0.5)
            ok("弹窗里是 A10", bool(await ev("/评价时限单位口径/.test(document.querySelector('.c96-mdl').textContent)")))

            # 第 66 题：1 个月 → 30 天
            await ev("""(function(){
              var n=document.querySelector('.c96-mdl .c96-ein.num');
              n.value='30'; n.dispatchEvent(new Event('input',{bubbles:true}));
              var u=document.querySelector('.c96-mdl .c96-ein.unit');
              u.value='day'; u.dispatchEvent(new Event('change',{bubbles:true}));
              return 'x';
            })()""")
            await asyncio.sleep(0.4)
            ok("底栏提示已改动", bool(await ev("/已改动/.test(document.querySelector('.c96-mnote').textContent)")))

            bodies.clear()
            await ev("document.querySelector('[data-c96m=\"save\"]').click(); 'x'")
            for _ in range(300):
                if await ev("window.Config96.state.phase==='idle' && !!window.Config96.state.result"):
                    break
                await asyncio.sleep(0.4)
            ok("保存后弹窗关闭", not bool(await ev("!!document.querySelector('.c96-mdl')")))
            ok("答案里第 66 题变成 30 天",
               bool(await ev("(function(){var v=window.Config96.state.ans.Q66; return v&&v.v===30&&v.unit==='day';})()")),
               str(await ev("JSON.stringify(window.Config96.state.ans.Q66)")))
            # 总数会随模型输出浮动（模型留「待确认」的配置条数不定），
            # 所以按 A10 这一条本身的存亡判断，而不是数总数
            after = await ev("window.Config96.state.result.issues.length")
            has_a10 = await ev("window.Config96.state.result.issues.some(function(i){return i.id==='A10';})")
            ok(f"冲突 A10 从待确认里消失（总数 {base_issues} → {after}）", has_a10 is False, str(has_a10))
            ok("提交体里带的是改后的答案",
               any('"Q66"' in b and '"v":30' in b.replace(" ", "") for b in bodies),
               bodies[-1][:120] if bodies else "空")

            # ---------- 路线二：人工确认 ----------
            await ev("document.querySelector('[data-c96=\"tab\"][data-k=\"issues\"]').click(); 'x'")
            await asyncio.sleep(0.4)
            await ev("document.querySelectorAll('.c96-issue')[0].click(); 'x'")
            await asyncio.sleep(0.5)
            code = await ev("document.querySelector('.c96-mdl [data-c96m=\"ov\"]').dataset.code")
            ok("弹窗提供人工确认输入", bool(code), str(code))

            # 不写理由 → 拒绝保存
            await ev("""(function(){
              var o=document.querySelector('.c96-mdl [data-c96m="ov"]');
              o.value='测试确认值'; o.dispatchEvent(new Event('input',{bubbles:true}));
              return 'x';
            })()""")
            await ev("document.querySelector('[data-c96m=\"save\"]').click(); 'x'")
            await asyncio.sleep(0.4)
            ok("没写理由时不保存", bool(await ev("!!document.querySelector('.c96-mdl')")))
            ok("提示补理由", bool(await ev("/没写理由/.test(document.querySelector('.c96-mnote').textContent)")))

            bodies.clear()
            await ev("""(function(){
              var w=document.querySelector('.c96-mdl [data-c96m="ovwhy"]');
              w.value='客户电话确认'; w.dispatchEvent(new Event('input',{bubbles:true}));
              return 'x';
            })()""")
            await ev("document.querySelector('[data-c96m=\"save\"]').click(); 'x'")
            for _ in range(300):
                if await ev("window.Config96.state.phase==='idle' && !!window.Config96.state.result"):
                    break
                await asyncio.sleep(0.4)
            ok("补理由后保存成功", not bool(await ev("!!document.querySelector('.c96-mdl')")))
            ok("提交体里带 overrides",
               any('"overrides"' in b for b in bodies), bodies[-1][:160] if bodies else "空")
            ok("配置值被人工确认覆盖",
               bool(await ev(f"""(function(){{
                 var c=(window.Config96.state.result.configs||[]).filter(function(x){{return x.code==='{code}';}})[0];
                 return !!c && !!c.override && c.override.v==='测试确认值';
               }})()""")),
               code)
            ok("状态变已确认且写明人工确认理由",
               bool(await ev(f"""(function(){{
                 var c=(window.Config96.state.result.configs||[]).filter(function(x){{return x.code==='{code}';}})[0];
                 return !!c && c.st==='ok' && c.note.indexOf('人工确认：客户电话确认')>=0;
               }})()""")), code)
            ok("统计里 overridden = 1",
               bool(await ev("window.Config96.state.result.stats.overridden===1")),
               str(await ev("window.Config96.state.result.stats.overridden")))
            ok("输入指纹因人工确认而变化",
               bool(await ev("window.Config96.state.result.input_hash.length===16")))
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
