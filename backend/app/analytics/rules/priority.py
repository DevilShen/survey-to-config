"""C61–C81 优先级、评价与回访。

输入题号：42–50、65–69、89–95、97–99

这一组覆盖三条独立链路（优先级 / 评价 / 回访），共同点是都有「启用开关」：
开关关掉时，后续配置一律写「不适用」，而不是留空——
留空会让实施以为还要配，最后配出一堆没人用的东西。
"""
from __future__ import annotations

from .base import (cfg, multi, num, rows, single, tbd, missing, warn, rule,
                   num_cn, labels, DIM_CN, VISIT_TYPE_CN, VISIT_STATUS_CN,
                   TERMINAL_CN)

EVAL_CONTENT_CN = {"overall": "总体满意度评分", "subitems": "分项打分",
                   "text": "文字评价", "image": "上传图片", "tags": "预设标签快选"}
TRIGGER_CN = {"done": "工单处理完成即触发", "bad_review": "评价为差评时触发",
              "overdue": "逾期未评价时触发"}
TPL_TYPE_CN = {"single": "单选题", "score": "评分题", "text": "填空题",
               "image": "上传图片", "file": "上传文件"}


@rule("R-PRI-01", "priority", [42], "优先级总开关",
      emits=["C61"])
def c61_enabled(ans):
    v = single(ans, "Q42")
    if v == "yes":
        return [cfg("R-PRI-01", "C61", "priority_enabled", "优先级启用", "启用", 42)], []
    if v == "no":
        return [cfg("R-PRI-01", "C61", "priority_enabled", "优先级启用", "不启用", 42,
                    "全部工单同一优先级")], []
    return [tbd("R-PRI-01", "C61", "priority_enabled", "优先级启用",
                "第 42 题未回答", 42)], \
           [missing("C61", "优先级启用", 42, "未确定是否启用优先级")]


@rule("R-PRI-02", "priority", [42, 43, 45], "优先级划分依据与分类型优先级表",
      emits=["C62"])
def c62_basis(ans):
    if single(ans, "Q42") != "yes":
        return [cfg("R-PRI-02", "C62", "priority_basis", "划分依据",
                    "不适用（未启用优先级）", 42, st="def")], []
    vs = multi(ans, "Q43")
    if not vs:
        return [tbd("R-PRI-02", "C62", "priority_basis", "划分依据",
                    "第 43 题未勾选划分依据", 43)], \
               [missing("C62", "划分依据", 43, "启用优先级但未定划分依据")]
    base = " + ".join(TERMINAL_CN.get(v, v) for v in vs)
    issues = []
    if "by_type" in vs:
        rs = [r for r in rows(ans, "Q45") if (r.get("优先级") or "").strip()]
        if not rs:
            issues.append(warn("C62", "划分依据", [43, 45],
                               "按工单类型划分优先级，但第 45 题优先级表为空",
                               "各类型优先级未定，等于没有优先级", block=True))
            return [tbd("R-PRI-02", "C62", "priority_basis", "划分依据",
                        "按类型划分但优先级表为空", [43, 45])], issues
        detail = "；".join(
            f"{(r.get('工单类型') or '').strip()} = {(r.get('优先级') or '').strip()}"
            for r in rs)
        return [cfg("R-PRI-02", "C62", "priority_basis", "划分依据",
                    f"{base}：{detail}", [43, 45])], issues
    return [cfg("R-PRI-02", "C62", "priority_basis", "划分依据", base, 43)], issues


@rule("R-PRI-03", "priority", [42, 43, 46], "业主端与员工端优先级是否有差异",
      emits=["C63"])
