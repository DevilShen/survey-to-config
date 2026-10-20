"""成本偏差分析。

核心不是"算差异"，而是把差异拆到可归因、可校验的五项：
    ① 范围偏差   已审批变更带来的净增人天
    ② 返工偏差   同一需求重复交付
    ③ 净效率损失 毛效率损失扣除返工（返工已在②计过，避免重复计算）
    ④ 等待偏差   阻塞、等待客户确认、依赖方延迟
    ⑤ 基线偏差   初始估算与估算基线表预测值之差

闭合校验：|Σ五项 − 总偏差| / 总偏差。超过 5% 直接判 data_gap，
因为残差项会吸收所有没录进来的损耗，这时候的归因比不归因更危险。
"""
from __future__ import annotations

import sqlite3
from statistics import mean

from ..db import rows, scalar

CLOSURE_THRESHOLD = 5.0


def effort_by_category(conn: sqlite3.Connection, project_id: str) -> dict[str, float]:
    rs = rows(
        conn,
        "SELECT category, SUM(days) AS d FROM effort_records WHERE project_id=? GROUP BY category",
        (project_id,),
    )
    return {r["category"]: float(r["d"] or 0) for r in rs}


def actual_days(conn: sqlite3.Connection, p: sqlite3.Row) -> float:
    """实际人天取财务上报口径；没有上报数时退回分类工时合计。"""
    reported = float(p["reported_days"] or 0)
    if reported > 0:
        return reported
    return float(scalar(
        conn, "SELECT SUM(days) FROM effort_records WHERE project_id=?", (p["id"],), 0.0))


def evm(conn: sqlite3.Connection, p: sqlite3.Row) -> dict:
    """挣值三件套。EAC 用 CPI 外推，CPI ≤ 0 时返回 None 而不是编一个数。"""
    ac = actual_days(conn, p)
    bac = float(p["budget_days"])
    ev = bac * float(p["progress"])
    cpi = (ev / ac) if ac > 0 else None
    eac = None
    if cpi and cpi > 0:
        eac = ac + (bac - ev) / cpi
    return {
        "bac": round(bac, 1),
        "ac": round(ac, 1),
        "ev": round(ev, 1),
        "cpi": round(cpi, 3) if cpi else None,
        "eac": round(eac, 1) if eac else None,
        "spi": None,
    }


def cpi_level(cpi: float | None) -> str:
    if cpi is None:
        return "unknown"
    if cpi >= 0.95:
        return "healthy"
    if cpi >= 0.90:
        return "watch"
    if cpi >= 0.85:
        return "warning"
    return "critical"


def peer_bias(conn: sqlite3.Connection, mode: str) -> float | None:
    """同类（同交付模式）已结项项目的平均超支率，用于类比。"""
    rs = rows(conn, """
        SELECT p.estimate_days AS est,
               (SELECT SUM(days) FROM effort_records e WHERE e.project_id = p.id) AS act
        FROM projects p
        WHERE p.status = '结项' AND p.mode = ?
    """, (mode,))
    vals = [(r["act"] - r["est"]) / r["est"] * 100 for r in rs
            if r["est"] and r["act"]]
    return round(mean(vals), 1) if vals else None


