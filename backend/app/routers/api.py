"""业务接口。读接口开放，写接口需要 pm/admin 角色。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from .. import ingest
from ..analytics import (config_engine, config_engine_v4, kpi, llm_derive,
                         lineage, sources, survey_import, variance, version)
from ..llm import config_store as llm_config
from ..db import connect, rows, scalar
from ..serializers import (
    bootstrap as build_bootstrap,
    lineage_payload,
    metrics_payload,
    module_payload,
    survey_payload,
    variance_payload,
    version_payload,
)
from .auth import require_roles

router = APIRouter(prefix="/api", tags=["api"])


def get_conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _audit(conn: sqlite3.Connection, actor: str, action: str, target: str, payload: dict):
    conn.execute(
        "INSERT INTO audit_log(actor,action,target,payload,created_at) VALUES(?,?,?,?,?)",
        (actor, action, target, json.dumps(payload, ensure_ascii=False),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()


# ---------------------------------------------------------------- 首屏

@router.get("/bootstrap")
def bootstrap(conn: sqlite3.Connection = Depends(get_conn)):
    return build_bootstrap(conn)


@router.get("/overview")
def overview(conn: sqlite3.Connection = Depends(get_conn)):
    return kpi.overview(conn)


# ---------------------------------------------------------------- 项目

@router.get("/projects")
def projects(conn: sqlite3.Connection = Depends(get_conn),
             status: str | None = Query(None, description="按状态过滤")):
    data = kpi.active_projects(conn) + kpi.closed_projects(conn)
    if status:
        data = [p for p in data if p["status"] == status]
    return {"count": len(data), "items": data}


@router.get("/projects/{project_id}")
def project_detail(project_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if p is None:
        raise HTTPException(404, "项目不存在")
    base = kpi.project_metrics(conn, p)
    base["effort"] = [
        {"category": r["category"], "days": r["days"], "month": r["work_month"], "note": r["note"]}
        for r in rows(conn, """SELECT * FROM effort_records WHERE project_id=?
            ORDER BY work_month, category""", (project_id,))
    ]
    base["changes"] = [
        {"id": r["id"], "cause": r["cause_code"], "days": r["net_days"],
         "billing": r["billing"], "status": r["status"]}
        for r in rows(conn, "SELECT * FROM changes WHERE project_id=?", (project_id,))
    ]
    return base


@router.get("/projects/{project_id}/variance")
def project_variance(project_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if p is None:
        raise HTTPException(404, "项目不存在")
    if p["detail_level"] != "full":
        raise HTTPException(409, "该项目只有工时汇总，未做五项分解；请先补录分类工时")
    b = variance.breakdown(conn, p)
    s0 = float(p["estimate_days"])
    s1 = s0 + float(scalar(conn, """SELECT SUM(days) FROM effort_records
        WHERE project_id=? AND category='scope'""", (project_id,), 0.0))
    b.update({"s0": s0, "s1": round(s1, 1), "p0": 1.0,
              "p1": round(s1 / b["act"], 3) if b["act"] else 0})
    return b


@router.post("/projects/{project_id}/variance/recompute")
def recompute_variance(project_id: str,
                       conn: sqlite3.Connection = Depends(get_conn),
                       user: dict = Depends(require_roles("pm", "admin"))):
    """重算分解并留痕。分解本身是幂等的，重算的意义在于把这次核对写进审计。"""
    p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if p is None:
        raise HTTPException(404, "项目不存在")
    b = variance.breakdown(conn, p)
    _audit(conn, user["name"], "variance.recompute", project_id,
           {"closure": b["closeRate"], "total": b["total"], "items": len(b["items"])})
    return {"ok": True, "project": project_id, "closure": b["closeRate"],
            "total": b["total"], "audited_by": user["name"], "detail": b}


# ---------------------------------------------------------------- 模块

@router.get("/modules/{key}")
def module(key: str, conn: sqlite3.Connection = Depends(get_conn)):
    data = module_payload(conn, key)
    if not data:
        raise HTTPException(404, "模块不存在")
    return data


# ---------------------------------------------------------------- 专题

@router.get("/topics/variance")
def topic_variance(conn: sqlite3.Connection = Depends(get_conn)):
    return variance_payload(conn)


@router.get("/topics/lineage")
def topic_lineage(conn: sqlite3.Connection = Depends(get_conn)):
    return {"chains": lineage_payload(conn), "stats": lineage.rework_stats(conn),
            "causes": lineage.cause_distribution(conn)}


@router.get("/topics/lineage/{req_id}")
def topic_lineage_detail(req_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    c = lineage.chain(conn, req_id)
    if c is None:
        raise HTTPException(404, "需求不存在")
    return c


@router.get("/topics/version")
def topic_version(conn: sqlite3.Connection = Depends(get_conn)):
    return version_payload(conn)


@router.get("/hypotheses")
def hypotheses(conn: sqlite3.Connection = Depends(get_conn)):
    return version.hypotheses(conn)


@router.post("/hypotheses/{hid}/review")
def review_hypothesis(hid: str, payload: dict = Body(...),
                      conn: sqlite3.Connection = Depends(get_conn),
                      user: dict = Depends(require_roles("pm", "admin"))):
    """回看假设：验证 / 否证，必须写结论，否则假设卡永远停在「待验证」。"""
    status = payload.get("status")
    if status not in ("已验证", "已否证"):
        raise HTTPException(400, "status 只能是 已验证 / 已否证")
    outcome = (payload.get("outcome") or "").strip()
    if not outcome:
        raise HTTPException(400, "必须填写结论，否则回看没有意义")
    conn.execute("UPDATE hypotheses SET status=?, reviewed_at=?, outcome=? WHERE id=?",
                 (status, datetime.now().date().isoformat(), outcome, hid))
    _audit(conn, user["name"], "hypothesis.review", hid, {"status": status})
    return {"ok": True, "id": hid, "status": status}


# ---------------------------------------------------------------- 问卷与配置

@router.get("/survey/{template_id}")
def survey(template_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    tpl = conn.execute("SELECT * FROM survey_templates WHERE id=?", (template_id,)).fetchone()
    if tpl is None:
        raise HTTPException(404, "问卷不存在")
    if template_id == "SV-2026-021":
        return survey_payload(conn)
    qs = rows(conn, "SELECT * FROM survey_questions WHERE template_id=? ORDER BY order_no",
              (template_id,))
    answers = {r["code"]: None if r["value"] is None else json.loads(r["value"])
               for r in rows(conn, "SELECT * FROM survey_answers WHERE template_id=?",
                             (template_id,))}
    return {
        "id": tpl["id"], "client": tpl["client"], "progress": len(answers),
        "total": tpl["total_questions"], "rule_version": kpi.setting(conn, "rules_version"),
        "questions": [
            {"id": q["code"], "t": q["text"], "opts": json.loads(q["options"]),
             "def": answers.get(q["code"])}
            for q in qs
        ],
    }


# v4 问卷的题号特征：出现任意一个就走报事报修 81 条配置引擎
V4_KEYS = {"Q1", "Q2", "Q8", "Q20", "Q22", "Q28", "Q30", "Q36", "Q42",
           "Q47", "Q51", "Q56", "Q65", "Q70", "Q74", "Q79", "Q84", "Q89",
           "Q95", "Q99"}


def _is_v4(answers: dict) -> bool:
    return any(k in answers for k in V4_KEYS)


@router.post("/config/derive")
def derive(payload: dict = Body(...)):
    """确定性推导：同一份答案，输出 hash 必然相同。不写库、不调模型。

    v3（6 题示例）与 v4（报事报修 81 条）按入参题号自动分流，老调用不受影响。
    """
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else \
        {k: v for k, v in payload.items() if k not in ("template", "overrides")}
    ov = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else None
    if payload.get("template") == "survey_96" or _is_v4(answers):
        return config_engine_v4.derive(answers, ov)
    result = config_engine.derive(answers)
    result["output_hash"] = config_engine.output_hash(result)
    return result


@router.post("/config/import-survey")
async def import_survey(file: UploadFile = File(...)):
    """导入客户回填的问卷 docx，只解析出答案，**不做推导**。

    前端拿到 answers 后只填表单，等用户点「确认计算」才走规则。
    解析不出来的题绝不补值，全部列进 report.skipped 让实施逐条核对。
    """
    name = file.filename or "survey.docx"
    if not name.lower().endswith((".docx", ".docm")):
        raise HTTPException(400, "只支持 .docx（Word 2007+）文件；.doc 请先另存为 .docx")
    content = await file.read()
    if not content:
        raise HTTPException(400, "文件是空的")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "文件超过 20MB，请确认是否传错文件")
    try:
        result = survey_import.parse(content)
    except Exception as e:                       # noqa: BLE001
        raise HTTPException(400, f"解析失败：{type(e).__name__}: {e}")
    result["filename"] = name
    return result


DRAFT_ID = "survey_96"


@router.get("/config/draft")
def get_draft(conn: sqlite3.Connection = Depends(get_conn)):
    """读草稿。只读，不推导——要不要算，等用户点「确认计算」。"""
    row = conn.execute("SELECT * FROM config_drafts WHERE template_id = ?", (DRAFT_ID,)).fetchone()
    if not row:
        return {"exists": False}
    return {
        "exists": True,
        "answers": json.loads(row["answers"]),
        "overrides": json.loads(row["overrides"]),
        "saved_at": row["saved_at"],
        "saved_by": row["saved_by"],
    }


@router.post("/config/draft")
def save_draft(payload: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn)):
    """「仅保存」：把当前答案与人工确认值存下来，**不触发推导**。

    与 /survey/{id}/answers 的区别：那张表存的是"已确认、参与推导"的答案，
    这里存的是待确认事项改到一半的草稿，落盘是为了刷新页面不丢。
    """
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
    overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else {}
    saved_by = payload.get("by") or "实施顾问"
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO config_drafts(template_id, answers, overrides, saved_at, saved_by)
           VALUES(?,?,?,?,?)
           ON CONFLICT(template_id) DO UPDATE SET
             answers=excluded.answers, overrides=excluded.overrides,
             saved_at=excluded.saved_at, saved_by=excluded.saved_by""",
        (DRAFT_ID, json.dumps(answers, ensure_ascii=False),
         json.dumps(overrides, ensure_ascii=False), now, saved_by))
    conn.commit()
    return {"ok": True, "saved_at": now, "answers": len(answers), "overrides": len(overrides)}


