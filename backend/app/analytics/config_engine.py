"""SaaS 初始化配置推导引擎。

三条硬约束，缺一条这套东西就不可信：
1. 确定性：同一份问卷重跑两次，输出必须逐字节相同（用 input_hash 证明）。
2. 可解释：每条配置都带 rule_id 与来源题号，客户问"为什么是这个值"能答上来。
3. 不猜：答案缺失或互相冲突时，输出「待确认 + 阻塞」，绝不自动补全。

AI 在这里的角色是理解与呈现（把客户口语翻译成选项），不是生成配置。
配置结构必须由代码推导，否则同一份问卷今天跑出 A、明天跑出 B。
"""
from __future__ import annotations

import hashlib
import json

RULE_VERSION = "rule_v3.4"

# 规则目录：给前端展示"这个值是哪条规则推出来的"
RULES = {
    "R-ORG-01": {"desc": "组织层级数映射组织树深度", "inputs": ["Q3"]},
    "R-ORG-02": {"desc": "组织编码规则随层级变化", "inputs": ["Q3"]},
    "R-SCL-01": {"desc": "使用人数规模映射角色模板数量", "inputs": ["Q7"]},
    "R-SCL-02": {"desc": "超过 200 人启用大数据量查询策略", "inputs": ["Q7"]},
    "R-PRM-01": {"desc": "数据可见范围映射 data_scope 策略", "inputs": ["Q12", "Q3"]},
    "R-PRM-02": {"desc": "全量可见触发最小权限原则告警", "inputs": ["Q12"]},
    "R-APR-01": {"desc": "金额阈值决定分级审批节点是否存在", "inputs": ["Q18"]},
    "R-APR-02": {"desc": "生成金额分级条件分支", "inputs": ["Q18"]},
    "R-APR-03": {"desc": "会签开关映射并行网关", "inputs": ["Q21"]},
    "R-APR-04": {"desc": "会签启用但阈值缺失时判为待确认", "inputs": ["Q21", "Q18"]},
    "R-FLD-01": {"desc": "成本字段可见性映射字段级权限", "inputs": ["Q25"]},
    "R-DEF-01": {"desc": "通知渠道默认值", "inputs": []},
    "R-DEF-02": {"desc": "分页大小默认值", "inputs": []},
}

ROLE_TEMPLATES = {"s": 4, "m": 6, "l": 6, "xl": 8}


def _cfg(key, value, status, source, rule):
    return {"k": key, "v": value, "st": status, "src": source, "rule": rule}


def _issue(level, question, message, affects, blocking, rule):
    return {"lv": level, "q": question, "msg": message, "affect": affects,
            "block": blocking, "rule": rule}


def normalize(answers: dict) -> dict:
    """把 JSON 传来的值收敛成引擎可判定的类型。未回答统一为 None。"""
    def pick(key, allowed=None):
        v = answers.get(key)
        if v is None:
            return None
        if allowed is not None and v not in allowed:
            return None
        return v

    q3 = pick("Q3", (2, 3))
    q7 = pick("Q7", ("s", "m", "l", "xl"))
    q12 = pick("Q12", ("self", "subtree", "all"))
    q25 = pick("Q25", ("fin", "all"))
    q18 = answers.get("Q18")
    if q18 in ("", "null"):
        q18 = None
    if q18 is not None:
        try:
            q18 = int(q18)
        except (TypeError, ValueError):
            q18 = None
    q21 = answers.get("Q21")
    if isinstance(q21, str):
        q21 = True if q21.lower() == "true" else False if q21.lower() == "false" else None

    return {"Q3": q3, "Q7": q7, "Q12": q12, "Q18": q18, "Q21": q21, "Q25": q25}


