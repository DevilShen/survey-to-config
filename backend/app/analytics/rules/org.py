"""C01–C06 组织与基础参数。

输入题号：1、2、3、4、5、7
判断依据全部来自问卷原文的选项语义，不引入问卷外的假设。
"""
from __future__ import annotations

from .base import (cfg, rows, single, text, tbd, missing, warn, rule,
                   RULES, window)

OFFICE_CN = {"residential": "住宅小区", "office": "商业写字楼",
             "school": "学校", "apartment": "公寓"}


@rule("R-ORG-01", "org", [1], "项目名称映射合同主体，单一法人主体直接取原文",
      emits=["C01"])
def c01_subject(ans):
    name = text(ans, "Q1")
    if not name:
        return [tbd("R-ORG-01", "C01", "company_subject", "公司主体",
                    "第 1 题未填项目名称", 1)], \
               [missing("C01", "公司主体", 1, "第 1 题为合同与配置的落款主体，未填无法生成配置说明书")]
    return [cfg("R-ORG-01", "C01", "company_subject", "公司主体", name, 1,
                "单一法人主体")], []


@rule("R-ORG-02", "org", [7], "是否多级管理决定管理层级文案与跨级限制",
      emits=["C02"])
def c02_level(ans):
    v = single(ans, "Q7")
    if v == "multi_level":
        detail = text(ans, "Q7_note")
        v_txt = "多级管理（总部 → 区域 → 项目）" if not detail else f"多级管理：{detail}"
        return [cfg("R-ORG-02", "C02", "org_level", "管理层级", v_txt, 7,
                    "项目不得跨级直报")], []
    if v == "single_level":
        return [cfg("R-ORG-02", "C02", "org_level", "管理层级", "单级管理（项目独立）", 7,
                    "无区域层，预警升级止于项目")], []
    return [tbd("R-ORG-02", "C02", "org_level", "管理层级", "第 7 题未回答", 7)], \
           [missing("C02", "管理层级", 7, "未确定是否多级管理，组织树深度与预警升级链路都无法生成")]


@rule("R-ORG-03", "org", [3, 2], "项目清单取表格行，业态以明细行为准、与第 2 题交叉校验",
      emits=["C03"])
def c03_projects(ans):
    rs = rows(ans, "Q3")
    names = [r.get("项目名称", "").strip() for r in rs if r.get("项目名称", "").strip()]
    if not names:
        return [tbd("R-ORG-03", "C03", "projects", "项目清单", "第 3 题项目表为空", 3)], \
               [missing("C03", "项目清单", 3, "项目规模表为空，无法生成项目清单与业态矩阵")]

    out = [cfg("R-ORG-03", "C03", "projects", "项目清单", f"{len(names)} 个：" + "、".join(names), 3,
               "按项目分别建账，不合并")]
    issues = []

    # 明细业态与第 2 题勾选业态不一致时不下结论，只提示
    declared = set(ans.get("Q2") or [])
    detail_types = {r.get("项目业态", "").strip() for r in rs if r.get("项目业态")}
    mixed = [t for t in detail_types if "/" in t or "、" in t]
    if mixed:
        issues.append(warn("C03", "项目清单", [2, 3],
                           f"项目表中有混合业态（{'、'.join(sorted(mixed))}），无法归入单一业态",
                           "建议按主要业态归属，或拆为独立项目建账", block=True))
    elif declared:
        known = {"住宅小区": "residential", "商业写字楼": "office", "商业": "office",
                 "学校": "school", "公寓": "apartment"}
        bad = [t for t in detail_types if known.get(t) and known[t] not in declared]
        if bad:
            issues.append(warn("C03", "项目清单", [2, 3],
                               f"明细业态（{'、'.join(sorted(bad))}）与第 2 题勾选业态不一致",
                               "以明细为准，同时请客户确认第 2 题是否漏勾", block=True))
    return out, issues


@rule("R-ORG-04", "org", [2], "勾选业态生成业态矩阵，学校业态在本项目未启用",
      emits=["C04"])
def c04_types(ans):
    vs = ans.get("Q2") or []
    if not vs:
        return [tbd("R-ORG-04", "C04", "business_types", "项目业态",
                    "第 2 题未勾选业态", 2)], \
               [missing("C04", "项目业态", 2, "未勾选业态，第二部分业态专属题的适用范围无法确定")]
    txt = " / ".join(OFFICE_CN.get(v, v) for v in vs)
    note = "多业态并行，按项目分别挂接" if len(vs) > 1 else "单一业态"
    return [cfg("R-ORG-04", "C04", "business_types", "项目业态", txt, 2, note)], []


@rule("R-ORG-05", "org", [4], "服务时段决定超时计时窗口，必须成对出现",
      emits=["C05"])
def c05_window(ans):
    w = window(ans, "Q4")
    if not w:
        return [tbd("R-ORG-05", "C05", "service_window", "服务时段",
                    "第 4 题起止时间不完整", 4)], \
               [missing("C05", "服务时段", 4, "服务时段缺失，非工作时段无法从超时计时中扣除")]
    start, end = w
    note = "全项目统一" if single(ans, "Q7") == "single_level" else "全项目统一，不按项目分设"
    return [cfg("R-ORG-05", "C05", "service_window", "服务时段",
                f"每天 {start} – {end}", 4, note)], []


@rule("R-ORG-06", "org", [5], "是否区分节假日决定日历维护方式",
      emits=["C06"])
def c06_holiday(ans):
    v = single(ans, "Q5")
    if v == "split":
        return [cfg("R-ORG-06", "C06", "holiday_calendar", "节假日日历",
                    "按法定节假日维护，节假日响应要求单独配置", 5,
                    "需导入当年度法定节假日表")], []
    if v == "nosplit":
        return [cfg("R-ORG-06", "C06", "holiday_calendar", "节假日日历",
                    "不单独维护，无需区分", 5, "超时计算不因节假日调整")], []
    return [tbd("R-ORG-06", "C06", "holiday_calendar", "节假日日历", "第 5 题未回答", 5)], \
           [warn("C06", "节假日日历", 5, "未确认是否区分节假日，按不区分处理但需客户确认",
                 "默认按不区分配置", block=False)]


def derive(ans: dict):
    cfgs, issues = [], []
    for fn in (c01_subject, c02_level, c03_projects, c04_types, c05_window, c06_holiday):
        c, i = fn(ans)
        cfgs += c
        issues += i
    return cfgs, issues