@router.delete("/config/draft")
def clear_draft(conn: sqlite3.Connection = Depends(get_conn)):
    conn.execute("DELETE FROM config_drafts WHERE template_id = ?", (DRAFT_ID,))
    conn.commit()
    return {"ok": True}


@router.post("/config/derive/stream")
def derive_stream(payload: dict = Body(...)):
    """推导过程流式返回（SSE）。

    为什么要流式：81 条配置一次算完只要几十毫秒，但"啪一下全出来"客户不敢信。
    这里把 12 个推导步骤逐步吐出来，每步的数字都是真实计算结果（规则数、
    配置数、耗时、命中的冲突编号），前端照着渲染就有可追溯的过程感。

    derive() 与本接口消费同一个生成器，不存在过程与结果对不上的可能。
    """
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else \
        {k: v for k, v in payload.items() if k not in ("template", "overrides")}
    ov = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else None

    def gen():
        for ev in config_engine_v4.steps(answers, ov):
            yield f"event: {ev['type']}\ndata: " \
                  f"{json.dumps(ev, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


# ---------------------------------------------------------------- 大模型配置
#
# 页面里改，改完立即生效。优先级：DB 配置 > 环境变量 > 离线。

@router.get("/llm/config")
def get_llm_config(conn: sqlite3.Connection = Depends(get_conn)):
    """当前生效的模型配置（api_key 只回脱敏串）。"""
    return llm_config.public(conn)


