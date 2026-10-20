"""C50–C60 收费与预约。

输入题号：70–78、84–88

收费组最怕「部分收费」——问卷只勾了模式，没说哪些类型收费。
这种情况不补假设，直接标 tbd + 阻塞，因为收费口径错了会直接变成客诉。
"""
from __future__ import annotations

from .base import (cfg, multi, num, rows, single, tbd, missing, warn, rule,
                   num_cn, labels, FEE_CN)

PAY_MODE_CN = {"all": "全部收费", "partial": "部分收费", "free": "全部免费"}
PRICE_CN = {"fixed": "固定价格（按服务项定价）", "hourly": "按工时计费",
            "area": "按面积计费", "custom": "按项目自定义标准"}
GRAN_CN = {"1h": "每 1 小时一个时段", "2h": "每 2 小时一个时段",
           "half_day": "上午 / 下午", "day": "按天预约"}


def _fee_off(code, key, name, q, note=""):
    return cfg("R-FEE-01", code, key, name, "不适用（全部免费）", q, note, st="def")


@rule("R-FEE-01", "fee", [70], "收费模式决定后续三项是否适用",
      emits=["C50"])
def c50_mode(ans):
    v = single(ans, "Q70")
    if v == "all":
        return [cfg("R-FEE-01", "C50", "charge_mode", "收费模式", "全部收费", 70)], []
    if v == "free":
        return [cfg("R-FEE-01", "C50", "charge_mode", "收费模式", "全部免费", 70,
                    "收费相关 4 项配置全部不适用")], []
    if v == "partial":
        scope = (ans.get("Q70_note") or "").strip() if isinstance(ans.get("Q70_note"), str) else ""
        if not scope:
            return [tbd("R-FEE-01", "C50", "charge_mode", "收费模式",
                        "选「部分收费」但未说明哪些工单类型收费", 70)], \
                   [warn("C50", "收费模式", 70,
                         "第 70 题选「部分收费」，但未说明哪些工单类型收费、哪些免费",
                         "收费范围无法落到具体类型，报价模块无法配置", block=True)]
        return [cfg("R-FEE-01", "C50", "charge_mode", "收费模式",
                    f"部分收费：{scope}", 70, "按类型区分收免")], []
    return [tbd("R-FEE-01", "C50", "charge_mode", "收费模式", "第 70 题未回答", 70)], \
           [missing("C50", "收费模式", 70, "未确定是否收费，收费链路无法生成")]


@rule("R-FEE-02", "fee", [70, 71], "定价方式",
      emits=["C51"])
def c51_pricing(ans):
    mode = single(ans, "Q70")
    if mode == "free":
        return [_fee_off("C51", "pricing_method", "定价方式", 70)], []
    if mode not in ("all", "partial"):
        return [tbd("R-FEE-02", "C51", "pricing_method", "定价方式", "收费模式未定", 70)], []
    v = single(ans, "Q71")
    if not v:
        return [tbd("R-FEE-02", "C51", "pricing_method", "定价方式",
                    "第 71 题未回答", 71)], \
               [missing("C51", "定价方式", 71, "未确定定价方式，收费规则无法生成")]
    return [cfg("R-FEE-02", "C51", "pricing_method", "定价方式",
                PRICE_CN.get(v, v), 71, "固定价格需先建价格表")], []


@rule("R-FEE-03", "fee", [70, 72], "费项构成",
      emits=["C52"])
def c52_items(ans):
    mode = single(ans, "Q70")
    if mode == "free":
        return [_fee_off("C52", "fee_items", "费项构成", 70)], []
    if mode not in ("all", "partial"):
        return [tbd("R-FEE-03", "C52", "fee_items", "费项构成", "收费模式未定", 70)], []
    vs = multi(ans, "Q72")
    if not vs:
        return [tbd("R-FEE-03", "C52", "fee_items", "费项构成", "第 72 题未勾选", 72)], \
               [missing("C52", "费项构成", 72, "未确定费项，报价明细无法拆")]
    return [cfg("R-FEE-03", "C52", "fee_items", "费项构成",
                " + ".join(FEE_CN.get(v, v) for v in vs), 72)], []


@rule("R-FEE-04", "fee", [70, 73], "收费是否需业主确认",
      emits=["C53"])
