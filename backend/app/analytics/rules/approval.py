"""C41–C49 审批与挂起。

输入题号：51–55、79–83

挂起与审批在问卷里分属两部分，但配置上是一件事：
挂起是否要审批、关闭是否要审批、审批几级由谁批，最后都落在同一张审批流上。
"""
from __future__ import annotations

from .base import (cfg, multi, num, rows, single, tbd, missing, warn, rule,
                   num_cn, OPER_CN)

APPROVE_TIMEOUT_CN = {"auto_pass": "自动通过", "escalate": "自动升级给上级",
                      "notify": "仅提醒，不自动处理"}


@rule("R-APR-01", "approval", [79], "哪些操作需要审批",
      emits=["C41"])
def c41_ops(ans):
    vs = [v for v in ("hang", "close") if v in multi(ans, "Q79")]
    if not vs:
        return [cfg("R-APR-01", "C41", "approval_ops", "需审批的操作",
                    "无（挂起与关闭均无需审批）", 79, "第 79 题未勾选", st="def")], \
               [warn("C41", "需审批的操作", 79, "未勾选任何需审批操作",
                     "挂起与关闭将直接生效，建议确认是否需要审批留痕", block=False)]
    return [cfg("R-APR-01", "C41", "approval_ops", "需审批的操作",
                "、".join(OPER_CN[v] for v in vs), 79)], []


@rule("R-APR-02", "approval", [80, 81], "审批级数与各级审批人",
      emits=["C42"])
def c42_levels(ans):
    need = [v for v in ("hang", "close") if v in multi(ans, "Q79")]
    if not need:
        return [cfg("R-APR-02", "C42", "approval_chain", "审批级数与审批人",
                    "不适用（无审批操作）", 80, "第 79 题未勾选审批操作", st="def")], []
    lv = str(single(ans, "Q80") or "")
    if lv not in ("1", "2", "3"):
        return [tbd("R-APR-02", "C42", "approval_chain", "审批级数与审批人",
                    "第 80 题未选择审批级数", 80)], \
               [missing("C42", "审批级数与审批人", 80, "有审批操作但级数未定，审批流无法生成")]
    rs = [r for r in rows(ans, "Q81") if (r.get("审批级别") or "").strip()]
    if not rs:
        return [tbd("R-APR-02", "C42", "approval_chain", "审批级数与审批人",
                    f"第 80 题选了 {lv} 级，但第 81 题审批人表为空", [80, 81])], \
               [missing("C42", "审批级数与审批人", [80, 81], "审批人未填，审批节点无法指派")]
    chain = []
    for r in rs:
        lvl = (r.get("审批级别") or "").strip()
        who = (r.get("审批人") or "").strip()
        if who and "不适用" not in who:
            chain.append(f"{lvl}：{who}")
    if not chain:
        return [tbd("R-APR-02", "C42", "approval_chain", "审批级数与审批人",
                    "第 81 题审批人全部为「不适用」", [80, 81])], \
               [missing("C42", "审批级数与审批人", [80, 81], f"选了 {lv} 级审批但未指定任何审批人")]
    cn = {"1": "一级", "2": "二级", "3": "三级"}[lv]
    return [cfg("R-APR-02", "C42", "approval_chain", "审批级数与审批人",
                " → ".join(chain), [80, 81],
                f"声明 {lv} 级，实际填写 {len(chain)} 级")], []


@rule("R-APR-03", "approval", [82], "审批超时后的兜底动作",
      emits=["C43"])
def c43_timeout(ans):
    if not [v for v in ("hang", "close") if v in multi(ans, "Q79")]:
        return [cfg("R-APR-03", "C43", "approval_timeout", "审批超时处理",
                    "不适用（无审批操作）", 82, st="def")], []
    v = single(ans, "Q82")
    if not v:
        return [tbd("R-APR-03", "C43", "approval_timeout", "审批超时处理",
                    "第 82 题未回答", 82)], \
               [missing("C43", "审批超时处理", 82, "审批超时无兜底动作，工单会永久卡在审批")]
    return [cfg("R-APR-03", "C43", "approval_timeout", "审批超时处理",
                APPROVE_TIMEOUT_CN.get(v, v), 82,
                "自动通过存在资损与责任风险" if v == "auto_pass" else "")], []


@rule("R-APR-04", "approval", [83], "审批意见是否必填",
      emits=["C44"])
def c44_opinion(ans):
    if not [v for v in ("hang", "close") if v in multi(ans, "Q79")]:
        return [cfg("R-APR-04", "C44", "approval_opinion", "审批意见",
                    "不适用（无审批操作）", 83, st="def")], []
    v = single(ans, "Q83")
    if v == "required":
        return [cfg("R-APR-04", "C44", "approval_opinion", "审批意见",
                    "必须填写", 83, "无意见不能提交审批")], []
    if v == "optional":
        return [cfg("R-APR-04", "C44", "approval_opinion", "审批意见",
                    "可不填", 83)], []
    return [tbd("R-APR-04", "C44", "approval_opinion", "审批意见", "第 83 题未回答", 83)], \
           [warn("C44", "审批意见", 83, "未确认审批意见是否必填",
                 "暂按必须填写处理", block=False)]


@rule("R-APR-05", "approval", [51], "挂起总开关",
      emits=["C45"])