def breakdown(conn: sqlite3.Connection, p: sqlite3.Row) -> dict:
    pid = p["id"]
    est = float(p["estimate_days"])
    sums = effort_by_category(conn, pid)
    act = actual_days(conn, p)
    categorized = sum(sums.values())

    scope = sums.get("scope", 0.0)
    rework = sums.get("rework", 0.0)
    net_eff = sums.get("efficiency", 0.0)
    wait = sums.get("wait", 0.0)
    base = sums.get("base", 0.0)

    # 未分类工时：财务上报数 − 分类工时。这一项才是真正的数据缺口。
    unexplained = round(act - categorized, 2)
    # ⑤ 残差 = 估算偏差（基线工时 − 估算）+ 未分类工时
    est_error = round(base - est, 2)
    residual = round(act - (est + scope + rework + net_eff + wait), 2)
    total = round(act - est, 2)

    closure = round(abs(unexplained) / total * 100, 2) if total else 0.0

    quality_gap = p["data_quality"] == "gap" or closure > CLOSURE_THRESHOLD

    items = [
        {
            "k": "① 范围偏差", "v": round(scope, 1),
            "note": _scope_note(conn, pid, scope),
            "src": "dwd_change" if scope else "data_gap",
        },
        {
            "k": "② 返工偏差", "v": round(rework, 1),
            "note": _rework_note(conn, pid, rework),
            "src": "dwd_effort",
        },
        {
            "k": "③ 效率偏差（净）", "v": round(net_eff, 1),
            "note": _efficiency_note(conn, pid, net_eff, rework),
            "src": "dwd_effort",
        },
        {
            "k": "④ 等待偏差", "v": round(wait, 1),
            "note": _wait_note(conn, pid, wait),
            "src": "dwd_block" if wait else "data_gap",
        },
        {
            "k": "⑤ 估算基线偏差", "v": residual,
            "note": _baseline_note(est_error, unexplained, residual, est),
            "src": "计算" if not unexplained else "data_gap",
        },
    ]

    return {
        "id": pid,
        "name": p["name"],
        "client": p["client"],
        "mode": p["mode"],
        "contract": p["contract_amount"],
        "est": est,
        "act": round(act, 1),
        "categorized": round(categorized, 1),
        "unexplained": unexplained,
        "est_error": est_error,
        "total": total,
        "closeRate": closure,
        "closed": closure <= CLOSURE_THRESHOLD,
        "gap": quality_gap,
        "items": items,
        "verdict": _verdict(est, act, total, items, p, conn, unexplained),
        "extra": _extra(conn, pid, net_eff, wait, quality_gap, unexplained),
        "action": _actions(conn, pid, items, total, p, unexplained),
        "similar": _similar(conn, p, total, est),
    }


def _scope_note(conn, pid, scope):
    if not scope:
        return "无已审批变更"
    cnt = scalar(conn, "SELECT COUNT(*) FROM changes WHERE project_id=? AND status='已通过'", (pid,), 0)
    billed = scalar(conn, """SELECT COUNT(*) FROM changes WHERE project_id=? AND billing='已计价'""", (pid,), 0)
    return f"{cnt} 张变更单净增 {scope:.0f} 点，其中 {billed} 张已计价"


def _rework_note(conn, pid, rework):
    if not rework:
        return "无返工记录"
    n = scalar(conn, """SELECT COUNT(*) FROM requirement_revisions r
        JOIN requirements q ON q.id = r.requirement_id
        WHERE q.project_id=? AND r.rev_no>1 AND r.planned_phase=0""", (pid,), 0)
    return f"{n} 次二次开发，按实际人天直接计入"


def _efficiency_note(conn, pid, net_eff, rework):
    if not net_eff:
        return "无效率损失记录"
    return f"毛值 {net_eff + rework:.1f} 扣除返工 {rework:.1f}"


def _wait_note(conn, pid, wait):
    if not wait:
        return "无阻塞记录；若实际存在等待，会被⑤残差吸收"
    kinds = rows(conn, """SELECT DISTINCT note FROM effort_records
        WHERE project_id=? AND category='wait'""", (pid,))
    return f"阻塞记录覆盖 {len(kinds)} 类：{'、'.join(r['note'] for r in kinds)}"


def _baseline_note(est_error, unexplained, residual, est):
    if abs(est_error) < 0.01 and abs(unexplained) < 0.01:
        return f"残差为 0，①–④ 已完整解释偏差；估算 {est:.0f} 与基线实际工时一致"
    if abs(unexplained) < 0.01:
        return f"估算 {est:.0f}，基线实际工时 {est + est_error:.0f}，估算偏差 {est_error:+.1f}"
    return (f"估算偏差 {est_error:+.1f}，未分类工时 {unexplained:+.1f}，"
            f"合计残差 {residual:+.1f}")


