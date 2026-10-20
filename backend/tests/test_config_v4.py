"""配置推导 v4（报事报修 81 条）的测试。

分三层：
1. 全局不变量：81 条齐全、确定性、键序无关、改答案必变、每条有规则来源；
2. 分组规则：7 组的关键分支，含「不适用」与「缺失不猜」；
3. 跨题冲突：A1–A14 逐条可复现。

真实问卷样本用 tests/fixtures/sample_project.json，它同时是回归基准。
"""
import json
from pathlib import Path

import pytest

from app.analytics import config_engine_v4 as e
from app.analytics import conflicts
from app.analytics.rules import org, catalog, flow, sla, approval, fee, priority

FIX = json.loads((Path(__file__).parent / "fixtures" / "sample_project.json").read_text(encoding="utf-8"))
XC = FIX["answers"]

BASE = {
    "Q1": "测试物业", "Q2": ["residential"], "Q4": {"start": "09:00", "end": "18:00"},
    "Q5": "nosplit", "Q6": "cs", "Q7": "single_level",
    "Q20": ["repair"], "Q22": "room", "Q23": "no", "Q24": "standard",
    "Q28": "no", "Q30": "role", "Q33": "escalate",
    "Q36": "exclude", "Q37": ["per_node"], "Q40": "unified",
    "Q41": ["executor"], "Q42": "no", "Q47": "no",
    "Q51": "no", "Q56": "no", "Q64": ["im"],
    "Q65": "no", "Q70": "free", "Q74": "no",
    "Q79": [], "Q84": "no", "Q89": ["satisfaction"], "Q90": ["done"],
    "Q91": "yes", "Q92": "no", "Q93": "none", "Q94": "no",
    "Q95": ["single"], "Q98": "equal", "Q99": "global",
}


def derive(ans):
    return e.derive(ans)


def code_map(r):
    return {c["code"]: c for c in r["configs"]}


def ids(r):
    return {i["id"] for i in r["issues"]}


# ============================================================ 1. 全局不变量

def test_八十一配置项齐全():
    r = derive(BASE)
    assert len(r["configs"]) == 81
    assert [c["code"] for c in r["configs"]] == [f"C{i:02d}" for i in range(1, 82)]


def test_七分组齐全且数量对齐():
    r = derive(BASE)
    assert [g["k"] for g in r["groups"]] == \
        ["org", "catalog", "flow", "sla", "approval", "fee", "priority"]
    assert [g["n"] for g in r["groups"]] == [6, 7, 12, 15, 9, 11, 21]
    assert sum(g["n"] for g in r["groups"]) == 81


def test_同答案同指纹():
    assert derive(BASE)["output_hash"] == derive(dict(BASE))["output_hash"]


def test_键顺序不影响结果():
    shuffled = dict(reversed(list(BASE.items())))
    assert derive(BASE)["output_hash"] == derive(shuffled)["output_hash"]


def test_改一个答案指纹必变():
    other = dict(BASE, Q22="none")
    assert derive(BASE)["output_hash"] != derive(other)["output_hash"]


def test_每条配置都有规则来源():
    r = derive(BASE)
    for c in r["configs"]:
        assert c["rule"] in e.RULES or c["rule"] == "R-NA-01", c
        assert c["src"], c
        assert c["st"] in ("ok", "def", "tbd"), c


def test_统计自洽():
    r = derive(BASE)
    s = r["stats"]
    assert s["confirmed"] + s["default"] + s["tbd"] == 81
    assert s["total"] == 81


def test_空答案不崩且全部标待确认或不适用():
    r = derive({})
    assert len(r["configs"]) == 81
    assert r["stats"]["tbd"] > 20
    assert r["stats"]["blocking"] > 10


def test_真实问卷跑通():
    r = derive(XC)
    assert len(r["configs"]) == 81
    assert r["stats"]["confirmed"] >= 60
    assert r["input_hash"] and r["output_hash"]


def test_真实问卷重复跑结果一致():
    a, b = derive(XC), derive(XC)
    assert [c["v"] for c in a["configs"]] == [c["v"] for c in b["configs"]]


def test_规则注册表有唯一ID():
    assert len(e.rule_index()) == len({r["id"] for r in e.rule_index()})


def test_规则输入题号均为整数或追问题号():
    for r in e.rule_index():
        for q in r["inputs"]:
            assert isinstance(q, int) or (isinstance(q, str) and q.endswith("b")), r


# ============================================================ 2. 分组规则

# ---- org C01-C06

