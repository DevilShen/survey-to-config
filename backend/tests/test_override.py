"""人工确认（override）的回归测试。

「待确认」要能在弹窗里直接处理，两条路：
1. 改答案 → 重算（答案变了，冲突从根上消失）；
2. 人工指定配置值 → 落值 + 留痕（问卷里根本没有依据的情况）。

第 2 条必须守住三条：没理由不收、指纹要变、同样的答案+同样的确认要能复现。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.analytics import config_engine_v4 as E

ROOT = Path(__file__).resolve().parent
FIX = json.loads((ROOT / "fixtures" / "sample_project.json").read_text(encoding="utf-8"))
ANS = FIX.get("answers", FIX)

THIN = {"Q2": "residential", "Q20": "repair", "Q24": "custom"}   # 缺失很多 → 一片 tbd


def item(result, code):
    return [c for c in result["configs"] if c["code"] == code][0]


# ---------------------------------------------------------------- 落值

def test_人工确认能把待确认变成已确认():
    before = E.derive(THIN)
    assert item(before, "C01")["st"] == "tbd"

    ov = {"C01": {"v": "示例物业服务有限公司", "why": "客户微信确认", "at": "2026-10-19 19:30:00"}}
    after = E.derive(THIN, ov)
    c = item(after, "C01")
    assert c["st"] == "ok"
    assert c["v"] == "示例物业服务有限公司"
    assert c["override"]["why"] == "客户微信确认"
    assert "人工确认：客户微信确认" in c["note"]
    assert "2026-10-19 19:30:00" in c["note"]


def test_没写理由的人工确认不收():
    """没理由的拍值等于黑箱，交付时说不清，一律忽略。"""
    ov = {"C01": {"v": "示例物业"}}
    r = E.derive(THIN, ov)
    assert item(r, "C01")["st"] == "tbd"
    assert "override" not in item(r, "C01")
    assert r["stats"]["overridden"] == 0


def test_空值的人工确认不收():
    ov = {"C01": {"v": "   ", "why": "随便"}}
    r = E.derive(THIN, ov)
    assert item(r, "C01")["st"] == "tbd"


def test_不存在的配置编号被忽略():
    ov = {"C99": {"v": "x", "why": "y"}, "C01": {"v": "公司A", "why": "客户确认"}}
    r = E.derive(THIN, ov)
    assert len(r["configs"]) == 81
    assert r["stats"]["overridden"] == 1


def test_人工确认只影响指定的那条():
    ov = {"C01": {"v": "公司A", "why": "客户确认"}}
    r = E.derive(THIN, ov)
    assert item(r, "C01")["st"] == "ok"
    assert item(r, "C02")["st"] == "tbd"


# ---------------------------------------------------------------- 指纹

def test_确认值参与输入指纹():
    a = E.derive(ANS)
    b = E.derive(ANS, {"C65": {"v": "30 天", "why": "按 30 天折算"}})
    assert a["input_hash"] != b["input_hash"]
    assert a["output_hash"] != b["output_hash"]


def test_同答案同确认必然同指纹():
    ov = {"C65": {"v": "30 天", "why": "按 30 天折算", "at": "2026-10-19 19:30:00"}}
    a = E.derive(ANS, ov)
    b = E.derive(json.loads(json.dumps(ANS)), json.loads(json.dumps(ov, ensure_ascii=False)))
    assert a["output_hash"] == b["output_hash"]


def test_没有确认时指纹不变():
    """不带 overrides 的老调用必须与以前完全一致，否则历史报告对不上。"""
    r = E.derive(ANS)
    assert "overrides" in r and r["overrides"] == {}
    assert E.answer_hash(E.normalize(ANS)) == r["input_hash"]


# ---------------------------------------------------------------- 流式

def test_流式过程写明人工确认条数():
    ov = {"C65": {"v": "30 天", "why": "按 30 天折算"}}
    evs = list(E.steps(ANS, ov))
    step = [e for e in evs if e.get("key") == "conflict"][0]
    assert "人工确认覆盖 1 条" in step["detail"]
    assert "C65" in step["codes"]


def test_流式与一次性结果同源():
    ov = {"C65": {"v": "30 天", "why": "按 30 天折算"}}
    evs = list(E.steps(ANS, ov))
    streamed = evs[-1]["result"]
    once = E.derive(ANS, ov)
    assert streamed["output_hash"] == once["output_hash"]
    assert streamed["configs"] == once["configs"]


# ---------------------------------------------------------------- 接口

@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


def test_接口接受人工确认(client):
    r = client.post("/api/config/derive", json={
        "template": "survey_96", "answers": THIN,
        "overrides": {"C01": {"v": "公司A", "why": "客户确认", "at": "2026-10-19 19:30:00"}},
    })
    assert r.status_code == 200
    c = [x for x in r.json()["configs"] if x["code"] == "C01"][0]
    assert c["st"] == "ok" and c["v"] == "公司A"


def test_流式接口接受人工确认(client):
    r = client.post("/api/config/derive/stream", json={
        "template": "survey_96", "answers": THIN,
        "overrides": {"C01": {"v": "公司A", "why": "客户确认"}},
    })
    assert r.status_code == 200
    assert "人工确认覆盖" in r.text


def test_接口不传确认时与原来一致(client):
    a = client.post("/api/config/derive", json={"template": "survey_96", "answers": ANS}).json()
    b = E.derive(ANS)
    assert a["output_hash"] == b["output_hash"]