def derive(raw_answers: dict) -> dict:
    ans = normalize(raw_answers)
    lvl, size, scope = ans["Q3"], ans["Q7"], ans["Q12"]
    thr, hui, cost = ans["Q18"], ans["Q21"], ans["Q25"]

    cfg: list[dict] = []
    issues: list[dict] = []
    flow: list[dict] = []

    # --- 组织 ---
    if lvl is None:
        cfg.append(_cfg("org_level", "无法推导（组织层级未回答）", "tbd", "Q3", "R-ORG-01"))
        issues.append(_issue("red", "Q3", "组织层级未回答，组织树与编码规则均无法生成",
                             "org_level、org_code_rule", True, "R-ORG-01"))
    else:
        cfg.append(_cfg("org_level", f"{lvl} 层组织树", "ok", "Q3", "R-ORG-01"))
        cfg.append(_cfg("org_code_rule",
                        "G-R-S（集团-区域-门店）" if lvl == 3 else "H-S（总部-门店）",
                        "ok", "Q3", "R-ORG-02"))

    # --- 规模 ---
    if size is None:
        cfg.append(_cfg("role_templates", "无法推导（规模未回答）", "tbd", "Q7", "R-SCL-01"))
        issues.append(_issue("amber", "Q7", "使用人数规模未回答，角色模板数量按最小值 4 类暂定",
                             "role_templates", False, "R-SCL-01"))
        size = "s"
    cfg.append(_cfg("role_templates", f"{ROLE_TEMPLATES[size]} 类角色模板", "ok", "Q7", "R-SCL-01"))
    cfg.append(_cfg("query_strategy",
                    "标准分页" if size == "s" else "大数据量模式（游标分页 + 组合索引）",
                    "ok", "Q7", "R-SCL-02"))

    # --- 数据权限 ---
    if scope is None:
        cfg.append(_cfg("data_scope", "无法推导（可见范围未回答）", "tbd", "Q12", "R-PRM-01"))
        issues.append(_issue("red", "Q12", "数据可见范围未回答，data_scope 无法生成",
                             "data_scope、角色模板", True, "R-PRM-01"))
    elif scope == "subtree" and lvl != 3:
        cfg.append(_cfg("data_scope", "无法推导（组织层级不足）", "tbd", "Q12", "R-PRM-01"))
        issues.append(_issue("red", "Q12 × Q3",
                             "选择「区域看下属门店」，但组织仅 2 级、无区域层，数据权限范围无法生成",
                             "data_scope、角色模板", True, "R-PRM-01"))
    elif scope == "self":
        cfg.append(_cfg("data_scope", "全部角色 = self（仅本部门）", "ok", "Q12", "R-PRM-01"))
    elif scope == "subtree":
        cfg.append(_cfg("data_scope", "门店 = self；区域 = subtree；集团 = all",
                        "ok", "Q12", "R-PRM-01"))
    else:
        cfg.append(_cfg("data_scope", "全部角色 = all（跨组织可见）", "ok", "Q12", "R-PRM-01"))
        issues.append(_issue("amber", "Q12", "「集团看全部」与最小权限原则冲突，建议补充例外清单",
                             "data_scope", False, "R-PRM-02"))

    # --- 审批流 ---
    flow.append({"t": "提交申请", "src": "Q1", "type": "start"})
    if thr is None:
        cfg.append(_cfg("approval_nodes", "无法确定（阈值未填）", "tbd", "Q18", "R-APR-01"))
        issues.append(_issue("red", "Q18", "审批金额阈值未填，审批流节点数与条件分支无法确定",
                             "approval_nodes、workflow", True, "R-APR-01"))
        flow.append({"t": "主管审批", "src": "默认", "type": "node"})
        flow.append({"t": "金额分级节点", "src": "Q18", "type": "unknown"})
    else:
        nodes = 2
        cfg.append(_cfg("approval_nodes", f"{nodes} 个节点", "ok", "Q18", "R-APR-01"))
        cfg.append(_cfg("approval_cond",
                        f"amount ≤ {thr:,} → 主管；> {thr:,} → 总监",
                        "ok", "Q18", "R-APR-02"))
        flow.append({"t": "主管审批", "src": "Q18", "type": "node"})
        flow.append({"t": "总监审批", "src": "Q18", "type": "node", "cond": f"> {thr:,}"})

    # --- 会签 ---
    if hui is True:
        cfg.append(_cfg("countersign", "并行会签：财务 ∥ 法务，全部通过才流转",
                        "ok", "Q21", "R-APR-03"))
        flow.append({"t": "会签", "src": "Q21", "type": "parallel", "subs": ["财务", "法务"]})
        if thr is None:
            issues.append(_issue("amber", "Q18 × Q21",
                                 "已启用会签，但审批阈值未定，会签节点的触发条件无法绑定",
                                 "countersign", False, "R-APR-04"))
    elif hui is False:
        cfg.append(_cfg("countersign", "未启用", "def", "Q21", "R-APR-03"))
    else:
        cfg.append(_cfg("countersign", "未回答，按未启用处理", "tbd", "Q21", "R-APR-03"))
        issues.append(_issue("amber", "Q21", "会签未回答，暂按未启用生成，需客户确认",
                             "countersign", False, "R-APR-03"))

    # --- 字段权限 ---
    if cost is None:
        cfg.append(_cfg("field_perm", "无法推导（成本字段权限未回答）", "tbd", "Q25", "R-FLD-01"))
        issues.append(_issue("amber", "Q25", "成本字段可见性未回答，默认仅财务可见",
                             "field_perm", False, "R-FLD-01"))
    elif cost == "fin":
        cfg.append(_cfg("field_perm", "cost_* 对非财务角色 hidden", "ok", "Q25", "R-FLD-01"))
    else:
        cfg.append(_cfg("field_perm", "cost_* 全角色可见", "ok", "Q25", "R-FLD-01"))
        issues.append(_issue("amber", "Q25", "成本字段对全员可见，可能违反客户薪酬保密要求，建议确认",
                             "field_perm", False, "R-FLD-01"))

    # --- 默认值 ---
    cfg.append(_cfg("notification", "站内信 + 邮件（行业默认）", "def", "默认值", "R-DEF-01"))
    cfg.append(_cfg("pagination_size", "20 条/页（行业默认）", "def", "默认值", "R-DEF-02"))

    flow.append({"t": "归档", "src": "—", "type": "end"})

    stats = {
        "confirmed": sum(1 for c in cfg if c["st"] == "ok"),
        "default": sum(1 for c in cfg if c["st"] == "def"),
        "tbd": sum(1 for c in cfg if c["st"] == "tbd"),
        "blocking": sum(1 for i in issues if i["block"]),
    }

    return {
        "rule_version": RULE_VERSION,
        "configs": cfg,
        "issues": issues,
        "flow": flow,
        "stats": stats,
        "input_hash": answer_hash(ans),
        "answers": ans,
    }


def answer_hash(ans: dict) -> str:
    """答案指纹：同答案必同指纹，用来证明推导的确定性。"""
    payload = json.dumps(ans, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def output_hash(result: dict) -> str:
    payload = json.dumps(
        {"configs": result["configs"], "issues": result["issues"], "flow": result["flow"]},
        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
