"""草稿接口（「仅保存」）的回归测试。

语义边界：保存 = 落盘，不推导。所以这里既要验证存得住、读得回、能覆盖、能清，
也要确保它**不会顺手把配置算了**——算不算由用户点「确认计算」决定。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.analytics import config_engine_v4 as E


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean(client):
    client.delete("/api/config/draft")
    yield
    client.delete("/api/config/draft")


ANS = {"Q1": "草稿测试公司", "Q2": ["residential"], "Q24": "custom", "Q26": "no"}
OV = {"C01": {"v": "草稿测试公司", "why": "客户确认", "at": "2026-10-19 19:45:00"}}


def test_没有草稿时返回_exists_false(client):
    r = client.get("/api/config/draft")
    assert r.status_code == 200
    assert r.json() == {"exists": False}


def test_保存后能原样读回(client):
    r = client.post("/api/config/draft", json={"answers": ANS, "overrides": OV})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["answers"] == len(ANS)
    assert body["overrides"] == 1
    assert body["saved_at"]

    got = client.get("/api/config/draft").json()
    assert got["exists"] is True
    assert got["answers"] == ANS
    assert got["overrides"] == OV
    assert got["saved_at"] == body["saved_at"]


def test_再保存会覆盖而不是堆积(client):
    client.post("/api/config/draft", json={"answers": ANS, "overrides": OV})
    client.post("/api/config/draft", json={"answers": {"Q1": "改名了"}, "overrides": {}})
    got = client.get("/api/config/draft").json()
    assert got["answers"] == {"Q1": "改名了"}
    assert got["overrides"] == {}


def test_能清除草稿(client):
    client.post("/api/config/draft", json={"answers": ANS, "overrides": {}})
    assert client.get("/api/config/draft").json()["exists"] is True
    assert client.delete("/api/config/draft").json()["ok"] is True
    assert client.get("/api/config/draft").json()["exists"] is False


def test_保存草稿不做推导(client):
    """草稿接口只写库，不该返回任何配置结果，也不该有推导副作用。"""
    r = client.post("/api/config/draft", json={"answers": ANS, "overrides": OV})
    assert "configs" not in r.json()
    assert "issues" not in r.json()
    # 再推导一次，结果应与"直接拿这份答案推导"完全一致 —— 草稿不参与计算
    derived = client.post("/api/config/derive",
                          json={"template": "survey_96", "answers": ANS,
                                "overrides": OV}).json()
    direct = E.derive(ANS, OV)
    assert derived["output_hash"] == direct["output_hash"]


def test_空答案也能存(client):
    r = client.post("/api/config/draft", json={"answers": {}, "overrides": {}})
    assert r.status_code == 200
    got = client.get("/api/config/draft").json()
    assert got["exists"] is True
    assert got["answers"] == {}
