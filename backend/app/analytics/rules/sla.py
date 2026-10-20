"""C26–C40 时限与预警。

输入题号：36–41、56–64

这一组最容易出错，因为时限是「数值 + 单位」两个字段，问卷里常常只填一个。
只填单位不填数值 → tbd（这是 A3），绝不按行业惯例补 24 小时。
"""
from __future__ import annotations

import re

from .base import (cfg, multi, num, rows, single, text, tbd, missing, warn, rule,
                   labels, num_cn, NODE_CN, LEVEL_CN, PLAN_CN, UNIT_CN)

DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(分钟|小时|天|工作日|min|h|d)")
UNIT_MAP = {"分钟": "min", "min": "min", "小时": "hour", "h": "hour",
            "天": "day", "d": "day", "工作日": "day"}


def parse_duration(s: str) -> tuple[float | None, str | None]:
    """从「30 分钟」「4 小时」「2工作日」里取数值与单位。取不到返回 (None, None)。"""
    if not s:
        return None, None
    m = DUR_RE.search(str(s))
    if not m:
        return None, None
    return float(m.group(1)), UNIT_MAP.get(m.group(2))


def _node_rows(ans: dict) -> dict[str, tuple[float | None, str | None]]:
    """第 39 题表格 -> {节点中文名: (数值, 单位)}"""
    out = {}
    for r in rows(ans, "Q39"):
        node = (r.get("节点名称") or "").strip()
        if not node:
            continue
        out[node] = parse_duration(r.get("超时时长") or "")
    return out


@rule("R-SLA-01", "sla", [36], "超时是否扣除非工作时段",
      emits=["C26"])
def c26_caliber(ans):
    v = single(ans, "Q36")
    if v == "exclude":
        return [cfg("R-SLA-01", "C26", "sla_caliber", "超时计时口径",
                    "仅计工作时间，非工作时段不计入", 36, "按第 4 题服务时段扣除")], []
    if v == "include":
        return [cfg("R-SLA-01", "C26", "sla_caliber", "超时计时口径",
                    "不排除，按自然时间计算", 36, "夜间与节假日同样计时")], []
    return [tbd("R-SLA-01", "C26", "sla_caliber", "超时计时口径", "第 36 题未回答", 36)], \
           [missing("C26", "超时计时口径", 36, "未确定是否扣除非工作时段，超时判定无法执行")]


@rule("R-SLA-02", "sla", [37], "整体 / 节点两种口径可并存",
      emits=["C27"])
def c27_dimension(ans):
    vs = multi(ans, "Q37")
    if not vs:
        return [tbd("R-SLA-02", "C27", "sla_dimension", "超时计算维度", "第 37 题未回答", 37)], \
               [missing("C27", "超时计算维度", 37, "未确定按整体还是按节点计时")]
    if len(vs) == 2:
        return [cfg("R-SLA-02", "C27", "sla_dimension", "超时计算维度",
                    "整体任务 + 各节点，两者并行计算", 37,
                    "两套计时同时生效，告警分别触发")], []
    v = vs[0]
    txt = "按整体任务（受理到办结）" if v == "overall" else "按各节点分别计算"
    return [cfg("R-SLA-02", "C27", "sla_dimension", "超时计算维度", txt, 37)], []


@rule("R-SLA-03", "sla", [37, 38], "整体时限需数值与单位齐全；只选单位不填数即 A3",
      emits=["C28"])
def c28_overall(ans):
    if "overall" not in multi(ans, "Q37"):
        return [cfg("R-SLA-03", "C28", "overall_sla", "整体任务时限",
                    "不适用（未启用整体任务计时）", 37, "仅按节点计时")], []
    n, u = num(ans, "Q38")
    if n is None and u is None:
        return [tbd("R-SLA-03", "C28", "overall_sla", "整体任务时限",
                    "第 38 题未填整体超时时长", 38)], \
               [missing("C28", "整体任务时限", 38, "启用整体计时却未填时长，整体超时无法判定")]
    if n is None:
        return [tbd("R-SLA-03", "C28", "overall_sla", "整体任务时限",
                    f"第 38 题只选了单位「{UNIT_CN.get(u, u)}」未填数值", [37, 38])], \
               [warn("C28", "整体任务时限", [37, 38],
                     f"第 38 题选了单位（{u}）但未填写具体数值，系统无法生成整体超时规则",
                     "请补填具体数值；确认前不做默认假设", block=True)]
    if u is None:
        return [tbd("R-SLA-03", "C28", "overall_sla", "整体任务时限",
                    "第 38 题填了数值但未选单位", [37, 38])], \
               [warn("C28", "整体任务时限", [37, 38],
                     "第 38 题有数值但单位缺失，无法判定是小时还是天",
                     "请补选单位", block=True)]
    return [cfg("R-SLA-03", "C28", "overall_sla", "整体任务时限",
                num_cn(n, u), [37, 38],
                "仅计工作时间" if single(ans, "Q36") == "exclude" else "按自然时间")], []