def test_公司主体取原文():
    c = code_map(derive(BASE))
    assert c["C01"]["v"] == "测试物业"
    assert c["C01"]["st"] == "ok"


def test_公司主体缺失时不猜():
    c = code_map(derive({}))
    assert c["C01"]["st"] == "tbd"
    assert "无法推导" in c["C01"]["v"]


def test_单级与多级管理层级文案不同():
    a = code_map(derive(BASE))["C02"]["v"]
    b = code_map(derive(dict(BASE, Q7="multi_level")))["C02"]["v"]
    assert "单级" in a and "多级" in b


def test_项目清单按行聚合():
    c = code_map(derive(XC))
    assert c["C03"]["v"].startswith("10 个")
    assert "紫琅天成" in c["C03"]["v"]


def test_混合业态触发A13():
    ans = dict(BASE, Q3=[{"项目名称": "紫琅天成/生活汇", "项目业态": "住宅/商业"}])
    assert "A13" in ids(derive(ans))


def test_综合体型单业态不误判为混合():
    ans = dict(BASE, Q3=[{"项目名称": "紫琅新天地", "项目业态": "商业写字楼（综合体）"}])
    assert "A13" not in ids(derive(ans))


def test_业态只保留勾选的():
    a = code_map(derive(dict(BASE, Q2=["residential"])))
    assert a["C04"]["v"] == "住宅小区"
    b = code_map(derive(dict(BASE, Q2=["office", "apartment"])))
    assert "商业写字楼" in b["C04"]["v"] and "公寓" in b["C04"]["v"]


def test_服务时段缺失标待确认():
    ans = dict(BASE)
    ans.pop("Q4")
    assert code_map(derive(ans))["C05"]["st"] == "tbd"


def test_节假日区分与不区分():
    assert "不单独维护" in code_map(derive(BASE))["C06"]["v"]
    assert "法定节假日" in code_map(derive(dict(BASE, Q5="split")))["C06"]["v"]


# ---- catalog C07-C13

def test_工单类型按问卷顺序输出():
    v = code_map(derive(XC))["C07"]["v"]
    assert v.index("报修") < v.index("投诉") < v.index("咨询")


def test_工单类型为空则阻塞():
    r = derive(dict(BASE, Q20=[]))
    assert code_map(r)["C07"]["st"] == "tbd"
    assert "M-C07" in ids(r)


def test_自定义名称为空则沿用推荐():
    assert "沿用系统推荐" in code_map(derive(BASE))["C08"]["v"]


def test_服务项细分与不细分():
    a = code_map(derive(dict(BASE, Q28="yes", Q29=[{"工单类型": "报修", "服务项": "电灯"}])))
    assert "细分" in a["C09"]["v"] and "1 条" in a["C09"]["v"]
    b = code_map(derive(BASE))["C09"]["v"]
    assert "不细分" in b


def test_服务项要求细分但表空则阻塞():
    r = derive(dict(BASE, Q28="yes"))
    assert code_map(r)["C09"]["st"] == "tbd"
    assert "M-C09" in ids(r)


def test_业态未勾选时不产出占位而是补齐为不适用():
    c = code_map(derive(dict(BASE, Q2=["residential"])))
    assert c["C12"]["st"] == "def" and "不适用" in c["C12"]["v"]


def test_住宅业态能力聚合():
    v = code_map(derive(dict(BASE, Q8="yes", Q9="no", Q10="yes", Q11="no")))["C11"]["v"]
    assert "业主手机端自助提报" in v
    assert "家电类维修" in v
    assert "区分业主" not in v


def test_报单地址三态():
    for v, kw in (("room", "房号"), ("public", "公共区域"), ("none", "不启用")):
        assert kw in code_map(derive(dict(BASE, Q22=v)))["C10"]["v"]


# ---- flow C14-C25

def test_标准流程取六节点默认():
    c = code_map(derive(BASE))
    assert c["C14"]["v"].startswith("系统标准流程")
    assert c["C15"]["v"].count("、") == 5


def test_自定义流程取勾选节点():
    ans = dict(BASE, Q24="custom", Q25=["accept", "processing"], Q26="deny", Q27="none")
    v = code_map(derive(ans))["C15"]["v"]
    assert v == "待接收、处理中"


def test_自定义流程未选节点则阻塞():
    r = derive(dict(BASE, Q24="custom", Q25=[]))
    assert code_map(r)["C15"]["st"] == "tbd"
    assert "M-C15" in ids(r)