def _verdict(est, act, total, items, p, conn, unexplained=0.0) -> str:
    if total <= 0:
        return (f"实际 {act:.0f} 人天低于估算 {est:.0f}，节余 {abs(total):.0f} 人天。"
                f"需确认是范围缩减还是效率提升，否则下个项目会据此低估。")

    d = {i["k"][0]: i["v"] for i in items}
    pct = lambda v: round(v / total * 100)  # noqa: E731
    rate = round(total / est * 100)

    segs = []
    if d["①"] > 0:
        segs.append(f"{pct(d['①'])}% 来自范围净增（客户变更）")
    if d["②"] > 0:
        segs.append(f"{pct(d['②'])}% 来自返工")
    if d["③"] > 0:
        segs.append(f"{pct(d['③'])}% 来自净效率下降")
    if d["④"] > 0:
        segs.append(f"{pct(d['④'])}% 来自等待阻塞")

    head = f"超支 {total:.0f} 人天（{rate}%），" + "、".join(segs) if segs else f"超支 {total:.0f} 人天（{rate}%）"

    if abs(d["⑤"]) <= 1.5:
        tail = f"；<b>估算基线本身没有偏差</b>（估算 {est:.0f} 人天与实际基线工时相符），需要改的是过程损耗，不是报价方法。"
    elif d["⑤"] < 0:
        tail = f"；估算基线偏保守 {abs(d['⑤']):.0f} 人天，说明报价时多留了余量。"
    else:
        tail = f"；估算基线偏乐观 {d['⑤']:.0f} 人天，这是报价方法本身的问题，要回写基线表。"

    # 最大的正向项决定下一步该看哪里
    positive = {k: v for k, v in d.items() if k != "⑤" and v > 0}
    if positive:
        top = max(positive, key=positive.get)
        hint = {
            "①": "应回到变更单核对计价归属",
            "②": "应优先看需求演进链，而不是重算报价",
            "③": "应查环境与上手成本",
            "④": "应查客户确认机制与依赖前置",
        }[top]
        tail += f" 最大项是{top}，{hint}。"

    if abs(unexplained) >= 0.5:
        tail += (f" 另有 <b>{unexplained:+.1f} 人天工时未进分类</b>（data_gap），"
                 f"说明阻塞或效率记录有漏登，上述归因需打折看。")
    return head + tail


def _extra(conn, pid, net_eff, wait, quality_gap, unexplained=0.0) -> str:
    parts = []
    if net_eff:
        kinds = rows(conn, """SELECT note, days FROM effort_records
            WHERE project_id=? AND category='efficiency' ORDER BY days DESC""", (pid,))
        parts.append("净效率 " + f"{net_eff:.0f}" + " 人天可进一步拆分：" +
                     "、".join(f"{r['note']} {r['days']:.0f}" for r in kinds))
    if unexplained:
        parts.append(f"财务上报与实际分类工时差 {unexplained:+.1f} 人天，未归入任何一类；"
                     f"残差会吸收这部分，因此本次分解可信度：中。")
    if quality_gap and not unexplained:
        parts.append("阻塞记录仅覆盖部分类型，④ 项可信度低（data_gap）。")
    if not parts:
        parts.append("本项目的分解项与记录一一对应，可信度：高。")
    return " ".join(parts)


def _actions(conn, pid, items, total, p, unexplained=0.0) -> list[dict]:
    out = []
    d = {i["k"][0]: i["v"] for i in items}
    if d["②"] > 0:
        c1 = scalar(conn, "SELECT COUNT(*) FROM changes WHERE project_id=? AND cause_code='C1'", (pid,), 0)
        out.append({"k": "返工控制",
                    "v": f"评审清单固化「审批分级依据」必问项；本项目 C1 类问题已出现 {c1} 次，"
                         f"预计可减少同类返工 {d['②'] * 0.6:.0f} 人天", "cost": "0 成本"})
    if d["③"] > 0:
        out.append({"k": "效率损失",
                    "v": "环境问题指向测试环境不稳定，建议由平台组专项处理（历史同类项目平均可挽回 60%）",
                    "cost": "约 3 人天投入"})
    if d["④"] > 0:
        out.append({"k": "等待压缩",
                    "v": "对三级决策链客户，在合同中约定「确认响应 3 个工作日内」，否则计入变更",
                    "cost": "商务配合"})
    if abs(d["⑤"]) <= 1.5:
        out.append({"k": "报价校准",
                    "v": "本项目基线无偏差，说明现有报价方法可靠，不需要整体上调",
                    "cost": "—"})
    else:
        out.append({"k": "基线校准",
                    "v": f"把本项目实际值回写基线表，样本 +1；当前偏差 {d['⑤']:+.0f} 人天",
                    "cost": "—"})
    if p["data_quality"] == "gap" or unexplained:
        out.append({"k": "数据补录",
                    "v": f"补录 {abs(unexplained):.0f} 人天未分类工时与缺失的阻塞记录，否则下次分解残差会继续偏大",
                    "cost": "1 人天"})
    return out


def _similar(conn, p, total, est) -> str:
    bias = peer_bias(conn, p["mode"])
    if bias is None:
        return "同类项目样本不足，暂不做类比。"
    mine = round(total / est * 100, 1)
    rel = "高于" if mine > bias else "低于"
    return (f"同类（{p['mode']}）项目平均超支 {bias:.1f}%，本项目 {mine:.1f}%，"
            f"{rel}同类 {abs(mine - bias):.1f} 个百分点。")
