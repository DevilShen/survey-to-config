"""配置推导引擎的测试。

重点验证三件事：确定性、不猜测、冲突可检出。
"""
from app.analytics import config_engine as ce


BASE = {"Q3": 3, "Q7": "m", "Q12": "subtree", "Q18": 5000, "Q21": False, "Q25": "fin"}


def test_同答案必同输出():
    a = ce.derive(BASE)
    b = ce.derive(dict(BASE))
    assert ce.output_hash(a) == ce.output_hash(b)
    assert a["input_hash"] == b["input_hash"]


def test_键顺序不影响结果():
    shuffled = {"Q25": "fin", "Q21": False, "Q18": 5000, "Q12": "subtree", "Q7": "m", "Q3": 3}
    assert ce.output_hash(ce.derive(BASE)) == ce.output_hash(ce.derive(shuffled))


def test_改一个答案结果必须变():
    other = dict(BASE, Q21=True)
    assert ce.output_hash(ce.derive(BASE)) != ce.output_hash(ce.derive(other))


def test_阈值未填时阻塞且不猜():
    r = ce.derive(dict(BASE, Q18=None))
    assert r["stats"]["blocking"] == 1
    assert any(i["q"] == "Q18" and i["block"] for i in r["issues"])
    nodes = [c for c in r["configs"] if c["k"] == "approval_nodes"][0]
    assert nodes["st"] == "tbd"
    assert "无法确定" in nodes["v"]


def test_会签生成并行节点():
    r = ce.derive(dict(BASE, Q21=True))
    parallel = [f for f in r["flow"] if f["type"] == "parallel"]
    assert len(parallel) == 1
    assert parallel[0]["subs"] == ["财务", "法务"]


def test_组织层级与数据权限冲突():
    r = ce.derive(dict(BASE, Q3=2))
    issue = [i for i in r["issues"] if i["q"] == "Q12 × Q3"]
    assert issue and issue[0]["block"] is True
    scope = [c for c in r["configs"] if c["k"] == "data_scope"][0]
    assert scope["st"] == "tbd"


def test_缺答案不自动补全():
    r = ce.derive({})
    assert r["stats"]["tbd"] >= 3
    assert r["stats"]["blocking"] >= 2
    # 默认值项必须显式标出来源，不能混进"已确认"
    defaults = [c for c in r["configs"] if c["st"] == "def"]
    assert all(c["src"] == "默认值" for c in defaults)


def test_每条配置都有规则来源():
    r = ce.derive(BASE)
    for c in r["configs"]:
        assert c["rule"] in ce.RULES, c
        assert c["src"]


def test_历史问卷重跑结果稳定():
    """同一套历史答案跑两次，配置与流程节点必须逐项相同。"""
    history = [
        {"Q3": 3, "Q7": "l", "Q12": "subtree", "Q18": 10000, "Q21": True, "Q25": "fin"},
        {"Q3": 2, "Q7": "m", "Q12": "self", "Q18": 5000, "Q21": False, "Q25": "fin"},
        {"Q3": 3, "Q7": "l", "Q12": "all", "Q18": 5000, "Q21": False, "Q25": "all"},
    ]
    for ans in history:
        first, second = ce.derive(ans), ce.derive(dict(ans))
        assert first["configs"] == second["configs"]
        assert [f["t"] for f in first["flow"]] == [f["t"] for f in second["flow"]]
