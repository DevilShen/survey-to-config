"""v4 配置推导的接口测试：分流、模板、规则目录、冲突目录。"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
FIX = json.loads((Path(__file__).parent / "fixtures" / "sample_project.json").read_text(encoding="utf-8"))


def test_旧入参仍走v3():
    r = client.post("/api/config/derive", json={"answers": {
        "Q3": 3, "Q7": "m", "Q12": "subtree", "Q18": 5000, "Q21": False, "Q25": "fin"}})
    assert r.status_code == 200
    body = r.json()
    assert body["rule_version"].startswith("rule_v3")
    assert any(c["k"] == "approval_nodes" for c in body["configs"])


def test_新入参走v4并产出81条():
    r = client.post("/api/config/derive", json={"answers": FIX["answers"]})
    assert r.status_code == 200
    body = r.json()
    assert body["rule_version"] == "rule_v4.0"
    assert len(body["configs"]) == 81
    assert body["stats"]["total"] == 81


def test_同一份答案接口两次结果一致():
    a = client.post("/api/config/derive", json={"answers": FIX["answers"]}).json()
    b = client.post("/api/config/derive", json={"answers": FIX["answers"]}).json()
    assert a["output_hash"] == b["output_hash"]


def test_问卷模板105项20部分():
    body = client.get("/api/config/v4/template").json()
    assert len(body["sections"]) == 20
    assert len(body["questions"]) == 105
    assert body["meta"]["questions"] == 95


def test_规则目录按分组过滤():
    allr = client.get("/api/config/v4/rules").json()
    assert allr["total"] >= 75
    sla = client.get("/api/config/v4/rules?group=sla").json()
    assert sla["total"] < allr["total"]
    assert all(r["group"] == "sla" for r in sla["rules"])


def test_冲突目录十四条():
    body = client.get("/api/config/v4/conflicts").json()
    assert [x["id"] for x in body["items"]] == [f"A{i}" for i in range(1, 15)]


def test_配置项目录81条():
    body = client.get("/api/config/v4/items").json()
    assert body["total"] == 81
    assert body["items"][0]["code"] == "C01"
    assert body["items"][-1]["code"] == "C81"


def test_缺答案不返回500():
    r = client.post("/api/config/derive", json={"template": "survey_96", "answers": {}})
    assert r.status_code == 200
    assert len(r.json()["configs"]) == 81


# ---------------------------------------------------------------- 流式推导

def _sse(body: str) -> list[dict]:
    """把 SSE 文本切成事件列表，顺便验证帧格式。"""
    out = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        ev, data = None, ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        assert ev and data, f"坏帧: {block!r}"
        out.append({"event": ev, **json.loads(data)})
    return out


def test_流式返回12步加done():
    r = client.post("/api/config/derive/stream",
                    json={"template": "survey_96", "answers": FIX["answers"]})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    evs = _sse(r.text)
    assert [e["event"] for e in evs].count("step") == 12
    assert evs[-1]["event"] == "done"
    assert [e["n"] for e in evs[:-1]] == list(range(1, 13))
    assert all(e["total"] == 12 for e in evs[:-1])


def test_流式每一步带真实数字():
    evs = _sse(client.post("/api/config/derive/stream",
                           json={"template": "survey_96",
                                 "answers": FIX["answers"]}).text)
    # 耗时单调不减，且第一步之后就大于 0
    ms = [e["ms"] for e in evs[:-1]]
    assert ms == sorted(ms)
    # 7 个分组步骤的规则数之和 = 规则目录总数
    G = ("org", "catalog", "flow", "sla", "approval", "fee", "priority")
    assert len([e for e in evs[:-1] if e["key"] in G]) == 7
    assert all(len(e["codes"]) > 0 for e in evs[:-1] if e["key"] in ("org", "catalog"))
    # 对账 / 冲突 / 状态机 / 指纹 各一条，key 不重名
    keys = [e["key"] for e in evs[:-1]]
    assert keys.count("fill") == 1 and keys.count("statemachine") == 1
    assert len(set(keys)) == 12


def test_流式结果与一次性推导完全同源():
    stream = _sse(client.post("/api/config/derive/stream",
                              json={"template": "survey_96", "answers": FIX["answers"]}).text)
    once = client.post("/api/config/derive", json={"template": "survey_96",
                                                   "answers": FIX["answers"]}).json()
    got = stream[-1]["result"]
    assert got["output_hash"] == once["output_hash"]
    assert got["configs"] == once["configs"]
    assert got["issues"] == once["issues"]


def test_流式对账步骤说明81条缺口():
    evs = _sse(client.post("/api/config/derive/stream",
                           json={"template": "survey_96", "answers": {}}).text)
    fill = [e for e in evs[:-1] if e["key"] == "fill"][0]
    assert "81 条" in fill["detail"] or "81" in fill["detail"]
    # 空答案时补齐的是 C11–C13 这三条业态相关配置
    assert set(fill["codes"]) == {"C11", "C12", "C13"}
