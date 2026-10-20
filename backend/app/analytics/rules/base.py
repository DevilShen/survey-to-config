"""配置推导规则的公共基座。

三条硬约束在这里落地，不在规则里各自实现：
1. 确定性：规则是纯函数，只读 ans，不读时间/随机/网络/库。
2. 可解释：每条规则有 ID、有输入题号，产出配置时自动带 src 与 rule。
3. 不猜：缺失或冲突一律 tbd，绝不填一个"看起来合理"的值。

规则不直接写 "第 N 题" 这种展示文案，只写题号；展示文案由 _src() 统一生成，
这样以后改题号顺序不用改规则。
"""
from __future__ import annotations

from typing import Any, Callable

# ------------------------------------------------------------------ 注册表

RULES: dict[str, dict] = {}

GROUP_TITLES = {
    "org": "组织与基础参数",
    "catalog": "报事报修类型字典",
    "flow": "工单流转流程",
    "sla": "时限与预警",
    "approval": "审批与挂起",
    "fee": "收费与预约",
    "priority": "优先级、评价与回访",
}

GROUP_ORDER = ["org", "catalog", "flow", "sla", "approval", "fee", "priority"]


def rule(rid: str, group: str, inputs: list[int], desc: str,
         emits: list[str] | None = None) -> Callable:
    """声明一条规则。inputs 是题号列表（整数），emits 是产出的配置编号。"""
    if rid in RULES:
        raise ValueError(f"规则 ID 重复：{rid}")
    if group not in GROUP_TITLES:
        raise ValueError(f"未知分组：{group}")
    RULES[rid] = {"desc": desc, "inputs": inputs, "group": group,
                  "emits": emits or []}

    def deco(fn: Callable) -> Callable:
        fn.rule_id = rid
        fn.group = group
        return fn
    return deco


# ------------------------------------------------------------------ 载体

def _src(q: int | list[int]) -> str:
    """题号 -> 展示文案。单个用「第 N 题」，多个用「第 N、M 题」。"""
    if isinstance(q, list):
        return "第 " + "、".join(str(x) for x in q) + " 题"
    return f"第 {q} 题"


def cfg(rid: str, code: str, k: str, name: str, v: str,
        q: int | list[int] | str = "", note: str = "", st: str = "ok") -> dict:
    """产出一条配置项。st: ok 已确认 / def 默认值 / tbd 待确认。"""
    RULES.setdefault(rid, {"desc": name, "inputs": [], "group": "", "emits": [code]})
    src = q if isinstance(q, str) else _src(q)
    return {"code": code, "group": RULES[rid]["group"], "k": k, "name": name,
            "v": v, "st": st, "src": src, "rule": rid, "note": note}


def tbd(rid: str, code: str, k: str, name: str, why: str,
        q: int | list[int] | str = "", note: str = "") -> dict:
    """缺失或冲突时的配置项：值写"无法推导"，并说明为什么。"""
    return cfg(rid, code, k, name, f"无法推导（{why}）", q, note, st="tbd")


def default(rid: str, code: str, k: str, name: str, v: str, note: str) -> dict:
    return cfg(rid, code, k, name, v, "默认值", note, st="def")


def missing(code: str, name: str, q: int | list[int], why: str) -> dict:
    """缺答案产生的关注项。编号 M-<配置编号> 稳定，便于回归比对。"""
    return {"id": f"M-{code}", "lv": "red", "title": f"{name}无法推导",
            "msg": why, "q": q if isinstance(q, list) else [q],
            "affect": [code], "block": True,
            "suggest": f"补充第 {q if not isinstance(q, list) else '、'.join(map(str, q))} 题答案后可自动推导"}


def warn(code: str, name: str, q: int | list[int], why: str,
         suggest: str, block: bool = False) -> dict:
    return {"id": f"M-{code}", "lv": "amber", "title": f"{name}需确认",
            "msg": why, "q": q if isinstance(q, list) else [q],
            "affect": [code], "block": block, "suggest": suggest}


# ------------------------------------------------------------------ 取值助手
#
# 答案在 normalize 之后统一成：
#   单选 -> str | None
#   多选 -> list[str]（空列表表示未答）
#   数值 -> {"v": 24, "unit": "hour"} | None
#   表格 -> list[dict]（空列表表示未答）
#   文本 -> str | None
#   时段 -> {"start": "08:30", "end": "17:30"} | None

