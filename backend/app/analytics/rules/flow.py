"""C14–C25 工单流转流程。

输入题号：6、23、"23b"、24、25、26、27、"27b"、30、"30b"、32、33、34、35、"35b"

流程模式（第 24 题）决定后面几题的适用性：
- 选「标准流程」时，状态节点取系统默认 6 节点，第 25–27 题不参与推导；
- 选「自定义」时按第 25–27 题生成，缺一即 tbd，不用默认值补齐。
"""
from __future__ import annotations

from .base import (cfg, single, tbd, missing, warn, rule, labels,
                   NODE_CN, ROLE_CN, RULES)

STANDARD_NODES = ["accept", "pending", "processing", "audit", "to_rate", "to_visit"]
NODE_ORDER = STANDARD_NODES

DISPATCH_CN = {"person": "直接指定到具体处理人", "role": "分配到角色，由角色内人员领取",
               "grab": "抢单模式，人员主动领取"}
ESCALATE_CN = {"escalate": "自动升级给上级", "transfer": "自动转派给其他人",
               "pool": "放入公共抢单池", "notify": "仅通知管理员，不自动处理"}


@rule("R-FLW-01", "flow", [24], "第 24 题决定用标准流程还是自定义流程",
      emits=["C14"])
def c14_mode(ans):
    v = single(ans, "Q24")
    if v == "custom":
        return [cfg("R-FLW-01", "C14", "flow_mode", "流程模式",
                    "自定义流程（不用系统标准流程）", 24,
                    "标准流程为 6 节点串行，本项目按第 25–27 题配置")], []
    if v == "standard":
        return [cfg("R-FLW-01", "C14", "flow_mode", "流程模式",
                    "系统标准流程（6 节点串行）", 24,
                    "待接收 → 待处理 → 处理中 → 审核中 → 待评价 → 待回访")], []
    return [tbd("R-FLW-01", "C14", "flow_mode", "流程模式", "第 24 题未回答", 24)], \
           [missing("C14", "流程模式", 24, "未确定流程模式，状态机无法生成")]


@rule("R-FLW-02", "flow", [24, 25], "自定义流程取第 25 题节点；标准流程取 6 节点默认值",
      emits=["C15"])
def c15_nodes(ans):
    mode = single(ans, "Q24")
    if mode == "standard":
        return [cfg("R-FLW-02", "C15", "flow_nodes", "启用状态节点",
                    "、".join(NODE_CN[n] for n in STANDARD_NODES), 24,
                    "标准流程默认节点，未按项目定制")], []
    if mode != "custom":
        return [tbd("R-FLW-02", "C15", "flow_nodes", "启用状态节点",
                    "流程模式未定", 24)], []
    vs = [n for n in NODE_ORDER if n in (ans.get("Q25") or [])]
    if not vs:
        return [tbd("R-FLW-02", "C15", "flow_nodes", "启用状态节点",
                    "第 25 题未勾选任何节点", 25)], \
               [missing("C15", "启用状态节点", 25, "自定义流程却未选节点，状态机无法生成")]
    return [cfg("R-FLW-02", "C15", "flow_nodes", "启用状态节点",
                "、".join(NODE_CN[n] for n in vs), [24, 25],
                f"共 {len(vs)} 个节点")], []


@rule("R-FLW-03", "flow", [24, 27, "27b"], "是否启用审核节点与审核人",
      emits=["C16"])
def c16_audit(ans):
    mode = single(ans, "Q24")
    v = single(ans, "Q27")
    if mode == "standard":
        return [cfg("R-FLW-03", "C16", "audit_node", "审核节点",
                    "启用「审核中」，审核人按标准流程角色配置", 24,
                    "标准流程自带审核节点", st="def")], []
    if v == "need":
        who = single(ans, "Q27b")
        if not who:
            return [tbd("R-FLW-03", "C16", "audit_node", "审核节点",
                        "第 27 题要求审核，但未指定审核人", [27, "27b"])], \
                   [missing("C16", "审核节点", [27, "27b"], "审核人未定，审核节点无法指派")]
        return [cfg("R-FLW-03", "C16", "audit_node", "审核节点",
                    f"启用「审核中」，审核人 = {ROLE_CN.get(who, who)}", [27, "27b"])], []
    if v == "none":
        return [cfg("R-FLW-03", "C16", "audit_node", "审核节点",
                    "不启用审核节点", 27, "办结即流转至待评价")], []
    return [tbd("R-FLW-03", "C16", "audit_node", "审核节点", "第 27 题未回答", 27)], \
           [warn("C16", "审核节点", 27, "未确认是否需要办结前审核",
                 "按不启用处理，需客户确认", block=False)]


@rule("R-FLW-04", "flow", [26], "节点是否允许跳过，默认禁止",
      emits=["C17"])
