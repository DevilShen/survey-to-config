"""C07–C13 报事报修类型字典。

输入题号：8–13、17–19、20、21、22、28
业态专属题（8–19）按第 2 题勾选结果分支：没勾的业态不产出配置，
而不是产出一条「不适用」占位——否则客户会以为要配。
"""
from __future__ import annotations

from .base import (cfg, rows, single, tbd, missing, warn, rule, labels,
                   TYPE_CN)

TYPE_ORDER = ["repair", "complaint", "consult", "suggest", "release", "decorate", "clean", "other"]

ADDR_CN = {"room": "具体房号", "public": "公共区域位置选择", "none": "无需填写地址"}


@rule("R-CAT-01", "catalog", [20], "勾选的工单类型生成类型字典，顺序按问卷顺序固定",
      emits=["C07"])
def c07_types(ans):
    vs = [v for v in TYPE_ORDER if v in (ans.get("Q20") or [])]
    if not vs:
        return [tbd("R-CAT-01", "C07", "ticket_types", "启用的工单类型",
                    "第 20 题未勾选任何类型", 20)], \
               [missing("C07", "启用的工单类型", 20, "无工单类型则整个报事报修模块无可用入口")]
    note = f"共 {len(vs)} 类"
    if "other" in vs:
        note += "；「其他」需后台可填自由文本"
    return [cfg("R-CAT-01", "C07", "ticket_types", "启用的工单类型",
                "、".join(TYPE_CN.get(v, v) for v in vs), 20, note)], []


@rule("R-CAT-02", "catalog", [21], "自定义名称表为空即沿用系统推荐名称",
      emits=["C08"])
def c08_naming(ans):
    rs = [r for r in rows(ans, "Q21") if (r.get("自定义名称") or "").strip()]
    if not rs:
        return [cfg("R-CAT-02", "C08", "type_naming", "类型名称",
                    "全部沿用系统推荐名称，不自定义", 21, "第 21 题名称列留空")], []
    pairs = "；".join(f"{r.get('系统推荐名称','')} → {r.get('自定义名称','').strip()}" for r in rs)
    return [cfg("R-CAT-02", "C08", "type_naming", "类型名称", pairs, 21,
                f"{len(rs)} 项自定义，其余沿用推荐名")], []


@rule("R-CAT-03", "catalog", [28], "服务项是否细分决定字典层级与定价维度",
      emits=["C09"])
def c09_service_item(ans):
    v = single(ans, "Q28")
    if v == "yes":
        rs = rows(ans, "Q29")
        n = len(rs)
        if n == 0:
            return [tbd("R-CAT-03", "C09", "service_item_level", "服务项细分",
                        "第 28 题选需细分，但第 29 题服务项表为空", [28, 29])], \
                   [missing("C09", "服务项细分", [28, 29], "要求细分却未列服务项，字典无法建立")]
        return [cfg("R-CAT-03", "C09", "service_item_level", "服务项细分",
                    f"细分，共 {n} 条服务项（二级字典）", [28, 29],
                    "工单类型 → 服务项 两级字典")], []
    if v == "no":
        return [cfg("R-CAT-03", "C09", "service_item_level", "服务项细分",
                    "不细分，工单类型即最小颗粒度", 28, "一级字典")], []
    return [tbd("R-CAT-03", "C09", "service_item_level", "服务项细分", "第 28 题未回答", 28)], \
           [missing("C09", "服务项细分", 28, "未确定字典层级，定价维度随之不确定")]


@rule("R-CAT-04", "catalog", [22], "报单地址项决定工单表单必填字段",
      emits=["C10"])
