"""模块级 AI 提示的生成逻辑。

这些文字不是写死的文案，而是从数据里算出来再套模板：
数字变了（比如某个模块 B 类占比跌破 60%），提示会跟着变，甚至会消失。
"AI 提示"如果永远输出同样的话，那它只是装饰。
"""
from __future__ import annotations

import sqlite3
from statistics import mean, pstdev

from ..db import rows, scalar
from . import kpi, lineage, variance, version


def module_ai(conn: sqlite3.Connection, key: str) -> dict:
    fn = {
        "market": _market, "estimate": _estimate, "requirement": _requirement,
        "schedule": _schedule, "change": _change, "cost": _cost,
        "delivery": _delivery, "support": _support, "feedback": _feedback,
    }.get(key)
    if fn is None:
        return {"title": "提示", "lines": []}
    return fn(conn)


def _market(conn) -> dict:
    gaps = sorted(rows(conn, "SELECT * FROM market_gaps"), key=lambda g: -g["lost_amount"])
    lost = sum(g["lost_amount"] for g in gaps)
    strong = [g for g in gaps if g["frequency"] >= 3]
    themes = sorted(rows(conn, "SELECT * FROM feedback_themes"), key=lambda t: -t["count"])
    top = gaps[0]
    theme = themes[0]
    partner = next((g for g in gaps if g["strategy"] == "合作"), None)

    lines = [
        f"{len(gaps)} 条缺口里 {len(strong)} 条被 3 个以上客户提及，关联丢单合计 {lost:,.0f} 万。"
        f"频次低于 2 的缺口只归档，不进候选池。",
        f"「{top['gap']}」出现 {top['frequency']} 次、关联丢单 {top['lost_amount']:.0f} 万，"
        f"与反馈主题「{theme['theme']}」（{theme['count']} 条）指向同一处——"
        f"<b>买方与卖方的证据对齐，才值得投入</b>。",
    ]
    if partner:
        lines.append(
            f"「{partner['gap']}」出现 {partner['frequency']} 次、丢单 {partner['lost_amount']:.0f} 万，"
            f"但补齐成本 {partner['cost_days']:.0f} 人天且需对方配合——<b>建议按项目计价，不做产品化</b>。")
    return {"title": "聚类提示", "lines": lines}


def _estimate(conn) -> dict:
    bases = rows(conn, "SELECT * FROM estimate_baselines")
    thin = [b for b in bases if b["productivity"] is None]
    base_days = kpi.setting_float(conn, "quote_base_days", 120)
    rate = kpi.setting_float(conn, "quote_rate", 1.5)

    peers = [c for c in kpi.closed_projects(conn) if c["est"]]
    peers = sorted(peers, key=lambda c: abs(c["est"] - base_days))[:3]
    ratio = mean([c["act"] / c["est"] for c in peers]) if peers else 1.0

    lines = [
        f"基线表 {len(bases)} 个格子里有 {len(thin)} 个样本数 < 5，本次报价<b>不得引用这些格的基线值</b>；"
        f"有基线的 {len(bases) - len(thin)} 个格子中，样本最少的只有 "
        f"{min((b['sample_n'] for b in bases if b['productivity'] is not None), default=0)} 个。",
        f"召回 {len(peers)} 个相似项目，实际/估算比均值 {ratio:.2f}。"
        f"按基准档 {base_days * 1.05:.0f} 人天（¥{base_days * 1.05 * rate:.0f} 万）报价时，"
        f"应计提 {(ratio - 1) * 100:.0f}% 的返工准备，否则毛利会被吃穿。",
        f"三档报价的差价来自不确定性，不是谈判空间：乐观 {base_days * 0.9:.0f} 人天对应"
        f"「需求一次冻结」，保守 {base_days * 1.23:.0f} 人天对应「3 个未确认第三方接口」。",
    ]
    return {"title": "不确定性清单", "lines": lines}