@rule("R-SLA-04", "sla", [39], "节点时限取第 39 题表格，逐节点独立判定",
      emits=["C29", "C30"])
def c29_c30_nodes(ans):
    nm = _node_rows(ans)
    if not nm:
        return [tbd("R-SLA-04", "C29", "accept_sla", "接单节点时限",
                    "第 39 题节点时限表为空", 39),
                tbd("R-SLA-04", "C30", "process_sla", "处理节点时限",
                    "第 39 题节点时限表为空", 39)], \
               [missing("C29", "节点时限", 39, "节点时限表为空，各节点超时无法判定")]

    out, issues = [], []
    for code, key, node, label in (
            ("C29", "accept_sla", "待接收", "接单节点时限"),
            ("C30", "process_sla", "处理中", "处理节点时限")):
        if node not in nm:
            out.append(tbd("R-SLA-04", code, key, label, f"第 39 题未列「{node}」节点时限", 39))
            issues.append(warn(code, label, 39, f"节点时限表缺少「{node}」行",
                               "该节点超时无法判定", block=False))
            continue
        n, u = nm[node]
        if n is None or u is None:
            out.append(tbd("R-SLA-04", code, key, label,
                           f"第 39 题「{node}」的时长格式无法解析", 39))
            issues.append(warn(code, label, 39, f"「{node}」行时长无法解析为数值+单位",
                               "请按「30 分钟」「4 小时」格式填写", block=True))
            continue
        out.append(cfg("R-SLA-04", code, key, label, num_cn(n, u), 39,
                       f"节点：{node}"))
    return out, issues


@rule("R-SLA-05", "sla", [40], "是否按工单类型差异化超时",
      emits=["C31"])
def c31_by_type(ans):
    v = single(ans, "Q40")
    if v == "diff":
        return [cfg("R-SLA-05", "C31", "sla_by_type", "按类型差异化",
                    "启用，按工单类型分别配置时限", 40, "需逐类型维护时限表")], \
               [warn("C31", "按类型差异化", 40, "启用差异化但未给出各类型差异",
                     "需补充各工单类型的时限", block=False)]
    if v == "unified":
        return [cfg("R-SLA-05", "C31", "sla_by_type", "按类型差异化",
                    "不启用，全类型统一标准", 40)], []
    return [cfg("R-SLA-05", "C31", "sla_by_type", "按类型差异化",
                "不启用，全类型统一标准", 40, "未回答，按统一标准处理", st="def")], \
           [warn("C31", "按类型差异化", 40, "未回答，暂按统一标准处理", "需客户确认", block=False)]


@rule("R-SLA-06", "sla", [41], "超时通知对象",
      emits=["C32"])
def c32_overtime_notify(ans):
    vs = multi(ans, "Q41")
    if not vs:
        return [tbd("R-SLA-06", "C32", "overtime_notify", "超时通知对象",
                    "第 41 题未勾选", 41)], \
               [missing("C32", "超时通知对象", 41, "超时后无人收到通知，预警链路断开")]
    return [cfg("R-SLA-06", "C32", "overtime_notify", "超时通知对象",
                " + ".join(PLAN_CN.get(v, v) for v in vs), 41)], []


@rule("R-SLA-07", "sla", [56], "预警总开关",
      emits=["C33"])
def c33_warn_switch(ans):
    v = single(ans, "Q56")
    if v == "yes":
        return [cfg("R-SLA-07", "C33", "alert_enabled", "预警启用", "启用", 56)], []
    if v == "no":
        return [cfg("R-SLA-07", "C33", "alert_enabled", "预警启用", "不启用", 56,
                    "仅保留超时通知，无预警等级")], []
    return [tbd("R-SLA-07", "C33", "alert_enabled", "预警启用", "第 56 题未回答", 56)], \
           [missing("C33", "预警启用", 56, "未确定是否启用预警，后续 7 项配置全部悬空")]


def _alert_off(ans, code, key, name, q, note=""):
    return cfg("R-SLA-08", code, key, name, "不适用（未启用预警）", q, note, st="def")


@rule("R-SLA-08", "sla", [56, 57], "预警适用范围",
      emits=["C34"])
