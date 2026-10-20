"""大模型配置的存取与连通性测试。

优先级：数据库配置 > 环境变量 > 离线。
放 DB 是为了「页面能改，改完立刻生效」，不用重启、不用改 .env——
实施到客户现场最烦的就是"要改配置得找运维重启服务"。
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime

import httpx

from .client import DEFAULTS, LLMError, OpenAICompat

# 支持的厂商：都是 OpenAI 兼容协议，差别只在 base_url
PROVIDERS = [
    {"k": "deepseek", "t": "DeepSeek", "base": "https://api.deepseek.com",
     "models": ["deepseek-flash", "deepseek-v4-pro"],
     "hint": "推荐。中文理解强、便宜、OpenAI 协议兼容。"
             "deepseek-flash 快且省，日常配置推导用这个；deepseek-v4-pro 更强但慢一些。"
             "（deepseek-chat 已不在 DeepSeek 的可用模型列表里，填了会 400。）"},
    {"k": "openai", "t": "OpenAI", "base": "https://api.openai.com/v1",
     "models": ["gpt-4o-mini", "gpt-4o"],
     "hint": "需要境外网络与信用卡，国内项目实施通常不用。"},
    {"k": "custom", "t": "其他 OpenAI 兼容服务", "base": "",
     "models": [],
     "hint": "通义 / 豆包 / 本地 vLLM 都行，填对 base_url 与模型名即可。"},
]

ROW_ID = "default"


def mask_key(key: str) -> str:
    """只回形如 sk-1****abcd 的脱敏串。前端永远拿不到明文。"""
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 8:
        return "*" * len(k)
    return f"{k[:4]}{'*' * 6}{k[-4:]}"


def load(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM llm_configs WHERE id = ?", (ROW_ID,)).fetchone()
    if not row:
        return None
    return {
        "provider": row["provider"], "base_url": row["base_url"],
        "model": row["model"], "api_key": row["api_key"],
        "temperature": row["temperature"], "updated_at": row["updated_at"],
        "updated_by": row["updated_by"], "source": "db",
    }


def _bad_chars(v: str) -> list[int]:
    return [i for i, ch in enumerate(v or "") if ord(ch) > 127]


def save(conn: sqlite3.Connection, *, provider: str, base_url: str, model: str,
         api_key: str, temperature: float = 0.2, by: str = "实施顾问") -> dict:
    """落库前校验：key / base / 模型名只能是 ASCII。

    脏 key 一旦存进去，后面每次推导都会在 httpx 里炸成一句
    「ascii codec can't encode...」，谁也看不出是 key 里混了中文。
    宁可在保存这一步就拒绝，并说清楚是第几位。
    """
    for label, val in (("API Key", api_key), ("Base URL", base_url), ("模型名", model)):
        bad = _bad_chars(val)
        if bad:
            i = bad[0]
            raise ValueError(
                f"{label} 里有 {len(bad)} 个非 ASCII 字符（第 {i + 1} 位是 U+{ord(val[i]):04X}）。"
                f"请重新复制纯英文的那串，中文、全角空格、零宽字符都不行。")

    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO llm_configs(id,provider,base_url,model,api_key,temperature,updated_at,updated_by)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             provider=excluded.provider, base_url=excluded.base_url, model=excluded.model,
             api_key=excluded.api_key, temperature=excluded.temperature,
             updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
        (ROW_ID, provider, base_url.rstrip("/"), model, api_key, temperature, now, by))
    conn.commit()
    return load(conn)


def clear(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM llm_configs WHERE id = ?", (ROW_ID,))
    conn.commit()


def effective(conn: sqlite3.Connection) -> dict:
    """当前生效配置（不脱敏，内部用）。来源优先级：DB > 环境变量 > 无。"""
    row = load(conn)
    if row and row["api_key"]:
        return row
    key = os.environ.get("SDLC_LLM_KEY")
    if key:
        return {
            "provider": "env", "base_url": os.environ.get("SDLC_LLM_BASE", DEFAULTS["base"]),
            "model": os.environ.get("SDLC_LLM_MODEL", DEFAULTS["model"]),
            "api_key": key, "temperature": 0.2, "source": "env",
            "updated_at": None, "updated_by": None,
        }
    return {"provider": "", "base_url": DEFAULTS["base"], "model": DEFAULTS["model"],
            "api_key": "", "temperature": 0.2, "source": "none",
            "updated_at": None, "updated_by": None}


def public(conn: sqlite3.Connection) -> dict:
    """给前端的视图：脱敏、带状态。"""
    e = effective(conn)
    online = bool(e["api_key"])
    return {
        "provider": e["provider"],
        "base_url": e["base_url"],
        "model": e["model"],
        "temperature": e["temperature"],
        "has_key": online,
        "key_masked": mask_key(e["api_key"]) if online else "",
        "source": e["source"],
        "updated_at": e["updated_at"],
        "updated_by": e["updated_by"],
        "online": online,
        "providers": PROVIDERS,
    }


def test(conn: sqlite3.Connection, *, provider=None, base_url=None, model=None,
         api_key=None, temperature=0.0) -> dict:
    """发一条最小请求，验证 key / base / model 三件事都对。

    参数里没给的项走已保存配置，这样"只测一下刚填的 key"不用先保存。
    """
    e = effective(conn)
    base = (base_url or e["base_url"] or "").rstrip("/")
    key = (api_key or e["api_key"] or "").strip()
    mdl = model or e["model"]
    if not key:
        return {"ok": False, "error": "没有 API Key：先填写并保存，或设置环境变量 SDLC_LLM_KEY"}
    if not base:
        return {"ok": False, "error": "没有 base_url"}

    client = OpenAICompat(base_url=base, api_key=key, model=mdl, timeout=30.0)
    t0 = time.perf_counter()
    try:
        r = client.chat_json(
            system="你是一个连通性测试端点。严格输出 JSON。",
            user='输出 {"ok": true, "echo": "pong"}，不要任何多余内容。',
            temperature=0.0)
    except (LLMError, Exception) as exc:          # noqa: BLE001
        return {"ok": False, "error": str(exc), "base_url": base, "model": mdl,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
    return {
        "ok": True,
        "base_url": base, "model": r.model or mdl,
        "latency_ms": round(r.latency_ms, 1),
        "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
        "sample": (r.text or "")[:160],
    }


def probe_models(conn: sqlite3.Connection, *, base_url=None, api_key=None) -> dict:
    """拉服务端真实可用的模型名，用来校页面上的推荐列表。

    参数没给的走已保存配置；失败就把原因原样带回去（401 / base 错 / 不给列），
    不做「请求失败请重试」这种废话包装。
    """
    e = effective(conn)
    base = (base_url or e["base_url"] or "").rstrip("/")
    key = (api_key or e["api_key"] or "").strip()
    if not base:
        return {"ok": False, "models": [], "error": "没有 base_url"}
    if not key:
        return {"ok": False, "models": [], "error": "没有 API Key"}

    client = OpenAICompat(base_url=base, api_key=key, model=e["model"] or "", timeout=15.0)
    try:
        models = client.list_models()
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "models": [], "base_url": base, "error": str(exc)}
    return {"ok": True, "models": models, "base_url": base,
            "saved_model": e["model"],
            "saved_model_ok": (e["model"] in models) if models else None}


__all__ = ["PROVIDERS", "load", "save", "clear", "effective", "public", "test",
           "probe_models", "mask_key"]
