"""数据源治理与 Excel 导入的规则测试。

这里的断言不是"接口能返回 200"，而是《数据源设计 v1.0》里那几条硬规则：
唯一权威源、MCP 必须可追溯、公开数据必须有真实端点、缺数不补 0、导入幂等。
"""
from __future__ import annotations

import pytest

from app import ingest
from app.analytics import sources


def test_every_source_has_owner_and_fallback(conn):
    reg = sources.registry(conn)
    assert len(reg) >= 55
    for s in reg:
        assert s["owner"], s["id"]
        assert s["fallback"], f"{s['id']} 未定义断源降级"
        assert s["type"] in {"api", "mcp", "excel", "open"}


def test_mcp_sources_declare_backing_tool(conn):
    """MCP 不是数据源类型，是访问方式——所以必须有背后系统 + 工具名，否则无法追溯。"""
    for s in sources.registry(conn, "mcp"):
        assert s["mcp_tool"], f"{s['id']} 缺 mcp_tool"
        assert s["vendor"], f"{s['id']} 缺背后系统"


def test_open_sources_have_real_endpoint(conn):
    """公开数据必须能真的探活，否则就是"写在文档里的数据源"。"""
    for s in sources.registry(conn, "open"):
        assert s["endpoint"] and s["endpoint"].startswith("http"), s["id"]


def test_every_module_has_authoritative_source(conn):
    """九个模块都必须有 primary，否则该模块的指标不可信。"""
    mx = sources.matrix(conn)
    assert len(mx["modules"]) == 9
    assert mx["gaps"] == [], f"存在无权威源的模块：{mx['gaps']}"
    for row in mx["modules"]:
        assert any(c["has_primary"] for c in row["cells"]), row["name"]


def test_matrix_counts_match_registry(conn):
    reg = sources.registry(conn)
    mx = sources.matrix(conn)
    total = sum(c["count"] for row in mx["modules"] for c in row["cells"])
    assert total == len(reg)


def test_no_public_source_for_private_modules(conn):
    """需求记录/变更/成本执行本就没有公开数据——这是设计结论，不是遗漏。"""
    for m in ("requirement", "change", "cost"):
        assert sources.registry(conn, "open", m) == []


def test_trust_score_ordering(conn):
    """同样新鲜的 T1 必须高于 T2，T2 高于 T3。"""
    base = {"freq": "day", "stale_days": 0, "error_rate": 0.0, "status": "online"}
    t1 = sources.trust_score({**base, "trust": "T1"})
    t2 = sources.trust_score({**base, "trust": "T2"})
    t3 = sources.trust_score({**base, "trust": "T3"})
    assert t1 > t2 > t3


def test_stale_source_scores_lower(conn):
    fresh = {"trust": "T1", "freq": "day", "stale_days": 0, "error_rate": 0, "status": "online"}
    stale = {**fresh, "stale_days": 30}
    assert sources.trust_score(fresh) > sources.trust_score(stale)


def test_actual_days_has_both_primary_and_validate(conn):
    """实际人天必须是"财务口径出数 + 工时口径对账"，单源不可信。"""
    lin = sources.lineage(conn, "actual_days")
    assert lin["found"]
    roles = {x["link_role"] for x in lin["links"]}
    assert roles == {"primary", "validate"}
    primary = next(x for x in lin["links"] if x["link_role"] == "primary")
    assert "财务" in primary["name"]


GOOD = ("项目编号,人天类别,人天,月份,说明\n"
        "P-2026-018,rework,3,2026-10,导出格式反复调整\n"
        "P-2026-018,wait,2,2026/10,等客户确认单点登录\n").encode("utf-8-sig")

MIXED = ("项目编号,人天类别,人天,月份,说明\n"
         "P-2026-018,rework,3,2026-10,正常行\n"
         "P-0000-000,rework,1,2026-10,项目不存在\n"
         "P-2026-018,乱写,2,2026-10,类别不在枚举\n"
         "P-2026-018,rework,-1,2026-10,负数人天\n"
         "P-2026-018,rework,500,2026-10,单位疑似小时\n"
         "P-2026-018,rework,3,2026-10,正常行\n").encode("utf-8-sig")


def test_preview_rejects_bad_rows_with_line_numbers(conn):
    r = ingest.preview(conn, "mixed.csv", MIXED)
    assert r["fatal"] is None
    assert r["stats"]["ok"] == 1          # 只有第 2 行合格，第 7 行与它重复
    bad_lines = {x["line"] for x in r["rejected"]}
    assert bad_lines == {3, 4, 5, 6, 7}   # 1 是表头，2 正常，7 与 2 重复
    assert any("不存在" in x["msg"] for x in r["rejected"])
    assert any("枚举" in x["msg"] for x in r["rejected"])


def test_preview_rejects_wrong_header(conn):
    r = ingest.preview(conn, "bad.csv", "a,b,c\n1,2,3\n".encode())
    assert "缺少列" in r["fatal"]


def test_commit_is_idempotent(conn):
    first = ingest.commit_effort(conn, "good.csv", GOOD, "测试")
    assert first["ok"] and first["stats"]["ok"] == 2
    second = ingest.commit_effort(conn, "good.csv", GOOD, "测试")
    assert second["ok"] is False and second.get("duplicate") is True

    # 库里只应有一份
    n = conn.execute(
        "SELECT COUNT(*) c FROM effort_records WHERE project_id='P-2026-018' "
        "AND category IN ('rework','wait') AND work_month='2026-10' "
        "AND note IN ('导出格式反复调整','等客户确认单点登录')").fetchone()["c"]
    assert n == 2


def test_commit_does_not_touch_reported_days(conn):
    """导入的是分类工时（校验源），不能改写财务上报人天（权威源）。"""
    before = conn.execute("SELECT reported_days FROM projects WHERE id='P-2026-018'").fetchone()[0]
    ingest.commit_effort(conn, "good2.csv", GOOD.replace(b"3", b"4"), "测试")
    after = conn.execute("SELECT reported_days FROM projects WHERE id='P-2026-018'").fetchone()[0]
    assert before == after


def test_template_has_expected_columns():
    text = ingest.template_csv().decode("utf-8-sig")
    header = text.splitlines()[0]
    for col in ("项目编号", "人天类别", "人天", "月份"):
        assert col in header
