"""大模型配置 + AI 推导的回归测试。

不真的打网络：用假 client 替换 get_client，重点验证
「模型出内容、规则兜底、编号锁死」这三条，以及配置存取与脱敏。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.analytics import config_engine_v4 as E
from app.analytics import llm_derive
from app.llm import config_store
from app.llm.client import ChatResult

ROOT = Path(__file__).resolve().parent
FIX = json.loads((ROOT / "fixtures" / "sample_project.json").read_text(encoding="utf-8"))
ANS = FIX.get("answers", FIX)


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean(client):
    client.delete("/api/llm/config")
    yield
    client.delete("/api/llm/config")


# ---------------------------------------------------------------- 配置存取

def test_初始状态是未配置(client):
    d = client.get("/api/llm/config").json()
    assert d["online"] is False
    assert d["has_key"] is False
    assert d["source"] == "none"
    assert len(d["providers"]) >= 1
    assert any(p["k"] == "deepseek" for p in d["providers"])


def test_保存后生效且key脱敏(client):
    r = client.post("/api/llm/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat", "api_key": "sk-1234567890abcdef", "temperature": 0.2,
    })
    assert r.status_code == 200
    d = r.json()
    assert d["online"] is True and d["has_key"] is True
    assert d["source"] == "db"
    # 明文绝不能回给前端
    assert "sk-1234567890abcdef" not in json.dumps(d, ensure_ascii=False)
    assert d["key_masked"].startswith("sk-1") and d["key_masked"].endswith("cdef")


def test_只改模型名不会把key清掉(client):
    client.post("/api/llm/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat", "api_key": "sk-1234567890abcdef"})
    masked = client.get("/api/llm/config").json()["key_masked"]
    r = client.post("/api/llm/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "model": "deepseek-reasoner", "api_key": masked})
    d = r.json()
    assert d["model"] == "deepseek-reasoner"
    assert d["has_key"] is True           # 传脱敏串 = 用回原 key


def test_缺字段要报错(client):
    for body in ({"base_url": "x", "model": "m"},          # 缺 key
                 {"model": "m", "api_key": "k"},            # 缺 base
                 {"base_url": "x", "api_key": "k"}):        # 缺 model
        assert client.post("/api/llm/config", json=body).status_code == 400


def test_清除配置(client):
    client.post("/api/llm/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat", "api_key": "sk-abc123456789"})
    assert client.delete("/api/llm/config").json()["online"] is False
    assert client.get("/api/llm/config").json()["source"] == "none"


def test_没有key时测试连接给出明确原因(client):
    d = client.post("/api/llm/config/test", json={}).json()
    assert d["ok"] is False
    assert "API Key" in d["error"]


# ---------------------------------------------------------------- AI 推导（假模型）

class FakeClient:
    """模拟 DeepSeek：只回第一组的 2 条，其余组装傻，用来验证兜底。"""
    online = True
    model = "deepseek-chat"

    def __init__(self, items=None, boom=False):
        self.items = items or []
        self.boom = boom
        self.calls = 0

    def chat_json(self, system, user, temperature=None, max_tokens=1600):
        self.calls += 1
        if self.boom:
            raise llm_derive.LLMError("模拟网络错误")
        # 从 prompt 里认出这一组有哪些 code，只回前两个
        codes = re.findall(r"^- (C\d{2}) ", user, flags=re.M)
        picked = codes[:2] if codes else []
        items = [{"code": c, "v": f"模型填的{c}", "note": f"依据 {c} 对应题目"} for c in picked]
        return ChatResult(data={"items": items}, text=json.dumps({"items": items}),
                          tokens_in=120, tokens_out=60, latency_ms=8.0, model=self.model)


def _run_with(monkeypatch, fake):
    monkeypatch.setattr(llm_derive, "get_client", lambda conn=None: fake)
    return llm_derive.derive(ANS)


def test_模型出内容_规则兜底(monkeypatch):
    fake = FakeClient()
    r = _run_with(monkeypatch, fake)
    assert r["engine"] == "llm"
    assert len(r["configs"]) == 81
    llm_items = [c for c in r["configs"] if c.get("by") == "llm"]
    rule_items = [c for c in r["configs"] if c.get("by") == "rule"]
    assert len(llm_items) > 0, "模型一条都没用上"
    assert len(rule_items) > 0, "兜底没生效"
    assert len(llm_items) + len(rule_items) == 81
    # 模型给的值得在最终结果里
    one = llm_items[0]
    assert one["v"].startswith("模型填的")
    assert one["st"] in ("ok", "tbd", "def")


def test_每组都调用了模型(monkeypatch):
    fake = FakeClient()
    _run_with(monkeypatch, fake)
    # 分批后调用次数大于组数：7 组里 sla 15 条、priority 21 条都要拆批
    assert fake.calls >= len(E.GROUPS), f"只调了 {fake.calls} 次"


def test_长组会分批且每批不超过上限(monkeypatch):
    """21 条一次要，输出的 JSON 会被 max_tokens 截断，整组回落。
    所以必须拆批：一批最多 BATCH 条，批失败只影响这一批。"""
    fake = FakeClient()
    sizes = []
    orig = fake.chat_json

    def spy(system, user, temperature=None, max_tokens=4000):
        sizes.append(len(re.findall(r"^- (C\d{2}) ", user, flags=re.M)))
        return orig(system, user, temperature=temperature, max_tokens=max_tokens)

    fake.chat_json = spy
    _run_with(monkeypatch, fake)
    assert max(sizes) <= llm_derive.BATCH, f"有一批要了 {max(sizes)} 条"
    assert len(sizes) > len(E.GROUPS), "长组没有被拆批"


def test_模型漏项时用规则值补齐(monkeypatch):
    r = _run_with(monkeypatch, FakeClient())
    base = E.derive(ANS)
    base_by = {c["code"]: c for c in base["configs"]}
    gaps = [c for c in r["configs"] if c.get("by") == "rule"]
    for c in gaps:
        assert c["v"] == base_by[c["code"]]["v"], f"{c['code']} 兜底值不对"


def test_模型编造编号会被丢弃(monkeypatch):
    class Liar(FakeClient):
        def chat_json(self, system, user, temperature=None, max_tokens=1600):
            items = [{"code": "C99", "v": "瞎编的"},
                     {"code": "C01", "v": "合法值"}]
            return ChatResult(data={"items": items}, text="{}", tokens_in=10, tokens_out=5,
                              latency_ms=1.0, model="deepseek-chat")

    r = _run_with(monkeypatch, Liar())
    codes = {c["code"] for c in r["configs"]}
    assert "C99" not in codes
    assert len(codes) == 81


def test_模型挂掉时整批回落规则(monkeypatch):
    r = _run_with(monkeypatch, FakeClient(boom=True))
    assert r["engine"] == "llm"
    assert all(c.get("by") == "rule" for c in r["configs"])
    assert r["llm"]["failed"] >= len(E.GROUPS)


def test_统计里带tokens与失败数(monkeypatch):
    r = _run_with(monkeypatch, FakeClient())
    assert r["llm"]["model"] == "deepseek-chat"
    assert r["llm"]["ok"] == r["llm"]["calls"] >= len(E.GROUPS)
    assert r["llm"]["tokens_in"] > 0 and r["llm"]["tokens_out"] > 0


def test_流式事件逐组回显(monkeypatch):
    monkeypatch.setattr(llm_derive, "get_client", lambda conn=None: FakeClient())
    evs = list(llm_derive.steps(ANS))
    steps = [e for e in evs if e["type"] == "step"]
    assert len(steps) == len(E.GROUPS) + 4   # 读取 + 7 组 + 冲突 + 状态机 + 指纹
    group_steps = [e for e in steps if str(e["key"]).startswith("llm_")]
    assert len(group_steps) == len(E.GROUPS)
    assert all("大模型" in e["title"] for e in group_steps)
    assert all(e["llm"]["model"] for e in group_steps)
    assert evs[-1]["type"] == "done"
    assert evs[-1]["result"]["engine"] == "llm"


# ---------------------------------------------------------------- 接口（无 key）

def test_没配模型时接口给error并回落规则(client):
    r = client.post("/api/config/derive/llm-stream",
                    json={"template": "survey_96", "answers": {"Q1": "测试公司"}})
    assert r.status_code == 200
    assert "event: error" in r.text
    assert "尚未配置大模型" in r.text
    # 仍然给出可用结果（engine=rule），不至于让人卡住
    assert '"engine": "rule"' in r.text


# ---------------------------------------------------------------- 模型列表探测
# 背景：DeepSeek 现在只认 deepseek-flash / deepseek-v4-pro，
# deepseek-chat 与页面写死的 deepseek-v4.1-flash 都会 400。
# 所以「模型名」不能只在页面写死，得能问服务端。

def test_没配key时拉不到模型列表(client):
    d = client.get("/api/llm/models").json()
    assert d["ok"] is False
    assert d["models"] == []
    assert "API Key" in d["error"]


def test_能拉到服务端真实列表并标出当前模型名是否有效(client, monkeypatch):
    client.post("/api/llm/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4.1-flash", "api_key": "sk-1234567890abcdef",
    })

    class Fake:
        def __init__(self, **kw):
            pass

        def list_models(self, timeout=15.0):
            return ["deepseek-flash", "deepseek-v4-pro"]

    monkeypatch.setattr(config_store, "OpenAICompat", Fake)
    d = client.get("/api/llm/models").json()
    assert d["ok"] is True
    assert d["models"] == ["deepseek-flash", "deepseek-v4-pro"]
    assert d["base_url"] == "https://api.deepseek.com"
    # 保存的是个不存在的名字，必须如实标出来，不能装作没事
    assert d["saved_model"] == "deepseek-v4.1-flash"
    assert d["saved_model_ok"] is False


def test_探测失败时把服务端原话带回来(client, monkeypatch):
    client.post("/api/llm/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash", "api_key": "sk-1234567890abcdef",
    })

    class Bad:
        def __init__(self, **kw):
            pass

        def list_models(self, timeout=15.0):
            raise RuntimeError("HTTP 401: Authentication Fails")

    monkeypatch.setattr(config_store, "OpenAICompat", Bad)
    d = client.get("/api/llm/models").json()
    assert d["ok"] is False
    assert d["models"] == []
    assert "401" in d["error"]


def test_推荐模型名与DeepSeek实际可用列表一致():
    ds = [p for p in config_store.PROVIDERS if p["k"] == "deepseek"][0]
    assert ds["models"] == ["deepseek-flash", "deepseek-v4-pro"]
    assert config_store.PROVIDERS[0]["base"] == "https://api.deepseek.com"


def test_默认模型是deepseek_flash():
    from app.llm.client import DEFAULTS
    assert DEFAULTS["model"] == "deepseek-flash"


# ---------------------------------------------------------------- 非 ASCII 拦截
# 起因：key 从聊天窗口复制时混进中文/零宽字符，httpx 写 header 报
# `'ascii' codec can't encode characters in position 7-10`——看不懂，也查不出。
# 现在提前拦：说清是哪个字段、第几位、什么字符。

def test_含中文的key不许入库(client):
    r = client.post("/api/llm/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash", "api_key": "sk-中文测试abcd",
    })
    assert r.status_code == 400
    msg = r.json()["detail"]
    assert "非 ASCII" in msg
    assert "API Key" in msg


def test_含零宽字符的key也不许入库(client):
    r = client.post("/api/llm/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash", "api_key": "sk-\u200b\u200b\u200babcd",
    })
    assert r.status_code == 400
    assert "非 ASCII" in r.json()["detail"]


def test_中文模型名被拒绝(client):
    r = client.post("/api/llm/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "model": "深度求索", "api_key": "sk-abcdefgh1234",
    })
    assert r.status_code == 400
    assert "模型名" in r.json()["detail"]


def test_测试连接时脏key给出可读原因(client):
    r = client.post("/api/llm/config/test", json={
        "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
        "api_key": "sk-中文测试",
    })
    d = r.json()
    assert d["ok"] is False
    # 不能再是 ascii codec 那种原始报错
    assert "ascii" not in d["error"]
    assert "非 ASCII" in d["error"]
    assert "第 4 位" in d["error"]


def test_正常key依然能保存(client):
    r = client.post("/api/llm/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash", "api_key": "sk-abcdef1234567890",
    })
    assert r.status_code == 200
    assert r.json()["has_key"] is True