def single(ans: dict, k: str) -> str | None:
    """单选题取值。选项值可能是数字（如第 80 题审批级数 1/2/3），统一转成字符串。"""
    v = ans.get(k)
    if v is None or v == "" or v is False:
        return None
    if isinstance(v, bool):
        return "true" if v else None
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    return v if isinstance(v, str) else None


def multi(ans: dict, k: str) -> list[str]:
    v = ans.get(k)
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    if isinstance(v, str) and v:
        return [v]
    return []


def num(ans: dict, k: str) -> tuple[float | None, str | None]:
    """返回 (数值, 单位)。两者任一缺失都按缺失处理，调用方自己判断。"""
    v = ans.get(k)
    if isinstance(v, dict):
        n = v.get("v")
        u = v.get("unit")
        try:
            n = float(n) if n is not None and n != "" else None
        except (TypeError, ValueError):
            n = None
        return n, (u if isinstance(u, str) and u else None)
    if isinstance(v, (int, float)):
        return float(v), None
    return None, None


def rows(ans: dict, k: str) -> list[dict]:
    v = ans.get(k)
    return [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []


def text(ans: dict, k: str) -> str | None:
    v = ans.get(k)
    return v.strip() if isinstance(v, str) and v.strip() else None


def window(ans: dict, k: str) -> tuple[str, str] | None:
    v = ans.get(k)
    if isinstance(v, dict) and v.get("start") and v.get("end"):
        return str(v["start"]), str(v["end"])
    return None


# ------------------------------------------------------------------ 展示助手

UNIT_CN = {"min": "分钟", "hour": "小时", "day": "天", "month": "个月", "unlimited": "不限时"}

NODE_CN = {"accept": "待接收", "pending": "待处理", "processing": "处理中",
           "audit": "审核中", "to_rate": "待评价", "to_visit": "待回访"}

TYPE_CN = {"repair": "报修", "complaint": "投诉", "consult": "咨询", "suggest": "建议",
           "release": "物品放行", "decorate": "装修申请", "clean": "保洁申请", "other": "其他"}

LEVEL_CN = {"l1": "提醒级", "l2": "一般", "l3": "严重", "l4": "非常严重"}

TERMINAL_CN = {"by_type": "工单类型", "by_terminal": "提报终端"}

ROLE_CN = {"cs": "客服中心", "eng": "工程部", "pm": "项目经理", "front": "前台",
           "other": "其他", "eng_sup": "工程主管", "auto": "系统自动", "any": "不限定"}

PLAN_CN = {"person": "指定人员", "role": "指定角色", "executor": "当前执行人",
           "owner": "组织负责人", "space_owner": "关联空间对应业主",
           "sms": "短信", "im": "站内信", "popup": "弹窗提醒"}

FEE_CN = {"material": "材料费", "labor": "人工费", "visit": "上门费", "other": "其他"}

OPER_CN = {"hang": "工单挂起", "close": "工单关闭"}

VISIT_TYPE_CN = {"satisfaction": "满意度回访", "complaint": "投诉回访",
                 "repair": "报修回访", "routine": "例行回访"}

VISIT_STATUS_CN = {"to_assign": "待分配", "to_visit": "待回访", "visiting": "回访中",
                   "to_audit": "待审核", "done": "已完成"}

DIM_CN = {"attitude": "服务人员态度", "timeliness": "响应及时", "skill": "技术水平",
          "communication": "沟通表达", "clean": "现场清理", "overall": "整体满意度"}


def labels(values: list[str], table: dict) -> str:
    """把选项值列表转成中文标签串。未知值原样保留，不静默丢弃。"""
    return "、".join(table.get(v, v) for v in values)


def num_cn(n: float, unit: str | None) -> str:
    """24 + 'hour' -> '24 小时'；整数不带小数点。"""
    s = str(int(n)) if float(n).is_integer() else str(n)
    return f"{s} {UNIT_CN.get(unit or '', unit or '')}".strip()


def manhour(n: float | None, unit: str | None) -> float | None:
    """统一折算成分钟，用于跨单位比较。"""
    if n is None or unit is None:
        return None
    return n * {"min": 1, "hour": 60, "day": 1440}.get(unit, 1)
