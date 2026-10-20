"""大模型驱动的配置推导：模型出内容，规则做校验与兜底。

和 config_engine_v4（纯规则）的分工：

    config_engine_v4  规则算出 81 条 → 这是「事实底座」
    llm_derive        逐组把「答案 + 规则草案 + 配置清单」交给模型 → 模型给最终值与说明

为什么不让模型从零编：81 条里任何一条漏掉或编造，交付时就说不清。
所以模型只负责「按清单填值并说明依据」，code 集合由清单锁死，
模型漏的、给错的、格式坏的，一律回落规则值并标记 `by='rule'`。

每一次模型调用都记 tokens / 耗时，流式事件里逐组回显——不是假进度。
"""
from __future__ import annotations

import json
import time

from ..llm.client import LLMError, SchemaError, get_client
from . import config_engine_v4 as E
from .rules.base import GROUP_TITLES

# 每组最多给模型看多少道题的答案（表格类只给摘要，避免 prompt 过长）
RETRY = 1      # 单批失败重试次数（模型偶尔返回空 content，重试一次基本就好）
BATCH = 8      # 每批最多几条配置（20 条以上的组必须拆，否则 JSON 会被截断）
MAX_Q = 26

SYSTEM = """你是物业报事报修 SaaS 系统的实施配置专家。客户刚填完需求调查问卷，
你要把问卷答案翻译成系统配置值。

铁律：
1. 只能填「配置项清单」里出现的 code，不要新增、不要遗漏、不要改编号。
2. 值必须具体可执行：写「30 分钟」「待接收 → 待处理 → 待评价」「按服务项固定价」，
   不要写「根据实际情况」「按需设置」这类没法落地的词。
3. 问卷答案里没有依据的，值写「待确认（缺：具体缺什么）」，绝不编造。
4. note 里写依据，形如「第 38 题：整体 24 小时」。
5. desc 写这条配置在系统里怎么落地、影响什么（一句 20-40 字，给实施看），
   例如「服务时段是超时计时的有效区间，时段外耗时不累加」。
6. 只输出 JSON：{"items":[{"code":"C01","v":"...","note":"...","desc":"..."}]}"""


def _fmt_value(v, q) -> str:
    """把答案压成一行可读文本，表格题只给行数与首行。"""
    if v is None or v == "":
        return "（未填）"
    if isinstance(v, list):
        if not v:
            return "（未填）"
        if isinstance(v[0], dict):
            head = "；".join(f"{k}：{x}" for k, x in list(v[0].items())[:4])
            return f"{len(v)} 行表格，首行 {head}"
        opts = {o["v"]: o["l"] for o in (q.get("options") or [])}
        return "、".join(opts.get(x, str(x)) for x in v)
    if isinstance(v, dict):
        if "start" in v:
            return f"{v.get('start') or '?'} – {v.get('end') or '?'}"
        unit = {u["v"]: u["l"] for u in (q.get("unit") or [])}
        val = v.get("v")
        return f"{'（未填数值）' if val is None else val} {unit.get(v.get('unit'), v.get('unit') or '')}".strip()
    opts = {o["v"]: o["l"] for o in (q.get("options") or [])}
    return opts.get(v, str(v))


def _group_prompt(group: str, configs: list[dict], questions: dict, ans: dict) -> str:
    """构造单组 prompt：答案摘要 + 配置清单（带规则草案）。"""
    codes = [c["code"] for c in configs]
    used_q: list[int] = []
    for r in E.rule_index():
        if r.get("group") == group:
            used_q.extend(r.get("inputs") or [])
    # 追问题号是 "27b" 这种字符串，统一收成整数（题号对齐用 no）
    nums: list[int] = []
    for x in used_q:
        try:
            nums.append(int(str(x).rstrip("bB")))
        except ValueError:
            continue
    used_q = sorted(set(nums))[:MAX_Q]

    lines = [f"【{GROUP_TITLES.get(group, group)}】", "", "问卷答案："]
    for no in used_q:
        qs = [q for q in questions.values() if int(q["no"]) == int(no)]
        if not qs:
            continue
        q = qs[0]
        lines.append(f"- 第 {no} 题 {q['text']}：{_fmt_value(ans.get(q['code']), q)}")
    if not used_q:
        lines.append("- （本组题目未作答）")

    lines += ["", f"配置项清单（逐条给值，code 不可改；本次只处理这 {len(configs)} 条）："]
    for c in configs:
        if c.get("conflict"):
            # 冲突项的值此刻是「待确认（A8…）」占位，别喂给模型当依据
            draft = f"（存在冲突 {c['conflict']}，请按问卷答案自行判断）"
        else:
            draft = str(c.get("v") or "")[:70] or "（无）"
        lines.append(f"- {c['code']} {c['name']} —— 规则草案：{draft}")
    lines += ["", f"请逐条输出 JSON，共 {len(configs)} 条，一条都不能少。"]
    return "\n".join(lines)


