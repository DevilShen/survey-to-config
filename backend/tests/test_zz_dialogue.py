"""对话层（LLM 接入）的闭环测试。

断言的不是"接口能通"，而是六条底线：

    1. SSE 事件序列完整 —— 离线也不缺帧，前端渲染不用区分在线离线
    2. 抽取可溯源     —— 对话里抽出的槽位，其 LLM 调用必须挂在这轮推理的 run 上
    3. 自由文本能变参数 —— "八百个律师用" 要变成 scale=800，evidence 必须是原文子串
    4. 驳回意见不是牢骚 —— "总价 80 万以内" 要变成下一轮推理的硬约束
    5. 缩范围有上限    —— 毛利不足自动砍模块，至多 1 轮，绝不无限循环
    6. 改规则先回归    —— 保存前能看到历史需求全量重算的对比表
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.analytics import pricing_agent as pa
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _req(conn, rid):
    return dict(conn.execute("SELECT * FROM quote_requests WHERE id=?", (rid,)).fetchone())


def _token(client, email="zhoumin@demo.cn"):
    r = client.post("/api/auth/login", json={"username": email, "password": "demo1234"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


def _sse_events(text: str) -> list[tuple[str, dict]]:
    """把 SSE 文本流解析回 (event, data) 序列。"""
    out = []
    for block in text.split("\n\n"):
        ev, data = None, ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if ev and data:
            out.append((ev, json.loads(data)))
    return out


# ---------------------------------------------------------------- 离线抽取

def test_离线抽取把口语变成参数():
    from app.llm import prompts as lp
    text = "大概八百个律师用，标准配置，预算 60 万，明年二季度上线"
    out = lp._offline_extract(text, ["scale", "mode", "budget", "deadline"])
    assert str(out["scale"]["value"]) == "800"
    assert out["scale"]["confidence"] >= 0.8
    # evidence 必须是原文子串——抽取可溯源的最低要求
    assert out["scale"]["evidence"] in text
    assert str(out["budget"]["value"]) == "60"
    assert re.fullmatch(r"\d{4}-\d{2}", str(out["deadline"]["value"]))


# ---------------------------------------------------------------- SSE 回合

def test_对话写操作要登录(client):
    r = client.post("/api/pricing/dialogue/QR-2026-001/turns", json={"text": "hi"})
    assert r.status_code == 401


def test_对话补答全链路(client):
    """待补充 → 自由文本补答 → 抽取 → 自动重推 → 出价，且抽取挂到本轮 run。"""
    h = _token(client)
    # 前面的用例可能已把 004 推到出价，先空跑一遍拉回「待补充」
    r = client.post("/api/pricing/requests/QR-2026-004/run", json={}, headers=h)
    assert r.status_code == 200
    assert r.json()["run"]["status"] == "clarified"

    resp = client.post("/api/pricing/dialogue/QR-2026-004/turns",
                       json={"text": "大概八百个律师用，标准配置，预算 60 万，明年二季度上线"},
                       headers=h)
    assert resp.status_code == 200
    evs = _sse_events(resp.text)
    kinds = [e for e, _ in evs]
    assert kinds[0] == "meta"
    assert "delta" in kinds
    assert "extract" in kinds
    assert "run_start" in kinds
    assert "stage" in kinds
    assert "quote" in kinds

    # 口语真的变成了参数
    scale = next((d for e, d in evs if e == "extract" and d["slot"] == "scale"), None)
    assert scale and str(scale["value"]) == "800"

    # 归位：这次抽取的 LLM 调用挂在本轮 run 上，溯源徽标才有数据
    rid = next(d["run_id"] for e, d in evs if e == "run_start")
    det = client.get(f"/api/pricing/runs/{rid}").json()
    assert "decompose" in [c["node"] for c in det["llm_calls"]]
    # 闭环完成：状态从「待补充」变成「已出价」
    assert det["run"]["status"] == "quoted"


def test_对已出价需求对话走解读(client):
    h = _token(client)
    resp = client.post("/api/pricing/dialogue/QR-2026-001/turns",
                       json={"text": "这个报价怎么算出来的？"}, headers=h)
    assert resp.status_code == 200
    kinds = [e for e, _ in _sse_events(resp.text)]
    assert kinds[0] == "meta"
    assert "delta" in kinds
    assert "quote" in kinds


# ---------------------------------------------------------------- 驳回重跑

def test_驳回意见抽取为约束(client):
    h = _token(client)
    reqs = client.get("/api/pricing/overview").json()["requests"]
    rid = next(r["latest"]["run_id"] for r in reqs if r["latest"])
    r = client.post(f"/api/pricing/runs/{rid}/rerun",
                    json={"comment": "客户说总价必须控制在 80 万以内，巡检那块先不做了"},
                    headers=h)
    assert r.status_code == 200
    d = r.json()
    assert d["constraints"]["max_price"] == 80.0
    assert isinstance(d["constraints"]["drop_modules"], list)
    assert d["constraint_source"] in ("llm", "offline")
    # 意见进了下一轮 answers，留痕可查
    assert (d["answers"].get("_constraints") or {}).get("max_price") == 80.0


# ---------------------------------------------------------------- 缩范围

def test_缩范围至多一轮(conn):
    rules = pa.load_rules(conn)
    rules["R-THR-03"]["params"]["auto_shrink"] = 1
    r = pa.run(conn, _req(conn, "QR-2026-003"), rules=rules)
    # 数「执行缩范围」的次数；缩完仍不足时还会有一个 escalated 的 revise 终止节点，不算砍
    shrinks = [n for n in r["trace"] if n["key"] == "revise"
               and ((n.get("detail") or {}).get("action")) == "shrink"]
    assert len(shrinks) <= 1  # 至多砍一次范围，绝不无限循环
    if r["status"] == "quoted":
        assert r["quote"]["margin"] >= 0.15 - 1e-9


# ---------------------------------------------------------------- 回归与审计

def test_改前回归对比结构(client):
    h = _token(client)
    r = client.post("/api/pricing/rules/regress",
                    json={"rules": [{"code": "R-COST-01", "params": {"markup": 0.5}}]},
                    headers=h)
    assert r.status_code == 200
    d = r.json()
    assert d["count"] >= 1
    it = d["items"][0]
    for k in ("run_id", "client", "before", "after", "delta_pct", "status_changed"):
        assert k in it


def test_llm审计可查(client):
    r = client.get("/api/pricing/llm-calls")
    assert r.status_code == 200
    d = r.json()
    assert d["total"] >= 1
    assert any(c["node"] == "decompose" for c in d["items"])
