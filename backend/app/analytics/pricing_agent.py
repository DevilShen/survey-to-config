"""市场报价 Agent · 确定性推理流水线。

为什么不用一次 LLM 调用直接吐价格
--------------------------------
报价出错不是"说得不好听"，是直接亏钱。所以这里把链路拆成 18 个可单独验证的
阶段，每个阶段只做一件事，并把「输入 → 判定 → 输出」整段留痕：

    理解层  S01 需求输入 → S02 推理层 → S03 拆解 → S04 关键词 → S05 明确度
            └─ S06 反向追问 ──→ 回到 S03（补齐参数再来一遍）
    检索层  S07 三路检索 → S08 RRF 融合 → S09 候选 Top3-5 → S10 置信度
            └─ S11 调整检索参数 ──→ 回到 S07（最多 N 轮）
    定价层  S12 相似度匹配 → S13 三规则各自出价 → S14 加权融合 → S15 毛利率
            └─ S16 重新评估 ──→ 回到 S13（提价 / 缩范围 / 转特批）
    交付层  S17 报价单 → S18 人工复核

三条硬约束
----------
1. 确定性：同一份需求 + 同一份规则权重 → output_hash 逐字节相同。
   所以调参是"可复现的实验"，不是"再摇一次骰子"。
2. 可解释：每个数字都能回答"为什么"。候选有相似度、规则有加权过程、
   毛利有成本明细；不允许出现无出处的价格。
3. 不猜：明确度不够就停下来问人（S06），置信度不够就调参（S11），
   毛利不够就重评估（S16）。任何一步都不许"先报个价再说"。

LLM 在这里的位置
----------------
理解与表达（把客户口语拆成槽位、生成追问话术）适合用模型；
但**定价计算与阈值判定必须由代码完成**，否则同一个需求今天报 98 万、
明天报 112 万，业务方没法向客户解释。本模块不依赖任何外部服务，
纯标准库实现，保证可离线复现。接入 LLM 时只替换 S03/S04 的抽取，
S13-S16 的口径一个字都不该变。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from collections import Counter
from datetime import date, timedelta

from ..db import one, rows, scalar

ENGINE_VERSION = "pricing_agent_v1.0"

# ---------------------------------------------------------------- 阶段拓扑

STAGES = [
    {"id": "S01", "key": "intake", "name": "客户需求输入", "group": "理解", "kind": "input"},
    {"id": "S02", "key": "reason", "name": "Agent 推理层", "group": "理解", "kind": "agent"},
    {"id": "S03", "key": "decompose", "name": "需求理解与拆解", "group": "理解", "kind": "agent"},
    {"id": "S04", "key": "keyword", "name": "关键词提取", "group": "理解", "kind": "agent"},
    {"id": "S05", "key": "clarity", "name": "需求明确度判断", "group": "理解", "kind": "gate"},
    {"id": "S06", "key": "clarify", "name": "反向补充关键参数", "group": "理解", "kind": "loop"},
    {"id": "S07", "key": "retrieve", "name": "多维度检索策略", "group": "检索", "kind": "agent"},
    {"id": "S08", "key": "fuse", "name": "混合检索结果融合", "group": "检索", "kind": "agent"},
    {"id": "S09", "key": "candidate", "name": "候选方案评估 Top3-5", "group": "检索", "kind": "agent"},
    {"id": "S10", "key": "confidence", "name": "置信度评估", "group": "检索", "kind": "gate"},
    {"id": "S11", "key": "retune", "name": "调整检索参数", "group": "检索", "kind": "loop"},
    {"id": "S12", "key": "match", "name": "需求相似度匹配", "group": "定价", "kind": "agent"},
    {"id": "S13", "key": "pricing", "name": "定价规则引擎", "group": "定价", "kind": "agent"},
    {"id": "S14", "key": "fusion", "name": "多规则融合决策", "group": "定价", "kind": "agent"},
    {"id": "S15", "key": "margin", "name": "利润率校验", "group": "定价", "kind": "gate"},
    {"id": "S16", "key": "revise", "name": "重新评估", "group": "定价", "kind": "loop"},
    {"id": "S17", "key": "quote", "name": "生成完整报价单", "group": "交付", "kind": "output"},
    {"id": "S18", "key": "review", "name": "人工复核确认", "group": "交付", "kind": "human"},
]

EDGES = [
    {"from": "S01", "to": "S02"},
    {"from": "S02", "to": "S03"},
    {"from": "S03", "to": "S04"},
    {"from": "S04", "to": "S05"},
    {"from": "S05", "to": "S06", "cond": "明确度 < 阈值", "branch": "no"},
    {"from": "S06", "to": "S03", "cond": "补齐参数后回到拆解", "branch": "loop"},
    {"from": "S05", "to": "S07", "cond": "明确度 ≥ 阈值", "branch": "yes"},
    {"from": "S07", "to": "S08"},
    {"from": "S08", "to": "S09"},
    {"from": "S09", "to": "S10"},
    {"from": "S10", "to": "S11", "cond": "置信度 < 阈值", "branch": "no"},
    {"from": "S11", "to": "S07", "cond": "调参后重检", "branch": "loop"},
    {"from": "S10", "to": "S12", "cond": "置信度 ≥ 阈值", "branch": "yes"},
    {"from": "S12", "to": "S13"},
    {"from": "S13", "to": "S14"},
    {"from": "S14", "to": "S15"},
    {"from": "S15", "to": "S16", "cond": "毛利率 < 下限", "branch": "no"},
    {"from": "S16", "to": "S13", "cond": "重新评估", "branch": "loop"},
    {"from": "S15", "to": "S17", "cond": "毛利率 ≥ 下限", "branch": "yes"},
    {"from": "S17", "to": "S18"},
]

# ---------------------------------------------------------------- 词典

# 口语 → 标准模块名。长词先匹配，避免「移动审批」被「审批」截胡。
MODULE_LEXICON = {
    "审批中台": "审批中台", "审批": "审批流", "工作流": "流程引擎", "流程": "流程引擎",
    "表单": "表单引擎", "预算管控": "预算管控", "预算控制": "预算管控",
    "费控": "费控报销", "费用控制": "费控报销", "报销": "费控报销", "差旅": "费控报销",
    "发票": "发票 OCR", "银企": "银企直连",
    "供应商": "供应商管理", "采购": "供应商管理", "询比价": "询比价", "询价": "询比价",
    "开标": "在线开标", "订单": "订单协同", "对账": "供应商对账", "三单匹配": "三单匹配",
    "开票": "开票协同", "返利": "返利结算", "交期": "交期跟踪", "准入": "供应商准入",
    "会员": "会员中心", "积分": "积分规则", "权益": "权益体系", "商城": "小程序商城",
    "合同": "合同管理", "条款": "条款库", "用印": "用印管理", "归档": "归档",
    "电子签": "电子签", "审查": "条款审查",
    "看板": "运营看板", "驾驶舱": "驾驶舱", "报表": "报表", "指标": "指标建模",
    "巡检": "巡检任务", "整改": "整改闭环", "拍照": "拍照取证",
    "移动审批": "移动审批", "移动端": "移动端", "移动": "移动端", "小程序": "小程序商城",
    "人事": "人事管理", "薪酬": "薪酬核算", "自助": "员工自助", "入转调离": "人事管理",
    "数据中台": "数据接入", "接入": "数据接入", "取数": "自助取数", "数仓": "数仓集成",
    "多租户": "多租户", "租户": "多租户", "工单": "工单管理", "IoT": "设备接入",
    "物联网": "设备接入", "设备": "设备接入", "售后": "售后工单",
    "权限": "权限与审计", "审计": "权限与审计", "等保": "安全合规", "合规": "安全合规",
    "培训": "上线与培训", "实施": "上线与培训",
}

INTEGRATION_LEXICON = {
    "SAP": "SAP", "用友": "用友 NC", "NC": "用友 NC", "金蝶": "金蝶",
    "HIS": "HIS", "HRP": "HRP", "POS": "POS", "钉钉": "钉钉",
    "企业微信": "企业微信", "企微": "企业微信", "OA": "OA", "ERP": "ERP",
    "MES": "MES", "CRM": "CRM", "银企直连": "银企直连", "发票平台": "发票平台",
    "数据仓库": "数据仓库", "数仓": "数据仓库",
}

INDUSTRY_LEXICON = {
    "制造": "制造", "工厂": "制造", "生产": "制造", "车间": "制造",
    "零售": "零售", "门店": "零售", "连锁": "零售", "电商": "零售",
    "医疗": "医疗", "医院": "医疗", "科室": "医疗", "医护": "医疗",
    "法务": "专业服务", "律所": "专业服务", "咨询": "专业服务",
    "互联网": "互联网", "平台": "互联网",
}

STOPWORDS = {"我们", "你们", "他们", "这个", "那个", "什么", "怎么", "可以", "需要",
             "希望", "最好", "其他", "大概", "现在", "已经", "一下", "一个", "这次",
             "要求", "支持", "增加", "现有", "基础", "能够", "自动", "进行"}


# ---------------------------------------------------------------- 规则装载

def load_rules(conn: sqlite3.Connection) -> dict:
    """把 price_rules 读成 {code: rule}。已禁用的规则保留在字典里，由调用方跳过。

    参数键以出厂默认为底、库里的值覆盖：新增参数（如 auto_shrink）上线后，
    旧库不用迁移也能看到并使用它，默认值的语义由 DEFAULT_RULES 定义。
    """
    from ..seed_pricing import DEFAULT_RULES
    defaults = {d["code"]: d for d in DEFAULT_RULES}
    out: dict[str, dict] = {}
    for r in rows(conn, "SELECT * FROM price_rules ORDER BY order_no"):
        dft = defaults.get(r["code"], {})
        out[r["code"]] = {
            "code": r["code"], "group": r["group_key"], "name": r["name"],
            "descr": r["descr"], "weight": float(r["weight"]),
            "params": {**(dft.get("params") or {}), **json.loads(r["params"])},
            "enabled": bool(r["enabled"]),
            "editable": bool(r["editable"]),
        }
    return out


def active_version(conn: sqlite3.Connection) -> str:
    r = one(conn, "SELECT version FROM price_rule_versions WHERE active=1 "
                  "ORDER BY created_at DESC, version DESC LIMIT 1")
    return r["version"] if r else "w_v1"


def _p(rules: dict, code: str) -> dict:
    return (rules.get(code) or {}).get("params", {})


# ---------------------------------------------------------------- 工具函数

def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _hash(obj) -> str:
    return hashlib.sha256(_j(obj).encode("utf-8")).hexdigest()[:16]


def _round(v: float, n: int = 1) -> float:
    return round(float(v) + 1e-9, n)


def _pct(vals: list[float], p: float) -> float | None:
    """分位点（线性插值）。样本少于 2 个时直接返回原值，不假装有分布。"""
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _cv(vals: list[float]) -> float:
    """变异系数。用来判断"多份参考报价是否口径一致"。"""
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    if mean <= 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    return math.sqrt(var) / mean


def _scan(text: str, lexicon: dict) -> list[dict]:
    """长词优先扫描。命中后占位，避免同一段文字被短词重复计入。"""
    hits: list[dict] = []
    remain = text
    for surface in sorted(lexicon, key=len, reverse=True):
        n = remain.count(surface)
        if n:
            hits.append({"surface": surface, "canonical": lexicon[surface], "n": n})
            remain = remain.replace(surface, "＃" * len(surface))
    return hits


SCALE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(万)?\s*(?:名|人|个|家)?\s*"
    r"(用户|账号|员工|医护|人员|管理者|人员|人|门店|供应商|会员|并发)")
BUDGET_RE = re.compile(r"预算[^0-9]{0,10}(\d+(?:\.\d+)?)\s*万")
BUDGET_RE2 = re.compile(r"(\d+(?:\.\d+)?)\s*万\s*(?:左右|以内|上下|封顶)")
DEADLINE_RES = [
    (re.compile(r"(?:明年|下一年)\s*Q([1-4])"), "next_q"),
    (re.compile(r"Q([1-4])"), "q"),
    (re.compile(r"(?:明年|下一年)\s*(\d{1,2})\s*月"), "next_m"),
    (re.compile(r"(\d{1,2})\s*月\s*(?:底|前|份|中)"), "m"),
    (re.compile(r"(20\d\d)\s*年\s*(\d{1,2})\s*月"), "ym"),
    (re.compile(r"年底"), "year_end"),
    (re.compile(r"上半年"), "h1"),
]


def _extract_scale(text: str) -> dict:
    """从原文抽规模。

    单位折算是个必须写明的口径：一家门店按 40 个使用者估、一家供应商按 6 个
    账号估。这个系数不参与定价公式，只用于"规模档位"分桶与追问话术，
    所以即便粗糙也不会污染报价。
    """
    mentions = []
    for m in SCALE_RE.finditer(text):
        val = float(m.group(1)) * (10000 if m.group(2) else 1)
        unit = m.group(3)
        factor = {"门店": 40, "供应商": 6, "会员": 0.05, "并发": 8}.get(unit, 1)
        mentions.append({"raw": m.group(0).strip(), "value": val,
                         "unit": unit, "factor": factor})
    # "门店从 420 家扩到 600 家" 这类没有尾随单位的，单独兜一层
    for m in re.finditer(r"(?:门店|网点)\D{0,8}?(\d{2,4})\s*家", text):
        mentions.append({"raw": m.group(0).strip(), "value": float(m.group(1)),
                         "unit": "门店", "factor": 40})
    if not mentions:
        return {"value": None, "bucket": None, "mentions": []}
    total = sum(m["value"] * m["factor"] for m in mentions)
    bucket = "s" if total < 300 else ("m" if total <= 2000 else "l")
    return {"value": int(round(total)), "bucket": bucket, "mentions": mentions}


def _extract_budget(text: str, hint: str | None) -> dict:
    if hint:
        m = re.search(r"(\d+(?:\.\d+)?)", hint)
        if m:
            return {"value": float(m.group(1)), "source": "客户经理登记", "raw": hint}
    m = BUDGET_RE.search(text) or BUDGET_RE2.search(text)
    if m:
        return {"value": float(m.group(1)), "source": "原话直取", "raw": m.group(0)}
    return {"value": None, "source": "缺失", "raw": None}


def _extract_deadline(text: str, hint: str | None) -> dict:
    if hint:
        return {"raw": hint, "months": _lead_months(hint), "source": "客户经理登记"}
    for rx, kind in DEADLINE_RES:
        m = rx.search(text)
        if m:
            return {"raw": m.group(0), "months": _lead_months(m.group(0), kind, m),
                    "source": "原话直取"}
    return {"raw": None, "months": None, "source": "缺失"}


def _lead_months(raw: str, kind: str = "", m=None) -> int | None:
    """把"明年 Q1"/"3 月前"折算成距今天数 → 月数。只用于判断交期是否紧张。"""
    today = date.today()
    raw = (raw or "").strip()
    try:
        # 客户经理登记的规范写法优先：2027-Q1 / 2027-03
        m_q = re.fullmatch(r"(\d{4})-?Q([1-4])", raw)
        if m_q:
            target = date(int(m_q.group(1)), (int(m_q.group(2)) - 1) * 3 + 1, 1)
            return max(1, int(round((target - today).days / 30.4)))
        m_ym = re.fullmatch(r"(\d{4})-(\d{1,2})", raw)
        if m_ym:
            target = date(int(m_ym.group(1)), int(m_ym.group(2)), 1)
            return max(1, int(round((target - today).days / 30.4)))
        if kind == "next_q" and m:
            q = int(m.group(1))
            target = date(today.year + 1, (q - 1) * 3 + 1, 1)
        elif kind == "q" and m:
            q = int(m.group(1))
            month = (q - 1) * 3 + 1
            target = date(today.year, month, 1)
            if target < today:
                target = date(today.year + 1, month, 1)
        elif kind == "next_m" and m:
            target = date(today.year + 1, int(m.group(1)), 1)
        elif kind == "ym" and m:
            target = date(int(m.group(1)), int(m.group(2)), 1)
        elif kind == "m" and m:
            mm = int(m.group(1))
            target = date(today.year, mm, 1)
            if target < today:
                target = date(today.year + 1, mm, 1)
        elif kind == "year_end":
            target = date(today.year, 12, 31)
        elif kind == "h1":
            target = date(today.year + 1, 6, 30)
        else:
            mm = re.search(r"(\d{1,2})", raw)
            if not mm:
                return None
            target = date(today.year, int(mm.group(1)), 1)
            if target < today:
                target = date(today.year + 1, int(mm.group(1)), 1)
        return max(1, int(round((target - today).days / 30.4)))
    except (ValueError, IndexError):
        return None


def _bucket_label(b: str | None) -> str:
    return {"s": "小型（<300 人）", "m": "中型（300–2000 人）",
            "l": "大型（>2000 人）"}.get(b or "", "未识别")


# ---------------------------------------------------------------- 关键词与向量

def extract_keywords(text: str, modules: list[str], integs: list[str],
                     industry: str | None) -> list[dict]:
    kws: list[dict] = []
    for m in modules:
        kws.append({"w": m, "type": "模块", "weight": 3.0})
    for i in integs:
        kws.append({"w": i, "type": "集成点", "weight": 2.5})
    if industry:
        kws.append({"w": industry, "type": "行业", "weight": 2.0})
    for h in _scan(text, MODULE_LEXICON):
        if h["canonical"] not in modules:
            kws.append({"w": h["surface"], "type": "口语词", "weight": 1.0})
    # 中文双字组：无分词器时的兜底召回，噪音由 IDF 压下去
    seen = set()
    for run in re.findall(r"[\u4e00-\u9fa5]{2,}", text):
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg in STOPWORDS or bg in seen:
                continue
            seen.add(bg)
            kws.append({"w": bg, "type": "字组", "weight": 0.6})
    kws.sort(key=lambda k: -k["weight"])
    return kws[:24]


def _vector(text: str) -> Counter:
    """稀疏向量：词典词 ×3、中文双字组 ×1、英文词 ×2。"""
    v: Counter = Counter()
    for h in _scan(text, MODULE_LEXICON):
        v["T:" + h["canonical"]] += 3 * h["n"]
    for h in _scan(text, INTEGRATION_LEXICON):
        v["T:" + h["canonical"]] += 3 * h["n"]
    for run in re.findall(r"[\u4e00-\u9fa5]+", text):
        for i in range(len(run) - 1):
            v["B:" + run[i:i + 2]] += 1
    for w in re.findall(r"[A-Za-z][A-Za-z0-9+.]*", text):
        v["W:" + w.upper()] += 2
    return v


def _idf(docs: list[Counter]) -> dict:
    n = len(docs) or 1
    df: Counter = Counter()
    for d in docs:
        for k in d:
            df[k] += 1
    return {k: math.log((n + 1) / (c + 0.5)) + 1.0 for k, c in df.items()}


def _cosine(a: Counter, b: Counter, idf: dict) -> float:
    if not a or not b:
        return 0.0
    num = 0.0
    for k, va in a.items():
        vb = b.get(k)
        if vb:
            w = idf.get(k, 1.0)
            num += (va * w) * (vb * w)
    if num == 0:
        return 0.0
    na = math.sqrt(sum((va * idf.get(k, 1.0)) ** 2 for k, va in a.items()))
    nb = math.sqrt(sum((vb * idf.get(k, 1.0)) ** 2 for k, vb in b.items()))
    return num / (na * nb) if na and nb else 0.0


def _mod_set(text: str) -> set[str]:
    out = set()
    for part in re.split(r"[,，、/;；]", text or ""):
        p = part.strip()
        if not p:
            continue
        p = re.sub(r"\s*集成$", "", p)
        out.add(p)
    return out


# ---------------------------------------------------------------- 理解层

SLOT_META = {
    "modules": {"label": "功能范围"},
    "industry": {"label": "所属行业"},
    "scale": {"label": "使用规模"},
    "mode": {"label": "交付模式"},
    "integration": {"label": "集成点"},
    "deadline": {"label": "交期要求"},
    "budget": {"label": "客户预算"},
}

QUESTIONS = {
    "modules": ("这套系统首先要解决哪几件事？请按优先级列 3–5 项。",
                "功能范围是工作量的第一驱动，拆不出模块就无法映射到任何历史案例",
                "影响人天 ±40% 以上", "按「标准审批 + 费控」最小集合假设"),
    "industry": ("这个客户属于哪个行业？有没有必须遵守的行业监管要求？",
                 "行业决定合规模块（等保、医疗数据）与可得的历史案例池",
                 "影响单价 ±7%，并决定检索范围", "按通用行业处理，置信度下调"),
    "scale": ("系统预计覆盖多少用户 / 多少个组织节点？峰值并发大概多少？",
              "规模决定权限模型与查询策略，是继功能范围之后的第二大变量",
              "影响人天 ±8–15%", "按 500–2000 人的中型规模假设"),
    "mode": ("是希望按我们的标准产品做配置，还是接受一定程度的定制开发？",
             "标准配置与定制交付的成本结构不同，直接影响加成率口径",
             "影响报价 ±12%", "按定制交付假设（成本更高）"),
    "integration": ("需要对接哪些既有系统？对接方式是接口、数据库还是文件？",
                    "每个未确认的第三方接口都是历史超支的主因，必须显式计数",
                    "每个集成点约 2.5–4 人天", "按 1 个标准接口假设"),
    "deadline": ("期望什么时候上线？这个时间点是硬约束还是目标？",
                 "交期紧于 16 周会触发赶工，需要计入加班与并行成本",
                 "影响报价 ±5–10%", "按标准 16 周交付周期排期"),
    "budget": ("客户内部的预算区间大概是多少？是已立项还是待立项？",
               "预算是市场对标规则的上界约束，缺了只能按同行价报，容易丢单或亏单",
               "影响成交概率与最终定价", "不设预算上限，按同行 P50 出价"),
}


def _slot(slot: str, value, display: str, confidence: float, source: str,
          evidence: str = "") -> dict:
    return {"slot": slot, "label": SLOT_META[slot]["label"], "value": value,
            "display": display, "confidence": round(confidence, 2),
            "source": source, "evidence": evidence}


def decompose(request: dict, answers: dict | None) -> dict:
    """把客户原话拆成 7 个可定价的槽位。

    每个槽位的 confidence 只有三档：1.0 原话直取 / 0.5 推断 / 0 缺失。
    故意不设 0.7、0.8 这种中间值——那会让人误以为"差不多够用了"。
    """
    text = (request.get("raw_text") or "").strip()
    answers = answers or {}

    mod_hits = _scan(text, MODULE_LEXICON)
    modules = []
    for h in mod_hits:
        if h["canonical"] not in modules:
            modules.append(h["canonical"])
    integ_hits = _scan(text, INTEGRATION_LEXICON)
    integs = []
    for h in integ_hits:
        if h["canonical"] not in integs:
            integs.append(h["canonical"])

    # 追问补充的值优先于原话推断：这是"反向机制"落地的地方
    if answers.get("modules"):
        v = answers["modules"]
        modules = [x.strip() for x in (v if isinstance(v, list) else re.split(r"[,，、]", v)) if x.strip()]
    if answers.get("integration"):
        v = answers["integration"]
        integs = [x.strip() for x in (v if isinstance(v, list) else re.split(r"[,，、]", v)) if x.strip()]

    scale = _extract_scale(text)
    scale_src = "原话直取" if scale["value"] else "缺失"
    if answers.get("scale") not in (None, ""):
        raw = answers["scale"]
        mapped = {"s": 200, "m": 1200, "l": 5000}.get(str(raw))
        num = mapped or int(re.sub(r"[^\d]", "", str(raw)) or 0)
        if num:
            scale = {"value": num, "bucket": "s" if num < 300 else ("m" if num <= 2000 else "l"),
                     "mentions": [{"raw": str(raw), "value": num, "unit": "人", "factor": 1}]}
            scale_src = "追问补充"

    industry = request.get("industry")
    ind_hits = _scan(text, INDUSTRY_LEXICON)
    ind_source = "客户档案"
    if not industry and ind_hits:
        industry, ind_source = ind_hits[0]["canonical"], "原话直取"
    if answers.get("industry"):
        industry, ind_source = answers["industry"], "追问补充"

    mode = None
    mode_src = "缺失"
    if re.search(r"标准(产品|配置|版)|开箱|不用改", text):
        mode, mode_src = "标准配置", "原话直取"
    elif re.search(r"定制|二次开发|从零|重新做|自己开发", text):
        mode, mode_src = "定制交付", "原话直取"
    elif re.search(r"二期|升级|一期|已经上线|存量|改造|基础上", text):
        mode, mode_src = "定制交付", "推断（存量系统上改造）"
    if answers.get("mode"):
        mode, mode_src = answers["mode"], "追问补充"

    budget = _extract_budget(text, request.get("budget_hint"))
    if answers.get("budget") not in (None, ""):
        num = re.sub(r"[^\d.]", "", str(answers["budget"]))
        if num:
            budget = {"value": float(num), "source": "追问补充", "raw": str(answers["budget"])}

    deadline = _extract_deadline(text, request.get("deadline"))
    if answers.get("deadline"):
        deadline = {"raw": str(answers["deadline"]),
                    "months": _lead_months(str(answers["deadline"])), "source": "追问补充"}

    slots = {
        "modules": _slot("modules", modules,
                         "、".join(modules) if modules else "未识别",
                         1.0 if modules else 0.0,
                         "原话直取" if mod_hits else "缺失",
                         "、".join(h["surface"] for h in mod_hits[:6])),
        "industry": _slot("industry", industry, industry or "未识别",
                          1.0 if industry else 0.0, ind_source if industry else "缺失",
                          "、".join(h["surface"] for h in ind_hits[:3])),
        "scale": _slot("scale", scale["value"],
                       (_bucket_label(scale["bucket"]) +
                        (f" · 约 {scale['value']:,} 人" if scale["value"] else ""))
                       if scale["bucket"] else "未识别",
                       1.0 if scale["value"] else 0.0,
                       scale_src,
                       "、".join(m["raw"] for m in scale["mentions"][:3])),
        "mode": _slot("mode", mode, mode or "未识别",
                      1.0 if mode_src == "原话直取" else (0.5 if mode else 0.0),
                      mode_src, "配置 / 定制 / 二期"),
        "integration": _slot("integration", integs,
                             "、".join(integs) if integs else "无外部集成",
                             1.0 if integs else 0.0,
                             "原话直取" if integ_hits else ("追问补充" if answers.get("integration") else "缺失"),
                             "、".join(h["surface"] for h in integ_hits[:4])),
        "deadline": _slot("deadline", deadline["months"], deadline["raw"] or "未识别",
                          1.0 if deadline["raw"] else 0.0,
                          deadline["source"] if deadline["raw"] else "缺失", deadline["raw"] or ""),
        "budget": _slot("budget", budget["value"],
                        f"{budget['value']:.0f} 万" if budget["value"] else "未识别",
                        1.0 if budget["value"] else 0.0,
                        budget["source"] if budget["value"] else "缺失", budget["raw"] or ""),
    }
    phased = bool(re.search(r"二期|升级|一期|已经上线|存量|改造|基础上", text))
    return {"slots": slots, "modules": modules, "integrations": integs,
            "scale": scale, "industry": industry, "mode": mode,
            "budget": budget, "deadline": deadline, "phased": phased}


def clarity_score(slots: dict, rule_params: dict) -> dict:
    weights = rule_params.get("slots") or {}
    total_w = sum(weights.values()) or 1.0
    detail = []
    score = 0.0
    for name, w in weights.items():
        s = slots.get(name) or {"confidence": 0.0}
        norm_w = w / total_w
        score += norm_w * s["confidence"]
        detail.append({"slot": name, "label": SLOT_META.get(name, {}).get("label", name),
                       "weight": round(norm_w, 3), "confidence": s["confidence"],
                       "score": round(norm_w * s["confidence"], 4),
                       "status": "ok" if s["confidence"] >= 1 else ("partial" if s["confidence"] > 0 else "missing")})
    return {"score": round(score, 4), "detail": detail}


def build_questions(slots: dict, weights: dict) -> list[dict]:
    """只为"影响权重 ≥ 8% 且没拿满"的槽位提问。问太多等于没问。"""
    out = []
    for name, w in weights.items():
        s = slots.get(name)
        if not s or s["confidence"] >= 1:
            continue
        q, why, impact, assume = QUESTIONS.get(name, ("请补充该参数", "", "", ""))
        out.append({
            "slot": name, "label": SLOT_META.get(name, {}).get("label", name),
            "q": q, "why": why, "impact": impact, "assume": assume,
            "weight": round(w, 3),
            "blocking": s["confidence"] == 0 and w >= 0.12,
            "current": s["display"],
        })
    out.sort(key=lambda x: (-int(x["blocking"]), -x["weight"]))
    return out


# ---------------------------------------------------------------- 检索层

def retrieve(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    """三路检索。行业过滤是可开关的——第 1 轮开，调参后放开。"""
    industry = ctx["industry"]
    modules = ctx["modules"]
    integs = ctx["integrations"]
    query_text = " ".join([ctx["raw_text"]] + modules + integs)
    strict = params.get("industry_strict", True)
    sim_min = float(params.get("sim_min", 0.18))
    topk = int(params.get("topk", 6))

    hist = rows(conn, "SELECT * FROM quote_history")
    corpus = [_vector(f"{h['title']} {h['scope']} {h['modules']} {h['industry']}", ) for h in hist]
    idf = _idf(corpus + [_vector(query_text)])
    qv = _vector(query_text)

    # ---- 路径 1：历史报价库 · 向量相似度
    scored = []
    for h, dv in zip(hist, corpus):
        if strict and industry and h["industry"] != industry:
            continue
        sim = _cosine(qv, dv, idf)
        if sim < sim_min:
            continue
        scored.append({
            "kind": "history", "id": h["id"], "title": f"{h['client']} · {h['title']}",
            "client": h["client"], "industry": h["industry"],
            "days": float(h["days"]), "price": float(h["price"]),
            "margin": float(h["margin"]), "won": bool(h["won"]),
            "modules": sorted(_mod_set(h["modules"])), "sim": round(sim, 4),
            "quoted_at": h["quoted_at"],
        })
    scored.sort(key=lambda x: (-x["sim"], x["id"]))
    history_hits = scored[:topk]

    # ---- 路径 2：行业经验库 · 同类案例模板
    templates = rows(conn, "SELECT * FROM case_templates")
    tpl_scored = []
    for t in templates:
        tags = set(re.split(r"[,，、]", t["tags"]))
        lex_hit = len(tags & set(modules)) / max(1, len(set(modules)))
        ind_hit = 1.0 if (not industry or t["industry"] in (industry, "通用")) else 0.0
        text_sim = _cosine(qv, _vector(f"{t['name']} {t['scenario']} {t['tags']}"), idf)
        score = 0.45 * lex_hit + 0.25 * ind_hit + 0.30 * min(1.0, text_sim * 1.6)
        if score < 0.16:
            continue
        tpl_scored.append({
            "kind": "template", "id": t["id"], "title": t["name"],
            "industry": t["industry"], "scenario": t["scenario"],
            "days": float(t["days"]), "price": None, "margin": None,
            "modules": [m["name"] for m in json.loads(t["modules"])],
            "module_days": json.loads(t["modules"]),
            "win_rate": t["win_rate"], "sample_n": t["sample_n"],
            "sim": round(score, 4), "note": t["note"],
        })
    tpl_scored.sort(key=lambda x: (-x["sim"], x["id"]))
    template_hits = tpl_scored[:topk]

    # ---- 路径 3：客户档案库 · 历史交易
    cust_hits = []
    profile = one(conn, "SELECT * FROM customer_profiles WHERE id=?",
                  (ctx.get("client_id"),)) if ctx.get("client_id") else None
    if profile:
        deals = rows(conn, """SELECT * FROM quote_history WHERE client=?
                              ORDER BY quoted_at DESC""", (profile["name"],))
        for d in deals[:topk]:
            cust_hits.append({
                "kind": "customer", "id": "CU-" + d["id"], "title": f"{profile['name']} · {d['title']}",
                "client": d["client"], "industry": d["industry"],
                "days": float(d["days"]), "price": float(d["price"]),
                "margin": float(d["margin"]), "won": bool(d["won"]),
                "modules": sorted(_mod_set(d["modules"])),
                "sim": 0.55 + 0.35 * (1 if d["won"] else 0),   # 同一客户，天然高相关
                "quoted_at": d["quoted_at"],
            })

    return {"history": history_hits, "template": template_hits, "customer": cust_hits,
            "profile": dict(profile) if profile else None,
            "corpus_n": len(hist), "strict": strict,
            "sim_min": sim_min, "topk": topk}


def fuse(retrieval: dict, params: dict) -> list[dict]:
    """加权 RRF。三路的分数量纲不同，直接把相似度相加是错的；
    RRF 只用排名，天然免疫量纲差异。"""
    k = float(params.get("rrf_k", 60))
    w = {"history": float(params.get("history", 0.5)),
         "template": float(params.get("template", 0.3)),
         "customer": float(params.get("customer", 0.2))}
    bucket: dict[str, dict] = {}
    for path in ("history", "template", "customer"):
        for i, item in enumerate(retrieval.get(path) or []):
            key = item["id"]
            hit = bucket.setdefault(key, {**item, "score": 0.0, "routes": [], "rank": {}})
            hit["score"] += w[path] * (1.0 / (k + i + 1))
            hit["routes"].append(path)
            hit["rank"][path] = i + 1
            if path != "template" and item.get("sim", 0) > hit.get("sim", 0):
                hit["sim"] = item["sim"]
    items = sorted(bucket.values(), key=lambda x: (-x["score"], x["id"]))
    top = items[0]["score"] if items else 0
    for it in items:
        it["score"] = round(it["score"] / top, 4) if top else 0.0
        it["routes"] = sorted(set(it["routes"]))
    return items


def _mod_match(a: str, b: str) -> bool:
    """模块名模糊对齐。历史库写「供应商准入」，需求里写「供应商管理」，
    字面不同但指的是同一件事；不做对齐会把覆盖率算低，进而虚增人天。"""
    if a == b or a in b or b in a:
        return True
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    inter = ga & gb
    return bool(inter) and len(inter) / min(len(ga), len(gb)) >= 0.5


def _pick_shrink_module(cands: list[dict], modules: list[str]) -> str | None:
    """缩范围砍谁：优先砍主候选明确没覆盖的模块；都覆盖了就砍被候选们
    提及次数最少的——它是最不成熟的范围，砍它对交付确定性伤害最小。"""
    if not modules:
        return None
    top_uncovered = (cands[0].get("uncovered") or []) if cands else []
    if top_uncovered:
        return top_uncovered[0]
    freq = {m: 0 for m in modules}
    for c in cands:
        cm = c.get("modules") or []
        for m in modules:
            if any(_mod_match(m, x) for x in cm):
                freq[m] += 1
    return min(modules, key=lambda m: (freq[m], m))


def to_candidates(fused: list[dict], ctx: dict, params: dict,
                  rules: dict | None = None) -> list[dict]:
    """取 Top3-5，并补上「这个候选覆盖了需求里的哪些模块、缺哪些」。

    二期项目的关键修正：候选案例是「全新建设」口径，而客户已经有一期在跑，
    直接照搬会把一期的人天重复计一遍。所以引入增量系数（默认 0.55，可调）。
    """
    need = set(ctx["modules"])
    out = []
    for it in fused[: int(params.get("top_n", 4))]:
        cand_mods = set(it.get("modules") or [])
        covered = {m for m in need if any(_mod_match(m, c) for c in cand_mods)}
        uncovered = [m for m in need if m not in covered]
        coverage = round(len(covered) / len(need), 3) if need else 0.0
        est_days = it.get("days") or 0.0
        ext_integ = max(0, len(ctx["integrations"]) -
                        sum(1 for c in cand_mods if "集成" in c or c.upper() in
                            {i.upper() for i in ctx["integrations"]}))
        raw_days = est_days * (1 + 0.28 * (1 - coverage)) + 2.5 * ext_integ
        factor = 1.0
        if ctx.get("phased"):
            factor = float(_p(rules or {}, "R-COST-01").get("phase2_factor", 0.55))
        adj_days = _round(raw_days * factor, 1)
        out.append({**it, "coverage": coverage, "uncovered": uncovered,
                    "raw_days": _round(raw_days, 1), "phase_factor": factor,
                    "adj_days": adj_days,
                    "rate": round(it["price"] / it["days"], 3) if it.get("price") and it.get("days") else None})
    return out


def confidence_of(clarity: float, cands: list[dict], profile: dict | None,
                  comp_weights: dict) -> dict:
    """置信度 = 明确度 × a + 检索证据 × b + 客户熟悉度 × c + 价格一致性 × d。"""
    rates = [c["rate"] for c in cands if c.get("rate")]
    if cands:
        top = cands[0]
        n_ref = len(cands)
        evidence = min(1.0, 0.45 * min(1.0, top.get("sim", 0) / 0.75)
                       + 0.30 * top["coverage"]
                       + 0.25 * min(1.0, n_ref / 4.0))
        if len(rates) < 2:
            # 一个样本谈不上"价格口径一致"，给中位数分，不给满分
            coherence = 0.55
        else:
            coherence = 1.0 - min(1.0, _cv(rates) / 0.6)
    else:
        evidence, coherence = 0.0, 0.0
    deals = int(profile["deals"]) if profile else 0
    familiarity = min(1.0, 0.25 + 0.15 * deals)

    parts = {
        "clarity": {"v": round(clarity, 4), "w": comp_weights.get("clarity", 0.35),
                    "why": "需求槽位加权覆盖率"},
        "evidence": {"v": round(evidence, 4), "w": comp_weights.get("evidence", 0.30),
                     "why": f"相似度 {min(1.0, (cands[0].get('sim', 0) if cands else 0) / 0.75):.0%} / "
                            f"覆盖 {cands[0]['coverage'] if cands else 0:.0%} / "
                            f"参考样本 {len(cands)} 个"},
        "familiarity": {"v": round(familiarity, 4), "w": comp_weights.get("familiarity", 0.15),
                        "why": f"历史成交 {deals} 单"},
        "coherence": {"v": round(coherence, 4), "w": comp_weights.get("coherence", 0.20),
                      "why": (f"候选单价比离散度 {_cv(rates):.2f}" if len(rates) >= 2
                              else f"可计价样本仅 {len(rates)} 个，无法判断口径一致性")},
    }
    total_w = sum(p["w"] for p in parts.values()) or 1.0
    score = sum(p["v"] * p["w"] for p in parts.values()) / total_w
    return {"score": round(score, 4), "parts": parts}


# ---------------------------------------------------------------- 定价层

def price_rules(cands: list[dict], ctx: dict, rules: dict) -> dict:
    """三条规则各自独立出价，互不影响。融合才发生在下一步。

    分开出价的意义：当三条规则算出 82 / 96 / 108 三个数时，业务方看到的
    不是"平均 95"，而是"成本说至少 96，市场说 82 也能赢"——这才是分歧，
    分歧才是需要人来拍的地方。
    """
    days = cands[0]["adj_days"] if cands else 0.0
    rates = [c["rate"] for c in cands if c.get("rate")]
    p50 = _pct(rates, 50) or 2.0
    p25 = _pct(rates, 25) or p50 * 0.88
    p75 = _pct(rates, 75) or p50 * 1.12
    budget = ctx["budget"].get("value")

    out = []

    # ---- R-MKT-01 市场竞争对标
    mk = _p(rules, "R-MKT-01")
    band = mk.get("price_band") or [0.75, 1.35]
    rate_mkt = p50
    note_mkt = f"同行单价比 P50 = {p50:.2f} 万/人天（P25 {p25:.2f} / P75 {p75:.2f}，样本 {len(rates)}）"
    # 样本不足时降权而不是弃权：1 条成交记录算出来的 P50 不是"市场价"，
    # 只是个孤例。让它照常说话但声音小一点，比假装它没说话更诚实——
    # 弃权会把权重全部让给成本加成，报价反而系统性偏高。
    mins = max(1, int(mk.get("min_samples", 3) or 1))
    low_sample = len(rates) < mins
    sample_cut = round(len(rates) / mins, 3) if low_sample else 1.0
    if low_sample:
        note_mkt += f"；可计价样本仅 {len(rates)} 条（要求 ≥{mins}），该规则权重按 {sample_cut:.0%} 折算"
    if budget and mk.get("budget_guard", True):
        cap = budget / days if days else 0
        if cap < rate_mkt:
            rate_mkt = min(rate_mkt, max(p50 * band[0], cap))
            note_mkt += f"；客户预算 {budget:.0f} 万压制，单价下探至 {rate_mkt:.2f}"
            out.append({"rule": "R-MKT-02", "name": "预算压制", "hit": True,
                        "note": f"预算单价 {cap:.2f} 万/人天 < 同行 P50，按 P25 与预算的较高者取值"})
    mkt_price = rate_mkt * days
    mkt_price = min(max(mkt_price, days * p50 * band[0]), days * p50 * band[1])
    out.insert(0, {"rule": "R-MKT-01", "name": "市场竞争对标", "hit": True,
                   "price": _round(mkt_price), "days": days,
                   "rate": round(rate_mkt, 3), "note": note_mkt,
                   "sample_cut": sample_cut, "low_sample": low_sample,
                   "detail": {"p25": round(p25, 3), "p50": round(p50, 3),
                              "p75": round(p75, 3), "samples": len(rates),
                              "min_samples": mins, "sample_cut": sample_cut}})

    # ---- R-CUS-01 客户级别折扣
    cus = _p(rules, "R-CUS-01")
    profile = ctx.get("profile") or {}
    level = profile.get("level") or "新客"
    ladder = cus.get("levels") or {"战略": 0.88, "重点": 0.93, "普通": 0.98, "新客": 1.06}
    disc = float(ladder.get(level, 1.0))
    base_rate = _pct([c["rate"] for c in cands if c.get("rate")], 50) or p50
    on_time = profile.get("on_time_rate")
    change_rate = profile.get("change_rate")
    if on_time is not None and on_time >= 0.92:
        disc -= float(cus.get("on_time_bonus", 0.02))
    if change_rate is not None and change_rate > 0.15:
        disc += float(cus.get("change_penalty", 0.15)) * (change_rate - 0.15)
    cus_price = base_rate * days * disc
    cus_note = (f"客户等级「{level}」折扣 {ladder.get(level, 1.0):.2f}"
                + (f"；付款及时率 {on_time:.0%} 减免 {cus.get('on_time_bonus', 0.02):.2f}" if on_time and on_time >= 0.92 else "")
                + (f"；历史变更率 {change_rate:.0%} 加价系数" if change_rate and change_rate > 0.15 else ""))
    if not profile:
        cus_note += "；无客户档案，按新客风险溢价"
    out.append({"rule": "R-CUS-01", "name": "客户级别与履约修正", "hit": True,
                "price": _round(cus_price), "days": days,
                "rate": round(cus_price / days, 3) if days else 0,
                "note": cus_note,
                "detail": {"level": level, "discount": round(disc, 3),
                           "base_rate": round(base_rate, 3)}})

    # ---- R-COST-01 成本加成
    cs = _p(rules, "R-COST-01")
    day_cost = float(cs.get("day_cost", 1.05))
    reserve = float(cs.get("rework_reserve", 0.12))
    markup = float(cs.get("markup", 0.45))
    # 命中「安全合规」（等保 / 密评 / 审计留痕）时成本上浮：
    # 这类工作量在报价阶段最容易被漏掉，是医疗与政企项目毛利被打穿的主因
    comply = (float(cs.get("compliance_factor", 1.0))
              if "安全合规" in ctx.get("modules", []) else 1.0)
    cost = days * day_cost * (1 + reserve) * comply
    cost_price = cost * (1 + markup)
    out.append({"rule": "R-COST-01", "name": "成本加成", "hit": True,
                "price": _round(cost_price), "days": days,
                "rate": round(cost_price / days, 3) if days else 0,
                "note": f"{days:.1f} 人天 × 成本 {day_cost:.2f} 万/人天 × (1+储备 {reserve:.0%})"
                        + (f" × 合规系数 {comply:.2f}" if comply != 1.0 else "")
                        + f" = 成本 {cost:.1f} 万；加成 {markup:.0%}",
                "detail": {"day_cost": day_cost, "cost": round(cost, 1),
                           "markup": markup, "reserve": reserve,
                           "compliance_factor": comply}})
    return {"prices": out, "cost": round(cost, 2), "days": days,
            "p50": round(p50, 3), "p25": round(p25, 3), "p75": round(p75, 3),
            "budget": budget}


def fuse_price(pricing: dict, rules: dict) -> dict:
    """多规则加权融合 + 分歧检测。分歧过大时不硬平均，而是交给人工。"""
    parts = [p for p in pricing["prices"]
             if rules.get(p["rule"], {}).get("enabled", True)]
    weights = {p["rule"]: float(rules.get(p["rule"], {}).get("weight", 0.0) or 0.0)
               for p in parts}
    # 子规则（R-MKT-02）不参与加权，只做说明
    main = [{"rule": p["rule"], "price": p["price"],
             "weight": weights.get(p["rule"], 0),
             "cut": p.get("sample_cut", 1.0)}
            for p in parts if p["rule"] in ("R-MKT-01", "R-CUS-01", "R-COST-01")]
    for m in main:
        m["eff_weight"] = round(m["weight"] * m["cut"], 4)
    total_w = sum(m["eff_weight"] for m in main) or 1.0
    price = sum(m["price"] * m["eff_weight"] for m in main) / total_w
    vals = [m["price"] for m in main]
    spread = (max(vals) - min(vals)) / max(vals) if vals and max(vals) else 0.0

    for m in main:
        m["norm_weight"] = round(m["eff_weight"] / total_w, 3)
        m["contribution"] = round(m["price"] * m["eff_weight"] / total_w, 1)

    cut_note = "；".join(f"{m['rule']} 按配置权重 {m['weight']:.2f} × 样本折算 {m['cut']:.2f}"
                        for m in main if m["cut"] < 1)
    diverged = spread > 0.20
    note = "三规则加权融合" + (f"（{cut_note}）" if cut_note else "")
    if diverged:
        mid = _pct(vals, 50)
        note = (f"规则分歧 {spread:.0%}（超过 20% 阈值），按中位数 {mid:.1f} 万取价，"
                f"并标记为需商务判断" + (f"（{cut_note}）" if cut_note else ""))
        price = mid
    return {"price": _round(price), "spread": round(spread, 3),
            "diverged": diverged, "note": note, "parts": main,
            "total_weight": round(total_w, 3)}


def margin_check(price: float, cost: float, rules: dict) -> dict:
    """毛利率校验 + 重新评估。

    重新评估不是"再算一次"，而是三条明确的出路：
        ① 提价（幅度在授权内 → 自动上调）
        ② 缩范围（砍掉低覆盖模块 → 回到 S13）
        ③ 转商务特批（超授权 → 不出单，交人）
    """
    th = _p(rules, "R-THR-03")
    floor = float(th.get("margin_min", 0.15))
    auto_max = float(th.get("auto_raise_max", 0.08))
    margin = (price - cost) / price if price else 0.0
    result = {"price": _round(price), "cost": _round(cost, 2),
              "margin": round(margin, 4), "floor": floor, "ok": margin >= floor,
              "action": "pass", "note": ""}

    if margin >= floor:
        result["note"] = f"毛利率 {margin:.1%} ≥ 下限 {floor:.0%}，放行"
        return result

    need = cost / (1 - floor) if floor < 1 else cost
    raise_pct = (need - price) / price if price else 0
    result["needed_price"] = _round(need)
    result["raise_pct"] = round(raise_pct, 4)

    if raise_pct <= auto_max:
        result.update({"action": "auto_raise", "price": _round(need), "ok": True,
                       "margin": round((need - cost) / need, 4),
                       "note": f"毛利率 {margin:.1%} 不足，提价 {raise_pct:.1%}（在 {auto_max:.0%} 授权内）"
                               f"至 {need:.1f} 万，毛利率回到 {floor:.0%}"})
    else:
        result.update({"action": "escalate",
                       "options": [
                           {"k": "提价", "v": f"提至 {need:.1f} 万（+{raise_pct:.1%}），超 {auto_max:.0%} 授权，需商务总监批准"},
                           {"k": "缩范围", "v": "砍掉覆盖率最低的模块，回到定价引擎重算"},
                           {"k": "放弃", "v": "按现价成交将亏损，建议不投或改为分期交付"},
                       ],
                       "note": f"毛利率 {margin:.1%} 低于下限 {floor:.0%}，且需提价 {raise_pct:.0%}"
                               f" 超过 {auto_max:.0%} 授权，转商务特批"})
    return result


def build_quote(ctx: dict, cands: list[dict], pricing: dict, fused: dict,
                margin: dict, conf: dict, clarity: float, rules: dict,
                version: str) -> dict:
    days = pricing["days"]
    price = margin["price"]
    cost = pricing["cost"]
    rate = price / days if days else 0

    # 模块人天：模板带拆解就用模板的，历史报价只有总人天则均分（并注明）
    top = cands[0] if cands else {}
    lines = []
    if top.get("module_days"):
        base = top["module_days"]
        scale = days / sum(m["days"] for m in base) if sum(m["days"] for m in base) else 1
        for m in base:
            d = _round(m["days"] * scale, 1)
            lines.append({"module": m["name"], "days": d, "rate": round(rate, 2),
                          "amount": _round(d * rate), "src": "模板拆解"})
    else:
        mods = ctx["modules"] or ["整体交付"]
        per = days / len(mods)
        for m in mods:
            d = _round(per, 1)
            lines.append({"module": m, "days": d, "rate": round(rate, 2),
                          "amount": _round(d * rate), "src": "历史总人天均分"})
    diff = _round(days - sum(l["days"] for l in lines), 1)
    if lines and abs(diff) >= 0.1:
        lines[-1]["days"] = _round(lines[-1]["days"] + diff, 1)
        lines[-1]["amount"] = _round(lines[-1]["days"] * lines[-1]["rate"])

    assumptions = []
    for s in ctx["slots"].values():
        if s["confidence"] < 1:
            q = QUESTIONS.get(s["slot"], ("", "", "", "按默认值处理"))
            assumptions.append(f"{s['label']}未确认，暂按「{q[3]}」")
    if ctx["deadline"].get("months"):
        m = ctx["deadline"]["months"]
        if m < 5:
            assumptions.append(f"交期约 {m} 个月，紧于标准 16 周，已计入并行投入")
    assumptions.append("人天按 8 小时/人天折算，不含客户侧配合人力")

    exclusions = ["硬件、网络与机房资源", "第三方软件许可与订阅费",
                  "历史数据迁移超过 10 万条的部分", "等保测评与第三方认证费用",
                  "上线后超出 3 个月的功能变更"]

    risks = []
    if conf["score"] < 0.70:
        risks.append(f"置信度 {conf['score']:.0%} 低于 70%，参考案例与实际需求存在偏差")
    if top.get("uncovered"):
        risks.append("未覆盖模块：" + "、".join(top["uncovered"][:4]) + "，已按 28% 系数补人天")
    if not ctx["profile"]:
        risks.append("无客户历史交易，折扣与履约风险按新客口径计")
    if fused["diverged"]:
        risks.append(f"三条定价规则分歧 {fused['spread']:.0%}，价格存在谈判空间")
    if margin["action"] == "escalate":
        risks.append(f"毛利率仅 {margin['margin']:.1%}，低于 {margin['floor']:.0%} 下限，需商务特批")
    b = ctx["budget"].get("value")
    if b and price > b * 1.05:
        risks.insert(0, f"报价 {price:.0f} 万超出客户预算 {b:.0f} 万 {price / b - 1:.0%}，"
                        f"需缩范围、分期交付或按预算反推范围")

    valid_days = int(_p(rules, "R-THR-05").get("valid_days", 30))
    return {
        "no": None,   # 由调用方按入库顺序编号
        "client": ctx["client"],
        "request_id": ctx["request_id"],
        "rule_version": version,
        "engine": ENGINE_VERSION,
        "days": days,
        "cost": cost,
        "price": price,
        "rate": round(rate, 2),
        "margin": margin["margin"],
        "margin_ok": margin["ok"],
        "confidence": conf["score"],
        "clarity": clarity,
        "lines": lines,
        "tiers": [
            {"k": "基准价", "price": price, "cond": "按当前范围与交期，30 天内有效"},
            {"k": "含变更储备", "price": _round(price * 1.12),
             "cond": "预留 12% 变更额度，超出部分按变更单另计"},
            {"k": "范围锁定价", "price": _round(price * 0.94),
             "cond": "范围一次冻结，任何新增需求单独报价"},
        ],
        "assumptions": assumptions,
        "exclusions": exclusions,
        "risks": risks,
        "valid_until": (date.today() + timedelta(days=valid_days)).isoformat(),
        "review_required": margin["action"] != "pass" or conf["score"] < 0.80,
    }


# ---------------------------------------------------------------- 主流程

def _node(seq: int, key: str, name: str, kind: str, status: str, summary: str,
          facts: list | None = None, detail: dict | None = None,
          loop: int = 0, ms: float = 0.0) -> dict:
    return {"seq": seq, "key": key, "name": name, "kind": kind, "status": status,
            "summary": summary, "facts": facts or [], "detail": detail or {},
            "loop": loop, "ms": round(ms, 2)}


def run(conn: sqlite3.Connection, request: dict, answers: dict | None = None,
        rules: dict | None = None, version: str | None = None,
        as_of: date | None = None) -> dict:
    """跑一次完整推理。不写库——落库由 persist() 负责，试算与正式出价共用这段逻辑。"""
    as_of = as_of or date.today()
    rules = rules or load_rules(conn)
    version = version or active_version(conn)
    answers = answers or {}

    trace: list[dict] = []
    seq = [0]

    def add(key, name, kind, status, summary, facts=None, detail=None, loop=0, ms=0.0):
        seq[0] += 1
        n = _node(seq[0], key, name, kind, status, summary, facts, detail, loop, ms)
        trace.append(n)
        return n

    t_all = time.perf_counter()
    raw_text = request.get("raw_text") or ""

    # ---- S01 需求输入
    t = time.perf_counter()
    add("intake", "客户需求输入", "input", "ok",
        f"{request['client']} 提交需求（{request.get('channel', '未知渠道')}），原文 {len(raw_text)} 字",
        facts=[{"k": "客户", "v": request["client"]},
               {"k": "渠道", "v": request.get("channel") or "—"},
               {"k": "提交时间", "v": request.get("created_at") or "—"},
               {"k": "预算提示", "v": request.get("budget_hint") or "未提供"}],
        detail={"raw_text": raw_text}, ms=(time.perf_counter() - t) * 1000)

    profile = one(conn, "SELECT * FROM customer_profiles WHERE id=?",
                  (request.get("client_id"),)) if request.get("client_id") else None
    profile_d = dict(profile) if profile else None

    # ---- S02 推理层：先决定这一单怎么打
    t = time.perf_counter()
    if profile_d and profile_d["deals"] >= 5:
        strategy = f"老客复购（{profile_d['deals']} 单成交），优先复用历史方案，允许让价换续约"
        risk = "低"
    elif profile_d:
        strategy = f"存量客户扩展（{profile_d['deals']} 单），历史方案可参考但样本少，需交叉验证"
        risk = "中"
    else:
        strategy = "陌生客户首单，无历史交易，按同行价 + 风险溢价，并放宽行业检索以找结构相近案例"
        risk = "高"
    plan = ["拆解需求槽位", "判断明确度", "三路混合检索", "置信度校验", "三规则定价", "毛利率校验"]
    add("reason", "Agent 推理层", "agent", "ok",
        f"判定为{risk}风险场景，执行 {len(plan)} 步策略",
        facts=[{"k": "客户画像", "v": f"{profile_d['level'] if profile_d else '未知'} / 成交 {profile_d['deals'] if profile_d else 0} 单"},
               {"k": "策略", "v": strategy},
               {"k": "风险等级", "v": risk},
               {"k": "规则版本", "v": version}],
        detail={"plan": plan, "engine": ENGINE_VERSION}, ms=(time.perf_counter() - t) * 1000)

    # ---- S03 需求理解与拆解
    t = time.perf_counter()
    dec = decompose(request, answers)

    # 复核驳回约束（L-C）：「砍掉 X 模块」「总价 ≤ 80 万」「必须含 Y」。
    # 约束是本轮输入的一部分，不是事后补丁——所以应用在拆解层，跟着进 input_hash。
    constraints = answers.get("_constraints") or {}
    applied_constraints: list[str] = []
    if constraints:
        drop = [m for m in constraints.get("drop_modules", [])]
        if drop:
            hit = [m for m in dec["modules"]
                   if any(_mod_match(m, d) for d in drop)]
            if hit:
                dec["modules"] = [m for m in dec["modules"] if m not in hit]
                applied_constraints.append(f"砍掉模块：{'、'.join(hit)}")
                dec["slots"]["modules"]["value"] = dec["modules"]
                dec["slots"]["modules"]["display"] = "、".join(dec["modules"]) or "未识别"
        req_mods = [m for m in constraints.get("require_modules", [])]
        for m in req_mods:
            if not any(_mod_match(m, x) for x in dec["modules"]):
                dec["modules"].append(m)
                applied_constraints.append(f"保留模块：{m}")
        mp = constraints.get("max_price")
        if isinstance(mp, (int, float)) and mp > 0:
            cur = dec["budget"].get("value")
            if cur is None or mp < cur:
                dec["budget"] = {"value": float(mp), "source": "复核意见约束",
                                 "raw": f"总价≤{mp:g}万"}
                applied_constraints.append(f"总价上限 {mp:g} 万")

    ctx = {"request_id": request["id"], "client": request["client"],
           "client_id": request.get("client_id"), "raw_text": raw_text,
           "industry": dec["industry"], "modules": dec["modules"],
           "integrations": dec["integrations"], "mode": dec["mode"],
           "scale": dec["scale"], "budget": dec["budget"],
           "deadline": dec["deadline"], "slots": dec["slots"], "profile": profile_d,
           "phased": dec["phased"], "constraints": applied_constraints}
    loop_no = 1 if answers else 0
    add("decompose", "需求理解与拆解", "agent", "ok",
        f"拆出 {len(dec['modules'])} 个功能模块、{len(dec['integrations'])} 个集成点、"
        f"{len(dec['slots'])} 个定价槽位" + ("（含追问补充）" if answers else "")
        + ("；约束：" + "；".join(applied_constraints) if applied_constraints else ""),
        facts=[{"k": s["label"], "v": s["display"], "tone": "ok" if s["confidence"] >= 1 else "warn"}
               for s in dec["slots"].values()]
              + ([{"k": "复核约束", "v": "；".join(applied_constraints), "tone": "warn"}]
                 if applied_constraints else []),
        detail={"slots": list(dec["slots"].values()),
                "modules": dec["modules"], "integrations": dec["integrations"],
                "scale_mentions": dec["scale"]["mentions"],
                "constraints": applied_constraints},
        loop=loop_no, ms=(time.perf_counter() - t) * 1000)

    # ---- S04 关键词提取
    t = time.perf_counter()
    kws = extract_keywords(raw_text, dec["modules"], dec["integrations"], dec["industry"])
    add("keyword", "关键词提取", "agent", "ok",
        f"提取 {len(kws)} 个检索词，其中模块词 {len(dec['modules'])} 个、集成点 {len(dec['integrations'])} 个",
        facts=[{"k": "高权重", "v": "、".join(k["w"] for k in kws[:6])}],
        detail={"keywords": kws}, loop=loop_no, ms=(time.perf_counter() - t) * 1000)

    # ---- S05 明确度判断
    t = time.perf_counter()
    th1 = _p(rules, "R-THR-01")
    cl = clarity_score(dec["slots"], th1)
    clarity_min = float(th1.get("clarity_min", 0.60))
    enough = cl["score"] >= clarity_min
    add("clarity", "需求明确度判断", "gate", "pass" if enough else "block",
        f"加权覆盖率 {cl['score']:.0%}，阈值 {clarity_min:.0%} → "
        + ("可以进入检索" if enough else "信息不足，转反向追问"),
        facts=[{"k": d["label"], "v": f"权重 {d['weight']:.0%} × 置信 {d['confidence']:.0%}",
                "tone": "ok" if d["status"] == "ok" else ("warn" if d["status"] == "partial" else "bad")}
               for d in cl["detail"]],
        detail={"score": cl["score"], "threshold": clarity_min, "items": cl["detail"]},
        loop=loop_no, ms=(time.perf_counter() - t) * 1000)

    if not enough:
        # ---- S06 反向补充机制（闭环回到 S03）
        t = time.perf_counter()
        qs = build_questions(dec["slots"], th1.get("slots") or {})
        blocking = [q for q in qs if q["blocking"]]
        add("clarify", "反向补充关键参数", "loop", "need_input",
            f"生成 {len(qs)} 条追问（其中 {len(blocking)} 条阻塞出价），暂不出价",
            facts=[{"k": q["label"], "v": q["q"]} for q in qs[:5]],
            detail={"questions": qs,
                    "loop_to": "S03",
                    "note": "客户补给答案后，从需求理解与拆解重跑，不是从追问处继续"},
            ms=(time.perf_counter() - t) * 1000)
        return {"status": "clarified", "trace": trace, "clarity": cl["score"],
                "confidence": 0.0, "retune_rounds": 0, "quote": None,
                "questions": qs, "ctx": ctx,
                "input_hash": _hash({"req": request["id"], "text": raw_text,
                                     "answers": answers, "rules": version,
                                     "as_of": as_of.isoformat()}),
                "output_hash": None, "rule_version": version,
                "elapsed_ms": round((time.perf_counter() - t_all) * 1000, 1)}

    # ---- S07～S11 检索 → 融合 → 候选 → 置信度 → 调参（带环）
    rtv = _p(rules, "R-RTV-01")
    rtv2 = _p(rules, "R-RTV-02")
    max_rounds = int(rtv2.get("max_rounds", 2))
    relax = float(rtv2.get("relax_step", 0.35))
    conf_min = float(_p(rules, "R-THR-02").get("confidence_min", 0.70))
    comp = _p(rules, "R-THR-02").get("components") or {}
    top_n = int(_p(rules, "R-THR-04").get("top_n", 4))
    min_n = int(_p(rules, "R-THR-04").get("min_n", 3))

    base_params = {"industry_strict": True,
                   "sim_min": float(rtv2.get("sim_min", 0.18)),
                   "topk": int(rtv2.get("topk", 6)),
                   "history": float(rtv.get("history", 0.5)),
                   "template": float(rtv.get("template", 0.3)),
                   "customer": float(rtv.get("customer", 0.2)),
                   "rrf_k": float(rtv.get("rrf_k", 60)),
                   "top_n": top_n}

    cands: list[dict] = []
    conf = {"score": 0.0, "parts": {}}
    retrieval = {}
    fused: list[dict] = []
    used = dict(base_params)
    rounds = 0

    for r in range(0, max_rounds + 1):
        rounds = r
        p = dict(base_params)
        if r > 0:
            p["industry_strict"] = False
            p["sim_min"] = round(base_params["sim_min"] * max(0.05, 1 - relax * r), 3)
            p["topk"] = base_params["topk"] + 2 * r
            p["history"] = round(min(0.7, base_params["history"] + 0.05 * r), 3)
            p["template"] = round(max(0.15, base_params["template"] - 0.05 * r), 3)
        used = p

        t = time.perf_counter()
        retrieval = retrieve(conn, ctx, p)
        n_h, n_t, n_c = (len(retrieval["history"]), len(retrieval["template"]),
                         len(retrieval["customer"]))
        add("retrieve", "多维度检索策略", "agent", "ok",
            f"第 {r + 1} 轮：历史库 {n_h} / 模板库 {n_t} / 客户库 {n_c} 条候选",
            facts=[{"k": "历史报价库", "v": f"{n_h} 条（余弦相似度 ≥ {p['sim_min']:.2f}）"},
                   {"k": "行业经验库", "v": f"{n_t} 条（行业 {'严格匹配' if p['industry_strict'] else '已放开'}）"},
                   {"k": "客户档案库", "v": f"{n_c} 条（{retrieval['profile']['name'] if retrieval['profile'] else '无档案'}）"},
                   {"k": "语料规模", "v": f"{retrieval['corpus_n']} 条历史报价"}],
            detail={"params": p, "top_hits": (retrieval["history"] or [])[:3],
                    "templates": (retrieval["template"] or [])[:3]},
            loop=r, ms=(time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        fused = fuse(retrieval, p)
        add("fuse", "混合检索结果融合", "agent", "ok",
            f"RRF 融合后 {len(fused)} 个去重候选，最高融合分 1.00",
            facts=[{"k": "融合方式", "v": f"加权 RRF（k={int(p['rrf_k'])}）"},
                   {"k": "权重", "v": f"历史 {p['history']:.0%} / 模板 {p['template']:.0%} / 客户 {p['customer']:.0%}"},
                   {"k": "多路命中", "v": f"{sum(1 for f in fused if len(f['routes']) > 1)} 个候选被 ≥2 路命中"}],
            detail={"candidates": [{"id": f["id"], "title": f["title"], "score": f["score"],
                                    "routes": f["routes"], "sim": f.get("sim")}
                                   for f in fused[:8]]},
            loop=r, ms=(time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        cands = to_candidates(fused, ctx, p, rules)
        if len(cands) < min_n:
            # 候选不足：从模板库兜底，绝不拿 1 个候选硬报
            for f in fused:
                if len(cands) >= min_n:
                    break
                if f["id"] not in [c["id"] for c in cands]:
                    cands.extend(to_candidates([f], ctx, {**p, "top_n": 1}, rules))
        _pf = cands[0]["phase_factor"] if cands else 1.0
        add("candidate", "候选方案评估 Top3-5", "agent", "ok",
            f"保留 {len(cands)} 个候选方案（要求 ≥{min_n}）"
            + (f"，二期增量系数 {_pf:.2f}" if _pf != 1.0 else ""),
            facts=[{"k": c["title"], "v": f"{c['adj_days']:.1f} 人天 · 覆盖 {c['coverage']:.0%} · 融合分 {c['score']:.2f}"}
                   for c in cands[:5]],
            detail={"candidates": cands}, loop=r, ms=(time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        conf = confidence_of(cl["score"], cands, retrieval["profile"], comp)
        ok = conf["score"] >= conf_min
        add("confidence", "置信度评估", "gate", "pass" if ok else "block",
            f"置信度 {conf['score']:.1%}，阈值 {conf_min:.0%} → "
            + ("进入定价" if ok else "结果不够可信，调整检索参数重跑"),
            facts=[{"k": k, "v": f"值 {v['v']:.1%} × 权重 {v['w']:.0%}（{v['why']}）",
                    "tone": "ok" if v["v"] >= 0.6 else "warn"}
                   for k, v in conf["parts"].items()],
            detail={"score": conf["score"], "threshold": conf_min,
                    "components": conf["parts"]},
            loop=r, ms=(time.perf_counter() - t) * 1000)

        if ok or r == max_rounds:
            break

        # ---- S11 调整检索参数（确定性策略，不是随机重试）
        t = time.perf_counter()
        nxt = {"industry_strict": False,
               "sim_min": round(base_params["sim_min"] * max(0.05, 1 - relax * (r + 1)), 3),
               "topk": base_params["topk"] + 2 * (r + 1),
               "history": round(min(0.7, base_params["history"] + 0.05 * (r + 1)), 3)}
        add("retune", "调整检索参数", "loop", "retuned",
            f"第 {r + 1} 次调参：放开行业过滤、相似度阈值降到 {nxt['sim_min']:.2f}、召回扩到 {nxt['topk']} 条",
            facts=[{"k": "行业过滤", "v": "严格 → 放开（同行业样本不足）"},
                   {"k": "相似度阈值", "v": f"{used['sim_min']:.2f} → {nxt['sim_min']:.2f}"},
                   {"k": "召回条数", "v": f"{used['topk']} → {nxt['topk']}"},
                   {"k": "回检", "v": "回到多维度检索策略"}],
            detail={"from": used, "to": nxt, "loop_to": "S07"},
            loop=r + 1, ms=(time.perf_counter() - t) * 1000)

    # ---- S12 需求相似度匹配
    t = time.perf_counter()
    top = cands[0]
    add("match", "需求相似度匹配", "agent", "ok",
        f"以「{top['title']}」为主参考，模块覆盖率 {top['coverage']:.0%}，"
        f"人天 {top.get('days', 0):.0f} → 修正后 {top['adj_days']:.1f}",
        facts=[{"k": "主参考", "v": top["title"]},
               {"k": "相似度", "v": f"{top.get('sim', 0):.1%}"},
               {"k": "覆盖模块", "v": "、".join(sorted(set(ctx['modules']) - set(top['uncovered']))) or "—"},
               {"k": "未覆盖", "v": "、".join(top["uncovered"]) or "无"}],
        detail={"primary": top, "coverage": top["coverage"]},
        loop=rounds, ms=(time.perf_counter() - t) * 1000)

    # ---- S13 定价规则引擎
    t = time.perf_counter()
    pricing = price_rules(cands, ctx, rules)
    add("pricing", "定价规则引擎", "agent", "ok",
        "三条规则独立出价：" + " / ".join(f"{p['name']} {p['price']:.1f} 万" for p in pricing["prices"] if p.get("price")),
        facts=[{"k": p["name"], "v": p["note"]} for p in pricing["prices"]],
        detail={"pricing": pricing}, loop=rounds, ms=(time.perf_counter() - t) * 1000)

    # ---- S14 融合决策
    t = time.perf_counter()
    fused_price = fuse_price(pricing, rules)
    add("fusion", "多规则融合决策", "agent", "warn" if fused_price["diverged"] else "ok",
        f"加权融合得 {fused_price['price']:.1f} 万，规则分歧 {fused_price['spread']:.0%}",
        facts=[{"k": p["rule"], "v": f"{p['price']:.1f} 万 × 权重 {p['norm_weight']:.0%} = 贡献 {p['contribution']:.1f} 万"}
               for p in fused_price["parts"]] + [{"k": "说明", "v": fused_price["note"]}],
        detail=fused_price, loop=rounds, ms=(time.perf_counter() - t) * 1000)

    # ---- S15 利润率校验（可能 S16 → S13 回环一次）
    t = time.perf_counter()
    margin = margin_check(fused_price["price"], pricing["cost"], rules)
    add("margin", "利润率校验", "gate", "pass" if margin["ok"] else "block",
        f"报价 {margin['price']:.1f} 万，成本 {margin['cost']:.1f} 万，"
        f"毛利率 {margin['margin']:.1%} vs 下限 {margin['floor']:.0%}",
        facts=[{"k": "报价", "v": f"{margin['price']:.1f} 万"},
               {"k": "成本", "v": f"{margin['cost']:.1f} 万（含 {_p(rules,'R-COST-01').get('rework_reserve', 0.12):.0%} 返工储备）"},
               {"k": "毛利率", "v": f"{margin['margin']:.1%}", "tone": "ok" if margin["ok"] else "bad"},
               {"k": "结论", "v": margin["note"]}],
        detail=margin, loop=rounds, ms=(time.perf_counter() - t) * 1000)

    if not margin["ok"]:
        # ---- S16 重新评估：auto_raise 照旧；escalate 时若开启 auto_shrink，
        # 「缩范围」从文字选项升级为可执行分支——真的砍掉一个模块回 S13 重算。
        shrink_on = int(_p(rules, "R-THR-03").get("auto_shrink", 0) or 0)
        victim = None
        if margin["action"] == "escalate" and shrink_on and len(ctx["modules"]) > 1:
            victim = _pick_shrink_module(cands, ctx["modules"])

        if victim:
            t = time.perf_counter()
            add("revise", "重新评估", "loop", "revised",
                f"毛利率不足且提价超授权，执行缩范围：砍掉「{victim}」"
                f"（候选方案中覆盖率最低的模块），回到定价引擎重算",
                facts=[{"k": "砍掉模块", "v": victim},
                       {"k": "砍前", "v": f"{margin['price']:.1f} 万 / 毛利 {margin['margin']:.1%}"},
                       {"k": "规则依据", "v": "R-THR-03.auto_shrink 已开启，至多执行 1 轮"}],
                detail={"action": "shrink", "dropped": victim, "loop_to": "S13",
                        "before": {"price": margin["price"], "margin": margin["margin"]}},
                loop=rounds + 1, ms=(time.perf_counter() - t) * 1000)

            ctx = {**ctx, "modules": [m for m in ctx["modules"] if m != victim]}
            ctx["slots"] = {**ctx["slots"]}
            cands = to_candidates(fused, ctx, used, rules)
            t = time.perf_counter()
            pricing = price_rules(cands, ctx, rules)
            add("pricing", "定价规则引擎", "agent", "ok",
                "缩范围后重新出价：" + " / ".join(f"{p['name']} {p['price']:.1f} 万"
                                                 for p in pricing["prices"] if p.get("price")),
                facts=[{"k": p["name"], "v": p["note"]} for p in pricing["prices"]],
                detail={"pricing": pricing}, loop=rounds + 1,
                ms=(time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            fused_price = fuse_price(pricing, rules)
            add("fusion", "多规则融合决策", "agent",
                "warn" if fused_price["diverged"] else "ok",
                f"缩范围后融合价 {fused_price['price']:.1f} 万，分歧 {fused_price['spread']:.0%}",
                facts=[{"k": p["rule"], "v": f"{p['price']:.1f} 万 × {p['norm_weight']:.0%}"}
                       for p in fused_price["parts"]],
                detail=fused_price, loop=rounds + 1, ms=(time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            margin2 = margin_check(fused_price["price"], pricing["cost"], rules)
            improved = margin2["margin"] > margin["margin"]
            add("margin", "利润率校验", "gate", "pass" if margin2["ok"] else "block",
                f"缩范围后：报价 {margin2['price']:.1f} 万，成本 {margin2['cost']:.1f} 万，"
                f"毛利率 {margin['margin']:.1%} → {margin2['margin']:.1%}",
                facts=[{"k": "砍掉模块", "v": victim},
                       {"k": "毛利变化", "v": f"{margin['margin']:.1%} → {margin2['margin']:.1%}",
                        "tone": "ok" if improved else "bad"},
                       {"k": "结论", "v": margin2["note"]}],
                detail={**margin2, "shrink": {"dropped": victim,
                                              "before_margin": margin["margin"]}},
                loop=rounds + 1, ms=(time.perf_counter() - t) * 1000)
            margin = margin2
            if not margin["ok"]:
                # 至多 1 轮：砍完仍不足，不再继续砍，转特批
                add("revise", "重新评估", "loop", "escalated",
                    f"缩范围后毛利率 {margin['margin']:.1%} 仍低于 {margin['floor']:.0%}，"
                    f"不再继续砍范围（至多 1 轮），转商务特批",
                    facts=[{"k": o["k"], "v": o["v"]} for o in margin.get("options", [])],
                    detail={"options": margin.get("options", []), "final": True},
                    loop=rounds + 1, ms=0)
        else:
            t = time.perf_counter()
            add("revise", "重新评估", "loop", "revised",
                ("提价 " + f"{margin['raise_pct']:.1%}（授权内），回到定价引擎重算"
                 if margin["action"] == "auto_raise"
                 else f"需提价 {margin['raise_pct']:.0%} 超出授权，转商务特批，不自动出单"),
                facts=[{"k": o["k"], "v": o["v"]} for o in margin.get("options", [])]
                      or [{"k": "提价", "v": f"{fused_price['price']:.1f} → {margin['needed_price']:.1f} 万"}],
                detail={"options": margin.get("options", []), "loop_to": "S13"},
                loop=rounds + 1, ms=(time.perf_counter() - t) * 1000)

    # ---- S17 报价单
    t = time.perf_counter()
    quote = build_quote(ctx, cands, pricing, fused_price, margin, conf,
                        cl["score"], rules, version)
    add("quote", "生成完整报价单", "output", "ok",
        f"报价 {quote['price']:.1f} 万 / {quote['days']:.1f} 人天，"
        f"{len(quote['lines'])} 个模块行项，三档报价",
        facts=[{"k": "基准价", "v": f"{quote['price']:.1f} 万"},
               {"k": "单价", "v": f"{quote['rate']:.2f} 万/人天"},
               {"k": "毛利率", "v": f"{quote['margin']:.1%}"},
               {"k": "有效期", "v": quote["valid_until"]}],
        detail={"quote": quote}, loop=rounds, ms=(time.perf_counter() - t) * 1000)

    # ---- S18 人工复核（状态由人推进，这里只记录"待办已生成"）
    add("review", "人工复核确认", "human", "pending",
        "AI 只出建议价与依据，签字的人负责最终价格",
        facts=[{"k": "是否需要复核", "v": "需要" if quote["review_required"] else "可免复核"},
               {"k": "复核要点", "v": "；".join(quote["risks"][:2]) or "按标准流程确认"}],
        detail={"review_required": quote["review_required"],
                "risk_count": len(quote["risks"])},
        loop=rounds, ms=0)

    status = "quoted"
    if not margin["ok"]:
        status = "below_margin"
    elif conf["score"] < 0.60:
        # 置信度太低时宁可不出单：一份 55% 置信度的报价，谈成之后大概率是亏的
        status = "rejected"

    payload = {
        "status": status, "trace": trace, "clarity": cl["score"],
        "confidence": conf["score"], "retune_rounds": rounds,
        "quote": quote, "questions": [], "ctx": ctx,
        "pricing": pricing, "fused_price": fused_price, "margin": margin,
        "candidates": cands,
        "rule_version": version,
        "input_hash": _hash({"req": request["id"], "text": raw_text,
                             "answers": answers, "rules": version,
                             "as_of": as_of.isoformat()}),
        "elapsed_ms": round((time.perf_counter() - t_all) * 1000, 1),
    }
    payload["output_hash"] = _hash({"trace": [{"k": n["key"], "s": n["status"],
                                               "sum": n["summary"]} for n in trace],
                                    "quote": quote})
    return payload


def persist(conn: sqlite3.Connection, request: dict, result: dict,
            actor: str, answers: dict | None = None) -> dict:
    """落库并编号。报价单号按提交顺序生成，不用随机数——审计要能对得上。"""
    n = scalar(conn, "SELECT COUNT(*) FROM quote_runs", (), 0) + 1
    run_id = f"RUN-{date.today():%Y%m%d}-{n:03d}"
    quote = result.get("quote")
    if quote:
        quote = dict(quote)
        quote["no"] = f"Q-{date.today():%Y%m%d}-{n:03d}"
    conn.execute(
        """INSERT INTO quote_runs(id,request_id,rule_version,status,clarity,confidence,
               retune_rounds,input_hash,output_hash,trace,quote,answers,created_at,created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, request["id"], result["rule_version"], result["status"],
         result["clarity"], result["confidence"], result["retune_rounds"],
         result["input_hash"], result.get("output_hash"),
         json.dumps(result["trace"], ensure_ascii=False),
         json.dumps(quote, ensure_ascii=False) if quote else None,
         json.dumps(answers or {}, ensure_ascii=False),
         f"{date.today()} {time.strftime('%H:%M:%S')}", actor))
    conn.commit()
    result = dict(result)
    result["run_id"] = run_id
    result["quote"] = quote
    return result


def simulate(conn: sqlite3.Connection, run_row: dict, rules: dict) -> dict:
    """影子试算：同一个需求、同一份输入，只换权重，看价格怎么动。
    不写库、不改正式运行记录——所以可以反复试。"""
    request = dict(one(conn, "SELECT * FROM quote_requests WHERE id=?",
                       (run_row["request_id"],)))
    try:
        answers = json.loads(run_row["answers"]) if run_row.get("answers") else {}
    except (ValueError, TypeError):
        answers = {}
    return run(conn, request, answers=answers, rules=rules,
               version="simulate", as_of=date.today())