def c45_hang_switch(ans):
    v = single(ans, "Q51")
    if v == "yes":
        return [cfg("R-APR-05", "C45", "hang_enabled", "挂起开关", "允许挂起", 51)], []
    if v == "no":
        return [cfg("R-APR-05", "C45", "hang_enabled", "挂起开关", "不允许挂起", 51,
                    "后续 4 项挂起配置全部不适用")], []
    return [tbd("R-APR-05", "C45", "hang_enabled", "挂起开关", "第 51 题未回答", 51)], \
           [missing("C45", "挂起开关", 51, "未确定是否允许挂起")]


def _hang_off(code, key, name, q):
    return cfg("R-APR-06", code, key, name, "不适用（不允许挂起）", q, st="def")


@rule("R-APR-06", "approval", [51, 52], "挂起次数上限",
      emits=["C46"])
def c46_max_times(ans):
    if single(ans, "Q51") != "yes":
        return [_hang_off("C46", "hang_max_times", "挂起次数上限", 51)], []
    n, _ = num(ans, "Q52")
    if n is None:
        return [cfg("R-APR-06", "C46", "hang_max_times", "挂起次数上限",
                    "不限制（第 52 题未填）", 52, "未填视为不限制", st="def")], \
               [warn("C46", "挂起次数上限", 52, "第 52 题未填挂起次数上限",
                     "暂按不限制处理，建议明确上限", block=False)]
    return [cfg("R-APR-06", "C46", "hang_max_times", "挂起次数上限",
                f"{int(n)} 次", 52)], []


@rule("R-APR-07", "approval", [51, 53], "单次挂起时长上限；只选单位不填数即 A3",
      emits=["C47"])
def c47_duration(ans):
    if single(ans, "Q51") != "yes":
        return [_hang_off("C47", "hang_max_duration", "单次挂起时长上限", 51)], []
    n, u = num(ans, "Q53")
    if u == "unlimited":
        return [cfg("R-APR-07", "C47", "hang_max_duration", "单次挂起时长上限",
                    "不限时", 53, "需靠人工巡检兜底")], []
    if n is None and u is None:
        return [tbd("R-APR-07", "C47", "hang_max_duration", "单次挂起时长上限",
                    "第 53 题未填", 53)], \
               [missing("C47", "单次挂起时长上限", 53, "挂起时长无上限，工单可无限期挂起")]
    if n is None:
        return [tbd("R-APR-07", "C47", "hang_max_duration", "单次挂起时长上限",
                    "第 53 题只选了单位未填数值", 53)], \
               [warn("C47", "单次挂起时长上限", 53,
                     "第 53 题只选了单位未填具体数值，系统无法生成挂起时长上限",
                     "请补填数值；确认前不做默认假设", block=True)]
    if u is None:
        return [tbd("R-APR-07", "C47", "hang_max_duration", "单次挂起时长上限",
                    "第 53 题填了数值但未选单位", 53)], \
               [warn("C47", "单次挂起时长上限", 53, "有数值但单位缺失",
                     "请补选单位", block=True)]
    return [cfg("R-APR-07", "C47", "hang_max_duration", "单次挂起时长上限",
                num_cn(n, u), 53)], []


@rule("R-APR-08", "approval", [51, 54], "挂起期间是否暂停超时计时",
      emits=["C48"])
def c48_hang_timer(ans):
    if single(ans, "Q51") != "yes":
        return [_hang_off("C48", "hang_timer", "挂起计时", 51)], []
    v = single(ans, "Q54")
    if v == "pause":
        return [cfg("R-APR-08", "C48", "hang_timer", "挂起计时",
                    "暂停计时，挂起时长不计入超时", 54)], []
    if v == "continue":
        return [cfg("R-APR-08", "C48", "hang_timer", "挂起计时",
                    "继续计时，挂起时长计入超时", 54,
                    "挂起不缓解超时压力，需评估合理性")], []
    return [tbd("R-APR-08", "C48", "hang_timer", "挂起计时", "第 54 题未回答", 54)], \
           [warn("C48", "挂起计时", 54, "未确认挂起是否暂停计时", "暂按暂停处理", block=False)]


@rule("R-APR-09", "approval", [51, 55], "挂起原因是否必填",
      emits=["C49"])
def c49_hang_reason(ans):
    if single(ans, "Q51") != "yes":
        return [_hang_off("C49", "hang_reason", "挂起原因", 51)], []
    v = single(ans, "Q55")
    if v == "required":
        return [cfg("R-APR-09", "C49", "hang_reason", "挂起原因",
                    "必须填写", 55, "挂起原因进入工单日志")], []
    if v == "optional":
        return [cfg("R-APR-09", "C49", "hang_reason", "挂起原因",
                    "可不填", 55)], []
    return [tbd("R-APR-09", "C49", "hang_reason", "挂起原因", "第 55 题未回答", 55)], \
           [warn("C49", "挂起原因", 55, "未确认挂起原因是否必填", "暂按必须填写处理", block=False)]


def derive(ans: dict):
    cfgs, issues = [], []
    for fn in (c41_ops, c42_levels, c43_timeout, c44_opinion, c45_hang_switch,
               c46_max_times, c47_duration, c48_hang_timer, c49_hang_reason):
        c, i = fn(ans)
        cfgs += c
        issues += i
    return cfgs, issues
