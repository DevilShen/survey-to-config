"""问卷 docx 导入的回归测试。

样本用一份真实的客户回填问卷（已脱敏）（放在 tests/fixtures 下，已脱敏）：
勾选框 ☑、题干内填空、Word 原生表格、缺题号的第 52 题都在里面，
是这个解析器能碰到的全部形态。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.analytics import survey_import

ROOT = Path(__file__).resolve().parent
DOCX = ROOT / "fixtures" / "survey_filled.docx"


@pytest.fixture(scope="module")
def parsed():
    return survey_import.parse(DOCX)


# ---------------------------------------------------------------- 总体

def test_样例文件存在(parsed):
    assert DOCX.exists(), f"缺少样本：{DOCX}"


def test_解析出九成以上的题(parsed):
    rep = parsed["report"]
    assert rep["total"] == 105
    assert rep["filled"] >= 90, f"只填了 {rep['filled']} 项"
    assert len(parsed["answers"]) == rep["filled"]


def test_没有解析出联系方式的表格(parsed):
    """封面信息表里有填写人与联系方式，导入结果绝不能把它们带进答案。"""
    blob = str(parsed["answers"])
    assert "138****0000" not in blob      # fixture 封面表里的联系方式（原卷已打码）
    assert "张**" not in blob             # 填写人
    assert "填写人" not in blob


# ---------------------------------------------------------------- 关键题

@pytest.mark.parametrize("code,expect", [
    ("Q1", "示例物业服务有限公司"),
    ("Q5", "nosplit"),
    ("Q7", "multi_level"),
    ("Q23", "yes"),
    ("Q24", "custom"),
    ("Q30", "role"),
    ("Q46", "diff"),
    ("Q57", "some_type"),
    ("Q70", "partial"),
    ("Q80", "3"),
    ("Q82", "escalate"),
    ("Q84", "yes"),
    ("Q87", "yes"),
    ("Q98", "equal"),
    ("Q99", "global"),
])
def test_单选题取值(parsed, code, expect):
    assert parsed["answers"].get(code) == expect


@pytest.mark.parametrize("code,expect", [
    ("Q2", ["residential", "office", "apartment"]),
    ("Q20", ["repair", "complaint", "consult", "suggest", "release", "decorate", "clean", "other"]),
    ("Q25", ["accept", "pending", "processing", "to_rate", "to_visit"]),
    ("Q41", ["executor", "owner"]),
    ("Q43", ["by_terminal"]),
    ("Q58", ["node", "hang"]),
    ("Q58b", ["accept", "pending", "processing"]),
    ("Q59", ["l1"]),
    ("Q60", ["before", "after"]),
])
def test_多选题取值(parsed, code, expect):
    assert parsed["answers"].get(code) == expect


def test_时间段题(parsed):
    assert parsed["answers"]["Q4"] == {"start": "08:30", "end": "18:00"}


@pytest.mark.parametrize("code,expect", [
    ("Q52", 1),
    ("Q60b", {"v": 30, "unit": "min"}),
    ("Q66", {"v": 1, "unit": "month"}),
    ("Q68b", {"v": 3, "unit": "day"}),
    ("Q75", {"v": 1, "unit": "day"}),
    ("Q76", {"v": 0.5, "unit": "day"}),
])
def test_数值题取值(parsed, code, expect):
    assert parsed["answers"].get(code) == expect


def test_勾了单位没写数值就留空(parsed):
    """第 38 题客户只勾了「天」没填数字 —— 必须如实留空，交给 A3 冲突。"""
    assert parsed["answers"]["Q38"] == {"v": None, "unit": "day"}


def test_不限时的题不编造数值(parsed):
    assert parsed["answers"]["Q92b"] == {"v": None, "unit": "unlimited"}


def test_纯文字补充题(parsed):
    assert "夜间紧急维修需电话通知值班人员" in parsed["answers"]["Q100"]


# ---------------------------------------------------------------- 表格

def test_项目规模表逐行读出(parsed):
    rows = parsed["answers"]["Q3"]
    assert len(rows) == 8
    assert rows[0]["项目名称"] == "紫琅科技城"
    assert rows[0]["项目户数"] == "248"
    assert all(r.get("所属公司/部门") for r in rows)


def test_节点超时表(parsed):
    rows = parsed["answers"]["Q39"]
    assert [r["节点名称"] for r in rows] == ["接单", "处理"]
    assert rows[0]["超时时长"] == "30分钟"


def test_表头为空也能按列位映射(parsed):
    """第 61 题的表头第二格是空的，只能按列位对到「通知对象」。"""
    rows = parsed["answers"]["Q61"]
    assert rows[0]["预警等级"] == "提醒级"
    assert rows[0]["通知对象（人员 / 角色）"] == "当前处理人"
    assert len(rows) == 4


def test_审批级别表(parsed):
    rows = parsed["answers"]["Q81"]
    assert [r["审批人"] for r in rows] == ["工程经理", "项目经理", "总经理"]


def test_空表格不造假(parsed):
    """第 29 / 45 / 86 / 63b 题客户没填，必须是 skipped，不能出现在 answers 里。"""
    for code in ("Q29", "Q45", "Q86", "Q63b"):
        assert code not in parsed["answers"]
    skipped = {s["code"] for s in parsed["report"]["skipped"]}
    assert {"Q29", "Q45", "Q86", "Q63b"} <= skipped


# ---------------------------------------------------------------- 异常

def test_没勾选的选项不算答案(parsed):
    """第 14/15 题客户一个都没勾，不能默认成「不需要」。"""
    for code in ("Q14", "Q15"):
        assert code not in parsed["answers"]


def test_示例文字不能当答案(parsed):
    """第 88 题只有问卷给的示例，客户没填 —— 取示例就是凭空造答案。"""
    assert "Q88" not in parsed["answers"]


def test_缺题号的题按题干兜底(parsed):
    """原卷「最多允许挂起几次？1次」漏了 52. 题号。"""
    assert parsed["answers"]["Q52"] == 1
    assert any(n["no"] == 52 for n in parsed["report"]["notes"])


# ---------------------------------------------------------------- 接口

@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


def _post(client, name, content):
    return client.post("/api/config/import-survey",
                       files={"file": (name, content,
                                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})


def test_接口返回答案与报告(client):
    r = _post(client, "survey.docx", DOCX.read_bytes())
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["filename"] == "survey.docx"
    assert len(d["answers"]) == d["report"]["filled"]


def test_接口拒收非docx(client):
    r = _post(client, "survey.pdf", b"%PDF-1.4")
    assert r.status_code == 400
    assert "docx" in r.json()["detail"]


def test_接口拒收空文件(client):
    r = _post(client, "survey.docx", b"")
    assert r.status_code == 400


def test_接口拒收坏文件(client):
    r = _post(client, "broken.docx", b"not a real docx at all")
    assert r.status_code == 400
    assert "解析失败" in r.json()["detail"]


def test_导入结果能直接喂给推导引擎(parsed):
    """导入不推导，但导出的答案必须能被引擎吃下——否则等于导了个假答案。"""
    from app.analytics import config_engine_v4
    r = config_engine_v4.derive(parsed["answers"])
    assert len(r["configs"]) == 81
    assert r["stats"]["tbd"] + r["stats"]["confirmed"] + r["stats"]["default"] == 81
