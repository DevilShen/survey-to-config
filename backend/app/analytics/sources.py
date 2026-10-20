"""数据源治理：覆盖矩阵、可信度打分、血缘、断源降级。

原则（对应《数据源设计 v1.0》）：
- 通道不是平级的：api=事实、excel=补断点/财务口径、open=参照、mcp=按需取数的手。
- 一个模块必须至少有一个 primary，否则该模块的指标不可信。
- 状态是运行时概念：unconfigured（内网无凭据）必须显式暴露，不能假装 online。
- 缺数就是 null，绝不补 0。
"""

from __future__ import annotations

import sqlite3

from ..db import rows, scalar

TYPES = [
    ("api", "API", "事实主动脉"),
    ("mcp", "MCP", "按需取数"),
    ("excel", "Excel 导入", "断点补录"),
    ("open", "公开数据", "参照标尺"),
]

MODULES = [
    ("market", "01", "市场洞察"),
    ("estimate", "02", "成本评估"),
    ("requirement", "03", "需求记录"),
    ("schedule", "04", "研发排期"),
    ("change", "05", "需求变更"),
    ("cost", "06", "成本执行"),
    ("delivery", "07", "部署实施"),
    ("support", "08", "售后运维"),
    ("feedback", "09", "用户反馈"),
]

# 各频率的"新鲜度窗口"（天）：超过即视为过期
FRESH_WINDOW = {
    "realtime": 0.05, "hour": 0.2, "day": 2, "week": 8,
    "month": 35, "quarter": 100, "year": 400, "on_demand": 3,
}

TRUST_FACTOR = {"T1": 1.0, "T2": 0.7, "T3": 0.4}
TRUST_LABEL = {"T1": "系统权威", "T2": "人工上报", "T3": "外部参照"}


def _freshness(src: dict) -> float:
    days = float(src.get("stale_days") or 0)
    win = FRESH_WINDOW.get(src.get("freq"), 7)
    if days <= win:
        return 1.0
    if days <= win * 2:
        return 0.7
    return 0.4


def trust_score(src: dict, conflicts: int = 0) -> int:
    """可信度打分：40 源级别 + 25 时效 + 20 完整度 + 15 一致性 − 冲突惩罚。"""
    tier = TRUST_FACTOR.get(src.get("trust"), 0.4)
    err = float(src.get("error_rate") or 0)
    completeness = max(0.0, 1.0 - min(1.0, err * 6))
    consistency = 0.6 if conflicts else 1.0
    if src.get("status") == "degraded":
        consistency = min(consistency, 0.6)
    score = 40 * tier + 25 * _freshness(src) + 20 * completeness + 15 * consistency
    return int(max(0, min(100, round(score - min(30, conflicts * 5)))))


def _decorate(conn: sqlite3.Connection, s: sqlite3.Row) -> dict:
    d = dict(s)
    n_conf = scalar(conn,
                    "SELECT COUNT(*) FROM source_conflicts WHERE source_a=? OR source_b=?",
                    (d["id"], d["id"]), 0)
    d["conflicts"] = int(n_conf)
    d["score"] = trust_score(d, d["conflicts"])
    d["type_label"] = dict((k, v) for k, v, _ in TYPES).get(d["type"], d["type"])
    d["trust_label"] = TRUST_LABEL.get(d["trust"], d["trust"])
    d["stale"] = float(d.get("stale_days") or 0) > FRESH_WINDOW.get(d.get("freq"), 7)
    d["fields_list"] = [x.strip() for x in (d.get("fields") or "").split(",") if x.strip()]
    return d


def registry(conn: sqlite3.Connection, type_: str | None = None,
             module: str | None = None) -> list[dict]:
    sql = "SELECT * FROM data_sources WHERE 1=1"
    args: list = []
    if type_:
        sql += " AND type=?"
        args.append(type_)
    if module:
        sql += " AND module_key=?"
        args.append(module)
    sql += " ORDER BY order_no"
    return [_decorate(conn, s) for s in rows(conn, sql, tuple(args))]


def one(conn: sqlite3.Connection, sid: str) -> dict | None:
    rs = rows(conn, "SELECT * FROM data_sources WHERE id=?", (sid,))
    return _decorate(conn, rs[0]) if rs else None


def matrix(conn: sqlite3.Connection) -> dict:
    """九模块 × 四通道。空格是设计结论（该模块本就无此类数据），不是遗漏。"""
    all_src = registry(conn)
    cells: list[dict] = []
    gaps: list[dict] = []

    for mkey, mno, mname in MODULES:
        row = {"module": mkey, "no": mno, "name": mname, "cells": []}
        for tkey, tlabel, _ in TYPES:
            hit = [s for s in all_src if s["module_key"] == mkey and s["type"] == tkey]
            prim = [s for s in hit if s["role"] == "primary"]
            row["cells"].append({
                "type": tkey,
                "type_label": tlabel,
                "count": len(hit),
                "primary": prim[0]["name"] if prim else None,
                "primary_id": prim[0]["id"] if prim else None,
                "trust": prim[0]["trust"] if prim else (hit[0]["trust"] if hit else None),
                "score": max([s["score"] for s in hit], default=0),
                "has_primary": bool(prim),
            })
        # 无 primary 的模块 = 该模块指标不可信
        if not any(c["has_primary"] for c in row["cells"]):
            gaps.append({"module": mkey, "name": mname, "reason": "无权威源"})
        cells.append(row)

    # 每列（通道）的模块覆盖度
    covers = []
    for tkey, tlabel, trole in TYPES:
        mods = {s["module_key"] for s in all_src if s["type"] == tkey}
        covers.append({"type": tkey, "label": tlabel, "role": trole,
                       "modules": len(mods), "modules_all": len(MODULES)})

    return {"modules": cells, "columns": covers, "gaps": gaps}


