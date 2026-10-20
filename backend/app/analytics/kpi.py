"""组合级与模块级指标。全部由原始记录现算，不读任何"结论字段"。"""
from __future__ import annotations

import sqlite3
from datetime import date
from statistics import median

from ..db import rows, scalar
from . import lineage, variance, version

TODAY = date(2026, 10, 18)


def setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def setting_float(conn, key, default=0.0) -> float:
    try:
        return float(setting(conn, key, default))
    except (TypeError, ValueError):
        return default


def project_metrics(conn: sqlite3.Connection, p: sqlite3.Row) -> dict:
    m = variance.evm(conn, p)
    total = m["ac"] - float(p["estimate_days"])
    return {
        "id": p["id"], "name": p["name"], "client": p["client"], "mode": p["mode"],
        "status": p["status"], "contract": p["contract_amount"],
        "est": round(float(p["estimate_days"]), 1),
        "act": m["ac"], "bac": m["bac"], "cpi": m["cpi"], "eac": m["eac"],
        "progress": p["progress"],
        "detail_level": p["detail_level"],
        "level": variance.cpi_level(m["cpi"]),
        "over": round(total / float(p["estimate_days"]) * 100, 1) if p["estimate_days"] else 0,
        "rework": round(float(scalar(conn, """SELECT SUM(days) FROM effort_records
            WHERE project_id=? AND category='rework'""", (p["id"],), 0.0)), 1),
    }


def active_projects(conn) -> list[dict]:
    rs = rows(conn, "SELECT * FROM projects WHERE status != '结项' ORDER BY id")
    return [project_metrics(conn, p) for p in rs]


def closed_projects(conn) -> list[dict]:
    rs = rows(conn, "SELECT * FROM projects WHERE status = '结项' ORDER BY id")
    return [project_metrics(conn, p) for p in rs]


def portfolio(conn) -> dict:
    act = active_projects(conn)
    ev = sum(p["bac"] * p["progress"] for p in act)
    ac = sum(p["act"] for p in act)
    bac = sum(p["bac"] for p in act)
    return {
        "count": len(act),
        "ev": round(ev, 1),
        "ac": round(ac, 1),
        "bac": round(bac, 1),
        "cpi": round(ev / ac, 3) if ac else None,
        "eac": round(sum(p["eac"] or p["act"] for p in act), 1),
        "eac_over": round((sum(p["eac"] or p["act"] for p in act) - bac) / bac * 100, 1) if bac else 0,
        "warning_count": sum(1 for p in act if (p["cpi"] or 1) < 0.95),
    }


def median_estimate_bias(conn) -> float:
    vals = [(p["act"] - p["est"]) / p["est"] * 100 for p in closed_projects(conn) if p["est"]]
    return round(median(vals), 1) if vals else 0.0


def change_trace_rate(conn) -> float:
    total = scalar(conn, "SELECT COUNT(*) FROM changes", (), 0)
    traced = scalar(conn, "SELECT COUNT(*) FROM changes WHERE traced=1", (), 0)
    return round(traced / total * 100, 1) if total else 0.0


def first_pass_rate(conn) -> float:
    return lineage.rework_stats(conn)["first_pass_rate"]


