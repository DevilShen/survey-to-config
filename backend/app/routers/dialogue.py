"""报价 Agent · 对话层（SSE 流式）。

对话是流水线的正式输入源：
    客户经理自由回答 → 槽位抽取（三档置信度）→ 回写 answers → 自动重跑
    → 逐阶段推送进度 → 出报价单。一个 SSE 连接完成一个完整回合。

事件类型（7 种）：meta / delta / question / extract / run_start / stage / quote / error
（meta 是第 8 种运维事件：turn_id + 在线状态，断线补拉要用）

离线兜底：无 SDLC_LLM_KEY 时全流程降级为模板话术 + 正则抽取，
事件序列一个不少——前端不需要知道后端有没有接模型。
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..analytics import pricing_agent as pa
from ..db import connect, one, rows, scalar
from ..llm import audit as llm_audit
from ..llm import prompts as lp
from ..llm.client import get_client, llm_status
from .auth import require_roles

router = APIRouter(prefix="/api/pricing/dialogue", tags=["pricing-dialogue"])

AFFIRM = re.compile(r"^(对|是的|是|没错|嗯|确认|可以|对的|好|好的|OK|ok)[。!！,，]*$")
DENY = re.compile(r"^(不对|不是|错了|不准确|不)")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _get_request(conn, rid: str) -> dict:
    r = one(conn, "SELECT * FROM quote_requests WHERE id=?", (rid,))
    if r is None:
        raise HTTPException(404, "报价需求不存在")
    return dict(r)


def _latest_run(conn, rid: str):
    r = one(conn, """SELECT * FROM quote_runs WHERE request_id=?
                     ORDER BY created_at DESC, id DESC LIMIT 1""", (rid,))
    return dict(r) if r else None


def _missing_slots(conn, req: dict, answers: dict) -> list[str]:
    """按当前规则权重算一遍还缺哪些槽位（权重 <8% 的不问，与引擎口径一致）。"""
    rules = pa.load_rules(conn)
    th1 = pa._p(rules, "R-THR-01")
    dec = pa.decompose(req, answers)
    qs = pa.build_questions(dec["slots"], th1.get("slots") or {})
    return [q["slot"] for q in qs]


def _gap_questions(conn, req: dict, answers: dict) -> list[dict]:
    rules = pa.load_rules(conn)
    th1 = pa._p(rules, "R-THR-01")
    dec = pa.decompose(req, answers)
    return pa.build_questions(dec["slots"], th1.get("slots") or {})


def _history(conn, rid: str, limit: int = 30) -> list[dict]:
    return [{"role": r["role"], "content": r["content"]}
            for r in rows(conn, """SELECT role, content FROM quote_dialogues
                                   WHERE request_id=? ORDER BY turn_no, id""", (rid,))][-limit:]


# ---------------------------------------------------------------- 查询接口

def _conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@router.get("/llm-status")
def status():
    """前端横幅用：在线/离线、模型名。读操作，不鉴权。"""
    return llm_status()


@router.get("/{rid}/history")
def history(rid: str, conn=Depends(_conn)):
    _get_request(conn, rid)
    items = []
    for r in rows(conn, """SELECT * FROM quote_dialogues WHERE request_id=?
                           ORDER BY turn_no, id""", (rid,)):
        items.append({"id": r["id"], "turn_no": r["turn_no"], "role": r["role"],
                      "content": r["content"],
                      "extracted": json.loads(r["extracted_json"]) if r["extracted_json"] else None,
                      "run_id": r["run_id"], "at": r["created_at"]})
    return {"request_id": rid, "count": len(items), "items": items, "llm": llm_status()}


@router.get("/{rid}/turns/{turn_no}")
def replay_turn(rid: str, turn_no: int, conn=Depends(_conn)):
    """断线补拉：拿回某个回合缓冲的完整事件序列。"""
    _get_request(conn, rid)
    agent = one(conn, """SELECT * FROM quote_dialogues WHERE request_id=? AND turn_no=?
                         AND role='agent' ORDER BY id DESC LIMIT 1""", (rid, turn_no))
    if agent is None or not agent["events_json"]:
        raise HTTPException(404, "该回合不存在或没有事件缓冲")
    return {"turn_no": turn_no, "events": json.loads(agent["events_json"]),
            "run_id": agent["run_id"], "at": agent["created_at"]}


# ---------------------------------------------------------------- SSE 回合

@router.post("/{rid}/turns")
def turn(rid: str, payload: dict = Body(...), user: dict = Depends(require_roles("pm", "admin"))):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "消息不能为空")
    if len(text) > 2000:
        raise HTTPException(400, "单条消息最长 2000 字")
    # 在打开 SSE 流之前先校验，让 404 走正常响应而不是断流
    chk = connect()
    try:
        _get_request(chk, rid)
    finally:
        chk.close()

    def stream():
        conn = connect()
        events: list[dict] = []     # 本回合事件缓冲，结束落库供断线补拉
        turn_no = -1
        run_id_out = None
        extracted_out = None
        reply_parts: list[str] = []
        try:
            req = _get_request(conn, rid)
            client = get_client()

            def emit(event, data):
                events.append({"event": event, "data": data})
                return _sse(event, data)

            # ---- 1. 存用户消息，定回合号
            turn_no = int(scalar(conn, "SELECT COALESCE(MAX(turn_no),0) FROM quote_dialogues WHERE request_id=?",
                                 (rid,), 0)) + 1
            cur = conn.execute(
                """INSERT INTO quote_dialogues(request_id,turn_no,role,content,created_at)
                   VALUES (?,?,?,?,?)""", (rid, turn_no, "user", text, _now()))
            conn.commit()
            turn_id = f"T{int(cur.lastrowid)}"

            yield emit("meta", {"turn_id": turn_id, "turn_no": turn_no,
                                "llm": llm_status()})

            latest = _latest_run(conn, rid)
            answers = {}
            if latest and latest.get("answers"):
                try:
                    answers = json.loads(latest["answers"]) or {}
                except ValueError:
                    answers = {}

            # ---- 2. 上一轮有待确认的抽取？先处理「对 / 不对」
            prev_agent = one(conn, """SELECT * FROM quote_dialogues WHERE request_id=? AND role='agent'
                                      ORDER BY id DESC LIMIT 1""", (rid,))
            pending = None
            if prev_agent and prev_agent["extracted_json"]:
                try:
                    pending = (json.loads(prev_agent["extracted_json"]) or {}).get("pending_confirm")
                except ValueError:
                    pending = None

            if pending and AFFIRM.match(text):
                answers.update({k: v["value"] for k, v in pending.items()})
                labels = "、".join(pa.SLOT_META[k]["label"] for k in pending)
                msg = f"好，{labels}按确认值写入，重新跑一遍推理。"
                reply_parts.append(msg)
                for chunk in _chunks(msg):
                    yield emit("delta", {"text": chunk})
                for s in _run_and_report(conn, req, answers, user, emit, turn_id):
                    yield s
                run_id_out = next((e["data"]["run_id"] for e in reversed(events)
                                   if e["event"] == "run_start"), None)
                return
            if pending and DENY.match(text):
                labels = "、".join(pa.SLOT_META[k]["label"] for k in pending)
                msg = f"收到，{labels}不按之前的理解写。请直接告诉我正确的说法，我重新抽。"
                reply_parts.append(msg)
                for chunk in _chunks(msg):
                    yield emit("delta", {"text": chunk})
                return

            # ---- 3. 分状态处理
            if latest is None:
                msg = "收到需求，我先跑一遍完整推理。"
                reply_parts.append(msg)
                for chunk in _chunks(msg):
                    yield emit("delta", {"text": chunk})
                for s in _run_and_report(conn, req, answers, user, emit, turn_id):
                    yield s
                return

            if latest["status"] == "clarified":
                missing = _missing_slots(conn, req, answers)
                if not missing:
                    # 槽位其实够了（可能规则权重刚改过），直接重跑
                    for s in _run_and_report(conn, req, answers, user, emit, turn_id):
                        yield s
                    return
                conv = _history(conn, rid)
                extracted, src = lp.extract_slots(client, conv, missing)
                t0 = time.perf_counter()
                llm_audit.record_call(
                    conn, run_id=latest["id"], turn_id=turn_id, node="decompose",
                    purpose="extract", model=src if src == "offline" else client.model,
                    prompt=conv[-1]["content"] if conv else "",
                    latency_ms=(time.perf_counter() - t0) * 1000,
                    status="offline" if src == "offline" else "ok")

                high = {s: v for s, v in extracted.items() if v["confidence"] >= 0.8}
                mid = {s: v for s, v in extracted.items() if 0.5 <= v["confidence"] < 0.8}
                extracted_out = {"extracted": extracted,
                                 "pending_confirm": mid or None}

                for slot, v in extracted.items():
                    yield emit("extract", {"slot": slot,
                                           "label": pa.SLOT_META[slot]["label"],
                                           "value": v["value"], "confidence": v["confidence"],
                                           "evidence": v["evidence"]})

                if high:
                    answers.update({k: v["value"] for k, v in high.items()})
                    labels = "、".join(f"「{pa.SLOT_META[k]['label']}」" for k in high)
                    msg = f"听清了，{labels}已写入。"
                    reply_parts.append(msg)
                    for chunk in _chunks(msg):
                        yield emit("delta", {"text": chunk})

                if mid:
                    asks = "；".join(
                        f"{pa.SLOT_META[k]['label']}我理解是「{_disp(k, v['value'])}」"
                        for k, v in mid.items())
                    msg = f"有两点想跟你核对：{asks}。回复「对」我就按这个写，不对就直接纠正我。"
                    reply_parts.append(msg)
                    for chunk in _chunks(msg):
                        yield emit("delta", {"text": chunk})
                    if not high:
                        return

                if not high and not mid:
                    msg = "这几个关键点我还是没抓到，换种方式再问一下。"
                    reply_parts.append(msg)
                    for chunk in _chunks(msg):
                        yield emit("delta", {"text": chunk})

                if high or not extracted:
                    # 有进展（或什么都没抽到，重跑让引擎重新生成追问）→ 重跑
                    for s in _run_and_report(conn, req, answers, user, emit, turn_id):
                        yield s
                return

            # ---- quoted / below_margin / reviewed：解读已有结论
            quote = json.loads(latest["quote"]) if latest.get("quote") else None
            if quote is None:
                msg = "这一单还没有有效结论，建议从右侧重新发起推理。"
                reply_parts.append(msg)
                for chunk in _chunks(msg):
                    yield emit("delta", {"text": chunk})
                return
            margin = {"margin": quote.get("margin"), "floor": None,
                      "note": f"规则版本 {latest['rule_version']}"}
            conf = latest["confidence"] or 0
            t0 = time.perf_counter()
            streamer = lp.explain_quote(client, quote, margin, conf)
            src = "llm" if client.online else "offline"
            buf = []
            try:
                for piece in streamer:
                    buf.append(piece)
                    yield emit("delta", {"text": piece})
            except Exception:
                if not buf:
                    piece = lp._offline_explain(quote, margin, conf)
                    buf.append(piece)
                    yield emit("delta", {"text": piece})
                    src = "offline"
            reply_parts.append("".join(buf))
            llm_audit.record_call(
                conn, run_id=latest["id"], turn_id=turn_id, node="explain",
                purpose="explain", model=src if src == "offline" else client.model,
                latency_ms=(time.perf_counter() - t0) * 1000,
                status="offline" if src == "offline" else "ok")
            yield emit("quote", {"no": quote.get("no"), "price": quote.get("price"),
                                 "margin": quote.get("margin"), "run_id": latest["id"]})
            run_id_out = latest["id"]

        except HTTPException:
            raise
        except Exception as e:  # 引擎或 LLM 挂了都不能让 SSE 裸 500
            yield _sse("error", {"code": "turn_failed", "message": str(e)[:200]})
        finally:
            if turn_no > 0:
                if run_id_out is None:
                    run_id_out = next((e["data"].get("run_id") for e in reversed(events)
                                       if e["event"] in ("run_start", "quote")), None)
                try:
                    conn.execute(
                        """INSERT INTO quote_dialogues(request_id,turn_no,role,content,
                               extracted_json,run_id,events_json,created_at)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (rid, turn_no, "agent", "".join(reply_parts) or "（本回合无文本回复）",
                         json.dumps(extracted_out, ensure_ascii=False) if extracted_out else None,
                         run_id_out,
                         json.dumps(events, ensure_ascii=False), _now()))
                    conn.execute("UPDATE quote_requests SET status=? WHERE id=?",
                                 (_status_of(conn, rid), rid))
                    conn.commit()
                except sqlite3.Error:
                    pass
                finally:
                    conn.close()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _status_of(conn, rid) -> str:
    latest = _latest_run(conn, rid)
    if latest is None:
        return "待推理"
    return {"clarified": "待补充", "below_margin": "待复核",
            "rejected": "待复核", "reviewed": "已复核"}.get(latest["status"], "已出价")


