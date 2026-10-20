"""市场报价 Agent 的规则测试。

断言的不是"接口能返回 200"，而是这套报价必须守住的五条底线：

    1. 确定性   —— 同一份需求 + 同一份权重，必须算出同一个价
    2. 不猜     —— 需求说不清就停下来问，不出价
    3. 不硬凑   —— 置信度不到阈值就调参重检，不拿弱证据去定价
    4. 不亏本   —— 毛利低于下限不放行；授权内自动提价，超授权转特批
    5. 可追责   —— 写操作要权限、要意见、要留痕
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.analytics import pricing_agent as pa
from app.main import app

AS_OF = date(2026, 10, 18)

# 星野数字那条"两句话需求"补齐参数后的答案
ANSWERS_002 = {"scale": "800", "mode": "标准配置", "deadline": "2027-Q2", "budget": "70"}
ANSWERS_004 = {"scale": "200", "mode": "定制交付", "deadline": "2027-Q1",
               "budget": "60", "integration": "OA"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _req(conn, rid):
    return dict(conn.execute("SELECT * FROM quote_requests WHERE id=?", (rid,)).fetchone())


def _any_run(client):
    """overview 按提交时间倒序，最新那条未必跑过推理——取第一条有记录的。"""
    reqs = client.get("/api/pricing/overview").json()["requests"]
    return next(r["latest"]["run_id"] for r in reqs if r["latest"])


def _token(client, email="zhoumin@demo.cn"):
    r = client.post("/api/auth/login", json={"username": email, "password": "demo1234"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


# ---------------------------------------------------------------- 引擎

def test_同一输入必得同一输出(conn):
    a = pa.run(conn, _req(conn, "QR-2026-001"), as_of=AS_OF)
    b = pa.run(conn, _req(conn, "QR-2026-001"), as_of=AS_OF)
    assert a["output_hash"] == b["output_hash"]
    assert a["quote"]["price"] == b["quote"]["price"]
    # 键序不同不影响指纹：这是"可复现"的前提
    c = pa.run(conn, dict(reversed(list(_req(conn, "QR-2026-001").items()))), as_of=AS_OF)
    assert c["output_hash"] == a["output_hash"]


def test_明确度不足时不出价(conn):
    res = pa.run(conn, _req(conn, "QR-2026-004"), as_of=AS_OF)
    assert res["status"] == "clarified"
    assert res["quote"] is None
    assert res["clarity"] < 0.60
    assert any(q["blocking"] for q in res["questions"]), "缺关键参数必须标阻塞"
    # 追问要能解释"为什么问"，否则业务方不会认真答
    for q in res["questions"]:
        assert q["why"] and q["impact"]


def test_补答后从拆解环节重跑(conn):
    res = pa.run(conn, _req(conn, "QR-2026-004"), answers=ANSWERS_004, as_of=AS_OF)
    keys = [n["key"] for n in res["trace"]]
    assert "clarify" not in keys                      # 这一轮不再追问
    assert res["trace"][2]["loop"] == 1               # 拆解节点标记为第 2 轮
    assert res["clarity"] >= 0.90
    assert res["status"] in ("quoted", "below_margin")


def test_置信度不足会调整检索参数(conn):
    """把置信度阈值调到 99%，任何单子都必须进调参回环——测的是机制不是数字。"""
    rules = pa.load_rules(conn)
    rules["R-THR-02"]["params"]["confidence_min"] = 0.99
    res = pa.run(conn, _req(conn, "QR-2026-002"), answers=ANSWERS_002,
                 rules=rules, as_of=AS_OF)
    assert res["retune_rounds"] >= 1
    retunes = [n for n in res["trace"] if n["key"] == "retune"]
    assert len(retunes) == res["retune_rounds"]
    # 调参必须是确定性策略：放开行业 + 降阈值 + 扩召回
    assert retunes[0]["detail"]["to"]["industry_strict"] is False
    assert retunes[0]["detail"]["to"]["sim_min"] < retunes[0]["detail"]["from"]["sim_min"]


def test_调参能救回低置信度需求(conn):
    res = pa.run(conn, _req(conn, "QR-2026-002"), answers=ANSWERS_002, as_of=AS_OF)
    conf_nodes = [n for n in res["trace"] if n["key"] == "confidence"]
    assert conf_nodes[0]["detail"]["score"] < conf_nodes[-1]["detail"]["score"]
    assert res["confidence"] >= 0.70
    assert res["retune_rounds"] >= 1


def test_毛利不足转商务特批(conn):
    res = pa.run(conn, _req(conn, "QR-2026-003"), as_of=AS_OF)
    m = res["margin"]
    assert m["ok"] is False
    assert m["action"] == "escalate"
    assert m["margin"] < m["floor"]
    assert len(m["options"]) == 3, "重新评估必须给出提价 / 缩范围 / 放弃三条路径"
    assert res["status"] == "below_margin"
    assert any("毛利" in r for r in res["quote"]["risks"])


def test_授权内毛利不足自动上调(conn):
    """把自动提价授权放宽，同一单应自动提价到毛利下限，而不是继续卡住。"""
    rules = pa.load_rules(conn)
    rules["R-THR-03"]["params"]["auto_raise_max"] = 0.50
    res = pa.run(conn, _req(conn, "QR-2026-003"), rules=rules, as_of=AS_OF)
    m = res["margin"]
    assert m["action"] == "auto_raise"
    assert m["ok"] is True
    assert m["margin"] >= m["floor"] - 1e-6
    assert res["status"] == "quoted"


def test_权重改变会改变报价(conn):
    base = pa.run(conn, _req(conn, "QR-2026-001"), as_of=AS_OF)
    rules = pa.load_rules(conn)
    rules["R-COST-01"]["weight"] = 0.90     # 成本说了算
    rules["R-MKT-01"]["weight"] = 0.05
    rules["R-CUS-01"]["weight"] = 0.05
    cost_led = pa.run(conn, _req(conn, "QR-2026-001"), rules=rules, as_of=AS_OF)
    assert cost_led["fused_price"]["price"] > base["fused_price"]["price"]

    rules2 = pa.load_rules(conn)
    rules2["R-COST-01"]["weight"] = 0.05
    rules2["R-MKT-01"]["weight"] = 0.90     # 市场说了算
    market_led = pa.run(conn, _req(conn, "QR-2026-001"), rules=rules2, as_of=AS_OF)
    assert market_led["fused_price"]["price"] < cost_led["fused_price"]["price"]


def test_二期项目按增量计人天(conn):
    """一期已交付的基础模块不能重复计人天，否则二期必然报出天价。"""
    res = pa.run(conn, _req(conn, "QR-2026-001"), as_of=AS_OF)
    cand = res["candidates"][0]
    assert res["ctx"]["phased"] is True
    assert 0 < cand["phase_factor"] < 1
    assert cand["adj_days"] < cand["raw_days"]


def test_报价单自洽(conn):
    q = pa.run(conn, _req(conn, "QR-2026-001"), as_of=AS_OF)["quote"]
    assert abs(sum(l["days"] for l in q["lines"]) - q["days"]) < 0.25
    assert q["price"] > q["cost"] > 0
    # 成本在报价单里保留两位小数，毛利率按未舍入的成本算，允许 1e-4 的舍入差
    assert abs((q["price"] - q["cost"]) / q["price"] - q["margin"]) < 1e-4
    assert len(q["tiers"]) == 3
    assert q["valid_until"] > AS_OF.isoformat()


# ---------------------------------------------------------------- 接口与权限

def test_未登录不能执行推理(client):
    r = client.post("/api/pricing/requests/QR-2026-005/run", json={})
    assert r.status_code == 401


def test_只读角色不能复核(client):
    run_id = _any_run(client)
    r = client.post(f"/api/pricing/runs/{run_id}/review",
                    json={"decision": "approved", "comment": "看过了"},
                    headers=_token(client, "wanglin@demo.cn"))
    assert r.status_code == 403


def test_复核必须写意见(client):
    h = _token(client)
    run_id = _any_run(client)
    r = client.post(f"/api/pricing/runs/{run_id}/review",
                    json={"decision": "approved", "comment": "  "}, headers=h)
    assert r.status_code == 400
    assert "意见" in r.json()["detail"]


def test_人工调价不能低于成本(client):
    h = _token(client)
    data = client.post("/api/pricing/requests/QR-2026-005/run", json={}, headers=h).json()
    r = client.post(f"/api/pricing/runs/{data['run']['run_id']}/review",
                    json={"decision": "adjusted", "final_price": 1, "comment": "压价成交"},
                    headers=h)
    assert r.status_code == 400
    assert "成本" in r.json()["detail"]


def test_复核后状态锁定(client):
    h = _token(client)
    data = client.post("/api/pricing/requests/QR-2026-001/run", json={}, headers=h).json()
    run_id = data["run"]["run_id"]
    ok = client.post(f"/api/pricing/runs/{run_id}/review",
                     json={"decision": "approved", "comment": "与历史口径一致，放行"},
                     headers=h)
    assert ok.status_code == 200
    again = client.post(f"/api/pricing/runs/{run_id}/review",
                        json={"decision": "approved", "comment": "再提交一次"}, headers=h)
    assert again.status_code == 409


def test_非法规则参数被拒(client):
    h = _token(client)
    r = client.put("/api/pricing/rules",
                   json={"rules": [{"code": "R-THR-03", "params": {"margin_min": 0.9}}]},
                   headers=h)
    assert r.status_code == 400
    assert "margin_min" in r.json()["detail"]


def test_硬约束不能被禁用(client):
    h = _token(client)
    r = client.put("/api/pricing/rules",
                   json={"rules": [{"code": "R-THR-03", "enabled": False}]}, headers=h)
    assert r.status_code == 400
    assert "硬约束" in r.json()["detail"]


def test_定价规则不能少于两条(client):
    h = _token(client)
    r = client.put("/api/pricing/rules",
                   json={"rules": [{"code": "R-MKT-01", "enabled": False},
                                   {"code": "R-CUS-01", "enabled": False}]}, headers=h)
    assert r.status_code == 400
    assert "两条" in r.json()["detail"]


def test_保存规则生成新版本并留痕(client):
    h = _token(client)
    before = client.get("/api/pricing/rules").json()["version"]
    r = client.put("/api/pricing/rules",
                   json={"label": "测试：市场权重上调",
                         "rules": [{"code": "R-MKT-01", "weight": 0.55}]}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["version"] != before
    assert body["changed"] == 1

    v = client.get("/api/pricing/rules").json()
    assert v["version"] == body["version"]
    assert any(x["version"] == body["version"] and x["active"] for x in v["versions"])
    audit = client.get("/api/audit").json()
    assert any(a["action"] == "pricing.rules.save" for a in audit)


def test_数组参数按下标保存(client):
    """价格带是 [下限, 上限]，前端按 "price_band.0" 提交会变成 {"0":..,"1":..}。

    这里守的是一个真实踩过的坑：后端曾经用 dict(list) 去合并，
    直接抛 TypeError → 500，界面上表现成"改价格带就崩"。
    """
    h = _token(client)
    r = client.put("/api/pricing/rules",
                   json={"label": "测试：改价格带",
                         "rules": [{"code": "R-MKT-01",
                                    "params": {"price_band": {"0": 0.8, "1": 1.4},
                                               "budget_guard": False}}]}, headers=h)
    assert r.status_code == 200, r.text
    mkt = [x for x in client.get("/api/pricing/rules").json()["rules"]
           if x["code"] == "R-MKT-01"][0]
    assert mkt["params"]["price_band"] == [0.8, 1.4]
    assert isinstance(mkt["params"]["price_band"], list)  # 不能被降级成字典
    assert mkt["params"]["budget_guard"] is False

    # 越界下标要报错，而不是静默丢弃
    bad = client.put("/api/pricing/rules",
                     json={"rules": [{"code": "R-MKT-01",
                                      "params": {"price_band": {"5": 9.9}}}]}, headers=h)
    assert bad.status_code == 400

    # 还原，避免影响后续用例的价格
    client.put("/api/pricing/rules",
               json={"label": "测试：还原价格带",
                     "rules": [{"code": "R-MKT-01",
                                "params": {"price_band": {"0": 0.75, "1": 1.35},
                                           "budget_guard": True}}]}, headers=h)
    mkt = [x for x in client.get("/api/pricing/rules").json()["rules"]
           if x["code"] == "R-MKT-01"][0]
    assert mkt["params"]["price_band"] == [0.75, 1.35]


def test_样本不足时市场规则降权(client):
    """可计价样本少于 min_samples 时，市场规则照常出价但要被削权。

    为什么不直接弃权：弃权会把权重全让给成本加成，报价系统性偏高，
    而且业务方看到"三规则"却只有两条在算，反而更困惑。
    """
    h = _token(client)
    r = client.post("/api/pricing/requests/QR-2026-003/run", json={}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    fus = [n for n in body["trace"] if n["key"] == "fusion"][0]["detail"]
    mkt = [p for p in fus["parts"] if p["rule"] == "R-MKT-01"][0]
    assert mkt["cut"] < 1, "样本 1 条 < min_samples 3，必须削权"
    assert mkt["eff_weight"] < mkt["weight"]
    assert "折算" in fus["note"]


def test_影子试算不落库(client):
    h = _token(client)
    run_id = _any_run(client)
    n_before = len(client.get("/api/pricing/quotes").json()["items"])
    r = client.post("/api/pricing/rules/simulate",
                    json={"run_id": run_id,
                          "rules": [{"code": "R-COST-01", "weight": 0.9}]})
    assert r.status_code == 200
    body = r.json()
    assert body["before"]["price"] and body["after"]["price"]
    assert body["delta"]["price"] != 0
    assert len(client.get("/api/pricing/quotes").json()["items"]) == n_before


def test_规则权重页与报价页数据一致(client):
    ov = client.get("/api/pricing/overview").json()
    rules = client.get("/api/pricing/rules").json()
    assert ov["version"] == rules["version"]
    assert len(ov["stages"]) == len({s["key"] for s in ov["stages"]}) == 18
    assert len(ov["edges"]) >= 18
    codes = {r["code"] for r in rules["rules"]}
    assert {"R-MKT-01", "R-CUS-01", "R-COST-01"} <= codes
