"""SaaS 初始化配置推导引擎 · v4（报事报修配置说明书版）。

与 v3 的区别：
- v3 是 6 题示例问卷、13 条规则、7 个配置键，用来演示「确定性推导」；
- v4 是真实问卷 105 个作答项、7 组规则、81 条配置项，产出可直接交付的配置说明书数据。

三条硬约束不变（v3 的立身之本）：
1. 确定性：同答案必同 output_hash，键顺序无关，规则纯函数。
2. 可解释：每条配置带 rule + src（题号）。
3. 不猜：缺失 / 冲突一律 tbd，绝不补默认值；默认值必须显式 st="def"。

v3 的 derive() 保留在 config_engine.py，旧接口与旧测试不受影响。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import conflicts
from .rules import (org, catalog, flow, sla, approval, fee, priority)
from .rules.base import GROUP_ORDER, GROUP_TITLES, RULES, NODE_CN

RULE_VERSION = "rule_v4.0"
SURVEY_VERSION = "survey_96.v1"

_KN = Path(__file__).resolve().parent.parent / "knowledge"

GROUPS = [org, catalog, flow, sla, approval, fee, priority]

# 推导步骤总数：读取答案 1 + 7 个分组 + 配置对账 / 冲突扫描 / 状态机 / 指纹 4
STEP_TOTAL = 1 + len(GROUPS) + 4


# ---------------------------------------------------------------- 元数据

def config_items() -> list[dict]:
    """81 条配置项的定义（编号 / 名称 / 分组）。"""
    with open(_KN / "config_items.json", encoding="utf-8") as f:
        return json.load(f)


def survey_template() -> dict:
    with open(_KN / "survey_96.json", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 归一化
#
# 前端/接口传来的答案可能是紧凑写法（24、\"08:30-17:30\"、\"residential\"），
# 这里统一收敛成规则层约定的形状。收敛只做类型转换，不改语义、不补值。

def normalize(raw: dict) -> dict:
    ans: dict = {}
    for k, v in (raw or {}).items():
        if v is None or v == "" or v == "null":
            continue
        ans[k] = _coerce(k, v)
    return ans


def _coerce(k: str, v):
    if k in ("Q4",) and isinstance(v, str):
        sep = "-" if "-" in v else "至" if "至" in v else None
        if sep:
            a, b = v.split(sep, 1)
            return {"start": a.strip(), "end": b.strip()}
        return v
    if isinstance(v, list):
        return [_coerce_item(x) for x in v]
    if isinstance(v, dict):
        return {kk: _coerce_item(vv) for kk, vv in v.items()}
    if isinstance(v, str):
        s = v.strip()
        if s.replace(".", "", 1).isdigit():
            return float(s) if "." in s else int(s)
        return s
    return v


def _coerce_item(x):
    if isinstance(x, str):
        s = x.strip()
        if s.replace(".", "", 1).isdigit():
            return float(s) if "." in s else int(s)
        return s
    if isinstance(x, (int, float)):
        return x
    return x


# ---------------------------------------------------------------- 推导

def _st_count(items: list[dict]) -> dict:
    return {
        "ok": sum(1 for c in items if c["st"] == "ok"),
        "def": sum(1 for c in items if c["st"] == "def"),
        "tbd": sum(1 for c in items if c["st"] == "tbd"),
    }


def steps(raw_answers: dict, overrides: dict | None = None):
    """逐步产出推导事件。**每一步的数字都是实测值**，不是预设的进度条。

    derive() 消费同一个生成器，保证「流式看到的过程」与「最终结果」同源——
    不存在"动画走一套、结果走另一套"的可能。

    事件形状：
      {"type":"step", "n":3, "total":12, "key":"catalog", "title":"…",
       "detail":"7 条规则 → 7 条配置（7 已确认 · 0 默认值 · 0 待确认）",
       "rules":["R-CAT-01",…], "codes":["C07",…], "ms":1.8}
      {"type":"done", "result":{…}, "total_ms":24.6}
    """
    t0 = time.perf_counter()

    def _ms() -> float:
        return round((time.perf_counter() - t0) * 1000, 1)

    def _ev(n, key, title, detail, rules=None, codes=None):
        return {"type": "step", "n": n, "total": STEP_TOTAL, "key": key,
                "title": title, "detail": detail,
                "rules": rules or [], "codes": codes or [], "ms": _ms()}

    ans = normalize(raw_answers)
    yield _ev(1, "read", "读取问卷答案",
              f"{len(ans)} 个作答项归一化完成，空值与 null 已剔除")

    configs: list[dict] = []
    issues: list[dict] = []
    n = 1
    for mod in GROUPS:
        n += 1
        g = mod.__name__.rsplit(".", 1)[-1]
        c, i = mod.derive(ans)
        configs += c
        issues += i
        ids = [k for k, v in RULES.items() if v.get("group") == g]
        st = _st_count(c)
        yield _ev(n, g, GROUP_TITLES[g],
                  f"{len(ids)} 条规则 → {len(c)} 条配置"
                  f"（{st['ok']} 已确认 · {st['def']} 默认值 · {st['tbd']} 待确认）",
                  rules=ids, codes=[x["code"] for x in c])

    # 未产出的编号补「不适用」——81 条必须齐全，缺条会让实施以为漏配
    n += 1
    made = len(configs)
    configs = _fill_missing_slots(configs)
    filled = [c["code"] for c in configs[made:]]
    yield _ev(n, "fill", "配置项对账",
              f"规则产出 {made} 条 + 补齐 {len(filled)} 条 = {len(configs)} 条"
              + ("（补齐项为前置条件未满足，标「不适用」）" if filled else "，无缺口"),
              rules=["R-NA-01"] if filled else [], codes=filled)

    # 跨题冲突：只降级为 tbd + 打标，不改值
    n += 1
    hits = conflicts.check(ans)
    applier = _apply_conflicts(configs, hits)
    ov_applied = []
    if overrides:
        applier, ov_applied = _apply_overrides(applier, overrides)
    yield _ev(n, "conflict", "跨题冲突扫描",
              f"{len(conflicts.CONFLICTS)} 条冲突规则，命中 {len(hits)} 条"
              + ("：" + "、".join(h["id"] for h in hits) if hits else "，无矛盾")
              + (f"；人工确认覆盖 {len(ov_applied)} 条（" + "、".join(ov_applied) + "）"
                 if ov_applied else ""),
              rules=[h["id"] for h in hits],
              codes=[c2 for h in hits for c2 in h["affect"]] + ov_applied)
    issues += hits
    configs = applier

    n += 1
    flows = build_flow(configs, ans)
    yield _ev(n, "statemachine", "工单状态机推导",
              f"{len(flows['nodes'])} 个节点 · {len(flows['edges'])} 条流转 · "
              f"{len(flows['branches'])} 个分支（流程模式：{flows['mode'] or '—'}）",
              codes=["C14", "C15", "C18", "C35", "C45", "C50"])

    n += 1
    configs.sort(key=lambda c: c["code"])
    _attach_desc(configs)
    stats = {
        "confirmed": sum(1 for c in configs if c["st"] == "ok"),
        "default": sum(1 for c in configs if c["st"] == "def"),
        "tbd": sum(1 for c in configs if c["st"] == "tbd"),
        "total": len(configs),
        "blocking": sum(1 for i in issues if i.get("block")),
        "conflicts": len(hits),
        "missing": sum(1 for i in issues if str(i.get("id", "")).startswith("M-")),
        "overridden": sum(1 for c in configs if c.get("override")),
    }

    result = {
        "rule_version": RULE_VERSION,
        "survey_version": SURVEY_VERSION,
        "configs": configs,
        "issues": sorted(issues, key=lambda i: (not i.get("block"), i["id"])),
        "flow": flows,
        "stats": stats,
        "groups": [{"k": g, "t": GROUP_TITLES[g],
                    "n": sum(1 for c in configs if c["group"] == g)} for g in GROUP_ORDER],
        "input_hash": answer_hash(ans, overrides),
        "answers": ans,
        "overrides": overrides or {},
    }
    result["output_hash"] = output_hash(result)

    yield _ev(n, "hash", "指纹固化",
              f"输入 {result['input_hash']} / 输出 {result['output_hash']}"
              f"（同答案必同指纹，键顺序无关）")
    yield {"type": "done", "result": result, "total_ms": _ms()}


def _apply_conflicts(configs: list[dict], hits: list[dict]) -> list[dict]:
    by_code = {c["code"]: c for c in configs}
    for h in hits:
        for code in h["affect"]:
            item = by_code.get(code)
            if item is None:
                continue
            item["conflict"] = h["id"]
            if h["lv"] == "red" and item["st"] == "ok":
                item["st"] = "tbd"
                item["v"] = f"待确认（{h['id']}：{h['title']}）"
    return configs


def finalize(configs: list[dict], issues: list[dict], ans: dict,
             overrides: dict | None = None):
    """收尾：冲突扫描 → 人工确认 → 状态机 → 统计 → 指纹。

    规则推导（steps）与大模型推导（llm_derive）共用这一段，
    保证「同一个 configs 输入，不管上游是规则还是模型，收尾口径完全一致」。
    """
    hits = conflicts.check(ans)
    configs = _apply_conflicts(configs, hits)
    ov_applied: list[str] = []
    if overrides:
        configs, ov_applied = _apply_overrides(configs, overrides)
    flows = build_flow(configs, ans)
    configs.sort(key=lambda c: c["code"])
    _attach_desc(configs)
    all_issues = list(issues) + hits
    stats = {
        "confirmed": sum(1 for c in configs if c["st"] == "ok"),
        "default": sum(1 for c in configs if c["st"] == "def"),
        "tbd": sum(1 for c in configs if c["st"] == "tbd"),
        "total": len(configs),
        "blocking": sum(1 for i in all_issues if i.get("block")),
        "conflicts": len(hits),
        "missing": sum(1 for i in all_issues if str(i.get("id", "")).startswith("M-")),
        "overridden": sum(1 for c in configs if c.get("override")),
    }
    result = {
        "rule_version": RULE_VERSION,
        "survey_version": SURVEY_VERSION,
        "configs": configs,
        "issues": sorted(all_issues, key=lambda i: (not i.get("block"), i["id"])),
        "flow": flows,
        "stats": stats,
        "groups": [{"k": g, "t": GROUP_TITLES[g],
                    "n": sum(1 for c in configs if c["group"] == g)} for g in GROUP_ORDER],
        "input_hash": answer_hash(ans, overrides),
        "answers": ans,
        "overrides": overrides or {},
    }
    result["output_hash"] = output_hash(result)
    return result, hits, ov_applied


def _apply_overrides(configs: list[dict], overrides: dict) -> tuple[list[dict], list[str]]:
    """人工确认的配置值：直接落值、置为已确认，并把理由写进说明。

    只接受带理由的覆盖——没理由的"人工拍值"等于黑箱，交付时说不清。
    冲突标记保留（冲突客观存在），只是不再阻塞。
    """
    by = {c["code"]: c for c in configs}
    applied: list[str] = []
    for code, ov in (overrides or {}).items():
        item = by.get(code)
        if not item or not isinstance(ov, dict):
            continue
        v = str(ov.get("v") or "").strip()
        why = str(ov.get("why") or "").strip()
        if not v or not why:
            continue
        item["v"] = v
        item["st"] = "ok"
        item["override"] = {"v": v, "why": why, "at": ov.get("at", "")}
        note = item.get("note") or ""
        item["note"] = (note + "；" if note else "") + f"人工确认：{why}" + \
            (f"（{ov['at']}）" if ov.get("at") else "")
        applied.append(code)
    return configs, applied


def derive(raw_answers: dict, overrides: dict | None = None) -> dict:
    """一次性拿到结果（接口与测试用）。与流式同源：消费同一个生成器。"""
    result = None
    for ev in steps(raw_answers, overrides):
        if ev["type"] == "done":
            result = ev["result"]
    return result



def _fill_missing_slots(configs: list[dict]) -> list[dict]:
    """规则没产出的编号 -> 不适用。不静默留空，也不假装已确认。"""
    got = {c["code"] for c in configs}
    out = list(configs)
    for item in config_items():
        if item["code"] in got:
            continue
        out.append({
            "code": item["code"], "group": item["group"],
            "k": item["code"].lower(), "name": item["name"],
            "v": "不适用（本次问卷未涉及该配置）", "st": "def",
            "src": "—", "rule": "R-NA-01", "note": "对应题目的前置条件未满足",
        })
    return out


# ---------------------------------------------------------------- 流程图

STATE_ORDER = ["accept", "pending", "processing", "audit", "to_rate", "to_visit", "closed"]


# 泳道：谁在什么时候做什么。前端按 (col, lane) 定位成泳道图。
LANES = [
    {"k": "report", "t": "提报端"},
    {"k": "service", "t": "客服中心"},
    {"k": "handler", "t": "处理人"},
    {"k": "approve", "t": "审批"},
    {"k": "review", "t": "评价 / 回访"},
]

# 节点 → (泳道, 列)。列是时间推进的阶段，不是排序号。
NODE_META = {
    "report_owner": ("report", 1),
    "report_staff": ("report", 1),
    "accept_order": ("service", 2),
    "dispatch": ("service", 2),
    "accept": ("handler", 3),
    "pending": ("handler", 4),
    "fee": ("report", 4),
    "processing": ("handler", 5),
    "hang": ("handler", 5),
    "audit": ("approve", 6),
    "close_approval": ("approve", 6),
    "to_rate": ("review", 7),
    "to_visit": ("review", 8),
    "closed": ("service", 9),
}


def _node(nid, t, kind="state", src="", alert="", manual=False, desc="", on=True):
    lane, col = NODE_META.get(nid, ("handler", 5))
    return {"id": nid, "t": t, "kind": kind, "lane": lane, "col": col,
            "src": src, "alert": alert, "manual": manual, "desc": desc, "on": on}


def build_flow(configs: list[dict], ans: dict) -> dict:
    """配置对象 → 分层状态机。图不是画的，是从配置里推出来的。

    分层两层含义：
    1. 泳道（lane）—— 谁是责任对象：提报端 / 客服 / 处理人 / 审批 / 评价回访
    2. 阶段（col）  —— 什么时候发生：提报 → 受理派单 → 接单 → 处理 → 审核 → 评价 → 回访 → 归档
    """
    m = {c["code"]: c for c in configs}

    def val(code, default=""):
        c = m.get(code)
        return c["v"] if c else default

    nodes_txt = val("C15", "")
    active = [n for n in STATE_ORDER if NODE_CN.get(n, "关闭") in nodes_txt]
    if not active:
        active = ["accept", "pending", "processing", "closed"]

    custom = val("C14", "").startswith("自定义")
    src_flow = "第 25 题" if custom else "标准流程"
    predict = "第 8 / 12 / 17 题"

    nodes = [
        _node("report_owner", "业主端提报", "start", predict, desc="业主手机自助报单，可带图"),
        _node("report_staff", "员工端代报", "start", predict, desc="客服/工程代业主录单"),
        _node("accept_order", "客服受理", "action", "第 6 题",
              desc="核对房号与公共区域，判断类型与紧急度"),
        _node("dispatch", "派单", "action", val("C20", "") or "第 30 题",
              desc="按配置的分配方式落到人、角色池或抢单池"),
    ]

    for n in active:
        name = NODE_CN.get(n, "关闭")
        nodes.append(_node(
            n, name, "state", src_flow,
            alert="预警" if name in val("C35", "") else "",
            manual=name in val("C18", ""),
            desc=_state_desc(n, val),
        ))
    if "closed" not in active:
        nodes.append(_node("closed", "关闭", "end", "—", desc="归档，不再流转"))

    # 并行分支：挂起、报价确认、关闭审批
    if val("C45", "").startswith("允许"):
        _hang_cap = str(val("C47", "")).strip()
        nodes.append(_node("hang", "挂起", "branch", "第 51、53 题",
                           desc=f"暂停计时，单次上限 {_hang_cap}" if _hang_cap else "挂起后暂停计时"))
    if val("C50", "").startswith(("全部收费", "部分收费")):
        nodes.append(_node("fee", "业主确认报价", "branch", "第 70、73 题",
                           desc=val("C53", "") or "报价确认后才处理"))
    if val("C41", "").find("关闭") >= 0:
        nodes.append(_node("close_approval", "关闭审批", "branch", "第 79、80 题",
                           desc=val("C42", "") or "关闭前走审批"))

    edges = [{"from": a, "to": b} for a, b in zip(active, active[1:])]
    if active:
        edges.insert(0, {"from": "dispatch", "to": active[0]})
        if active[-1] != "closed":
            edges.append({"from": active[-1], "to": "closed"})

    branches = []
    hang = [n for n in nodes if n["id"] == "hang"]
    if hang:
        branches.append({"from": "processing", "to": "hang", "t": "挂起",
                         "cond": val("C47", ""), "src": "第 51、53 题",
                         "lane": "handler", "col": 5, "back": True})
    fee = [n for n in nodes if n["id"] == "fee"]
    if fee:
        branches.append({"from": "pending", "to": "fee", "t": "报价确认",
                         "cond": val("C53", ""), "src": "第 70、73 题",
                         "lane": "report", "col": 4, "back": False})
    if [n for n in nodes if n["id"] == "close_approval"]:
        branches.append({"from": active[-1] if active else "processing", "to": "close_approval",
                         "t": "关闭审批", "cond": val("C42", ""), "src": "第 79、80 题",
                         "lane": "approve", "col": 6, "back": False})

    return {
        "lanes": LANES,
        "nodes": nodes,
        "edges": edges,
        "branches": branches,
        "cols": max([n["col"] for n in nodes] or [1]),
        "mode": val("C14", ""),
        "note": "泳道=责任对象，列=推进阶段；虚线框为问卷内部冲突，待甲方确认",
    }


_STATE_DESC = {
    "accept": "处理人确认接单，限 {t}，超时触发升级",
    "pending": "已接单待开工，按优先级排队",
    "processing": "现场处理中，限 {t}",
    "audit": "办结前审核，通过后才进入评价",
    "to_rate": "等待业主评价（{t}）",
    "to_visit": "客服回访，记录回访结果",
}
_DESC_UNIT = {"accept": "C29", "processing": "C30", "to_rate": "C65"}


def _state_desc(nid: str, val) -> str:
    tpl = _STATE_DESC.get(nid, "")
    if not tpl:
        return ""
    if "{t}" not in tpl:
        return tpl
    limit = str(val(_DESC_UNIT.get(nid, ""), "") or "").strip()
    if not limit:
        # 没配时限就别留个空洞的"限 ，"
        return tpl.replace("，限 {t}", "").replace("（{t}）", "")
    return tpl.replace("{t}", limit)


def _attach_desc(configs: list[dict]) -> list[dict]:
    """每条配置补一句「这是什么」的说明，来源 config_items.json 的 about。"""
    about = {i["code"]: i.get("about", "") for i in config_items()}
    for c in configs:
        if not c.get("desc"):
            c["desc"] = about.get(c["code"], "")
    return configs


# ---------------------------------------------------------------- 指纹

def answer_hash(ans: dict, overrides: dict | None = None) -> str:
    payload = json.dumps(ans, sort_keys=True, ensure_ascii=False, default=str)
    if overrides:
        # 人工确认也是输入的一部分：同一份答案 + 不同确认值，指纹必须不同
        payload += "|" + json.dumps(overrides, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def output_hash(result: dict) -> str:
    # 只对"值"取指纹：desc/about 是说明文案，改措辞不该让指纹变
    slim = [{"code": c.get("code"), "v": c.get("v"), "st": c.get("st"),
             "src": c.get("src"), "rule": c.get("rule"), "by": c.get("by"),
             "override": c.get("override"), "conflict": c.get("conflict")}
            for c in result["configs"]]
    flow = result["flow"]
    flow_slim = {"nodes": [{k: n.get(k) for k in ("id", "t", "lane", "col", "kind")}
                           for n in flow.get("nodes", [])],
                 "edges": flow.get("edges"), "branches": flow.get("branches"),
                 "mode": flow.get("mode")}
    payload = json.dumps({"configs": slim, "issues": result["issues"], "flow": flow_slim},
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 自检接口

def rule_index() -> list[dict]:
    return [{"id": k, **v} for k, v in sorted(RULES.items())]


def conflict_index() -> list[dict]:
    return conflicts.index()