@router.post("/llm/config")
def save_llm_config(payload: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn)):
    provider = str(payload.get("provider") or "deepseek").strip()
    base_url = str(payload.get("base_url") or "").strip()
    model = str(payload.get("model") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    try:
        temp = float(payload.get("temperature", 0.2))
    except (TypeError, ValueError):
        raise HTTPException(400, "temperature 必须是数字")
    if not base_url:
        raise HTTPException(400, "base_url 不能为空")
    if not model:
        raise HTTPException(400, "模型名不能为空")
    if not api_key:
        raise HTTPException(400, "API Key 不能为空")

    # 用户只改 model 不重填 key 时，前端传的是脱敏串，按原值处理
    old = llm_config.load(conn)
    if old and ("*" in api_key and api_key == llm_config.mask_key(old["api_key"])):
        api_key = old["api_key"]

    try:
        llm_config.save(conn, provider=provider, base_url=base_url, model=model,
                        api_key=api_key, temperature=max(0.0, min(1.5, temp)),
                        by=payload.get("by") or "实施顾问")
    except ValueError as e:
        # 脏 key（混了中文/零宽字符）不许入库：存进去之后每次推导都会
        # 炸成一句看不懂的 ascii codec 错，还查不出原因
        raise HTTPException(400, str(e))
    return {"ok": True, **llm_config.public(conn)}


@router.post("/llm/config/test")
def test_llm_config(payload: dict = Body(default={}),
                    conn: sqlite3.Connection = Depends(get_conn)):
    """连通性测试：参数没给的项走已保存配置，方便"只测一下刚填的 key"。"""
    key = payload.get("api_key") or None
    # 前端把脱敏串原样传回来时按"用已保存的 key"处理
    if key and "*" in str(key):
        key = None
    return llm_config.test(conn,
                           base_url=payload.get("base_url") or None,
                           model=payload.get("model") or None,
                           api_key=key)


@router.get("/llm/models")
def list_llm_models(conn: sqlite3.Connection = Depends(get_conn)):
    """问服务端到底有哪些模型名可用。

    模型名是厂商说了算的活物，写死迟早 400。页面拿这个结果校推荐列表，
    顺便告诉用户「你保存的那个名字现在还在不在」。
    """
    return llm_config.probe_models(conn)


@router.delete("/llm/config")
def clear_llm_config(conn: sqlite3.Connection = Depends(get_conn)):
    llm_config.clear(conn)
    return {"ok": True, **llm_config.public(conn)}


@router.post("/config/derive/llm-stream")
def derive_llm_stream(payload: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn)):
    """大模型推导（SSE）：逐组真实调用模型，模型出内容、规则兜底。

    与 /config/derive/stream（纯规则）并列，前端可切换。
    """
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else \
        {k: v for k, v in payload.items() if k not in ("template", "overrides")}
    ov = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else None

    def gen():
        try:
            for ev in llm_derive.steps(answers, ov, conn):
                yield f"event: {ev['type']}\ndata: " \
                      f"{json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
        except Exception as e:                   # noqa: BLE001
            err = {"type": "error", "code": "llm_failed",
                   "message": f"{type(e).__name__}: {e}"}
            yield f"event: error\ndata: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@router.get("/config/v4/template")