def c53_confirm(ans):
    mode = single(ans, "Q70")
    if mode == "free":
        return [_fee_off("C53", "owner_confirm", "业主确认", 70)], []
    if mode not in ("all", "partial"):
        return [tbd("R-FEE-04", "C53", "owner_confirm", "业主确认", "收费模式未定", 70)], []
    v = single(ans, "Q73")
    if v == "confirm":
        return [cfg("R-FEE-04", "C53", "owner_confirm", "业主确认",
                    "报价需业主确认后才开始处理", 73, "确认前工单停在待处理")], []
    if v == "no_confirm":
        return [cfg("R-FEE-04", "C53", "owner_confirm", "业主确认",
                    "无需确认，直接处理", 73, "先服务后结算，存在收不回款的风险")], []
    return [tbd("R-FEE-04", "C53", "owner_confirm", "业主确认", "第 73 题未回答", 73)], \
           [warn("C53", "业主确认", 73, "未确认是否需要业主确认报价", "暂按需确认处理", block=False)]


@rule("R-FEE-05", "fee", [84], "是否有统一收费标准表",
      emits=["C54"])
def c54_std_table(ans):
    v = single(ans, "Q84")
    if v == "yes":
        return [cfg("R-FEE-05", "C54", "price_table", "统一标准表",
                    "启用统一收费标准表", 84)], []
    if v == "no":
        return [cfg("R-FEE-05", "C54", "price_table", "统一标准表",
                    "无统一标准，按实际情况逐单报价", 84,
                    "需工单内支持临时报价")], []
    return [tbd("R-FEE-05", "C54", "price_table", "统一标准表", "第 84 题未回答", 84)], \
           [missing("C54", "统一标准表", 84, "未确定是否有标准价格表")]


@rule("R-FEE-06", "fee", [84, 85], "定价维度：服务项 or 工单类型",
      emits=["C55"])
def c55_dimension(ans):
    if single(ans, "Q84") != "yes":
        return [cfg("R-FEE-06", "C55", "price_dimension", "定价维度",
                    "不适用（无统一标准表）", 84, st="def")], []
    v = single(ans, "Q85")
    if v == "by_item":
        return [cfg("R-FEE-06", "C55", "price_dimension", "定价维度",
                    "按具体服务项定价", 85, "要求服务项字典为二级结构")], []
    if v == "by_type":
        return [cfg("R-FEE-06", "C55", "price_dimension", "定价维度",
                    "按工单类型整体定价", 85, "不区分服务项")], []
    return [tbd("R-FEE-06", "C55", "price_dimension", "定价维度", "第 85 题未回答", 85)], \
           [missing("C55", "定价维度", 85, "未确定定价维度，价格表结构无法建立")]


@rule("R-FEE-07", "fee", [84, 86, 87], "价格表条目数与业主端公示",
      emits=["C56"])
def c56_publish(ans):
    out, issues = [], []
    if single(ans, "Q84") != "yes":
        return [cfg("R-FEE-07", "C56", "price_publish", "业主端公示",
                    "不适用（无统一标准表）", 84, st="def")], []
    rs = [r for r in rows(ans, "Q86") if (r.get("收费标准") or "").strip()]
    v87 = single(ans, "Q87")
    pub = {"yes": "需在业主端公示收费标准", "no": "不需公示，内部掌握"}.get(v87 or "", "")
    if not pub:
        out.append(tbd("R-FEE-07", "C56", "price_publish", "业主端公示",
                       "第 87 题未回答", 87))
        issues.append(warn("C56", "业主端公示", 87, "未确认是否公示收费标准",
                           "暂按不公示处理", block=False))
    else:
        note = f"价格表 {len(rs)} 条" if rs else "价格表为空，需补录后公示"
        out.append(cfg("R-FEE-07", "C56", "price_publish", "业主端公示", pub, 87, note))
        if not rs:
            issues.append(warn("C56", "业主端公示", [84, 86],
                               "启用统一标准表但第 86 题价格表为空",
                               "无价格可公示，收费模块无法配置", block=True))
    return out, issues


@rule("R-FEE-11", "fee", [74], "预约总开关，决定后三项预约配置是否适用",
      emits=["C57"])