def c17_skip(ans):
    v = single(ans, "Q26")
    if v == "allow":
        return [cfg("R-FLW-04", "C17", "node_skip", "节点跳过",
                    "允许跳过指定节点（清单待客户补充）", 26,
                    "第 26 题未列可跳过节点，需补充")], \
               [warn("C17", "节点跳过", 26, "允许跳过但未指定哪些节点可跳",
                     "请客户补充可跳过节点清单", block=False)]
    if v == "deny":
        return [cfg("R-FLW-04", "C17", "node_skip", "节点跳过",
                    "全部节点禁止跳过", 26, "系统不得自动越过任何节点")], []
    return [cfg("R-FLW-04", "C17", "node_skip", "节点跳过",
                "全部节点禁止跳过", 26, "未回答，按最严口径处理", st="def")], \
           [warn("C17", "节点跳过", 26, "第 26 题未回答，按禁止跳过处理",
                 "需客户确认", block=False)]


@rule("R-FLW-05", "flow", [34], "人工流转节点清单，空则按全自动处理并提示",
      emits=["C18"])
def c18_manual(ans):
    vs = [n for n in NODE_ORDER if n in (ans.get("Q34") or [])]
    if not vs:
        return [cfg("R-FLW-05", "C18", "manual_nodes", "必须人工流转的节点",
                    "无（全部节点允许系统自动流转）", 34,
                    "第 34 题未勾选", st="def")], \
               [warn("C18", "必须人工流转的节点", 34,
                     "未勾选任何人工节点，工单可被系统全自动推进",
                     "建议至少保留「待接收」为人工节点", block=False)]
    return [cfg("R-FLW-05", "C18", "manual_nodes", "必须人工流转的节点",
                "、".join(NODE_CN[n] for n in vs), 34,
                f"{len(vs)} 个节点需人工点击推进")], []


@rule("R-FLW-06", "flow", [6], "受理岗位即第一受理人",
      emits=["C19"])
def c19_receiver(ans):
    v = single(ans, "Q6")
    if not v:
        return [tbd("R-FLW-06", "C19", "receiver_role", "受理岗位",
                    "第 6 题未回答", 6)], \
               [missing("C19", "受理岗位", 6, "第一受理人未定，派单默认值无法生成")]
    return [cfg("R-FLW-06", "C19", "receiver_role", "受理岗位",
                f"{ROLE_CN.get(v, v)}（第一受理人）", 6, "全项目统一")], []


@rule("R-FLW-07", "flow", [30], "分配方式决定工单池模型",
      emits=["C20"])
def c20_dispatch(ans):
    v = single(ans, "Q30")
    if not v:
        return [tbd("R-FLW-07", "C20", "dispatch_mode", "分配方式",
                    "第 30 题未回答", 30)], \
               [missing("C20", "分配方式", 30, "未确定派单方式，工单池模型无法生成")]
    note = {"person": "指定到人，需维护人员与工单类型的对应关系",
            "role": "非指定到人，按角色池领取",
            "grab": "需配置抢单可见范围与抢单窗口"}[v]
    return [cfg("R-FLW-07", "C20", "dispatch_mode", "分配方式", DISPATCH_CN[v], 30, note)], []


@rule("R-FLW-08", "flow", ["30b"], "接单范围是否限制",
      emits=["C21"])
def c21_range(ans):
    v = single(ans, "Q30b")
    if v == "restrict":
        return [cfg("R-FLW-08", "C21", "dispatch_scope", "接单范围限制",
                    "指定范围外的人员不能接单", 30, "需维护角色-项目对应关系")], []
    if v == "free":
        return [cfg("R-FLW-08", "C21", "dispatch_scope", "接单范围限制",
                    "不限制（范围外人员也可接单）", 30, "第 30 题补充项未勾，按不限制处理")], []
    return [cfg("R-FLW-08", "C21", "dispatch_scope", "接单范围限制",
                "不限制（范围外人员也可接单）", 30,
                "补充项未答，按不限制处理，需客户确认", st="def")], \
           [warn("C21", "接单范围限制", 30, "未选择接单范围限制",
                 "暂按不限制处理", block=False)]


@rule("R-FLW-09", "flow", [30, 32], "非抢单模式时抢单窗口直接判不适用",
      emits=["C22"])
