"""跨题冲突检测 A1–A14。

冲突与「单题缺失」是两件事，分开管：
- 单题缺失 -> 规则内部产 M-<编号>，说明这一条为什么算不出来；
- 跨题矛盾 -> 这里统一检测，产出 A 编号，并把涉及的配置降为 tbd。

冲突**不改配置值**，只标待确认。客户拿到的是「有依据的待确认」，
不是「算不出来」——这两者对实施的意义完全不同。
"""
from __future__ import annotations

from .rules.base import multi, num, rows, single, text

CONFLICTS = [
    {
        "id": "A1", "lv": "red", "title": "「审核中」节点是否启用",
        "q": [25, 27, 34], "affect": ["C15", "C16"],
        "when": lambda a: (single(a, "Q24") == "custom"
                           and single(a, "Q27") == "need"
                           and "audit" not in multi(a, "Q25")
                           and "audit" in multi(a, "Q34")),
        "msg": "第 25 题状态节点未勾选「审核中」，第 27 题却要求审核，"
               "第 34 题又把「审核中」列入必须人工流转的节点。",
        "suggest": "按「启用审核中节点」配置，流程图中该节点为虚线框待确认",
    },
    {
        "id": "A2", "lv": "red", "title": "预警等级数与通知表行数不一致",
        "q": [59, 61], "affect": ["C36", "C38"],
        "when": lambda a: _lv_mismatch(a),
        "msg": "第 59 题勾选的预警等级数与第 61 题填写的通知对象行数不一致。",
        "suggest": "按通知表实际行数配置等级，否则多出的等级无通知对象",
    },
    {
        "id": "A3", "lv": "red", "title": "时限只有单位、没有数值",
        "q": [38, 53], "affect": ["C28", "C47"],
        "when": lambda a: _unit_without_number(a),
        "msg": "第 38 题整体超时、第 53 题挂起时长只选了单位，未填写具体数值。",
        "suggest": "补填具体数值；确认前不做任何默认假设，系统无法生成超时规则",
    },
    {
        "id": "A4", "lv": "red", "title": "收费范围与收费标准缺失",
        "q": [70, 71, 86, 88], "affect": ["C50", "C51", "C56"],
        "when": lambda a: (single(a, "Q70") == "partial"
                           and not _q70_scope(a)
                           and not [r for r in rows(a, "Q86") if (r.get("收费标准") or "").strip()]),
        "msg": "第 70 题选「部分收费」，但未说明哪些工单类型收费，第 86 题收费标准表也为空。",
        "suggest": "先明确收免边界与价格表，否则收费模块无法配置",
    },
    {
        "id": "A5", "lv": "red", "title": "服务项是否细分：第 28 题与第 85 题冲突",
        "q": [28, 85], "affect": ["C09", "C55"],
        "when": lambda a: (single(a, "Q28") == "no" and single(a, "Q85") == "by_item"),
        "msg": "第 28 题选「不需要细分，用工单类型即可」，第 85 题却选「按具体服务项定价」。",
        "suggest": "以第 85 题为定价维度，服务项字典需建二级结构；或改回按类型定价",
    },
    {
        "id": "A6", "lv": "red", "title": "主办人 / 协办人是否区分未填",
        "q": [23], "affect": ["C24"],
        "when": lambda a: (single(a, "Q23") == "yes" and single(a, "Q23b") is None),
        "msg": "第 23 题选了「需要多人协同」，但追问项「是否区分主办人与协办人」两个选项都没勾。",
        "suggest": "必须二选一，否则协作工单的办结权限无法判定",
    },
    {
        "id": "A7", "lv": "amber", "title": "抢单窗口与优先级支撑信息缺失",
        "q": [30, 32, 43, 45], "affect": ["C22", "C62"],
        "when": lambda a: ((single(a, "Q32") == "limit" and not text(a, "Q32_note"))
                           or ("by_type" in multi(a, "Q43")
                               and not [r for r in rows(a, "Q45") if (r.get("优先级") or "").strip()])),
        "msg": "第 32 题选「有限制」但未填时段，或第 43 题按类型划分优先级但第 45 题优先级表为空。",
        "suggest": "补填抢单时段与各类型优先级；否则相关配置按不适用处理",
    },
    {
        "id": "A8", "lv": "amber", "title": "升级链路与关闭审批重叠",
        "q": [27, 33, 62, 79], "affect": ["C16", "C23", "C39", "C41"],
        "when": lambda a: ((single(a, "Q33") == "escalate" and "role" in multi(a, "Q62"))
                           or (single(a, "Q27") == "need" and "close" in multi(a, "Q79"))),
        "msg": "无人接单自动升级与预警升级按角色通知两条链路叠加；办结前审核与工单关闭审批也存在重叠。",
        "suggest": "明确由哪一条链路承担升级，以及「审核中」与「关闭审批」谁负责办结校验",
    },
    {
        "id": "A9", "lv": "amber", "title": "预警触发时点与适用范围信息不足",
        "q": [57, 60], "affect": ["C34", "C37"],
        "when": lambda a: ((set(multi(a, "Q60")) == {"before", "after"}
                            and num(a, "Q60b")[0] is None)
                           or (single(a, "Q57") == "some_type" and not text(a, "Q57_note"))),
        "msg": "第 60 题同时勾选「超时前提醒」与「超时后提醒」，但只填了一个时长；或第 57 题选特定类型但未列出类型。",
        "suggest": "补填超时后的提醒间隔与适用的工单类型",
    },
    {
        "id": "A10", "lv": "amber", "title": "评价时限单位口径未明确",
        "q": [66], "affect": ["C65"],
        "when": lambda a: (num(a, "Q66")[1] == "month"),
        "msg": "第 66 题评价时限以「月」为单位，未说明是自然月还是 30 天。",
        "suggest": "统一按自然月计算，并在报表口径中注明",
    },
    {
        "id": "A11", "lv": "red", "title": "预约时间窗倒挂",
        "q": [75, 76], "affect": ["C58"],
        "when": lambda a: _appointment_inverted(a),
        "msg": "最多可提前预约的天数小于最少需提前预约的时长，预约窗口为空。",
        "suggest": "重新确认两项数值，最多提前必须大于最少提前",
    },
    {
        "id": "A12", "lv": "amber", "title": "回访时限与工单超时口径不一致",
        "q": [39, 92], "affect": ["C77", "C79"],
        "when": lambda a: _visit_sla_conflict(a),
        "msg": "回访任务完成时限小于工单处理节点的时限，回访任务可能先于工单处理完成而超时。",
        "suggest": "回访时限应从工单办结后开始计时，且不短于最短处理节点时限",
    },
    {
        "id": "A13", "lv": "red", "title": "混合业态项目未归入三类业态",
        "q": [2, 3], "affect": ["C03", "C04"],
        "when": lambda a: any(("/" in (r.get("项目业态") or "") or "、" in (r.get("项目业态") or ""))
                              for r in rows(a, "Q3")),
        "msg": "项目表中有项目同时标注两种业态，但第 2 题只按三类业态勾选，混合项目无归属。",
        "suggest": "按主要业态归属，或拆为独立项目建账",
    },
    {
        "id": "A14", "lv": "red", "title": "超时维度勾选「两者都要」但只有一边有值",
        "q": [37, 38, 39], "affect": ["C28", "C29"],
        "when": lambda a: _sla_both_missing(a),
        "msg": "第 37 题同时勾选整体与节点计时，但整体时限或节点时限表有一边为空。",
        "suggest": "补齐两边时限，或改回单一计时口径",
    },
]


