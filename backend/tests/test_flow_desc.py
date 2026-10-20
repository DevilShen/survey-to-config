"""配置说明与分层流程图的回归测试。

对齐《报事报修配置说明书》的两处要求：
1. 81 条配置每条都要有「配置说明」，不能只有一个值；
2. 流程图要分层分对象——泳道（谁负责）× 阶段（什么时候），不是一串箭头。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.analytics import config_engine_v4 as E

ROOT = Path(__file__).resolve().parent
FIX = json.loads((ROOT / "fixtures" / "sample_project.json").read_text(encoding="utf-8"))
ANS = FIX.get("answers", FIX)


@pytest.fixture(scope="module")
def result():
    return E.derive(ANS)


# ---------------------------------------------------------------- 配置说明

def test_每条配置都有说明(result):
    missing = [c["code"] for c in result["configs"] if not (c.get("desc") or "").strip()]
    assert not missing, f"这些配置没有说明：{missing}"


def test_说明不是把配置名重复一遍(result):
    """说明要有信息量：长度够，且不等于配置名。"""
    bad = []
    for c in result["configs"]:
        d = (c.get("desc") or "").strip()
        if len(d) < 10 or d == c["name"] or d.startswith(c["name"] + "，"):
            bad.append((c["code"], d))
    assert not bad, f"说明过短或只是复述名称：{bad}"


def test_说明覆盖到所有分组(result):
    groups = {c["group"] for c in result["configs"] if (c.get("desc") or "").strip()}
    assert groups == {"org", "catalog", "flow", "sla", "approval", "fee", "priority"}


def test_改说明不影响指纹():
    """desc 是文案，不该进指纹——否则改一个字就让历史指纹全变。"""
    a = E.derive(ANS)
    items = E.config_items()
    backup = [(x["code"], x.get("about")) for x in items]
    try:
        for x in items:
            x["about"] = "（测试改文案）"
        # 直接改内存里的产物再算 hash，验证 hash 不看 desc
        fake = json.loads(json.dumps(a, ensure_ascii=False))
        for c in fake["configs"]:
            c["desc"] = "（测试改文案）"
        assert E.output_hash(fake) == a["output_hash"]
    finally:
        for code, about in backup:
            for x in items:
                if x["code"] == code:
                    x["about"] = about


# ---------------------------------------------------------------- 分层流程图

def test_流程图有泳道(result):
    f = result["flow"]
    assert "lanes" in f and len(f["lanes"]) == 5
    keys = [l["k"] for l in f["lanes"]]
    assert keys == ["report", "service", "handler", "approve", "review"]
    assert all(l.get("t") for l in f["lanes"])


def test_每个节点都落在某个泳道和阶段(result):
    f = result["flow"]
    lanes = {l["k"] for l in f["lanes"]}
    for n in f["nodes"]:
        assert n.get("lane") in lanes, f"{n['t']} 没有泳道"
        assert isinstance(n.get("col"), int) and 1 <= n["col"] <= f["cols"], \
            f"{n['t']} 阶段列越界：{n.get('col')}"


def test_责任对象分得清(result):
    """客服、处理人、审批、评价回访要落在不同泳道，不能全挤在一起。"""
    f = result["flow"]
    by = {n["id"]: n["lane"] for n in f["nodes"]}
    assert by["accept_order"] == "service"
    assert by["dispatch"] == "service"
    assert by["processing"] == "handler"
    assert by["to_rate"] == "review"


def test_阶段随流程推进(result):
    """接单 → 处理 → 审核 → 评价，阶段列必须递增。"""
    f = result["flow"]
    by = {n["id"]: n["col"] for n in f["nodes"]}
    assert by["accept"] < by["processing"] < by["audit"] < by["to_rate"]


def test_节点带业务说明(result):
    f = result["flow"]
    no_desc = [n["t"] for n in f["nodes"] if not (n.get("desc") or "").strip()]
    assert not no_desc, f"这些节点没有说明：{no_desc}"
    # 时限要落到说明里，不是空口说"超时"
    acc = [n for n in f["nodes"] if n["id"] == "accept"][0]
    assert "分钟" in acc["desc"] or "小时" in acc["desc"] or "限" not in acc["desc"]


def test_提报端与归档在两端(result):
    f = result["flow"]
    by = {n["id"]: n["col"] for n in f["nodes"]}
    assert by["report_owner"] == 1
    assert by["closed"] == f["cols"]


def test_挂起与审批作为并行分支(result):
    f = result["flow"]
    ids = {n["id"] for n in f["nodes"]}
    ts = {b["t"] for b in f["branches"]}
    assert "hang" in ids and "挂起" in ts
    assert "close_approval" in ids and "关闭审批" in ts
    # 分支节点用虚线，别混进主链路
    assert all(n["kind"] == "branch" for n in f["nodes"] if n["id"] in ("hang", "fee"))


def test_既没勾审核节点也不要审核时不画审核():
    """第 25 题没勾「审核中」且第 27 题说不需要审核 → 主链路不该有审核节点。"""
    ans = dict(ANS)
    ans["Q24"] = "custom"          # 标准流程自带 6 节点，测不出差异
    ans["Q25"] = ["accept", "pending", "processing", "to_rate"]
    ans["Q27"] = "none"
    f = E.derive(ans)["flow"]
    state_ids = [n["id"] for n in f["nodes"] if n["kind"] == "state"]
    assert "audit" not in state_ids


def test_第27题要审核时即使没勾节点也补上():
    """第 25 题漏勾「审核中」但第 27 题要求审核 —— 这正是 A8 指的冲突，
    流程图按「业务上确实要审核」画出来，同时在待确认里标出来。"""
    ans = dict(ANS)
    ans["Q24"] = "custom"
    ans["Q25"] = ["accept", "pending", "processing", "to_rate"]
    ans["Q27"] = "need"
    r = E.derive(ans)
    assert "audit" in [n["id"] for n in r["flow"]["nodes"] if n["kind"] == "state"]


# ---------------------------------------------------------------- 模型

def test_支持的模型包含_deepseek_v4_1_flash():
    from app.llm import config_store as C
    ds = [p for p in C.PROVIDERS if p["k"] == "deepseek"][0]
    # 模型名以服务端可用列表为准：deepseek-flash / deepseek-v4-pro
    assert "deepseek-flash" in ds["models"]
    assert "deepseek-v4-pro" in ds["models"]
    # 曾经写错的 deepseek-v4.1-flash 与已下线的 deepseek-chat 都不该再出现在推荐里
    assert "deepseek-v4.1-flash" not in ds["models"]
    assert "deepseek-chat" not in ds["models"]