def test_审核节点需要审核人():
    a = code_map(derive(dict(BASE, Q24="custom", Q25=["audit"], Q27="need", Q27b="pm")))
    assert "项目经理" in a["C16"]["v"]
    r = derive(dict(BASE, Q24="custom", Q25=["audit"], Q27="need"))
    assert code_map(r)["C16"]["st"] == "tbd"


def test_节点跳过默认禁止():
    assert "禁止跳过" in code_map(derive(BASE))["C17"]["v"]


def test_人工流转节点为空时提示():
    r = derive(dict(BASE, Q34=[]))
    assert code_map(r)["C18"]["st"] == "def"
    assert "M-C18" in ids(r)


def test_分配方式三种():
    assert "指定到" in code_map(derive(dict(BASE, Q30="person")))["C20"]["v"]
    assert "角色" in code_map(derive(dict(BASE, Q30="role")))["C20"]["v"]
    assert "抢单" in code_map(derive(dict(BASE, Q30="grab")))["C20"]["v"]


def test_非抢单模式抢单窗口判不适用():
    assert "不适用" in code_map(derive(dict(BASE, Q30="role")))["C22"]["v"]


def test_主办协办未答则A6():
    r = derive(dict(BASE, Q23="yes"))
    assert "A6" in ids(r)
    assert code_map(r)["C24"]["st"] == "tbd"


def test_转派与审批分开判定():
    a = code_map(derive(dict(BASE, Q35="yes", Q35b="need")))["C25"]["v"]
    b = code_map(derive(dict(BASE, Q35="no")))["C25"]["v"]
    assert "需审批" in a and "不可自行转派" in b


# ---- sla C26-C40

def test_超时口径两种():
    assert "仅计工作时间" in code_map(derive(BASE))["C26"]["v"]
    assert "自然时间" in code_map(derive(dict(BASE, Q36="include")))["C26"]["v"]


def test_整体加节点双维度():
    v = code_map(derive(XC))["C27"]["v"]
    assert "整体" in v and "节点" in v


def test_整体时限单位换算():
    c = code_map(derive(dict(BASE, Q37=["overall"], Q38={"v": 2, "unit": "day"})))
    assert c["C28"]["v"] == "2 天"


def test_整体时限只有单位不填数即A3():
    r = derive(dict(BASE, Q37=["overall"], Q38={"v": None, "unit": "day"}))
    assert "A3" in ids(r)
    assert code_map(r)["C28"]["st"] == "tbd"


def test_节点时限解析中文时长():
    c = code_map(derive(XC))
    assert c["C29"]["v"] == "30 分钟"
    assert c["C30"]["v"] == "4 小时"


def test_节点时限表缺行不猜():
    ans = dict(XC)
    ans["Q39"] = [{"节点名称": "待接收", "超时时长": "30 分钟"}]
    c = code_map(derive(ans))
    assert c["C30"]["st"] == "tbd"


def test_预警未启用时后续判不适用():
    c = code_map(derive(dict(BASE, Q56="no")))
    for code in ("C34", "C35", "C36", "C37", "C38", "C39"):
        assert c[code]["st"] == "def" and "不适用" in c[code]["v"], code


def test_预警等级取勾选():
    assert "4 级" in code_map(derive(XC))["C36"]["v"]
    assert "1 级" in code_map(derive(dict(BASE, Q56="yes", Q59=["l1"], Q61=[])))["C36"]["v"]


def test_预警等级与通知表行数不一致触发A2():
    ans = dict(BASE, Q56="yes", Q59=["l1"], Q60=["before"], Q60b={"v": 30, "unit": "min"},
               Q61=[{"预警等级": "提醒级", "通知对象": "处理人"},
                    {"预警等级": "严重", "通知对象": "项目经理"}])
    assert "A2" in ids(derive(ans))


def test_超时前提醒缺时长触发提示():
    r = derive(dict(BASE, Q56="yes", Q58=["overall"], Q59=["l1"], Q60=["before"]))
    assert "M-C37" in ids(r)


def test_升级链路只通知一次():
    v = code_map(derive(dict(BASE, Q56="yes", Q62=["role"], Q63="once")))["C39"]["v"]
    assert "只通知一次" in v


def test_逐级升级缺表则阻塞():
    r = derive(dict(BASE, Q56="yes", Q62=["role"], Q63="escalate"))
    assert code_map(r)["C39"]["st"] == "tbd"


# ---- approval C41-C49

def test_无审批操作时整组不适用():
    c = code_map(derive(dict(BASE, Q79=[])))
    assert c["C41"]["v"].startswith("无")
    for code in ("C42", "C43", "C44"):
        assert "不适用" in c[code]["v"], code


