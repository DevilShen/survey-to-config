"""需求演进链分析。

需求不是一个"状态"，而是一条线：rev 1.0 → 2.0 → 3.0，每次返工都有诱因。
状态机回答不了"为什么改三次"，演进链可以。
"""
from __future__ import annotations

import sqlite3

from ..db import rows, scalar

DELIVERED = ("已验收", "已上线")
CAUSE_NAMES = {
    "C1": "需求不完备", "C2": "理解偏差", "C3": "验收标准缺失", "C4": "客户业务变化",
    "C5": "技术方案错误", "C6": "依赖系统变更", "C7": "有意简化", "C8": "协作断点",
}


def rework_stats(conn: sqlite3.Connection) -> dict:
    delivered = scalar(conn, f"""SELECT COUNT(*) FROM requirements
        WHERE status IN {DELIVERED}""", (), 0)
    reworked = scalar(conn, f"""SELECT COUNT(DISTINCT q.id) FROM requirements q
        JOIN requirement_revisions r ON r.requirement_id = q.id
        WHERE q.status IN {DELIVERED} AND r.rev_no > 1 AND r.planned_phase = 0""", (), 0)
    events = scalar(conn, """SELECT COUNT(*) FROM requirement_revisions
        WHERE rev_no > 1 AND planned_phase = 0""", (), 0)
    first_pass = delivered - reworked
    return {
        "delivered": delivered,
        "reworked": reworked,
        "events": events,
        "first_pass": first_pass,
        "first_pass_rate": round(first_pass / delivered * 100, 1) if delivered else 0.0,
        "rework_rate": round(reworked / delivered * 100, 1) if delivered else 0.0,
        "avg_rework_times": round(events / reworked, 2) if reworked else 0.0,
    }


def cause_distribution(conn: sqlite3.Connection) -> list[dict]:
    rs = rows(conn, """SELECT cause_code AS c, COUNT(*) AS n FROM requirement_revisions
        WHERE rev_no > 1 AND planned_phase = 0 AND cause_code IS NOT NULL
        GROUP BY cause_code ORDER BY n DESC""")
    total = sum(r["n"] for r in rs) or 1
    return [
        {"c": f"{r['c']} {CAUSE_NAMES.get(r['c'], '')}", "code": r["c"],
         "n": r["n"], "p": round(r["n"] / total, 3)}
        for r in rs
    ]


def requirement_row(conn: sqlite3.Connection, req_id: str):
    return conn.execute("SELECT * FROM requirements WHERE id=?", (req_id,)).fetchone()


def revisions(conn: sqlite3.Connection, req_id: str) -> list[sqlite3.Row]:
    return rows(conn, """SELECT * FROM requirement_revisions
        WHERE requirement_id=? ORDER BY rev_no""", (req_id,))


def knowledge_card(conn: sqlite3.Connection, req_id: str):
    return conn.execute(
        "SELECT * FROM knowledge_cards WHERE requirement_id=? ORDER BY created_at DESC LIMIT 1",
        (req_id,)).fetchone()


def chain(conn: sqlite3.Connection, req_id: str) -> dict | None:
    req = requirement_row(conn, req_id)
    if req is None:
        return None
    revs = revisions(conn, req_id)
    card = knowledge_card(conn, req_id)
    return {
        "id": req["id"],
        "title": req["title"],
        "owner": req["proposer"],
        "est": round(sum(r["est_days"] for r in revs), 1),
        "act": round(sum(r["act_days"] for r in revs), 1),
        "count": len(revs),
        "span": _span(revs),
        "revs": [
            {
                "no": f"rev {r['rev_no']}.0",
                "desc": r["description"],
                "ver": r["version_delivered"] or "未交付",
                "est": r["est_days"],
                "act": r["act_days"],
                "cause": (f"{r['cause_code']} {CAUSE_NAMES.get(r['cause_code'], '')}"
                          if r["cause_code"] else None),
                "date": (r["delivered_at"] or "")[:7],
                "planned": bool(r["planned_phase"]),
            }
            for r in revs
        ],
        "lesson": _lesson(card),
    }


def _span(revs) -> str:
    dates = [r["delivered_at"] for r in revs if r["delivered_at"]]
    if len(dates) < 2:
        return "本月内"
    first, last = min(dates), max(dates)
    months = (int(last[:4]) - int(first[:4])) * 12 + (int(last[5:7]) - int(first[5:7]))
    return f"{max(months, 1)} 个月"


def _lesson(card) -> dict | None:
    if card is None:
        return None
    return {
        "trigger": card["trigger_cond"],
        "phenomenon": card["phenomenon"],
        "root": card["root_cause"],
        "action": card["action"],
        "verify": card["verify_criterion"],
        "hit": card["hit_count"],
        "adopt": card["adopt_count"],
        "eff": card["effective_count"],
    }


def top_requirements(conn: sqlite3.Connection, limit: int = 8) -> list[dict]:
    rs = rows(conn, """SELECT q.*, COUNT(r.id) AS revs FROM requirements q
        JOIN requirement_revisions r ON r.requirement_id = q.id
        GROUP BY q.id ORDER BY q.created_at DESC LIMIT ?""", (limit,))
    return [
        {
            "id": r["id"], "t": r["title"], "by": r["proposer"], "done": r["fields_completed"],
            "size": r["size"], "state": r["status"], "rev": r["revs"],
        }
        for r in rs
    ]


def lineage_total(conn: sqlite3.Connection) -> int:
    return scalar(conn, "SELECT COUNT(*) FROM knowledge_cards WHERE status='active'", (), 0)


def recall_stats(conn: sqlite3.Connection) -> dict:
    return {
        "cards": scalar(conn, "SELECT COUNT(*) FROM knowledge_cards", (), 0),
        "active": scalar(conn, "SELECT COUNT(*) FROM knowledge_cards WHERE status='active'", (), 0),
        "hit": scalar(conn, "SELECT SUM(hit_count) FROM knowledge_cards", (), 0),
        "adopt": scalar(conn, "SELECT SUM(adopt_count) FROM knowledge_cards", (), 0),
    }