def c34_scope(ans):
    if single(ans, "Q56") != "yes":
        return [_alert_off(ans, "C34", "alert_scope", "预警适用范围", 56)], []
    v = single(ans, "Q57")
    if v == "all":
        return [cfg("R-SLA-08", "C34", "alert_scope", "预警适用范围",
                    "所有项目统一预警", 57)], []
    if v == "some_project":
        return [cfg("R-SLA-08", "C34", "alert_scope", "预警适用范围",
                    "仅个别项目预警", 57, "项目清单未填，需补充")], \
               [warn("C34", "预警适用范围", 57, "选「仅个别项目」但未列出项目",
                     "需补充预警项目清单", block=False)]
    if v == "some_type":
        scope = text(ans, "Q57_note")
        if scope:
            return [cfg("R-SLA-08", "C34", "alert_scope", "预警适用范围",
                        f"仅特定工单类型预警：{scope}", [57], "按类型触发")], []
        return [cfg("R-SLA-08", "C34", "alert_scope", "预警适用范围",
                    "仅特定工单类型预警", 57, "类型清单未填，需补充")], \
               [warn("C34", "预警适用范围", 57, "选「仅特定工单类型」但未列出类型",
                     "需补充预警类型清单，否则全部类型都会触发或都不触发", block=True)]
    return [tbd("R-SLA-08", "C34", "alert_scope", "预警适用范围", "第 57 题未回答", 57)], \
           [missing("C34", "预警适用范围", 57, "未确定预警范围")]


@rule("R-SLA-09", "sla", [56, 58, "58b"], "预警类型与需预警节点",
      emits=["C35"])
def c35_alert_types(ans):
    if single(ans, "Q56") != "yes":
        return [_alert_off(ans, "C35", "alert_types", "预警类型", 56)], []
    vs = multi(ans, "Q58")
    if not vs:
        return [tbd("R-SLA-09", "C35", "alert_types", "预警类型", "第 58 题未勾选", 58)], \
               [missing("C35", "预警类型", 58, "启用预警但未选预警类型")]
    parts = []
    if "node" in vs:
        nodes = [n for n in NODE_CN if n in multi(ans, "Q58b")]
        if nodes:
            parts.append("节点预警（" + "、".join(NODE_CN[n] for n in nodes) + "）")
        else:
            parts.append("节点预警（节点未指定）")
    if "overall" in vs:
        parts.append("整体预警")
    if "hang" in vs:
        parts.append("挂起预警")
    out = [cfg("R-SLA-09", "C35", "alert_types", "预警类型", " + ".join(parts), [58, "58b"])]
    issues = []
    if "node" in vs and not multi(ans, "Q58b"):
        issues.append(warn("C35", "预警类型", [58, "58b"],
                           "启用节点预警但未勾选需预警的节点",
                           "节点预警无法绑定具体节点", block=True))
    return out, issues


@rule("R-SLA-10", "sla", [56, 59, 61], "预警等级以勾选为准，通知表行数不一致时提示",
      emits=["C36"])
def c36_levels(ans):
    if single(ans, "Q56") != "yes":
        return [_alert_off(ans, "C36", "alert_levels", "预警等级", 56)], []
    vs = [v for v in ("l1", "l2", "l3", "l4") if v in multi(ans, "Q59")]
    if not vs:
        return [tbd("R-SLA-10", "C36", "alert_levels", "预警等级", "第 59 题未勾选", 59)], \
               [missing("C36", "预警等级", 59, "启用预警但未设等级")]
    filled = [r for r in rows(ans, "Q61") if (r.get("通知对象（人员 / 角色）") or r.get("通知对象") or "").strip()]
    return [cfg("R-SLA-10", "C36", "alert_levels", "预警等级",
                f"{len(vs)} 级：" + " / ".join(LEVEL_CN[v] for v in vs), [59, 61],
                f"通知表填写 {len(filled)} 行")], []


@rule("R-SLA-11", "sla", [56, 60, "60b"], "预警触发时点；选「超时前」必须给时长",
      emits=["C37"])
def c37_trigger(ans):
    if single(ans, "Q56") != "yes":
        return [_alert_off(ans, "C37", "alert_trigger", "预警触发时点", 56)], []
    vs = multi(ans, "Q60")
    if not vs:
        return [tbd("R-SLA-11", "C37", "alert_trigger", "预警触发时点", "第 60 题未勾选", 60)], \
               [missing("C37", "预警触发时点", 60, "未确定触发时点，预警无法计算")]
    parts, issues = [], []
    if "before" in vs:
        n, u = num(ans, "Q60b")
        if n is None or u is None:
            parts.append("超时前提醒（时长未填）")
            issues.append(warn("C37", "预警触发时点", [60, "60b"],
                               "勾选「超时前提醒」但未填提前时长",
                               "提前预警无法计算，需补填", block=True))
        else:
            parts.append(f"超时前 {num_cn(n, u)}提醒")
    if "after" in vs:
        parts.append("超时后提醒（间隔未定义）")
        issues.append(warn("C37", "预警触发时点", 60,
                           "勾选「超时后提醒」但未定义超时后的提醒间隔",
                           "超时后按多久提醒一次未定义，需补充", block=False))
    return [cfg("R-SLA-11", "C37", "alert_trigger", "预警触发时点",
                " + ".join(parts), [60, "60b"])], issues


