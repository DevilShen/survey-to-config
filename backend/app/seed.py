"""演示数据。

所有数字都是"原始事实"，指标由 analytics 现算。例如 P-2026-014：
    人天流水 120(base) + 22(scope) + 14(rework) + 12(efficiency) = 168 实际人天
    估算 120 → 偏差 48，五项分解 22 / 14 / 12 / 0 / 0 自动得出。
这样口径一改，全站数字跟着变，而不是散落在各处的手写常量。
"""
from __future__ import annotations

import json
import random
from .seed_sources import seed_sources
import sqlite3
from datetime import date, timedelta

RNG = random.Random(20261018)
TODAY = date(2026, 10, 18)


def _d(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------- 用户与参数

USERS = [
    ("U-001", "沈佳伟", "shenjw@demo.cn", "admin"),
    ("U-002", "周敏", "zhoumin@demo.cn", "pm"),
    ("U-003", "李维", "liwei@demo.cn", "pm"),
    ("U-004", "陈曦", "chenxi@demo.cn", "finance"),
    ("U-005", "王琳", "wanglin@demo.cn", "viewer"),
]

SETTINGS = [
    ("team_size", "6", "交付团队人数"),
    ("iteration_workdays", "10", "单迭代工作日"),
    ("availability", "0.78", "容量可用率（扣除会议、支持、请假）"),
    ("block_ratio", "0.14", "历史阻塞占比"),
    ("quote_base_days", "120", "当前商机基准估算人天"),
    ("quote_rate", "1.5", "人天单价（万元）"),
    ("quotes_this_quarter", "9", "本季出价单数"),
    ("adoption_baseline", "0.67", "三档报价中基准档采纳占比"),
    ("reserve_usage", "0.43", "风险准备金使用率"),
    ("rules_version", "rule_v3.4", "配置推导规则版本"),
    ("kpi_version", "kpi_v1.1", "指标口径版本"),
    ("hypothesis_review_days", "30", "假设卡回看周期（天）"),
]

PIPELINE = [
    ("商机", 46, 1), ("评估", 28, 2), ("签约", 17, 3), ("交付中", 12, 4), ("结项", 9, 5),
]

# ---------------------------------------------------------------- 项目

# id, 名称, 客户, 模式, 状态, 合同额, 预算人天, 估算, 基线预测, 完工比例, 明细级别, 数据质量
ACTIVE_PROJECTS = [
    ("P-2026-014", "某集团审批中台", "华辰控股", "定制交付", "预警", 180, 150, 120, 120, 0.963, "full", "ok"),
    ("P-2026-009", "零售会员体系重构", "悦邻商业", "标准配置", "关注", 96, 108, 96, 101, 1.000, "full", "ok"),
    ("P-2026-021", "供应链协同门户", "中远新材", "定制交付", "高危", 88, 88, 64, 70, 0.860, "full", "gap"),
    ("P-2026-003", "费控升级", "安泰集团", "标准配置", "进行中", 90, 88, 86, 84, 0.970, "summary", "ok"),
    ("P-2026-018", "供应链对账平台", "恒信集团", "定制交付", "进行中", 160, 147, 132, 138, 0.905, "summary", "ok"),
    ("P-2026-011", "集团费控中台", "瑞和实业", "标准配置", "关注", 150, 137, 124, 129, 0.902, "summary", "ok"),
    ("P-2026-025", "渠道商协同系统", "联创电子", "标准配置", "进行中", 140, 126, 113, 118, 0.905, "summary", "ok"),
    ("P-2026-007", "医院运营看板", "康泰医疗", "定制交付", "进行中", 145, 131, 118, 121, 0.907, "summary", "ok"),
    ("P-2026-029", "人力共享服务中心", "中天人力", "标准配置", "进行中", 120, 116, 104, 108, 0.901, "summary", "ok"),
    ("P-2026-016", "合同全生命周期", "正弘法务", "定制交付", "关注", 155, 137, 123, 130, 0.902, "summary", "ok"),
    ("P-2026-023", "门店巡检移动端", "悦邻商业", "标准配置", "进行中", 110, 126, 108, 112, 0.905, "summary", "ok"),
    ("P-2026-030", "供应商准入门户", "中远新材", "定制交付", "预警", 120, 125, 112, 118, 0.904, "summary", "ok"),
]

# 结项项目：估算偏差 = (实际-估算)/估算，用于算中位数
CLOSED_PROJECTS = [
    ("P-2025-031", "恒信集团审批平台", "恒信集团", "定制交付", 160, 118, 110, 138.0),
    ("P-2025-018", "瑞和流程中台", "瑞和实业", "定制交付", 140, 103, 96, 121.0),
    ("P-2025-007", "安泰费控升级", "安泰集团", "标准配置", 120, 95, 88, 97.0),
    ("P-2025-022", "联创渠道中台", "联创电子", "标准配置", 130, 132, 120, 124.8),
    ("P-2025-014", "康泰运营平台", "康泰医疗", "定制交付", 125, 102, 95, 106.4),
    ("P-2025-009", "中天人力共享", "中天人力", "标准配置", 150, 118, 110, 128.7),
    ("P-2025-003", "正弘合同系统", "正弘法务", "定制交付", 135, 102, 95, 113.8),
    ("P-2025-001", "悦邻会员升级", "悦邻商业", "标准配置", 110, 96, 90, 116.1),
]

# 人天流水：project_id -> [(category, days, month, note)]
EFFORT = {
    "P-2026-014": [
        ("base", 120, "2026-09", "按初始估算完成的计划内工作"),
        ("scope", 6, "2026-09", "CHG-0312 审批分级补充"),
        ("scope", 8, "2026-08", "CHG-0305 新增报表订阅"),
        ("scope", 8, "2026-07", "CHG-0287 组织权限细化"),
        ("rework", 8, "2026-08", "REQ-2317 第二次交付"),
        ("rework", 6, "2026-09", "REQ-2317 第三次交付"),
        ("efficiency", 6, "2026-07", "新人上手期"),
        ("efficiency", 4, "2026-08", "测试环境不稳定"),
        ("efficiency", 2, "2026-09", "任务并行切换"),
    ],
    "P-2026-009": [
        ("base", 91, "2026-09", "计划内工作（估算 96，实际基线工时有偏差）"),
        ("scope", 8, "2026-08", "CHG-0309 会员等级规则"),
        ("rework", 6, "2026-09", "REQ-2249 二次交付"),
        ("efficiency", 8, "2026-08", "环境迁移与并行任务"),
        ("wait", 3, "2026-07", "等待客户确认"),
        ("wait", 2, "2026-08", "依赖方接口延迟"),
    ],
    "P-2026-021": [
        ("base", 58, "2026-09", "计划内工作（估算 64，实际基线工时有偏差）"),
        ("scope", 5, "2026-08", "CHG-0318 准入流程调整"),
        ("scope", 3, "2026-09", "CHG-0315 对账口径变更"),
        ("rework", 8, "2026-09", "REQ-1188 第三次交付"),
        ("efficiency", 9, "2026-08", "需求反复澄清导致上下文切换"),
        ("efficiency", 7, "2026-09", "第三方接口联调返工"),
        ("wait", 4, "2026-08", "等待客户确认"),
        ("wait", 2, "2026-09", "环境不可用"),
    ],
}

# ---------------------------------------------------------------- 变更单

# id, project, requirement, 标题, cause, 发起方, 净增人天, 计价, 状态, 提交日, 评估日, 留痕
CHANGES = [
    ("CHG-0312", "P-2026-014", "REQ-2317", "审批金额分级阈值补充", "C1", "客户", 6, "内部承担", "已通过", _d(7), _d(7), 1),
    ("CHG-0305", "P-2026-014", "REQ-2288", "新增报表订阅推送", "C4", "客户", 8, "已计价", "已通过", _d(32), _d(31), 1),
    ("CHG-0287", "P-2026-014", "REQ-2317", "组织权限细化到岗位", "C1", "客户", 8, "内部承担", "已关闭", _d(61), _d(60), 1),
    ("CHG-0309", "P-2026-009", "REQ-2249", "会员等级规则补充", "C3", "客户", 8, "待定", "审批中", _d(4), _d(3), 1),
    ("CHG-0318", "P-2026-021", "REQ-1188", "准入流程节点调整", "C1", "客户", 5, "内部承担", "已通过", _d(11), _d(10), 1),
    ("CHG-0315", "P-2026-021", "REQ-1201", "对账差异口径变更", "C3", "客户", 3, "已计价", "已通过", _d(16), _d(15), 1),
    ("CHG-0321", "P-2026-003", "REQ-3051", "费用科目映射调整", "C4", "客户", 4, "已计价", "已通过", _d(2), _d(1), 1),
    ("CHG-0320", "P-2026-018", "REQ-3088", "对账单模板补充", "C8", "内部", 3, "内部承担", "已通过", _d(3), _d(2), 1),
    ("CHG-0319", "P-2026-011", "REQ-3112", "预算控制维度增加", "C4", "客户", 6, "已计价", "已通过", _d(5), _d(5), 1),
    ("CHG-0317", "P-2026-025", "REQ-3140", "渠道返利计算规则", "C6", "客户", 5, "待定", "审批中", _d(9), None, 0),
    ("CHG-0316", "P-2026-007", "REQ-3201", "看板指标口径调整", "C2", "客户", 3, "内部承担", "已通过", _d(12), _d(11), 1),
    ("CHG-0314", "P-2026-029", "REQ-3260", "入离调转流程补充", "C4", "客户", 7, "已计价", "已通过", _d(14), _d(13), 1),
    ("CHG-0313", "P-2026-016", "REQ-3290", "合同审批权限变更", "C1", "客户", 4, "内部承担", "已通过", _d(18), _d(17), 1),
    ("CHG-0311", "P-2026-023", "REQ-3320", "巡检项模板调整", "C3", "客户", 2, "已计价", "已通过", _d(20), _d(19), 1),
    ("CHG-0310", "P-2026-030", "REQ-3355", "供应商资质字段扩展", "C1", "客户", 5, "内部承担", "已通过", _d(22), _d(21), 1),
    ("CHG-0308", "P-2026-018", "REQ-3081", "回款计划节点调整", "C4", "客户", 6, "已计价", "已通过", _d(26), _d(25), 1),
    ("CHG-0307", "P-2026-003", "REQ-3060", "报销单打印格式", "C3", "客户", 2, "内部承担", "已关闭", _d(28), _d(27), 1),
    ("CHG-0306", "P-2026-011", "REQ-3105", "费用标准按地区分化", "C6", "客户", 8, "已计价", "已通过", _d(35), _d(34), 1),
    ("CHG-0304", "P-2026-007", "REQ-3188", "门诊看板增加时段维度", "C4", "客户", 4, "已计价", "已通过", _d(40), _d(39), 1),
    ("CHG-0303", "P-2026-016", "REQ-3285", "合同模板版本管理", "C2", "内部", 3, "内部承担", "已关闭", _d(44), _d(43), 1),
    ("CHG-0302", "P-2026-025", "REQ-3135", "经销商等级规则调整", "C4", "客户", 7, "已计价", "已通过", _d(48), _d(47), 1),
    ("CHG-0301", "P-2026-029", "REQ-3255", "组织架构导入模板", "C3", "客户", 2, "内部承担", "已关闭", _d(52), _d(51), 1),
    ("CHG-0300", "P-2026-023", "REQ-3315", "巡检异常拍照要求", "C7", "内部", 1, "内部承担", "已关闭", _d(56), _d(55), 1),
    ("CHG-0299", "P-2026-030", "REQ-3350", "供应商黑名单规则", "C4", "客户", 4, "已计价", "已通过", _d(60), _d(59), 1),
    ("CHG-0297", "P-2026-018", "REQ-3075", "对账差异阈值配置", "C5", "内部", 3, "内部承担", "已关闭", _d(64), _d(63), 1),
    ("CHG-0296", "P-2026-011", "REQ-3100", "预算冻结释放规则", "C1", "客户", 5, "内部承担", "已关闭", _d(68), _d(67), 1),
]

# ---------------------------------------------------------------- 需求（手写部分）

# id, project, 标题, 提出方, 规模, 状态, 字段完整度, 创建日
REQS = [
    ("REQ-2317", "P-2026-014", "多级审批流", "客户 · 财务总监", "L", "已验收", 8, _d(220)),
    ("REQ-2288", "P-2026-014", "报表自定义导出", "客户 · 运营经理", "M", "已验收", 6, _d(140)),
    ("REQ-2301", "P-2026-014", "组织架构批量导入", "实施顾问", "M", "待评审", 7, _d(18)),
    ("REQ-2295", "P-2026-014", "成本字段可见性", "客户 · 财务", "S", "已排期", 8, _d(30)),
    ("REQ-2274", "P-2026-014", "移动端待办聚合", "客户 · 区域经理", "L", "待补充", 5, _d(26)),
    ("REQ-2310", "P-2026-014", "审批超时提醒", "客户 · 行政", "M", "开发中", 7, _d(12)),
    ("REQ-2244", "P-2026-009", "客户标签体系", "内部 · 产品", "M", "已验收", 7, _d(180)),
    ("REQ-2260", "P-2026-009", "库存预警", "客户 · 供应链", "M", "已验收", 8, _d(150)),
    ("REQ-2255", "P-2026-009", "会员等级迁移", "客户 · 运营", "L", "已验收", 7, _d(160)),
    ("REQ-2249", "P-2026-009", "优惠券叠加规则", "客户 · 营销", "M", "已验收", 8, _d(170)),
    ("REQ-2238", "P-2026-009", "会员数据看板", "客户 · 运营", "M", "已验收", 6, _d(190)),
    ("REQ-1188", "P-2026-021", "供应商准入流程", "客户 · 采购", "L", "已验收", 6, _d(210)),
    ("REQ-1195", "P-2026-021", "物流轨迹对接", "客户 · 物流", "M", "已验收", 7, _d(180)),
    ("REQ-1201", "P-2026-021", "对账差异处理", "客户 · 财务", "M", "已验收", 8, _d(150)),
    ("REQ-1210", "P-2026-021", "供应商评分卡", "客户 · 采购", "M", "已验收", 5, _d(120)),
    ("REQ-1218", "P-2026-021", "门户单点登录", "内部 · 架构", "S", "已排期", 6, _d(14)),
]

# requirement_id -> [(rev_no, 描述, 交付版本, 估, 实, 诱因, 交付日, 是否计划内)]
REVISIONS = {
    "REQ-2317": [
        (1, "两级审批（仅按角色）", "v3.2", 12, 14, None, "2026-03-18", 0),
        (2, "按金额分级三级", "v3.4", 10, 12, "C1", "2026-05-22", 0),
        (3, "增加会签节点", "v3.6", 12, 8, "C3", "2026-07-30", 0),
    ],
    "REQ-2288": [
        (1, "固定模板导出 Excel", "v3.3", 8, 9, None, "2026-06-12", 0),
        (2, "支持字段自选 + 条件保存", "v3.5", 10, 14, "C2", "2026-08-15", 0),
    ],
    "REQ-2301": [(1, "基于 Excel 模板的组织批量导入", None, 5, 5, None, None, 0)],
    "REQ-2295": [(1, "成本字段按角色隐藏", None, 3, 3, None, None, 0)],
    "REQ-2274": [(1, "待办按优先级聚合到移动端首页", None, 8, 8, None, None, 0)],
    "REQ-2310": [(1, "超时未审批自动提醒", None, 4, 4, None, None, 0)],
    "REQ-2244": [
        (1, "基础标签 + 手工打标（本期不含规则引擎）", "v3.4", 6, 4, None, "2026-05-20", 1),
        (2, "补规则引擎自动打标", "v3.6", 6, 2, "C7", "2026-07-25", 1),
    ],
    "REQ-2260": [(1, "库存上下限预警", "v3.4", 7, 7, None, "2026-06-05", 0)],
    "REQ-2255": [(1, "历史会员等级平滑迁移", "v3.3", 9, 9, None, "2026-05-28", 0)],
    "REQ-2249": [
        (1, "优惠券按优先级依次抵扣", "v3.3", 7, 8, None, "2026-06-18", 0),
        (2, "支持叠加与互斥规则配置", "v3.5", 8, 9, "C4", "2026-08-20", 0),
    ],
    "REQ-2238": [(1, "会员核心指标看板", "v3.2", 6, 6, None, "2026-04-22", 0)],
    "REQ-1188": [
        (1, "两级准入审核", "v1.0", 8, 9, None, "2026-03-10", 0),
        (2, "增加资质分级与黑名单", "v1.4", 7, 10, "C3", "2026-06-02", 0),
        (3, "补充现场评审节点", "v1.8", 6, 8, "C3", "2026-08-28", 0),
    ],
    "REQ-1195": [
        (1, "承运商轨迹查询", "v1.3", 6, 6, None, "2026-05-15", 0),
        (2, "轨迹异常自动预警", "v1.7", 5, 7, "C6", "2026-08-06", 0),
    ],
    "REQ-1201": [(1, "对账差异台账与处理流", "v1.5", 8, 8, None, "2026-07-11", 0)],
    "REQ-1210": [(1, "供应商季度评分卡", "v1.2", 5, 6, None, "2026-05-30", 0)],
    "REQ-1218": [(1, "门户接入集团 SSO", None, 3, 3, None, None, 0)],
}

# ---------------------------------------------------------------- 生成用素材

FEATURES = [
    "审批流配置", "报表订阅", "数据导出", "组织导入", "权限矩阵", "消息模板",
    "移动端首页", "单据打印", "字段扩展", "流程催办", "定时任务", "操作日志",
    "数据看板", "批量处理", "附件管理", "审批代理", "多语言", "水印设置",
    "数据校验", "接口监控", "成本分摊", "预算控制", "合同归档", "供应商评级",
    "客户画像", "库存周转", "价格策略", "结算对账", "发票识别", "电子签章",
    "指标字典", "异常预警", "工单流转", "知识库检索", "角色继承", "数据脱敏",
    "审批加签", "草稿箱", "模板市场", "性能优化",
]
PROPOSERS = ["客户 · 财务总监", "客户 · 运营经理", "客户 · 采购", "客户 · 信息中心",
             "内部 · 产品", "实施顾问", "客户 · 区域经理", "客户 · 行政"]
UNITS = [
    ("定制交付", "某集团审批中台"), ("定制交付", "供应链协同门户"), ("定制交付", "医院运营看板"),
    ("标准配置", "零售会员体系重构"), ("标准配置", "渠道商协同系统"), ("标准配置", "集团费控中台"),
]

CAUSE_POOL = (["C1"] * 33 + ["C3"] * 16 + ["C2"] * 13 + ["C4"] * 12
              + ["C7"] * 9 + ["C6"] * 5 + ["C5"] * 3 + ["C8"] * 2)
CAUSE_DESC = {
    "C1": "需求要素缺失，评审未覆盖",
    "C2": "理解偏差，双方对描述认知不同",
    "C3": "验收标准缺失，完成定义不一致",
    "C4": "客户业务规则变化",
    "C5": "技术方案选型错误",
    "C6": "依赖系统接口变更",
    "C7": "有意简化，分期交付",
    "C8": "协作断点，交接信息丢失",
}


# ---------------------------------------------------------------- 写入

def _users(conn):
    conn.executemany(
        "INSERT INTO users(id,name,email,role,created_at) VALUES(?,?,?,?,?)",
        [(u[0], u[1], u[2], u[3], "2025-12-01") for u in USERS],
    )


def _settings(conn):
    conn.executemany("INSERT INTO app_settings(key,value,note) VALUES(?,?,?)", SETTINGS)
    conn.executemany(
        "INSERT INTO pipeline_stages(stage,count,order_no) VALUES(?,?,?)", PIPELINE
    )


def _projects(conn):
    pm_ids = ["U-002", "U-003"]
    for i, p in enumerate(ACTIVE_PROJECTS):
        conn.execute(
            """INSERT INTO projects(id,name,client,mode,status,contract_amount,budget_days,
               estimate_days,baseline_predicted_days,progress,detail_level,data_quality,
               start_date,end_date,pm_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10], p[11],
             "2026-03-01", None, pm_ids[i % 2]),
        )
    for p in CLOSED_PROJECTS:
        conn.execute(
            """INSERT INTO projects(id,name,client,mode,status,contract_amount,budget_days,
               estimate_days,baseline_predicted_days,progress,detail_level,data_quality,
               start_date,end_date,pm_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p[0], p[1], p[2], p[3], "结项", p[4], p[5], p[6], p[6], 1.0, "summary", "ok",
             "2025-04-01", "2026-01-20", "U-002"),
        )


def _effort(conn):
    rows = []
    for pid, items in EFFORT.items():
        for cat, days, month, note in items:
            rows.append((pid, None, cat, days, month, note))
    # 仅汇总的项目：一条 base 记录，代表"只上报总工时，未做分解"
    for p in ACTIVE_PROJECTS:
        if p[0] in EFFORT:
            continue
        ac = _actual_days(p[0])
        rows.append((p[0], None, "base", ac, "2026-09", "月度工时汇总，未做分解"))
    # 已结项项目：只保留实际总工时
    for p in CLOSED_PROJECTS:
        rows.append((p[0], None, "base", p[7], "2025-12", "结项汇总工时"))
    conn.executemany(
        "INSERT INTO effort_records(project_id,requirement_id,category,days,work_month,note) "
        "VALUES(?,?,?,?,?,?)",
        rows,
    )
    # 财务上报口径 = 各项目人天合计；中远新材有 6 人天未分类工时（阻塞记录漏登）
    conn.execute("""UPDATE projects SET reported_days = COALESCE(
        (SELECT SUM(days) FROM effort_records e WHERE e.project_id = projects.id), 0)""")
    conn.execute("UPDATE projects SET reported_days = 102 WHERE id = 'P-2026-021'")


# 仅汇总项目的实际人天（用预算与目标 CPI 反推，保持组合 CPI 落在 0.93 附近）
SUMMARY_ACTUAL = {
    "P-2026-003": 84.0, "P-2026-018": 140.0, "P-2026-011": 130.0, "P-2026-025": 120.0,
    "P-2026-007": 125.0, "P-2026-029": 110.0, "P-2026-016": 130.0, "P-2026-023": 120.0,
    "P-2026-030": 119.0,
}


def _actual_days(pid: str) -> float:
    return SUMMARY_ACTUAL[pid]


def _changes(conn):
    conn.executemany(
        """INSERT INTO changes(id,project_id,requirement_id,title,cause_code,initiated_by,
           net_days,billing,status,submitted_at,assessed_at,traced)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        CHANGES,
    )


def _requirements(conn):
    rows = [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]) for r in REQS]
    revs = []
    for rid, items in REVISIONS.items():
        for it in items:
            revs.append((rid, it[0], it[1], it[2], it[3], it[4], it[5], it[6], it[7]))

    # --- 生成其余需求，凑足 186 条台账 ---
    generated_events = list(CAUSE_POOL)          # 93 个返工事件
    RNG.shuffle(generated_events)
    patterns = [0] * 96 + [1] * 33 + [2] * 18 + [3] * 8   # 155 条已交付：96 一次通过 / 59 有返工，共 93 次返工
    RNG.shuffle(patterns)

    projects = [p[0] for p in ACTIVE_PROJECTS]
    idx = 0
    for n in range(170):
        rid = f"REQ-{3001 + n}"
        # 前 155 条已交付，后 15 条在途
        delivered = n < 155
        status = RNG.choice(["已验收", "已验收", "已上线"]) if delivered else \
            RNG.choice(["待评审", "待补充", "已排期", "开发中"])
        pid = projects[(n * 7) % len(projects)]
        size = RNG.choice(["S", "M", "M", "L"])
        rows.append((rid, pid, FEATURES[n % len(FEATURES)], RNG.choice(PROPOSERS),
                     size, status, RNG.randint(5, 8), _d(RNG.randint(20, 300))))

        est = {"S": 3, "M": 6, "L": 9}[size]
        revs.append((rid, 1, f"{FEATURES[n % len(FEATURES)]}（首版）", "v3.2", est,
                     est + RNG.choice([0, 0, 1]), None, _d(RNG.randint(10, 90)), 0))
        if delivered:
            extra = patterns[idx]
            idx += 1
            for k in range(extra):
                cause = generated_events.pop() if generated_events else "C1"
                e = est * 0.6
                revs.append((rid, 2 + k, f"第 {2 + k} 次交付：{CAUSE_DESC[cause]}",
                             "v3.4", round(e, 1), round(e + RNG.randint(1, 4), 1),
                             cause, _d(RNG.randint(5, 60)), 0))

    conn.executemany(
        """INSERT INTO requirements(id,project_id,title,proposer,size,status,
           fields_completed,created_at) VALUES(?,?,?,?,?,?,?,?)""", rows)
    conn.executemany(
        """INSERT INTO requirement_revisions(requirement_id,rev_no,description,
           version_delivered,est_days,act_days,cause_code,delivered_at,planned_phase)
           VALUES(?,?,?,?,?,?,?,?,?)""", revs)


def _iterations(conn):
    data = [
        ("26-S06", "交付一组", 33, 32, "已完成", None, "2026-07-06", "2026-07-17"),
        ("26-S07", "交付一组", 36, 29, "已完成", "第三方接口延迟", "2026-07-20", "2026-07-31"),
        ("26-S08", "交付一组", 35, 27, "已完成", "两人请假", "2026-08-03", "2026-08-14"),
        ("26-S09", "交付一组", 34, 33, "已完成", None, "2026-08-17", "2026-08-28"),
        ("26-S10", "交付一组", 36, 31, "已完成", "第三方接口延迟 3 天", "2026-09-01", "2026-09-12"),
        ("26-S11", "交付一组", 36, None, "进行中", "依赖未就绪", "2026-09-15", "2026-09-26"),
    ]
    conn.executemany(
        """INSERT INTO iterations(id,team,committed_points,done_points,status,note,
           start_date,end_date) VALUES(?,?,?,?,?,?,?,?)""", data)


def _deployments(conn):
    data = [
        ("D-001", "P-2026-014", "华辰控股", "v3", 18, 1, 78, "已上线"),
        ("D-002", "P-2026-009", "悦邻商业", "v2", 26, 2, 61, "配置中"),
        ("D-003", "P-2026-021", "中远新材", "v1", 31, 3, 44, "待确认"),
        ("D-004", "P-2026-003", "安泰集团", "v3", 15, 0, 92, "已上线"),
        ("D-005", "P-2026-018", "恒信集团", "v2", 23, 1, 68, "配置中"),
    ]
    conn.executemany(
        """INSERT INTO deployments(id,project_id,client,config_pack,cycle_days,
           redo_count,survey_pass_rate,status) VALUES(?,?,?,?,?,?,?,?)""", data)


HEAT = {
    "报表中心": (12, 47, 9, 0),
    "审批流": (21, 8, 14, 3),
    "权限配置": (9, 6, 26, 1),
    "数据导入": (14, 3, 7, 2),
    "消息通知": (6, 18, 2, 0),
}

MODULE_USAGE = [
    ("报表中心", 6.2, 4.3), ("审批流", 38.4, 4.1), ("权限配置", 12.1, 3.6),
    ("数据导入", 9.8, 3.9), ("消息通知", 44.2, 4.2),
]


def _tickets(conn):
    clients = ["华辰控股", "悦邻商业", "中远新材", "安泰集团", "恒信集团", "瑞和实业"]
    rows = []
    seq = 0
    slow = [38 + (i % 23) for i in range(38)]      # 保证 P90 = 38 分钟
    fast = [5 + (i % 27) for i in range(341)]
    mins = slow + fast
    RNG.shuffle(mins)

    for module, (a, b, c, d) in HEAT.items():
        for cat, n in (("A", a), ("B", b), ("C", c), ("D", d)):
            for _ in range(n):
                seq += 1
                rows.append((f"T-{2600 + seq}", RNG.choice(clients), module, cat,
                             _d(RNG.randint(0, 30)), _d(RNG.randint(0, 20)),
                             mins[seq % len(mins)], 0))
    # 报障工单：不计入咨询，但计入总工单量与重复报障
    bug_modules = list(HEAT.keys())
    for _ in range(171):
        seq += 1
        rows.append((f"T-{2600 + seq}", RNG.choice(clients), RNG.choice(bug_modules), "bug",
                     _d(RNG.randint(0, 30)), _d(RNG.randint(0, 15)),
                     mins[seq % len(mins)], 0))
    # 28 张重复报障，落在报障单上
    bug_idx = [i for i, r in enumerate(rows) if r[3] == "bug"]
    for i in RNG.sample(bug_idx, 28):
        r = list(rows[i])
        r[7] = 1
        rows[i] = tuple(r)

    conn.executemany(
        """INSERT INTO tickets(id,client,module,category,created_at,resolved_at,
           first_response_min,is_repeat) VALUES(?,?,?,?,?,?,?,?)""", rows)
    conn.executemany("INSERT INTO module_usage(module,usage_rate,satisfaction) VALUES(?,?,?)",
                     MODULE_USAGE)


def _feedback(conn):
    themes = [
        ("FB-T01", "报表导出与订阅", 186, "改进现有", 14, 380),
        ("FB-T02", "移动端体验", 142, "改进现有", 11, 260),
        ("FB-T03", "权限配置复杂", 97, "改进现有", 9, 310),
        ("FB-T04", "批量操作能力", 74, "新增能力", 7, 180),
        ("FB-T05", "系统响应速度", 61, "表达抱怨", 12, 420),
        ("FB-T06", "审批流自定义", 110, "改进现有", 10, 290),
        ("FB-T07", "数据导入易错", 95, "改进现有", 8, 210),
        ("FB-T08", "消息通知过多", 85, "表达抱怨", 13, 350),
        ("FB-T09", "移动端离线", 78, "新增能力", 6, 160),
        ("FB-T10", "权限申请流程", 70, "改进现有", 7, 190),
        ("FB-T11", "看板自定义", 65, "新增能力", 5, 140),
    ]
    conn.executemany(
        """INSERT INTO feedback_themes(id,theme,count,type,customer_count,contract_weight)
           VALUES(?,?,?,?,?,?)""", themes)
    conn.execute(
        "INSERT INTO feedback_periods(id,period,total_count,nps,clustered_count) VALUES(?,?,?,?,?)",
        ("FP-2026Q3", "2026Q3", 1208, 34, 1063))


def _market(conn):
    gaps = [
        ("G-01", "多组织数据汇总报表", 7, 420, 18, "自研", "集团型", "2026Q3"),
        ("G-02", "移动端审批与消息聚合", 5, 260, 12, "自研", "零售", "2026Q3"),
        ("G-03", "与 ERP 双向同步", 4, 310, 25, "合作", "制造业", "2026Q3"),
        ("G-04", "自定义字段级权限", 3, 95, 8, "自研", "通用", "2026Q3"),
        ("G-05", "电子签章集成", 2, 150, 20, "暂缓", "通用", "2026Q3"),
        ("G-06", "预算编制与滚动预测", 3, 0, 30, "暂缓", "集团型", "2026Q3"),
        ("G-07", "供应商门户多语言", 2, 0, 14, "暂缓", "制造业", "2026Q3"),
        ("G-08", "移动端离线审批", 2, 0, 10, "暂缓", "零售", "2026Q3"),
        ("G-09", "费用标准智能推荐", 2, 0, 16, "暂缓", "通用", "2026Q3"),
        ("G-10", "跨系统主数据同步", 3, 0, 22, "合作", "集团型", "2026Q3"),
        ("G-11", "报表订阅到企业微信", 2, 0, 6, "自研", "通用", "2026Q3"),
        ("G-12", "审批流版本对比", 1, 0, 9, "暂缓", "通用", "2026Q3"),
        ("G-13", "合同履约提醒", 2, 0, 7, "自研", "法务", "2026Q3"),
        ("G-14", "单据 OCR 批量识别", 1, 0, 18, "合作", "财务", "2026Q3"),
    ]
    conn.executemany(
        """INSERT INTO market_gaps(id,gap,frequency,lost_amount,cost_days,strategy,
           industry,quarter) VALUES(?,?,?,?,?,?,?,?)""", gaps)


def _baselines(conn):
    rows = [
        ("B-01", "定制 × 审批流", 0.8, 12, "2026-07-01"),
        ("B-02", "定制 × 报表", 1.5, 18, "2026-07-01"),
        ("B-03", "标准配置 × 审批流", 2.4, 9, "2026-07-01"),
        ("B-04", "集成对接 × 任意", 0.5, 7, "2026-07-01"),
        ("B-05", "定制 × 数据迁移", None, 3, "2026-07-01"),
        ("B-06", "标准配置 × 报表", 3.1, 6, "2026-07-01"),
        ("B-07", "标准配置 × 会员域", None, 4, "2026-07-01"),
        ("B-08", "定制 × 权限", 1.2, 8, "2026-07-01"),
        ("B-09", "标准配置 × 组织", None, 4, "2026-07-01"),
        ("B-10", "集成对接 × 财务", None, 2, "2026-07-01"),
        ("B-11", "定制 × 移动端", None, 1, "2026-07-01"),
    ]
    conn.executemany(
        "INSERT INTO estimate_baselines(id,dim,productivity,sample_n,updated_at) VALUES(?,?,?,?,?)",
        rows)


KNOWLEDGE = [
    ("KC-0001", "REQ-2317",
     "涉及多级审批、且客户有金额分级要求",
     "首版通常只做角色审批，上线后客户提出金额分级，导致二次开发",
     "需求采集未覆盖「分级维度」这一必问项",
     "评审必问：分级依据（金额/类型/部门）？跨级跳转规则？会签是否必需？",
     "后续 5 个同类需求返工次数 ≤ 1", 23, 14, 9, "active", _d(200), _d(1)),
    ("KC-0002", "REQ-2288",
     "需求描述为「导出要灵活」，未定义灵活的具体维度",
     "首版按固定模板做，客户认为「灵活」指字段可自选，返工",
     "「灵活」这类形容词未转化为可验收的结构描述",
     "涉及形容词的需求，必须让客户在原型上勾选具体字段与条件",
     "后续同类需求首版验收通过", 11, 3, 2, "active", _d(120), _d(3)),
    ("KC-0003", "REQ-2244",
     "需求首版明确写了「本期不含 X」，并已排入后续版本池",
     "分两次交付，但计划内、非失误",
     "策略性分期，不是返工",
     "统计时单列为「计划内分期」，不计入返工率",
     "—", 8, 6, 5, "active", _d(150), _d(9)),
]

KB_TEMPLATES = [
    ("涉及跨系统集成且对方接口文档未提供", "首版按旧版接口开发，联调阶段发现字段变更", "未在签约前锁定接口版本", "合同中约定接口冻结日期，未冻结则按保守档报价", "同类项目联调返工 ≤ 1 次"),
    ("客户提出「审批要快」但未定义时限", "上线后客户以时效不达标为由要求改流程", "非功能需求未量化", "将「快」转化为具体 SLA：节点停留 ≤ X 小时", "验收单包含时效条款"),
    ("报表需求涉及口径计算", "交付后财务与业务对同一指标口径不一致", "指标口径未在需求阶段确认唯一来源", "需求评审必须附指标口径定义与取数 SQL 归属", "上线后无口径争议"),
    ("需求由多个部门共同提出", "开发中后期出现互相矛盾的要求", "未指定唯一决策人", "需求登记时必填「需求决策人」字段", "变更单中无对立诉求"),
    ("涉及数据迁移", "迁移后发现字段缺失或编码不一致", "源数据质量未评估", "迁移前做 500 条抽样质量报告", "迁移返工为 0"),
]


def _knowledge(conn):
    conn.executemany(
        """INSERT INTO knowledge_cards(id,requirement_id,trigger_cond,phenomenon,root_cause,
           action,verify_criterion,hit_count,adopt_count,effective_count,status,created_at,
           last_recalled_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", KNOWLEDGE)
    rows = []
    for i in range(44):
        tpl = KB_TEMPLATES[i % len(KB_TEMPLATES)]
        hit = RNG.randint(3, 26)
        adopt = RNG.randint(1, max(1, hit - 1))
        eff = RNG.randint(0, adopt)
        status = "active" if adopt / hit >= 0.2 else "demoted"
        rows.append((
            f"KC-{1001 + i:04d}", f"REQ-{3001 + (i * 3) % 170}", tpl[0], tpl[1], tpl[2],
            tpl[3], tpl[4], hit, adopt, eff, status,
            _d(RNG.randint(60, 300)), _d(RNG.randint(1, 40)),
        ))
    conn.executemany(
        """INSERT INTO knowledge_cards(id,requirement_id,trigger_cond,phenomenon,root_cause,
           action,verify_criterion,hit_count,adopt_count,effective_count,status,created_at,
           last_recalled_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)


def _hypotheses(conn):
    rows = [
        ("HK-2026-041", "报表中心",
         "报表模块的咨询集中在「找不到自定义导出」，根因是入口藏在二级菜单且有三种命名并存，而非客户不需要该功能。",
         _j(["该模块 B 类（找不到）咨询 47 条 / 该模块咨询总量 68 条 = 69%",
             "涉及 12 个客户，覆盖 5 个行业（非单客户偏好）",
             "该功能实际使用率 6.2%，低于同类功能均值 24%",
             "客户原话高频词：「导出」「下载」「在哪」"]),
         _j(["若客户在访谈中明确知道入口位置但仍不用 → 是需求不成立，不是入口问题",
             "若使用率低但导出后使用频繁 → 是频次天然低，不应占用版本资源"]),
         "A/B 调整入口层级（1 个迭代），观察使用率变化", 2, "中高", "待验证", _d(12), None, None),
        ("HK-2026-038", "权限配置",
         "权限配置的 C 类咨询（理解错）源于客户按「部门」理解数据范围、系统按「角色」表达，属概念模型问题。",
         _j(["该模块 C 类 26 条，占该模块咨询 62%", "涉及 9 个客户，话术中「部门」出现率 71%"]),
         _j(["若按部门维度改版后咨询量下降 < 20% → 是培训问题而非模型问题"]),
         "访谈 8 个客户 + 原型测试", 3, "中", "待验证", _d(9), None, None),
        ("HK-2026-035", "数据导入",
         "批量导入失败集中在「模板格式校验提示不清」，客户反复试错而非不会操作。",
         _j(["导入类工单 26 条，其中 21 条与校验失败有关", "平均每个客户试错 2.4 次"]),
         _j(["若补充提示后失败率不降 → 是源数据质量问题，应做导入前质量检查"]),
         "上线校验提示改造，对比失败率", 2, "高", "待验证", _d(6), None, None),
        ("HK-2026-029", "消息通知",
         "「消息太多」的抱怨可通过聚合与频次控制解决，不需要做消息中心改版。",
         _j(["抱怨类反馈 85 条，集中在审批提醒", "关闭通知的客户次日留存无显著变化"]),
         _j(["若聚合后打开率仍 < 15% → 说明渠道本身失效，需换触达方式"]),
         "对 30% 客户灰度聚合策略", 1.5, "中", "待验证", _d(38), None, None),
        ("HK-2026-024", "审批流",
         "审批会签节点的需求来自少数大客户，不具备普遍性，应按项目计价而非产品化。",
         _j(["会签相关变更 3 次，均来自 2 个客户", "合同额合计占比 18%"]),
         _j(["若后续 3 个月内新增 3 家以上客户提出 → 假设不成立"]),
         "观察 3 个月需求分布", 0, "低", "待验证", _d(41), None, None),
        ("HK-2026-019", "报表中心",
         "报表导出慢是数据量问题，加索引即可，不需要重构查询层。",
         _j(["慢查询工单 9 条，均出现在单客户月结日"]),
         _j(["若加索引后 P95 仍 > 8s → 需重构"]),
         "对 top5 慢查询加索引并观察", 1, "高", "已验证", _d(55), _d(20),
         "P95 从 12.4s 降至 2.1s，假设成立"),
    ]
    conn.executemany(
        """INSERT INTO hypotheses(id,module,claim,evidence,falsify,method,cost_days,
           strength,status,created_at,reviewed_at,outcome) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows)


def _candidates(conn):
    rows = [
        ("FB-101", "报表自定义导出入口重构", "报表中心", 21.6, 1.5, 0.8, 4, 1.25, "HK-041", _d(10)),
        ("FB-118", "成本字段级权限", "权限配置", 12.0, 2.0, 1.0, 5, 1.20, "工单 26 条", _d(12)),
        ("FB-093", "批量导入模板校验提示", "数据导入", 11.0, 1.0, 0.8, 3, 1.05, "工单 21 条", _d(14)),
        ("FB-127", "移动端待办消息聚合", "消息通知", 18.0, 1.5, 0.5, 8, 1.10, "反馈 142 条", _d(16)),
        ("FB-085", "审批流会签节点", "审批流", 12.0, 2.0, 0.8, 12, 1.40, "变更 3 次", _d(18)),
    ]
    conn.executemany(
        """INSERT INTO version_candidates(id,title,module,reach,impact,confidence,effort,
           risk,evidence,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""", rows)


QUESTIONS = [
    ("Q3", "贵公司组织层级有几级？", "single",
     [{"v": 2, "l": "2 级：总部 → 门店"}, {"v": 3, "l": "3 级：集团 → 区域 → 门店"}], 3),
    ("Q7", "系统使用人数规模？", "single",
     [{"v": "s", "l": "< 200 人"}, {"v": "m", "l": "200–500 人"},
      {"v": "l", "l": "500–1000 人"}, {"v": "xl", "l": "> 1000 人"}], "m"),
    ("Q12", "各层级能看到哪部分数据？", "single",
     [{"v": "self", "l": "只看本部门"}, {"v": "subtree", "l": "区域看下属门店"},
      {"v": "all", "l": "集团看全部"}], "subtree"),
    ("Q18", "报销金额超过多少，需要再上一级审批？", "single",
     [{"v": 5000, "l": "5,000 元"}, {"v": 10000, "l": "10,000 元"}, {"v": None, "l": "暂不确定"}], 5000),
    ("Q21", "是否需要财务与法务同时审批（会签）？", "single",
     [{"v": True, "l": "需要，同时审批"}, {"v": False, "l": "不需要，依次审批"}], False),
    ("Q25", "成本类字段谁能看到？", "single",
     [{"v": "fin", "l": "仅财务角色"}, {"v": "all", "l": "所有角色可见"}], "fin"),
]

SURVEYS = [
    ("SV-2026-021", "中远新材", "P-2026-021", 40, "已回答", 34),
    ("SV-2026-014", "华辰控股", "P-2026-014", 40, "已回答", 40),
    ("SV-2026-009", "悦邻商业", "P-2026-009", 40, "已回答", 40),
    ("SV-2026-003", "安泰集团", "P-2026-003", 40, "已回答", 40),
]

# 历史问卷答案（用于规则回归比对）
HISTORY_ANSWERS = {
    "SV-2026-014": {"Q3": 3, "Q7": "l", "Q12": "subtree", "Q18": 10000, "Q21": True, "Q25": "fin"},
    "SV-2026-009": {"Q3": 2, "Q7": "m", "Q12": "self", "Q18": 5000, "Q21": False, "Q25": "fin"},
    "SV-2026-003": {"Q3": 3, "Q7": "l", "Q12": "all", "Q18": 5000, "Q21": False, "Q25": "all"},
}


def _survey(conn):
    for sid, client, pid, total, status, progress in SURVEYS:
        conn.execute(
            """INSERT INTO survey_templates(id,client,project_id,version,total_questions,
               status,created_at) VALUES(?,?,?,?,?,?,?)""",
            (sid, client, pid, "survey_v2.1", total, status, _d(30)),
        )
        for i, (code, text, qtype, opts, default) in enumerate(QUESTIONS, start=1):
            conn.execute(
                """INSERT INTO survey_questions(template_id,code,text,qtype,options,
                   default_value,order_no,section) VALUES(?,?,?,?,?,?,?,?)""",
                (sid, code, text, qtype, _j(opts), _j(default), i, "组织与流程"),
            )

    for sid, _, _, _, _, progress in SURVEYS:
        if sid == "SV-2026-021":
            answers = {code: default for code, _, _, _, default in QUESTIONS}
        else:
            answers = HISTORY_ANSWERS[sid]
        for code, val in answers.items():
            conn.execute(
                """INSERT INTO survey_answers(template_id,code,value,answered_at,answered_by)
                   VALUES(?,?,?,?,?)""",
                (sid, code, None if val is None else _j(val), _d(3), "客户 · 信息中心"),
            )
        conn.execute(
            "UPDATE survey_templates SET status=? WHERE id=?",
            ("已回答" if progress >= 40 else "已回答", sid),
        )


METRICS = [
    ("估算人天", "成本", "Σ 任务估算人天", "立项/报价时基线，风险准备金单列", "dwd_task"),
    ("实际人天", "成本", "Σ hours / 8", "标准人天折算，不含加班系数", "dwd_effort"),
    ("人天偏差率", "成本", "(实际 − 估算) / 估算", "正值为超支", "计算"),
    ("CPI", "成本", "EV / AC", "EV = 预算 × 完成规模比例", "dws_project_month"),
    ("EAC", "成本", "AC + (BAC − EV) / CPI", "CPI ≤ 0 时返回 INVALID，不给假数", "计算"),
    ("返工占比", "成本", "返工人天 / 实际人天", "严格口径，仅需求二次开发；缺陷修复另计", "dwd_effort"),
    ("变更净增率", "成本", "变更净增人天 / 初始估算人天", "只计已审批变更", "dwd_change"),
    ("需求返工率", "需求", "返工需求数 / 交付需求数", "返工 = 同一需求 ≥ 2 次交付", "dwd_requirement_revision"),
    ("平均返工次数", "需求", "Σ 返工次数 / 返工需求数", "只统计已返工需求", "计算"),
    ("一次通过率", "需求", "一次通过验收需求数 / 交付需求数", "以验收单为准，不以开发完成为准", "dwd_acceptance"),
    ("需求稳定周期", "需求", "最后一次变更日 − 首次交付日", "单位天，衡量需求定型时间", "计算"),
    ("变更响应时长", "变更", "提交 → 影响评估完成", "单位小时", "dwd_change"),
    ("变更蔓延度", "变更", "受影响需求数 / 变更单数", "> 2 说明单点变更在扩散", "计算"),
    ("计划达成率", "排期", "按期完成迭代数 / 迭代总数", "按期 = 实际完成日 ≤ 承诺日", "dws_iteration"),
    ("速率 Velocity", "排期", "每迭代完成需求点（近 6 迭代中位数）", "用中位数不用均值", "计算"),
    ("阻塞占比", "排期", "阻塞人天 / 实际人天", "含等待客户确认、依赖方、环境不可用", "dwd_block"),
    ("需求前置时间", "排期", "进入排期 → 上线", "取 P85，不用均值", "计算"),
    ("缺陷密度", "质量", "缺陷数 / 需求点", "分母统一用需求点，不用代码行", "dwd_defect"),
    ("缺陷逃逸率", "质量", "上线后缺陷 / (上线前 + 上线后)", "核心质量指标，反映测试有效性", "计算"),
    ("上线回滚率", "质量", "回滚发布数 / 发布总数", "发布单元为版本，不是单次部署", "dwd_release"),
    ("MTTR", "质量", "Σ 故障恢复时长 / 故障数", "只计 P1/P2 故障", "dwd_incident"),
    ("咨询占比", "服务", "咨询类工单 / 工单总数", "咨询 = 不会用 / 找不到，不含功能缺失", "dwd_ticket"),
    ("重复报障率", "服务", "同客户同问题 30 日内二次报障 / 总报障", "反映「修了没修好」或「说了没说清」", "计算"),
    ("功能咨询集中度", "服务", "Top3 模块咨询量 / 咨询总量", "> 60% 说明值得专项治理", "计算"),
    ("初始化周期", "交付", "签约 → 系统可用", "系统可用 = 完成首个真实业务流程", "dwd_impl"),
    ("配置返工次数", "交付", "同一客户配置推翻重做次数", "一次「推导→确认→修改」计 1 次", "dwd_config"),
    ("问卷一次通过率", "交付", "无需追问即完成推导的问卷 / 总问卷", "衡量问卷设计质量", "计算"),
    ("TTFV", "交付", "签约 → 首次完成核心业务闭环", "SaaS 关键指标，与初始化周期区分", "计算"),
]

MODULE_CONTENT = [
    ("market", "01", "市场洞察", "Market Insight", "输入",
     "价值不在「知道市场要什么」，而在知道自己的产品能力与客户诉求之间的差距在哪、值不值得补。",
     "唯一有增量价值的数据是自己的丢单原因与客户诉求，行业报告摘要不构成决策依据。", 1),
    ("estimate", "02", "成本评估", "Estimation", "输入",
     "报价的本质不是算成本，是用有限的历史样本给不确定性定价。",
     "样本数 < 5 的格子不出基线。用一个样本得出的「基线」比拍脑袋更危险，因为它有数字的外衣。", 2),
    ("requirement", "03", "需求记录", "Requirement", "输入",
     "真正的产出不是需求列表，是每条需求的可追溯决策链——谁提的、为什么提、后来为什么改。",
     "8 个必填字段少一个就不允许进入排期——这是防范围蔓延的第一道闸。", 3),
    ("schedule", "04", "研发排期", "Scheduling", "过程",
     "排期失真的根因几乎从不是「估错工时」，而是承诺时忽略了历史速率、阻塞概率、变更冲击。",
     "有这张测算纸，排期争议会从「你凭什么说做不完」变成「这个参数我们认不认」。", 4),
    ("change", "05", "需求变更", "Change", "过程",
     "变更管理的目标不是减少变更，是让每一次变更有单、有因、有价、有归属。",
     "变更率降不下来是业务现实，隐性变更才致命。所以目标定「变更有单」，不是「变更少」。", 5),
    ("cost", "06", "成本执行", "Cost Execution", "过程",
     "不是「看花了多少钱」，是在项目还没结束时就知道最终会超多少、超在哪。",
     "EAC 是预测不是事实。项目 30% 前进度时，系统必须给区间而非单点，否则会误导决策。", 6),
    ("delivery", "07", "部署实施", "Deployment", "输出",
     "实施不可复制的根因不是配置项太多，是客户的组织与流程信息在签约后才被采集。",
     "问卷前置到售前，答案作为报价与排期输入——不是签约后才开始问。", 7),
    ("support", "08", "售后运维", "Support", "反馈",
     "价值不在工单量多少，而在把工单翻译成产品问题：设计问题、质量问题，还是客户自己的使用问题。",
     "判断「配置错」还是「产品错」，必须对照该客户上线时的配置基线。没有基线，所有问题都会被归到「产品不稳定」。", 8),
    ("feedback", "09", "用户反馈", "Feedback", "反馈",
     "反馈不是需求，反馈是关于产品哪里让人不舒服的证据。直接把反馈当需求做，是最常见的灾难。",
     "市场洞察看「没买的人为什么没买」，用户反馈看「买了的人在忍什么」。两者合起来才是完整输入。", 9),
]


def _metrics(conn):
    conn.executemany(
        "INSERT INTO metrics_dict(name,category,formula,note,source,version) VALUES(?,?,?,?,?,?)",
        [(m[0], m[1], m[2], m[3], m[4], "kpi_v1.1") for m in METRICS])


def _module_content(conn):
    conn.executemany(
        """INSERT INTO module_content(module,no,name,en,tag,judgment,note,order_no)
           VALUES(?,?,?,?,?,?,?,?)""", MODULE_CONTENT)


def _audit(conn):
    entries = [
        ("成本执行", "variance.recompute", "P-2026-014", {"closure": 0.0, "items": 5}, "10:24"),
        ("需求变更", "change.assess", "CHG-0312", {"net_days": 6, "billing": "内部承担"}, "09:51"),
        ("部署实施", "config.derive", "SV-2026-021", {"rule_version": "rule_v3.4", "tbd": 2}, "09:30"),
        ("售后运维", "hypothesis.create", "HK-2026-041", {"module": "报表中心", "b_share": 0.69}, "昨天 18:02"),
        ("用户反馈", "feedback.cluster", "2026Q3", {"new": 2, "merged": 5}, "昨天 16:40"),
        ("成本评估", "baseline.update", "定制 × 审批流", {"samples": 12, "productivity": 0.8}, "昨天 15:10"),
        ("需求记录", "requirement.review", "REQ-2301", {"fields": "7/8"}, "昨天 11:22"),
        ("研发排期", "iteration.commit", "26-S11", {"committed": 36}, "昨天 10:05"),
    ]
    conn.executemany(
        "INSERT INTO audit_log(actor,action,target,payload,created_at) VALUES(?,?,?,?,?)",
        [(a[0], a[1], a[2], _j(a[3]), f"2026-10-18 {a[4]}" if ":" in a[4] and "昨天" not in a[4]
          else f"2026-10-17 {a[4].replace('昨天 ', '')}") for a in entries])


def _snapshots(conn):
    """12 周组合挣值快照。CPI = ev / ac，历史趋势不靠手写。"""
    cpis = [0.99, 0.98, 0.97, 0.95, 0.94, 0.96, 0.93, 0.91, 0.92, 0.90, 0.89, 0.86]
    weeks = ["W40", "W41", "W42", "W43", "W44", "W45", "W46", "W47", "W48", "W49", "W50", "W51"]
    ac = 1000.0
    rows_ = [(w, round(c * ac, 1), ac) for w, c in zip(weeks, cpis)]
    conn.executemany("INSERT INTO evm_snapshots(week,ev,ac) VALUES(?,?,?)", rows_)


def seed_all(conn: sqlite3.Connection) -> None:
    _users(conn)
    _settings(conn)
    _projects(conn)
    _effort(conn)
    _changes(conn)
    _requirements(conn)
    _iterations(conn)
    _deployments(conn)
    _tickets(conn)
    _feedback(conn)
    _market(conn)
    _baselines(conn)
    _knowledge(conn)
    _hypotheses(conn)
    _candidates(conn)
    _survey(conn)
    _metrics(conn)
    _module_content(conn)
    _audit(conn)
    _snapshots(conn)
    seed_sources(conn)

    # 市场报价 Agent：客户档案、历史报价、案例模板、规则权重、商机需求
    from .seed_pricing import seed_pricing, seed_runs

    seed_pricing(conn)
    seed_runs(conn)
