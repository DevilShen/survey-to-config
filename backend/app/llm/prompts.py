"""三类 prompt 与输出校验 + 离线降级实现。

铁律：LLM 只做拆解、话术、说人话；数字永远代码算。
所有结构化输出都过三道关：JSON 解析 → slot 枚举白名单 → evidence 原文子串校验。
任何一道不过，回退到离线模板，绝不把脏数据写进 answers。

离线降级不是摆设：追问用引擎自带的 build_questions 模板，抽取用正则 + 词典
（复用 pricing_agent.decompose 的同一套口径），保证无 key 演示全流程可跑。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from ..analytics import pricing_agent as pa
from .client import LLMError, SchemaError

SLOTS = list(pa.SLOT_META.keys())
MODE_ENUM = ("标准配置", "定制交付")

# ---------------------------------------------------------------- ASK 追问

ASK_SYSTEM = """你是软件报价助手。客户经理在跟客户沟通需求，有几个关键信息缺失。
请为每个缺失槽位生成一句自然、口语化的追问，像有经验的售前顾问那样说话，不要模板腔。

输出 JSON：{"questions": [{"slot": "槽位名", "q": "追问话术", "options": ["快捷选项1", "快捷选项2"]}]}

要求：
- slot 只能从给定的槽位清单里选，一个槽位一条
- options 给 2-4 个常见回答，客户点选即答；给不出合理选项就空数组
- 语气简短专业，一次问清，不寒暄
- 不要解释、不要输出 JSON 以外的内容"""


def ask_questions(client, request: dict, gap_questions: list[dict]) -> tuple[list[dict], str]:
    """生成自然话术追问。返回 (questions, source)：source ∈ llm / offline。

    离线或校验失败时逐条回退到模板——话术可以朴素，问题不能丢。
    """
    if not gap_questions:
        return [], "none"
    if not client.online:
        return gap_questions, "offline"

    slot_brief = [{"slot": q["slot"], "label": q["label"],
                   "模板问法": q["q"], "为什么问": q["why"]} for q in gap_questions]
    user = ("客户原文：" + (request.get("raw_text") or "")[:400] + "\n\n"
            "缺失槽位：" + _json(slot_brief))
    try:
        res = client.chat_json(ASK_SYSTEM, user)
        data = res.data if isinstance(res.data, dict) else {}
        llm_qs = data.get("questions") or []
        by_slot = {}
        for q in llm_qs:
            if not isinstance(q, dict):
                continue
            slot = q.get("slot")
            if slot not in SLOTS or not isinstance(q.get("q"), str) or not q["q"].strip():
                continue
            opts = q.get("options")
            by_slot[slot] = {
                "q": q["q"].strip()[:120],
                "options": [str(o)[:30] for o in opts][:4] if isinstance(opts, list) else [],
            }
        # 以模板清单为准做覆盖：模型漏掉的槽位用模板补齐，保证一个不落
        out = []
        used_llm = False
        for tpl in gap_questions:
            hit = by_slot.get(tpl["slot"])
            if hit:
                used_llm = True
                out.append({**tpl, "q": hit["q"], "options": hit["options"], "ask_by": "llm"})
            else:
                out.append({**tpl, "options": _offline_options(tpl["slot"]), "ask_by": "template"})
        return out, "llm" if used_llm else "offline"
    except (LLMError, SchemaError):
        return [{**q, "options": _offline_options(q["slot"]), "ask_by": "template"}
                for q in gap_questions], "offline"


def _offline_options(slot: str) -> list[str]:
    return {
        "scale": ["300 人以内", "300–2000 人", "2000 人以上"],
        "mode": ["标准配置", "定制交付"],
        "industry": ["制造", "零售", "医疗", "专业服务", "互联网"],
        "deadline": ["3 个月内", "半年内", "一年内"],
        "budget": ["50 万以内", "50–100 万", "100 万以上"],
    }.get(slot, [])


# ---------------------------------------------------------------- EXTRACT 槽位抽取

EXTRACT_SYSTEM = """你是信息抽取器。从客户经理与客户的对话中，抽取报价所需的槽位值。