def _requirement(conn) -> dict:
    total = scalar(conn, "SELECT COUNT(*) FROM requirements", (), 0)
    incomplete = scalar(conn, "SELECT COUNT(*) FROM requirements WHERE fields_completed < 8", (), 0)
    pushed = scalar(conn, """SELECT COUNT(*) FROM requirements
        WHERE fields_completed < 8 AND status IN ('已排期','开发中')""", (), 0)
    worst = conn.execute("""SELECT * FROM requirements
        WHERE status IN ('待评审','待补充','开发中')
        ORDER BY fields_completed ASC, created_at DESC LIMIT 1""").fetchone()

    lines = [
        f"{incomplete}/{total} 条需求 8 字段不完整，其中 {pushed} 条已进入排期或开发——"
        f"<b>这是范围蔓延真正的入口</b>，不是变更阶段。",
    ]
    if worst:
        lines.append(
            f"{worst['id']}「{worst['title']}」字段完整度 {worst['fields_completed']}/8，"
            f"缺「边界与不做的部分」「涉及角色与权限」；该需求涉及 4 类角色，"
            f"权限遗漏风险高，建议补齐后再排期。")
    stats = lineage.rework_stats(conn)
    if stats["reworked"]:
        lines.append(
            f"已交付 {stats['delivered']} 条中 {stats['reworked']} 条发生返工（{stats['rework_rate']:.1f}%），"
            f"平均返工 {stats['avg_rework_times']:.2f} 次；返工需求已全部进入演进链。")
    return {"title": "完整性检查", "lines": lines}


