"""版本方向决策。

把"咨询多"拆成四类，再决定是改引导、改入口、改概念模型，还是补能力。
只用绝对数会误导：报表中心咨询量最高，但使用率最低——那是入口问题，不是需求旺盛。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from ..db import rows, scalar

CATS = ("A", "B", "C", "D")


def problem_matrix(conn: sqlite3.Connection) -> list[dict]:
    counts = {}
    for r in rows(conn, """SELECT module, category, COUNT(*) AS n FROM tickets
            WHERE category IN ('A','B','C','D') GROUP BY module, category"""):
        counts.setdefault(r["module"], {c: 0 for c in CATS})[r["category"]] = r["n"]

    usage = {r["module"]: r for r in rows(conn, "SELECT * FROM module_usage")}
    avg_use = (sum(u["usage_rate"] for u in usage.values()) / len(usage)) if usage else 0

    out = []
    for module, c in counts.items():
        total = sum(c.values()) or 1
        b_share = c["B"] / total
        c_share = c["C"] / total
        a_share = c["A"] / total
        if b_share >= 0.60:
            flag, tone = f"B {b_share * 100:.0f}% → 入口问题", "blue"
        elif c_share >= 0.40:
            flag, tone = f"C {c_share * 100:.0f}% → 概念模型", "red"
        elif a_share >= 0.60:
            flag, tone = f"A {a_share * 100:.0f}% → 引导", "amber"
        else:
            flag, tone = "结构均衡", "gray"
        u = usage.get(module)
        out.append({
            "t": module, "a": c["A"], "b": c["B"], "c": c["C"], "d": c["D"],
            "total": total,
            "use": u["usage_rate"] if u else None,
            "sat": u["satisfaction"] if u else None,
            "flag": flag, "tone": tone,
            "b_share": round(b_share, 3),
            "c_share": round(c_share, 3),
            "below_avg_usage": bool(u and u["usage_rate"] < avg_use),
        })
    out.sort(key=lambda x: -x["total"])
    return out


def avg_usage(conn: sqlite3.Connection) -> float:
    return round(float(scalar(conn, "SELECT AVG(usage_rate) FROM module_usage", (), 0.0)), 1)


def rice(conn: sqlite3.Connection) -> list[dict]:
    rs = rows(conn, "SELECT * FROM version_candidates")
    out = []
    for r in rs:
        score = (r["reach"] * r["impact"] * r["confidence"]) / (r["effort"] * r["risk"])
        out.append({
            "id": r["id"], "t": r["title"], "module": r["module"],
            "reach": r["reach"], "impact": r["impact"], "conf": r["confidence"],
            "effort": r["effort"], "risk": r["risk"], "ev": r["evidence"],
            "score": round(score, 2),
        })
    out.sort(key=lambda x: -x["score"])
    for i, item in enumerate(out):
        item["rank"] = i + 1
        item["suggest"] = "进本版" if i == 0 else ("候选" if i <= 2 else "下版")
    return out


def hypotheses(conn: sqlite3.Connection, review_days: int = 30) -> list[dict]:
    today = date(2026, 10, 18)
    out = []
    for r in rows(conn, "SELECT * FROM hypotheses ORDER BY created_at DESC"):
        created = date.fromisoformat(r["created_at"])
        age = (today - created).days
        status = r["status"]
        overdue = status == "待验证" and age > review_days
        out.append({
            "id": r["id"], "module": r["module"], "claim": r["claim"],
            "evidence": json.loads(r["evidence"]),
            "falsify": json.loads(r["falsify"]),
            "method": r["method"], "cost": r["cost_days"], "strength": r["strength"],
            "status": "超期未回看" if overdue else status,
            "created": r["created_at"], "age": age, "overdue": overdue,
            "outcome": r["outcome"],
        })
    return out


def pending_hypotheses(conn: sqlite3.Connection) -> dict:
    hs = hypotheses(conn)
    pending = [h for h in hs if h["status"] in ("待验证", "超期未回看")]
    return {
        "total": len(hs),
        "pending": len(pending),
        "overdue": sum(1 for h in pending if h["overdue"]),
    }