def c63_terminal(ans):
    if single(ans, "Q42") != "yes":
        return [cfg("R-PRI-03", "C63", "priority_terminal", "业主端 vs 员工端",
                    "不适用（未启用优先级）", 42, st="def")], []
    if "by_terminal" not in multi(ans, "Q43"):
        return [cfg("R-PRI-03", "C63", "priority_terminal", "业主端 vs 员工端",
                    "不适用（未按提报终端划分优先级）", 43, st="def")], []
    v = single(ans, "Q46")
    if v == "diff":
        return [cfg("R-PRI-03", "C63", "priority_terminal", "业主端 vs 员工端",
                    "有差异，业主端工单优先于员工端", 46, "差异幅度未填，需补充")], \
               [warn("C63", "业主端 vs 员工端", 46, "选有差异但未说明差多少",
                     "建议补充优先级差级数", block=False)]
    if v == "same":
        return [cfg("R-PRI-03", "C63", "priority_terminal", "业主端 vs 员工端",
                    "无差异", 46)], []
    return [tbd("R-PRI-03", "C63", "priority_terminal", "业主端 vs 员工端",
                "第 46 题未回答", 46)], \
           [warn("C63", "业主端 vs 员工端", 46, "未确认两端优先级是否有差异",
                 "暂按无差异处理", block=False)]


@rule("R-PRI-04", "priority", [65], "评价总开关",
      emits=["C64"])
def c64_eval_switch(ans):
    v = single(ans, "Q65")
    if v == "yes":
        return [cfg("R-PRI-04", "C64", "eval_enabled", "评价启用", "启用业主评价", 65)], []
    if v == "no":
        return [cfg("R-PRI-04", "C64", "eval_enabled", "评价启用", "不启用业主评价", 65,
                    "评价相关 5 项配置全部不适用")], []
    return [tbd("R-PRI-04", "C64", "eval_enabled", "评价启用", "第 65 题未回答", 65)], \
           [missing("C64", "评价启用", 65, "未确定是否启用评价")]


def _eval_off(code, key, name, q):
    return cfg("R-PRI-05", code, key, name, "不适用（未启用评价）", q, st="def")


@rule("R-PRI-05", "priority", [65, 66], "评价有效期",
      emits=["C65"])
def c65_valid(ans):
    if single(ans, "Q65") != "yes":
        return [_eval_off("C65", "eval_validity", "评价有效期", 65)], []
    n, u = num(ans, "Q66")
    if n is None:
        return [tbd("R-PRI-05", "C65", "eval_validity", "评价有效期",
                    "第 66 题未填天数", 66)], \
               [warn("C65", "评价有效期", 66, "启用评价但未填评价时限",
                     "评价入口无法自动关闭", block=True)]
    unit = u or "day"
    return [cfg("R-PRI-05", "C65", "eval_validity", "评价有效期",
                f"完工后 {num_cn(n, unit)}内可评价", 66,
                "按自然月计算" if unit == "month" else "按自然日计算")], []


@rule("R-PRI-06", "priority", [65, 67], "逾期未评价的处理口径",
      emits=["C66"])
def c66_expired(ans):
    if single(ans, "Q65") != "yes":
        return [_eval_off("C66", "eval_expired", "逾期未评价", 65)], []
    v = single(ans, "Q67")
    if v == "default_good":
        return [cfg("R-PRI-06", "C66", "eval_expired", "逾期未评价",
                    "默认好评", 67, "会影响满意度统计，需在报表口径中标注")], []
    if v == "no_count":
        return [cfg("R-PRI-06", "C66", "eval_expired", "逾期未评价",
                    "不计入评价", 67, "满意度分母只算已评价工单")], []
    return [tbd("R-PRI-06", "C66", "eval_expired", "逾期未评价", "第 67 题未回答", 67)], \
           [warn("C66", "逾期未评价", 67, "未确认逾期未评价如何处理",
                 "暂按不计入处理", block=False)]


@rule("R-PRI-07", "priority", [65, 68, "68b"], "评价是否可修改",
      emits=["C67"])
def c67_modify(ans):
    if single(ans, "Q65") != "yes":
        return [_eval_off("C67", "eval_modify", "评价修改", 65)], []
    v = single(ans, "Q68")
    if v == "deny":
        return [cfg("R-PRI-07", "C67", "eval_modify", "评价修改",
                    "不可修改", 68)], []
    if v == "allow":
        n, u = num(ans, "Q68b")
        if n is None:
            return [tbd("R-PRI-07", "C67", "eval_modify", "评价修改",
                        "允许修改但未填可修改期限", [68, "68b"])], \
                   [missing("C67", "评价修改", [68, "68b"], "可修改期限未填，修改窗口无法关闭")]
        return [cfg("R-PRI-07", "C67", "eval_modify", "评价修改",
                    f"允许修改，{num_cn(n, u or 'day')}内有效", [68, "68b"])], []
    return [tbd("R-PRI-07", "C67", "eval_modify", "评价修改", "第 68 题未回答", 68)], \
           [warn("C67", "评价修改", 68, "未确认评价是否可修改", "暂按不可修改处理", block=False)]