# ---------------------------------------------------------------- 辅助判定

def _q70_scope(ans) -> str:
    v = ans.get("Q70_note")
    return v.strip() if isinstance(v, str) else ""


def _lv_mismatch(ans) -> bool:
    picked = [v for v in ("l1", "l2", "l3", "l4") if v in multi(ans, "Q59")]
    filled = [r for r in rows(ans, "Q61")
              if (r.get("通知对象（人员 / 角色）") or r.get("通知对象") or "").strip()]
    if not picked or not filled:
        return False
    return len(picked) != len(filled)


def _unit_without_number(ans) -> bool:
    for k in ("Q38", "Q53"):
        n, u = num(ans, k)
        if u and u != "unlimited" and n is None:
            return True
    return False


def _appointment_inverted(ans) -> bool:
    n75, _ = num(ans, "Q75")
    n76, u76 = num(ans, "Q76")
    if n75 is None or n76 is None:
        return False
    day = n76 / 24 if (u76 or "hour") == "hour" else n76
    return day > n75


def _visit_sla_conflict(ans) -> bool:
    n92, u92 = num(ans, "Q92b")
    if n92 is None or u92 is None or u92 == "unlimited":
        return False
    mins92 = n92 * {"min": 1, "hour": 60, "day": 1440}.get(u92, 1)
    best = None
    for r in rows(ans, "Q39"):
        txt = (r.get("超时时长") or "").strip()
        import re
        m = re.search(r"(\d+(?:\.\d+)?)\s*(分钟|小时|天)", txt)
        if not m:
            continue
        v = float(m.group(1)) * {"分钟": 1, "小时": 60, "天": 1440}[m.group(2)]
        best = v if best is None else min(best, v)
    return best is not None and mins92 < best


def _sla_both_missing(ans) -> bool:
    if set(multi(ans, "Q37")) != {"overall", "per_node"}:
        return False
    n38, _ = num(ans, "Q38")
    has_rows = bool([r for r in rows(ans, "Q39") if (r.get("超时时长") or "").strip()])
    return (n38 is None) or (not has_rows)


# ---------------------------------------------------------------- 对外接口

def check(ans: dict) -> list[dict]:
    """跑全部冲突，命中的返回 issue 结构。顺序固定，保证输出可复现。"""
    out = []
    for c in CONFLICTS:
        try:
            hit = bool(c["when"](ans))
        except Exception:            # 单条判定异常不能让整个推导挂掉
            hit = False
        if not hit:
            continue
        out.append({
            "id": c["id"], "lv": c["lv"], "title": c["title"],
            "msg": c["msg"], "q": c["q"], "affect": c["affect"],
            "block": c["lv"] == "red", "suggest": c["suggest"],
        })
    return out


def index() -> list[dict]:
    """给前端展示「一共有哪些冲突可被检出」，不含触发状态。"""
    return [{"id": c["id"], "lv": c["lv"], "title": c["title"],
             "q": c["q"], "affect": c["affect"], "suggest": c["suggest"]}
            for c in CONFLICTS]