def test_审批级数与审批人():
    ans = dict(BASE, Q79=["close"], Q80="2",
               Q81=[{"审批级别": "一级", "审批人": "项目经理"},
                    {"审批级别": "二级", "审批人": "总经理"}])
    assert code_map(derive(ans))["C42"]["v"] == "一级：项目经理 → 二级：总经理"


def test_审批人全不适用则阻塞():
    ans = dict(BASE, Q79=["close"], Q80="1",
               Q81=[{"审批级别": "一级", "审批人": "不适用（一级审批）"}])
    assert code_map(derive(ans))["C42"]["st"] == "tbd"


def test_不允许挂起时挂起组不适用():
    c = code_map(derive(dict(BASE, Q51="no")))
    for code in ("C46", "C47", "C48", "C49"):
        assert "不适用" in c[code]["v"], code


def test_挂起次数与时长():
    c = code_map(derive(dict(BASE, Q51="yes", Q52=2, Q53={"v": 3, "unit": "day"})))
    assert c["C46"]["v"] == "2 次"
    assert c["C47"]["v"] == "3 天"


def test_挂起时长只选单位不填数即A3():
    r = derive(dict(BASE, Q51="yes", Q53={"v": None, "unit": "day"}))
    assert "A3" in ids(r)


def test_挂起时长不限时():
    assert code_map(derive(dict(BASE, Q51="yes", Q53={"unit": "unlimited"})))["C47"]["v"] == "不限时"


# ---- fee C50-C60

def test_全部免费时收费组不适用():
    c = code_map(derive(dict(BASE, Q70="free")))
    for code in ("C51", "C52", "C53"):
        assert "不适用" in c[code]["v"], code


def test_部分收费未说明范围即A4():
    r = derive(dict(BASE, Q70="partial", Q86=[]))
    assert "A4" in ids(r)
    assert code_map(r)["C50"]["st"] == "tbd"


def test_部分收费有说明则生成():
    ans = dict(BASE, Q70="partial", Q70_note="户内收费，公共区域免费")
    assert "户内收费" in code_map(derive(ans))["C50"]["v"]


def test_预约关闭时预约组不适用():
    c = code_map(derive(dict(BASE, Q74="no")))
    for code in ("C58", "C59", "C60"):
        assert "不适用" in c[code]["v"], code


def test_预约窗口正反向计算():
    v = code_map(derive(dict(BASE, Q74="yes", Q75={"v": 7, "unit": "day"},
                             Q76={"v": 2, "unit": "hour"})))["C58"]["v"]
    assert "最多提前 7 天" in v and "最少提前 2 小时" in v


def test_预约时间窗倒挂即A11():
    r = derive(dict(BASE, Q74="yes", Q75={"v": 1, "unit": "day"},
                    Q76={"v": 48, "unit": "hour"}))
    assert "A11" in ids(r)


def test_时段容量未填按不限制():
    assert "不限制" in code_map(derive(dict(BASE, Q74="yes", Q75={"v": 7, "unit": "day"},
                                            Q76={"v": 2, "unit": "hour"})))["C59"]["v"]


# ---- priority C61-C81

def test_优先级关闭时组内不适用():
    c = code_map(derive(dict(BASE, Q42="no")))
    assert "不适用" in c["C62"]["v"] and "不适用" in c["C63"]["v"]


def test_按类型划分优先级需优先级表():
    r = derive(dict(BASE, Q42="yes", Q43=["by_type"]))
    assert "A7" in ids(r)
    assert code_map(r)["C62"]["st"] == "tbd"


def test_优先级表有值则明细展开():
    ans = dict(BASE, Q42="yes", Q43=["by_type"],
               Q45=[{"工单类型": "报修", "优先级": "紧急"}])
    assert "报修 = 紧急" in code_map(derive(ans))["C62"]["v"]


def test_评价关闭时评价组不适用():
    c = code_map(derive(dict(BASE, Q65="no")))
    for code in ("C65", "C66", "C67", "C68", "C69", "C70"):
        assert "不适用" in c[code]["v"], code


def test_评价有效期按月():
    assert "1 个月内" in code_map(derive(dict(BASE, Q65="yes", Q66={"v": 1, "unit": "month"})))["C65"]["v"]


def test_评价允许修改必须有期限():
    r = derive(dict(BASE, Q65="yes", Q68="allow"))
    assert code_map(r)["C67"]["st"] == "tbd"
    assert "M-C67" in ids(r)


def test_打分权重选择设置但不给值则阻塞():
    r = derive(dict(BASE, Q65="yes", Q98="weight"))
    assert code_map(r)["C70"]["st"] == "tbd"
    assert "M-C70" in ids(r)