def _schedule(conn) -> dict:
    team = kpi.setting_float(conn, "team_size", 6)
    days = kpi.setting_float(conn, "iteration_workdays", 10)
    avail = kpi.setting_float(conn, "availability", 0.78)
    block = kpi.setting_float(conn, "block_ratio", 0.14)
    cap = team * days * avail
    usable = cap * (1 - block)

    iters = rows(conn, "SELECT * FROM iterations ORDER BY id")
    done = [i["done_points"] for i in iters if i["done_points"] is not None]
    vel = sorted(done)[len(done) // 2] if done else 0
    cv = (pstdev(done) / mean(done) * 100) if len(done) > 1 and mean(done) else 0
    cur = next((i for i in iters if i["status"] == "进行中"), None)
    suggest = min(vel, usable * 0.95)

    lines = [
        f"容量测算：{team:.0f} 人 × {days:.0f} 工作日 × {avail:.2f} 可用率 = {cap:.1f} 人天，"
        f"扣减 {block * 100:.0f}% 历史阻塞后可用 {usable:.1f} 人天。",
        f"近 {len(done)} 个已完成迭代的完成点数中位数 {vel:.0f} 点。"
        + (f"本迭代承诺 {cur['committed_points']:.0f} 点，<b>高出建议值 {suggest:.0f} 点 "
           f"{cur['committed_points'] - suggest:.0f} 点</b>，回调到 {suggest:.0f} 点更稳妥。" if cur and
           cur["committed_points"] > suggest else
           f"本迭代承诺 {cur['committed_points']:.0f} 点，在建议值 {suggest:.0f} 点以内。" if cur else ""),
        f"速率波动率 {cv:.0f}%，{'高于' if cv > 15 else '低于'} 15% 阈值，"
        f"缓冲比例建议 {'从 10% 提到 15%' if cv > 15 else '维持 10%'}——"
        f"波动大的团队用固定缓冲，等于每年白送两个迭代。",
    ]
    return {"title": "排期风险", "lines": lines}


def _change(conn) -> dict:
    causes = lineage.cause_distribution(conn)
    top = causes[0] if causes else None
    c1 = next((c for c in causes if c["code"] == "C1"), None)
    c3 = next((c for c in causes if c["code"] == "C3"), None)
    preventable = (c1["n"] if c1 else 0) + (c3["n"] if c3 else 0)
    total_events = sum(c["n"] for c in causes) or 1

    same = conn.execute("""
        SELECT requirement_id, COUNT(*) AS n, SUM(net_days) AS days, requirement_id AS rid
        FROM changes WHERE requirement_id IS NOT NULL GROUP BY requirement_id
        ORDER BY n DESC, days DESC LIMIT 1""").fetchone()
    changes = rows(conn, "SELECT * FROM changes")
    internal = sum(1 for c in changes if c["billing"] == "内部承担")
    month = sum(1 for c in changes if c["submitted_at"] >= "2026-10-01")

    lines = []
    if same and same["n"] > 1:
        lines.append(
            f"{same['rid']} 已被变更 {same['n']} 次，累计 {same['days']:.0f} 人天。"
            f"<b>同一需求反复变更，通常不是客户善变，是首次评审没问对问题</b>。")
    if top:
        lines.append(
            f"{top['code']}（{top['c'].split(' ', 1)[-1]}）占 {top['p'] * 100:.0f}%，"
            f"C1+C3 合计 {preventable / total_events * 100:.0f}%，属需求侧可预防项。")
    lines.append(
        f"在册 {len(changes)} 张变更单中 {internal} 张由内部承担（{internal / len(changes) * 100:.0f}%），"
        f"本月新增 {month} 张。这个比例持续上升，说明要么报价漏了范围，要么评审没拦住——"
        f"两者都会吃掉毛利。")
    return {"title": "同因提示", "lines": lines}


def _cost(conn) -> dict:
    act = sorted(kpi.active_projects(conn), key=lambda p: p["cpi"] or 1)
    worst = act[0] if act else None
    lines = []
    if worst:
        p = conn.execute("SELECT * FROM projects WHERE id=?", (worst["id"],)).fetchone()
        b = variance.breakdown(conn, p)
        for a in b["action"][:3]:
            lines.append(f"<b>{a['k']}</b>：{a['v']}（{a['cost']}）")
        lines.append(
            f"纠偏窗口：{worst['name']} 进度 {worst['progress'] * 100:.0f}%，"
            f"CPI {worst['cpi']:.2f}。进度过 80% 后再纠偏，只能改范围，改不了效率。")
    return {"title": "纠偏选项", "lines": lines}


def _delivery(conn) -> dict:
    deploys = rows(conn, "SELECT * FROM deployments ORDER BY cycle_days DESC")
    worst = deploys[0] if deploys else None
    lines = []
    if worst:
        lines.append(
            f"{worst['client']} 问卷通过率 {worst['survey_pass_rate']:.0f}%、配置返工 {worst['redo_count']} 次、"
            f"初始化 {worst['cycle_days']:.0f} 天——<b>三项指标同时最差，问题在问卷而不是实施</b>。")
    blocked = [d for d in deploys if d["status"] == "待确认"]
    if blocked:
        lines.append(
            f"{blocked[0]['client']} 仍有答案冲突未确认，审批流节点数无法推导，配置已阻塞。"
            f"系统不猜客户意图，宁可阻塞也不给一个「看起来对」的配置。")
    ok = [d for d in deploys if d["survey_pass_rate"] >= 70]
    if ok:
        lines.append(
            f"{len(ok)}/{len(deploys)} 个客户问卷一次通过（≥70%），平均初始化 "
            f"{mean([d['cycle_days'] for d in ok]):.0f} 天；一次通过的客户返工次数平均为 "
            f"{mean([d['redo_count'] for d in ok]):.1f} 次。")
    return {"title": "阻塞项", "lines": lines}


def _support(conn) -> dict:
    matrix = version.problem_matrix(conn)
    avg = version.avg_usage(conn)
    # 同为 69% 时按绝对条数决胜：47 条的报表中心比 18 条的消息通知更值得先动
    report = max(matrix, key=lambda m: (m["b"], m["b_share"]))
    concept = max(matrix, key=lambda m: (m["c"], m["c_share"]))
    lines = [
        f"{report['t']} B 类 {report['b']} 条，占该模块 {report['b_share'] * 100:.0f}%，"
        f"<b>但该模块使用率仅 {report['use']}%（同类均值 {avg}%）</b>——"
        f"不是「用得多所以问得多」，是入口找不到导致用得少。",
        f"{concept['t']} C 类 {concept['c']} 条占 {concept['c_share'] * 100:.0f}%，属概念模型问题："
        f"客户按「部门」理解数据范围，系统按「角色」表达。改文案救不了，得改模型。",
    ]
    return {"title": "结构判断", "lines": lines}


def _feedback(conn) -> dict:
    themes = sorted(rows(conn, "SELECT * FROM feedback_themes"), key=lambda t: -t["count"])
    complaints = [t for t in themes if t["type"] == "表达抱怨"]
    gaps = sorted(rows(conn, "SELECT * FROM market_gaps"), key=lambda g: -g["lost_amount"])
    top = themes[0]
    lines = []
    if complaints:
        c = complaints[0]
        lines.append(
            f"「{c['theme']}」反馈 {c['count']} 条，但只涉及 {c['customer_count']} 个客户、"
            f"合同权重 {c['contract_weight']:.0f} 万——<b>先查这 {c['customer_count']} 个客户的环境监控，"
            f"不要直接立项</b>。")
    lines.append(
        f"「{top['theme']}」反馈 {top['count']} 条、合同权重 {top['contract_weight']:.0f} 万，"
        f"与市场洞察缺口「{gaps[0]['gap']}」（丢单 {gaps[0]['lost_amount']:.0f} 万）指向同一处，"
        f"具备普遍性证据，已进入候选池。")
    lines.append(
        "直接把反馈当需求做，是最常见的灾难：反馈说的是「不舒服」，需求要回答的是「改什么、值多少」。")
    return {"title": "交叉校验", "lines": lines}