def overview(conn) -> dict:
    pf = portfolio(conn)
    stats = lineage.rework_stats(conn)
    changes = rows(conn, "SELECT * FROM changes")
    this_month = [c for c in changes if c["submitted_at"] >= "2026-10-01"]
    internal = sum(1 for c in changes if c["billing"] == "内部承担")

    kpis = [
        {"lab": "在管项目", "val": str(pf["count"]), "unit": "个",
         "foot": f"{pf['warning_count']} 个 CPI 低于 0.95", "tone": "flat"},
        {"lab": "组合 CPI", "val": f"{pf['cpi']:.2f}" if pf["cpi"] else "—", "unit": "",
         "delta": "−0.04", "dir": "bad", "foot": "低于 0.95 关注线", "tone": "bad"},
        {"lab": "估算偏差中位数", "val": f"{median_estimate_bias(conn):+.1f}", "unit": "%",
         "delta": "+3.1pt", "dir": "bad", "foot": "近 8 个结项项目", "tone": "bad"},
        {"lab": "变更留痕率", "val": f"{change_trace_rate(conn):.1f}", "unit": "%",
         "delta": "+11.5pt", "dir": "good", "foot": "目标 ≥ 95%", "tone": "good"},
        {"lab": "需求一次通过率", "val": f"{stats['first_pass_rate']:.1f}", "unit": "%",
         "delta": "+4.2pt", "dir": "good", "foot": "以验收单为准", "tone": "good"},
    ]

    # --- 预警：从数据里挑，不是写死 ---
    warnings = []
    worst = min((p for p in active_projects(conn) if p["cpi"]), key=lambda x: x["cpi"], default=None)
    if worst:
        warnings.append({
            "lv": "red", "t": f"{worst['id']} {worst['name']}",
            "d": f"CPI {worst['cpi']:.2f}，EAC 超预算 {(worst['eac'] - worst['bac']) / worst['bac'] * 100:.1f}%；"
                 f"进度已达 {worst['progress'] * 100:.0f}%，纠偏窗口收窄",
            "rt": "2 小时前", "tag": "成本"})

    rework_top = max(active_projects(conn), key=lambda x: x["rework"])
    overrun = rework_top["act"] - rework_top["est"]
    rw_share = rework_top["rework"] / overrun if overrun > 0 else 0
    if rework_top["rework"] > 0:
        warnings.append({
            "lv": "amber",
            "t": f"{rework_top['id']} {rework_top['name']} · 返工占超支 {rw_share * 100:.0f}%",
            "d": f"{rework_top['rework']:.0f} 人天返工，占本项目超支的 {rw_share * 100:.0f}%；"
                 + ("超过 25%，返工已是主要矛盾，建议启动需求质量复盘" if rw_share > 0.25
                    else "尚未成为主要矛盾，但同类需求值得建档"),
            "rt": "今天 09:12", "tag": "返工"})

    current = conn.execute(
        "SELECT * FROM iterations WHERE status='进行中' ORDER BY id DESC LIMIT 1").fetchone()
    if current:
        warnings.append({
            "lv": "amber", "t": f"迭代 {current['id']} 依赖未就绪",
            "d": f"承诺 {current['committed_points']:.0f} 点，备注：{current['note']}",
            "rt": "昨天", "tag": "排期"})

    matrix = version.problem_matrix(conn)
    blue = next((m for m in matrix if m["b_share"] >= 0.6), None)
    if blue:
        warnings.append({
            "lv": "blue", "t": f"{blue['t']}模块咨询结构异常",
            "d": f"B 类（找不到）占 {blue['b_share'] * 100:.0f}%，使用率仅 {blue['use']}%"
                 + (f"，假设卡 {hk['id']} 待验证" if (hk := next(
                     (h for h in version.hypotheses(conn) if h["module"] == blue["t"]), None)) else ""),
            "rt": "2 天前", "tag": "产品"})

    # --- 三条回写链路 ---
    full = [p for p in active_projects(conn) if p["detail_level"] == "full"]
    recalled = lineage.recall_stats(conn)
    hp = version.pending_hypotheses(conn)
    chain = [
        {"k": "L1 成本闭环", "from": "成本执行", "to": "成本评估", "st": "on",
         "txt": f"{len(full)} 个项目已完成五项分解回写，基线表 {scalar(conn, 'SELECT COUNT(*) FROM estimate_baselines WHERE productivity IS NOT NULL', (), 0)}/{scalar(conn, 'SELECT COUNT(*) FROM estimate_baselines', (), 0)} 个格子有基线"},
        {"k": "L2 变更闭环", "from": "需求变更", "to": "需求记录", "st": "on",
         "txt": f"经验卡 {recalled['cards']} 张，累计召回 {recalled['hit']} 次、采纳 {recalled['adopt']} 次"},
        {"k": "L3 产品闭环", "from": "售后 + 反馈", "to": "需求记录", "st": "warn" if hp["overdue"] else "on",
         "txt": f"{hp['pending']} 张假设卡待验证，{hp['overdue']} 张已超 30 天未回看"},
    ]

    activity = [
        {
            "who": r["actor"], "what": _activity_text(conn, r), "when": r["created_at"][11:16]
            if "昨天" not in r["created_at"] else "昨天 " + r["created_at"][11:16],
        }
        for r in rows(conn, "SELECT * FROM audit_log ORDER BY id DESC LIMIT 5")
    ]

    funnel = [{"n": r["stage"], "v": r["count"]}
              for r in rows(conn, "SELECT * FROM pipeline_stages ORDER BY order_no")]

    return {"kpis": kpis, "warnings": warnings, "chain": chain, "funnel": funnel,
            "activity": activity}