@rule("R-PRI-08", "priority", [65, 69], "评价模板内容",
      emits=["C68"])
def c68_template(ans):
    if single(ans, "Q65") != "yes":
        return [_eval_off("C68", "eval_template", "评价模板内容", 65)], []
    vs = [v for v in EVAL_CONTENT_CN if v in multi(ans, "Q69")]
    if not vs:
        return [tbd("R-PRI-08", "C68", "eval_template", "评价模板内容",
                    "第 69 题未勾选", 69)], \
               [missing("C68", "评价模板内容", 69, "评价模板为空，业主端无内容可填")]
    return [cfg("R-PRI-08", "C68", "eval_template", "评价模板内容",
                " + ".join(EVAL_CONTENT_CN[v] for v in vs), 69)], []


@rule("R-PRI-09", "priority", [97], "评价打分项",
      emits=["C69"])
def c69_dims(ans):
    if single(ans, "Q65") != "yes":
        return [_eval_off("C69", "eval_dims", "评价打分项", 65)], []
    vs = [v for v in DIM_CN if v in multi(ans, "Q97")]
    if not vs:
        return [tbd("R-PRI-09", "C69", "eval_dims", "评价打分项",
                    "第 97 题未勾选", 97)], \
               [missing("C69", "评价打分项", 97, "无打分项，评价无法量化")]
    return [cfg("R-PRI-09", "C69", "eval_dims", "评价打分项",
                "、".join(DIM_CN[v] for v in vs), 97, f"{len(vs)} 项")], []


@rule("R-PRI-10", "priority", [98], "打分项权重",
      emits=["C70"])
def c70_weight(ans):
    if single(ans, "Q65") != "yes":
        return [_eval_off("C70", "eval_weight", "打分权重", 65)], []
    v = single(ans, "Q98")
    if v == "equal":
        return [cfg("R-PRI-10", "C70", "eval_weight", "打分权重",
                    "各项等权", 98, "总分 = 各项算术平均")], []
    if v == "weight":
        return [tbd("R-PRI-10", "C70", "eval_weight", "打分权重",
                    "选择设置权重但未给出各分项权重值", 98)], \
               [warn("C70", "打分权重", 98, "需设置权重但未提供权重分配",
                     "请给出各分项占比（合计 100%）", block=True)]
    return [cfg("R-PRI-10", "C70", "eval_weight", "打分权重",
                "各项等权", 98, "第 98 题未回答，按等权处理", st="def")], \
           [warn("C70", "打分权重", 98, "未确认权重口径", "暂按等权处理", block=False)]


@rule("R-PRI-11", "priority", [47, 48], "回访总开关与执行人",
      emits=["C71"])
def c71_visit_switch(ans):
    v = single(ans, "Q47")
    if v == "no":
        return [cfg("R-PRI-11", "C71", "visit_enabled", "回访启用",
                    "不启用回访", 47, "回访相关 11 项配置全部不适用")], []
    if v != "yes":
        return [tbd("R-PRI-11", "C71", "visit_enabled", "回访启用",
                    "第 47 题未回答", 47)], \
               [missing("C71", "回访启用", 47, "未确定是否回访")]
    who = single(ans, "Q48")
    if not who:
        return [tbd("R-PRI-11", "C71", "visit_enabled", "回访启用",
                    "启用回访但未指定执行人", [47, 48])], \
               [missing("C71", "回访启用", [47, 48], "回访执行人未定，回访任务无法派发")]
    cn = {"cs": "客服人员", "auto": "系统自动回访", "pm": "项目经理",
          "any": "不限定，谁都可执行", "other": "其他"}
    return [cfg("R-PRI-11", "C71", "visit_enabled", "回访启用",
                f"启用，由{cn.get(who, who)}执行", [47, 48])], []


@rule("R-PRI-12", "priority", [47, 50], "回访触发条件",
      emits=["C72"])