输出 JSON：{"slots": {"槽位名": {"value": 值, "confidence": 0.0-1.0, "evidence": "原文片段"}}}

铁规：
- 只抽取给定的缺失槽位，其他一律不输出
- evidence 必须是对话原文的连续子串，找不到就把 confidence 设为 0
- 值不确定就降 confidence：明确说了 → 0.9；推测的 → 0.6；没提 → 不输出该槽位
- 值的格式：scale 用整数（"八百人"→800）；budget 用数字（万元）；deadline 用 "2027-Q2" 或 "2027-03" 格式；
  mode 只能是 "标准配置" 或 "定制交付"；modules / integration 用数组；industry 用行业名"""


def extract_slots(client, conversation: list[dict], missing: list[str]) -> tuple[dict, str]:
    """从对话里抽槽位。返回 ({slot: {value, confidence, evidence}}, source)。

    三档置信度由调用方执行：≥0.8 自动写，0.5–0.8 要确认，<0.5 再问。
    """
    text = "\n".join(f"{'客户经理' if m['role'] == 'user' else '助手'}：{m['content']}"
                     for m in conversation[-8:])
    if not client.online:
        return _offline_extract(text, missing), "offline"

    user = ("缺失槽位：" + "、".join(missing) + "\n\n对话记录：\n" + text)
    try:
        res = client.chat_json(EXTRACT_SYSTEM, user)
        data = res.data if isinstance(res.data, dict) else {}
        raw = data.get("slots") or {}
        out = {}
        for slot, item in raw.items():
            if slot not in missing or not isinstance(item, dict):
                continue
            ev = str(item.get("evidence") or "")
            try:
                conf = float(item.get("confidence") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            # evidence 必须是原文子串：这条不过，整个槽位判 0——
            # 模型编造的"依据"比没有依据更危险
            if ev and ev not in text:
                conf = 0.0
            value = _normalize_value(slot, item.get("value"))
            if value in (None, "", []):
                continue
            out[slot] = {"value": value, "confidence": max(0.0, min(1.0, conf)),
                         "evidence": ev[:80]}
        return out, "llm"
    except (LLMError, SchemaError):
        return _offline_extract(text, missing), "offline"


def _normalize_value(slot: str, v):
    """把模型给的值收成引擎认识的形态。收不了就丢弃，不猜。"""
    if v is None:
        return None
    if slot in ("modules", "integration"):
        items = v if isinstance(v, list) else re.split(r"[,，、/]", str(v))
        return [str(x).strip() for x in items if str(x).strip()]
    if slot == "mode":
        s = str(v)
        if "标准" in s or "配置" in s:
            return "标准配置"
        if "定制" in s or "开发" in s:
            return "定制交付"
        return None
    if slot == "scale":
        digits = re.sub(r"[^\d]", "", str(v))
        return digits or None
    if slot == "budget":
        m = re.search(r"(\d+(?:\.\d+)?)", str(v))
        return m.group(1) if m else None
    if slot == "deadline":
        s = str(v).strip()
        if re.fullmatch(r"\d{4}-?Q[1-4]", s) or re.fullmatch(r"\d{4}-\d{1,2}", s):
            return s
        m = re.search(r"(\d{4}).{0,3}?(\d{1,2})", s)
        return f"{m.group(1)}-{int(m.group(2)):02d}" if m else None
    return str(v).strip()[:40]


# ---- 离线抽取：正则 + 词典，复用引擎同一套拆解口径

_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(s: str) -> int | None:
    """八百 → 800；一千二 → 1200；三千五 → 3500。只支持到万。"""
    if not s:
        return None
    total, num = 0, 0
    for ch in s:
        if ch in _CN_NUM:
            num = _CN_NUM[ch]
        elif ch == "十":
            total += (num or 1) * 10
            num = 0
        elif ch == "百":
            total += (num or 1) * 100
            num = 0
        elif ch == "千":
            total += (num or 1) * 1000
            num = 0
        elif ch == "万":
            total = (total + num) * 10000
            num = 0
        else:
            return None
    return total + num or None


_CN_SCALE_RE = re.compile(
    r"([零一二两三四五六七八九十百千万]+)\s*(?:名|个|位)?\s*(?:用户|账号|员工|医护|人|并发)?[\u4e00-\u9fa5]{0,4}?用")
_CN_SCALE_RE2 = re.compile(
    r"([零一二两三四五六七八九十百千万]+)\s*(?:名|个|位)?\s*(用户|账号|员工|医护|人员|人|并发)")
_NUM_SCALE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:名|个|位)?\s*[\u4e00-\u9fa5]{0,4}?用(?![语途])")
_CN_QUARTER_RE = re.compile(r"(明年|后年|今年)?\s*([一二三四])季度")
_BUDGET_LOOSE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*万")


def _offline_extract(text: str, missing: list[str]) -> dict:
    """把用户回复当一段新需求文本，用引擎同款拆解抽槽位。

    置信度映射：正则/词典直取 → 0.85（自动写入档）；推断 → 0.6（要确认档）。
    """
    dec = pa.decompose({"raw_text": text}, None)
    out: dict[str, dict] = {}

    def put(slot, value, conf, evidence):
        if value not in (None, "", []):
            out[slot] = {"value": value, "confidence": conf, "evidence": (evidence or "")[:80]}

    for slot in missing:
        s = dec["slots"].get(slot)
        conf = 0.85 if (s and s["confidence"] >= 1) else 0.6
        if slot == "modules" and dec["modules"]:
            put("modules", dec["modules"], 0.85, s["evidence"])
        elif slot == "integration" and dec["integrations"]:
            put("integration", dec["integrations"], 0.85, s["evidence"])
        elif slot == "industry" and dec["industry"]:
            put("industry", dec["industry"], 0.85, s["evidence"])
        elif slot == "mode" and dec["mode"]:
            put("mode", dec["mode"], conf, dec["mode"])
        elif slot == "scale":
            val = dec["scale"]["value"]
            ev = s["evidence"] if s else ""
            if not val:
                m = _CN_SCALE_RE.search(text) or _CN_SCALE_RE2.search(text)
                if m:
                    n = _cn_to_int(m.group(1))
                    if n:
                        val, ev = n, m.group(0)
            if not val:
                m = _NUM_SCALE_RE.search(text)
                if m:
                    val, ev = int(float(m.group(1))), m.group(0)
            if val:
                put("scale", str(val), 0.85, ev)
        elif slot == "budget":
            val = dec["budget"]["value"]
            ev = dec["budget"].get("raw") or ""
            if not val:
                m = _BUDGET_LOOSE_RE.search(text)
                if m:
                    val, ev = float(m.group(1)), m.group(0)
            if val:
                put("budget", str(int(val) if float(val).is_integer() else val), 0.85, ev)
        elif slot == "deadline":
            canon, ev = None, dec["deadline"].get("raw") or ""
            m = _CN_QUARTER_RE.search(text)
            if m:
                # 季度能直接定到月，不经过「月数→日期」折算，避免漂移一个月
                q = "一二三四".index(m.group(2)) + 1
                year = date.today().year + (1 if m.group(1) == "明年"
                                            else 2 if m.group(1) == "后年" else 0)
                target = date(year, (q - 1) * 3 + 1, 1)
                if target < date.today():
                    target = date(year + 1, (q - 1) * 3 + 1, 1)
                canon, ev = f"{target.year}-{target.month:02d}", m.group(0)
            elif dec["deadline"]["months"]:
                months = dec["deadline"]["months"]
                target = date.today() + timedelta(days=int(months * 30.4))
                canon = f"{target.year}-{target.month:02d}"
            if canon:
                put("deadline", canon, 0.85, ev)
    return out


# ---------------------------------------------------------------- EXPLAIN 结论解读

EXPLAIN_SYSTEM = """你是报价解读员。把已经算好的报价结论解释给客户经理听。
给你的只有结构化数字，不许自己重新算、不许改动任何数字、不许编造数字之外的事实。
口语化，三到五句话，先说结论再说理由，最后给一个行动建议。"""


def explain_quote(client, quote: dict, margin: dict, conf: float) -> "object":
    """返回一个文本迭代器（在线流式 / 离线一次性）。数字原样进 prompt，模型只组织语言。"""
    brief = {
        "报价": quote.get("price"), "人天": quote.get("days"),
        "毛利率": margin.get("margin"), "毛利下限": margin.get("floor"),
        "置信度": conf, "毛利结论": margin.get("note"),
        "主要风险": (quote.get("risks") or [])[:3],
    }
    if client.online:
        try:
            return client.stream(EXPLAIN_SYSTEM, "结论数据：" + _json(brief))
        except LLMError:
            pass
    return iter([_offline_explain(quote, margin, conf)])


def _offline_explain(quote: dict, margin: dict, conf: float) -> str:
    price = quote.get("price")
    days = quote.get("days")
    m = margin.get("margin", 0)
    lines = [f"本次建议报价 {price} 万，对应 {days} 人天，毛利率 {m:.1%}。"]
    lines.append(f"（离线话术）置信度 {conf:.0%}；{margin.get('note', '')}。")
    risks = (quote.get("risks") or [])[:2]
    if risks:
        lines.append("需要留意：" + "；".join(risks) + "。")
    lines.append("建议先过一遍右侧推理链路的定价依据，再决定是否提交复核。")
    return "".join(lines)


# ---------------------------------------------------------------- CONSTRAINT 复核意见抽取

CONSTRAINT_SYSTEM = """你是意见解析器。复核人驳回了一份报价，把驳回意见解析成可执行约束。

