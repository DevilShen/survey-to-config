"""市场报价 Agent · 接口层。

读接口开放，写接口（执行推理 / 人工复核 / 改规则）需要 pm 或 admin。
规则改动一律留审计：报价权重是能直接改变成交价的参数，不能悄悄改。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, HTTPException

from ..analytics import pricing_agent as pa
from ..db import one, rows, scalar
from ..seed_pricing import DEFAULT_RULES
from .auth import require_roles

router = APIRouter(prefix="/api/pricing", tags=["pricing"])

# 参数可调范围：超出范围直接拒绝，而不是"先存下来再说"
LIMITS = {
    "weight": (0.0, 1.0),
    "day_cost": (0.3, 3.0),
    "markup": (0.0, 2.0),
    "rework_reserve": (0.0, 0.6),
    "phase2_factor": (0.2, 1.0),
    "compliance_factor": (0.8, 3.0),
    "clarity_min": (0.30, 0.95),
    "confidence_min": (0.40, 0.95),
    "margin_min": (0.05, 0.40),
    "auto_raise_max": (0.0, 0.30),
    "topk": (3, 20),
    "sim_min": (0.0, 0.80),
    "top_n": (3, 6),
    "min_n": (2, 5),
    "valid_days": (7, 180),
    "max_rounds": (0, 4),
    "rrf_k": (10, 200),
}


def get_conn():
    from ..db import connect

    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _audit(conn, actor, action, target, payload):
    conn.execute(
        "INSERT INTO audit_log(actor,action,target,payload,created_at) VALUES(?,?,?,?,?)",
        (actor, action, target, json.dumps(payload, ensure_ascii=False),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()


def _run_summary(r) -> dict:
    q = json.loads(r["quote"]) if r["quote"] else None
    return {
        "run_id": r["id"], "request_id": r["request_id"], "status": r["status"],
        "clarity": r["clarity"], "confidence": r["confidence"],
        "retune_rounds": r["retune_rounds"], "rule_version": r["rule_version"],
        "output_hash": r["output_hash"], "at": r["created_at"],
        "by": r["created_by"],
        "quote_no": (q or {}).get("no"), "price": (q or {}).get("price"),
        "days": (q or {}).get("days"), "margin": (q or {}).get("margin"),
    }


def _request_row(conn, rid):
    r = one(conn, "SELECT * FROM quote_requests WHERE id=?", (rid,))
    if r is None:
        raise HTTPException(404, "报价需求不存在")
    return dict(r)


# ---------------------------------------------------------------- 工作台

@router.get("/overview")
def overview(conn=Depends(get_conn)):
    reqs = []
    for r in rows(conn, "SELECT * FROM quote_requests ORDER BY created_at DESC, id DESC"):
        latest = one(conn, """SELECT * FROM quote_runs WHERE request_id=?
                              ORDER BY created_at DESC, id DESC LIMIT 1""", (r["id"],))
        item = dict(r)
        item["latest"] = _run_summary(latest) if latest else None
        item["run_count"] = scalar(conn, "SELECT COUNT(*) FROM quote_runs WHERE request_id=?",
                                   (r["id"],), 0)
        reqs.append(item)

    done = [r for r in reqs if r["latest"]]
    # 明确度不足的运行置信度为 0，它代表"没有结论"，算进均值会把整体拉低失真
    confs = [r["latest"]["confidence"] for r in done if r["latest"]["confidence"] > 0]
    margins = [r["latest"]["margin"] for r in done if r["latest"]["margin"] is not None]
    stats = {
        "requests": len(reqs),
        "clarified": sum(1 for r in done if r["latest"]["status"] == "clarified"),
        "quoted": sum(1 for r in done if r["latest"]["status"] == "quoted"),
        "below_margin": sum(1 for r in done if r["latest"]["status"] == "below_margin"),
        "reviewed": sum(1 for r in done if r["latest"]["status"] == "reviewed"),
        "avg_confidence": round(sum(confs) / len(confs), 4) if confs else 0,
        "avg_margin": round(sum(margins) / len(margins), 4) if margins else 0,
        "retuned": sum(1 for r in done if r["latest"]["retune_rounds"] > 0),
    }
    return {"requests": reqs, "stats": stats,
            "version": pa.active_version(conn),
            "stages": pa.STAGES, "edges": pa.EDGES,
            "engine": pa.ENGINE_VERSION}


@router.get("/requests/{rid}")
def request_detail(rid: str, conn=Depends(get_conn)):
    req = _request_row(conn, rid)
    runs = [_run_summary(r) for r in rows(
        conn, "SELECT * FROM quote_runs WHERE request_id=? ORDER BY created_at DESC, id DESC",
        (rid,))]
    return {"request": req, "runs": runs}


@router.post("/requests/{rid}/run")
def run_request(rid: str, payload: dict = Body(default={}),
                conn=Depends(get_conn),
                user: dict = Depends(require_roles("pm", "admin"))):
    """执行一次完整推理。

    answers 是"反向追问"的补答；补答后仍然从需求拆解重跑，
    不是从追问处继续——否则中间状态会带着上一轮的假设，越跑越偏。
    """
    req = _request_row(conn, rid)
    answers = payload.get("answers") or {}
    result = pa.run(conn, req, answers=answers)
    result = pa.persist(conn, req, result, user["name"], answers=answers)

    status = result["status"]
    new_status = {"clarified": "待补充", "below_margin": "待复核",
                  "rejected": "待复核"}.get(status, "已出价")
    conn.execute("UPDATE quote_requests SET status=? WHERE id=?", (new_status, rid))
    _audit(conn, user["name"], "pricing.run", rid,
           {"run": result["run_id"], "status": status,
            "confidence": result["confidence"], "retune": result["retune_rounds"],
            "output_hash": result.get("output_hash")})
    return _detail(conn, result["run_id"])


@router.get("/runs/{run_id}")
def run_detail(run_id: str, conn=Depends(get_conn)):
    return _detail(conn, run_id)


def _detail(conn, run_id: str) -> dict:
    r = one(conn, "SELECT * FROM quote_runs WHERE id=?", (run_id,))
    if r is None:
        raise HTTPException(404, "推理记录不存在")
    req = dict(one(conn, "SELECT * FROM quote_requests WHERE id=?", (r["request_id"],)))
    reviews = [{"decision": x["decision"], "reviewer": x["reviewer"],
                "final_price": x["final_price"], "comment": x["comment"],
                "at": x["created_at"]}
               for x in rows(conn, "SELECT * FROM quote_reviews WHERE run_id=? ORDER BY id",
                             (run_id,))]
    # 本次运行前后关联的 LLM 调用（对话抽取/追问话术/结论解读）——溯源徽标的数据源
    llm_calls = [{"id": c["id"], "node": c["node"], "purpose": c["purpose"],
                  "model": c["model"], "tokens_in": c["tokens_in"],
                  "tokens_out": c["tokens_out"], "latency_ms": c["latency_ms"],
                  "status": c["status"], "at": c["created_at"]}
                 for c in rows(conn, "SELECT * FROM llm_calls WHERE run_id=? ORDER BY id",
                               (run_id,))]
    return {
        "run": _run_summary(r),
        "request": req,
        "trace": json.loads(r["trace"]),
        "quote": json.loads(r["quote"]) if r["quote"] else None,
        "answers": json.loads(r["answers"]) if r["answers"] else {},
        "reviews": reviews,
        "llm_calls": llm_calls,
        "stages": pa.STAGES, "edges": pa.EDGES,
    }


# ---------------------------------------------------------------- 人工复核

@router.post("/runs/{run_id}/review")
def review(run_id: str, payload: dict = Body(...),
           conn=Depends(get_conn),
           user: dict = Depends(require_roles("pm", "admin"))):
    """人工复核。AI 给的是建议价，签字的是人；所以必须留意见。"""
    decision = payload.get("decision")
    if decision not in ("approved", "adjusted", "rejected"):
        raise HTTPException(400, "decision 只能是 approved / adjusted / rejected")
    r = one(conn, "SELECT * FROM quote_runs WHERE id=?", (run_id,))
    if r is None:
        raise HTTPException(404, "推理记录不存在")
    if r["status"] in ("reviewed",):
        raise HTTPException(409, "该报价已复核，不可重复提交")

    comment = (payload.get("comment") or "").strip()
    if not comment:
        raise HTTPException(400, "必须填写复核意见——没有理由的放行等于没复核")

    final_price = payload.get("final_price")
    if decision == "adjusted":
        if final_price in (None, ""):
            raise HTTPException(400, "人工调价必须给出最终价格")
        final_price = float(final_price)
        quote = json.loads(r["quote"]) if r["quote"] else None
        if quote is None:
            raise HTTPException(409, "该运行没有报价单，无法调价")
        cost = quote["cost"]
        margin = (final_price - cost) / final_price if final_price else 0
        if margin < 0:
            raise HTTPException(400, f"最终价 {final_price} 万低于成本 {cost} 万，不允许放行")

    conn.execute(
        """INSERT INTO quote_reviews(run_id,decision,reviewer,final_price,comment,created_at)
           VALUES (?,?,?,?,?,?)""",
        (run_id, decision, user["name"], final_price, comment,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    quote = json.loads(r["quote"]) if r["quote"] else None
    if quote and final_price:
        quote["price"] = final_price
        quote["margin"] = round((final_price - quote["cost"]) / final_price, 4)
        quote["reviewed_price"] = final_price
    conn.execute("UPDATE quote_runs SET status='reviewed', quote=? WHERE id=?",
                 (json.dumps(quote, ensure_ascii=False) if quote else None, run_id))
    conn.execute("UPDATE quote_requests SET status=? WHERE id=?",
                 ("已复核" if decision != "rejected" else "已放弃", r["request_id"]))
    _audit(conn, user["name"], "pricing.review", run_id,
           {"decision": decision, "final_price": final_price, "comment": comment})
    return {"ok": True, "run_id": run_id, "decision": decision,
            "reviewer": user["name"], "final_price": final_price}


# ---------------------------------------------------------------- 规则权重

def _rules_payload(conn) -> list[dict]:
    rules = pa.load_rules(conn)
    out = []
    for code, r in rules.items():
        out.append({**r, "default": next(
            (d["params"] for d in DEFAULT_RULES if d["code"] == code), {}),
            "default_weight": next(
                (d["weight"] for d in DEFAULT_RULES if d["code"] == code), 0.0)})
    return out


@router.get("/rules")
def get_rules(conn=Depends(get_conn)):
    versions = [{"version": v["version"], "label": v["label"], "at": v["created_at"],
                 "by": v["created_by"], "active": bool(v["active"]),
                 "diff": json.loads(v["diff"]) if v["diff"] else None}
                for v in rows(conn, "SELECT * FROM price_rule_versions ORDER BY created_at")]
    return {"version": pa.active_version(conn), "rules": _rules_payload(conn),
            "versions": versions, "limits": LIMITS,
            "groups": [{"k": "market", "t": "市场竞争"}, {"k": "customer", "t": "客户级别"},
                       {"k": "cost", "t": "成本加成"}, {"k": "retrieval", "t": "检索策略"},
                       {"k": "threshold", "t": "判定阈值"}]}


def _validate(rules_in: list[dict], current: dict) -> tuple[list[str], list[dict]]:
    """逐条校验。宁可报错也不静默夹逼——夹逼会让人以为改动生效了。"""
    errors = []
    out = []
    for item in rules_in:
        code = item.get("code")
        cur = current.get(code)
        if cur is None:
            errors.append(f"{code}: 规则不存在")
            continue
        weight = float(item.get("weight", cur["weight"]))
        lo, hi = LIMITS["weight"]
        if not lo <= weight <= hi:
            errors.append(f"{code}: 权重 {weight} 超出 [{lo}, {hi}]")
        enabled = bool(item.get("enabled", cur["enabled"]))
        if not cur["editable"] and not enabled:
            errors.append(f"{code}: 该类规则不允许禁用（硬约束）")
        params = dict(cur["params"])
        for k, v in (item.get("params") or {}).items():
            if k not in params:
                errors.append(f"{code}: 未知参数 {k}")
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if k in LIMITS:
                    lo, hi = LIMITS[k]
                    if not lo <= float(v) <= hi:
                        errors.append(f"{code}.{k}: {v} 超出 [{lo}, {hi}]")
                if isinstance(params[k], int) and not isinstance(params[k], bool) and float(v).is_integer():
                    params[k] = int(v)
                else:
                    params[k] = float(v)
            elif isinstance(v, dict):
                base = params.get(k)
                if isinstance(base, list):
                    # 数组型参数（如价格带 [下限, 上限]）：前端按 "k.0" 下标提交，
                    # 这里按位回填，绝不能用 dict(list) 去合并，那会直接抛 TypeError。
                    merged = list(base)
                    for idx, val in v.items():
                        try:
                            i = int(idx)
                        except (TypeError, ValueError):
                            errors.append(f"{code}.{k}: 非法下标 {idx}")
                            continue
                        if not 0 <= i < len(merged):
                            errors.append(f"{code}.{k}: 下标 {i} 越界")
                            continue
                        merged[i] = val
                    params[k] = merged
                else:
                    merged = dict(base or {})
                    merged.update(v)
                    params[k] = merged
            elif isinstance(v, list):
                params[k] = v
            else:
                params[k] = v
        out.append({"code": code, "weight": weight, "params": params, "enabled": enabled})

    # 定价三规则至少要留两条：只剩一条就不是"多规则融合"了。
    # 只在本轮确实提交了这三条时才校验，否则改一个阈值也会被误判。
    touched = [x for x in out if x["code"] in ("R-MKT-01", "R-CUS-01", "R-COST-01")]
    if touched:
        main = [{"code": c, "weight": current[c]["weight"], "enabled": current[c]["enabled"]}
                for c in ("R-MKT-01", "R-CUS-01", "R-COST-01")]
        for x in touched:
            for m in main:
                if m["code"] == x["code"]:
                    m["weight"], m["enabled"] = x["weight"], x["enabled"]
        if len([m for m in main if m["enabled"]]) < 2:
            errors.append("定价规则至少保留两条（否则融合失去意义）")
        if sum(m["weight"] for m in main if m["enabled"]) <= 0:
            errors.append("三条定价规则的权重之和必须大于 0")
    return errors, out


@router.put("/rules")
def save_rules(payload: dict = Body(...), conn=Depends(get_conn),
               user: dict = Depends(require_roles("pm", "admin"))):
    incoming = payload.get("rules") or []
    label = (payload.get("label") or "").strip() or "人工调整"
    current = pa.load_rules(conn)
    errors, clean = _validate(incoming, current)
    if errors:
        raise HTTPException(400, "；".join(errors))

    diff = {}
    for c in clean:
        cur = current[c["code"]]
        if abs(c["weight"] - cur["weight"]) > 1e-9 or c["params"] != cur["params"] \
                or c["enabled"] != cur["enabled"]:
            diff[c["code"]] = {
                "weight": [cur["weight"], c["weight"]] if abs(c["weight"] - cur["weight"]) > 1e-9 else None,
                "enabled": [cur["enabled"], c["enabled"]] if c["enabled"] != cur["enabled"] else None,
                "params": {k: [cur["params"].get(k), v] for k, v in c["params"].items()
                           if cur["params"].get(k) != v},
            }
    if not diff:
        return {"ok": True, "version": pa.active_version(conn), "changed": 0,
                "message": "没有检测到改动"}

    merged = []
    for code, cur in current.items():
        hit = next((c for c in clean if c["code"] == code), None)
        merged.append({"code": code,
                       "weight": hit["weight"] if hit else cur["weight"],
                       "params": hit["params"] if hit else cur["params"],
                       "enabled": hit["enabled"] if hit else cur["enabled"]})

    n = len(rows(conn, "SELECT version FROM price_rule_versions")) + 1
    version = f"w_v{n}"
    conn.execute("UPDATE price_rule_versions SET active=0")
    conn.execute(
        """INSERT INTO price_rule_versions(version,label,snapshot,diff,created_at,created_by,active)
           VALUES (?,?,?,?,?,?,1)""",
        (version, label, json.dumps(_snapshot(merged), ensure_ascii=False),
         json.dumps(diff, ensure_ascii=False),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user["name"]))
    for c in clean:
        conn.execute("UPDATE price_rules SET weight=?,params=?,enabled=? WHERE code=?",
                     (c["weight"], json.dumps(c["params"], ensure_ascii=False),
                      1 if c["enabled"] else 0, c["code"]))
    conn.commit()
    _audit(conn, user["name"], "pricing.rules.save", version, {"label": label, "diff": diff})
    return {"ok": True, "version": version, "changed": len(diff), "diff": diff}


def _snapshot(clean: list[dict]) -> list[dict]:
    return [{"code": c["code"], "weight": c["weight"], "params": c["params"],
             "enabled": c["enabled"]} for c in clean]


@router.post("/rules/simulate")
def simulate_rules(payload: dict = Body(...), conn=Depends(get_conn)):
    """影子试算：拿一条历史运行的需求，用新权重重算，只对比不落库。"""
    run_id = payload.get("run_id")
    r = one(conn, "SELECT * FROM quote_runs WHERE id=?", (run_id,))
    if r is None:
        raise HTTPException(404, "推理记录不存在")
    run_row = dict(r)

    current = pa.load_rules(conn)
    incoming = payload.get("rules") or []
    if incoming:
        errors, clean = _validate(incoming, current)
        if errors:
            raise HTTPException(400, "；".join(errors))
    else:
        clean = [{"code": k, "weight": v["weight"], "params": v["params"],
                  "enabled": v["enabled"]} for k, v in current.items()]

    draft = {code: dict(base) for code, base in current.items()}
    for c in clean:
        draft[c["code"]] = {**current[c["code"]], **c}
    before = pa.simulate(conn, run_row, current)
    after = pa.simulate(conn, run_row, draft)

    def pack(x):
        q = x.get("quote")
        return {"status": x["status"], "confidence": x["confidence"],
                "clarity": x["clarity"], "price": (q or {}).get("price"),
                "days": (q or {}).get("days"), "margin": (q or {}).get("margin"),
                "retune": x["retune_rounds"],
                "rules": list({p["rule"]: p["price"] for p in
                               (x.get("pricing") or {}).get("prices", []) if p.get("price")}.items())}

    b, a = pack(before), pack(after)
    delta = None
    if b.get("price") and a.get("price"):
        delta = {"price": round(a["price"] - b["price"], 1),
                 "pct": round(a["price"] / b["price"] - 1, 4),
                 "margin": round((a.get("margin") or 0) - (b.get("margin") or 0), 4)}
    return {"run_id": run_id, "request_id": run_row["request_id"],
            "before": b, "after": a, "delta": delta,
            "status_changed": b["status"] != a["status"]}


@router.post("/rules/regress")
def regress_rules(payload: dict = Body(...), conn=Depends(get_conn),
                  user: dict = Depends(require_roles("pm", "admin"))):
    """改前回归（L-D）：拿最近 20 条已出价需求，用新规则全量重算对比。

    影子试算只能看一条；改规则的人必须看到"这一改，历史上哪些单子会变脸"。
    状态翻转、价差超 ±10% 的置顶——这两类是必须人工过目的。
    纯试算，不落库、不改任何正式记录。
    """
    current = pa.load_rules(conn)
    incoming = payload.get("rules") or []
    if incoming:
        errors, clean = _validate(incoming, current)
        if errors:
            raise HTTPException(400, "；".join(errors))
    else:
        clean = [{"code": k, "weight": v["weight"], "params": v["params"],
                  "enabled": v["enabled"]} for k, v in current.items()]
    draft = {code: dict(base) for code, base in current.items()}
    for c in clean:
        draft[c["code"]] = {**current[c["code"]], **c}

    latest_runs = rows(conn, """
        SELECT r.* FROM quote_runs r
        JOIN (SELECT request_id, MAX(created_at || id) AS mx FROM quote_runs GROUP BY request_id) m
          ON r.request_id = m.request_id AND (r.created_at || r.id) = m.mx
        WHERE r.quote IS NOT NULL
        ORDER BY r.created_at DESC LIMIT 20""")

    def pack(x):
        q = x.get("quote") or {}
        return {"status": x["status"], "price": q.get("price"),
                "margin": q.get("margin"), "confidence": x["confidence"]}

    items = []
    for r in latest_runs:
        run_row = dict(r)
        before = pack(pa.simulate(conn, run_row, current))
        after = pack(pa.simulate(conn, run_row, draft))
        delta_pct = None
        if before["price"] and after["price"]:
            delta_pct = round(after["price"] / before["price"] - 1, 4)
        items.append({
            "run_id": r["id"], "request_id": r["request_id"],
            "client": (json.loads(r["quote"]) or {}).get("client"),
            "before": before, "after": after,
            "delta_pct": delta_pct,
            "status_changed": before["status"] != after["status"],
            "big_move": delta_pct is not None and abs(delta_pct) >= 0.10,
        })
    items.sort(key=lambda x: (not x["status_changed"], not x["big_move"],
                              -(abs(x["delta_pct"]) if x["delta_pct"] is not None else 0)))
    return {"count": len(items),
            "changed": sum(1 for i in items if i["status_changed"]),
            "big_moves": sum(1 for i in items if i["big_move"]),
            "items": items}


@router.post("/rules/reset")
def reset_rules(conn=Depends(get_conn),
                user: dict = Depends(require_roles("admin"))):
    """恢复出厂默认。只有 admin 能做——这条路径会一次性改动全部报价口径。"""
    n = len(rows(conn, "SELECT version FROM price_rule_versions")) + 1
    version = f"w_v{n}"
    conn.execute("UPDATE price_rule_versions SET active=0")
    conn.execute(
        """INSERT INTO price_rule_versions(version,label,snapshot,diff,created_at,created_by,active)
           VALUES (?,?,?,?,?,?,1)""",
        (version, "恢复出厂默认", json.dumps(DEFAULT_RULES, ensure_ascii=False),
         json.dumps({"reset": True}, ensure_ascii=False),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user["name"]))
    for d in DEFAULT_RULES:
        conn.execute("UPDATE price_rules SET weight=?,params=?,enabled=1 WHERE code=?",
                     (d["weight"], json.dumps(d["params"], ensure_ascii=False), d["code"]))
    conn.commit()
    _audit(conn, user["name"], "pricing.rules.reset", version, {})
    return {"ok": True, "version": version}


# ---------------------------------------------------------------- 报价单

@router.post("/runs/{run_id}/rerun")
def rerun_with_constraints(run_id: str, payload: dict = Body(...),
                           conn=Depends(get_conn),
                           user: dict = Depends(require_roles("pm", "admin"))):
    """驳回后带意见重跑（L-C 闭环）。

    复核意见不是写给系统看的牢骚，是下一轮推理的输入：
    「总价必须 ≤80 万」→ 预算约束；「砍掉巡检模块」→ 范围约束。
    驳回 → 约束 → 重跑 三段映射全部留痕，人能看到意见在哪一环生了效。
    """
    r = one(conn, "SELECT * FROM quote_runs WHERE id=?", (run_id,))
    if r is None:
        raise HTTPException(404, "推理记录不存在")
    comment = (payload.get("comment") or "").strip()
    if not comment:
        raise HTTPException(400, "重跑必须带复核意见——没有意见的驳回无法转化为约束")

    from ..llm import audit as llm_audit
    from ..llm import prompts as lp
    from ..llm.client import get_client

    client = get_client()
    constraints, src = lp.extract_constraints(client, comment)
    cid = llm_audit.record_call(
        conn, run_id=run_id, node="review", purpose="constraint",
        model="offline" if src == "offline" else client.model,
        prompt=comment,
        status="offline" if src == "offline" else "ok")

    try:
        answers = json.loads(r["answers"]) if r["answers"] else {}
    except ValueError:
        answers = {}
    prev_c = answers.get("_constraints") or {}
    merged_c = {
        "max_price": constraints.get("max_price") or prev_c.get("max_price"),
        "drop_modules": list(dict.fromkeys(
            (prev_c.get("drop_modules") or []) + constraints.get("drop_modules", []))),
        "require_modules": list(dict.fromkeys(
            (prev_c.get("require_modules") or []) + constraints.get("require_modules", []))),
        "note": constraints.get("note") or prev_c.get("note") or "",
    }
    answers["_constraints"] = merged_c
    if merged_c["max_price"] and not answers.get("budget"):
        answers["budget"] = str(int(merged_c["max_price"]))

    req = _request_row(conn, r["request_id"])
    result = pa.run(conn, req, answers=answers)
    result = pa.persist(conn, req, result, user["name"], answers=answers)
    # 意见抽取的 LLM 调用归位到新 run，trace 徽标可溯源
    conn.execute("UPDATE llm_calls SET run_id=? WHERE id=?", (result["run_id"], cid))
    conn.commit()
    conn.execute("UPDATE quote_requests SET status=? WHERE id=?",
                 ({"clarified": "待补充", "below_margin": "待复核",
                   "rejected": "待复核"}.get(result["status"], "已出价"), r["request_id"]))
    _audit(conn, user["name"], "pricing.rerun", result["run_id"],
           {"from_run": run_id, "comment": comment, "constraints": merged_c,
            "source": src, "status": result["status"]})
    out = _detail(conn, result["run_id"])
    out["constraints"] = merged_c
    out["constraint_source"] = src
    out["from_run"] = run_id
    return out


# ---------------------------------------------------------------- LLM 审计

@router.get("/llm-calls")
def llm_calls(conn=Depends(get_conn)):
    """LLM 调用审计（只读）：近 50 次调用的节点 / 模型 / token / 耗时 / 状态。"""
    from ..llm import audit as llm_audit
    from ..llm.client import llm_status
    items = llm_audit.recent_calls(conn, 50)
    total = scalar(conn, "SELECT COUNT(*) FROM llm_calls", (), 0)
    tokens = scalar(conn,
                    "SELECT SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)) FROM llm_calls",
                    (), 0)
    return {"items": items, "total": total, "tokens": tokens or 0, "llm": llm_status()}


# ---------------------------------------------------------------- 报价单

@router.get("/quotes")
def quotes(conn=Depends(get_conn)):
    out = []
    for r in rows(conn, """SELECT * FROM quote_runs WHERE quote IS NOT NULL
                           ORDER BY created_at DESC, id DESC LIMIT 50"""):
        q = json.loads(r["quote"])
        out.append({"run_id": r["id"], "no": q.get("no"), "client": q.get("client"),
                    "price": q.get("price"), "days": q.get("days"),
                    "margin": q.get("margin"), "confidence": q.get("confidence"),
                    "status": r["status"], "rule_version": r["rule_version"],
                    "review_required": q.get("review_required")})
    return {"count": len(out), "items": out}