def test_回访关闭时后续不适用():
    c = code_map(derive(dict(BASE, Q47="no")))
    for code in ("C72", "C73"):
        assert "不适用" in c[code]["v"], code


def test_回访执行人缺失则阻塞():
    r = derive(dict(BASE, Q47="yes"))
    assert code_map(r)["C71"]["st"] == "tbd"


def test_回访时限不限时():
    assert code_map(derive(dict(BASE, Q92="no")))["C77"]["v"] == "不限时"


def test_回访时限缺数值则阻塞():
    r = derive(dict(BASE, Q92="yes"))
    assert code_map(r)["C77"]["st"] == "tbd"


def test_模板题型与范围():
    c = code_map(derive(dict(BASE, Q95=["single", "score"], Q99="global")))
    assert c["C80"]["v"] == "单选题、评分题"
    assert c["C81"]["v"] == "全局通用一套模板"


# ============================================================ 3. 冲突 A1-A14

def _conflict_answers():
    """每条冲突一份最小可触发答案。返回 {id: answers}。"""
    base = dict(BASE)
    return {
        "A1": dict(base, Q24="custom", Q25=["accept"], Q27="need", Q27b="pm",
                   Q34=["accept", "audit"]),
        "A2": dict(base, Q56="yes", Q59=["l1"], Q61=[{"预警等级": "提醒级", "通知对象": "处理人"},
                                                    {"预警等级": "严重", "通知对象": "经理"}]),
        "A3": dict(base, Q37=["overall"], Q38={"v": None, "unit": "day"}),
        "A4": dict(base, Q70="partial", Q86=[]),
        "A5": dict(base, Q28="no", Q85="by_item", Q84="yes"),
        "A6": dict(base, Q23="yes"),
        "A7": dict(base, Q42="yes", Q43=["by_type"], Q45=[]),
        "A8": dict(base, Q33="escalate", Q62=["role"]),
        "A9": dict(base, Q56="yes", Q60=["before", "after"], Q60b={"v": None, "unit": "min"}),
        "A10": dict(base, Q65="yes", Q66={"v": 1, "unit": "month"}),
        "A11": dict(base, Q74="yes", Q75={"v": 1, "unit": "day"}, Q76={"v": 48, "unit": "hour"}),
        "A12": dict(base, Q92="yes", Q92b={"v": 10, "unit": "min"},
                    Q39=[{"节点名称": "处理中", "超时时长": "4 小时"}]),
        "A13": dict(base, Q3=[{"项目名称": "混合盘", "项目业态": "住宅/商业"}]),
        "A14": dict(base, Q37=["overall", "per_node"], Q38={"v": None, "unit": "hour"}),
    }


@pytest.mark.parametrize("cid", ["A1", "A2", "A3", "A4", "A5", "A6", "A7",
                                 "A8", "A9", "A10", "A11", "A12", "A13", "A14"])
def test_冲突可复现(cid):
    ans = _conflict_answers()[cid]
    assert cid in {i["id"] for i in conflicts.check(e.normalize(ans))}


def test_冲突定义共十四条():
    assert [c["id"] for c in e.conflict_index()] == [f"A{i}" for i in range(1, 15)]


def test_冲突不改配置值只降级():
    ans = dict(BASE, Q28="no", Q85="by_item", Q84="yes")
    r = derive(ans)
    c = code_map(r)
    assert c["C09"]["conflict"] == "A5"
    assert "待确认" in c["C09"]["v"]


def test_红色冲突计入阻塞():
    r = derive(dict(BASE, Q70="partial", Q86=[]))
    hit = [i for i in r["issues"] if i["id"] == "A4"][0]
    assert hit["block"] is True


def test_琥珀冲突不阻塞():
    r = derive(dict(BASE, Q65="yes", Q66={"v": 1, "unit": "month"}))
    hit = [i for i in r["issues"] if i["id"] == "A10"][0]
    assert hit["block"] is False


# ============================================================ 4. 流程图

def test_流程图节点来自配置():
    r = derive(XC)
    names = [n["t"] for n in r["flow"]["nodes"]]
    assert "待接收" in names and "关闭" in names


def test_流程图有分支():
    r = derive(XC)
    kinds = {b["t"] for b in r["flow"]["branches"]}
    assert "挂起" in kinds and "报价确认" in kinds


def test_关闭挂起后无挂起分支():
    r = derive(dict(BASE, Q51="no"))
    assert "挂起" not in {b["t"] for b in r["flow"]["branches"]}