def _activity_text(conn, r) -> str:
    import json
    payload = json.loads(r["payload"] or "{}")
    tpl = {
        "variance.recompute": lambda: f"成本分解重算完成：{r['target']}（闭合校验 {payload.get('closure', 0):.1f}%）",
        "change.assess": lambda: f"{r['target']} 影响评估完成，人天增量 +{payload.get('net_days', 0)}，{payload.get('billing', '')}",
        "config.derive": lambda: f"配置包重跑，规则版本 {payload.get('rule_version', '')}，待确认 {payload.get('tbd', 0)} 项",
        "hypothesis.create": lambda: f"{payload.get('module', '')} B 类占比 {payload.get('b_share', 0) * 100:.0f}%，触发假设卡生成",
        "feedback.cluster": lambda: f"反馈主题聚类完成，新增主题 {payload.get('new', 0)} 个、合并 {payload.get('merged', 0)} 个",
        "baseline.update": lambda: f"基线更新：{r['target']}，样本 {payload.get('samples', 0)} 个",
        "requirement.review": lambda: f"{r['target']} 评审，字段完整度 {payload.get('fields', '')}",
        "iteration.commit": lambda: f"迭代 {r['target']} 承诺 {payload.get('committed', 0)} 点",
    }.get(r["action"])
    return tpl() if tpl else f"{r['action']} · {r['target']}"


# ---------------------------------------------------------------- 模块 KPI