@rule("R-SLA-12", "sla", [56, 61], "各级预警通知对象取第 61 题表格",
      emits=["C38"])
def c38_alert_notify(ans):
    if single(ans, "Q56") != "yes":
        return [_alert_off(ans, "C38", "alert_notify", "预警通知对象", 56)], []
    rs = [r for r in rows(ans, "Q61") if (r.get("预警等级") or "").strip()]
    if not rs:
        return [tbd("R-SLA-12", "C38", "alert_notify", "预警通知对象",
                    "第 61 题通知表为空", 61)], \
               [missing("C38", "预警通知对象", 61, "各级预警无人接收，预警等于没配")]
    parts = []
    for r in rs:
        lvl = (r.get("预警等级") or "").strip()
        who = (r.get("通知对象（人员 / 角色）") or r.get("通知对象") or "").strip()
        parts.append(f"{lvl} → {who or '未填'}")
    return [cfg("R-SLA-12", "C38", "alert_notify", "预警通知对象",
                "；".join(parts), 61, f"{len(rs)} 级通知链路")], []


@rule("R-SLA-13", "sla", [62, 63, "63b"], "预警升级链路与升级间隔",
      emits=["C39"])
def c39_escalation(ans):
    if single(ans, "Q56") != "yes":
        return [_alert_off(ans, "C39", "alert_escalation", "预警升级", 56)], []
    vs = multi(ans, "Q62")
    if not vs:
        return [tbd("R-SLA-13", "C39", "alert_escalation", "预警升级",
                    "第 62 题未勾选升级通知对象", 62)], \
               [missing("C39", "预警升级", 62, "预警升级无人接收")]
    base = " + ".join(PLAN_CN.get(v, v) for v in vs)
    esc = single(ans, "Q63")
    if esc == "once":
        return [cfg("R-SLA-13", "C39", "alert_escalation", "预警升级",
                    f"{base}；只通知一次，不逐级升级", [62, 63])], []
    if esc == "escalate":
        rs = [r for r in rows(ans, "Q63b") if (r.get("升级层级") or "").strip()]
        if not rs:
            return [tbd("R-SLA-13", "C39", "alert_escalation", "预警升级",
                        "选逐级升级但升级表为空", [63, "63b"])], \
                   [missing("C39", "预警升级", [63, "63b"], "逐级升级缺层级与间隔，链路无法生成")]
        chain = "；".join(
            f"{r.get('升级层级','').strip()} → {(r.get('通知对象') or '').strip()}"
            f"（{(r.get('间隔时长') or '').strip()}）" for r in rs)
        return [cfg("R-SLA-13", "C39", "alert_escalation", "预警升级",
                    f"{base}；逐级升级：{chain}", [62, 63, "63b"])], []
    return [cfg("R-SLA-13", "C39", "alert_escalation", "预警升级",
                f"{base}；升级机制未答，按只通知一次处理", 62,
                "第 63 题未回答", st="def")], \
           [warn("C39", "预警升级", 63, "未确认是否逐级升级", "暂按只通知一次处理", block=False)]


@rule("R-SLA-14", "sla", [64], "升级通知渠道",
      emits=["C40"])
def c40_channels(ans):
    vs = multi(ans, "Q64")
    if not vs:
        return [tbd("R-SLA-14", "C40", "notify_channels", "通知方式", "第 64 题未勾选", 64)], \
               [missing("C40", "通知方式", 64, "无通知渠道，预警发不出去")]
    return [cfg("R-SLA-14", "C40", "notify_channels", "通知方式",
                "仅「" + "、".join(PLAN_CN.get(v, v) for v in vs) + "」", 64)], []


def derive(ans: dict):
    cfgs, issues = [], []
    for fn in (c26_caliber, c27_dimension, c28_overall, c29_c30_nodes, c31_by_type,
               c32_overtime_notify, c33_warn_switch, c34_scope, c35_alert_types,
               c36_levels, c37_trigger, c38_alert_notify, c39_escalation, c40_channels):
        c, i = fn(ans)
        cfgs += c
        issues += i
    return cfgs, issues