def c72_trigger(ans):
    if single(ans, "Q47") != "yes":
        return [_eval_off("C72", "visit_trigger", "触发条件", 47)], []
    vs = [v for v in TRIGGER_CN if v in multi(ans, "Q50")]
    if not vs:
        return [tbd("R-PRI-12", "C72", "visit_trigger", "触发条件",
                    "第 50 题未勾选", 50)], \
               [missing("C72", "触发条件", 50, "未确定何时触发回访")]
    return [cfg("R-PRI-12", "C72", "visit_trigger", "触发条件",
                " + ".join(TRIGGER_CN[v] for v in vs), 50)], []


@rule("R-PRI-13", "priority", [47, 49], "回访打分维度",
      emits=["C73"])
def c73_visit_dims(ans):
    if single(ans, "Q47") != "yes":
        return [_eval_off("C73", "visit_dims", "回访打分维度", 47)], []
    vs = [v for v in DIM_CN if v in multi(ans, "Q49")]
    if not vs:
        return [tbd("R-PRI-13", "C73", "visit_dims", "回访打分维度",
                    "第 49 题未勾选", 49)], \
               [missing("C73", "回访打分维度", 49, "回访无打分维度")]
    return [cfg("R-PRI-13", "C73", "visit_dims", "回访打分维度",
                "、".join(DIM_CN[v] for v in vs), 49)], []


@rule("R-PRI-14", "priority", [89], "回访任务类型",
      emits=["C74"])
def c74_task_types(ans):
    vs = [v for v in VISIT_TYPE_CN if v in multi(ans, "Q89")]
    if not vs:
        return [tbd("R-PRI-14", "C74", "visit_task_types", "回访任务类型",
                    "第 89 题未勾选", 89)], \
               [missing("C74", "回访任务类型", 89, "无回访任务类型，任务模块无法建单")]
    return [cfg("R-PRI-14", "C74", "visit_task_types", "回访任务类型",
                "、".join(VISIT_TYPE_CN[v] for v in vs), 89)], []


@rule("R-PRI-15", "priority", [90], "回访任务状态",
      emits=["C75"])
def c75_task_status(ans):
    vs = [v for v in VISIT_STATUS_CN if v in multi(ans, "Q90")]
    if not vs:
        return [tbd("R-PRI-15", "C75", "visit_task_status", "回访任务状态",
                    "第 90 题未勾选", 90)], \
               [missing("C75", "回访任务状态", 90, "无状态定义，回访任务无法流转")]
    return [cfg("R-PRI-15", "C75", "visit_task_status", "回访任务状态",
                "、".join(VISIT_STATUS_CN[v] for v in vs), 90,
                f"{len(vs)} 个状态")], []


@rule("R-PRI-16", "priority", [91], "回访任务是否支持转派",
      emits=["C76"])
def c76_transfer(ans):
    v = single(ans, "Q91")
    if v == "yes":
        return [cfg("R-PRI-16", "C76", "visit_transfer", "回访转派", "支持", 91)], []
    if v == "no":
        return [cfg("R-PRI-16", "C76", "visit_transfer", "回访转派", "不支持", 91)], []
    return [tbd("R-PRI-16", "C76", "visit_transfer", "回访转派", "第 91 题未回答", 91)], \
           [warn("C76", "回访转派", 91, "未确认回访任务是否可转派", "暂按不支持处理", block=False)]


@rule("R-PRI-17", "priority", [92, "92b"], "回访完成时限",
      emits=["C77"])
def c77_deadline(ans):
    v = single(ans, "Q92")
    if v == "no":
        return [cfg("R-PRI-17", "C77", "visit_deadline", "回访时限", "不限时", 92,
                    "无时限时预警应改为按任务积压天数")], []
    if v != "yes":
        return [tbd("R-PRI-17", "C77", "visit_deadline", "回访时限",
                    "第 92 题未回答", 92)], \
               [warn("C77", "回访时限", 92, "未确认回访是否有完成时限",
                     "暂按不限时处理", block=False)]
    n, u = num(ans, "Q92b")
    if u == "unlimited":
        return [cfg("R-PRI-17", "C77", "visit_deadline", "回访时限", "不限时",
                    [92, "92b"], "选了不限时")], []
    if n is None or u is None:
        return [tbd("R-PRI-17", "C77", "visit_deadline", "回访时限",
                    "第 92 题选了有时限但未填完整数值+单位", [92, "92b"])], \
               [warn("C77", "回访时限", [92, "92b"],
                     "选了「有完成时限」但数值或单位缺失",
                     "回访超时无法判定", block=True)]
    return [cfg("R-PRI-17", "C77", "visit_deadline", "回访时限",
                num_cn(n, u), [92, "92b"], "从任务生成时开始计时")], []