输出 JSON：{"max_price": 数字或null, "drop_modules": ["模块名"], "require_modules": ["模块名"], "note": "一句话概述"}

规则：
- "总价不能超过80万" → max_price = 80
- "砍掉巡检模块" / "不做报表了" → drop_modules
- "必须含等保合规" → require_modules
- 解析不出来的约束不要硬猜，写进 note 里"""


def extract_constraints(client, comment: str) -> tuple[dict, str]:
    """复核驳回意见 → 结构化约束。返回 (constraints, source)。"""
    base = {"max_price": None, "drop_modules": [], "require_modules": [], "note": ""}
    if client.online:
        try:
            res = client.chat_json(CONSTRAINT_SYSTEM, "复核意见：" + comment)
            d = res.data if isinstance(res.data, dict) else {}
            out = dict(base)
            mp = d.get("max_price")
            if isinstance(mp, (int, float)) and mp > 0:
                out["max_price"] = float(mp)
            for k in ("drop_modules", "require_modules"):
                v = d.get(k)
                if isinstance(v, list):
                    out[k] = [str(x).strip() for x in v if str(x).strip()][:6]
            out["note"] = str(d.get("note") or "")[:120]
            return out, "llm"
        except (LLMError, SchemaError):
            pass
    return _offline_constraints(comment), "offline"


def _offline_constraints(comment: str) -> dict:
    out = {"max_price": None, "drop_modules": [], "require_modules": [], "note": ""}
    m = re.search(r"(?:总价|报价|价格)?\s*(?:≤|<=|不超过|以内|控制在|降到|压到)\s*(\d+(?:\.\d+)?)\s*万", comment)
    if m:
        out["max_price"] = float(m.group(1))
    for seg in re.findall(r"(?:砍掉|去掉|不做|减去|删除)([^，。；,;]{2,12})", comment):
        hits = pa._scan(seg, pa.MODULE_LEXICON)
        for h in hits:
            if h["canonical"] not in out["drop_modules"]:
                out["drop_modules"].append(h["canonical"])
        if not hits and seg.strip():
            out["drop_modules"].append(seg.strip())
    for seg in re.findall(r"(?:必须含|必须包含|一定要有|保留)([^，。；,;]{2,12})", comment):
        hits = pa._scan(seg, pa.MODULE_LEXICON)
        for h in hits:
            if h["canonical"] not in out["require_modules"]:
                out["require_modules"].append(h["canonical"])
    return out


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)
