"""Excel / CSV 导入（数据源设计 v1.0 §4.2 的落地）。

为什么导入不是"打开文件插库"：
1. 列名会改版 → 必须按模板版本校验首行，不匹配直接拒绝，不做模糊猜测。
2. 同一份表会被重复上传 → content_hash 幂等，重放不重复记账。
3. 脏行不能整批失败 → 逐行校验，好行进、坏行出回执，人工可修。
4. 导入必须可追溯 → 每次一个 batch，记录文件、hash、人、时间。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from datetime import datetime

from .db import rows, scalar

# 模板列（顺序无关，列名必须完全一致）
EFFORT_COLUMNS = ["项目编号", "人天类别", "人天", "月份", "说明"]

CATEGORY_ALIAS = {
    "base": "base", "计划内": "base", "基线": "base", "正常": "base",
    "scope": "scope", "范围": "scope", "变更": "scope", "需求新增": "scope",
    "rework": "rework", "返工": "rework", "返修": "rework",
    "efficiency": "efficiency", "效率": "efficiency", "损耗": "efficiency",
    "wait": "wait", "等待": "wait", "阻塞": "wait",
}
CATEGORY_LABEL = {
    "base": "计划内", "scope": "范围变更", "rework": "返工",
    "efficiency": "效率损耗", "wait": "等待阻塞",
}


def _decode_text(content: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文件编码（尝试 utf-8 / gbk 均失败）")


def read_table(filename: str, content: bytes) -> tuple[list[str], list[list]]:
    """返回 (表头, 数据行)。支持 .xlsx/.xlsm 与 .csv。"""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        all_rows = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
    elif name.endswith((".csv", ".txt")):
        text = _decode_text(content)
        all_rows = list(csv.reader(io.StringIO(text)))
    else:
        raise ValueError("仅支持 .xlsx / .xlsm / .csv")

    all_rows = [r for r in all_rows if any(c not in (None, "") for c in r)]
    if not all_rows:
        raise ValueError("文件为空")
    header = [str(c).strip() if c is not None else "" for c in all_rows[0]]
    return header, all_rows[1:]


def _norm_month(v) -> str | None:
    """接受 2026-10 / 2026/10 / 202610 / datetime。统一成 YYYY-MM。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m")
    s = str(v).strip().replace("/", "-").replace(".", "-")
    if len(s) == 6 and s.isdigit():
        s = f"{s[:4]}-{s[4:]}"
    parts = s.split("-")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        y, m = int(parts[0]), int(parts[1])
        if 2000 <= y <= 2100 and 1 <= m <= 12:
            return f"{y:04d}-{m:02d}"
    return None


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def preview(conn: sqlite3.Connection, filename: str, content: bytes) -> dict:
    """只校验不落库。返回逐行结果，供页面展示"导入前体检"。"""
    header, body = read_table(filename, content)

    missing = [c for c in EFFORT_COLUMNS if c not in header and c != "说明"]
    if missing:
        return {
            "ok": False,
            "fatal": f"表头不匹配，缺少列：{'、'.join(missing)}。"
                     f"当前表头：{'、'.join([h for h in header if h])}",
            "template": EFFORT_COLUMNS,
            "rows": [], "accepted": [], "rejected": [],
        }

    idx = {c: header.index(c) for c in EFFORT_COLUMNS if c in header}
    projects = {r["id"]: r["name"] for r in rows(conn, "SELECT id,name FROM projects")}
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen: dict[tuple, int] = {}

    for i, raw in enumerate(body, start=2):  # 第 1 行是表头
        cell = lambda c: raw[idx[c]] if idx.get(c) is not None and idx[c] < len(raw) else None
        pid = str(cell("项目编号") or "").strip()
        cat_raw = str(cell("人天类别") or "").strip()
        days = _num(cell("人天"))
        month = _norm_month(cell("月份"))
        note = str(cell("说明") or "").strip()

        errs = []
        if not pid:
            errs.append("项目编号为空")
        elif pid not in projects:
            errs.append(f"项目编号 {pid} 不存在")
        cat = CATEGORY_ALIAS.get(cat_raw.lower()) or CATEGORY_ALIAS.get(cat_raw)
        if not cat:
            errs.append(f"人天类别「{cat_raw}」不在枚举内（base/scope/rework/efficiency/wait）")
        if days is None:
            errs.append("人天不是数字")
        elif days <= 0:
            errs.append("人天必须大于 0")
        elif days > 200:
            errs.append("单人单月人天 > 200，疑似单位错误（小时/人天混用）")
        if not month:
            errs.append("月份格式无法识别（应为 YYYY-MM）")

        key = (pid, cat, month, note)
        if not errs and key in seen:
            errs.append(f"与第 {seen[key]} 行重复")

        if errs:
            rejected.append({"line": i, "msg": "；".join(errs),
                             "raw": [str(c) if c is not None else "" for c in raw][:6]})
        else:
            seen[key] = i
            accepted.append({"line": i, "project_id": pid, "project": projects[pid],
                             "category": cat, "category_label": CATEGORY_LABEL[cat],
                             "days": days, "month": month, "note": note})

    digest = hashlib.sha256(
        json.dumps([[a["project_id"], a["category"], a["days"], a["month"], a["note"]]
                    for a in accepted], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:12]

    dup = rows(conn, "SELECT id,created_at FROM ingest_batches "
                     "WHERE content_hash=? AND status='committed'", (digest,))
    return {
        "ok": not rejected or True,
        "fatal": None,
        "template": EFFORT_COLUMNS,
        "header": header,
        "content_hash": digest,
        "duplicate_of": dup[0]["id"] if dup else None,
        "accepted": accepted,
        "rejected": rejected,
        "rows": accepted + rejected,
        "stats": {
            "total": len(body),
            "ok": len(accepted),
            "bad": len(rejected),
            "days": round(sum(a["days"] for a in accepted), 1),
        },
    }


def commit_effort(conn: sqlite3.Connection, filename: str, content: bytes,
                  actor: str, batch_id: str | None = None) -> dict:
    """校验通过后落库。同一个 content_hash 不会重复写。"""
    res = preview(conn, filename, content)
    if res.get("fatal"):
        return {"ok": False, "fatal": res["fatal"]}
    if res.get("duplicate_of"):
        return {"ok": False, "duplicate": True,
                "message": f"该文件已于批次 {res['duplicate_of']} 导入过（hash {res['content_hash']}），本次未重复写入"}
    if not res["accepted"]:
        return {"ok": False, "fatal": "没有可导入的有效行", "rejected": res["rejected"]}

    bid = batch_id or _next_batch_id(conn)
    conn.executemany(
        """INSERT INTO effort_records(project_id,requirement_id,category,days,work_month,note)
           VALUES(?,?,?,?,?,?)""",
        [(a["project_id"], None, a["category"], a["days"], a["month"],
          a["note"] or "Excel 导入（未填说明）") for a in res["accepted"]],
    )
    conn.execute(
        """INSERT INTO ingest_batches(id,source_id,filename,content_hash,rows_total,
           rows_ok,rows_bad,status,detail,created_at,created_by)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (bid, "S-CST-EXC-03", filename, res["content_hash"], res["stats"]["total"],
         res["stats"]["ok"], res["stats"]["bad"], "committed",
         json.dumps({"errors": [{"line": r["line"], "msg": r["msg"]} for r in res["rejected"]]},
                    ensure_ascii=False),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), actor),
    )
    conn.execute(
        "INSERT INTO audit_log(actor,action,target,payload,created_at) VALUES(?,?,?,?,?)",
        (actor, "excel_import", f"batch:{bid}",
         json.dumps({"file": filename, "ok": res["stats"]["ok"], "bad": res["stats"]["bad"],
                     "hash": res["content_hash"]}, ensure_ascii=False),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()

    touched = sorted({a["project_id"] for a in res["accepted"]})
    return {
        "ok": True,
        "batch": bid,
        "stats": res["stats"],
        "rejected": res["rejected"],
        "projects": touched,
        # 导入后立刻回显闭合率变化，证明"改一条记录，全站数字跟着变"
        "closure": _closure_after(conn, touched),
    }


def _closure_after(conn: sqlite3.Connection, project_ids: list[str]) -> list[dict]:
    from .analytics import variance

    out = []
    for pid in project_ids:
        p = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if not p:
            continue
        b = variance.breakdown(conn, p)
        out.append({"project": pid, "name": p["name"], "closeRate": b["closeRate"],
                    "unexplained": b.get("unexplained", 0), "total": b["total"],
                    "categorized": b.get("categorized")})
    return out


def _next_batch_id(conn: sqlite3.Connection) -> str:
    day = datetime.now().strftime("%Y%m%d")
    n = scalar(conn, "SELECT COUNT(*) FROM ingest_batches WHERE id LIKE ?", (f"BAT-{day}-%",), 0)
    return f"BAT-{day}-{n + 1:03d}"


def batches(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    out = []
    for r in rows(conn, """SELECT b.*, s.name AS source_name FROM ingest_batches b
                           LEFT JOIN data_sources s ON s.id=b.source_id
                           ORDER BY b.created_at DESC LIMIT ?""", (limit,)):
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d.get("detail") else {"errors": []}
        d["errors"] = d["detail"].get("errors", [])
        d["source_name"] = d.get("source_name") or d["source_id"]
        out.append(d)
    return out


def template_csv() -> bytes:
    """给用户的导入模板：带一行示例，避免第一次就因列名错被拒。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EFFORT_COLUMNS)
    w.writerow(["P-2026-014", "rework", "3.5", "2026-10", "会员权益重复交付"])
    w.writerow(["P-2026-014", "wait", "1", "2026-10", "等客户确认接口文档"])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")