@rule("R-PRI-18", "priority", [93], "回访关闭是否需审批",
      emits=["C78"])
def c78_close_approval(ans):
    v = single(ans, "Q93")
    if v == "need":
        return [cfg("R-PRI-18", "C78", "visit_close_approval", "回访关闭审批",
                    "需要审批", 93, "与工单关闭审批复用同一审批流")], []
    if v == "none":
        return [cfg("R-PRI-18", "C78", "visit_close_approval", "回访关闭审批",
                    "不需要审批", 93)], []
    return [tbd("R-PRI-18", "C78", "visit_close_approval", "回访关闭审批",
                "第 93 题未回答", 93)], \
           [warn("C78", "回访关闭审批", 93, "未确认回访关闭是否需审批",
                 "暂按不需要处理", block=False)]


@rule("R-PRI-19", "priority", [94], "回访是否启用预警",
      emits=["C79"])
def c79_alert(ans):
    v = single(ans, "Q94")
    if v == "yes":
        return [cfg("R-PRI-19", "C79", "visit_alert", "回访预警", "启用", 94,
                    "预警口径需与回访时限一致")], []
    if v == "no":
        return [cfg("R-PRI-19", "C79", "visit_alert", "回访预警", "不启用", 94)], []
    return [tbd("R-PRI-19", "C79", "visit_alert", "回访预警", "第 94 题未回答", 94)], \
           [warn("C79", "回访预警", 94, "未确认回访是否启用预警", "暂按不启用处理", block=False)]


@rule("R-PRI-20", "priority", [95], "回访模板题型",
      emits=["C80"])
def c80_tpl_types(ans):
    vs = [v for v in TPL_TYPE_CN if v in multi(ans, "Q95")]
    if not vs:
        return [tbd("R-PRI-20", "C80", "visit_tpl_types", "回访模板题型",
                    "第 95 题未勾选", 95)], \
               [warn("C80", "回访模板题型", 95, "未勾选题型，回访模板无法生成",
                     "暂按单选+评分+填空处理", block=False)]
    return [cfg("R-PRI-20", "C80", "visit_tpl_types", "回访模板题型",
                "、".join(TPL_TYPE_CN[v] for v in vs), 95)], []


@rule("R-PRI-21", "priority", [99], "模板适用范围",
      emits=["C81"])
def c81_scope(ans):
    v = single(ans, "Q99")
    if v == "global":
        return [cfg("R-PRI-21", "C81", "tpl_scope", "模板适用范围",
                    "全局通用一套模板", 99)], []
    if v == "by_type":
        return [cfg("R-PRI-21", "C81", "tpl_scope", "模板适用范围",
                    "按工单类型分别配置", 99, "需为每个类型建模板")], \
               [warn("C81", "模板适用范围", 99, "按类型分别配置但未提供各类型模板",
                     "需补充各类型模板", block=False)]
    return [tbd("R-PRI-21", "C81", "tpl_scope", "模板适用范围",
                "第 99 题未回答", 99)], \
           [warn("C81", "模板适用范围", 99, "未确认模板范围",
                 "暂按全局通用一套处理", block=False)]


def derive(ans: dict):
    cfgs, issues = [], []
    for fn in (c61_enabled, c62_basis, c63_terminal, c64_eval_switch, c65_valid,
               c66_expired, c67_modify, c68_template, c69_dims, c70_weight,
               c71_visit_switch, c72_trigger, c73_visit_dims, c74_task_types,
               c75_task_status, c76_transfer, c77_deadline, c78_close_approval,
               c79_alert, c80_tpl_types, c81_scope):
        c, i = fn(ans)
        cfgs += c
        issues += i
    return cfgs, issues
