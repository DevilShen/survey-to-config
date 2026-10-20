"""成本偏差分解的测试。

五项分解最容易出的问题是"算得挺准，但加起来不等于总偏差"。
这组测试就是在守这条恒等式，以及闭合校验能不能真的抓到脏数据。
"""
import pytest

from app.analytics import variance


def _project(conn, pid):
    return conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()


def test_五项之和等于总偏差(conn):
    for pid in ("P-2026-014", "P-2026-009", "P-2026-021"):
        b = variance.breakdown(conn, _project(conn, pid))
        total_items = sum(i["v"] for i in b["items"])
        assert round(total_items, 2) == pytest.approx(b["total"], abs=0.05), pid


def test_基准项目分解结构正确(conn):
    b = variance.breakdown(conn, _project(conn, "P-2026-014"))
    assert (b["est"], b["act"], b["total"]) == (120.0, 168.0, 48.0)
    assert [i["v"] for i in b["items"]] == [22.0, 14.0, 12.0, 0.0, 0.0]
    assert b["closeRate"] == 0.0
    assert b["gap"] is False


def test_脏数据会被闭合校验抓到(conn):
    """中远新材有 6 人天未分类工时，必须被判为 data_gap 而不是悄悄吸收。"""
    b = variance.breakdown(conn, _project(conn, "P-2026-021"))
    assert b["unexplained"] == 6.0
    assert b["closeRate"] > variance.CLOSURE_THRESHOLD
    assert b["gap"] is True
    assert "data_gap" in b["verdict"]


def test_实际人天取财务上报口径(conn):
    b = variance.breakdown(conn, _project(conn, "P-2026-021"))
    assert b["act"] == 102.0          # 上报口径
    assert b["categorized"] == 96.0   # 分类工时


def test_返工不重复计入效率(conn):
    """②返工 + ③净效率 应等于毛效率损失，不能一项吃两遍。"""
    b = variance.breakdown(conn, _project(conn, "P-2026-014"))
    rework = b["items"][1]["v"]
    net_eff = b["items"][2]["v"]
    assert rework + net_eff == 26.0


def test_组合CPI可复算(conn):
    from app.analytics import kpi

    pf = kpi.portfolio(conn)
    assert pf["ev"] / pf["ac"] == pytest.approx(pf["cpi"], abs=0.001)
    assert 0.85 < pf["cpi"] < 1.0
