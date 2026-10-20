"""真浏览器回归：导入问卷必须只填表单，绝不触发规则推导。

为什么要真浏览器跑：jsdom 里没有真实文件上传通道，也看不到 app.js 的全局事件委托。
这里用 CDP 的 DOM.setFileInputFiles 走一次真实 multipart 上传，并抓包确认
整个过程没有任何 POST /api/config/derive。

跑法（后端需在 8010 运行）：
    .venv/Scripts/python.exe frontend/tests/e2e_import_survey.py [docx路径]
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
PORT = 9455
URL = "http://127.0.0.1:8010/#/topic-config"
BAD = "/api/config/derive"
DOCX = Path(sys.argv[1] if len(sys.argv) > 1 else
            str(Path(__file__).resolve().parents[2] / "backend" / "tests" / "fixtures" / "survey_filled.docx"))

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
    if not DOCX.exists():
        print(f"缺少样本文件：{DOCX}")
        return 2

    profile = Path(tempfile.mkdtemp(prefix="cdpimp_"))
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

            async def call(method: str, params: dict | None = None):
                nonlocal seq
                seq += 1
                mid = seq
                await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == mid:
                        if "error" in msg:
                            raise RuntimeError(f"{method}: {msg['error']}")
                        return msg.get("result", {})
                    if msg.get("method") == "Network.requestWillBeSent":
                        u = msg["params"]["request"]["url"]
                        if BAD in u and not u.endswith("/derive/stream") and not u.endswith("/derive/llm-stream"):
                            hits.append(u)

            await call("Network.enable")
            await call("Runtime.enable")

            async def ev(expr: str):
                r = await call("Runtime.evaluate",
                               {"expression": expr, "returnByValue": True, "awaitPromise": True})
                return r.get("result", {}).get("value")

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
            await call("DOM.enable")


            # 清掉残留草稿，避免自动恢复与导入结果打架
            await ev("fetch('/api/config/draft',{method:'DELETE'}).then(function(){location.reload();});'x'")
            await asyncio.sleep(1.5)

            for _ in range(80):
                if await ev("!!document.querySelector('[data-c96=\"import\"]')"):
                    break
                await asyncio.sleep(0.25)

            print("\n[真浏览器] 导入问卷")
            ok("页面已挂载", bool(await ev("!!document.getElementById('c96-left')")))

            hits.clear()
            await ev("document.querySelector('[data-c96=\"import\"]').click(); 'x'")
            await asyncio.sleep(0.4)
            ok("点「导入问卷」生成了文件选择控件", bool(await ev("!!document.getElementById('c96-filepick')")))

            root = await call("DOM.getDocument", {"depth": -1})
            node = await call("DOM.querySelector",
                              {"nodeId": root["root"]["nodeId"], "selector": "#c96-filepick"})
            ok("能定位到文件输入框", node.get("nodeId", 0) > 0, str(node))
            await call("DOM.setFileInputFiles",
                       {"nodeId": node["nodeId"], "files": [str(DOCX)]})

            await ev("""(function(){
              var i=document.getElementById('c96-filepick');
              i.dispatchEvent(new Event('change',{bubbles:true}));
              return 'x';
            })()""")

            done = False
            for _ in range(60):
                if await ev("!!(window.Config96.state.imp && window.Config96.state.imp.state==='done')"):
                    done = True
                    break
                await asyncio.sleep(0.3)
            ok("导入完成", done, str(await ev("JSON.stringify(window.Config96.state.imp&&window.Config96.state.imp.state)")))

            filled = await ev("Object.keys(window.Config96.state.ans||{}).length")
            ok("答案已填入 state（≥90 项）", isinstance(filled, int) and filled >= 90, str(filled))
            ok("第 1 题项目名称已回填",
               bool(await ev("(window.Config96.state.ans.Q1||'').indexOf('示例')>=0")),
               str(await ev("window.Config96.state.ans.Q1")))

            # 本次需求的核心断言
            ok("导入过程零 /api/config/derive 请求", not hits, str(hits))
            ok("右栏仍是未计算空态",
               bool(await ev("/等待计算/.test(document.getElementById('c96-right').textContent)")))
            ok("没有自动产生推导结果", bool(await ev("window.Config96.state.result===null")))

            ok("左栏显示导入结果条",
               bool(await ev("!!document.querySelector('.c96-imp.ok')")))
            ok("结果条说明「没有跑推导」",
               bool(await ev("/没有跑推导/.test((document.querySelector('.c96-imp')||{}).textContent||'')")))
            ok("表单输入框真的填上了",
               bool(await ev("(document.querySelector('[data-c96=\"text\"][data-q=\"Q1\"]')||{}).value===(window.Config96.state.ans.Q1)")))
            ok("表格题也渲染出数据行",
               bool(await ev("document.querySelectorAll('.c96-q[data-qq=\"Q3\"] tbody tr').length>=8")),
               str(await ev("document.querySelectorAll('.c96-q[data-qq=\"Q3\"] tbody tr').length")))

            # 点「AI 推导」（唯一计算入口）才允许请求
            hits.clear()
            await ev("document.querySelector('[data-c96=\"run\"]').click(); 'x'")
            # AI 推导逐组调模型，实测 66-88 秒；上限给到 200 秒，别把「慢」判成「没出结果」
            for _ in range(500):
                if await ev("window.Config96.state.phase==='idle' && !!window.Config96.state.result"):
                    break
                await asyncio.sleep(0.4)
            if not await ev("!!window.Config96.state.result"):
                print('    [诊断] error=', await ev("window.Config96.state.error"),
                      'llmErr=', await ev("window.Config96.state.llmErr"))
            ok("点「AI 推导」后才出 81 条配置",
               bool(await ev("(window.Config96.state.result||{}).configs && window.Config96.state.result.configs.length===81")),
               str(await ev("((window.Config96.state.result||{}).configs||[]).length")))
            ok("推导走的是流式通道，没有回落到一次性接口", not hits, str(hits))
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