def _chunks(text: str, n: int = 6):
    """离线话术也切段推，前端渲染逻辑不用区分在线离线。"""
    for i in range(0, len(text), n):
        yield text[i:i + n]


def _disp(slot: str, value) -> str:
    if isinstance(value, list):
        return "、".join(str(x) for x in value)
    if slot == "scale":
        return f"{value} 人"
    if slot == "budget":
        return f"{value} 万"
    return str(value)


def _run_and_report(conn, req, answers, user, emit, turn_id):
    """跑引擎 + 持久化 + 逐阶段推事件。事件序列：
    run_start → stage × N → (question × M | quote)。返回最后 yield 的 run_id 由调用方从事件里取。"""
    result = pa.run(conn, req, answers=answers)
    result = pa.persist(conn, req, result, user["name"], answers=answers)
    run_id = result["run_id"]
    # 本回合早先挂到旧 run 上的 LLM 调用（如槽位抽取）归位到本次运行——
    # 溯源徽标才能如实反映"这轮推理的槽位是模型抽的"，而不是显示成代码
    conn.execute("UPDATE llm_calls SET run_id=? WHERE turn_id=? AND run_id IS NOT ?",
                 (run_id, turn_id, run_id))
    conn.commit()
    yield emit("run_start", {"run_id": run_id, "answers": answers})
    for n in result["trace"]:
        yield emit("stage", {"key": n["key"], "name": n["name"], "kind": n["kind"],
                             "status": n["status"], "loop": n.get("loop", 0),
                             "ms": n.get("ms", 0), "summary": n["summary"]})
    if result["status"] == "clarified":
        client = get_client()
        gaps = result.get("questions") or []
        t0 = time.perf_counter()
        qs, src = lp.ask_questions(client, req, gaps)
        llm_audit.record_call(
            conn, run_id=run_id, turn_id=turn_id, node="clarify", purpose="ask",
            model=src if src == "offline" else client.model,
            prompt=req.get("raw_text", "")[:400],
            latency_ms=(time.perf_counter() - t0) * 1000,
            status="offline" if src == "offline" else "ok")
        intro = "信息还差一点，我需要跟你确认几个问题：" if qs else "信息已补齐。"
        for chunk in _chunks(intro):
            yield emit("delta", {"text": chunk})
        for q in qs:
            yield emit("question", {"slot": q["slot"], "label": q["label"], "q": q["q"],
                                    "why": q["why"], "blocking": q["blocking"],
                                    "options": q.get("options") or [],
                                    "current": q.get("current"), "ask_by": q.get("ask_by", src)})
    else:
        q = result.get("quote") or {}
        yield emit("quote", {"no": q.get("no"), "price": q.get("price"),
                             "margin": q.get("margin"), "days": q.get("days"),
                             "status": result["status"],
                             "confidence": result["confidence"],
                             "run_id": run_id})