def summary(conn: sqlite3.Connection) -> dict:
    all_src = registry(conn)
    total = len(all_src)
    by_type = []
    for tkey, tlabel, trole in TYPES:
        hit = [s for s in all_src if s["type"] == tkey]
        by_type.append({
            "type": tkey, "label": tlabel, "role": trole,
            "count": len(hit),
            "primary": sum(1 for s in hit if s["role"] == "primary"),
            "avg_score": round(sum(s["score"] for s in hit) / len(hit), 1) if hit else 0,
        })

    t1 = sum(1 for s in all_src if s["trust"] == "T1")
    mods_with_primary = len({s["module_key"] for s in all_src if s["role"] == "primary"})
    degraded = [s for s in all_src if s["status"] == "degraded"]
    unconfigured = [s for s in all_src if s["status"] == "unconfigured"]
    stale = [s for s in all_src if s["stale"]]
    manual = [s for s in all_src if s["status"] == "manual"]

    return {
        "total": total,
        "by_type": by_type,
        "t1_share": round(t1 / total * 100, 1) if total else 0,
        "module_coverage": f"{mods_with_primary}/{len(MODULES)}",
        "degraded": [{"id": s["id"], "name": s["name"], "note": s["note"]} for s in degraded],
        "unconfigured": [{"id": s["id"], "name": s["name"], "note": s["note"]}
                         for s in unconfigured],
        "stale": [{"id": s["id"], "name": s["name"], "days": s["stale_days"],
                   "freq": s["freq"]} for s in stale],
        "manual": len(manual),
        "batch_24h": scalar(conn,
            "SELECT COUNT(*) FROM ingest_batches WHERE created_at >= date('now','-1 day')", (), 0),
        "batches": scalar(conn, "SELECT COUNT(*) FROM ingest_batches", (), 0),
        "conflicts": [dict(x) for x in rows(conn,
            "SELECT * FROM source_conflicts ORDER BY detected_at DESC", ())],
    }


def lineage(conn: sqlite3.Connection, metric_id: str) -> dict:
    """指标血缘：这个数字从哪来，谁负责校验，公式里承担哪一段。"""
    links = rows(conn, """
        SELECT ms.metric_id, ms.role AS link_role, ms.formula_part, ds.*
        FROM metric_sources ms JOIN data_sources ds ON ds.id = ms.source_id
        WHERE ms.metric_id=? ORDER BY CASE ms.role WHEN 'primary' THEN 0 ELSE 1 END
    """, (metric_id,))
    if not links:
        return {"metric": metric_id, "links": [], "found": False}
    out = []
    for r in links:
        d = _decorate(conn, r)
        d["link_role"] = r["link_role"]
        d["formula_part"] = r["formula_part"]
        out.append(d)
    return {"metric": metric_id, "links": out, "found": True,
            "primary": next((x["name"] for x in out if x["link_role"] == "primary"), None)}


METRIC_LABEL = {
    "actual_days": ("实际人天", "成本"),
    "cost_actual": ("实际成本", "成本"),
    "rework_share": ("返工占比", "质量"),
    "change_trace_rate": ("变更留痕率", "变更"),
    "availability": ("系统可用性", "运维"),
    "ticket_volume": ("工单量", "售后"),
    "nps": ("NPS 净推荐值", "反馈"),
    "estimate_bias": ("估算偏差", "成本"),
    "capacity": ("可用容量", "排期"),
}


def metric_index(conn: sqlite3.Connection) -> list[dict]:
    """有血缘的指标清单。只列真正能追到源的，不列"应该有但没有"的。"""
    ms = rows(conn, """
        SELECT metric_id,
               SUM(CASE WHEN role='primary' THEN 1 ELSE 0 END) p,
               SUM(CASE WHEN role='validate' THEN 1 ELSE 0 END) v,
               COUNT(*) n
        FROM metric_sources GROUP BY metric_id
    """, ())
    out = []
    for r in ms:
        label, cat = METRIC_LABEL.get(r["metric_id"], (r["metric_id"], "其他"))
        out.append({"id": r["metric_id"], "name": label, "category": cat,
                    "primary": int(r["p"] or 0), "validate": int(r["v"] or 0),
                    "sources": int(r["n"] or 0),
                    "single_source": int(r["n"] or 0) == 1})
    order = {"成本": 0, "变更": 1, "质量": 2, "排期": 3, "运维": 4, "售后": 5, "反馈": 6}
    out.sort(key=lambda x: (order.get(x["category"], 9), x["name"]))
    return out