def config_template_v4():
    """96 题问卷模板（20 部分 / 105 个作答项）。"""
    return config_engine_v4.survey_template()


@router.get("/config/v4/rules")
def config_rules_v4(group: str | None = None):
    items = config_engine_v4.rule_index()
    if group:
        items = [r for r in items if r["group"] == group]
    return {"version": config_engine_v4.RULE_VERSION,
            "total": len(items), "rules": items}


@router.get("/config/v4/conflicts")
def config_conflicts_v4():
    """14 条可检出的跨题冲突（不含触发状态，触发状态在 derive 的 issues 里）。"""
    return {"version": config_engine_v4.RULE_VERSION,
            "items": config_engine_v4.conflict_index()}


@router.get("/config/v4/items")
def config_items_v4():
    """81 条配置项的定义清单（编号 / 名称 / 分组）。"""
    return {"total": 81, "items": config_engine_v4.config_items()}


@router.get("/config/rules")
def rules():
    return {"version": config_engine.RULE_VERSION,
            "rules": [{"id": k, **v} for k, v in config_engine.RULES.items()]}


@router.post("/survey/{template_id}/answers")
def save_answers(template_id: str, payload: dict = Body(...),
                 conn: sqlite3.Connection = Depends(get_conn),
                 user: dict = Depends(require_roles("pm", "admin"))):
    """保存答案并立即重推。答案变了配置就变，中间不允许人工改配置。"""
    answers = payload.get("answers", payload)
    for code, val in answers.items():
        conn.execute(
            """INSERT INTO survey_answers(template_id,code,value,answered_at,answered_by)
               VALUES(?,?,?,?,?)
               ON CONFLICT(template_id,code) DO UPDATE SET
                 value=excluded.value, answered_at=excluded.answered_at,
                 answered_by=excluded.answered_by""",
            (template_id, code, None if val is None else json.dumps(val, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds"), user["name"]))
    conn.commit()
    result = config_engine.derive(answers)
    _audit(conn, user["name"], "config.derive", template_id,
           {"rule_version": config_engine.RULE_VERSION, "tbd": result["stats"]["tbd"]})
    result["output_hash"] = config_engine.output_hash(result)
    return result


@router.get("/config/export")
def export_config(template_id: str = "SV-2026-021",
                  conn: sqlite3.Connection = Depends(get_conn)):
    data = survey_payload(conn)
    answers = {q["id"]: q["def"] for q in data["questions"]}
    result = config_engine.derive(answers)
    return {
        "client": data["client"],
        "rule_version": config_engine.RULE_VERSION,
        "input_hash": result["input_hash"],
        "output_hash": config_engine.output_hash(result),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "configs": result["configs"],
        "issues": result["issues"],
        "flow": result["flow"],
        "stats": result["stats"],
    }


# ---------------------------------------------------------------- 口径与审计

@router.get("/metrics")
def metrics(conn: sqlite3.Connection = Depends(get_conn)):
    return {"version": kpi.setting(conn, "kpi_version"), "items": metrics_payload(conn)}


@router.get("/audit")
def audit(limit: int = 20, conn: sqlite3.Connection = Depends(get_conn)):
    return [
        {"id": r["id"], "actor": r["actor"], "action": r["action"], "target": r["target"],
         "payload": json.loads(r["payload"] or "{}"), "at": r["created_at"]}
        for r in rows(conn, "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    ]


@router.get("/health")
def health(conn: sqlite3.Connection = Depends(get_conn)):
    return {
        "ok": True,
        "projects": conn.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"],
        "requirements": conn.execute("SELECT COUNT(*) c FROM requirements").fetchone()["c"],
        "tickets": conn.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"],
    }


# ---------------------------------------------------------------- 数据源治理

@router.get("/sources")
def list_sources(type: str | None = Query(None), module: str | None = Query(None),
                 conn: sqlite3.Connection = Depends(get_conn)):
    return sources.registry(conn, type, module)


@router.get("/sources/summary")
def sources_summary(conn: sqlite3.Connection = Depends(get_conn)):
    return sources.summary(conn)


@router.get("/sources/matrix")
def sources_matrix(conn: sqlite3.Connection = Depends(get_conn)):
    return sources.matrix(conn)


@router.get("/sources/metrics")
def sources_metrics(conn: sqlite3.Connection = Depends(get_conn)):
    return sources.metric_index(conn)


@router.get("/sources/metrics/{metric_id}/lineage")
def metric_lineage(metric_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    data = sources.lineage(conn, metric_id)
    if not data["found"]:
        raise HTTPException(404, "该指标尚未登记数据源血缘")
    return data


@router.get("/sources/{sid}")
def source_detail(sid: str, conn: sqlite3.Connection = Depends(get_conn)):
    d = sources.one(conn, sid)
    if d is None:
        raise HTTPException(404, "数据源不存在")
    return d


@router.post("/sources/{sid}/probe")
def probe_source(sid: str, conn: sqlite3.Connection = Depends(get_conn),
                 user: dict = Depends(require_roles("pm", "admin"))):
    """真实探活。

    只有登记了 endpoint 的源才会真的发请求；内网源没有地址就返回 not_configured，
    不编造"连通正常"。这一点必须诚实：假的绿灯比红灯危害大。
    """
    d = sources.one(conn, sid)
    if d is None:
        raise HTTPException(404, "数据源不存在")
    if d["type"] == "excel":
        return {"source": sid, "state": "n/a", "detail": "文件导入源无端点，靠批次回执校验"}
    if not d.get("endpoint"):
        return {"source": sid, "state": "not_configured",
                "detail": "未配置 endpoint（内网系统需在部署环境配置地址与凭据）"}

    import time

    import httpx

    t0 = time.time()
    try:
        r = httpx.get(d["endpoint"], timeout=8.0, follow_redirects=True,
                      headers={"User-Agent": "sdlc-datasource-probe/1.0"})
        ms = int((time.time() - t0) * 1000)
        state = "ok" if r.status_code < 400 else "http_error"
        detail = f"HTTP {r.status_code} · {ms} ms"
        if r.status_code >= 400:
            detail += "（端点可达但返回错误，需检查凭据或路径）"
    except Exception as e:  # 网络错误一律如实返回，不吞
        ms = int((time.time() - t0) * 1000)
        state, detail = "unreachable", f"{type(e).__name__}: {str(e)[:120]}（{ms} ms）"

    conn.execute("UPDATE data_sources SET last_sync=? WHERE id=?",
                 (datetime.now().strftime("%Y-%m-%d %H:%M"), sid))
    _audit(conn, user["name"], "source.probe", sid, {"state": state, "detail": detail})
    return {"source": sid, "state": state, "detail": detail}


# ---------------------------------------------------------------- Excel 导入

@router.get("/ingest/batches")
def ingest_batches(limit: int = 20, conn: sqlite3.Connection = Depends(get_conn)):
    return ingest.batches(conn, limit)


@router.get("/ingest/template")
def ingest_template():
    from fastapi.responses import Response

    return Response(
        content=ingest.template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="effort_import_template.csv"'},
    )


@router.post("/ingest/effort/preview")
async def ingest_preview(file: UploadFile = File(...),
                         conn: sqlite3.Connection = Depends(get_conn)):
    """导入前体检：逐行校验，坏行带行号返回，不落库。"""
    content = await file.read()
    try:
        return ingest.preview(conn, file.filename or "upload.csv", content)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/ingest/effort")
async def ingest_effort(file: UploadFile = File(...),
                        conn: sqlite3.Connection = Depends(get_conn),
                        user: dict = Depends(require_roles("pm", "admin"))):
    """落库。content_hash 保证同一文件不会被重复记账。"""
    content = await file.read()
    try:
        return ingest.commit_effort(conn, file.filename or "upload.csv", content, user["name"])
    except ValueError as e:
        raise HTTPException(400, str(e))