def _pick_items(data, allowed: set[str]) -> tuple[dict, list[str]]:
    """从模型输出里挑出合法的 code→值，返回 (结果, 被丢弃的说明)。"""
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise SchemaError("模型没返回 items 数组")
    ok: dict[str, dict] = {}
    bad: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            bad.append("非对象项")
            continue
        code = str(it.get("code") or "").strip().upper()
        v = it.get("v")
        if code not in allowed:
            bad.append(f"未知编号 {code or '?'}")
            continue
        if v is None or (isinstance(v, str) and not v.strip()):
            bad.append(f"{code} 值为空")
            continue
        ok[code] = {"v": str(v).strip()[:200],
                    "note": str(it.get("note") or "").strip()[:160],
                    "desc": str(it.get("desc") or "").strip()[:220]}
    return ok, bad


_FATAL = ("401", "403", "400", "404", "Authentication", "insufficient",
          "余额", "model name", "supported API model")


def _retryable(msg: str) -> bool:
    """这几类错重试没用：key 错、余额不足、模型名不存在、参数字段错。"""
    return not any(k in (msg or "") for k in _FATAL)


def steps(raw_answers: dict, overrides: dict | None = None, conn=None):
    """流式产出：规则底座 → 逐组模型生成 → 冲突/状态机/指纹。"""
    t0 = time.perf_counter()

    def _ms():
        return round((time.perf_counter() - t0) * 1000, 1)

    ans = E.normalize(raw_answers)
    total_steps = len(E.GROUPS) + 4   # 读取 + 7 组 + 冲突 + 状态机 + 指纹
    n = 1
    yield {"type": "step", "n": n, "total": total_steps, "key": "read",
           "title": "读取问卷答案",
           "detail": f"{len(ans)} 个作答项归一化完成", "rules": [], "codes": [], "ms": _ms()}

    # 1) 规则先算一遍：作为事实底座与兜底来源
    base = E.derive(ans, overrides)
    by_code = {c["code"]: c for c in base["configs"]}
    questions = {q["code"]: q for q in E.survey_template()["questions"]}
    cfg_meta = {c["code"]: c for c in E.config_items()}

    client = get_client(conn)
    model = getattr(client, "model", "offline")
    stat = {"model": model, "calls": 0, "ok": 0, "failed": 0,
            "tokens_in": 0, "tokens_out": 0, "latency_ms": 0.0, "fallback": 0}

    result_cfgs: list[dict] = []
    if not getattr(client, "online", False):
        # 没配模型时不装样子：直接把规则结果标成 engine=rule 返回
        yield {"type": "error", "code": "no_llm",
               "message": "尚未配置大模型：请到「模型设置」填写 DeepSeek API Key"}
        base["engine"] = "rule"
        base["llm"] = stat
        yield {"type": "done", "result": base, "total_ms": _ms()}
        return

    for g in E.GROUP_ORDER:
        n += 1
        cfgs = [c for c in base["configs"] if c["group"] == g]
        tried_at = time.perf_counter()
        got, bad, errs = {}, [], []

        # 一次要 20 条以上时，输出的 JSON 会长到被 max_tokens 截断，
        # 截断的后果是整组回落——看着像"模型不行"，其实是字数不够。
        # 所以按批要，一批 BATCH 条：批失败只影响这一批，其他批照常生效。
        batches = [cfgs[i:i + BATCH] for i in range(0, len(cfgs), BATCH)]
        for bi, batch in enumerate(batches):
            allowed = {c["code"] for c in batch}
            prompt = _group_prompt(g, batch, questions, ans)
            tag = f"第 {bi + 1} 批 " if len(batches) > 1 else ""
            for attempt in range(RETRY + 1):
                try:
                    # 重试时用 0 温度：既然上一次没给出来，就别再引入随机性
                    r = client.chat_json(SYSTEM, prompt,
                                         temperature=0.0 if attempt else None)
                    stat["calls"] += 1
                    stat["ok"] += 1
                    stat["tokens_in"] += r.tokens_in
                    stat["tokens_out"] += r.tokens_out
                    one, b2 = _pick_items(r.data, allowed)
                    got.update(one)
                    bad += b2
                    break
                except (LLMError, SchemaError) as exc:
                    stat["calls"] += 1
                    msg = str(exc)
                    # key 错、余额不足、模型名错：重试也是白花时间，直接记下来
                    if attempt < RETRY and _retryable(msg):
                        continue
                    stat["failed"] += 1
                    errs.append(tag + msg[:100])
                    break

        cost = round((time.perf_counter() - tried_at) * 1000, 1)
        stat["latency_ms"] = round(stat["latency_ms"] + cost, 1)
        err = "；".join(errs)
        allowed = {c["code"] for c in cfgs}

        # 合并：模型给的就用，没给的回落到规则值
        sample = []
        for c in cfgs:
            item = dict(c)
            if c["code"] in got:
                item["v"] = got[c["code"]]["v"]
                if got[c["code"]]["note"]:
                    item["note"] = got[c["code"]]["note"]
                if got[c["code"]].get("desc"):
                    item["desc"] = got[c["code"]]["desc"]
                item["by"] = "llm"
                # 模型给了确定值：状态回到已确认，冲突标记清掉，交给 finalize 重新判
                item["st"] = "ok"
                item.pop("conflict", None)
            else:
                item["by"] = "rule"
                stat["fallback"] += 1
            result_cfgs.append(item)
            if len(sample) < 3:
                sample.append(f"{c['code']}={item['v'][:18]}")

        detail = (f"{len(got)}/{len(cfgs)} 条由 {model} 生成 · {cost} ms"
                  + (f" · {len(batches)} 批" if len(batches) > 1 else "")
                  + (f" · 回落规则 {len(cfgs) - len(got)} 条" if len(got) < len(cfgs) else "")
                  + (f" · 失败：{err}" if err else "")
                  + (f" · 丢弃 {len(bad)} 项" if bad else ""))
        yield {"type": "step", "n": n, "total": total_steps, "key": f"llm_{g}",
               "title": f"{GROUP_TITLES[g]} · 大模型",
               "detail": detail,
               "rules": [k for k, v in E.RULES.items() if v.get("group") == g],
               "codes": [c["code"] for c in cfgs],
               "sample": sample, "llm": {"model": model, "ok": not errs,
                                         "batches": len(batches),
                                         "tokens_in": 0, "tokens_out": 0, "ms": cost},
               "ms": _ms()}

    # 2) 冲突 / 人工确认 / 状态机 / 指纹：与规则推导共用同一套收尾
    n += 1
    result, hits, ov_applied = E.finalize(result_cfgs, base["issues"], ans, overrides)
    yield {"type": "step", "n": n, "total": total_steps, "key": "conflict",
           "title": "跨题冲突与人工确认",
           "detail": f"{len(E.conflicts.CONFLICTS)} 条冲突规则，命中 {len(hits)} 条"
                     + ("：" + "、".join(h["id"] for h in hits) if hits else "，无矛盾")
                     + (f"；人工确认覆盖 {len(ov_applied)} 条" if ov_applied else ""),
           "rules": [h["id"] for h in hits], "codes": [c for h in hits for c in h["affect"]],
           "ms": _ms()}

    n += 1
    yield {"type": "step", "n": n, "total": total_steps, "key": "statemachine",
           "title": "工单状态机推导",
           "detail": f"{len(result['flow']['nodes'])} 个节点 · {len(result['flow']['edges'])} 条流转 · "
                     f"{len(result['flow']['branches'])} 个分支",
           "codes": ["C14", "C15", "C18", "C35", "C45", "C50"], "ms": _ms()}

    n += 1
    result["engine"] = "llm"
    result["llm"] = stat
    llm_n = sum(1 for c in result["configs"] if c.get("by") == "llm")
    yield {"type": "step", "n": n, "total": total_steps, "key": "hash",
           "title": "指纹固化",
           "detail": f"模型产出 {llm_n}/{len(result['configs'])} 条，规则兜底 "
                     f"{len(result['configs']) - llm_n} 条 · tokens {stat['tokens_in']}/"
                     f"{stat['tokens_out']} · 输入 {result['input_hash']}",
           "rules": [], "codes": [], "ms": _ms()}
    yield {"type": "done", "result": result, "total_ms": _ms()}


def derive(raw_answers: dict, overrides: dict | None = None, conn=None) -> dict:
    out = None
    for ev in steps(raw_answers, overrides, conn):
        if ev["type"] == "done":
            out = ev["result"]
        elif ev["type"] == "error":
            raise LLMError(ev.get("message", "模型未配置"))
    return out


__all__ = ["steps", "derive"]