def c57_appointment(ans):
    v = single(ans, "Q74")
    if v == "yes":
        return [cfg("R-FEE-11", "C57", "appointment_enabled", "预约开关",
                    "启用，报单时必须选择上门时段", 74)], []
    if v == "no":
        return [cfg("R-FEE-11", "C57", "appointment_enabled", "预约开关",
                    "不启用，尽快上门即可", 74, "后续 3 项预约配置全部不适用")], []
    return [tbd("R-FEE-11", "C57", "appointment_enabled", "预约开关",
                "第 74 题未回答", 74)], \
           [missing("C57", "预约开关", 74, "未确定是否需预约上门")]


@rule("R-FEE-08", "fee", [74, 75, 76], "预约时间窗：最多提前 / 最少提前",
      emits=["C58"])
def c58_window(ans):
    if single(ans, "Q74") != "yes":
        return [cfg("R-FEE-08", "C58", "appointment_window", "预约时间窗",
                    "不适用（未启用预约）", 74, st="def")], []
    n75, u75 = num(ans, "Q75")
    n76, u76 = num(ans, "Q76")
    issues = []
    if n75 is None:
        return [tbd("R-FEE-08", "C58", "appointment_window", "预约时间窗",
                    "第 75 题未填最多可提前天数", 75)], \
               [missing("C58", "预约时间窗", 75, "最多提前天数未填，预约日历无法生成")]
    if n76 is None:
        return [tbd("R-FEE-08", "C58", "appointment_window", "预约时间窗",
                    "第 76 题未填最少提前时长", 76)], \
               [missing("C58", "预约时间窗", 76, "最少提前时长未填，预约截止规则无法生成")]
    # 倒挂检测：最少提前折算成天 > 最多可提前
    n76_day = n76 / 24 if (u76 or "hour") == "hour" else n76
    if n76_day > n75:
        return [tbd("R-FEE-08", "C58", "appointment_window", "预约时间窗",
                    "最多可提前与最少提前互相矛盾", [75, 76])], \
               [warn("C58", "预约时间窗", [75, 76],
                     f"最多提前 {n75:g} 天，但最少需提前 {num_cn(n76, u76)}，两者倒挂",
                     "预约窗口为空，需客户重新确认两项数值", block=True)]
    val = f"最多提前 {num_cn(n75, u75 or 'day')}，最少提前 {num_cn(n76, u76 or 'hour')}"
    return [cfg("R-FEE-08", "C58", "appointment_window", "预约时间窗", val, [75, 76])], issues


@rule("R-FEE-09", "fee", [74, 77], "单时段可预约容量",
      emits=["C59"])
def c59_capacity(ans):
    if single(ans, "Q74") != "yes":
        return [cfg("R-FEE-09", "C59", "slot_capacity", "时段容量",
                    "不适用（未启用预约）", 74, st="def")], []
    n, _ = num(ans, "Q77")
    if n is None:
        return [cfg("R-FEE-09", "C59", "slot_capacity", "时段容量",
                    "不限制单时段预约人数", 77, "第 77 题未填，按不限制处理", st="def")], \
               [warn("C59", "时段容量", 77, "第 77 题未填单时段容量",
                     "暂按不限制处理，需评估上门人力是否够", block=False)]
    return [cfg("R-FEE-09", "C59", "slot_capacity", "时段容量",
                f"{int(n)} 人次/时段", 77)], []


@rule("R-FEE-10", "fee", [74, 78], "预约时段颗粒度",
      emits=["C60"])
def c60_granularity(ans):
    if single(ans, "Q74") != "yes":
        return [cfg("R-FEE-10", "C60", "slot_granularity", "时段颗粒度",
                    "不适用（未启用预约）", 74, st="def")], []
    v = single(ans, "Q78")
    if not v:
        return [tbd("R-FEE-10", "C60", "slot_granularity", "时段颗粒度",
                    "第 78 题未回答", 78)], \
               [warn("C60", "时段颗粒度", 78, "未选择预约颗粒度",
                     "暂按每 2 小时处理", block=False)]
    return [cfg("R-FEE-10", "C60", "slot_granularity", "时段颗粒度",
                GRAN_CN.get(v, v), 78)], []


def derive(ans: dict):
    cfgs, issues = [], []
    for fn in (c50_mode, c51_pricing, c52_items, c53_confirm, c54_std_table,
               c55_dimension, c56_publish, c57_appointment, c58_window,
               c59_capacity, c60_granularity):
        c, i = fn(ans)
        cfgs += c
        issues += i
    return cfgs, issues
