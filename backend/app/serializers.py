"""把数据库里的原始记录组装成前端需要的形状。

前端只负责渲染，不再持有任何业务计算——这是这次改造的核心：
之前 JS 里那几个 derive()/五项分解/RICE 全部搬到了服务端。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from .db import rows, scalar
from . import ingest
from .analytics import config_engine, kpi, lineage, narratives, sources, variance, version

NAV = [
    {"g": "决策总览", "items": [{"k": "overview", "t": "总览", "icon": "◎"}]},
    {"g": "全流程模块", "items": [
        {"k": "market", "t": "市场洞察", "no": "01"},
        {"k": "estimate", "t": "成本评估", "no": "02"},
        {"k": "requirement", "t": "需求记录", "no": "03"},
        {"k": "schedule", "t": "研发排期", "no": "04"},
        {"k": "change", "t": "需求变更", "no": "05"},
        {"k": "cost", "t": "成本执行", "no": "06"},
        {"k": "delivery", "t": "部署实施", "no": "07"},
        {"k": "support", "t": "售后运维", "no": "08"},
        {"k": "feedback", "t": "用户反馈", "no": "09"},
    ]},
    {"g": "AI 决策专题", "items": [
        {"k": "topic-variance", "t": "成本偏差归因", "icon": "①"},
        {"k": "topic-lineage", "t": "需求演进线路图", "icon": "②"},
        {"k": "topic-version", "t": "版本方向决策", "icon": "③"},
        {"k": "topic-config", "t": "初始化配置推导", "icon": "④"},
        {"k": "quote", "t": "市场报价 Agent", "icon": "⑤"},
    ]},
    {"g": "基础", "items": [
        {"k": "llm-config", "t": "模型设置", "icon": "⚙"},
        {"k": "pricing-rules", "t": "报价规则权重", "icon": "⚖"},
        {"k": "sources", "t": "数据源与接入", "icon": "⇄"},
        {"k": "metrics", "t": "指标口径字典", "icon": "≡"},
    ]},
]


def _tone(strategy: str) -> str:
    return {"自研": "teal", "合作": "blue", "暂缓": "gray"}.get(strategy, "gray")


def bootstrap(conn: sqlite3.Connection) -> dict:
    """一次性返回首屏所需的全部数据，避免前端瀑布式请求。"""
    return {
        "meta": {
            "source": "api",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rule_version": kpi.setting(conn, "rules_version", "rule_v3.4"),
            "kpi_version": kpi.setting(conn, "kpi_version", "kpi_v1.1"),
        },
        "projects": _overview_projects(conn),
        "overview": kpi.overview(conn),
        "modules": {key: module_payload(conn, key) for _, key in _module_order(conn)},
        "variance": variance_payload(conn),
        "lineage": lineage_payload(conn),
        "versionPool": version_payload(conn),
        "survey": survey_payload(conn),
        "metrics": metrics_payload(conn),
        "nav": nav_payload(conn),
        "sources": sources_payload(conn),
    }


def sources_payload(conn: sqlite3.Connection) -> dict:
    """数据源页一次取全：汇总 + 矩阵 + 注册表 + 指标血缘索引 + 导入批次。"""
    return {
        "summary": sources.summary(conn),
        "matrix": sources.matrix(conn),
        "registry": sources.registry(conn),
        "metricIndex": sources.metric_index(conn),
        "lineages": {m["id"]: sources.lineage(conn, m["id"])
                     for m in sources.metric_index(conn)},
        "batches": ingest.batches(conn, 8),
        "types": [{"k": k, "label": lb, "role": role} for k, lb, role in sources.TYPES],
    }


def nav_payload(conn) -> list[dict]:
    """导航角标也由数据驱动：本月变更单数、预警项目数。"""
    import copy

    nav = copy.deepcopy(NAV)
    month_changes = conn.execute(
        "SELECT COUNT(*) c FROM changes WHERE submitted_at >= '2026-10-01'").fetchone()["c"]
    alerts = conn.execute(
        "SELECT COUNT(*) c FROM projects WHERE status IN ('预警','高危')").fetchone()["c"]
    for group in nav:
        for item in group["items"]:
            if item["k"] == "change" and month_changes:
                item["tail"] = str(month_changes)
            if item["k"] == "cost" and alerts:
                item["tail"] = str(alerts)
                item["warn"] = True
    return nav


def _module_order(conn):
    return [(r["no"], r["module"]) for r in rows(
        conn, "SELECT module, no FROM module_content ORDER BY order_no")]


def _overview_projects(conn) -> list[dict]:
    """总览只展示风险最高的 6 个，全量走 /api/projects。"""
    act = sorted(kpi.active_projects(conn), key=lambda p: (p["cpi"] or 1))[:6]
    act.sort(key=lambda p: -p["act"])
    return [
        {
            "id": p["id"], "name": p["name"], "client": p["client"], "mode": p["mode"],
            "contract": p["contract"], "est": p["est"], "act": p["act"],
            "cpi": p["cpi"], "eac": p["eac"], "bac": p["bac"],
            "rework": p["rework"], "status": p["status"],
        }
        for p in act
    ]


# ---------------------------------------------------------------- 模块

def module_content(conn, key):
    return conn.execute("SELECT * FROM module_content WHERE module=?", (key,)).fetchone()


def module_payload(conn: sqlite3.Connection, key: str) -> dict:
    c = module_content(conn, key)
    if c is None:
        return {}
    return {
        "no": c["no"], "name": c["name"], "en": c["en"], "tag": c["tag"],
        "judgment": c["judgment"],
        "kpis": kpi.module_kpis(conn)[key],
        "ai": narratives.module_ai(conn, key),
        "note": c["note"],
        **_body(conn, key),
    }


def _body(conn, key):
    if key == "market":
        gaps = sorted(rows(conn, "SELECT * FROM market_gaps"), key=lambda g: -g["lost_amount"])[:8]
        return {"gaps": [
            {"g": g["gap"], "freq": g["frequency"], "lost": g["lost_amount"],
             "cost": g["cost_days"], "adv": g["strategy"], "advTone": _tone(g["strategy"])}
            for g in gaps
        ]}

    if key == "estimate":
        bases = rows(conn, "SELECT * FROM estimate_baselines ORDER BY sample_n DESC")
        base_days = kpi.setting_float(conn, "quote_base_days", 120)
        rate = kpi.setting_float(conn, "quote_rate", 1.5)
        peers = sorted([p for p in kpi.closed_projects(conn) if p["est"]],
                       key=lambda p: abs(p["est"] - base_days))[:3]
        similar = [
            {"id": p["id"], "name": p["name"],
             "sim": round(max(0.5, 1 - abs(p["est"] - base_days) / (base_days * 2)), 2),
             "est": p["est"], "act": p["act"],
             "why": f"同交付模式（{p['mode']}），规模差 {abs(p['est'] - base_days) / base_days * 100:.0f}%"}
            for p in peers
        ]
        quote = [
            {"k": f"乐观（{base_days * 0.9:.0f} 人天）", "cond": "需求一次冻结、无新增集成",
             "p": f"¥{base_days * 0.9 * rate:.0f} 万"},
            {"k": f"基准（{base_days * 1.05:.0f} 人天）", "cond": "按历史返工率 12% 计提",
             "p": f"¥{base_days * 1.05 * rate:.0f} 万"},
            {"k": f"保守（{base_days * 1.23:.0f} 人天）", "cond": "含 3 个未确认第三方接口",
             "p": f"¥{base_days * 1.23 * rate:.0f} 万"},
        ]
        return {
            "baseline": [
                {"dim": b["dim"], "p": b["productivity"], "n": b["sample_n"],
                 "upd": "样本不足" if b["productivity"] is None else "季度"}
                for b in bases
            ],
            "similar": similar,
            "quote": quote,
        }

    if key == "requirement":
        return {"reqs": lineage.top_requirements(conn, 8)}

    if key == "schedule":
        team = kpi.setting_float(conn, "team_size", 6)
        wd = kpi.setting_float(conn, "iteration_workdays", 10)
        avail = kpi.setting_float(conn, "availability", 0.78)
        block = kpi.setting_float(conn, "block_ratio", 0.14)
        cap = team * wd * avail
        iters = rows(conn, "SELECT * FROM iterations ORDER BY id DESC LIMIT 3")[::-1]
        done = [i["done_points"] for i in iters if i["done_points"] is not None]
        vel = sorted(done)[len(done) // 2] if done else 0
        cur = next((i for i in iters if i["status"] == "进行中"), None)
        return {
            "capacity": [
                {"k": "团队容量", "v": f"{team:.0f} 人 × {wd:.0f} 工作日 × {avail:.2f} 可用率 = {cap:.1f} 人天", "tone": ""},
                {"k": "扣减历史阻塞", "v": f"{cap:.1f} × (1 − {block * 100:.0f}%) = {cap * (1 - block):.1f} 人天", "tone": ""},
                {"k": "速率校准", "v": f"近 {len(done)} 迭代完成点数中位数 {vel:.0f} 点", "tone": ""},
                {"k": "本迭代承诺", "v": f"{cur['committed_points']:.0f} 需求点" if cur else "—", "tone": "strong"},
                {"k": "缓冲", "v": "4 人天（单列，用于线上问题与紧急支持）", "tone": "warn"},
            ],
            "iters": [
                {"id": i["id"], "commit": i["committed_points"], "done": i["done_points"],
                 "rate": round(i["done_points"] / i["committed_points"], 2) if i["done_points"] else None,
                 "note": i["note"] or ("进行中" if i["status"] == "进行中" else "达成")}
                for i in iters
            ],
        }

    if key == "change":
        changes = sorted(rows(conn, "SELECT * FROM changes"), key=lambda c: c["submitted_at"], reverse=True)[:8]
        return {
            "causes": lineage.cause_distribution(conn),
            "changes": [
                {"id": c["id"], "req": f"{c['requirement_id'] or '—'}", "cause": c["cause_code"],
                 "by": c["initiated_by"], "days": c["net_days"], "bill": c["billing"], "st": c["status"]}
                for c in changes
            ],
        }

    if key == "cost":
        snaps = rows(conn, "SELECT * FROM evm_snapshots ORDER BY id")
        health = sorted(kpi.active_projects(conn), key=lambda p: p["cpi"] or 1)[:4]
        return {
            "trend": [round(s["ev"] / s["ac"], 3) for s in snaps],
            "health": [
                {"id": p["id"], "n": p["name"], "cpi": p["cpi"], "eac": p["eac"],
                 "bac": p["bac"], "st": "red" if (p["cpi"] or 1) < 0.85 else
                 "amber" if (p["cpi"] or 1) < 0.95 else "teal"}
                for p in health
            ],
        }

    if key == "delivery":
        ds = rows(conn, "SELECT * FROM deployments ORDER BY cycle_days DESC")
        return {"impls": [
            {"c": d["client"], "pack": d["config_pack"], "cycle": d["cycle_days"],
             "redo": d["redo_count"], "pass": d["survey_pass_rate"], "st": d["status"]}
            for d in ds
        ]}

    if key == "support":
        matrix = version.problem_matrix(conn)
        return {"heat": {
            "cols": ["A 不会用", "B 找不到", "C 理解错", "D 确实缺"],
            "rows": [{"m": m["t"], "v": [m["a"], m["b"], m["c"], m["d"]]} for m in matrix],
        }}

    if key == "feedback":
        themes = sorted(rows(conn, "SELECT * FROM feedback_themes"), key=lambda t: -t["count"])[:6]
        return {"themes": [
            {"t": t["theme"], "n": t["count"], "type": t["type"], "cust": t["customer_count"],
             "amt": t["contract_weight"]}
            for t in themes
        ]}

    return {}


# ---------------------------------------------------------------- 专题

def variance_payload(conn) -> dict:
    rs = rows(conn, """SELECT * FROM projects
        WHERE detail_level='full' ORDER BY estimate_days DESC""")
    out = {}
    for p in rs:
        b = variance.breakdown(conn, p)
        s0 = float(p["estimate_days"])
        s1 = s0 + float(scalar(conn, """SELECT SUM(days) FROM effort_records
            WHERE project_id=? AND category='scope'""", (p["id"],), 0.0))
        b.update({
            "s0": s0, "s1": round(s1, 1),
            "p0": 1.0,
            "p1": round(s1 / b["act"], 3) if b["act"] else 0,
        })
        out[p["id"]] = b
    return out


def lineage_payload(conn) -> list[dict]:
    ids = ["REQ-2317", "REQ-2288", "REQ-2244"]
    out = []
    for rid in ids:
        c = lineage.chain(conn, rid)
        if c:
            out.append(c)
    return out


def version_payload(conn) -> dict:
    hs = version.hypotheses(conn)
    matrix = version.problem_matrix(conn)
    # 假设卡跟随"最值得先看的模块"（B 类占比最高），否则页面上下两张卡讲的是两件事
    focus = max(matrix, key=lambda m: (m["b"], m["b_share"]))["t"] if matrix else None
    hk = next((h for h in hs if h["module"] == focus
               and h["status"] in ("待验证", "超期未回看")), None) \
        or next((h for h in hs if h["status"] in ("待验证", "超期未回看")), hs[0])
    return {
        "matrix": matrix,
        "hypothesis": hk,
        "pool": version.rice(conn),
        "hypotheses": hs,
    }


def survey_payload(conn) -> dict:
    tpl = conn.execute(
        "SELECT * FROM survey_templates WHERE id='SV-2026-021'").fetchone()
    if tpl is None:
        tpl = conn.execute("SELECT * FROM survey_templates ORDER BY created_at DESC LIMIT 1").fetchone()
    qs = rows(conn, "SELECT * FROM survey_questions WHERE template_id=? ORDER BY order_no", (tpl["id"],))
    answers = {
        r["code"]: (None if r["value"] is None else json.loads(r["value"]))
        for r in rows(conn, "SELECT * FROM survey_answers WHERE template_id=?", (tpl["id"],))
    }
    answered = len(answers)
    return {
        "id": tpl["id"],
        "client": tpl["client"],
        "progress": 34,
        "total": tpl["total_questions"],
        "answered": answered,
        "questions": [
            {"id": q["code"], "t": q["text"], "opts": json.loads(q["options"]),
             "def": answers.get(q["code"], json.loads(q["default_value"]) if q["default_value"] else None)}
            for q in qs
        ],
        "rule_version": kpi.setting(conn, "rules_version", "rule_v3.4"),
    }


def metrics_payload(conn) -> list[dict]:
    return [
        {"n": m["name"], "cat": m["category"], "f": m["formula"], "note": m["note"],
         "src": m["source"]}
        for m in rows(conn, "SELECT * FROM metrics_dict ORDER BY rowid")
    ]
