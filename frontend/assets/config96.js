/* 初始化配置推导 · 96 题问卷版（v4.1）
 *
 * 设计约束（与后端一致）：
 * 1. 前端不算配置——所有值都来自后端，本地不留第二套规则；
 * 2. 不自动重算——填问卷只记录答案，点「AI 推导」才推导（唯一计算入口）。
 *    v4.0 的毛病是每个 input 事件都触发 derive → 整页重绘，
 *    结果输入框失焦、滚动位置丢失，看起来像页面在刷新；
 * 3. 交互一律局部重绘：改答案只重绘左栏，切页签只重绘右栏；
 * 4. 推导过程流式回放——后端逐步吐真实计算事件，前端逐条显示，最后一次性出结果。
 */
(function () {
  const S = {
    ans: {},          // 已答内容
    result: null,     // 上一次推导结果
    phase: 'idle',    // idle | running
    steps: [],        // 本次已揭示的推导步骤
    pending: [],      // 已到达但未揭示的步骤
    final: null,      // 流式最后一步带回的结果
    totalMs: 0,       // 后端真实耗时
    streamEnd: false,
    streamErr: null,
    error: null,
    sec: null,        // 展开的问卷部分
    tab: 'cfg',       // cfg | issues | flow | rules | agent
    group: 'all',
    st: 'all',
    rules: null,
    inited: false,
    autoRun: false,
    imp: null,        // 导入结果：{state:'loading'|'done'|'error', ...}
    modal: null,      // 弹窗：{kind:'issue'|'cfg', id, code, draft:{}, ov:{}, ovWhy:{}}
    overrides: {},    // 人工指定的配置值：{C37:{v,why,at}}，随推导一起提交
    savedAt: null,    // 草稿保存时间（只是落盘，不代表算过）
    saveErr: null,
    llmCfg: null,     // 大模型配置状态（来自 /api/llm/config）
    llmErr: null,     // 本次 AI 推导里的模型错误
  };

  const GROUP_T = {
    org: '组织与基础参数', catalog: '报事报修类型字典', flow: '工单流转流程',
    sla: '时限与预警', approval: '审批与挂起', fee: '收费与预约',
    priority: '优先级、评价与回访',
  };
  const ST_T = { ok: ['已确认', 'teal'], def: ['默认值', 'gray'], tbd: ['待确认', 'red'] };
  const TYPE_T = { single: '单选', multi: '多选', number: '数值', text: '文本', table: '表格', time_range: '时段' };

  const tpl = () => window.SURVEY96 || { sections: [], questions: [] };
  const qmap = () => {
    const m = {};
    tpl().questions.forEach((q) => { m[q.code] = q; });
    return m;
  };
  const $ = (sel) => document.querySelector(sel);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  /* ---------------- 分支判断：没勾的业态/前置条件不展示 ---------------- */
  function visible(q) {
    const b = q.branch && q.branch.when;
    if (!b) return true;
    return Object.keys(b).every((k) => {
      const need = [].concat(b[k]);
      const got = [].concat(S.ans[k] || []);
      return need.some((n) => got.indexOf(n) >= 0);
    });
  }

  function answered(q) {
    const v = S.ans[q.code];
    if (q.qtype === 'table') {
      const rows = Array.isArray(v) ? v : [];
      return rows.some(rowFilled);
    }
    if (v === undefined || v === null || v === '') return false;
    if (Array.isArray(v)) return v.length > 0;
    if (typeof v === 'object') return Object.keys(v).length > 0;
    return true;
  }

  function requiredList() {
    return tpl().questions.filter((q) => q.required && visible(q));
  }

  /* ---------------- 表格题：真正的行列控件，不用纯文本 ---------------- */
  const SEQ_COLS = ['序号'];

  /* 列的建议值：只做 datalist 输入提示，能自由填，不覆盖已有内容 */
  const COL_HINT = {
    '项目业态': ['住宅小区', '商业写字楼', '商业写字楼（综合体）', '公寓', '学校', '产业园区'],
    '工单类型': ['报修', '投诉', '咨询', '建议', '物品放行', '装修申请', '保洁申请'],
    '优先级': ['紧急', '较紧急', '一般'],
    '审批级别': ['一级', '二级', '三级'],
    '预警等级': ['提醒级', '一般', '严重', '非常严重'],
    '升级层级': ['一级', '二级', '三级'],
    '超时时长': ['30 分钟', '1 小时', '2 小时', '4 小时', '8 小时', '24 小时', '不设超时'],
    '间隔时长': ['15 分钟', '30 分钟', '1 小时', '2 小时'],
  };

  const tableCols = (q) => (q.table && q.table.cols) || [];
  /* 同一列在不同来源里可能多空格（docx 表头「服务项  /  工单类型」），
   * 取/写一律按「去空白后相等」匹配，避免值明明有却渲染成空。 */
  const nkey = (s) => String(s == null ? '' : s).replace(/\s+/g, '');
  const isSeq = (col) => SEQ_COLS.some((s) => nkey(s) === nkey(col));
  /* 一行是否填了实质内容（序号列不算） */
  const rowFilled = (r) => !!r && Object.keys(r).some(
    (k) => !isSeq(k) && String(r[k] == null ? '' : r[k]).trim() !== '');

  /* 当前答案的行数组；不足 min_rows 补空行（只补到 DOM 层，提交时裁掉） */
  function tableRows(q) {
    const v = S.ans[q.code];
    const rows = Array.isArray(v) ? v.filter((r) => r && typeof r === 'object') : [];
    const min = Math.max(1, (q.table && q.table.min_rows) || 0);
    while (rows.length < min) rows.push({});
    return rows;
  }

  /* 同一列在不同来源里可能多空格（docx 表头「服务项  /  工单类型」），
   * 取/写一律按「去空白后相等」匹配，避免值明明有却渲染成空。 */
  function rowVal(row, col) {
    if (!row) return '';
    if (row[col] != null) return row[col];
    const k = Object.keys(row).find((x) => nkey(x) === nkey(col));
    return k === undefined ? '' : row[k];
  }

  function setRowVal(row, col, val) {
    const k = Object.keys(row).find((x) => nkey(x) === nkey(col));
    if (k !== undefined && k !== col) delete row[k];
    row[col] = val;
  }

  function cellHtml(q, r, ci, row) {
    const col = tableCols(q)[ci];
    if (isSeq(col)) {
      const own = rowVal(row, col);
      return `<td class="c96-tdt"><span class="c96-seq"${own ? ' data-fixed="1"' : ''}>${esc(String(own || r + 1))}</span></td>`;
    }
    const hint = COL_HINT[col];
    const list = hint ? ` list="dl-${q.code}-${ci}"` : '';
    const val = rowVal(row, col);
    return `<td class="c96-tdc" data-col="${esc(col)}"><input class="c96-cell" data-c96="cell" data-q="${q.code}"
      data-r="${r}" data-c="${ci}" value="${esc(val)}"${list}
      placeholder="${hint ? '选填 / 可下拉' : '填写'}"
      autocomplete="off" spellcheck="false"></td>`;
  }

  function rowHtml(q, r, row) {
    return `<tr data-tr="${r}">
      ${tableCols(q).map((c, ci) => cellHtml(q, r, ci, row)).join('')}
      <td class="c96-tdx"><button class="c96-rowx" data-c96="rowdel" data-q="${q.code}" data-r="${r}"
        title="删除这一行" tabindex="-1">×</button></td>
    </tr>`;
  }

  function tableHtml(q) {
    const cols = tableCols(q);
    const rows = tableRows(q);
    const dls = cols.map((c, ci) => {
      const h = COL_HINT[c];
      if (!h) return '';
      return `<datalist id="dl-${q.code}-${ci}">${h.map((x) => `<option value="${esc(x)}">`).join('')}</datalist>`;
    }).join('');
    return `<div class="c96-tblw">${dls}
      <table class="c96-tbl">
        <thead><tr>
          ${cols.map((c) => `<th${isSeq(c) ? ' class="c96-tht"' : ''}>${esc(c)}</th>`).join('')}
          <th class="c96-thx"></th>
        </tr></thead>
        <tbody>${rows.map((r, i) => rowHtml(q, i, r)).join('')}</tbody>
      </table>
      <div class="c96-tblf">
        <button class="c96-rowadd" data-c96="rowadd" data-q="${q.code}">+ 添加一行</button>
        <span class="c96-tbln">共 <b>${rows.length}</b> 行</span>
        <span class="c96-tblh">${cols.some(isSeq) ? '序号自动编号，不用填' : '点单元格直接填写'}</span>
      </div>
    </div>`;
  }

  /* ---------------- 渲染：题目 ---------------- */
  function renderQ(q) {
    const v = S.ans[q.code];
    let body = '';

    if (q.qtype === 'single') {
      body = q.options.map((o) =>
        `<button class="c96-opt ${v === o.v ? 'on' : ''}" data-c96="pick" data-q="${q.code}" data-v="${o.v}">${o.l}</button>`).join('');
    } else if (q.qtype === 'multi') {
      const arr = Array.isArray(v) ? v : [];
      body = q.options.map((o) =>
        `<button class="c96-opt ${arr.indexOf(o.v) >= 0 ? 'on' : ''}" data-c96="toggle" data-q="${q.code}" data-v="${o.v}">${o.l}</button>`).join('');
    } else if (q.qtype === 'number') {
      const n = v && typeof v === 'object' ? v.v : v;
      const u = v && typeof v === 'object' ? v.unit : '';
      const units = q.unit || [{ v: '', l: '—' }];
      body = `<input class="c96-in num" type="number" data-c96="num" data-q="${q.code}" value="${n == null ? '' : n}" placeholder="数值">
        <select class="c96-in unit" data-c96="unit" data-q="${q.code}">
          ${units.map((u2) => `<option value="${u2.v}" ${u2.v === u ? 'selected' : ''}>${u2.l}</option>`).join('')}
        </select>`;
    } else if (q.qtype === 'text') {
      body = `<input class="c96-in wide" type="text" data-c96="text" data-q="${q.code}" value="${esc(v || '')}" placeholder="填写">`;
    } else if (q.qtype === 'time_range') {
      const w = v || {};
      body = `<input class="c96-in t" type="time" data-c96="tstart" data-q="${q.code}" value="${w.start || ''}">
        <span class="c96-dash">–</span>
        <input class="c96-in t" type="time" data-c96="tend" data-q="${q.code}" value="${w.end || ''}">`;
    } else if (q.qtype === 'table') {
      body = tableHtml(q);
    } else {
      body = `<input class="c96-in wide" type="text" data-c96="text" data-q="${q.code}" value="${esc(v || '')}">`;
    }

    const req = q.required ? '<span class="req">*</span>' : '';
    const done = answered(q) ? '<i class="c96-dot on"></i>' : '<i class="c96-dot"></i>';
    return `<div class="c96-q ${answered(q) ? 'done' : ''}" data-qq="${q.code}">
      <div class="c96-qt">${done}<b class="mono">${q.no}.</b><span>${q.text}</span>${req}
        <em class="c96-ty">${TYPE_T[q.qtype] || q.qtype}</em></div>
      <div class="c96-qb">${body}</div>
    </div>`;
  }

  /* 左栏 HTML（不含 .c96-left 外壳，便于只换 innerHTML 保住滚动位置） */
  function renderSurveyInner() {
    const secs = tpl().sections;
    const total = requiredList().length;
    const done = requiredList().filter(answered).length;

    const body = secs.map((sec) => {
      const qs = tpl().questions.filter((q) => q.section === sec.k && visible(q));
      if (!qs.length) return '';
      const open = S.sec ? S.sec === sec.k : (sec.k === 'S1');
      const fin = qs.filter(answered).length;
      return `<div class="c96-sec ${open ? 'open' : ''}">
        <button class="c96-sech" data-c96="sec" data-k="${sec.k}">
          <span>${sec.t}</span>
          <em>${fin}/${qs.length}</em>
          <i>${open ? '−' : '+'}</i>
        </button>
        ${open ? `<div class="c96-secb">${qs.map(renderQ).join('')}</div>` : ''}
      </div>`;
    }).join('');

    return `<div class="card-h" style="padding:0 0 8px">
        <h3>问卷录入</h3><span class="sub">${tpl().meta ? tpl().meta.questions : 0} 题 / 20 部分</span>
        <div class="spacer"></div>
        <span class="hint num c96-hintnum">${done}/${total} 必答</span>
      </div>
      <div class="c96-bar"><i style="width:${total ? (done / total) * 100 : 0}%"></i></div>
      ${renderImportNote()}
      <div class="c96-secs" id="c96-secs">${body}</div>`;
  }

  /* ---------------- 导入结果条：只填表，不算规则 ---------------- */
  function renderImportNote() {
    const im = S.imp;
    if (!im) return '';
    if (im.state === 'loading') {
      return `<div class="c96-imp loading"><i class="c96-impdot"></i>
        <div><b>正在解析 ${esc(im.name || '问卷')}…</b>
        <span>读取勾选框、填空与表格，只取答案，不做任何推导</span></div></div>`;
    }
    if (im.state === 'error') {
      return `<div class="c96-imp bad"><i class="c96-impdot"></i>
        <div><b>导入失败</b><span>${esc(im.msg || '未知错误')}</span></div>
        <button class="c96-impclose" data-c96="impclose" title="关闭">×</button></div>`;
    }

    const rep = im.report || {};
    const skipped = rep.skipped || [];
    const warn = rep.mismatch || [];
    const infer = rep.inferred_unit || [];
    const nums = (arr) => arr.slice(0, 12).map((x) => x.code.replace(/^Q/, '')).join('、')
      + (arr.length > 12 ? ` 等 ${arr.length} 项` : '');

    return `<div class="c96-imp ok">
      <i class="c96-impdot"></i>
      <div>
        <b>已导入《${esc(im.filename || im.doc_title || '问卷')}》· ${rep.filled || 0} / ${rep.total || 0} 项答案</b>
        <span>表单已自动填好，<b>没有跑推导</b>——点右上角「AI 推导」才会生成 81 条配置</span>
        ${skipped.length ? `<span class="w">未填 / 未识别 ${skipped.length} 项：第 ${nums(skipped)} 题</span>` : ''}
        ${warn.length ? `<span class="w">题干与模板不一致 ${warn.length} 处：第 ${nums(warn)} 题（值仍按题号提取，建议核对）</span>` : ''}
        ${infer.length ? `<span class="w">单位不在模板选项内：${infer.map((x) => x.code + '=' + x.unit).join('、')}</span>` : ''}
      </div>
      <button class="c96-impclose" data-c96="impclose" title="关闭">×</button>
    </div>`;
  }

  /* ---------------- 渲染：结果 ---------------- */
  function renderCfg() {
    const r = S.result;
    if (!r) return renderEmpty();
    let list = r.configs;
    if (S.group !== 'all') list = list.filter((c) => c.group === S.group);
    if (S.st !== 'all') list = list.filter((c) => c.st === S.st);

    const groups = S.group === 'all'
      ? r.groups.filter((g) => list.some((c) => c.group === g.k))
      : r.groups.filter((g) => g.k === S.group);

    if (!list.length) return '<div class="note"><div>当前过滤条件下没有配置项。</div></div>';

    return groups.map((g) => {
      const items = list.filter((c) => c.group === g.k);
      return `<div class="c96-grp">
        <div class="c96-grph"><b>${GROUP_T[g.k]}</b><em>${g.k.toUpperCase()} · ${items.length} 条</em></div>
        ${items.map((c) => {
          const [t, tone] = ST_T[c.st] || ['—', 'gray'];
          return `<div class="c96-cfg ${c.conflict ? 'has-cf' : ''}" data-c96="why" data-code="${c.code}">
            <div class="c96-cfgm">
              <span class="mono c96-code">${c.code}</span>
              <span class="c96-k">${c.name}</span>
              <span class="c96-v">${esc(c.v)}</span>
              <span class="c96-src">${c.src}</span>
              <span class="tag ${c.st}">${t}</span>
              ${c.conflict ? `<span class="c96-cf">${c.conflict}</span>` : ''}
            </div>
            ${c.desc ? `<div class="c96-cfgd">${esc(c.desc)}</div>` : ''}
          </div>`;
        }).join('')}
      </div>`;
    }).join('');
  }

  function renderIssues() {
    const r = S.result;
    if (!r) return renderEmpty();
    if (!r.issues.length) return '<div class="note teal"><div>答案体检通过，无冲突项，81 条配置可唯一推导。</div></div>';
    const blk = r.issues.filter((i) => i.block).length;
    return `<div class="c96-ihd">
        <span>共 <b>${r.issues.length}</b> 条待确认${blk ? `，其中 <b class="red">${blk}</b> 条阻塞交付` : ''}</span>
        <span class="c96-ihint">点任意一条，可就地改答案或人工指定配置值</span>
      </div>`
      + r.issues.map((i) => `
      <div class="c96-issue ${i.block ? 'blk' : ''}" data-c96="issueopen" data-id="${i.id}">
        <div class="c96-ih"><span class="tag ${i.block ? 'tbd' : 'def'}">${i.block ? '阻塞' : '关注'}</span>
          <b class="mono">${i.id}</b><span>${esc(i.title)}</span>
          <i class="c96-open">处理 ›</i></div>
        <div class="c96-im">${esc(i.msg)}</div>
        <div class="c96-if">涉及第 ${i.q.join('、')} 题 · 影响 ${i.affect.join('、')}
          ${i.suggest ? `<br><span class="c96-sg">建议：${esc(i.suggest)}</span>` : ''}</div>
      </div>`).join('');
  }

  /* 流程图：泳道（谁是责任对象）× 阶段（什么时候发生）。
   * 每个节点带 lane / col，直接落到网格里，不是拿箭头硬串一条线。 */
  const STAGES = ['提报', '受理派单', '接单', '待处理', '处理中', '审核', '评价', '回访', '归档'];

  function renderFlow() {
    const f = S.result && S.result.flow;
    if (!f) return renderEmpty();
    const cols = f.cols || 9;
    const lanes = f.lanes || [{ k: 'handler', t: '处理人' }];

    // 同一 (泳道, 阶段) 可能有多个节点（如业主端提报 / 员工端代报），竖着叠
    const cell = {};
    f.nodes.forEach((n) => {
      const k = n.lane + '#' + n.col;
      (cell[k] = cell[k] || []).push(n);
    });

    const head = Array.from({ length: cols }, (_, i) =>
      `<div class="c96-fh" style="grid-column:${i + 2};grid-row:1">${esc(STAGES[i] || '')}</div>`).join('');

    const rows = lanes.map((l, li) => {
      const cells = Object.keys(cell)
        .filter((k) => k.indexOf(l.k + '#') === 0)
        .map((k) => {
          const col = Number(k.split('#')[1]);
          const items = cell[k].map((n) => `
            <div class="c96-fn ${n.kind || ''} ${n.alert ? 'alert' : ''} ${n.manual ? 'manual' : ''}"
              title="${esc(n.desc || '')}${n.src ? ' · 来源：' + esc(n.src) : ''}">
              <div class="c96-fnt"><b>${esc(n.t)}</b>${n.alert ? `<em>${esc(n.alert)}</em>` : ''}</div>
              ${n.desc ? `<span>${esc(n.desc)}</span>` : ''}
            </div>`).join('');
          return `<div class="c96-fc" style="grid-column:${col + 1};grid-row:${li + 2}">${items}</div>`;
        }).join('');
      return `<div class="c96-fln" style="grid-column:1;grid-row:${li + 2}">${esc(l.t)}</div>${cells}`;
    }).join('');

    const br = (f.branches || []).map((b) => `
      <div class="c96-br"><b>${esc(b.t)}</b><span>${esc(b.cond || '')}</span><em>${esc(b.src || '')}</em></div>`).join('');

    const laneCount = lanes.length;
    const conf = (f.nodes || []).filter((n) => n.manual).length;
    return `<div class="c96-flw">
        <div class="c96-fl" style="grid-template-columns:64px repeat(${cols},minmax(118px,1fr))">
          ${head}${rows}
        </div>
      </div>
      <div class="c96-flg">
        <span><i class="c96-dotk start"></i>提报入口</span>
        <span><i class="c96-dotk state"></i>状态节点</span>
        <span><i class="c96-dotk branch"></i>并行分支</span>
        <span><i class="c96-dotk end"></i>终态</span>
        <span class="c96-flr">${laneCount} 条泳道 · ${(f.nodes || []).length} 个节点 · ${conf} 个必须人工流转</span>
      </div>
      ${br ? `<div class="c96-brs"><div class="c96-brh">并行分支</div>${br}</div>` : ''}
      <div class="note"><div>${esc(f.note || '')}。流程模式：${esc(f.mode || '—')}。</div></div>`;
  }

  function renderRules() {
    const rs = S.rules;
    if (!rs) return '<div class="note amber"><div>未连接后端，规则目录不可用。</div></div>';
    return `<div class="note"><div>共 ${rs.total} 条规则，版本 <span class="mono">${rs.version}</span>。每条规则有唯一 ID 与输入题号，缺失或冲突时输出「待确认」，不补默认值。</div></div>
      <div class="c96-rules">${rs.rules.map((r) => `
        <div class="c96-rule"><b class="mono">${r.id}</b><span>${r.desc}</span>
        <em>${GROUP_T[r.group] || r.group} · 第 ${r.inputs.join('、')} 题${r.emits && r.emits.length ? ' · 产出 ' + r.emits.join('、') : ''}</em></div>`).join('')}</div>`;
  }

  function renderEmpty() {
    return `<div class="c96-empty">
      <div class="c96-eyebrow">等待计算</div>
      <b>左边答完，点右上角「AI 推导」</b>
      <p>填问卷时页面不会自动重算——改多少题都不会打断输入。点一次按钮，
      81 条配置按 7 个分组逐组交给大模型生成（规则先算事实底座、模型出配置说明），
      过程逐条显示，最后一次性给出配置清单。</p>
      <ul>
        <li>同答案必同指纹（<span class="mono">output_hash</span>），键顺序无关</li>
        <li>每条配置带 <span class="mono">rule</span> 与 <span class="mono">src</span>，可回溯到题号</li>
        <li>缺失或冲突一律标「待确认」，绝不补值</li>
      </ul>
      <div class="c96-empty-h">想先看效果？点右上角「载入示例」。</div>
    </div>`;
  }

  /* ---------------- 渲染：Agent 推导过程 ---------------- */
  function agentShell() {
    const ai = S.engine === 'llm';
    const model = (S.llmCfg && S.llmCfg.model) || '大模型';
    const wait = ai
      ? `正在逐组调用 ${esc(model)}（共 7 组、长组会拆批），每组约 5–15 秒，整轮 60–90 秒`
      : '读取问卷答案 → 7 组规则 → 配置对账 → 冲突扫描 → 状态机';
    return `<div class="c96-agent">
      <div class="c96-agh">
        <span class="c96-pulse"></span>
        <div><b>${ai ? 'AI 推导中' : 'Agent 推导中'}</b><span id="c96-agnow">启动推导引擎…</span></div>
        <em class="mono" id="c96-agt">0/0</em>
      </div>
      <div class="c96-agbar"><i id="c96-agp" style="width:0%"></i></div>
      <div class="c96-agsteps" id="c96-agsteps">
        <div class="c96-agwait">${wait}</div>
      </div>
      <div class="c96-agfoot" id="c96-agfoot">${ai
        ? `模型只负责内容，编号与兜底由规则锁定 · 81 条配置项一条不少`
        : '80 条规则 · 81 条配置项 · 过程与结果同源，不做预设动画'}</div>
    </div>`;
  }

  function rulesHtml(e) {
    if (!e.rules || !e.rules.length) return '';
    const head = e.rules.slice(0, 8).map((r) => `<em class="mono">${r}</em>`).join('');
    const more = e.rules.length > 8 ? `<em>+${e.rules.length - 8}</em>` : '';
    return `<div class="c96-agr">${head}${more}</div>`;
  }

  function stepRowHtml(e) {
    return `<div class="c96-agstep"><i class="ok">✓</i>
      <div class="c96-agt"><b>${e.n}. ${esc(e.title)}</b><span>${esc(e.detail)}</span>${rulesHtml(e)}</div>
      <em class="mono c96-agms">${e.ms} ms</em></div>`;
  }

  function stepRow(e) {
    const wrap = document.createElement('div');
    wrap.innerHTML = stepRowHtml(e);
    return wrap.firstChild;
  }

  /* 揭示一条步骤：增量 append，不重绘，滚动位置不跳 */
  function pushStep(e) {
    S.steps.push(e);
    const box = document.getElementById('c96-agsteps');
    if (box) {
      const wait = box.querySelector('.c96-agwait');
      if (wait) wait.remove();
      box.appendChild(stepRow(e));
      box.scrollTop = box.scrollHeight;
    }
    const p = document.getElementById('c96-agp');
    if (p) p.style.width = Math.round((S.steps.length / (e.total || 12)) * 100) + '%';
    const t = document.getElementById('c96-agt');
    if (t) t.textContent = `${S.steps.length}/${e.total || 12}`;
    const now = document.getElementById('c96-agnow');
    if (now) now.textContent = e.title;
  }

  function markAgentDone() {
    const h = $('.c96-agh');
    const st = S.final ? S.final.stats : null;
    if (h) {
      h.classList.add('done');
      const pl = h.querySelector('.c96-pulse');
      if (pl) pl.className = 'c96-pcheck';
      const b = h.querySelector('b');
      if (b) b.textContent = '推导完成';
    }
    const n = document.getElementById('c96-agnow');
    if (n && st) n.textContent = `${S.final.configs.length} 条配置 · ${st.confirmed} 已确认 / ${st.default} 默认 / ${st.tbd} 待确认`;
    const p = document.getElementById('c96-agp');
    if (p) p.style.width = '100%';
    const f = document.getElementById('c96-agfoot');
    if (f) f.innerHTML = `12 步全部执行完毕 · 后端真实耗时 <b class="mono">${S.totalMs} ms</b>`
      + `（界面按可读节奏逐条展开，不是假的进度）· 即将切到配置清单`;
  }

  /* ---------------- 渲染：右栏 ---------------- */
  function renderHead() {
    const r = S.result;
    let meta = '<span class="hint">尚未计算</span>';
    if (S.phase === 'running') meta = '<span class="hint">推导中 · 逐步回放</span>';
    else if (r) meta = `<span class="hint mono">${r.rule_version} · ${r.input_hash}/${r.output_hash}</span>`;
    return `<div class="card-h" style="padding:0 0 8px">
      <h3>推导结果</h3><div class="spacer"></div>${meta}</div>`;
  }

  function renderTabs() {
    const r = S.result;
    const tabs = [
      ['cfg', '配置清单', r ? r.configs.length : ''],
      ['issues', '待确认', r ? r.issues.length : ''],
      ['flow', '流程图', ''],
      ['rules', '规则目录', S.rules ? S.rules.total : ''],
      ['agent', '推导过程', S.steps.length ? S.steps.length : ''],
    ];
    return `<div class="tabs">${tabs.map(([k, t, n]) =>
      `<button class="${S.tab === k ? 'on' : ''}" data-c96="tab" data-k="${k}">${t}${n !== '' ? ` <span>${n}</span>` : ''}</button>`).join('')}</div>`;
  }

  function renderStats() {
    const r = S.result;
    const s = r ? r.stats : null;
    if (!s) return '';
    return `<div class="c96-stat">
      ${r.engine === 'llm'
        ? `<span class="tag ai" title="配置内容由大模型生成，规则做校验与兜底">AI · ${esc((r.llm || {}).model || '')}</span>`
        : '<span class="tag def" title="确定性规则推导">规则引擎</span>'}
      <span class="tag ok">${s.confirmed} 已确认</span>
      <span class="tag def">${s.default} 默认值</span>
      <span class="tag tbd">${s.tbd} 待确认</span>
      <span class="c96-sp"></span>
      <span>${s.blocking} 项阻塞 · ${s.conflicts} 项冲突</span>
    </div>`;
  }

  function renderPanel() {
    if (S.tab === 'agent') {
      if (!S.steps.length) return '<div class="note"><div>还没有推导记录。点右上角「AI 推导」跑一次。</div></div>';
      const rows = S.steps.map(stepRowHtml).join('');
      return `<div class="c96-agent done">
        <div class="c96-agsteps open">${rows}</div>
        <div class="c96-agfoot">本次推导 ${S.steps.length} 步 · 后端真实耗时 <b class="mono">${S.totalMs} ms</b> · 每一步的数字都是实测值</div>
      </div>`;
    }
    if (S.tab === 'cfg') {
      const r = S.result;
      if (!r) return renderEmpty();
      const chips = r.groups.map((g) =>
        `<button class="c96-chip ${S.group === g.k ? 'on' : ''}" data-c96="group" data-k="${g.k}">${GROUP_T[g.k]}<em>${g.n}</em></button>`).join('');
      const sts = [['all', '全部'], ['ok', '已确认'], ['def', '默认值'], ['tbd', '待确认']].map(([k, t]) =>
        `<button class="c96-chip ${S.st === k ? 'on' : ''}" data-c96="st" data-k="${k}">${t}</button>`).join('');
      return `<div class="c96-filters">
        <button class="c96-chip ${S.group === 'all' ? 'on' : ''}" data-c96="group" data-k="all">全部分组</button>${chips}
        <span class="c96-sp"></span>${sts}</div>${renderCfg()}`;
    }
    if (S.tab === 'issues') return renderIssues();
    if (S.tab === 'flow') return renderFlow();
    return renderRules();
  }

  function renderRightInner() {
    if (S.phase === 'running') return renderHead() + agentShell();
    return renderHead() + renderTabs() + `<div class="c96-panel">${renderPanel()}</div>` + renderStats();
  }

  /* ---------------- 渲染：操作区 ---------------- */
  function renderActs() {
    const run = S.phase === 'running';
    const d = S.result ? diffCount() : 0;
    const llmOn = !!(S.llmCfg && S.llmCfg.online);
    const hhmm = S.savedAt ? String(S.savedAt).slice(11, 16) : '';
    let stamp = '';
    if (S.result) {
      const ms = S.totalMs ? ` · ${Math.round(S.totalMs)} ms` : '';
      stamp = `<span class="c96-stamp ${d ? 'dirty' : ''}">`
        + (d ? `${d} 项改动未计算` : `已计算${ms} · 与答案一致`) + '</span>';
    }
    if (S.savedAt) {
      // 草稿标记独立叠加：算了也存过、没算也存过，都要让人看见
      stamp += `<span class="c96-stamp draft" title="改动已写入草稿，刷新页面不会丢；点「AI 推导」才会更新配置">草稿 ${hhmm} 已存</span>`;
    }
    if (!stamp) stamp = '<span class="hint">尚未推导</span>';
    return `${stamp}
      <button class="btn sm" data-c96="import" title="导入客户回填的 Word 问卷，只填表单不推导">导入问卷</button>
      <button class="btn sm" data-c96="demo">载入示例</button>
      <button class="btn sm" data-c96="export">导出配置包</button>
      <button class="btn sm" data-c96="reset">清空重填</button>
      <button class="btn sm primary" data-c96="run" ${run ? 'disabled' : ''}
        title="${llmOn ? '逐组调用大模型：模型出配置内容，规则兜底' : '还没配置大模型，先去「模型设置」填 DeepSeek API Key'}">
        ${run ? 'AI 推导中…' : (S.result && d ? `AI 推导 (${d})` : 'AI 推导')}</button>`;
  }

  /* 大模型配置状态：每次进页面刷一遍，用户可能刚去设置页配过 */
  async function loadLLM() {
    if (!window.API || !window.API.llmGetConfig) return;
    try { S.llmCfg = await window.API.llmGetConfig(); }
    catch (e) { S.llmCfg = { online: false, model: '' }; }
    paintActs();
  }

  /* 错误条：单独一个容器，只重绘它，不动左右两栏（否则会丢输入焦点与滚动位置） */
  function errHtml() {
    let h = '';
    if (S.error) h += `<div class="note red"><div>${esc(S.error)}</div></div>`;
    if (S.llmErr) h += `<div class="note red"><div>大模型调用失败：${esc(S.llmErr)}。本次已回落到规则结果。</div></div>`;
    return h;
  }

  function paintErr() {
    const el = document.getElementById('c96-err');
    if (el) el.innerHTML = errHtml();
  }

  /* ---------------- 主渲染 ---------------- */
  function paint() {
    const root = document.getElementById('c96-root');
    if (!root) return;
    root.innerHTML = `
      <div class="c96-head">
        <div>
          <div class="c96-eyebrow">AI 决策专题 ④</div>
          <h2>问卷进，配置说明书出</h2>
          <p>${tpl().meta ? `${tpl().meta.questions} 个主问题 + 10 个追问项，推导 81 条配置项。规则算事实底座，模型出配置内容。` : ''}</p>
        </div>
        <div class="c96-acts" id="c96-acts">${renderActs()}</div>
      </div>
      <div id="c96-err">${errHtml()}</div>
      <div class="c96-grid">
        <div class="c96-left" id="c96-left">${renderSurveyInner()}</div>
        <div class="c96-right" id="c96-right">${renderRightInner()}</div>
      </div>`;
    S.sig = visibleSig();
    bind(root);
  }

  function paintLeft() {
    const el = document.getElementById('c96-left');
    if (!el) return;
    const box = el.querySelector('.c96-secs');
    const top = box ? box.scrollTop : 0;
    el.innerHTML = renderSurveyInner();
    const nb = el.querySelector('.c96-secs');
    if (nb && top) nb.scrollTop = top;
    S.sig = visibleSig();
  }

  function paintRight() {
    const el = document.getElementById('c96-right');
    if (!el) return;
    // 流程图有 9 个阶段列，挤在右栏里必然横向滚，切到流程图时让它占满整行
    const grid = document.querySelector('.c96-grid');
    if (grid) grid.classList.toggle('full', S.tab === 'flow');
    const old = el.querySelector('.c96-panel');
    const top = old ? old.scrollTop : 0;
    el.innerHTML = renderRightInner();
    const np = el.querySelector('.c96-panel');
    if (np && top) np.scrollTop = top;
  }

  function paintActs() { const el = document.getElementById('c96-acts'); if (el) el.innerHTML = renderActs(); }

  /* 只重绘一道表格题（增删行后重排序号用），不影响其他题的输入焦点 */
  function paintTable(code) {
    const root = document.getElementById('c96-left');
    const box = root && root.querySelector(`.c96-q[data-qq="${code}"] .c96-qb`);
    const q = qmap()[code];
    if (box && q) box.innerHTML = tableHtml(q);
    refreshLeftMeta();
    paintActs();
  }

  /* 行数变化后就地修正 data-r / 序号 / 删除按钮状态，不重绘整表 */
  function syncRowMeta(q, tb) {
    const trs = [].slice.call(tb.querySelectorAll('tr'));
    trs.forEach((tr, i) => {
      tr.dataset.tr = String(i);
      tr.querySelectorAll('.c96-cell').forEach((el) => { el.dataset.r = String(i); });
      const del = tr.querySelector('.c96-rowx');
      if (del) { del.dataset.r = String(i); del.disabled = trs.length <= 1; }
      const seq = tr.querySelector('.c96-seq');
      if (seq && !seq.dataset.fixed) seq.textContent = String(i + 1);
    });
    const w = tb.closest('.c96-tblw');
    const n = w && w.querySelector('.c96-tbln b');
    if (n) n.textContent = String(trs.length);
  }

  /* 在末尾补一个空行：只 append DOM，不重绘，正在输入的格子不失焦 */
  function appendRowDom(q, idx) {
    const root = document.getElementById('c96-left');
    const tb = root && root.querySelector(`.c96-q[data-qq="${q.code}"] tbody`);
    if (!tb) return null;
    const buf = document.createElement('tbody');
    buf.innerHTML = rowHtml(q, idx, {});
    const tr = buf.firstElementChild;
    tb.appendChild(tr);
    syncRowMeta(q, tb);
    return tr;
  }

  /* 点选项后判断分支可见性是否变了：变了才整栏重绘，否则只改按钮样式。
     用「当前可见题号串」做签名——只跟分支条件有关，跟答案内容无关。 */
  function visibleSig() {
    return tpl().questions.filter(visible).map((q) => q.code).join(',');
  }

  /* 就地同步某一题的选中态（不重绘，焦点与滚动位置不动） */
  function syncOptions(code) {
    const root = document.getElementById('c96-left');
    if (!root) return;
    const box = root.querySelector(`.c96-q[data-qq="${code}"] .c96-qb`);
    if (!box) return;
    const v = S.ans[code];
    [].forEach.call(box.querySelectorAll('.c96-opt'), (b) => {
      const val = b.dataset.v;
      const on = Array.isArray(v) ? v.indexOf(val) >= 0 : v === val;
      b.classList.toggle('on', on);
    });
  }

  /* 输入过程中不重绘，只把进度条 / 小圆点 / 计数就地更新，输入框焦点与光标不丢 */
  function refreshLeftMeta() {
    const root = document.getElementById('c96-left');
    if (!root) return;
    const req = requiredList();
    const done = req.filter(answered).length;
    const bar = root.querySelector('.c96-bar i');
    if (bar) bar.style.width = (req.length ? (done / req.length) * 100 : 0) + '%';
    const hint = root.querySelector('.c96-hintnum');
    if (hint) hint.textContent = `${done}/${req.length} 必答`;

    const qm = qmap();
    root.querySelectorAll('.c96-q[data-qq]').forEach((el) => {
      const q = qm[el.dataset.qq];
      if (!q) return;
      const on = answered(q);
      el.classList.toggle('done', on);
      const dot = el.querySelector('.c96-dot');
      if (dot) dot.classList.toggle('on', on);
    });
    tpl().sections.forEach((sec) => {
      const qs = tpl().questions.filter((q) => q.section === sec.k && visible(q));
      if (!qs.length) return;
      const em = root.querySelector(`.c96-sech[data-k="${sec.k}"] em`);
      if (em) em.textContent = `${qs.filter(answered).length}/${qs.length}`;
    });
  }

  function bind(root) {
    if (!root.dataset.bound) {
      root.dataset.bound = '1';
      root.addEventListener('click', onClick);
      root.addEventListener('input', onInput);
      root.addEventListener('change', onInput);
      root.addEventListener('keydown', onKeydown);
    }
  }

  /* ---------------- 提交前清洗：空行、空格一律不往引擎里塞 ----------------
   * 表格题的 DOM 里永远留着一条空行方便录入，提交时必须裁掉，
   * 否则引擎会把空行当成「已答」——三态就会受浏览器界面影响。 */
  function cleanAns(ans) {
    const qm = qmap();
    const out = {};
    Object.keys(ans).forEach((k) => {
      const v = ans[k];
      const q = qm[k];
      if (q && q.qtype === 'table') {
        const cols = tableCols(q);
        const rows = (Array.isArray(v) ? v : []).map((r) => {
          const o = {};
          cols.forEach((c) => {
            const s = rowVal(r, c);
            const t = s == null ? '' : String(s).trim();
            if (t) o[c] = t;
          });
          return o;
        }).filter(rowFilled);
        if (rows.length) out[k] = rows;
        return;
      }
      out[k] = v;
    });
    return out;
  }

  /* ---------------- 改动计数：与上一次推导用的答案比 ---------------- */
  function diffCount() {
    const a = cleanAns((S.result && S.result.answers) || {});
    const b = cleanAns(S.ans);
    const norm = (v) => {
      if (v === undefined || v === null || v === '') return null;
      if (Array.isArray(v)) return v.length ? JSON.stringify(v) : null;
      if (typeof v === 'object') return Object.keys(v).length ? JSON.stringify(v) : null;
      return String(v);
    };
    const keys = {};
    Object.keys(a).forEach((k) => { keys[k] = 1; });
    Object.keys(b).forEach((k) => { keys[k] = 1; });
    let n = 0;
    Object.keys(keys).forEach((k) => { if (norm(a[k]) !== norm(b[k])) n += 1; });
    return n;
  }

  /* ---------------- 交互 ---------------- */
  function onClick(e) {
    const el = e.target.closest('[data-c96]');
    if (!el) return;
    const act = el.dataset.c96;
    const q = el.dataset.q;
    const v = el.dataset.v;

    if (act === 'sec') { S.sec = S.sec === el.dataset.k ? '' : el.dataset.k; paintLeft(); return; }
    if (act === 'tab') { S.tab = el.dataset.k; paintRight(); return; }
    if (act === 'group') { S.group = el.dataset.k; paintRight(); return; }
    if (act === 'st') { S.st = el.dataset.k; paintRight(); return; }
    if (act === 'run') { runDerive('llm'); return; }
    if (act === 'import') { pickFile(); return; }
    if (act === 'impclose') { S.imp = null; paintLeft(); return; }
    if (act === 'export') { exportPack(); return; }
    if (act === 'why') { openCfg(el.dataset.code); return; }
    if (act === 'issueopen') { openIssue(el.dataset.id); return; }
    if (act === 'reset') {
      S.ans = {}; S.result = null; S.steps = []; S.pending = []; S.final = null;
      S.phase = 'idle'; S.error = null; S.group = 'all'; S.st = 'all'; S.tab = 'cfg';
      S.imp = null;
      S.overrides = {}; S.savedAt = null; S.saveErr = null;
      // 清空重填同时清掉草稿，否则下次开页面又被恢复回来
      if (window.API && window.API.clearConfigDraft) {
        window.API.clearConfigDraft().catch(() => {});
      }
      paintLeft(); paintRight(); paintActs(); return;
    }
    if (act === 'demo') {
      S.ans = JSON.parse(JSON.stringify(window.SURVEY96_DEMO || {}));
      S.sec = 'S1'; S.tab = 'cfg'; S.group = 'all'; S.st = 'all';
      paintLeft(); paintActs();
      runDerive();
      return;
    }

    if (act === 'rowadd') {
      const qq = qmap()[q];
      if (!qq) return;
      const rows = tableRows(qq).slice();
      rows.push({});
      S.ans[q] = rows;
      const tr = appendRowDom(qq, rows.length - 1);
      refreshLeftMeta();
      paintActs();
      if (tr) {
        const c = tr.querySelector('.c96-cell');
        if (c) c.focus();
      }
      return;
    }
    if (act === 'rowdel') {
      const qq = qmap()[q];
      const r = Number(el.dataset.r);
      const tb = el.closest('tbody');
      const domRows = tb ? tb.querySelectorAll('tr').length : r + 1;
      const rows = tableRows(qq).slice();
      while (rows.length < domRows) rows.push({});   // 与 DOM 行数对齐，索引才准
      if (rows.length <= 1) return;
      rows.splice(r, 1);
      S.ans[q] = rows;
      paintTable(q);
      return;
    }

    if (act === 'pick') {
      S.ans[q] = v;
      if (visibleSig() !== S.sig) { paintLeft(); } else { syncOptions(q); refreshLeftMeta(); }
      paintActs();
      return;
    }
    if (act === 'toggle') {
      const arr = Array.isArray(S.ans[q]) ? S.ans[q].slice() : [];
      const i = arr.indexOf(v);
      if (i >= 0) arr.splice(i, 1); else arr.push(v);
      S.ans[q] = arr;
      if (visibleSig() !== S.sig) { paintLeft(); } else { syncOptions(q); refreshLeftMeta(); }
      paintActs();
      return;
    }
  }

  function onInput(e) {
    const el = e.target.closest('[data-c96]');
    if (!el) return;
    const act = el.dataset.c96;
    const q = el.dataset.q;
    if (!q) return;

    if (act === 'num' || act === 'unit') {
      const cur = (typeof S.ans[q] === 'object' && S.ans[q]) || {};
      if (act === 'num') {
        S.ans[q] = { v: el.value === '' ? null : Number(el.value), unit: cur.unit || '' };
      } else {
        S.ans[q] = { v: cur.v === undefined ? null : cur.v, unit: el.value };
      }
    } else if (act === 'text') {
      S.ans[q] = el.value;
    } else if (act === 'tstart' || act === 'tend') {
      const cur = S.ans[q] || {};
      S.ans[q] = {
        start: act === 'tstart' ? el.value : (cur.start || ''),
        end: act === 'tend' ? el.value : (cur.end || ''),
      };
    } else if (act === 'cell') {
      const qq = qmap()[q];
      const ci = Number(el.dataset.c);
      const r = Number(el.dataset.r);
      const col = tableCols(qq)[ci];
      const tb = el.closest('tbody');
      const domRows = tb ? tb.querySelectorAll('tr').length : r + 1;
      const rows = tableRows(qq).slice();
      while (rows.length < domRows) rows.push({});
      if (!rows[r]) rows[r] = {};
      setRowVal(rows[r], col, el.value);
      S.ans[q] = rows;
      // 在最后一行落笔 → 自动补一行空行（只 append，不重绘，光标不动）
      if (r === rows.length - 1 && el.value.trim() !== '') appendRowDom(qq, rows.length);
    } else {
      return;
    }

    // 关键：输入过程中绝不重绘，只就地刷新进度与「未计算改动」提示
    refreshLeftMeta();
    paintActs();
  }

  /* 表格里按 Enter → 跳到同列下一行，到底就在末尾补一行，符合连续录入习惯 */
  function onKeydown(e) {
    const el = e.target.closest('[data-c96="cell"]');
    if (!el || e.key !== 'Enter') return;
    e.preventDefault();
    const q = el.dataset.q;
    const r = Number(el.dataset.r);
    const ci = el.dataset.c;
    const tr = el.closest('tr');
    const next = tr && tr.nextElementSibling;
    let target = next && next.querySelector(`.c96-cell[data-c="${ci}"]`);
    if (!target) {
      const qq = qmap()[q];
      const tb = el.closest('tbody');
      const domRows = tb ? tb.querySelectorAll('tr').length : r + 1;
      const rows = tableRows(qq).slice();
      while (rows.length < domRows) rows.push({});   // 与 DOM 行数对齐
      rows.push({});
      S.ans[q] = rows;
      const ntr = appendRowDom(qq, rows.length - 1);
      target = ntr && ntr.querySelector(`.c96-cell[data-c="${ci}"]`);
    }
    if (target) target.focus();
  }

  /* ---------------- 导入问卷：只填表，不推导 ----------------
   * 明确边界：拿到 answers 后只写进 S.ans 并重绘左栏，
   * 绝不调用 runDerive——要不要算，由用户点「AI 推导」决定。 */
  function pickFile() {
    const old = document.getElementById('c96-filepick');
    if (old) old.remove();
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.id = 'c96-filepick';
    inp.accept = '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document';
    inp.style.display = 'none';
    inp.addEventListener('change', () => {
      const f = inp.files && inp.files[0];
      document.body.removeChild(inp);
      if (f) importFile(f);
    });
    document.body.appendChild(inp);
    inp.click();
  }

  async function importFile(file) {
    if (!window.API || !window.API.importSurvey) {
      S.imp = { state: 'error', msg: '未连接后端：请先运行 backend/run.py' };
      paintLeft();
      return;
    }
    if (!/\.docx$/i.test(file.name)) {
      S.imp = { state: 'error', msg: '只支持 .docx 文件；.doc / pdf 请先另存为 .docx' };
      paintLeft();
      return;
    }

    S.imp = { state: 'loading', name: file.name };
    paintLeft();
    paintActs();

    try {
      const r = await window.API.importSurvey(file);
      S.ans = r.answers || {};
      S.imp = { state: 'done', ...r };
      S.sec = firstFilledSection();
      paintLeft();
      paintActs();
      // 注意：这里没有 runDerive()，也没有 schedule()
    } catch (e) {
      S.imp = { state: 'error', msg: String((e && e.message) || e) };
      paintLeft();
      paintActs();
    }
  }

  /* 导入后把第一个有答案的部分展开，省得用户自己找 */
  function firstFilledSection() {
    const qs = tpl().questions;
    for (const sec of tpl().sections) {
      const hit = qs.some((q) => q.section === sec.k && answered(q));
      if (hit) return sec.k;
    }
    return 'S1';
  }

  /* ---------------- 推导：流式，全部走后端 ----------------
   * 唯一计算入口是「AI 推导」——调 runDerive 不传参就是 llm。
   * mode='rule' 只留给排障/脚本（后端规则通道仍在，页面不再提供按钮）。
   */
  async function runDerive(mode) {
    if (S.phase === 'running') return;
    const engine = mode === 'rule' ? 'rule' : 'llm';
    if (engine === 'llm') {
      // 首次进页面配置还没拉回来就点了按钮：现拉一次，避免误报「未配置」
      if (!S.llmCfg && window.API && window.API.llmGetConfig) {
        try { S.llmCfg = await window.API.llmGetConfig(); }
        catch (e) { S.llmCfg = { online: false, model: '' }; }
      }
      if (!(S.llmCfg && S.llmCfg.online)) {
        S.error = 'AI 推导需要大模型：先去左侧「模型设置」填 DeepSeek API Key，保存后再点';
        paintActs(); paintErr();
        return;
      }
    }
    const han = !!window.API && (engine === 'llm'
      ? !!window.API.configDeriveLLMStream
      : !!(window.API.configDeriveStream || window.API.configDeriveV4));
    if (!han) {
      S.error = '未连接后端：请先运行 backend/run.py';
      paintActs(); paintErr();
      return;
    }

    S.phase = 'running';
    S.engine = engine;
    S.steps = []; S.pending = []; S.final = null;
    S.totalMs = 0; S.error = null; S.streamEnd = false; S.streamErr = null;
    S.llmErr = null;
    paintRight();
    paintActs();
    paintErr();

    const t0 = Date.now();
    const payload = cleanAns(S.ans);   // 空行不提交，避免「界面留白行 = 已答」
    const onEvent = (ev) => {
      if (ev.type === 'done') { S.final = ev.result; S.totalMs = ev.total_ms || 0; }
      else if (ev.type === 'error') {
        S.llmErr = ev.message || ev.code || '模型调用失败';
        paintErr();                      // 模型报错要当场看见，不能等推导结束
      }
      else S.pending.push(ev);
    };

    const call = (engine === 'llm' && window.API.configDeriveLLMStream
      ? window.API.configDeriveLLMStream(payload, onEvent, S.overrides)
      : (window.API.configDeriveStream
        ? window.API.configDeriveStream(payload, onEvent, S.overrides)
        : window.API.configDeriveV4(payload, S.overrides).then((r) => { S.final = r; }))
    ).catch((err) => { S.streamErr = err; }).then(() => { S.streamEnd = true; });
    void call;

    // 纯规则 7ms 就算完，按可读节奏慢放；AI 推导每步真要 1-3 秒，不用再等
    const pace = engine === 'llm' ? 0 : 170;
    while (!S.streamEnd || S.pending.length) {
      if (S.streamErr) break;
      if (S.pending.length) pushStep(S.pending.shift());
      await sleep(S.pending.length ? pace : 60);
    }
    // 兜底：流已结束但还有剩余步骤
    while (S.pending.length) { pushStep(S.pending.shift()); await sleep(120); }

    if (S.streamErr || !S.final) {
      S.phase = 'idle';
      S.error = '推导失败：' + ((S.streamErr && S.streamErr.message) || '后端未返回结果');
      paintRight(); paintActs(); paintErr();
      return;
    }

    markAgentDone();
    await sleep(800);

    S.result = S.final;
    S.phase = 'idle';
    S.tab = S.forceTab || 'cfg'; S.group = 'all'; S.st = 'all';
    S.error = null;
    // 算过了，草稿就作废——否则下次开页面又把旧草稿捡回来，跟结果打架
    if (S.savedAt) {
      S.savedAt = null;
      if (window.API && window.API.clearConfigDraft) {
        window.API.clearConfigDraft().catch(() => {});
      }
    }
    paintRight(); paintActs(); paintErr();
  }

  async function loadRules() {
    if (S.rules || !window.API || !window.API.configRulesV4) return;
    try {
      S.rules = await window.API.configRulesV4();
      if (S.tab === 'rules') paintRight();
    } catch (e) { /* 规则目录失败不影响推导 */ }
  }

  /* ================= 待确认弹窗 =================
   * 两件事：把 alert 换成正经弹窗；把「待确认」从只读列表变成可处理的工作台。
   * 处理方式分两条路：
   *   1) 修正答案 —— 从根因上解决，保存后重算，冲突自然消失；
   *   2) 人工指定配置值 —— 问卷里根本没有依据的（如「月 vs 30 天」口径），
   *      顾问直接拍值并写理由，留痕后进入配置清单。
   */
  const QMAP = () => qmap();

  function modalHost() {
    let host = document.getElementById('c96-modal-host');
    if (!host) {
      host = document.createElement('div');
      host.id = 'c96-modal-host';
      document.body.appendChild(host);
      host.addEventListener('click', onModalClick);
      host.addEventListener('input', onModalInput);
      host.addEventListener('change', onModalInput);
    }
    return host;
  }

  function issueById(id) {
    return ((S.result && S.result.issues) || []).filter((i) => i.id === id)[0] || null;
  }

  function openIssue(id, code) {
    const it = issueById(id);
    if (!it) return;
    S.tab = 'issues';
    S.modal = {
      kind: 'issue', id, code: code || '',
      draft: {}, ov: {}, ovWhy: {},
      cfgOpen: code || '',
    };
    paintModal();
  }

  function openCfg(code) {
    if (!S.result) return;
    const c = S.result.configs.filter((x) => x.code === code)[0];
    if (!c) return;
    const rel = ((S.result.issues) || []).filter((i) => (i.affect || []).indexOf(code) >= 0);
    S.modal = {
      kind: 'cfg', code, id: rel.length ? rel[0].id : '',
      draft: {}, ov: {}, ovWhy: {}, cfgOpen: code,
    };
    paintModal();
  }

  function closeModal() {
    S.modal = null;
    const host = document.getElementById('c96-modal-host');
    if (host) host.innerHTML = '';
  }

  const cur = (code) => (S.modal && code in S.modal.draft) ? S.modal.draft[code] : S.ans[code];

  function sameVal(a, b) {
    const n = (v) => (v === undefined || v === null || v === '') ? null : JSON.stringify(v);
    return n(a) === n(b);
  }

  function draftChanged() {
    if (!S.modal) return 0;
    let n = 0;
    Object.keys(S.modal.draft).forEach((k) => { if (!sameVal(S.modal.draft[k], S.ans[k])) n += 1; });
    Object.keys(S.modal.ov).forEach((k) => {
      const v = (S.modal.ov[k] || '').trim();
      if (v && v !== ((S.overrides[k] || {}).v || '')) n += 1;
    });
    return n;
  }

  /* ---------------- 弹窗里的表格编辑 ----------------
   * 左栏那套表格控件绑的是 S.ans，弹窗里绑的是 draft，不能直接复用。
   * 这里做同形态的紧凑版：序号列只读、其余格可改、行可增删。
   */
  function curRows(q) {
    const v = cur(q.code);
    const src = Array.isArray(v) ? v.filter((r) => r && typeof r === 'object') : [];
    const rows = src.map((r) => Object.assign({}, r));
    const min = Math.max(1, (q.table && q.table.min_rows) || 0);
    while (rows.length < min) rows.push({});
    return rows;
  }

  /* 提交前裁掉全空行与首尾空格：界面留空行 ≠ 已答 */
  function cleanTableVal(q, val) {
    const cols = tableCols(q);
    const out = (Array.isArray(val) ? val : []).map((r) => {
      const o = {};
      cols.forEach((c) => {
        const s = rowVal(r, c);
        const t = s == null ? '' : String(s).trim();
        if (t) o[c] = t;
      });
      return o;
    }).filter(rowFilled);
    return out.length ? out : null;
  }

  function editTable(q) {
    const cols = tableCols(q);
    const rows = curRows(q);
    const head = cols.map((c) => `<th${isSeq(c) ? ' class="seq"' : ''}>${esc(c)}</th>`).join('');
    // fixed 布局下靠 colgroup 定列宽：序号窄、其余平分，否则 auto 会按字数分得很难看
    const cg = '<colgroup>' + cols.map((c) => (isSeq(c) ? '<col style="width:46px">' : '<col>')).join('')
      + '<col style="width:26px"></colgroup>';
    const trs = rows.map((r, i) => `<tr>
      ${cols.map((c, ci) => (isSeq(c)
        ? `<td class="seq">${i + 1}</td>`
        : `<td><input class="c96-ecell" data-c96m="tcell" data-q="${q.code}"
            data-r="${i}" data-c="${ci}" value="${esc(rowVal(r, c))}"
            autocomplete="off" spellcheck="false"></td>`)).join('')}
      <td class="x"><button class="c96-trowx" data-c96m="tdel" data-q="${q.code}" data-r="${i}"
        title="删除这一行" ${rows.length <= 1 ? 'disabled' : ''}>×</button></td>
    </tr>`).join('');
    return `<div class="c96-etbl">
      <div class="c96-etsc">
        <table class="c96-et">${cg}
          <thead><tr>${head}<th class="x"></th></tr></thead>
          <tbody>${trs}</tbody>
        </table>
      </div>
      <div class="c96-etf">
        <button class="c96-tadd" data-c96m="tadd" data-q="${q.code}">+ 添加一行</button>
        <span class="c96-etn">共 <b>${rows.length}</b> 行</span>
        <span class="c96-eth">${cols.some(isSeq) ? '序号自动编号' : '直接点单元格填写'}</span>
      </div>
    </div>`;
  }

  /* 单题编辑控件：与左栏同一套取值形态，但更紧凑 */
  function editQ(q) {
    const v = cur(q.code);
    const chg = !sameVal(v, S.ans[q.code]);
    let body = '';
    if (q.qtype === 'single' || q.qtype === 'multi') {
      const arr = Array.isArray(v) ? v : [];
      body = `<div class="c96-eopts">${(q.options || []).map((o) => {
        const on = q.qtype === 'single' ? v === o.v : arr.indexOf(o.v) >= 0;
        return `<button class="c96-eopt ${on ? 'on' : ''}" data-c96m="${q.qtype === 'single' ? 'pick' : 'tgl'}"
          data-q="${q.code}" data-v="${o.v}">${esc(o.l)}</button>`;
      }).join('')}</div>`;
    } else if (q.qtype === 'number') {
      const n = v && typeof v === 'object' ? v.v : v;
      const u = v && typeof v === 'object' ? v.unit : '';
      const units = q.unit || [{ v: '', l: '—' }];
      body = `<div class="c96-erow">
        <input class="c96-ein num" type="number" data-c96m="num" data-q="${q.code}"
          value="${n == null ? '' : n}" placeholder="数值">
        <select class="c96-ein unit" data-c96m="unit" data-q="${q.code}">
          ${units.map((x) => `<option value="${x.v}" ${x.v === u ? 'selected' : ''}>${x.l}</option>`).join('')}
        </select></div>`;
    } else if (q.qtype === 'text') {
      body = `<input class="c96-ein wide" type="text" data-c96m="text" data-q="${q.code}"
        value="${esc(v || '')}" placeholder="填写">`;
    } else if (q.qtype === 'time_range') {
      const w = v || {};
      body = `<div class="c96-erow">
        <input class="c96-ein t" type="time" data-c96m="tstart" data-q="${q.code}" value="${w.start || ''}">
        <span>–</span>
        <input class="c96-ein t" type="time" data-c96m="tend" data-q="${q.code}" value="${w.end || ''}"></div>`;
    } else if (q.qtype === 'table') {
      body = editTable(q);
    } else {
      body = `<input class="c96-ein wide" type="text" data-c96m="text" data-q="${q.code}" value="${esc(v || '')}">`;
    }
    const canGoto = q.qtype === 'table';
    return `<div class="c96-eq ${chg ? 'chg' : ''}">
      <div class="c96-eqh">
        <b class="mono">${q.no}.</b><span>${esc(q.text)}</span>
        ${q.required ? '<em class="req">*</em>' : ''}
        ${chg ? '<i class="c96-chgflag">已改</i>' : ''}
        <button class="c96-egoto" data-c96m="gotoq" data-no="${q.no}">定位到原题 ›</button>
      </div>
      ${body}
    </div>`;
  }

  function renderModal() {
    const m = S.modal;
    if (!m) return '';
    // 从配置行点进来就以配置为主体；关联的待确认单独列一节，不抢主体
    const issue = m.kind === 'issue' ? issueById(m.id) : null;
    const cfg = m.code ? (S.result.configs.filter((x) => x.code === m.code)[0] || null) : null;

    // 头部
    const head = issue
      ? `<span class="tag ${issue.block ? 'tbd' : 'def'}">${issue.block ? '阻塞' : '关注'}</span>
         <b class="mono c96-mid">${issue.id}</b><h4>${esc(issue.title)}</h4>`
      : `<b class="mono c96-mid">${m.code}</b><h4>${esc(cfg ? cfg.name : '配置详情')}</h4>`;

    // 说明段
    const desc = issue
      ? `<p class="c96-mmsg">${esc(issue.msg)}</p>
         ${issue.suggest ? `<p class="c96-msg2">建议：${esc(issue.suggest)}</p>` : ''}`
      : `<p class="c96-mmsg">${esc(cfg ? cfg.v : '')}</p>
         ${cfg && cfg.desc ? `<p class="c96-msgd">${esc(cfg.desc)}</p>` : ''}
         <p class="c96-msg2">来源：${cfg ? esc(cfg.src) : '—'} · 规则：<span class="mono">${cfg ? cfg.rule : '—'}</span>
         ${cfg && cfg.note ? ' · 备注：' + esc(cfg.note) : ''}</p>`;

    // 影响配置（可点开看现值）
    const aff = issue
      ? `<div class="c96-msec">
          <div class="c96-mt">受影响的配置项 <em>${issue.affect.length} 条</em></div>
          <div class="c96-aff">${issue.affect.map((code) => {
            const c = S.result.configs.filter((x) => x.code === code)[0];
            if (!c) return '';
            return `<div class="c96-affi ${m.cfgOpen === code ? 'on' : ''}" data-c96m="cfgopen" data-code="${code}"
              title="${esc(c.desc || '')}">
              <b class="mono">${c.code}</b><span>${esc(c.name)}</span><em>${esc(c.v)}</em></div>`;
          }).join('')}</div>
        </div>`
      : '';

    // 关联待确认（配置弹窗入口）
    const rel = (!issue && cfg)
      ? ((S.result.issues) || []).filter((i) => (i.affect || []).indexOf(m.code) >= 0)
      : [];
    const relHtml = rel.length
      ? `<div class="c96-msec">
          <div class="c96-mt">关联待确认 <em>${rel.length} 条</em></div>
          ${rel.map((i) => `<div class="c96-mrel" data-c96m="issue" data-id="${i.id}">
            <span class="tag ${i.block ? 'tbd' : 'def'}">${i.block ? '阻塞' : '关注'}</span>
            <b class="mono">${i.id}</b><span>${esc(i.title)}</span><i>处理 ›</i></div>`).join('')}
        </div>`
      : '';

    // 可编辑题目
    const qs = issue ? (issue.q || []).flatMap((no) =>
      QMAP() && Object.values(QMAP()).filter((q) => Number(q.no) === Number(no))) : [];
    const editBlock = qs.length
      ? `<div class="c96-msec">
          <div class="c96-mt">修正答案 <em>改完保存即重算，冲突就从根上消失</em></div>
          <div class="c96-eqs">${qs.map(editQ).join('')}</div>
        </div>`
      : '';

    // 人工指定值（只对受影响的配置项开放）
    const ovTargets = issue ? issue.affect : (cfg ? [m.code] : []);
    const ovBlock = `<div class="c96-msec">
        <div class="c96-mt">或直接确认配置值 <em>问卷里没有依据时用，会记录理由</em></div>
        <div class="c96-ovs">${ovTargets.map((code) => {
          const c = S.result.configs.filter((x) => x.code === code)[0];
          if (!c) return '';
          const curOv = m.ov[code] !== undefined ? m.ov[code] : ((S.overrides[code] || {}).v || '');
          const curWhy = m.ovWhy[code] !== undefined ? m.ovWhy[code] : ((S.overrides[code] || {}).why || '');
          return `<div class="c96-ov">
            <div class="c96-ovh"><b class="mono">${c.code}</b><span>${esc(c.name)}</span>
              <em>当前：${esc(c.v)}</em></div>
            <input class="c96-ein" type="text" data-c96m="ov" data-code="${code}"
              value="${esc(curOv)}" placeholder="人工确认值，如「30 天」">
            <input class="c96-ein why" type="text" data-c96m="ovwhy" data-code="${code}"
              value="${esc(curWhy)}" placeholder="理由（写入配置说明，必填）">
          </div>`;
        }).join('')}</div>
      </div>`;

    const n = draftChanged();
    return `<div class="c96-mask" data-c96m="close"></div>
      <div class="c96-mdl" role="dialog" aria-modal="true">
        <div class="c96-mdlh">${head}<button class="c96-mx" data-c96m="close" title="关闭">×</button></div>
        <div class="c96-mdlb">
          <div class="c96-msec">${desc}</div>
          ${aff}${relHtml}${editBlock}${ovBlock}
        </div>
        <div class="c96-mdlf">
          <span class="c96-mnote">${n ? `已改动 <b>${n}</b> 处 · 「仅保存」只存档不跑推导` : '还没改动'}</span>
          <button class="btn sm" data-c96m="close">取消</button>
          <button class="btn sm" data-c96m="saveonly" ${n ? '' : 'disabled'}
            title="只把改动存成草稿，不跑 AI 推导">仅保存</button>
          <button class="btn sm primary" data-c96m="save" ${n ? '' : 'disabled'}>保存并 AI 推导</button>
        </div>
      </div>`;
  }

  function paintModal() {
    const host = modalHost();
    host.innerHTML = renderModal();
  }

  function onModalClick(e) {
    const el = e.target.closest('[data-c96m]');
    if (!el) return;
    const act = el.dataset.c96m;
    const m = S.modal;
    if (!m) return;

    if (act === 'close') { closeModal(); return; }
    if (act === 'save') { saveModal('run'); return; }
    if (act === 'saveonly') { saveModal('draft'); return; }
    if (act === 'issue') { openIssue(el.dataset.id); return; }
    if (act === 'cfgopen') { m.cfgOpen = el.dataset.code; paintModal(); return; }
    if (act === 'gotoq') {
      const no = Number(el.dataset.no);
      const vis = gotoQuestion(no);
      if (vis) {
        closeModal();
      } else {
        // 题目被分支条件挡住（如选了标准流程，第 27 题不适用）：
        // 不装作跳过去了，就在弹窗里说清楚该先改哪道题
        const note = document.querySelector('#c96-modal-host .c96-mnote');
        if (note) {
          note.innerHTML = `<b class="red">第 ${no} 题在当前答案下不适用</b>`
            + '（前置条件未满足），已展开到所在部分，先改前置题才会出现';
        }
      }
      return;
    }
    if (act === 'tadd' || act === 'tdel') {
      const q = QMAP()[el.dataset.q];
      if (!q) return;
      const rows = curRows(q);
      if (act === 'tadd') {
        rows.push({});
      } else {
        if (rows.length <= 1) return;
        rows.splice(Number(el.dataset.r), 1);
      }
      m.draft[q.code] = rows;
      paintModal();
      return;
    }
    if (act === 'pick') {
      m.draft[el.dataset.q] = el.dataset.v;
      paintModal();
      return;
    }
    if (act === 'tgl') {
      const code = el.dataset.q;
      const v = el.dataset.v;
      const arr = (Array.isArray(cur(code)) ? cur(code) : []).slice();
      const i = arr.indexOf(v);
      if (i >= 0) arr.splice(i, 1); else arr.push(v);
      m.draft[code] = arr;
      paintModal();
      return;
    }
  }

  function onModalInput(e) {
    const el = e.target.closest('[data-c96m]');
    if (!el || !S.modal) return;
    const act = el.dataset.c96m;
    const m = S.modal;

    if (act === 'ov') { m.ov[el.dataset.code] = el.value; refreshModalFooter(); return; }
    if (act === 'ovwhy') { m.ovWhy[el.dataset.code] = el.value; return; }

    const code = el.dataset.q;
    if (!code) return;
    if (act === 'tcell') {
      const q = QMAP()[code];
      if (!q) return;
      const r = Number(el.dataset.r);
      const ci = Number(el.dataset.c);
      const rows = curRows(q);
      while (rows.length <= r) rows.push({});
      setRowVal(rows[r], tableCols(q)[ci], el.value);
      m.draft[code] = rows;
      const box = el.closest('.c96-eq');
      if (box) box.classList.add('chg');
      refreshModalFooter();
      return;
    }
    if (act === 'num' || act === 'unit') {
      const old = cur(code);
      const o = (old && typeof old === 'object') ? old : { v: null, unit: '' };
      m.draft[code] = act === 'num'
        ? { v: el.value === '' ? null : Number(el.value), unit: o.unit || '' }
        : { v: o.v === undefined ? null : o.v, unit: el.value };
    } else if (act === 'text') {
      m.draft[code] = el.value;
    } else if (act === 'tstart' || act === 'tend') {
      const o = cur(code) || {};
      m.draft[code] = {
        start: act === 'tstart' ? el.value : (o.start || ''),
        end: act === 'tend' ? el.value : (o.end || ''),
      };
    } else {
      return;
    }
    // 输入框正在焦点上，不整块重绘，只更新「已改」标记与底栏
    const box = el.closest('.c96-eq');
    if (box && !sameVal(m.draft[code], S.ans[code])) box.classList.add('chg');
    refreshModalFooter();
  }

  function refreshModalFooter() {
    const host = document.getElementById('c96-modal-host');
    const note = host && host.querySelector('.c96-mnote');
    const n = draftChanged();
    if (note) note.innerHTML = n ? `已改动 <b>${n}</b> 处 · 「仅保存」只存档不跑推导` : '还没改动';
    // 两个保存按钮都要跟着启用/禁用，漏一个就会出现"显示已改动但点不动"
    ['save', 'saveonly'].forEach((k) => {
      const b = host && host.querySelector(`[data-c96m="${k}"]`);
      if (b) b.disabled = !n;
    });
  }

  /* 保存：两种模式
   *   'draft' —— 仅保存。改动落到 state 并写进后端草稿表，**不跑推导**；
   *              适合一次处理多条待确认，最后再统一点「AI 推导」。
   *   'run'   —— 保存并 AI 推导。改完立刻重算，冲突该消的当场消失。
   */
  async function saveModal(mode) {
    const m = S.modal;
    if (!m) return;

    // 1) 答案改动（表格题要裁掉界面为录入方便留的空行）
    Object.keys(m.draft).forEach((code) => {
      const q = QMAP()[code];
      const v = (q && q.qtype === 'table') ? cleanTableVal(q, m.draft[code]) : m.draft[code];
      if (!sameVal(v, S.ans[code])) S.ans[code] = v;
    });

    // 2) 人工指定的配置值：必须写理由，否则不认
    let missingWhy = 0;
    Object.keys(m.ov).forEach((code) => {
      const v = (m.ov[code] || '').trim();
      const why = (m.ovWhy[code] || '').trim();
      if (!v) { delete S.overrides[code]; return; }
      if (!why) { missingWhy += 1; return; }
      S.overrides[code] = { v, why, at: new Date().toISOString().slice(0, 19).replace('T', ' ') };
    });
    if (missingWhy) {
      const note = document.querySelector('#c96-modal-host .c96-mnote');
      if (note) note.innerHTML = `<b class="red">${missingWhy} 条人工确认值没写理由</b>，补上再保存`;
      return;
    }

    if (mode === 'draft') {
      closeModal();
      const okd = await persistDraft();
      paintLeft();
      paintActs();
      if (!okd) S.error = `草稿保存失败：${S.saveErr || '后端未响应'}`;
      return;
    }

    closeModal();
    runDerive();
  }

  /* 写草稿：只落盘，绝不推导 */
  async function persistDraft() {
    if (!window.API || !window.API.saveConfigDraft) {
      S.saveErr = '未连接后端';
      return false;
    }
    try {
      const r = await window.API.saveConfigDraft(cleanAns(S.ans), S.overrides);
      S.savedAt = r.saved_at || new Date().toISOString().slice(0, 19).replace('T', ' ');
      S.saveErr = null;
      return true;
    } catch (e) {
      S.saveErr = String((e && e.message) || e);
      return false;
    }
  }

  /* 打开页面时捞回上次草稿。已经有答案（例如 ?demo=1）就不覆盖。 */
  async function loadDraft() {
    if (!window.API || !window.API.getConfigDraft) return;
    try {
      const d = await window.API.getConfigDraft();
      if (!d || !d.exists) return;
      if (!d.answers || !Object.keys(d.answers).length) return;
      if (Object.keys(S.ans).length) return;
      S.ans = d.answers;
      S.overrides = d.overrides || {};
      S.savedAt = d.saved_at;
      paintLeft();
      paintActs();
    } catch (e) { /* 草稿读不到不影响正常使用 */ }
  }

  /* 定位到原题：展开所在部分并滚过去。返回该题当前是否可见（被分支挡住时返回 false） */
  function gotoQuestion(no) {
    const q = Object.values(qmap()).filter((x) => Number(x.no) === Number(no))[0];
    if (!q) return false;
    S.sec = q.section;
    S.tab = 'cfg';
    paintLeft();
    const shown = visible(q);
    if (!shown) return false;
    setTimeout(() => {
      const box = document.querySelector(`.c96-q[data-qq="${q.code}"]`);
      if (!box || !box.scrollIntoView) return;
      box.scrollIntoView({ block: 'center' });
      box.classList.add('hl');
      setTimeout(() => box.classList.remove('hl'), 1400);
    }, 60);
    return true;
  }


  function exportPack() {
    if (!S.result) { alert('先点「AI 推导」再导出'); return; }
    const blob = new Blob([JSON.stringify(S.result, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `config_pack_${S.result.input_hash}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  /* ---------------- 对外 ---------------- */
  window.Config96 = {
    // app.js 每次 render 后调用；首次自动挂载并拉规则
    afterRender() {
      const root = document.getElementById('c96-root');
      if (!root) return;
      if (!tpl().questions.length) {
        root.innerHTML = '<div class="note red"><div>问卷模板未加载（assets/survey96.js）。</div></div>';
        return;
      }
      if (!S.inited) {
        S.inited = true;
        // 支持 ?demo=1&tab=issues 直接进示例态（演示与截图用），仅首次生效
        if (location.search.indexOf('demo') >= 0 && window.SURVEY96_DEMO) {
          S.ans = JSON.parse(JSON.stringify(window.SURVEY96_DEMO));
          S.autoRun = true;
        }
        const mt = /[?&]tab=(\w+)/.exec(location.search);
        if (mt) { S.tab = mt[1]; S.forceTab = mt[1]; }
      }
      paint();
      if (!S.rules) loadRules();
      loadLLM();
      if (S.autoRun) { S.autoRun = false; runDerive(); }
      else if (!S.draftLoaded) { S.draftLoaded = true; loadDraft(); }
    },
    run: runDerive,
    reset() { S.ans = {}; S.result = null; S.steps = []; },
    state: S,
  };
})();
