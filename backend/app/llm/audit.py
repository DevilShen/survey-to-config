"""LLM 调用审计。

每次调用（成功、失败、离线降级）都落一行。这张表本身就是账单与证据：
哪个节点用了模型、花了多少 token、耗时多少、为什么降级，全部可回查。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def record_call(conn: sqlite3.Connection, *, run_id=None, turn_id=None,
                node: str, purpose: str, model: str, prompt: str = "",
                tokens_in: int = 0, tokens_out: int = 0, latency_ms: float = 0.0,
                status: str = "ok") -> int:
    """落一条调用记录，返回 call_id。prompt 只存指纹不存全文。"""
    cur = conn.execute(
        """INSERT INTO llm_calls(run_id,turn_id,node,purpose,model,prompt_hash,
               tokens_in,tokens_out,latency_ms,status,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, turn_id, node, purpose, model, _hash(prompt),
         tokens_in, tokens_out, round(latency_ms, 1), status,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    return int(cur.lastrowid)


def calls_of(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    from ..db import rows
    return [dict(r) for r in rows(
        conn, "SELECT * FROM llm_calls WHERE run_id=? ORDER BY id", (run_id,))]


def recent_calls(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    from ..db import rows
    return [dict(r) for r in rows(
        conn, "SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?", (limit,))]