def c22_grab_window(ans):
    if single(ans, "Q30") != "grab":
        return [cfg("R-FLW-09", "C22", "grab_window", "抢单时间窗口",
                    "不启用（不适用，非抢单模式）", [30, 32], "第 30 题未选抢单模式")], []
    v = single(ans, "Q32")
    if v == "limit":
        w = single(ans, "Q32_note")
        if not w:
            return [tbd("R-FLW-09", "C22", "grab_window", "抢单时间窗口",
                        "选有限制但未填开放时段", [30, 32])], \
                   [missing("C22", "抢单时间窗口", [30, 32], "抢单时段未填，窗口规则无法生成")]
        return [cfg("R-FLW-09", "C22", "grab_window", "抢单时间窗口", f"限制：{w}", [30, 32])], []
    if v == "free":
        return [cfg("R-FLW-09", "C22", "grab_window", "抢单时间窗口",
                    "无限制，随时可抢", [30, 32])], []
    return [tbd("R-FLW-09", "C22", "grab_window", "抢单时间窗口",
                "第 32 题未回答", [30, 32])], \
           [warn("C22", "抢单时间窗口", 32, "抢单模式但未确认是否限时段",
                 "暂按无限制处理", block=False)]


@rule("R-FLW-10", "flow", [33], "长时间无人接单的兜底动作",
      emits=["C23"])
def c23_timeout_action(ans):
    v = single(ans, "Q33")
    if not v:
        return [tbd("R-FLW-10", "C23", "no_taker_action", "接单超时后动作",
                    "第 33 题未回答", 33)], \
               [missing("C23", "接单超时后动作", 33, "无人接单的兜底动作未定，工单会一直挂着")]
    return [cfg("R-FLW-10", "C23", "no_taker_action", "接单超时后动作",
                ESCALATE_CN.get(v, v), 33,
                "与预警升级链路需去重" if v == "escalate" else "")], []


@rule("R-FLW-11", "flow", [23, "23b"], "多人协办与主办/协办区分是两件事，分别判定",
      emits=["C24"])
def c24_collab(ans):
    v = single(ans, "Q23")
    if v == "no":
        return [cfg("R-FLW-11", "C24", "collaboration", "主办 / 协办",
                    "不支持多人协同", 23, "单一处理人")], []
    if v != "yes":
        return [tbd("R-FLW-11", "C24", "collaboration", "主办 / 协办",
                    "第 23 题未回答", 23)], \
               [missing("C24", "主办 / 协办", 23, "未确认是否支持多人协同")]
    who = single(ans, "Q23b")
    if who == "distinguish":
        return [cfg("R-FLW-11", "C24", "collaboration", "主办 / 协办",
                    "支持多人协同，区分主办人与协办人", [23, "23b"],
                    "主办人负责办结，协办人配合")], []
    if who == "flat":
        return [cfg("R-FLW-11", "C24", "collaboration", "主办 / 协办",
                    "支持多人协同，不区分主协办", [23, "23b"],
                    "所有人平级，任一人可办结")], []
    # 支持协同但没选是否区分 —— 这是 A6，值不猜，标 tbd
    return [tbd("R-FLW-11", "C24", "collaboration", "主办 / 协办",
                "第 23 题要求协同，但未勾选是否区分主办/协办", 23)], \
           [warn("C24", "主办 / 协办", 23,
                 "支持多人协同，但追问项「是否区分主办人/协办人」两个选项都未勾",
                 "影响协作工单的权责判定与办结权限，需客户二选一", block=True)]


@rule("R-FLW-12", "flow", [35, "35b"], "转派权限与转派审批分开判定",
      emits=["C25"])
def c25_transfer(ans):
    v = single(ans, "Q35")
    if v == "no":
        return [cfg("R-FLW-12", "C25", "transfer", "转派",
                    "处理人不可自行转派", 35, "需转派由管理员操作")], []
    if v == "yes":
        need = single(ans, "Q35b")
        if need == "need":
            return [cfg("R-FLW-12", "C25", "transfer", "转派",
                        "处理人可自行转派，转派需审批", [35, "35b"])], []
        if need == "free":
            return [cfg("R-FLW-12", "C25", "transfer", "转派",
                        "处理人可自行转派，无需审批", [35, "35b"])], []
        return [cfg("R-FLW-12", "C25", "transfer", "转派",
                    "处理人可自行转派（是否审批未答，按无需审批）", 35,
                    "第 35 题追问项未答，需确认", st="def")], \
               [warn("C25", "转派", 35, "可转派但未确认是否需要审批",
                     "暂按无需审批处理", block=False)]
    return [tbd("R-FLW-12", "C25", "transfer", "转派", "第 35 题未回答", 35)], \
           [warn("C25", "转派", 35, "未确认是否允许转派", "暂按不允许处理", block=False)]


def derive(ans: dict):
    cfgs, issues = [], []
    for fn in (c14_mode, c15_nodes, c16_audit, c17_skip, c18_manual, c19_receiver,
               c20_dispatch, c21_range, c22_grab_window, c23_timeout_action,
               c24_collab, c25_transfer):
        c, i = fn(ans)
        cfgs += c
        issues += i
    return cfgs, issues