def c10_address(ans):
    v = single(ans, "Q22")
    if v == "room":
        return [cfg("R-CAT-04", "C10", "address_fields", "报单地址",
                    "启用：具体房号", 22, "房号关联空间主数据")], []
    if v == "public":
        return [cfg("R-CAT-04", "C10", "address_fields", "报单地址",
                    "启用：公共区域位置选择", 22, "公共区域清单需项目方提供")], []
    if v == "none":
        return [cfg("R-CAT-04", "C10", "address_fields", "报单地址",
                    "不启用地址填写", 22, "工单无法关联空间，后续按项目维度统计")], []
    return [tbd("R-CAT-04", "C10", "address_fields", "报单地址", "第 22 题未回答", 22)], \
           [missing("C10", "报单地址", 22, "未确定地址项，工单表单必填字段无法生成")]


def _bool_items(ans, qs: list[int], texts: dict, on: str, off: str) -> tuple[str, list[int]]:
    """把一组 yes/no 题聚成一句话。只有答过且为 yes 的才写进配置值。"""
    yes, answered = [], []
    for q in qs:
        v = single(ans, f"Q{q}")
        if v is None:
            continue
        answered.append(q)
        if v == "yes":
            yes.append(texts[q])
    if not yes:
        return off, answered
    return on.format(items="、".join(yes)), answered


@rule("R-CAT-05", "catalog", [8, 9, 10, 11], "住宅业态专属能力按勾选聚合",
      emits=["C11"])
def c11_residential(ans):
    if "residential" not in (ans.get("Q2") or []):
        return [], []          # 未勾住宅，不产出，不是「不适用」占位
    t = {8: "业主手机端自助提报", 9: "区分业主/租户", 10: "家电类维修", 11: "装修申请类工单"}
    val, answered = _bool_items(ans, [8, 9, 10, 11], t, "启用：{items}", "未启用任何住宅专属能力")
    if not answered:
        return [tbd("R-CAT-05", "C11", "residential_cap", "住宅业态",
                    "第 8–11 题均未回答", [8, 9, 10, 11])], \
               [missing("C11", "住宅业态", [8, 9, 10, 11], "已勾住宅业态但专属题全空")]
    return [cfg("R-CAT-05", "C11", "residential_cap", "住宅业态", val, [8, 9, 10, 11],
                "仅对住宅项目生效")], []


@rule("R-CAT-06", "catalog", [12, 13], "写字楼业态专属能力按勾选聚合",
      emits=["C12"])
def c12_office(ans):
    if "office" not in (ans.get("Q2") or []):
        return [], []
    t = {12: "识别租户身份", 13: "区分公共区域与租户单元内部"}
    val, answered = _bool_items(ans, [12, 13], t, "启用：{items}", "未启用写字楼专属能力")
    if not answered:
        return [tbd("R-CAT-06", "C12", "office_cap", "写字楼业态",
                    "第 12–13 题均未回答", [12, 13])], \
               [missing("C12", "写字楼业态", [12, 13], "已勾写字楼业态但专属题全空")]
    return [cfg("R-CAT-06", "C12", "office_cap", "写字楼业态", val, [12, 13],
                "仅对写字楼项目生效")], []


@rule("R-CAT-07", "catalog", [17, 18, 19], "公寓业态专属能力按勾选聚合",
      emits=["C13"])
def c13_apartment(ans):
    if "apartment" not in (ans.get("Q2") or []):
        return [], []
    t = {17: "识别租客身份", 18: "退租查验类工单", 19: "入住办理类工单"}
    val, answered = _bool_items(ans, [17, 18, 19], t, "启用：{items}", "未启用公寓专属能力")
    if not answered:
        return [tbd("R-CAT-07", "C13", "apartment_cap", "公寓业态",
                    "第 17–19 题均未回答", [17, 18, 19])], \
               [missing("C13", "公寓业态", [17, 18, 19], "已勾公寓业态但专属题全空")]
    return [cfg("R-CAT-07", "C13", "apartment_cap", "公寓业态", val, [17, 18, 19],
                "仅对公寓项目生效")], []


def derive(ans: dict):
    cfgs, issues = [], []
    for fn in (c07_types, c08_naming, c09_service_item, c10_address,
               c11_residential, c12_office, c13_apartment):
        c, i = fn(ans)
        cfgs += c
        issues += i
    return cfgs, issues