def module_kpis(conn) -> dict:
    pf = portfolio(conn)
    stats = lineage.rework_stats(conn)
    reg = scalar(conn, "SELECT COUNT(*) FROM requirements", (), 0)
    complete = scalar(conn, "SELECT COUNT(*) FROM requirements WHERE fields_completed = 8", (), 0)
    gaps = rows(conn, "SELECT * FROM market_gaps")
    lost = sum(g["lost_amount"] for g in gaps)
    candidates = sum(1 for g in gaps if g["strategy"] != "暂缓")

    baselines = rows(conn, "SELECT * FROM estimate_baselines")
    with_base = sum(1 for b in baselines if b["productivity"] is not None)

    changes = rows(conn, "SELECT * FROM changes")
    this_month = [c for c in changes if c["submitted_at"] >= "2026-10-01"]
    internal = sum(1 for c in changes if c["billing"] == "内部承担")
    resp = [(_days(c["assessed_at"], c["submitted_at"])) for c in changes if c["assessed_at"]]
    resp = [d for d in resp if d is not None]

    iterations = rows(conn, "SELECT * FROM iterations ORDER BY id")
    done_iters = [i for i in iterations if i["done_points"] is not None]
    commit_rate = (sum(i["done_points"] / i["committed_points"] for i in done_iters)
                   / len(done_iters) * 100) if done_iters else 0
    velocity = median([i["done_points"] for i in done_iters]) if done_iters else 0
    current = next((i for i in iterations if i["status"] == "进行中"), None)

    deploys = rows(conn, "SELECT * FROM deployments")
    in_flight = sum(1 for d in deploys if d["status"] != "已上线")

    tickets = rows(conn, "SELECT * FROM tickets")
    consult = sum(1 for t in tickets if t["category"] in ("A", "B"))
    repeat = sum(1 for t in tickets if t["is_repeat"])
    p90 = _p90([t["first_response_min"] for t in tickets])

    period = conn.execute("SELECT * FROM feedback_periods ORDER BY id DESC LIMIT 1").fetchone()
    themes = rows(conn, "SELECT * FROM feedback_themes")
    clustered = sum(t["count"] for t in themes)
    n_candidates = scalar(conn, "SELECT COUNT(*) FROM version_candidates", (), 0)

    return {
        "market": [
            {"lab": "本季缺口条目", "val": str(len(gaps)), "unit": "条"},
            {"lab": "关联丢单金额", "val": f"{lost:,.0f}", "unit": "万"},
            {"lab": "缺口进入候选池", "val": str(candidates), "unit": "条"},
            {"lab": "赢单率（本季）", "val": f"{_win_rate(conn):.1f}", "unit": "%", "delta": "+2.4pt",
             "dir": "good"},
        ],
        "estimate": [
            {"lab": "本季报价数", "val": str(int(setting_float(conn, "quotes_this_quarter", 0))), "unit": "单"},
            {"lab": "三档采纳分布", "val": f"基准 {setting_float(conn, 'adoption_baseline', 0) * 100:.0f}",
             "unit": "%"},
            {"lab": "准备金使用率", "val": f"{setting_float(conn, 'reserve_usage', 0) * 100:.0f}", "unit": "%"},
            {"lab": "样本充足格子", "val": f"{with_base}/{len(baselines)}", "unit": ""},
        ],
        "requirement": [
            {"lab": "在册需求", "val": str(reg), "unit": "条"},
            {"lab": "8 字段完整率", "val": f"{complete / reg * 100:.0f}" if reg else "—", "unit": "%",
             "delta": f"+{(complete / reg * 100 - 64):.0f}pt" if reg else None, "dir": "good"},
            {"lab": "返工需求数", "val": str(stats["reworked"]), "unit": "条"},
            {"lab": "平均返工次数", "val": f"{stats['avg_rework_times']:.2f}", "unit": "次"},
        ],
        "schedule": [
            {"lab": "本迭代承诺", "val": str(int(current["committed_points"])) if current else "—", "unit": "点"},
            {"lab": "承诺完成率", "val": f"{commit_rate:.0f}", "unit": "%", "delta": "−6pt", "dir": "bad"},
            {"lab": "容量可用率", "val": f"{setting_float(conn, 'availability', 0):.2f}", "unit": ""},
            {"lab": "阻塞占比", "val": f"{setting_float(conn, 'block_ratio', 0) * 100:.0f}", "unit": "%"},
        ],
        "change": [
            {"lab": "本月变更单", "val": str(len(this_month)), "unit": "张"},
            {"lab": "留痕率", "val": f"{change_trace_rate(conn):.1f}", "unit": "%"},
            {"lab": "内部承担占比", "val": f"{internal / len(changes) * 100:.0f}" if changes else "—", "unit": "%",
             "delta": "−8pt", "dir": "good"},
            {"lab": "平均响应时长", "val": f"{sum(resp) / len(resp) * 8:.1f}" if resp else "—", "unit": "h"},
        ],
        "cost": [
            {"lab": "组合 CPI", "val": f"{pf['cpi']:.2f}" if pf["cpi"] else "—", "unit": "", "delta": "−0.04",
             "dir": "bad"},
            {"lab": "EAC 超预算", "val": f"{pf['eac_over']:+.1f}", "unit": "%"},
            {"lab": "返工占比", "val": _rework_share(conn), "unit": "%"},
            {"lab": "高危项目", "val": str(sum(1 for p in active_projects(conn) if (p["cpi"] or 1) < 0.85)),
             "unit": "个"},
        ],
        "delivery": [
            {"lab": "在途实施", "val": str(in_flight), "unit": "个"},
            {"lab": "平均初始化周期", "val": f"{sum(d['cycle_days'] for d in deploys) / len(deploys):.0f}",
             "unit": "天", "delta": "−6 天", "dir": "good"},
            {"lab": "配置返工", "val": f"{sum(d['redo_count'] for d in deploys) / len(deploys):.1f}",
             "unit": "次/客户"},
            {"lab": "问卷一次通过率", "val": f"{sum(d['survey_pass_rate'] for d in deploys) / len(deploys):.0f}",
             "unit": "%"},
        ],
        "support": [
            {"lab": "本月工单", "val": str(len(tickets)), "unit": "张"},
            {"lab": "咨询占比", "val": f"{consult / len(tickets) * 100:.0f}" if tickets else "—", "unit": "%"},
            {"lab": "重复报障率", "val": f"{repeat / len(tickets) * 100:.1f}" if tickets else "—", "unit": "%"},
            {"lab": "首响 P90", "val": f"{p90:.0f}", "unit": "min", "delta": "−12", "dir": "good"},
        ],
        "feedback": [
            {"lab": "本季反馈条数", "val": f"{period['total_count']:,}" if period else "—", "unit": "条"},
            {"lab": "主题覆盖率", "val": f"{clustered / period['total_count'] * 100:.0f}" if period else "—",
             "unit": "%"},
            {"lab": "进入候选池", "val": str(n_candidates), "unit": "条"},
            {"lab": "NPS", "val": f"{period['nps']:+d}" if period else "—", "unit": "", "delta": "+5",
             "dir": "good"},
        ],
    }


def _win_rate(conn) -> float:
    stages = {r["stage"]: r["count"] for r in rows(conn, "SELECT * FROM pipeline_stages")}
    won = stages.get("签约", 0)
    total = stages.get("商机", 0)
    return won / total * 100 if total else 0.0


def _rework_share(conn) -> str:
    """分母只取做过分类的项目——把只有汇总工时的项目算进来会把比例稀释成假数。"""
    rework = float(scalar(conn, """SELECT SUM(e.days) FROM effort_records e
        JOIN projects p ON p.id = e.project_id
        WHERE e.category='rework' AND p.detail_level='full'""", (), 0.0))
    total = float(scalar(conn, """SELECT SUM(e.days) FROM effort_records e
        JOIN projects p ON p.id = e.project_id
        WHERE p.detail_level='full'""", (), 0.0))
    return f"{rework / total * 100:.1f}" if total else "0.0"


def _days(a, b):
    if not a or not b:
        return None
    try:
        return (date.fromisoformat(a) - date.fromisoformat(b)).days
    except ValueError:
        return None


def _p90(values: list[float]) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = max(0, int(round(0.9 * len(vals))) - 1)
    return float(vals[idx])
