/* 市场报价 Agent · 前端
   ---------------------
   两个页面：
     #/quote          报价工作台：需求池 → 推理链可视化 → 报价单 → 人工复核
     #/pricing-rules  规则权重：改权重、改参数、影子试算、版本历史

   这里只做渲染与交互，所有计算都在服务端 pricing_agent 里。
   前端不复刻定价逻辑——复刻一份就有了两套口径，迟早对不上。 */

(function () {
  'use strict';

  const $ = (s, r) => (r || document).querySelector(s);
  const num = (v, d) => (v === null || v === undefined || v === '' ? '—'
    : Number(v).toFixed(d === undefined ? 1 : d));
  const pct = (v, d) => (v === null || v === undefined ? '—' : (Number(v) * 100).toFixed(d === undefined ? 0 : d) + '%');
  const esc = (s) => String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  const api = () => window.API;
  const online = () => !!(window.API && window.API.online);

  const TONE = {
    quoted: ['teal', '已出价'], clarified: ['amber', '待补充'],
    below_margin: ['red', '毛利不足'], rejected: ['gray', '置信度不足'],
    reviewed: ['blue', '已复核']
  };
  const NODE_TONE = {
    ok: 'teal', pass: 'teal', block: 'amber', warn: 'amber',
    need_input: 'amber', retuned: 'violet', revised: 'violet', pending: 'gray'
  };
  const NODE_TEXT = {
    ok: '完成', pass: '通过', block: '拦截', warn: '注意',
    need_input: '需补充', retuned: '已调参', revised: '已重评', pending: '待人工'
  };
  const KIND_ICON = { input: '▤', agent: '◆', gate: '⛭', loop: '↺', output: '▦', human: '◉' };
  const GROUPS = ['理解', '检索', '定价', '交付'];

  const bd = (tone, txt) => `<span class="bd ${tone}">${txt}</span>`;

  const S = {
    reqId: null,
    runId: null,
    data: null,
    detail: null,
    open: {},
    ans: {},
    busy: false,
    showJson: {},
    review: { decision: 'approved', comment: '', price: '' },
    rules: null,
    draft: null,
    sim: null,
    simRun: null,
    regress: null,
    audit: null,
    err: null,
    chat: { msgs: [], llm: null, streaming: false, input: '' }
  };

  // SSE 实时渲染用到的 DOM 指针（打字机 / 抽取卡 / 阶段进度），跨事件共享
  let agentTxt = null, extractBox = null, stageBox = null;

  let loadingOverview = false;
  let loadingRules = false;

  /* ================= 数据 ================= */

  async function ensureOverview(force) {
    if (!online()) { S.err = '报价 Agent 的推理与定价在服务端完成，请先启动后端（backend/run.py）'; return false; }
    if ((S.data && !force) || loadingOverview) return false;
    loadingOverview = true;
    try {
      S.data = await api().pricingOverview();
      S.err = null;
      if (!S.reqId || !S.data.requests.some((r) => r.id === S.reqId)) {
        // 默认打开「最近一条有推理记录」的需求：空着首屏等于让人自己猜从哪看
        const withRun = S.data.requests.filter((r) => r.latest);
        S.reqId = ((withRun[0] || S.data.requests[0]) || {}).id || null;
      }
      const cur = S.data.requests.find((r) => r.id === S.reqId);
      const latest = cur && cur.latest;
      if (latest) await loadDetail(latest.run_id, true);
      else S.detail = null;
      return true;
    } catch (e) {
      S.err = e.message || String(e);
      return true;
    } finally {
      loadingOverview = false;
    }
  }

  async function loadDetail(runId, keep) {
    if (!runId) { S.detail = null; return; }
    try {
      S.detail = await api().pricingRun(runId);
      S.runId = runId;
      if (!keep) S.open = {};
    } catch (e) {
      S.err = e.message || String(e);
    }
  }

  async function ensureRules(force) {
    if (!online()) { S.err = '规则权重需要连接后端'; return false; }
    if ((S.rules && !force) || loadingRules) return false;
    loadingRules = true;
    try {
      S.rules = await api().pricingRules();
      S.draft = {};
      S.rules.rules.forEach((r) => {
        S.draft[r.code] = { code: r.code, weight: r.weight, enabled: r.enabled,
          params: JSON.parse(JSON.stringify(r.params)) };
      });
      if (!S.simRun) {
        const ov = S.data || await api().pricingOverview();
        S.data = ov;
        const withQuote = ov.requests.filter((x) => x.latest && x.latest.quote_no);
        S.simRun = withQuote.length ? withQuote[0].latest.run_id : null;
      }
      S.err = null;
      return true;
    } catch (e) {
      S.err = e.message || String(e);
      return true;
    } finally {
      loadingRules = false;
    }
  }

  function paint() {
    const v = $('#view');
    if (!v) return;
    const page = window.PRICING[location.hash.replace(/^#\/?/, '')];
    if (!page) return;
    v.innerHTML = page.render();
    if (page.mount) page.mount();
  }

  function toast(msg) {
    if (window.API && window.API.__toast) { window.API.__toast(msg); return; }
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.classList.add('show'), 10);
    setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 300); }, 3400);
  }

  /* ================= 通用展示件 ================= */

  function head(o) {
    return `<div class="page-head">
      <div class="t">
        <div class="eyebrow">${o.eyebrow || ''}${o.pill ? `<span class="pill">${o.pill}</span>` : ''}</div>
        <h2 class="title">${o.title}</h2>
        ${o.desc ? `<div class="desc">${o.desc}</div>` : ''}
      </div>
      ${o.actions ? `<div class="head-act">${o.actions}</div>` : ''}
    </div>`;
  }

  function errBox() {
    if (!S.err) return '';
    return `<div class="note red" style="margin-bottom:14px"><b>无法加载：</b>${esc(S.err)}</div>`;
  }

  function gauge(v, label, sub, text) {
    const r = 26, c = 2 * Math.PI * r;
    const color = v >= 0.85 ? 'var(--teal-fg)' : v >= 0.7 ? 'var(--blue-fg)' : v >= 0.55 ? 'var(--amber-fg)' : 'var(--red-fg)';
    return `<div class="q-gauge">
      <svg width="68" height="68" viewBox="0 0 68 68">
        <circle cx="34" cy="34" r="${r}" fill="none" stroke="#eef0f3" stroke-width="7"/>
        <circle cx="34" cy="34" r="${r}" fill="none" stroke="${color}" stroke-width="7"
          stroke-linecap="round" stroke-dasharray="${(c * v).toFixed(1)} ${c.toFixed(1)}"
          transform="rotate(-90 34 34)"/>
        <text x="34" y="38" text-anchor="middle" font-size="14" font-weight="650"
          fill="var(--ink)">${text || pct(v)}</text>
      </svg>
      <div class="q-gauge-l">${label}</div>
      ${sub ? `<div class="q-gauge-s">${sub}</div>` : ''}
    </div>`;
  }

  function barLine(label, v, weight, note) {
    const tone = v >= 0.8 ? 'teal' : v >= 0.6 ? 'blue' : v >= 0.4 ? 'amber' : 'red';
    return `<div class="q-brow">
      <span class="q-brow-l">${label}</span>
      <span class="q-brow-t"><i class="${tone}" style="width:${Math.max(2, v * 100)}%"></i></span>
      <span class="q-brow-v">${pct(v)}</span>
      ${weight ? `<span class="q-brow-w">×${pct(weight)}</span>` : ''}
      ${note ? `<span class="q-brow-n">${esc(note)}</span>` : ''}
    </div>`;
  }

  /* ================= 推理链 ================= */

  function stageMap(d) {
    const m = {};
    (d.stages || []).forEach((s) => { m[s.key] = s; });
    return m;
  }

  function detailHtml(n) {
    const d = n.detail || {};
    if (S.showJson[n.seq]) {
      return `<pre class="q-json">${esc(JSON.stringify(d, null, 2))}</pre>`;
    }
    switch (n.key) {
      case 'intake':
        return `<div class="q-quote">${esc(d.raw_text || '')}</div>`;
      case 'reason':
        return `<ol class="q-ol">${(d.plan || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ol>
          <div class="q-dim">引擎 ${esc(d.engine || '')} · 同一输入必得同一 output_hash，可复现</div>`;
      case 'decompose':
        return `<table class="data q-tb"><thead><tr><th>槽位</th><th>抽取值</th><th>来源</th><th class="num">置信</th></tr></thead>
          <tbody>${(d.slots || []).map((s) => `<tr>
            <td>${esc(s.label)}</td><td>${esc(s.display)}</td>
            <td class="q-dim">${esc(s.source)}${s.evidence ? ` · 「${esc(s.evidence)}」` : ''}</td>
            <td class="num">${pct(s.confidence)}</td></tr>`).join('')}</tbody></table>`;
      case 'keyword':
        return `<div class="chips">${(d.keywords || []).map((k) =>
          `<span class="chip q-kw" style="font-size:${10.5 + k.weight * 1.6}px">${esc(k.w)}<em>${k.type}</em></span>`).join('')}</div>`;
      case 'clarity':
        return (d.items || []).map((x) =>
          barLine(x.label, x.confidence, x.weight, `权重 ${pct(x.weight)} · 贡献 ${pct(x.score, 1)}`)).join('')
          + `<div class="q-dim">加权覆盖率 ${pct(d.score, 1)}，阈值 ${pct(d.threshold)}</div>`;
      case 'clarify':
        return (d.questions || []).map((q) => `<div class="q-q">
            <div class="q-q-t">${q.blocking ? bd('red', '阻塞') : bd('amber', '建议')} ${esc(q.q)}</div>
            <div class="q-q-w">为什么问：${esc(q.why)}</div>
            <div class="q-q-w">影响：${esc(q.impact)}</div>
          </div>`).join('') + `<div class="q-dim">补齐后回到「需求理解与拆解」重跑，不是从追问处继续</div>`;
      case 'retrieve':
        return ['history', 'template', 'customer'].map((k) => {
          const list = (k === 'history' ? d.top_hits : k === 'template' ? d.templates : []) || [];
          if (!list.length) return '';
          return `<div class="q-sub">${k === 'history' ? '历史报价库' : '行业经验库'}</div>` +
            list.map((x) => `<div class="q-hit"><span>${esc(x.title || x.name)}</span>
              <span class="q-dim">${x.days ? num(x.days, 0) + ' 天' : ''} ${x.sim ? '· 相似 ' + pct(x.sim) : ''}</span></div>`).join('');
        }).join('') + `<div class="q-dim">检索参数：${esc(JSON.stringify(d.params || {}))}</div>`;
      case 'fuse':
        return `<table class="data q-tb"><thead><tr><th>候选</th><th>命中路径</th><th class="num">融合分</th><th class="num">相似度</th></tr></thead>
          <tbody>${(d.candidates || []).map((c) => `<tr><td>${esc(c.title)}</td>
            <td class="q-dim">${c.routes.map((r) => ({ history: '历史', template: '模板', customer: '客户' }[r] || r)).join(' + ')}</td>
            <td class="num">${num(c.score, 2)}</td><td class="num">${c.sim ? pct(c.sim) : '—'}</td></tr>`).join('')}</tbody></table>`;
      case 'candidate':
        return `<table class="data q-tb"><thead><tr><th>候选方案</th><th class="num">人天</th><th class="num">覆盖</th><th class="num">单价</th><th>缺口</th></tr></thead>
          <tbody>${(d.candidates || []).map((c) => `<tr><td>${esc(c.title)}</td>
            <td class="num">${num(c.adj_days)}</td><td class="num">${pct(c.coverage)}</td>
            <td class="num">${c.rate ? num(c.rate, 2) : '—'}</td>
            <td class="q-dim">${(c.uncovered || []).join('、') || '无'}</td></tr>`).join('')}</tbody></table>`;
      case 'confidence': {
        const p = d.components || {};
        return Object.keys(p).map((k) =>
          barLine({ clarity: '需求明确度', evidence: '检索证据', familiarity: '客户熟悉度', coherence: '价格一致性' }[k] || k,
            p[k].v, p[k].w, p[k].why)).join('')
          + `<div class="q-dim">加权置信度 ${pct(d.score, 1)}，阈值 ${pct(d.threshold)}</div>`;
      }
      case 'retune':
        return `<div class="q-cmp">
          ${Object.keys(d.to || {}).filter((k) => JSON.stringify(d.from[k]) !== JSON.stringify(d.to[k]))
            .map((k) => `<div class="q-cmp-r"><span>${esc(k)}</span><b>${esc(JSON.stringify(d.from[k]))}</b><i>→</i><b>${esc(JSON.stringify(d.to[k]))}</b></div>`).join('')}
        </div><div class="q-dim">调参是确定性策略，不是随机重试：放宽行业 → 降阈值 → 扩召回</div>`;
      case 'match':
        return `<div class="q-kv"><span>主参考</span><b>${esc(d.primary.title)}</b>
          <span>相似度</span><b>${pct(d.primary.sim)}</b>
          <span>模块覆盖</span><b>${pct(d.coverage)}</b>
          <span>人天修正</span><b>${num(d.primary.days || 0, 0)} → ${num(d.primary.adj_days)}</b>
          ${d.primary.phase_factor && d.primary.phase_factor !== 1
            ? `<span>二期系数</span><b>${num(d.primary.phase_factor, 2)}（一期已交付部分不计人天）</b>` : ''}</div>`;
      case 'pricing':
        return (d.pricing.prices || []).map((p) => `<div class="q-rule">
          <div class="q-rule-h"><b>${esc(p.name)}</b><span class="mono">${p.rule}</span>
            <span class="q-rule-p">${p.price ? num(p.price) + ' 万' : ''}</span></div>
          <div class="q-rule-n">${esc(p.note)}</div></div>`).join('');
      case 'fusion':
        return `<table class="data q-tb"><thead><tr><th>规则</th><th class="num">独立出价</th><th class="num">权重</th><th class="num">贡献</th></tr></thead>
          <tbody>${(d.parts || []).map((p) => `<tr><td class="mono">${p.rule}</td>
            <td class="num">${num(p.price)} 万</td><td class="num">${pct(p.norm_weight)}</td>
            <td class="num">${num(p.contribution)} 万</td></tr>`).join('')}</tbody></table>
          <div class="q-dim">${esc(d.note)}${d.diverged ? '（分歧 > 20%，取中位数并标记人工判断）' : ''}</div>`;
      case 'margin': {
        const m = d;
        return `<div class="q-kv">
            <span>报价</span><b>${num(m.price)} 万</b>
            <span>成本</span><b>${num(m.cost)} 万</b>
            <span>毛利率</span><b class="${m.ok ? '' : 'warn-txt'}">${pct(m.margin, 1)}</b>
            <span>下限</span><b>${pct(m.floor)}</b></div>
          ${m.needed_price ? `<div class="q-dim">达到下限需报价 ${num(m.needed_price)} 万（+${pct(m.raise_pct, 1)}），授权上限 ${pct(0.06)}</div>` : ''}
          ${(m.options || []).map((o) => `<div class="q-opt-line"><span>${esc(o.k)}</span>${esc(o.v)}</div>`).join('')}`;
      }
      default:
        return '';
    }
  }

  function llmBadge(n) {
    // 这一步是 LLM 参与还是纯代码计算：以本运行关联的 llm_calls 为准，可溯源
    const calls = (S.detail && S.detail.llm_calls) || [];
    const hit = calls.find((c) => c.node === n.key);
    if (hit) {
      return `<span class="q-src ${hit.model === 'offline' ? 'off' : 'llm'}"
        title="${esc(hit.model)} · ${esc(hit.purpose)}">${hit.model === 'offline' ? '模板' : 'LLM'}</span>`;
    }
    return `<span class="q-src code" title="确定性代码计算，无模型参与">代码</span>`;
  }

  function nodeCard(n, sm) {
    const st = sm[n.key] || {};
    const tone = NODE_TONE[n.status] || 'gray';
    const open = !!S.open[n.seq];
    const body = open ? detailHtml(n) : '';
    const hasBody = ['intake', 'reason', 'decompose', 'keyword', 'clarity', 'clarify', 'retrieve',
      'fuse', 'candidate', 'confidence', 'retune', 'match', 'pricing', 'fusion', 'margin', 'revise']
      .indexOf(n.key) >= 0;
    return `<div class="q-node ${n.loop > 0 ? 'looped' : ''} ${n.status === 'block' ? 'blocked' : ''}">
      <div class="q-node-l"><span class="q-dot ${tone}">${KIND_ICON[st.kind] || '·'}</span></div>
      <div class="q-node-b">
        <div class="q-node-h">
          <span class="q-seq">S${String(n.seq).padStart(2, '0')}</span>
          <b>${esc(n.name)}</b>
          ${bd(tone, NODE_TEXT[n.status] || n.status)}
          ${llmBadge(n)}
          ${n.loop > 0 && (n.kind === 'loop' || n.key === 'retrieve')
            ? `<span class="q-loop">↺ 第 ${n.loop + 1} 轮</span>` : ''}
          <span class="q-ms">${num(n.ms, 1)} ms</span>
          ${hasBody ? `<button class="btn xs q-exp" data-qnode="${n.seq}">${open ? '收起' : '看依据'}</button>` : ''}
        </div>
        <div class="q-node-s">${esc(n.summary)}</div>
        ${n.facts && n.facts.length ? `<div class="q-facts">${n.facts.map((f) =>
          `<div class="q-fact"><span>${esc(f.k)}：</span><b class="${f.tone === 'bad' ? 'warn-txt' : ''}">${esc(f.v)}</b></div>`).join('')}</div>` : ''}
        ${body ? `<div class="q-detail">${body}</div>` : ''}
        ${open ? `<div class="q-json-t"><button class="btn xs" data-qjson="${n.seq}">${S.showJson[n.seq] ? '看结构化视图' : '看原始 JSON'}</button></div>` : ''}
      </div>
    </div>`;
  }

  function traceHtml(d) {
    const sm = stageMap(d);
    let html = '';
    GROUPS.forEach((g) => {
      const nodes = d.trace.filter((n) => (sm[n.key] || {}).group === g);
      if (!nodes.length) return;
      html += `<div class="q-grp"><div class="q-grp-h"><span>${g}层</span>
        <i>${nodes.length} 个节点</i></div>
        <div class="q-nodes">${nodes.map((n) => nodeCard(n, sm)).join('')}</div></div>`;
    });
    return html;
  }

  /* ================= 报价工作台 ================= */

  const QuotePage = {
    crumb: 'AI 决策专题 / 市场报价 Agent',

    render() {
      if (!online()) {
        return `<div class="page">${head({ eyebrow: 'AI 决策专题', pill: '⑤', title: '市场报价 Agent',
          desc: '客户需求 → 推理 → 报价单，全链路留痕' })}${errBox()}</div>`;
      }
      if (S.err && !S.data) {
        return `<div class="page">${head({ eyebrow: 'AI 决策专题', pill: '⑤', title: '市场报价 Agent' })}${errBox()}</div>`;
      }
      if (!S.data) return `<div class="page"><div class="card q-loading">正在加载需求池与推理记录…</div></div>`;

      const req = S.data.requests.find((r) => r.id === S.reqId) || S.data.requests[0] || null;
      const st = S.data.stats;
      const det = S.detail;

      return `<div class="page">
        ${head({
          eyebrow: 'AI 决策专题', pill: '⑤',
          title: '市场报价 Agent',
          desc: '客户需求进来后，拆解 → 追问 → 三路检索 → 置信度校验 → 三规则定价 → 毛利校验 → 报价单。'
            + ' 每一步都能点开看依据；AI 只出建议价，签字的是人。',
          actions: `<span class="q-ver mono">引擎 ${esc(S.data.engine)} · 规则 ${esc(S.data.version)}</span>
            <a class="btn sm" href="#/pricing-rules">规则权重 ⚖</a>`
        })}
        ${errBox()}

        <div class="grid g4" style="margin-bottom:14px">
          <div class="kpi"><div class="lab">需求总数</div><div class="val">${st.requests}</div>
            <div class="foot">其中 ${st.clarified} 条待补充参数</div></div>
          <div class="kpi"><div class="lab">已出价</div><div class="val">${st.quoted}<small>条</small></div>
            <div class="foot">${st.reviewed} 条已人工复核</div></div>
          <div class="kpi"><div class="lab">毛利不足转特批</div><div class="val ${st.below_margin ? 'warn-txt' : ''}">${st.below_margin}<small>条</small></div>
            <div class="foot">低于 15% 下限不放行</div></div>
          <div class="kpi"><div class="lab">平均置信度</div><div class="val">${pct(st.avg_confidence)}</div>
            <div class="foot">${st.retuned} 条经过调参</div></div>
        </div>

        <div class="grid g-1-2" style="margin-bottom:14px">
          <div class="card pad0">
            <div class="card-h"><h3>需求池</h3><span class="sub">${S.data.requests.length} 条</span></div>
            <div class="q-pool">
              ${S.data.requests.map((r) => {
                const l = r.latest;
                const tone = l ? (TONE[l.status] || ['gray', l.status]) : ['gray', '未推理'];
                return `<button class="q-pool-i ${r.id === (req || {}).id ? 'on' : ''}" data-qreq="${r.id}">
                  <span class="q-pool-h">${esc(r.client)}<em>${esc(r.industry || '—')}</em>${bd(tone[0], tone[1])}</span>
                  <span class="q-pool-t">${esc(r.raw_text.slice(0, 46))}…</span>
                  <span class="q-pool-f">${esc(r.id)} · 预算 ${esc(r.budget_hint || '未给')}
                    ${l && l.price ? ` · 报价 ${num(l.price)} 万` : ''}</span>
                </button>`;
              }).join('')}
            </div>
          </div>

          <div class="card">
            ${req ? reqCard(req, det) : '<div class="q-dim">暂无需求</div>'}
          ${req && !det && !req.latest ? `<div class="q-empty">
            <b>这条需求还没有跑过推理</b>
            <span>点「开始推理」，Agent 会先判断需求是否足够明确：不够就先反问，够了才进入检索与定价。</span>
          </div>` : ''}
          ${req ? chatPanel(req) : ''}
          </div>
        </div>

        ${det ? verdictCard(det) : ''}
        ${det ? reviewQuestions(det) : ''}
        ${det ? `<div class="card" style="margin-bottom:14px">
            <div class="card-h"><h3>推理过程</h3>
              <span class="sub">${det.trace.length} 个节点 · 悬停看摘要，点「看依据」展开输入输出</span>
              <div class="spacer"></div>
              <button class="btn sm" data-qact="rerun">重新推理</button></div>
            ${traceHtml(det)}
          </div>` : ''}
        ${det ? `<div class="grid g3" style="margin-bottom:14px">${candsCard(det)}${ruleCard(det)}${mergeCard(det)}</div>` : ''}
        ${det && det.quote ? quoteCard(det) : ''}
        ${det && det.reviews && det.reviews.length ? reviewHistory(det) : ''}
      </div>`;
    },

    mount() {
      ensureOverview().then((dirty) => {
        if (dirty) paint();
        loadChat();
      });
    }
  };

  function reqCard(req, det) {
    const latest = req.latest;
    const ans = (det && det.answers) || {};
    const slots = det ? (det.trace.find((n) => n.key === 'decompose') || {}).detail : null;
    return `<div class="card-h"><h3>${esc(req.client)} · ${esc(req.industry || '')}</h3>
        ${latest ? bd((TONE[latest.status] || ['gray', ''])[0], (TONE[latest.status] || ['', latest.status])[1]) : bd('gray', '未推理')}
        <div class="spacer"></div>
        <span class="hint">${esc(req.id)} · ${esc(req.channel)}</span></div>
      <div class="q-quote">${esc(req.raw_text)}</div>
      <div class="q-meta">
        <span>预算 <b>${esc(req.budget_hint || '未提供')}</b></span>
        <span>期望上线 <b>${esc(req.deadline || '未提供')}</b></span>
        <span>提交 <b>${esc(req.created_at)}</b></span>
        ${req.run_count ? `<span>已推理 <b>${req.run_count}</b> 次</span>` : ''}
      </div>
      ${slots ? `<div class="q-slots">${(slots.slots || []).map((s) => `<span class="q-slot ${s.confidence >= 1 ? 'ok' : s.confidence > 0 ? 'part' : 'miss'}">
          ${esc(s.label)}<b>${esc(s.display)}</b></span>`).join('')}</div>` : ''}
      <div class="q-acts">
        <button class="btn sm primary" data-qact="rerun">${req.run_count ? '重新推理' : '开始推理'}</button>
        ${Object.keys(ans).length ? `<span class="hint">本次含 ${Object.keys(ans).length} 项追问补答</span>` : ''}
        ${latest ? `<span class="hint">最近：${esc(latest.at)} · ${esc(latest.by)}</span>` : ''}
      </div>`;
  }

  function verdictCard(d) {
    const status = d.run.status;
    const [tone, label] = TONE[status] || ['gray', status];
    const q = d.quote;
    const retuneNode = d.trace.filter((n) => n.key === 'retune');
    return `<div class="card q-verdict ${tone}" style="margin-bottom:14px">
      <div class="q-v-left">
        <div class="q-v-st">${bd(tone, label)}</div>
        <div class="q-v-num">${q ? `<b>${num(q.price)}</b><small>万元</small>` : '<b>不出价</b>'}</div>
        <div class="q-v-sub">${q ? `${num(q.days)} 人天 · ${num(q.rate, 2)} 万/人天 · 毛利 ${pct(q.margin, 1)}`
          : '需求参数不足，先补齐再定价'}</div>
      </div>
      ${status === 'clarified'
        ? gauge(0, '置信度', '未进入评估', '未评估')
        : gauge(d.run.confidence, '置信度', `阈值 70%${d.run.retune_rounds > 0 ? ` · 调参 ${d.run.retune_rounds} 轮` : ''}`)}
      ${gauge(d.run.clarity, '明确度', '阈值 60%')}
      <div class="q-v-right">
        <div class="q-v-row"><span>运行编号</span><b class="mono">${esc(d.run.run_id)}</b></div>
        <div class="q-v-row"><span>报价单号</span><b class="mono">${esc(q ? q.no : '—')}</b></div>
        <div class="q-v-row"><span>规则版本</span><b class="mono">${esc(d.run.rule_version)}</b></div>
        <div class="q-v-row"><span>输出指纹</span><b class="mono">${esc(d.run.output_hash || '—')}</b></div>
        ${retuneNode.length ? `<div class="q-v-row"><span>调参回环</span><b>${retuneNode.length} 次</b></div>` : ''}
      </div>
    </div>`;
  }

  /* ---- 对话澄清（SSE 流式） ---- */

  const SLOT_LABEL = { scale: '使用规模', mode: '交付模式', deadline: '交期', budget: '预算',
    industry: '所属行业', integration: '集成点', modules: '功能范围' };

  function llmBanner(llm) {
    if (!llm) return `<span class="chat-llm pending">模型检测中…</span>`;
    if (llm.online) return `<span class="chat-llm ok">在线 · ${esc(llm.model)}</span>`;
    return `<span class="chat-llm off">离线 · 模板话术降级</span>`;
  }

  function updateLlmUi() {
    // loadChat 异步拿到模型状态后，就地刷新横幅与离线提示（不整页重绘）
    const st = S.chat.llm;
    const b = document.querySelector('.chat-llm');
    if (b) {
      if (!st) { b.className = 'chat-llm pending'; b.textContent = '模型检测中…'; }
      else if (st.online) { b.className = 'chat-llm ok'; b.textContent = '在线 · ' + st.model; }
      else { b.className = 'chat-llm off'; b.textContent = '离线 · 模板话术降级'; }
    }
    const note = document.querySelector('.chat-note');
    if (note) {
      if (st && !st.online) {
        note.style.display = '';
        note.innerHTML = '未配置 <code>SDLC_LLM_KEY</code>，当前走离线模板话术 + 正则抽取；接入 DeepSeek 后自动升级为 LLM 追问。';
      } else {
        note.style.display = 'none';
      }
    }
  }

  function chatPanel(req) {
    const c = S.chat;
    return `<div class="chat">
      <div class="chat-h"><h3>对话澄清</h3>${llmBanner(c.llm)}
        <span class="sub">自由回答 → 槽位抽取 → 自动重推</span></div>
      <div class="chat-note" ${c.llm && !c.llm.online ? '' : 'style="display:none"'}>未配置 <code>SDLC_LLM_KEY</code>，当前走离线模板话术 + 正则抽取；接入 DeepSeek 后自动升级为 LLM 追问。</div>
      <div class="chat-scroll" id="chatScroll">${c.msgs.map(chatMsg).join('')}</div>
      <div class="chat-in">
        <input id="chatInput" placeholder="直接说需求或回答问题，例如：大概八百个律师用，预算 60 万以内，明年一季度上线"
          ${c.streaming ? 'disabled' : ''} value="${esc(c.input || '')}">
        <button class="btn sm primary" data-qchat="send" ${c.streaming ? 'disabled' : ''}>${c.streaming ? '推理中…' : '发送'}</button>
      </div>
    </div>`;
  }

  function chatMsg(m) {
    const isUser = m.role === 'user';
    return `<div class="chat-msg ${isUser ? 'user' : 'agent'}">
      <div class="chat-avatar">${isUser ? '你' : 'AI'}</div>
      <div class="chat-bub">${m.html || esc(m.text || '')}</div></div>`;
  }

  async function loadChat() {
    if (!online() || !S.reqId) return;
    try {
      const [h, st] = await Promise.all([api().dialogueHistory(S.reqId), api().llmStatus()]);
      S.chat.llm = st;
      S.chat.msgs = (h.items || []).map((it) => {
        let extra = '';
        if (it.role === 'agent' && it.extracted) {
          const ex = it.extracted.extracted || {};
          extra = `<div class="chat-ex-box">${Object.keys(ex).map((k) => {
            const v = ex[k];
            return `<div class="chat-ex"><span class="bd ${v.confidence >= 0.8 ? 'teal' : 'amber'}">抽取</span>
              <b>${esc(SLOT_LABEL[k] || k)}</b> = <b>${esc(v.value)}</b> <span class="q-dim">${pct(v.confidence)}</span></div>`;
          }).join('')}</div>`;
        }
        return { role: it.role, html: esc(it.content) + extra };
      });
      if (!S.chat.msgs.length) {
        S.chat.msgs = [{ role: 'agent', text: '把需求直接讲给我听。参数不全我会停下来追问，够了就进检索与定价、直接出价。' }];
      }
      const sc = $('#chatScroll');
      if (sc) { sc.innerHTML = S.chat.msgs.map(chatMsg).join(''); scrollChat(); }
      updateLlmUi();
    } catch (e) { /* 对话拉不到不阻塞主流程 */ }
  }

  function scrollChat() {
    const sc = $('#chatScroll');
    if (sc) sc.scrollTop = sc.scrollHeight;
  }

  function resetChatPtrs() { agentTxt = null; extractBox = null; stageBox = null; }

  function appendDelta(txt) {
    if (!agentTxt) {
      const el = document.createElement('div');
      el.className = 'chat-msg agent';
      el.innerHTML = '<div class="chat-avatar">AI</div><div class="chat-bub"><div class="chat-txt"></div></div>';
      const sc = $('#chatScroll');
      if (!sc) return;
      sc.appendChild(el);
      agentTxt = el.querySelector('.chat-txt');
    }
    agentTxt.textContent += txt;
    scrollChat();
  }

  function appendBlock(html) {
    agentTxt = null; extractBox = null;
    const el = document.createElement('div');
    el.className = 'chat-msg agent';
    el.innerHTML = `<div class="chat-avatar">AI</div><div class="chat-bub">${html}</div>`;
    const sc = $('#chatScroll');
    if (sc) { sc.appendChild(el); scrollChat(); }
    return el;
  }

  function questionCard(q) {
    const opts = (q.options || []).map((o) =>
      `<button class="chip q-opt" data-qchat="opt" data-qopt="${esc(o)}">${esc(o)}</button>`).join('');
    return `<div class="chat-q">
      <div class="chat-q-t">${q.blocking ? bd('red', '阻塞') : bd('amber', '建议')} <b>${esc(q.label)}</b> ${esc(q.q)}</div>
      <div class="chat-q-w">${esc(q.why)}</div>
      ${opts ? `<div class="chat-q-o">${opts}</div>` : ''}</div>`;
  }

  function appendExtract(slot, label, value, confidence, evidence) {
    if (!extractBox) {
      const el = document.createElement('div');
      el.className = 'chat-msg agent';
      el.innerHTML = '<div class="chat-avatar">AI</div><div class="chat-bub"><div class="chat-ex-box"></div></div>';
      const sc = $('#chatScroll');
      if (!sc) return;
      sc.appendChild(el);
      extractBox = el.querySelector('.chat-ex-box');
      agentTxt = null;
    }
    const row = document.createElement('div');
    row.className = 'chat-ex';
    row.innerHTML = `<span class="bd ${confidence >= 0.8 ? 'teal' : 'amber'}">抽取</span>
      <b>${esc(label)}</b> = <b>${esc(value)}</b> <span class="q-dim">${pct(confidence)} ·「${esc(evidence || '')}」</span>`;
    extractBox.appendChild(row);
    scrollChat();
  }

  function startStages() {
    const el = appendBlock('<div class="chat-stage-box"></div>');
    stageBox = el ? el.querySelector('.chat-stage-box') : null;
  }

  function appendStage(n) {
    if (!stageBox) return;
    const tone = NODE_TONE[n.status] || 'gray';
    const row = document.createElement('div');
    row.className = 'chat-stage';
    row.innerHTML = `<span class="q-dot ${tone}"></span><span class="chat-stage-n">${esc(n.name)}</span>
      ${bd(tone, NODE_TEXT[n.status] || n.status)}
      ${n.loop > 0 ? `<span class="q-loop">↺ ${n.loop + 1}</span>` : ''}`;
    stageBox.appendChild(row);
    scrollChat();
  }

  function appendQuote(q) {
    const [tone, label] = TONE[q.status || 'quoted'] || ['teal', '已出价'];
    appendBlock(`<div class="chat-quote">
      <div class="chat-quote-h">${bd(tone, label)}${q.no ? ` <span class="mono">${esc(q.no)}</span>` : ''}</div>
      <div class="chat-quote-p">${q.price ? `<b>${num(q.price)}</b><small>万元</small>` : '不出价'}
        ${q.margin !== undefined && q.margin !== null ? ` · 毛利 ${pct(q.margin, 1)}` : ''}
        ${q.confidence !== undefined && q.confidence !== null ? ` · 置信度 ${pct(q.confidence)}` : ''}</div>
    </div>`);
  }

  function appendError(msg) {
    appendBlock(`<div class="note red"><b>出错：</b>${esc(msg)}</div>`);
  }

  async function sendChat() {
    const inp = $('#chatInput');
    const text = (inp ? inp.value : '').trim();
    if (!text || S.chat.streaming || !S.reqId) return;
    // 手动 append 用户气泡，不整页重绘——否则 mount→loadChat 会覆盖实时流
    const uel = document.createElement('div');
    uel.className = 'chat-msg user';
    uel.innerHTML = `<div class="chat-avatar">你</div><div class="chat-bub">${esc(text)}</div>`;
    const sc = $('#chatScroll');
    if (sc) { sc.appendChild(uel); scrollChat(); }
    S.chat.msgs.push({ role: 'user', html: esc(text) });
    S.chat.streaming = true;
    if (inp) { inp.value = ''; inp.disabled = true; }
    const btn = document.querySelector('[data-qchat="send"]');
    if (btn) { btn.disabled = true; btn.textContent = '推理中…'; }
    resetChatPtrs();
    try {
      await api().dialogueTurn(S.reqId, text, (ev, d) => {
        if (ev === 'meta') { if (d.llm) S.chat.llm = d.llm; return; }
        if (ev === 'delta') { appendDelta(d.text || ''); return; }
        if (ev === 'extract') { appendExtract(d.slot, d.label, d.value, d.confidence, d.evidence); return; }
        if (ev === 'question') { appendBlock(questionCard(d)); return; }
        if (ev === 'run_start') { startStages(); return; }
        if (ev === 'stage') { appendStage(d); return; }
        if (ev === 'quote') { appendQuote(d); return; }
        if (ev === 'error') { appendError(d.message); return; }
      });
    } catch (e) {
      appendError(e.message || String(e));
    } finally {
      S.chat.streaming = false;
      resetChatPtrs();
      try {
        S.data = await api().pricingOverview();
        const cur = S.data.requests.find((r) => r.id === S.reqId);
        if (cur && cur.latest) await loadDetail(cur.latest.run_id);
      } catch (e2) { /* 忽略 */ }
      S.chat.msgs = [];
      await loadChat();
      paint();
    }
  }

  function quickOpt(opt) {
    const inp = $('#chatInput');
    if (!inp) return;
    inp.value = opt;
    sendChat();
  }

  /* ---- 追问与补答 ---- */

  const ANSWER_HINT = {
    scale: '800', mode: '标准配置', deadline: '2027-Q2', budget: '70',
    integration: 'ERP', industry: '互联网'
  };

  function reviewQuestions(d) {
    const node = d.trace.find((n) => n.key === 'clarify');
    if (!node) return '';
    const qs = (node.detail || {}).questions || [];
    if (!qs.length) return '';
    return `<div class="card q-clarify" style="margin-bottom:14px">
      <div class="card-h"><h3>需要补充 ${qs.length} 个关键参数</h3>
        <span class="sub">明确度 ${pct(d.run.clarity)} 未达阈值，Agent 主动停下反问——而不是先报个价再说</span></div>
      ${qs.map((q) => `<div class="q-qa">
        <div class="q-qa-q">${q.blocking ? bd('red', '阻塞') : bd('amber', '建议')}
          <b>${esc(q.label)}</b> ${esc(q.q)}</div>
        <div class="q-qa-w">${esc(q.why)}　<span class="q-dim">${esc(q.impact)}</span></div>
        <div class="q-qa-in">
          <input class="q-in" data-qans="${q.slot}" placeholder="当前：${esc(q.current)}｜不答将按「${esc(q.assume)}」"
            value="${esc(S.ans[q.slot] !== undefined ? S.ans[q.slot] : (ANSWER_HINT[q.slot] || ''))}">
        </div>
      </div>`).join('')}
      <div class="q-acts">
        <button class="btn sm primary" data-qact="answer">补答并重推</button>
        <button class="btn sm" data-qact="answer-default">用建议值补答</button>
        <span class="hint">补答后会从「需求理解与拆解」重跑，推理链上会看到第 2 轮</span>
      </div>
    </div>`;
  }

  /* ---- 候选 / 定价 / 融合 ---- */

  function candsCard(d) {
    const node = d.trace.filter((n) => n.key === 'candidate').pop();
    const cs = node ? node.detail.candidates || [] : [];
    return `<div class="card"><div class="card-h"><h3>候选方案</h3><span class="sub">融合后 Top${cs.length}</span></div>
      ${cs.map((c, i) => `<div class="q-cand ${i === 0 ? 'top' : ''}">
        <div class="q-cand-h"><b>${esc(c.title)}</b>${i === 0 ? bd('teal', '主参考') : ''}</div>
        <div class="q-cand-m"><span>${num(c.adj_days)} 人天</span><span>覆盖 ${pct(c.coverage)}</span>
          <span>相似 ${c.sim ? pct(c.sim) : '—'}</span></div>
        <div class="q-cand-r">${c.routes.map((r) => ({ history: '历史库', template: '模板库', customer: '客户库' }[r] || r)).join(' · ')}</div>
      </div>`).join('')}
      <div class="q-dim">候选来自三路检索的加权 RRF 融合；同一候选被多路命中会加分</div></div>`;
  }

  function ruleCard(d) {
    const node = d.trace.find((n) => n.key === 'pricing');
    const ps = node ? (node.detail.pricing || {}).prices || [] : [];
    const max = Math.max.apply(null, ps.map((p) => p.price || 0)) || 1;
    return `<div class="card"><div class="card-h"><h3>三规则独立出价</h3><span class="sub">互相不干扰</span></div>
      ${ps.filter((p) => p.price).map((p) => `<div class="q-pr">
        <div class="q-pr-h"><b>${esc(p.name)}</b><span>${num(p.price)} 万</span></div>
        <div class="q-pr-b"><i style="width:${Math.max(4, p.price / max * 100)}%"></i></div>
        <div class="q-pr-n">${esc(p.note)}</div></div>`).join('')}
      <div class="q-dim">分开出价是为了看见分歧：市场说 82 万能打赢，成本说至少要 96 万，这才是需要人拍板的地方</div></div>`;
  }

  function mergeCard(d) {
    const node = d.trace.find((n) => n.key === 'fusion');
    const f = node ? node.detail : null;
    const m = d.trace.find((n) => n.key === 'margin');
    if (!f) return '';
    return `<div class="card"><div class="card-h"><h3>融合与毛利校验</h3>
        ${f.diverged ? bd('amber', `分歧 ${pct(f.spread)}`) : bd('teal', '口径一致')}</div>
      ${f.parts.map((p) => `<div class="q-fr"><span class="mono">${p.rule}</span>
        <span class="q-fr-b"><i style="width:${p.norm_weight * 100}%"></i></span>
        <b>${num(p.price)} 万 × ${pct(p.norm_weight)}</b>
        ${p.cut && p.cut < 1 ? `<em class="q-cut" title="可计价样本不足，该规则权重已折算">样本折算 ×${num(p.cut, 2)}</em>` : ''}</div>`).join('')}
      <div class="q-fr-total"><span>融合价</span><b>${num(f.price)} 万</b></div>
      ${m ? `<div class="q-margin ${m.detail.ok ? 'ok' : 'bad'}">
        <div class="q-margin-h">毛利率 ${pct(m.detail.margin, 1)}
          <em>下限 ${pct(m.detail.floor)}</em></div>
        <div class="q-margin-b"><i style="width:${Math.max(2, Math.min(100, m.detail.margin * 300))}%"></i>
          <u style="left:${m.detail.floor * 300}%"></u></div>
        <div class="q-margin-n">${esc(m.detail.note)}</div></div>` : ''}
      <div class="q-dim">${esc(f.note)}</div></div>`;
  }

  /* ---- 报价单 ---- */

  function quoteCard(d) {
    const q = d.quote;
    return `<div class="card" style="margin-bottom:14px">
      <div class="card-h"><h3>报价单 ${esc(q.no)}</h3>
        <span class="sub">${esc(q.client)} · 有效期至 ${esc(q.valid_until)} · 规则 ${esc(q.rule_version)}</span>
        <div class="spacer"></div>
        ${q.review_required ? bd('amber', '需人工复核') : bd('teal', '可免复核')}</div>

      <table class="data">
        <thead><tr><th>模块 / 工作项</th><th class="num">人天</th><th class="num">单价（万）</th><th class="num">小计（万）</th><th>来源</th></tr></thead>
        <tbody>${q.lines.map((l) => `<tr><td>${esc(l.module)}</td><td class="num">${num(l.days)}</td>
          <td class="num">${num(l.rate, 2)}</td><td class="num">${num(l.amount)}</td>
          <td class="q-dim">${esc(l.src)}</td></tr>`).join('')}
          <tr class="q-sum"><td><b>合计</b></td><td class="num"><b>${num(q.days)}</b></td>
            <td class="num">${num(q.rate, 2)}</td><td class="num"><b>${num(q.price)}</b></td><td></td></tr>
        </tbody>
      </table>

      <div class="grid g3" style="margin-top:16px">
        <div><div class="q-h4">三档报价</div>
          ${q.tiers.map((t, i) => `<div class="q-tier ${i === 0 ? 'main' : ''}">
            <span>${esc(t.k)}</span><b>${num(t.price)} 万</b><em>${esc(t.cond)}</em></div>`).join('')}</div>
        <div><div class="q-h4">前置假设</div>
          <ul class="q-ul">${q.assumptions.map((a) => `<li>${esc(a)}</li>`).join('')}</ul></div>
        <div><div class="q-h4">不含范围</div>
          <ul class="q-ul">${q.exclusions.map((a) => `<li>${esc(a)}</li>`).join('')}</ul></div>
      </div>

      ${q.risks.length ? `<div class="note amber" style="margin-top:14px"><b>风险提示：</b>
        <ul class="q-ul" style="margin-top:4px">${q.risks.map((r) => `<li>${esc(r)}</li>`).join('')}</ul></div>` : ''}

      ${d.run.status === 'reviewed' ? '' : reviewBox(d)}
    </div>`;
  }

  function reviewBox(d) {
    const dec = S.review.decision;
    return `<div class="q-review">
      <div class="q-review-h"><b>人工复核</b>
        <span class="hint">AI 给的是建议价；放行、调价、驳回都记在审计里</span></div>
      <div class="q-review-row">
        <button class="opt ${dec === 'approved' ? 'on' : ''}" data-qrev="approved">通过</button>
        <button class="opt ${dec === 'adjusted' ? 'on' : ''}" data-qrev="adjusted">调价通过</button>
        <button class="opt ${dec === 'rejected' ? 'on' : ''}" data-qrev="rejected">驳回</button>
        ${dec === 'adjusted' ? `<input class="q-in q-in-s" id="qFinalPrice" placeholder="最终价（万）"
          value="${esc(S.review.price || d.quote.price)}">` : ''}
      </div>
      <textarea class="q-ta" id="qComment" placeholder="复核意见（必填）：为什么同意 / 为什么调价 / 为什么驳回">${esc(S.review.comment)}</textarea>
      <div class="q-acts"><button class="btn sm primary" data-qact="review" ${S.busy ? 'disabled' : ''}>
        ${S.busy ? '提交中…' : '提交复核'}</button>
        <span class="hint">提交后该运行状态锁定为「已复核」，不可重复提交</span></div>
    </div>`;
  }

  function reviewHistory(d) {
    return `<div class="card"><div class="card-h"><h3>复核记录</h3></div>
      ${d.reviews.map((r) => `<div class="q-rev">
        <span class="q-rev-d">${bd(r.decision === 'approved' ? 'teal' : r.decision === 'adjusted' ? 'blue' : 'red',
          { approved: '通过', adjusted: '调价通过', rejected: '驳回' }[r.decision] || r.decision)}</span>
        <span class="q-rev-t">${esc(r.comment)}</span>
        <span class="q-rev-m">${esc(r.reviewer)} · ${esc(r.at)}${r.final_price ? ' · 最终 ' + num(r.final_price) + ' 万' : ''}</span>
      </div>`).join('')}</div>`;
  }

  /* ================= 规则权重页 ================= */

  const RULES_META = {
    'R-MKT-01': { fixed: false },
    'R-CUS-01': {}, 'R-COST-01': {}, 'R-RTV-01': {}, 'R-RTV-02': {},
    'R-THR-01': {}, 'R-THR-02': {}, 'R-THR-03': { lock: true },
    'R-THR-04': {}, 'R-THR-05': {}
  };
  const PARAM_LABEL = {
    percentile: '对标分位点', budget_guard: '预算压制开关', loss_penalty: '丢单惩罚系数',
    min_samples: '最少样本数', price_band: '价格带上下限',
    levels: '客户等级折扣', on_time_bonus: '按时付款减免', change_penalty: '变更率加价',
    day_cost: '人天成本（万）', markup: '加成率', rework_reserve: '返工储备',
    phase2_factor: '二期增量系数', compliance_factor: '合规成本系数',
    history: '历史库权重', template: '模板库权重', customer: '客户库权重', rrf_k: 'RRF 常数 k',
    topk: '召回条数', sim_min: '相似度阈值', max_rounds: '最大调参轮次', relax_step: '放宽步长',
    clarity_min: '明确度阈值', slots: '槽位权重', confidence_min: '置信度阈值',
    components: '置信度分项权重', margin_min: '毛利率下限', auto_raise_max: '自动提价上限',
    top_n: '候选方案数', min_n: '最少候选数', valid_days: '报价有效期（天）'
  };

  // 嵌套参数的键名也是英文，界面上必须翻成业务语言
  const NESTED_LABEL = {
    modules: '功能范围', industry: '所属行业', scale: '使用规模', mode: '交付模式',
    integration: '集成点', deadline: '交期要求', budget: '客户预算',
    clarity: '明确度', evidence: '检索证据', familiarity: '客户熟悉度', coherence: '价格一致性',
    战略: '战略客户', 重点: '重点客户', 普通: '普通客户', 新客: '新客户'
  };

  const RulesPage = {
    crumb: '基础 / 报价规则权重',

    render() {
      if (!online()) {
        return `<div class="page">${head({ eyebrow: '基础', pill: '⚖', title: '报价规则权重' })}${errBox()}</div>`;
      }
      if (!S.rules) return `<div class="page"><div class="card q-loading">正在加载规则与版本…</div></div>`;

      const groups = S.rules.groups;
      const main = S.rules.rules.filter((r) => ['market', 'customer', 'cost'].indexOf(r.group) >= 0);
      const sum = main.filter((r) => draftOf(r.code).enabled)
        .reduce((a, r) => a + Number(draftOf(r.code).weight), 0) || 1;

      return `<div class="page">
        ${head({
          eyebrow: '基础', pill: '⚖', title: '报价规则权重',
          desc: '这里改的是「怎么定价」，不是「这一次报多少」。保存后生成新版本，之后每次推理都用新版本；'
            + '已经出过的报价单锁定在它生成时的版本，不会被追溯改写。',
          actions: `<span class="q-ver mono">当前 ${esc(S.rules.version)}</span>
            <a class="btn sm" href="#/quote">← 回报价工作台</a>
            <button class="btn sm" data-rul="simulate">影子试算</button>
            <button class="btn sm" data-rul="regress">改前回归</button>
            <button class="btn sm" data-rul="audit">LLM 审计</button>
            <button class="btn sm primary" data-rul="save">保存为新版本</button>`
        })}
        ${errBox()}

        <div class="grid g-3-2">
          <div>
            <div class="card" style="margin-bottom:14px">
              <div class="card-h"><h3>定价三规则权重</h3>
                <span class="sub">融合时按权重归一化，合计不必等于 100%</span></div>
              ${main.map((r) => {
                const d = draftOf(r.code);
                const norm = d.enabled ? Number(d.weight) / sum : 0;
                return `<div class="r-row ${d.enabled ? '' : 'off'}">
                  <div class="r-lab"><b>${esc(r.name)}</b><span class="mono">${r.code}</span></div>
                  <input class="r-slider" type="range" min="0" max="1" step="0.01"
                    value="${d.weight}" data-rw="${r.code}" ${r.editable ? '' : 'disabled'}>
                  <div class="r-val"><b data-rnorm="${r.code}">${pct(norm)}</b>
                    <input class="r-num" type="number" min="0" max="1" step="0.01"
                      value="${d.weight}" data-rw="${r.code}" ${r.editable ? '' : 'disabled'}></div>
                  <label class="r-sw"><input type="checkbox" data-ren="${r.code}" ${d.enabled ? 'checked' : ''}
                    ${r.editable ? '' : 'disabled'}> 启用</label>
                  <div class="r-note">${esc(r.descr)}</div>
                </div>`;
              }).join('')}
              <div class="q-dim">归一化合计：${pct(sum, 0)}　
                ${main.filter((r) => draftOf(r.code).enabled).length < 2 ? '<span class="warn-txt">至少保留两条规则</span>' : '权重越大，该规则对最终价的影响越大'}</div>
            </div>

            ${groups.filter((g) => ['retrieval', 'threshold'].indexOf(g.k) >= 0).map((g) => `
              <div class="card" style="margin-bottom:14px">
                <div class="card-h"><h3>${esc(g.t)}</h3>
                  <span class="sub">${S.rules.rules.filter((r) => r.group === g.k).length} 条规则</span></div>
                ${S.rules.rules.filter((r) => r.group === g.k).map((r) => ruleParams(r)).join('')}
              </div>`).join('')}

            <div class="card">
              <div class="card-h"><h3>定价参数（市场竞争 / 客户 / 成本）</h3>
                <span class="sub">权重决定「谁说了算」，参数决定「这条规则怎么算」</span></div>
              ${S.rules.rules.filter((r) => ['market', 'cost', 'customer'].indexOf(r.group) >= 0)
                .map((r) => ruleParams(r)).join('')}
            </div>
          </div>

          <div>
            <div class="card" style="margin-bottom:14px">
              <div class="card-h"><h3>影子试算</h3><span class="sub">不落库，只对比</span></div>
              <div class="r-sim-sel">
                <select id="simRun">
                  ${(S.data ? S.data.requests : []).filter((x) => x.latest).map((x) =>
                    `<option value="${x.latest.run_id}" ${S.simRun === x.latest.run_id ? 'selected' : ''}>
                      ${esc(x.client)} · ${esc(x.id)}</option>`).join('')}
                </select>
              </div>
              ${S.sim ? simResult(S.sim) : `<div class="q-dim">点右上角「影子试算」，用当前草稿权重重算一条历史需求，看价格与毛利率怎么变。</div>`}
            </div>

            ${regressCard()}
            ${auditCard()}

            <div class="card">
              <div class="card-h"><h3>版本历史</h3><span class="sub">${S.rules.versions.length} 个</span></div>
              ${S.rules.versions.slice().reverse().map((v) => `<div class="q-ver-i ${v.active ? 'on' : ''}">
                <div><b class="mono">${esc(v.version)}</b> ${v.active ? bd('teal', '生效中') : ''}</div>
                <div class="q-dim">${esc(v.label)} · ${esc(v.by)} · ${esc(v.at)}</div>
                ${v.diff ? `<div class="q-ver-d">${Object.keys(v.diff).slice(0, 4).map((k) => {
                  const x = v.diff[k];
                  if (x.reset) return `<span>全量恢复默认</span>`;
                  const bits = [];
                  if (x.weight) bits.push(`权重 ${num(x.weight[0], 2)} → ${num(x.weight[1], 2)}`);
                  if (x.enabled) bits.push(`启用 ${x.enabled[0]} → ${x.enabled[1]}`);
                  Object.keys(x.params || {}).forEach((p) => bits.push(
                    `${PARAM_LABEL[p] || p} ${JSON.stringify(x.params[p][0])} → ${JSON.stringify(x.params[p][1])}`));
                  return `<span class="mono">${k}：${esc(bits.join('，'))}</span>`;
                }).join('')}</div>` : ''}
              </div>`).join('')}
              <div class="q-acts" style="margin-top:10px">
                <button class="btn sm" data-rul="reset">恢复出厂默认</button>
                <span class="hint">需 admin 权限</span>
              </div>
            </div>
          </div>
        </div>
      </div>`;
    },

    mount() { ensureRules().then((dirty) => { if (dirty) paint(); }); }
  };

  function draftOf(code) {
    return (S.draft && S.draft[code]) || { weight: 0, enabled: true, params: {} };
  }

  function ruleParams(r) {
    const d = draftOf(r.code);
    const rows = Object.keys(d.params || {}).map((k) => {
      const v = d.params[k];
      const lock = RULES_META[r.code] && RULES_META[r.code].lock;
      if (typeof v === 'number') {
        const step = k === 'valid_days' || k === 'topk' || k === 'top_n' || k === 'min_n'
          || k === 'min_samples' || k === 'rrf_k' || k === 'max_rounds' ? 1 : 0.01;
        return `<div class="r-p"><span>${esc(PARAM_LABEL[k] || k)}</span>
          <input class="r-num" type="number" step="${step}" value="${v}"
            data-rp="${r.code}:${k}" ${r.editable && !lock ? '' : 'disabled'}></div>`;
      }
      if (typeof v === 'boolean') {
        return `<div class="r-p"><span>${esc(PARAM_LABEL[k] || k)}</span>
          <label class="r-sw"><input type="checkbox" data-rpb="${r.code}:${k}" ${v ? 'checked' : ''}
            ${r.editable && !lock ? '' : 'disabled'}> ${v ? '开启' : '关闭'}</label></div>`;
      }
      if (typeof v === 'object' && v !== null) {
        // 数组型参数没有业务键名，按下标给中文标签（价格带就两个端点）
        const arrLab = { price_band: ['下限', '上限'] }[k] || [];
        return `<div class="r-p r-p-wide"><span>${esc(PARAM_LABEL[k] || k)}</span>
          <div class="r-sub">${Object.keys(v).map((kk) => `<label>${esc(
            Array.isArray(v) ? (arrLab[Number(kk)] || String(Number(kk) + 1)) : (NESTED_LABEL[kk] || kk))}
            <input class="r-num" type="number" step="0.01" value="${v[kk]}"
              data-rp="${r.code}:${k}.${kk}" ${r.editable && !lock ? '' : 'disabled'}></label>`).join('')}</div></div>`;
      }
      return `<div class="r-p"><span>${esc(PARAM_LABEL[k] || k)}</span>
        <input class="r-num r-bool" type="text" value="${esc(v)}" data-rp="${r.code}:${k}" disabled></div>`;
    }).join('');
    return `<div class="r-block">
      <div class="r-block-h"><b>${esc(r.name)}</b><span class="mono">${r.code}</span>
        ${r.editable ? '' : bd('red', '硬约束')}</div>
      <div class="r-block-d">${esc(r.descr)}</div>
      <div class="r-pgrid">${rows}</div></div>`;
  }

  function simResult(sim) {
    const b = sim.before, a = sim.after, d = sim.delta;
    return `<div class="q-sim">
      <div class="q-sim-row"><span>状态</span>
        <b>${(TONE[b.status] || ['', b.status])[1]}</b><i>→</i>
        <b class="${sim.status_changed ? 'warn-txt' : ''}">${(TONE[a.status] || ['', a.status])[1]}</b></div>
      <div class="q-sim-row"><span>报价</span><b>${num(b.price)} 万</b><i>→</i>
        <b>${num(a.price)} 万${d ? `<em class="${d.price > 0 ? 'up' : d.price < 0 ? 'down' : ''}">${d.price > 0 ? '+' : ''}${num(d.price)}（${d.pct > 0 ? '+' : ''}${pct(d.pct, 1)}）</em>` : ''}</b></div>
      <div class="q-sim-row"><span>毛利率</span><b>${pct(b.margin, 1)}</b><i>→</i>
        <b>${pct(a.margin, 1)}${d ? `<em class="${d.margin > 0 ? 'up' : d.margin < 0 ? 'down' : ''}">${d.margin > 0 ? '+' : ''}${pct(d.margin, 1)}</em>` : ''}</b></div>
      <div class="q-sim-row"><span>置信度</span><b>${pct(b.confidence)}</b><i>→</i><b>${pct(a.confidence)}</b></div>
      <div class="q-dim">同一份需求、同一份输入，只换了权重：${esc(sim.run_id)}</div>
      ${a.status !== b.status ? `<div class="note amber" style="margin-top:8px"><b>注意：</b>规则改动让结论状态发生了变化，这类改动上线前应做回归。</div>` : ''}
    </div>`;
  }

  function regressCard() {
    if (!S.regress) return '';
    const g = S.regress;
    return `<div class="card" style="margin-bottom:14px">
      <div class="card-h"><h3>改前回归</h3><span class="sub">${g.count} 条历史需求重算</span>
        ${g.changed ? bd('amber', g.changed + ' 条状态翻转') : bd('teal', '状态无翻转')}</div>
      ${(g.items || []).slice(0, 8).map((it) => `<div class="q-rev ${it.status_changed ? 'warn' : ''}">
        <span class="q-rev-d">${esc(it.client)}</span>
        <span class="q-rev-t">${num(it.before.price)} → ${num(it.after.price)} 万
          <em class="${(it.delta_pct || 0) > 0 ? 'up' : 'down'}">${(it.delta_pct || 0) > 0 ? '+' : ''}${pct(it.delta_pct, 1)}</em></span>
        <span class="q-rev-m">${(TONE[it.before.status] || ['', it.before.status])[1]} → ${(TONE[it.after.status] || ['', it.after.status])[1]}
          ${it.status_changed ? bd('red', '翻转') : ''}</span>
      </div>`).join('')}
      <div class="q-dim">用当前草稿权重对已报价需求全量重算；价差超 ±10% 或状态翻转应人工确认后再保存。</div>
    </div>`;
  }

  function auditCard() {
    if (!S.audit) return '';
    const a = S.audit;
    return `<div class="card" style="margin-bottom:14px">
      <div class="card-h"><h3>LLM 调用审计</h3><span class="sub">${a.total} 次 · ${a.tokens} tokens</span></div>
      <table class="data"><thead><tr><th>节点</th><th>用途</th><th>模型</th><th class="num">tokens</th><th class="num">耗时</th></tr></thead>
        <tbody>${(a.items || []).slice(0, 15).map((c) => `<tr>
          <td class="mono">${esc(c.node)}</td><td>${esc(c.purpose)}</td>
          <td>${c.model === 'offline' ? bd('amber', '模板') : `<span class="q-src llm">${esc(c.model)}</span>`}</td>
          <td class="num">${(c.tokens_in || 0) + (c.tokens_out || 0)}</td>
          <td class="num">${num(c.latency_ms, 0)} ms</td></tr>`).join('')}</tbody></table>
      <div class="q-dim">哪一步用了模型、用了哪个、花了多少 token，一眼可答；离线时 model=模板。</div>
    </div>`;
  }

  /* ================= 交互 ================= */

  document.addEventListener('input', (e) => {
    const t = e.target;
    if (t.dataset.rw) {
      const code = t.dataset.rw;
      if (!S.draft || !S.draft[code]) return;
      let v = Number(t.value);
      if (isNaN(v)) v = 0;
      v = Math.max(0, Math.min(1, v));
      S.draft[code].weight = v;
      document.querySelectorAll(`[data-rw="${code}"]`).forEach((el) => {
        if (el !== t) el.value = v;
      });
      refreshNorm();
      return;
    }
    if (t.dataset.rp) {
      const [code, path] = t.dataset.rp.split(':');
      if (!S.draft[code]) return;
      const v = t.type === 'number' ? Number(t.value) : t.value;
      if (path.indexOf('.') > 0) {
        const [k, kk] = path.split('.');
        const cur = S.draft[code].params[k];
        if (Array.isArray(cur)) cur[Number(kk)] = isNaN(v) ? t.value : v;
        else cur[kk] = isNaN(v) ? t.value : v;
      } else {
        S.draft[code].params[path] = isNaN(v) ? t.value : v;
      }
      S.sim = null;
      return;
    }
    if (t.id === 'qComment') S.review.comment = t.value;
    if (t.id === 'qFinalPrice') S.review.price = t.value;
    if (t.dataset && t.dataset.qans) S.ans[t.dataset.qans] = t.value;
  });

  document.addEventListener('keydown', (e) => {
    if (e.target && e.target.id === 'chatInput' && e.key === 'Enter') {
      e.preventDefault();
      sendChat();
    }
  });

  document.addEventListener('change', (e) => {
    const t = e.target;
    if (t.dataset && t.dataset.ren) {
      const code = t.dataset.ren;
      if (!S.draft[code]) return;
      S.draft[code].enabled = t.checked;
      paint();
      return;
    }
    if (t.dataset && t.dataset.rpb) {
      const [code, path] = t.dataset.rpb.split(':');
      if (!S.draft[code]) return;
      S.draft[code].params[path] = t.checked;
      S.sim = null;
      paint();
      return;
    }
    if (t.id === 'simRun') { S.simRun = t.value; S.sim = null; paint(); }
  });

  function refreshNorm() {
    const main = ['R-MKT-01', 'R-CUS-01', 'R-COST-01'];
    const on = main.filter((c) => draftOf(c).enabled);
    const sum = on.reduce((a, c) => a + Number(draftOf(c).weight), 0) || 1;
    on.forEach((c) => {
      const el = document.querySelector(`[data-rnorm="${c}"]`);
      if (el) el.textContent = pct(Number(draftOf(c).weight) / sum);
    });
  }

  document.addEventListener('click', async (e) => {
    const t = e.target.closest('[data-qreq],[data-qnode],[data-qjson],[data-qact],[data-qrev],[data-rul],[data-qchat]');
    if (!t) return;

    // ---- 对话澄清（发送 / 快捷选项）
    if (t.dataset.qchat) {
      if (t.dataset.qchat === 'send') { sendChat(); return; }
      if (t.dataset.qchat === 'opt') { quickOpt(t.dataset.qopt); return; }
    }

    // ---- 选需求
    if (t.dataset.qreq) {
      S.reqId = t.dataset.qreq;
      S.ans = {};
      S.review = { decision: 'approved', comment: '', price: '' };
      const cur = S.data.requests.find((r) => r.id === S.reqId);
      if (cur && cur.latest) await loadDetail(cur.latest.run_id);
      else S.detail = null;
      paint();
      return;
    }

    // ---- 展开节点
    if (t.dataset.qnode) {
      const k = t.dataset.qnode;
      S.open[k] = !S.open[k];
      paint();
      return;
    }
    if (t.dataset.qjson) {
      const k = t.dataset.qjson;
      S.showJson[k] = !S.showJson[k];
      paint();
      return;
    }

    // ---- 复核决策选择
    if (t.dataset.qrev) {
      S.review.decision = t.dataset.qrev;
      paint();
      return;
    }

    // ---- 动作
    if (t.dataset.qact) {
      const act = t.dataset.qact;

      if (act === 'rerun' || act === 'answer' || act === 'answer-default') {
        if (S.busy) return;
        if (!S.reqId) return;
        let answers = {};
        if (act === 'answer') {
          answers = {};
          Object.keys(S.ans).forEach((k) => { if (S.ans[k] !== '') answers[k] = S.ans[k]; });
        } else if (act === 'answer-default') {
          const node = S.detail && S.detail.trace.find((n) => n.key === 'clarify');
          ((node || {}).detail || {}).questions && node.detail.questions.forEach((q) => {
            answers[q.slot] = ANSWER_HINT[q.slot] || q.current;
          });
        }
        S.busy = true; paint();
        try {
          const d = await api().pricingGo(S.reqId, answers);
          S.detail = d;
          S.runId = d.run.run_id;
          S.open = {};
          S.review = { decision: 'approved', comment: '', price: '' };
          S.data = await api().pricingOverview();
          const cur = S.data.requests.find((r) => r.id === S.reqId);
          if (cur && cur.latest) await loadDetail(cur.latest.run_id);
          toast(`推理完成 · ${(TONE[d.run.status] || ['', d.run.status])[1]} · 置信度 ${pct(d.run.confidence)}`);
        } catch (err) {
          toast('推理失败：' + err.message);
        }
        S.busy = false;
        paint();
        return;
      }

      if (act === 'review') {
        if (!S.detail || !S.detail.run) return;
        if (!S.review.comment.trim()) { toast('请填写复核意见'); return; }
        if (S.review.decision === 'adjusted' && !S.review.price) { toast('调价需给出最终价格'); return; }
        S.busy = true; paint();
        try {
          await api().pricingReview(S.detail.run.run_id, {
            decision: S.review.decision,
            comment: S.review.comment,
            final_price: S.review.decision === 'adjusted' ? Number(S.review.price) : null
          });
          const rid = S.detail.run.run_id;
          await loadDetail(rid);
          S.data = await api().pricingOverview();
          S.review = { decision: 'approved', comment: '', price: '' };
          toast('复核已提交并写入审计');
        } catch (err) {
          toast('复核失败：' + err.message);
        }
        S.busy = false;
        paint();
        return;
      }
    }

    // ---- 规则页动作
    if (t.dataset.rul) {
      const act = t.dataset.rul;

      if (act === 'simulate') {
        if (!S.simRun) { toast('先选一条已出价的需求'); return; }
        const rules = draftPayload();
        t.disabled = true;
        try {
          S.sim = await api().pricingSimulate({ run_id: S.simRun, rules });
          paint();
        } catch (err) {
          toast('试算失败：' + err.message);
          t.disabled = false;
        }
        return;
      }

      if (act === 'save') {
        const label = window.prompt('给这个规则版本起个名字', '市场偏保守（提高市场对标权重）');
        if (label === null) return;
        try {
          const r = await api().pricingSaveRules({ rules: draftPayload(), label });
          toast(r.changed ? `已保存 ${r.version} · 改动 ${r.changed} 条规则` : r.message || '没有改动');
          await ensureRules(true);
          paint();
        } catch (err) {
          toast('保存失败：' + err.message);
        }
        return;
      }

      if (act === 'reset') {
        if (!window.confirm('恢复出厂默认会生成一个新版本，当前权重全部覆盖。继续？')) return;
        try {
          const r = await api().pricingResetRules();
          toast(`已恢复出厂默认 · ${r.version}`);
          await ensureRules(true);
          paint();
        } catch (err) {
          toast('恢复失败：' + err.message);
        }
        return;
      }

      if (act === 'regress') {
        if (!S.draft) { toast('先加载规则'); return; }
        t.disabled = true;
        try {
          S.regress = await api().pricingRegress(draftPayload());
          paint();
        } catch (err) {
          toast('回归失败：' + err.message);
        }
        t.disabled = false;
        return;
      }

      if (act === 'audit') {
        try {
          S.audit = await api().pricingLlmCalls();
          paint();
        } catch (err) {
          toast('审计拉取失败：' + err.message);
        }
        return;
      }
    }
  });

  function draftPayload() {
    return Object.keys(S.draft).map((code) => ({
      code: code,
      weight: S.draft[code].weight,
      enabled: S.draft[code].enabled,
      params: S.draft[code].params
    }));
  }

  /* ================= 挂载 ================= */

  window.PRICING = { quote: QuotePage, 'pricing-rules': RulesPage };
  window.PRICING.__paint = paint;
})();
