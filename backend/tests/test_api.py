"""接口层测试：首屏契约、鉴权、写操作留痕。"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_首屏契约完整(client):
    r = client.get("/api/bootstrap")
    assert r.status_code == 200
    data = r.json()
    for key in ("projects", "overview", "modules", "variance", "lineage",
                "versionPool", "survey", "metrics", "nav"):
        assert key in data, key
    assert len(data["modules"]) == 9
    assert len(data["metrics"]) == 28
    assert data["meta"]["source"] == "api"


def test_关键指标与原始数据一致(client):
    data = client.get("/api/bootstrap").json()
    p14 = next(p for p in data["projects"] if p["id"] == "P-2026-014")
    assert p14["est"] == 120 and p14["act"] == 168
    assert data["variance"]["P-2026-014"]["total"] == 48


def test_未登录不能重算(client):
    r = client.post("/api/projects/P-2026-014/variance/recompute")
    assert r.status_code == 401


def test_登录后重算并写审计(client):
    login = client.post("/api/auth/login", json={"username": "zhoumin@demo.cn", "password": "demo1234"})
    assert login.status_code == 200
    token = login.json()["token"]
    r = client.post("/api/projects/P-2026-014/variance/recompute",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    audit = client.get("/api/audit?limit=1").json()
    assert audit[0]["action"] == "variance.recompute"


def test_只读角色不能写(client):
    token = client.post("/api/auth/login", json={"username": "wanglin@demo.cn", "password": "demo1234"}).json()["token"]
    r = client.post("/api/projects/P-2026-014/variance/recompute",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_配置推导接口确定性(client):
    answers = {"Q3": 3, "Q7": "m", "Q12": "subtree", "Q18": 5000, "Q21": False, "Q25": "fin"}
    a = client.post("/api/config/derive", json={"answers": answers}).json()
    b = client.post("/api/config/derive", json={"answers": dict(reversed(list(answers.items())))}).json()
    assert a["output_hash"] == b["output_hash"]
    assert a["rule_version"] == "rule_v3.4"


def test_汇总级项目不能做五项分解(client):
    r = client.get("/api/projects/P-2026-018/variance")
    assert r.status_code == 409


def test_指标口径字典带版本(client):
    r = client.get("/api/metrics")
    assert r.status_code == 200
    assert r.json()["version"].startswith("kpi_")
