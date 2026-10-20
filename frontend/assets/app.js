/* 软件开发全流程决策系统 · 前端交互
   纯前端演示：无后端，数据来自 data.js（内存 mock）。 */

(function () {
  'use strict';

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const num = (v, d) => (v === null || v === undefined ? '—' : Number(v).toFixed(d === undefined ? 1 : d));
  const signed = (v, d) => (v > 0 ? '+' : '') + num(v, d);

  const state = {
    page: 'overview',
    proj: 'P-2026-014',
    req: 0,
    sortKey: 'score',
    sortDir: 'desc',
    cfgTab: 'flow',
    metricQ: '',
    metricCat: '全部',
    srcType: 'all',
    srcModule: null,
    srcSel: null,
    metricSel: null,
    probeRes: {},
    probing: null,
    imp: null
  };

  /* ============ 后端接入 ============ */

  const apiOn = () => !!(window.API && window.API.online);


  function toast(msg) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.classList.add('show'), 10);
    setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 300); }, 3200);
  }

  function paintSource() {
    const s = document.querySelector('#srcLine');
    const v = document.querySelector('#verLine');
    if (!s || !v) return;
    if (apiOn()) {
      const m = window.API.meta || {};
      s.textContent = `API 在线 · ${m.kpi_version || 'kpi_v1.1'}`
        + (window.API.user ? ` · ${window.API.user.name}` : ' · 只读');
      v.innerHTML = 'v1.1 · <em>API</em> 已连接';
    } else {
      s.textContent = '离线演示数据 · 启动后端后自动切换';
      v.innerHTML = 'v1.1 · <em>MOCK</em> 离线';
    }
  }

  /* ============ 登录用户 ============ */

  const ROLE_T = { admin: '管理员', pm: '项目经理', finance: '财务', viewer: '只读' };

  function paintUser() {
    const av = document.getElementById('avatar');
    if (!av) return;
    const u = (window.API && window.API.user) || null;
    const name = (u && u.name) || '未登录';
    av.textContent = name.slice(0, 1);
    av.title = u ? `${name} · ${u.login_name || u.email} · ${ROLE_T[u.role] || u.role}` : '未登录';
    av.dataset.name = name;
    av.dataset.role = u ? (ROLE_T[u.role] || u.role) : '';
    if (!u) av.classList.add('guest');
  }

  /* 头像点开的小菜单：显示账号 + 退出。JWT 存在浏览器，退出就是清凭证回登录页。 */
  function bindUserMenu() {
    const av = document.getElementById('avatar');
    if (!av || av.dataset.bound) return;
    av.dataset.bound = '1';
    av.style.cursor = 'pointer';

    av.addEventListener('click', (e) => {
      e.stopPropagation();
      let m = document.getElementById('whoMenu');
      if (m) { m.remove(); return; }
      const u = (window.API && window.API.user) || null;
      m = document.createElement('div');
      m.id = 'whoMenu';
      m.className = 'who-menu';
      m.innerHTML = `
        <div class="who-head">
          <b>${u ? u.name : '未登录'}</b>
          <span>${u ? (u.login_name || u.email) + ' · ' + (ROLE_T[u.role] || u.role) : ''}</span>
        </div>
        <button class="who-out" id="whoOut">退出登录</button>`;
      document.body.appendChild(m);
      const r = av.getBoundingClientRect();
      m.style.top = (r.bottom + 8) + 'px';
      m.style.right = Math.max(12, window.innerWidth - r.right) + 'px';
      m.querySelector('#whoOut').addEventListener('click', () => {
        if (window.API && window.API.logout) window.API.logout();
      });
    });

    document.addEventListener('click', () => {
      const m = document.getElementById('whoMenu');
      if (m) m.remove();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const m = document.getElementById('whoMenu');
        if (m) m.remove();
      }
    });
  }

  /* ============ 通用组件 ============ */

  const bd = (tone, txt, dot) =>
    `<span class="bd ${tone}">${dot ? '<i class="pt"></i>' : ''}${txt}</span>`;

  const kpiCard = (k) => `
    <div class="kpi">
      <div class="lab">${k.lab}</div>
      <div class="val">${k.val}${k.unit ? `<small>${k.unit}</small>` : ''}</div>
      <div class="foot">${
        k.delta ? `<span class="delta ${k.dir || 'flat'}">${k.delta}</span>` : ''
      }${k.foot ? `<span>${k.foot}</span>` : ''}</div>
    </div>`;

  const aiBox = (a) => `
    <div class="ai">
      <div class="ai-h"><span class="badge-m">AI</span>${a.title}</div>
      ${a.lines.map((l) => `<p>${l}</p>`).join('')}
    </div>`;

  const pageHead = (o) => `
    <div class="page-head">
      <div class="t">
        <div class="eyebrow">${o.eyebrow || ''}${o.pill ? `<span class="pill">${o.pill}</span>` : ''}</div>
        <h2 class="title">${o.title}</h2>
        ${o.desc ? `<div class="desc">${o.desc}</div>` : ''}
      </div>
      ${o.actions ? `<div class="head-act">${o.actions}</div>` : ''}
    </div>`;

  const table = (cols, rows, opt) => {
    opt = opt || {};
    const th = cols.map((c) => {
      const active = opt.sortKey && opt.sortKey === c.k;
      const cls = [c.num ? 'num' : '', opt.sortable ? 'sortable' : '', active ? 'sorted' : ''].join(' ');
      const ar = opt.sortable && c.k ? `<span class="ar">${active ? (opt.sortDir === 'desc' ? '▼' : '▲') : '↕'}</span>` : '';
      return `<th class="${cls}" ${opt.sortable && c.k ? `data-sort="${c.k}"` : ''}>${c.t}${ar}</th>`;
    }).join('');
    const tr = rows.map((r, i) => {
      const tds = cols.map((c) => {
        const v = typeof c.v === 'function' ? c.v(r, i) : r[c.k];
        return `<td class="${c.num ? 'num' : ''} ${c.strong ? 'strong' : ''}">${v === undefined || v === null ? '—' : v}</td>`;
      }).join('');
      return `<tr ${opt.clickable ? `class="clickable" data-row="${i}"` : ''}>${tds}</tr>`;
    }).join('');
    return `<div class="tbl-wrap"><table class="data"><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table></div>`;
  };

  /* ============ 图表 ============ */

  // 瀑布图：start → 各项增减 → end
  function waterfall(start, deltas, labels, maxV) {
    const W = 760, H = 300, pl = 48, pr = 16, pt = 28, pb = 56;
    const n = deltas.length + 2;
    const innerW = W - pl - pr, innerH = H - pt - pb;
    maxV = maxV || Math.ceil((start + deltas.reduce((a, b) => a + Math.max(0, b), 0)) * 1.12 / 20) * 20;
    const y = (v) => pt + innerH - (v / maxV) * innerH;
    const slot = innerW / n, bw = Math.min(46, slot * 0.52);

    let html = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="成本偏差分解瀑布图">`;

    // 网格
    for (let g = 0; g <= 4; g++) {
      const v = (maxV / 4) * g, yy = y(v);
      html += `<line x1="${pl}" y1="${yy}" x2="${W - pr}" y2="${yy}" stroke="#eef0f3" stroke-width="1"/>`;
      html += `<text x="${pl - 8}" y="${yy + 3.5}" font-size="10" fill="#aab3c0" text-anchor="end">${Math.round(v)}</text>`;
    }
    html += `<line x1="${pl}" y1="${y(0)}" x2="${W - pr}" y2="${y(0)}" stroke="#cfd4dc" stroke-width="1.2"/>`;

    // 起点
    const cx = (i) => pl + slot * i + slot / 2;
    html += `<rect x="${cx(0) - bw / 2}" y="${y(start)}" width="${bw}" height="${y(0) - y(start)}" rx="3" fill="#1b2430"/>`;
    html += `<text x="${cx(0)}" y="${y(start) - 8}" font-size="11.5" font-weight="600" fill="#1b2430" text-anchor="middle">${start}</text>`;
    html += `<text x="${cx(0)}" y="${H - pb + 18}" font-size="11" fill="#4a5568" text-anchor="middle">${labels[0]}</text>`;
    html += `<text x="${cx(0)}" y="${H - pb + 32}" font-size="9.5" fill="#aab3c0" text-anchor="middle">人天</text>`;

    let acc = start;
    deltas.forEach((d, i) => {
      const idx = i + 1;
      const from = acc, to = acc + d;
      const top = Math.min(from, to), bot = Math.max(from, to);
      const hgt = Math.max(2, y(top) - y(bot));
      const color = d > 0 ? '#a32d2d' : d < 0 ? '#0f6e56' : '#b9c0cb';
      if (d !== 0) {
        html += `<rect x="${cx(idx) - bw / 2}" y="${y(bot)}" width="${bw}" height="${hgt}" rx="3" fill="${color}" opacity="${d > 0 ? 0.86 : 0.8}"/>`;
      } else {
        html += `<line x1="${cx(idx) - bw / 2}" y1="${y(from)}" x2="${cx(idx) + bw / 2}" y2="${y(from)}" stroke="#b9c0cb" stroke-width="2.5" stroke-dasharray="3 3"/>`;
      }
      // 连接线
      html += `<line x1="${cx(idx) - bw / 2}" y1="${y(to)}" x2="${cx(idx) + bw / 2}" y2="${y(to)}" stroke="${color}" stroke-width="1" opacity=".35" stroke-dasharray="2 2"/>`;
      // 值
      const vy = d >= 0 ? y(to) - 9 : y(to) + 15;
      html += `<text x="${cx(idx)}" y="${vy}" font-size="11.5" font-weight="600" fill="${color}" text-anchor="middle">${d === 0 ? '0' : signed(d)}</text>`;
      html += `<text x="${cx(idx)}" y="${H - pb + 18}" font-size="11" fill="#4a5568" text-anchor="middle">${labels[idx]}</text>`;
      html += `<text x="${cx(idx)}" y="${H - pb + 32}" font-size="9.5" fill="#8a94a6" text-anchor="middle">${d === 0 ? '无记录' : (d > 0 ? '+' : '') + ((d / (start + deltas.reduce((a, b) => a + b, 0) - start)) * 100).toFixed(0) + '%'}</text>`;
      acc = to;
    });

    // 终点
    const li = n - 1;
    html += `<rect x="${cx(li) - bw / 2}" y="${y(acc)}" width="${bw}" height="${y(0) - y(acc)}" rx="3" fill="#1c5fa8"/>`;
    html += `<text x="${cx(li)}" y="${y(acc) - 8}" font-size="11.5" font-weight="600" fill="#1c5fa8" text-anchor="middle">${acc}</text>`;
    html += `<text x="${cx(li)}" y="${H - pb + 18}" font-size="11" fill="#4a5568" text-anchor="middle">${labels[li]}</text>`;
    html += `<text x="${cx(li)}" y="${H - pb + 32}" font-size="9.5" fill="#aab3c0" text-anchor="middle">人天</text>`;

    html += '</svg>';
    return html;
  }

  // 折线图
  function lineChart(values, labels, opt) {
    opt = opt || {};
    const W = 720, H = 200, pl = 38, pr = 14, pt = 18, pb = 26;
    const min = opt.min || 0.75, max = opt.max || 1.05;
    const innerW = W - pl - pr, innerH = H - pt - pb;
    const x = (i) => pl + (innerW / (values.length - 1)) * i;
    const y = (v) => pt + innerH - ((v - min) / (max - min)) * innerH;
    let h = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="CPI 趋势">`;
    // 阈值带
    h += `<rect x="${pl}" y="${y(0.95)}" width="${innerW}" height="${y(0.90) - y(0.95)}" fill="#faeeda" opacity=".55"/>`;
    h += `<rect x="${pl}" y="${y(0.90)}" width="${innerW}" height="${y(min) - y(0.90)}" fill="#fcebeb" opacity=".55"/>`;
    [1.0, 0.95, 0.90, 0.85].forEach((g) => {
      if (g < min || g > max) return;
      h += `<line x1="${pl}" y1="${y(g)}" x2="${W - pr}" y2="${y(g)}" stroke="#cfd4dc" stroke-width="1" stroke-dasharray="${g === 1 ? '0' : '3 3'}"/>`;
      h += `<text x="${pl - 6}" y="${y(g) + 3.5}" font-size="10" fill="#8a94a6" text-anchor="end">${g.toFixed(2)}</text>`;
    });
    const pts = values.map((v, i) => `${x(i)},${y(v)}`).join(' ');
    const area = `${pl},${y(min)} ${pts} ${x(values.length - 1)},${y(min)}`;
    h += `<polygon points="${area}" fill="#1c5fa8" opacity=".07"/>`;
    h += `<polyline points="${pts}" fill="none" stroke="#1c5fa8" stroke-width="2" stroke-linejoin="round"/>`;
    values.forEach((v, i) => {
      const last = i === values.length - 1;
      h += `<circle cx="${x(i)}" cy="${y(v)}" r="${last ? 4.2 : 2.8}" fill="${last ? '#a32d2d' : '#fff'}" stroke="${last ? '#a32d2d' : '#1c5fa8'}" stroke-width="1.6"/>`;
      if (i % 2 === 0 || last) h += `<text x="${x(i)}" y="${H - pb + 14}" font-size="9.5" fill="#aab3c0" text-anchor="middle">${labels[i]}</text>`;
    });
    h += `<text x="${x(values.length - 1)}" y="${y(values[values.length - 1]) - 10}" font-size="11" font-weight="600" fill="#a32d2d" text-anchor="middle">${values[values.length - 1]}</text>`;
    h += '</svg>';
    return h;
  }

  /* ============ 总览 ============ */

  function viewOverview() {
    const d = window.DB.overview;
    const projs = window.DB.projects;

    const health = projs.map((p) => {
      const over = (p.act - p.est) / p.est;
      const tone = p.cpi >= 0.95 ? 'teal' : p.cpi >= 0.9 ? 'amber' : 'red';
      const w = Math.min(100, (p.act / p.est) * 55);
      const cw = Math.min(100, 55);
      return `
        <div style="padding:11px 0;border-bottom:1px solid #eef0f3">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
            <span style="font-size:12.5px;font-weight:560;color:var(--ink)">${p.name}</span>
            <span style="font-size:10.5px;color:var(--ink-4)" class="num">${p.id}</span>
            <span style="margin-left:auto">${bd(tone, 'CPI ' + p.cpi.toFixed(2), true)}</span>
          </div>
          <div style="display:flex;align-items:center;gap:10px">
            <div class="tr" style="flex:1;height:7px;border-radius:4px;background:#eef0f3;overflow:hidden;position:relative">
              <i style="display:block;height:100%;width:${cw}%;background:#cfd4dc;border-radius:4px"></i>
              <i style="display:block;height:100%;width:${Math.min(100, (p.act / p.est) * 55)}%;background:${p.cpi >= 0.95 ? '#0f6e56' : p.cpi >= 0.9 ? '#8a5208' : '#a32d2d'};border-radius:4px;position:absolute;top:0;left:0"></i>
            </div>
            <span class="num" style="font-size:11.5px;color:var(--ink-3);width:96px;text-align:right">${p.est} → ${p.act} 人天</span>
            <span class="num" style="font-size:11.5px;font-weight:600;color:${p.cpi >= 0.95 ? '#0f6e56' : '#a32d2d'};width:44px;text-align:right">${signed(over * 100, 0)}%</span>
          </div>
        </div>`;
    }).join('');

    const warn = d.warnings.map((w) => {
      const cls = w.lv === 'red' ? 'red' : w.lv === 'amber' ? 'amber' : 'blue';
      return `<div class="li">
        <div class="ic" style="background:var(--${cls === 'blue' ? 'blue' : cls}-bg);color:var(--${cls === 'blue' ? 'blue' : cls}-fg)">!</div>
        <div class="bd-2">
          <div class="t1">${w.t}</div>
          <div class="t2">${w.d}</div>
        </div>
        <div class="rt">${w.rt}</div>
      </div>`;
    }).join('');

    const chain = d.chain.map((c) => `
      <div style="display:flex;gap:10px;padding:9px 0;border-bottom:1px solid #eef0f3;align-items:flex-start">
        ${bd(c.st === 'on' ? 'teal' : 'amber', c.k, true)}
        <div style="flex:1">
          <div style="font-size:12px;color:var(--ink);font-weight:540">${c.from} → ${c.to}</div>
          <div style="font-size:11.3px;color:var(--ink-3);margin-top:1px">${c.txt}</div>
        </div>
      </div>`).join('');

    const mods = Object.entries(window.DB.modules).map(([k, m], i) => `
      <a href="#/module/${k}" style="display:block;padding:10px 11px;border:1px solid var(--line);border-radius:7px;background:#fff;transition:.12s">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
          <span class="num" style="font-size:10px;color:var(--ink-4)">${m.no}</span>
          <span style="font-size:12px;font-weight:560;color:var(--ink)">${m.name}</span>
        </div>
        <div style="font-size:10.5px;color:var(--ink-3);line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${m.judgment}</div>
      </a>`).join('');

    return `<div class="page">
      ${pageHead({
        eyebrow: '决策总览',
        pill: '演示数据',
        title: '组合健康度',
        desc: '九个模块不是九个独立看板，而是一条带反馈的链。三条回写链路任一断裂，系统就退化为事后统计工具。',
        actions: '<button class="btn">导出周报</button><button class="btn primary">新建立项</button>'
      })}
      <div class="grid g5" style="margin-bottom:14px">${d.kpis.map(kpiCard).join('')}</div>

      <div class="grid g-3-2" style="margin-bottom:14px">
        <div class="card">
          <div class="card-h"><h3>项目成本健康度</h3><span class="sub">实际 / 估算人天</span><div class="spacer"></div><span class="hint">阈值 CPI 0.95 / 0.90 / 0.85</span></div>
          ${health}
          <div style="margin-top:10px;font-size:11px;color:var(--ink-3)">灰条为估算基线，彩条为实际消耗。CPI 低于 0.85 已超多数项目毛利空间，必须上报。</div>
        </div>
        <div class="card">
          <div class="card-h"><h3>待处理预警</h3><div class="spacer"></div><span class="hint">4 条</span></div>
          <div class="list" style="margin-top:-2px">${warn}</div>
        </div>
      </div>

      <div class="grid g-3-2">
        <div class="card">
          <div class="card-h"><h3>九模块闭环</h3><div class="spacer"></div><span class="hint">点击进入模块</span></div>
          <div class="grid g3" style="gap:8px">${mods}</div>
          <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
            ${bd('teal', 'L1 成本闭环 · 已回写', true)}
            ${bd('teal', 'L2 变更闭环 · 运行中', true)}
            ${bd('amber', 'L3 产品闭环 · 2 张假设卡待回看', true)}
          </div>
        </div>
        <div class="card">
          <div class="card-h"><h3>最近活动</h3></div>
          <div class="list">${d.activity.map((a) => `
            <div class="li" style="padding:8px 0">
              <div class="ic" style="background:var(--gray-bg);color:var(--gray-fg)">${a.who.slice(0, 2)}</div>
              <div class="bd-2"><div class="t2" style="color:var(--ink-2)">${a.what}</div>
              <div class="t2" style="font-size:10.5px;margin-top:2px">${a.who} · ${a.when}</div></div>
            </div>`).join('')}</div>
        </div>
      </div>
    </div>`;
  }

  /* ============ 九模块 ============ */

  function moduleBody(key, m) {
    if (key === 'market') {
      return table(
        [{ t: '缺口', k: 'g', strong: true }, { t: '出现频次', k: 'freq', num: true },
         { t: '关联丢单（万）', k: 'lost', num: true }, { t: '补齐成本（人天）', k: 'cost', num: true },
         { t: '建议', v: (r) => bd(r.advTone, r.adv) }],
        m.gaps
      );
    }
    if (key === 'estimate') {
      return `<div class="grid g2" style="gap:18px">
        <div>
          <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:8px">估算基准表（资产）</div>
          ${table(
            [{ t: '交付模式 × 功能类型', k: 'dim', strong: true }, { t: '基线生产率', k: 'p', num: true },
             { t: '样本', k: 'n', num: true }, { t: '更新', k: 'upd' }],
            m.baseline,
            { }
          ).replace('<table class="data">', '<table class="data" style="font-size:11.8px">')}
          <div style="font-size:10.8px;color:var(--ink-3);margin-top:8px">基线单位：需求点 / 人天。样本不足的格不出基线。</div>
        </div>
        <div>
          <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:8px">相似项目召回</div>
          ${m.similar.map((s) => `
            <div style="padding:8px 0;border-bottom:1px solid #eef0f3">
              <div style="display:flex;gap:7px;align-items:baseline">
                <span style="font-size:12.3px;font-weight:560;color:var(--ink)">${s.name}</span>
                <span class="num" style="font-size:10.5px;color:var(--ink-4)">${s.id}</span>
                <span style="margin-left:auto">${bd('blue', '相似度 ' + s.sim)}</span>
              </div>
              <div style="font-size:11.3px;color:var(--ink-3);margin-top:2px">${s.why}</div>
              <div class="num" style="font-size:11.3px;color:var(--ink-2);margin-top:2px">估 ${s.est} → 实 ${s.act}（${signed(((s.act - s.est) / s.est) * 100, 0)}%）</div>
            </div>`).join('')}
        </div>
      </div>`;
    }
    if (key === 'requirement') {
      return table(
        [{ t: '编号', k: 'id', v: (r) => `<span class="mono" style="color:var(--ink-3)">${r.id}</span>` },
         { t: '需求', k: 't', strong: true }, { t: '提出方', k: 'by' },
         { t: '字段完整度', v: (r) => `<div style="display:flex;align-items:center;gap:7px">
             <div style="width:52px;height:5px;border-radius:3px;background:#eef0f3;overflow:hidden">
               <i style="display:block;height:100%;width:${(r.done / 8) * 100}%;background:${r.done >= 8 ? '#0f6e56' : r.done >= 6 ? '#8a5208' : '#a32d2d'}"></i>
             </div><span class="num" style="font-size:10.5px;color:var(--ink-3)">${r.done}/8</span></div>` },
         { t: '规模', k: 'size', num: true }, { t: '返工', k: 'rev', num: true },
         { t: '状态', v: (r) => bd(r.state === '待补充' ? 'red' : r.state === '开发中' ? 'blue' : r.state === '已验收' ? 'teal' : 'gray', r.state) }],
        m.reqs
      );
    }
    if (key === 'schedule') {
      return `<div class="grid g2" style="gap:18px">
        <div>
          <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:8px">迭代 26-S11 承诺量测算</div>
          <div style="background:#fcfcfd;border:1px solid var(--line);border-radius:7px;padding:12px 14px">
            ${m.capacity.map((c, i) => `
              <div style="display:flex;gap:10px;padding:6px 0;${i < m.capacity.length - 1 ? 'border-bottom:1px dashed #eef0f3' : ''}">
                <span style="font-size:11.5px;color:var(--ink-3);width:88px;flex:0 0 auto">${c.k}</span>
                <span style="font-size:12px;color:${c.tone === 'strong' ? 'var(--ink)' : c.tone === 'warn' ? 'var(--amber-fg)' : 'var(--ink-2)'};font-weight:${c.tone === 'strong' ? '600' : '400'}">${c.v}</span>
              </div>`).join('')}
          </div>
        </div>
        <div>
          <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:8px">近三个迭代</div>
          ${m.iters.map((it) => `
            <div style="display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid #eef0f3">
              <span class="mono" style="font-size:11.5px;color:var(--ink-2);width:56px">${it.id}</span>
              <div style="flex:1">
                <div style="height:6px;border-radius:3px;background:#eef0f3;overflow:hidden">
                  <i style="display:block;height:100%;width:${it.done ? (it.done / it.commit) * 100 : 0}%;background:${it.done ? '#1c5fa8' : '#cfd4dc'}"></i>
                </div>
              </div>
              <span class="num" style="font-size:11.5px;color:var(--ink-3);width:76px;text-align:right">${it.done || '—'} / ${it.commit}</span>
              <span style="font-size:10.8px;color:${it.rate && it.rate < 0.9 ? 'var(--red-fg)' : 'var(--ink-3)'};width:92px">${it.note || '达成'}</span>
            </div>`).join('')}
        </div>
      </div>`;
    }
    if (key === 'change') {
      const max = Math.max.apply(null, m.causes.map((c) => c.n));
      return `<div class="grid g-1-2" style="gap:18px">
        <div>
          <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:10px">返工诱因分布（8 类）</div>
          ${m.causes.map((c) => `
            <div class="bar-row" style="margin-bottom:7px">
              <span class="nm" style="width:104px;font-size:11.3px">${c.c}</span>
              <div class="tr" style="height:6px"><i style="width:${(c.n / max) * 100}%;background:${c.c.startsWith('C1') || c.c.startsWith('C3') ? '#a32d2d' : '#1b2430'};opacity:${c.c.startsWith('C1') || c.c.startsWith('C3') ? '.85' : '.75'}"></i></div>
              <span class="vl" style="width:46px;font-size:10.8px">${c.n} · ${(c.p * 100).toFixed(0)}%</span>
            </div>`).join('')}
          <div style="font-size:10.8px;color:var(--ink-3);margin-top:6px">C1 + C3 合计 53%，属需求侧可预防项。</div>
        </div>
        <div>${table(
          [{ t: '变更单', k: 'id', v: (r) => `<span class="mono" style="font-size:11px;color:var(--ink-3)">${r.id}</span>` },
           { t: '需求', k: 'req', strong: true },
           { t: '诱因', v: (r) => bd(r.cause === 'C1' || r.cause === 'C3' ? 'red' : 'gray', r.cause) },
           { t: '发起方', k: 'by' },
           { t: '人天', k: 'days', num: true },
           { t: '计价', v: (r) => bd(r.bill === '已计价' ? 'teal' : r.bill === '内部承担' ? 'amber' : 'gray', r.bill) },
           { t: '状态', k: 'st' }],
          m.changes
        )}</div>
      </div>`;
    }
    if (key === 'cost') {
      const labels = ['W40', '', 'W42', '', 'W44', '', 'W46', '', 'W48', '', 'W50', 'W51'];
      return `<div class="grid g-3-2" style="gap:18px">
        <div>
          <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:4px">组合 CPI · 近 12 周</div>
          ${lineChart(m.trend, labels, { min: 0.78, max: 1.05 })}
          <div style="display:flex;gap:14px;font-size:10.8px;color:var(--ink-3);margin-top:2px">
            <span>${bd('amber', '关注 < 0.95')}</span><span>${bd('red', '高危 < 0.85')}</span>
            <span style="margin-left:auto">EAC 为预测值，早期项目需给区间</span>
          </div>
        </div>
        <div>${table(
          [{ t: '项目', k: 'n', strong: true }, { t: 'CPI', k: 'cpi', num: true },
           { t: 'BAC', k: 'bac', num: true }, { t: 'EAC', k: 'eac', num: true },
           { t: '判断', v: (r) => bd(r.st === 'red' ? 'red' : r.st === 'amber' ? 'amber' : 'teal', r.st === 'red' ? '超支' : r.st === 'amber' ? '关注' : '健康') }],
          m.health
        )}</div>
      </div>`;
    }
    if (key === 'delivery') {
      return table(
        [{ t: '客户', k: 'c', strong: true }, { t: '配置包', k: 'pack' },
         { t: '初始化周期', k: 'cycle', num: true, v: (r) => r.cycle + ' 天' },
         { t: '配置返工', k: 'redo', num: true },
         { t: '问卷通过率', v: (r) => `<div style="display:flex;align-items:center;gap:7px;justify-content:flex-end">
             <div style="width:48px;height:5px;border-radius:3px;background:#eef0f3;overflow:hidden">
               <i style="display:block;height:100%;width:${r.pass}%;background:${r.pass >= 70 ? '#0f6e56' : '#8a5208'}"></i></div>
             <span class="num" style="font-size:10.5px;color:var(--ink-3)">${r.pass}%</span></div>` },
         { t: '状态', v: (r) => bd(r.st === '已上线' ? 'teal' : r.st === '配置中' ? 'blue' : 'red', r.st) }],
        m.impls
      );
    }
    if (key === 'support') {
      const maxv = 47;
      return `<div>
        <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:8px">模块 × 问题类型热力（本月工单数）</div>
        <div class="tbl-wrap"><table class="data">
          <thead><tr><th>模块</th>${m.heat.cols.map((c) => `<th class="num">${c}</th>`).join('')}<th class="num">合计</th></tr></thead>
          <tbody>${m.heat.rows.map((r) => {
            const sum = r.v.reduce((a, b) => a + b, 0);
            return `<tr><td class="strong">${r.m}</td>${r.v.map((v, i) => {
              const a = v / maxv;
              const bg = v === 0 ? 'transparent' : `rgba(${i === 2 ? '163,45,45' : i === 1 ? '28,95,168' : '74,85,104'},${(0.06 + a * 0.62).toFixed(2)})`;
              return `<td class="num" style="background:${bg};color:${a > 0.6 ? '#fff' : 'var(--ink-2)'};font-weight:${v > 30 ? '600' : '400'}">${v || '—'}</td>`;
            }).join('')}<td class="num strong">${sum}</td></tr>`;
          }).join('')}</tbody>
        </table></div>
        <div style="font-size:10.8px;color:var(--ink-3);margin-top:8px">深色为高密度区。判断优先级必须结合该模块使用率，不能单看咨询数。</div>
      </div>`;
    }
    if (key === 'feedback') {
      return table(
        [{ t: '主题簇', k: 't', strong: true },
         { t: '反馈条数', k: 'n', num: true },
         { t: '诉求类型', v: (r) => bd(r.type === '新增能力' ? 'violet' : r.type === '表达抱怨' ? 'gray' : 'blue', r.type) },
         { t: '涉及客户', k: 'cust', num: true },
         { t: '合同权重（万）', k: 'amt', num: true },
         { t: '处置', v: (r) => r.type === '表达抱怨' ? '<span style="font-size:11px;color:var(--ink-3)">先查监控，不立项</span>' : '<span style="font-size:11px;color:var(--blue-fg)">进入候选池</span>' }],
        m.themes
      );
    }
    return '';
  }

  function viewModule(key) {
    const m = window.DB.modules[key];
    if (!m) return viewOverview();
    return `<div class="page">
      ${pageHead({
        eyebrow: `${m.no} · 全流程模块`,
        pill: m.en,
        title: m.name,
        desc: m.judgment,
        actions: '<button class="btn">本模块口径</button><button class="btn primary">导出</button>'
      })}
      <div class="grid g4" style="margin-bottom:14px">${m.kpis.map(kpiCard).join('')}</div>
      <div class="card" style="margin-bottom:14px">
        <div class="card-h"><h3>${m.name} · 工作台</h3><div class="spacer"></div><span class="hint">数据截至 2026-10-18 10:00</span></div>
        ${moduleBody(key, m)}
      </div>
      <div class="grid g-2-1">
        <div>${aiBox(m.ai)}</div>
        <div class="note"><div><b>模块边界</b><br>${m.note}</div></div>
      </div>
    </div>`;
  }

  /* ============ 专题一：成本偏差归因 ============ */

  function viewVariance() {
    const all = window.DB.variance;
    const v = all[state.proj];
    const total = v.total;
    const deltas = v.items.map((i) => i.v);
    const labels = ['估算', '① 范围', '② 返工', '③ 效率', '④ 等待', '⑤ 基线', '实际'];

    const chips = Object.entries(all).map(([k, x]) => {
      const over = ((x.act - x.est) / x.est) * 100;
      return `<button class="chip ${state.proj === k ? 'on' : ''}" data-proj="${k}">${x.name} · ${signed(over, 0)}%</button>`;
    }).join('');

    const rows = v.items.map((i) => ({
      k: i.k,
      v: i.v,
      note: i.note,
      src: i.src === 'data_gap' ? bd('red', 'data_gap') : `<span class="mono" style="font-size:10.5px;color:var(--ink-4)">${i.src}</span>`,
      pct: total ? (i.v / total) * 100 : 0
    }));

    return `<div class="page">
      ${pageHead({
        eyebrow: 'AI 决策专题 ①',
        pill: '成本偏差归因',
        title: '不是「估不准」，是超支的结构没人拆',
        desc: '只盯估算精度最多解决 20% 的问题。系统的第一步不是建更精细的模型，是建五项分解 + 闭合校验。',
        actions: '<button class="btn">切换口径版本</button><button class="btn primary" data-action="recompute">重算分解</button>'
      })}

      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">${chips}</div>

      <div class="grid g-3-2" style="margin-bottom:14px">
        <div class="card">
          <div class="card-h">
            <h3>${v.name}</h3>
            <span class="sub">${v.client} · 合同 ${v.contract} 万</span>
            <div class="spacer"></div>
            <span class="hint num">${v.id || ''}</span>
          </div>
          <div class="grid g4" style="gap:10px;margin-bottom:12px">
            <div style="border:1px solid var(--line);border-radius:7px;padding:9px 11px">
              <div style="font-size:10.5px;color:var(--ink-3)">初始估算 E</div>
              <div class="num" style="font-size:18px;font-weight:640">${v.est}</div>
            </div>
            <div style="border:1px solid var(--line);border-radius:7px;padding:9px 11px">
              <div style="font-size:10.5px;color:var(--ink-3)">实际 A</div>
              <div class="num" style="font-size:18px;font-weight:640">${v.act}</div>
            </div>
            <div style="border:1px solid var(--line);border-radius:7px;padding:9px 11px">
              <div style="font-size:10.5px;color:var(--ink-3)">总偏差</div>
              <div class="num" style="font-size:18px;font-weight:640;color:var(--red-fg)">${signed(total, 0)}</div>
            </div>
            <div style="border:1px solid var(--line);border-radius:7px;padding:9px 11px">
              <div style="font-size:10.5px;color:var(--ink-3)">偏差率</div>
              <div class="num" style="font-size:18px;font-weight:640;color:var(--red-fg)">${signed((total / v.est) * 100, 0)}%</div>
            </div>
          </div>
          ${waterfall(v.est, deltas, labels)}
          <div style="font-size:10.8px;color:var(--ink-3);margin-top:2px">
            S = ${v.s0} 点 → S′ = ${v.s1} 点 ｜ p₀ = ${v.p0.toFixed(2)} → p₁ = ${v.p1.toFixed(3)} 点/人天
          </div>
        </div>

        <div>
          <div class="card" style="margin-bottom:14px">
            <div class="card-h"><h3>闭合校验</h3><div class="spacer"></div>${bd(v.closeRate <= 5 ? 'teal' : 'red', v.closeRate.toFixed(1) + '% / 阈值 5%', true)}</div>
            <div style="font-size:12px;color:var(--ink-2);line-height:1.7">
              |Σ 五项 − 总偏差| / 总偏差 = <b class="num">${v.closeRate.toFixed(1)}%</b>
              <br>${v.closeRate <= 5 ? '分解自洽，可将「实际/估算比」归入基准样本池。' : '分解不自洽，标注 <code>data_gap</code>，不进样本池。'}
            </div>
            ${v.gap ? `<div class="note red" style="margin-top:10px"><div><b>数据质量提示</b><br>阻塞记录仅覆盖 2 类，④ 项可信度低。残差项会吸收未记录部分，导致归因偏差。</div></div>` : ''}
          </div>
          <div class="card">
            <div class="card-h"><h3>归因结论</h3><span class="sub">系统生成</span></div>
            <p style="font-size:12.2px;color:var(--ink-2);line-height:1.75">${v.verdict}</p>
            <div class="note" style="margin-top:10px"><div>${v.extra}</div></div>
            <div style="margin-top:10px;font-size:11.5px;color:var(--ink-3)">${v.similar}</div>
          </div>
        </div>
      </div>

      <div class="grid g-3-2">
        <div class="card">
          <div class="card-h"><h3>五项分解明细</h3><div class="spacer"></div><span class="hint">占比按总偏差计</span></div>
          ${table(
            [{ t: '偏差项', k: 'k', strong: true }, { t: '人天', k: 'v', num: true },
             { t: '占总偏差', v: (r) => `<div style="display:flex;align-items:center;gap:8px;justify-content:flex-end">
                 <div style="width:90px;height:5px;border-radius:3px;background:#eef0f3;overflow:hidden">
                   <i style="display:block;height:100%;width:${Math.min(100, Math.abs(r.pct))}%;background:${r.v > 0 ? '#a32d2d' : r.v < 0 ? '#0f6e56' : '#cfd4dc'}"></i></div>
                 <span class="num" style="font-size:10.8px;color:var(--ink-3);width:34px">${r.v === 0 ? '—' : r.pct.toFixed(0) + '%'}</span></div>` },
             { t: '说明', k: 'note' }, { t: '数据源', k: 'src' }],
            rows
          )}
        </div>
        <div class="card">
          <div class="card-h"><h3>纠偏选项</h3><div class="spacer"></div><span class="hint">系统只给选项，不做取舍</span></div>
          ${v.action.map((a) => `
            <div style="padding:9px 0;border-bottom:1px solid #eef0f3">
              <div style="display:flex;gap:8px;align-items:baseline">
                <span style="font-size:12.2px;font-weight:560;color:var(--ink)">${a.k}</span>
                <span style="margin-left:auto;font-size:10.8px;color:var(--ink-3)">${a.cost}</span>
              </div>
              <div style="font-size:11.6px;color:var(--ink-2);margin-top:2px">${a.v}</div>
            </div>`).join('')}
          <div style="margin-top:10px">${aiBox({ title: '类比检索', lines: [v.similar] })}</div>
        </div>
      </div>
    </div>`;
  }

  /* ============ 专题二：需求演进线路图 ============ */

  function viewLineage() {
    const list = window.DB.lineage;
    const cur = list[state.req] || list[0];

    const tabs = list.map((r, i) => `
      <button class="chip ${i === state.req ? 'on' : ''}" data-req="${i}" style="border-radius:7px;padding:6px 11px;text-align:left">
        <span class="mono" style="font-size:10px;opacity:.7">${r.id}</span>
        <span style="margin-left:5px">${r.title}</span>
        <span style="margin-left:6px;font-size:10px;opacity:.7">×${r.count}</span>
      </button>`).join('');

    const revs = cur.revs.map((r, i) => {
      const over = r.act - r.est;
      return `<div class="rev-row">
        <div class="rev-no"><span class="nd ${i === 0 ? '' : 'hollow'}"></span>${r.no}</div>
        <div>
          <div class="body-t">${r.desc}</div>
          <div class="body-s">交付 ${r.ver} · ${r.date}${r.cause ? ' · ' + bd(r.cause.startsWith('C7') ? 'teal' : 'red', r.cause) : ' · 首版'}</div>
        </div>
        <div class="rr">估 ${r.est} / 实 ${r.act} <span style="color:${over > 0 ? 'var(--red-fg)' : 'var(--ink-3)'}">(${signed(over, 0)})</span></div>
      </div>`;
    }).join('');

    const L = cur.lesson;
    return `<div class="page">
      ${pageHead({
        eyebrow: 'AI 决策专题 ②',
        pill: '演进链 · 非状态机',
        title: '需求是一条线，不是一个点',
        desc: '状态机无法表达「同一需求分三次交付、第三次才对」。改成演进链后，才能回答：为什么返工、每次多花多少、下次怎么避免。',
        actions: '<button class="btn">经验卡库</button><button class="btn primary">新建评审</button>'
      })}

      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">${tabs}</div>

      <div class="grid g-3-2" style="margin-bottom:14px">
        <div class="card">
          <div class="card-h">
            <h3>${cur.title}</h3>
            <span class="sub mono">${cur.id} · ${cur.owner}</span>
            <div class="spacer"></div>
            ${bd('violet', '交付 ' + cur.count + ' 次', true)}
          </div>
          <div class="grid g4" style="gap:10px;margin-bottom:14px">
            <div><div style="font-size:10.5px;color:var(--ink-3)">累计估算</div><div class="num" style="font-size:17px;font-weight:640">${cur.est} 人天</div></div>
            <div><div style="font-size:10.5px;color:var(--ink-3)">累计实际</div><div class="num" style="font-size:17px;font-weight:640">${cur.act} 人天</div></div>
            <div><div style="font-size:10.5px;color:var(--ink-3)">偏差</div><div class="num" style="font-size:17px;font-weight:640;color:${cur.act > cur.est ? 'var(--red-fg)' : 'var(--teal-fg)'}">${signed(cur.act - cur.est, 0)}</div></div>
            <div><div style="font-size:10.5px;color:var(--ink-3)">跨度</div><div class="num" style="font-size:17px;font-weight:640">${cur.span}</div></div>
          </div>
          <div style="border-left:1.5px solid var(--line);margin-left:3px;padding-left:0">
            ${revs}
          </div>
          <div style="margin-top:10px;font-size:11.2px;color:var(--ink-3);padding-top:8px;border-top:1px dashed var(--line)">
            合计：估 ${cur.est} / 实际 ${cur.act}，交付 ${cur.count} 次，跨 ${cur.span}。${cur.count >= 3 ? '3 次以上属长尾，应重点复盘。' : ''}
          </div>
        </div>

        <div class="card">
          <div class="card-h"><h3>返工诱因</h3><div class="spacer"></div><span class="hint">C1–C8 分类体系</span></div>
          ${cur.revs.filter((r) => r.cause).length === 0 ? '<div class="note"><div>本条需求无返工记录。</div></div>' : ''}
          ${cur.revs.filter((r) => r.cause).map((r) => `
            <div style="padding:9px 0;border-bottom:1px solid #eef0f3">
              <div style="display:flex;align-items:center;gap:7px">
                ${bd(r.cause.startsWith('C7') ? 'teal' : 'red', r.cause)}
                <span class="num" style="margin-left:auto;font-size:11px;color:var(--ink-3)">${r.act - r.est > 0 ? '+' + (r.act - r.est) : (r.act - r.est)} 人天</span>
              </div>
              <div style="font-size:11.4px;color:var(--ink-2);margin-top:4px">${r.no}：${r.desc}</div>
            </div>`).join('')}
          <div class="note amber" style="margin-top:12px"><div><b>C7 单列规则</b><br>首版验收标准写明「本期不含 X」的，属计划内分期，不计入返工率。否则会压制合理的分期交付策略。</div></div>
        </div>
      </div>

      <div class="grid g-2-1">
        <div class="card">
          <div class="card-h"><h3>经验卡 · ${cur.id}</h3><div class="spacer"></div>
            ${bd('gray', '命中 ' + L.hit)} ${bd('blue', '采纳 ' + L.adopt)} ${bd('teal', '有效 ' + L.eff)}
          </div>
          <dl class="kv">
            <dt>触发条件</dt><dd>${L.trigger}</dd>
            <dt>现象</dt><dd>${L.phenomenon}</dd>
            <dt>根因</dt><dd>${L.root}</dd>
            <dt>预防动作</dt><dd style="color:var(--blue-fg)">${L.action}</dd>
            <dt>验证方式</dt><dd>${L.verify}</dd>
          </dl>
          <div style="margin-top:11px;display:flex;gap:6px;flex-wrap:wrap">
            ${bd('violet', '召回时机 · 评审')} ${bd('violet', '召回时机 · 变更审批')} ${bd('violet', '召回时机 · 成本评估')}
          </div>
        </div>
        <div class="note teal"><div>
          <b>淘汰机制</b><br>上线 90 天后评估：命中 ≥ 3 次且采纳率 &lt; 20%，自动降权；连续两个季度有效确认数为 0，归档。<br><br>
          <b>不做淘汰的经验库，一年后一定变成没人看的垃圾场。</b>
        </div></div>
      </div>
    </div>`;
  }

  /* ============ 专题三：版本方向决策 ============ */

  function viewVersion() {
    const d = window.DB.versionPool;
    const hk = d.hypothesis;

    const maxSum = Math.max.apply(null, d.matrix.map((r) => r.a + r.b + r.c + r.d));
    const matrixRows = d.matrix.map((r) => {
      const sum = r.a + r.b + r.c + r.d;
      const seg = (v, color) => `<i style="display:block;height:100%;width:${(v / maxSum) * 100}%;background:${color}"></i>`;
      const bPct = Math.round((r.b / sum) * 100);
      const cPct = Math.round((r.c / sum) * 100);
      let flag = '';
      if (bPct >= 60) flag = bd('blue', 'B ' + bPct + '% → 入口问题');
      else if (cPct >= 40) flag = bd('red', 'C ' + cPct + '% → 概念模型');
      else if (r.a / sum >= 0.6) flag = bd('amber', 'A 占 ' + Math.round((r.a / sum) * 100) + '% → 引导');
      else flag = bd('gray', '结构均衡');
      return `<tr>
        <td class="strong">${r.t}</td>
        <td><div style="display:flex;height:8px;border-radius:4px;overflow:hidden;background:#eef0f3;width:150px">
          ${seg(r.a, '#4a5568')}${seg(r.b, '#1c5fa8')}${seg(r.c, '#a32d2d')}${seg(r.d, '#8a5208')}
        </div></td>
        <td class="num">${r.a}/${r.b}/${r.c}/${r.d}</td>
        <td class="num">${r.use}%</td>
        <td class="num">${r.sat}</td>
        <td>${flag}</td>
      </tr>`;
    }).join('');

    // RICE
    const rows = d.pool.map((p) => ({ ...p, score: (p.reach * p.impact * p.conf) / (p.effort * p.risk) }));
    rows.sort((a, b) => (state.sortDir === 'desc' ? b[state.sortKey] - a[state.sortKey] : a[state.sortKey] - b[state.sortKey]));

    return `<div class="page">
      ${pageHead({
        eyebrow: 'AI 决策专题 ③',
        pill: '问题结构 → 版本方向',
        title: '「咨询多」不是结论，是待拆的信号',
        desc: '100 条咨询里 70 条是 A/B，正确答案是改引导和导航（2 人天）；70 条是 C，才是真正的设计问题（改文案救不了）。',
        actions: '<button class="btn">上期版本回看</button><button class="btn primary">生成候选池</button>'
      })}

      <div class="grid g-2-1" style="margin-bottom:14px">
        <div class="card">
          <div class="card-h"><h3>问题四分法</h3><span class="sub">A 不会用 / B 找不到 / C 理解错 / D 确实缺</span>
            <div class="spacer"></div>
            <div style="display:flex;gap:8px;font-size:10.5px;color:var(--ink-3)">
              <span><i style="display:inline-block;width:7px;height:7px;background:#4a5568;border-radius:2px"></i> A</span>
              <span><i style="display:inline-block;width:7px;height:7px;background:#1c5fa8;border-radius:2px"></i> B</span>
              <span><i style="display:inline-block;width:7px;height:7px;background:#a32d2d;border-radius:2px"></i> C</span>
              <span><i style="display:inline-block;width:7px;height:7px;background:#8a5208;border-radius:2px"></i> D</span>
            </div>
          </div>
          <div class="tbl-wrap"><table class="data">
            <thead><tr><th>模块</th><th>A/B/C/D 构成</th><th class="num">条数</th><th class="num">使用率</th><th class="num">满意度</th><th>判读</th></tr></thead>
            <tbody>${matrixRows}</tbody>
          </table></div>
          <div class="note amber" style="margin-top:10px"><div><b>最易误判的一行</b>：报表中心咨询 68 条为全模块最高，但使用率仅 6.2%——不是「用得多所以问得多」，而是找不到入口所以用得少。必须用「咨询率 = 咨询数 / 使用次数」，不能只看绝对数。</div></div>
        </div>

        <div class="card">
          <div class="card-h"><h3>假设卡</h3><span class="sub mono">${hk.id}</span><div class="spacer"></div>${bd('amber', '待验证', true)}</div>
          <p style="font-size:12.2px;color:var(--ink);line-height:1.7;margin-bottom:10px">${hk.claim}</p>
          <div style="font-size:10.5px;color:var(--ink-3);letter-spacing:.05em;margin-bottom:5px">支持证据</div>
          <ul style="list-style:none;font-size:11.5px;color:var(--ink-2);line-height:1.7">
            ${hk.evidence.map((e) => `<li style="padding-left:12px;position:relative;margin-bottom:3px"><span style="position:absolute;left:0;color:var(--ink-4)">·</span>${e}</li>`).join('')}
          </ul>
          <div style="font-size:10.5px;color:var(--red-fg);letter-spacing:.05em;margin:10px 0 5px">反证条件（成立则假设不成立）</div>
          <ul style="list-style:none;font-size:11.5px;color:var(--ink-2);line-height:1.7">
            ${hk.falsify.map((e) => `<li style="padding-left:12px;position:relative;margin-bottom:3px"><span style="position:absolute;left:0;color:var(--red-fg)">·</span>${e}</li>`).join('')}
          </ul>
          <div style="display:flex;gap:10px;margin-top:11px;padding-top:9px;border-top:1px dashed var(--line);font-size:11.3px;color:var(--ink-3)">
            <span>验证：${hk.method}</span><span style="margin-left:auto">成本 ${hk.cost} 人天</span>
          </div>
          <div style="font-size:11px;color:var(--ink-3);margin-top:6px">证据强度：${bd('teal', hk.strength)}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-h"><h3>版本候选池</h3><span class="sub">Score = Reach × Impact × Confidence / (Effort × Risk)</span>
          <div class="spacer"></div><span class="hint">点击表头排序</span></div>
        <div class="tbl-wrap"><table class="data">
          <thead><tr>
            <th class="sortable ${state.sortKey === 'score' ? 'sorted' : ''}" data-sort="score">Score <span class="ar">${state.sortKey === 'score' ? (state.sortDir === 'desc' ? '▼' : '▲') : '↕'}</span></th>
            <th>候选</th><th>所属模块</th>
            <th class="num sortable ${state.sortKey === 'reach' ? 'sorted' : ''}" data-sort="reach">Reach</th>
            <th class="num sortable ${state.sortKey === 'impact' ? 'sorted' : ''}" data-sort="impact">Impact</th>
            <th class="num sortable ${state.sortKey === 'conf' ? 'sorted' : ''}" data-sort="conf">Conf.</th>
            <th class="num sortable ${state.sortKey === 'effort' ? 'sorted' : ''}" data-sort="effort">Effort</th>
            <th class="num sortable ${state.sortKey === 'risk' ? 'sorted' : ''}" data-sort="risk">Risk</th>
            <th>证据</th>
            <th>建议</th>
          </tr></thead>
          <tbody>${rows.map((r, i) => `
            <tr>
              <td class="num"><span style="display:inline-block;min-width:34px;text-align:center;font-weight:660;color:${i === 0 ? 'var(--teal-fg)' : 'var(--ink)'};background:${i === 0 ? 'var(--teal-bg)' : 'transparent'};padding:1px 6px;border-radius:4px">${r.score.toFixed(2)}</span></td>
              <td class="strong">${r.t}</td>
              <td style="color:var(--ink-3)">${r.module}</td>
              <td class="num">${r.reach}</td>
              <td class="num">${r.impact}</td>
              <td class="num">${(r.conf * 100).toFixed(0)}%</td>
              <td class="num">${r.effort}</td>
              <td class="num">${r.risk}</td>
              <td style="font-size:11px;color:var(--ink-3)">${r.ev}</td>
              <td>${i === 0 ? bd('teal', '进本版') : i <= 2 ? bd('blue', '候选') : bd('gray', '下版')}</td>
            </tr>`).join('')}</tbody>
        </table></div>
        <div class="grid g3" style="margin-top:12px;gap:10px">
          <div class="note"><div><b>Risk 是本领域的必要修正</b><br>改动高逃逸率模块的 1 人天，实际代价可能是 3 人天。不加这项，排序会系统性偏向「改老模块」。</div></div>
          <div class="note"><div><b>回看机制</b><br>上线 30 天后自动检查：相关问题量是否降 ≥ 30%、新问题簇占比是否 &gt; 15%，结果写入假设卡状态。</div></div>
          <div class="note"><div><b>系统不做的事</b><br>不排最终优先级，不代替产品经理取舍。打分是素材，决策是人。</div></div>
        </div>
      </div>
    </div>`;
  }

  /* ============ 专题四：配置推导（含规则引擎） ============ */

  /* ============ 初始化配置推导（96 题问卷版）
     这里只是挂载点：渲染与推导全部在 assets/config96.js + 后端 config_engine_v4.py。
     前端不再保留第二套规则——两套实现必然漂移，会把"同问卷同结果"这条立身之本击穿。 */
  function viewConfig() {
    return '<div class="page" id="c96-root"><div class="note"><div>加载问卷模板…</div></div></div>';
  }

  /* ============ 指标字典 ============ */

  /* ============ 数据源与接入 ============ */

  const SRC_ROLE = { primary: '权威源', validate: '校验源', supplement: '补录', reference: '参照' };
  const SRC_ROLE_BD = { primary: 'teal', validate: 'blue', supplement: 'gray', reference: 'violet' };
  const SRC_STATUS = {
    online: ['teal', '在线'], degraded: ['amber', '降级'],
    offline: ['red', '离线'], unconfigured: ['gray', '待配置'], manual: ['blue', '人工批次']
  };
  const FREQ_LABEL = {
    realtime: '实时', hour: '小时', day: '日', week: '周',
    month: '月', quarter: '季', year: '年', on_demand: '按需'
  };

  function srcScoreBar(v) {
    const tone = v >= 85 ? '#0f6e56' : v >= 70 ? '#a06a1f' : '#a32d2d';
    return `<div class="bar-cell"><i style="width:${v}%;background:${tone}"></i><span>${v}</span></div>`;
  }

  function viewSources() {
    const S = window.DB.sources;
    if (!S) {
      return `<div class="page">
        ${pageHead({ eyebrow: '基础', title: '数据源与接入',
          desc: '数据源注册表由后端提供。离线演示模式下不可用。' })}
        <div class="card"><div class="note amber"><div>当前为离线模式。启动
          <span class="mono">backend/run.py</span> 后刷新即可看到 58 条源的注册表、覆盖矩阵与导入回执。</div></div></div>
      </div>`;
    }

    const sum = S.summary;
    const mx = S.matrix;
    const type = state.srcType || 'all';
    const mod = state.srcModule || null;

    const reg = S.registry.filter((s) =>
      (type === 'all' || s.type === type) && (!mod || s.module_key === mod));

    // ---- KPI
    const kpis = [
      { lab: '已登记数据源', val: sum.total, unit: ' 条',
        foot: `T1 权威源占 ${sum.t1_share}%` },
      { lab: '模块覆盖', val: sum.module_coverage.split('/')[0], unit: '/9',
        foot: '每个模块均有权威源', delta: '无缺口' },
      { lab: '异常源', val: sum.degraded.length + sum.unconfigured.length, unit: ' 条',
        foot: `${sum.degraded.length} 降级 · ${sum.unconfigured.length} 待配置`, delta: sum.degraded.length ? '需处理' : '', dir: 'down' },
      { lab: '过期源', val: sum.stale.length, unit: ' 条',
        foot: '超出承诺同步频率' },
      { lab: '已检测冲突', val: sum.conflicts.length, unit: ' 处',
        foot: '不静默覆盖，全部留痕' }
    ].map(kpiCard).join('');

    // ---- 通道定位
    const chanCards = sum.by_type.map((t) => `
      <div class="src-ch">
        <div class="src-ch-h"><b>${t.label}</b><span>${t.role}</span></div>
        <div class="src-ch-v">${t.count}<small>条</small></div>
        <div class="src-ch-f">权威源 ${t.primary} · 均分 ${t.avg_score}</div>
      </div>`).join('');

    // ---- 覆盖矩阵
    const headCells = mx.columns.map((c) =>
      `<th class="num">${c.label}<small class="mx-sub">${c.modules}/9 模块</small></th>`).join('');
    const mxRows = mx.modules.map((row) => {
      const tds = row.cells.map((c) => {
        const empty = c.count === 0;
        const cls = ['mx-cell', empty ? 'empty' : '', c.has_primary ? 'prim' : '',
          mod === row.module && type === c.type ? 'sel' : ''].join(' ');
        const label = empty ? '本模块无此通道'
          : (c.primary ? c.primary : `${c.count} 条（无权威源）`);
        return `<td class="${cls}" data-src-type="${c.type}" data-src-mod="${row.module}">
          <b>${empty ? '—' : c.count}</b><span>${label}</span></td>`;
      }).join('');
      return `<tr class="${mod === row.module ? 'mx-row-sel' : ''}">
        <td class="strong"><span class="idx">${row.no}</span>${row.name}</td>${tds}</tr>`;
    }).join('');

    // ---- 注册表
    const typeChips = [{ k: 'all', label: '全部' }].concat(S.types)
      .map((t) => `<button class="chip ${type === t.k ? 'on' : ''}" data-src-type="${t.k}">${t.label}</button>`)
      .join('');

    const regRows = reg.map((s) => {
      const st = SRC_STATUS[s.status] || ['gray', s.status];
      const probeRes = (state.probeRes || {})[s.id];
      const op = s.type === 'excel'
        ? `<span class="hint">批次校验</span>`
        : `<button class="btn xs" data-probe="${s.id}" ${state.probing === s.id ? 'disabled' : ''}>${
            state.probing === s.id ? '探测中…' : '探活'}</button>`;
      const row = `<tr class="clickable ${state.srcSel === s.id ? 'row-sel' : ''}" data-src-sel="${s.id}">
        <td class="strong">${s.name}<span class="src-id mono">${s.id}</span></td>
        <td>${bd(s.type === 'open' ? 'violet' : s.type === 'excel' ? 'amber' : s.type === 'mcp' ? 'blue' : 'teal', s.type_label)}</td>
        <td class="hint">${(mx.modules.find((m) => m.module === s.module_key) || {}).name || s.module_key}</td>
        <td>${bd(SRC_ROLE_BD[s.role], SRC_ROLE[s.role])}</td>
        <td>${bd(s.trust === 'T1' ? 'teal' : s.trust === 'T2' ? 'amber' : 'gray', s.trust)}<span class="hint"> ${s.trust_label}</span></td>
        <td class="hint">${FREQ_LABEL[s.freq] || s.freq}</td>
        <td class="hint">${s.last_sync || '—'}${s.stale ? ' <span class="stale">过期</span>' : ''}</td>
        <td>${srcScoreBar(s.score)}</td>
        <td>${bd(st[0], st[1], true)} ${probeRes
          ? bd(probeRes.state === 'ok' ? 'teal' : probeRes.state === 'not_configured' ? 'gray' : 'red',
               probeRes.short, true) : ''}</td>
        <td class="act">${op}</td>
      </tr>`;
      if (state.srcSel !== s.id) return row;
      return row + `<tr class="detail-row"><td colspan="10">
        <div class="detail">
          <div class="d-item"><label>接入对象</label><span class="mono">${s.obj}</span></div>
          <div class="d-item"><label>关键字段</label><span>${s.fields_list.join(' · ')}</span></div>
          <div class="d-item"><label>去重 / 关联键</label><span class="mono">${s.join_key || '—'}</span></div>
          ${s.mcp_tool ? `<div class="d-item"><label>MCP 工具</label><span class="mono">${s.mcp_tool}</span>
            <span class="hint">（背后系统：${s.vendor || '—'}）</span></div>` : ''}
          <div class="d-item"><label>质量规则</label><span>${s.dq_rules || '—'}</span></div>
          <div class="d-item"><label>断源降级</label><span class="warn-txt">${s.fallback || '—'}</span></div>
          <div class="d-item wide"><label>设计说明</label><span>${s.note || '—'}</span></div>
        </div></td></tr>`;
    }).join('');

    // ---- 血缘
    const defMetric = (S.metricIndex.find((m) => m.id === 'actual_days') || S.metricIndex[0] || {}).id;
    const metricSel = state.metricSel || defMetric;
    const mChips = S.metricIndex.map((m) =>
      `<button class="chip ${metricSel === m.id ? 'on' : ''}" data-metric="${m.id}">${m.name}</button>`).join('');
    const lin = (S.lineages || {})[metricSel] || { links: [] };
    const linRows = lin.links.map((l) => `
      <tr>
        <td>${bd(l.link_role === 'primary' ? 'teal' : 'blue', l.link_role === 'primary' ? '主源' : '校验源')}</td>
        <td class="strong">${l.name}</td>
        <td>${bd(l.type === 'open' ? 'violet' : l.type === 'excel' ? 'amber' : l.type === 'mcp' ? 'blue' : 'teal', l.type_label)}</td>
        <td class="hint">${l.formula_part || '—'}</td>
        <td>${srcScoreBar(l.score)}</td>
        <td>${bd(SRC_STATUS[l.status] ? SRC_STATUS[l.status][0] : 'gray', SRC_STATUS[l.status] ? SRC_STATUS[l.status][1] : l.status)}</td>
      </tr>`).join('');

    // ---- 导入
    const imp = state.imp;
    let impBody = `<div class="imp-drop" id="impDrop">
        <input type="file" id="impFile" accept=".csv,.xlsx,.xlsm" hidden>
        <b>选择 CSV / XLSX 文件</b>
        <span>模板列：项目编号 · 人天类别 · 人天 · 月份 · 说明</span>
        <span class="hint">先做逐行校验，确认后再落库；同一文件不会重复记账</span>
      </div>`;
    if (imp && imp.preview) {
      const pv = imp.preview;
      if (pv.fatal) {
        impBody = `<div class="note red"><div><b>表头校验未通过</b>：${pv.fatal}</div></div>
          <button class="btn" data-imp-clear="1">重新选择</button>`;
      } else {
        const okRows = pv.accepted.slice(0, 8).map((a) => `
          <tr><td class="num">${a.line}</td><td class="strong">${a.project}</td>
          <td>${a.category_label}</td><td class="num">${num(a.days)}</td>
          <td class="mono">${a.month}</td><td class="hint">${a.note || '—'}</td></tr>`).join('');
        const badRows = pv.rejected.map((r) => `
          <tr class="bad-row"><td class="num">${r.line}</td><td colspan="4">${r.msg}</td>
          <td class="mono hint">${(r.raw || []).join(' | ')}</td></tr>`).join('');
        const result = imp.result;
        impBody = `
          <div class="imp-stat">
            <div><b>${pv.stats.ok}</b><span>可导入</span></div>
            <div class="${pv.stats.bad ? 'warn' : ''}"><b>${pv.stats.bad}</b><span>被拒绝</span></div>
            <div><b>${num(pv.stats.days)}</b><span>人天</span></div>
            <div><b class="mono" style="font-size:12px">${pv.content_hash}</b><span>内容指纹</span></div>
          </div>
          ${pv.duplicate_of ? `<div class="note amber"><div>该文件已于批次 <b>${pv.duplicate_of}</b> 导入过，提交会被拒绝（幂等保护）。</div></div>` : ''}
          <div class="tbl-wrap" style="margin-top:10px"><table class="data">
            <thead><tr><th class="num">行号</th><th>项目</th><th>类别</th><th class="num">人天</th><th>月份</th><th>说明</th></tr></thead>
            <tbody>${okRows || '<tr><td colspan="6" class="hint">没有有效行</td></tr>'}</tbody>
          </table></div>
          ${badRows ? `<div class="tbl-wrap" style="margin-top:8px"><table class="data">
            <thead><tr><th class="num">行号</th><th colspan="4">拒绝原因</th><th>原始行</th></tr></thead>
            <tbody>${badRows}</tbody></table></div>` : ''}
          ${result ? `<div class="note teal" style="margin-top:10px"><div>
              <b>已写入批次 ${result.batch}</b>：${result.stats.ok} 行 / ${result.stats.days} 人天。
              ${(result.closure || []).map((c) =>
                `${c.name} 闭合率 ${num(c.closeRate)}%，未解释 ${signed(c.unexplained)} 人天`).join('；')}
              <br>专题①的数字已随之变化，无需手动刷新缓存。
            </div></div>` : `
            <div class="imp-act">
              <button class="btn primary" data-imp-commit="1" ${pv.stats.ok ? '' : 'disabled'}>
                ${imp.busy ? '提交中…' : `确认导入 ${pv.stats.ok} 行`}</button>
              <button class="btn" data-imp-clear="1">放弃</button>
              <span class="hint">${imp.name}</span>
            </div>`}`;
      }
    }

    const batchRows = (S.batches || []).map((b) => `
      <tr>
        <td class="mono">${b.id}</td>
        <td>${b.source_name}</td>
        <td class="hint">${b.filename}</td>
        <td class="num">${b.rows_ok}</td>
        <td class="num ${b.rows_bad ? 'warn-txt' : ''}">${b.rows_bad}</td>
        <td>${bd(b.status === 'committed' ? 'teal' : 'gray', b.status === 'committed' ? '已入库' : b.status)}</td>
        <td class="hint">${b.created_by} · ${b.created_at}</td>
      </tr>`).join('');

    return `<div class="page">
      ${pageHead({
        eyebrow: '基础 · 数据治理',
        pill: 'source_v1.0',
        title: '数据源与接入',
        desc: 'API 供事实、Excel 补断点、公开数据做标尺、MCP 是按需取数的手。一个指标只能有一个权威源，其余都是校验源。',
        actions: `<a class="btn" href="${apiOn() ? window.API.templateUrl() : '#'}" ${apiOn() ? 'target="_blank"' : 'disabled'}>下载导入模板</a>`
      })}

      <div class="grid g5" style="margin-bottom:14px">${kpis}</div>

      <div class="grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:14px">${chanCards}</div>

      <div class="card">
        <div class="card-h"><h3>业务模块 × 通道 覆盖矩阵</h3>
          <span class="hint">点单元格可下钻；空格是该模块本就没有这类数据，不是遗漏</span></div>
        <div class="tbl-wrap"><table class="data mx">
          <thead><tr><th>模块</th>${headCells}</tr></thead>
          <tbody>${mxRows}</tbody>
        </table></div>
        <div class="note" style="margin-top:10px"><div>
          需求记录、需求变更、成本执行三块没有公开数据源——需求是客户给的，变更是双方谈的，成本是自家账。
          硬凑一个"行业变更率"进来，只会变成汇报里一句正确的废话。</div></div>
      </div>

      <div class="card" style="margin-top:14px">
        <div class="card-h" style="flex-wrap:wrap;gap:8px">
          <h3>数据源注册表</h3>
          <div class="chips">${typeChips}${mod ? `<button class="chip on" data-src-mod="">模块：${(mx.modules.find((m) => m.module === mod) || {}).name} ✕</button>` : ''}</div>
          <div class="spacer"></div>
          <span class="hint">${reg.length} / ${sum.total} 条 · 点行展开</span>
        </div>
        <div class="tbl-wrap"><table class="data">
          <thead><tr><th>数据源</th><th>通道</th><th>模块</th><th>角色</th><th>信任</th>
            <th>频率</th><th>最近同步</th><th>可信度</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>${regRows || '<tr><td colspan="10" class="hint" style="padding:20px;text-align:center">该筛选下没有数据源</td></tr>'}</tbody>
        </table></div>
      </div>

      <div class="card" style="margin-top:14px">
        <div class="card-h" style="flex-wrap:wrap;gap:8px"><h3>指标血缘</h3>
          <div class="chips">${mChips}</div>
          <div class="spacer"></div>
          <span class="hint">主源出数，校验源只对账</span></div>
        <div class="tbl-wrap"><table class="data">
          <thead><tr><th>角色</th><th>数据源</th><th>通道</th><th>公式承担部分</th><th>可信度</th><th>状态</th></tr></thead>
          <tbody>${linRows || '<tr><td colspan="6" class="hint">该指标尚未登记血缘</td></tr>'}</tbody>
        </table></div>
        ${sum.conflicts.length ? `<div class="note amber" style="margin-top:10px"><div>
          <b>已检测冲突 ${sum.conflicts.length} 处</b>：${sum.conflicts.map((c) =>
            `${c.note}（${c.source_a} ${c.value_a} vs ${c.source_b} ${c.value_b}，裁决：${c.resolved_by}）`).join('；')}
          </div></div>` : ''}
      </div>

      <div class="card" style="margin-top:14px">
        <div class="card-h"><h3>Excel / CSV 导入 · 人天流水</h3>
          <span class="hint">逐行校验 · hash 幂等 · 写入后自动回算闭合率</span></div>
        ${impBody}
      </div>

      <div class="card" style="margin-top:14px">
        <div class="card-h"><h3>导入批次回执</h3><span class="hint">每一次导入都可追溯到人、时间和文件</span></div>
        <div class="tbl-wrap"><table class="data">
          <thead><tr><th>批次</th><th>数据源</th><th>文件</th><th class="num">成功</th>
            <th class="num">拒绝</th><th>状态</th><th>操作人 / 时间</th></tr></thead>
          <tbody>${batchRows || '<tr><td colspan="7" class="hint">暂无批次</td></tr>'}</tbody>
        </table></div>
      </div>
    </div>`;
  }

  function viewMetrics() {
    const cats = ['全部', '成本', '需求', '变更', '排期', '质量', '服务', '交付'];
    const q = state.metricQ.trim();
    const rows = window.DB.metrics.filter((m) =>
      (state.metricCat === '全部' || m.cat === state.metricCat) &&
      (!q || m.n.includes(q) || m.f.includes(q) || m.note.includes(q))
    );
    const catChips = cats.map((c) => `<button class="chip ${state.metricCat === c ? 'on' : ''}" data-cat="${c}">${c}</button>`).join('');

    return `<div class="page">
      ${pageHead({
        eyebrow: '基础',
        pill: 'kpi_v1.1',
        title: '指标口径字典',
        desc: '一个指标只能有一个计算位置。口径必须带版本，修改不覆盖历史——否则跨季度对比会得出错误结论。',
        actions: '<button class="btn">变更记录</button>'
      })}
      <div class="card">
        <div class="card-h" style="flex-wrap:wrap;gap:8px">
          <div class="search" style="width:240px"><span>⌕</span><input id="mq" placeholder="搜索指标 / 公式 / 说明" value="${q}"></div>
          <div class="chips" style="margin-left:4px">${catChips}</div>
          <div class="spacer"></div>
          <span class="hint">${rows.length} 个指标</span>
        </div>
        <div class="tbl-wrap"><table class="data">
          <thead><tr><th>指标</th><th>分类</th><th>公式</th><th>口径说明</th><th>数据源</th></tr></thead>
          <tbody>${rows.map((m) => `
            <tr>
              <td class="strong">${m.n}</td>
              <td>${bd('gray', m.cat)}</td>
              <td class="mono" style="color:var(--ink-2);font-size:11px">${m.f}</td>
              <td style="color:var(--ink-3);font-size:11.5px">${m.note}</td>
              <td class="mono" style="font-size:10.5px;color:var(--ink-4)">${m.src}</td>
            </tr>`).join('')}</tbody>
        </table></div>
        ${rows.length === 0 ? '<div style="padding:30px;text-align:center;color:var(--ink-3);font-size:12px">没有匹配的指标</div>' : ''}
      </div>

      <div class="card" style="margin-top:14px">
        <div class="card-h"><h3>口径争议处理 · 以「返工」为例</h3></div>
        <div class="tbl-wrap"><table class="data">
          <thead><tr><th>争议点</th><th>交付方观点</th><th>财务/管理方观点</th><th>系统处理</th></tr></thead>
          <tbody>
            <tr><td class="strong">缺陷修复算不算返工</td><td>算，都是白干</td><td>不算，属质量成本</td><td>拆成两个指标：返工占比（严格）+ 质量成本占比（含缺陷）</td></tr>
            <tr><td class="strong">客户变更导致的算不算</td><td>算，还是那些活</td><td>不算，客户付费了不亏</td><td>按 is_billable 拆分：付费变更 / 内部承担变更</td></tr>
            <tr><td class="strong">拆分后的小改算不算</td><td>算</td><td>不算，属正常迭代</td><td>以「是否改变已验收功能行为」为判定标准</td></tr>
          </tbody>
        </table></div>
        <div class="note" style="margin-top:10px"><div><b>处理规则</b>：口径不一致时不投票、不折中，而是拆指标。拆不出来的，说明业务定义本身有问题，提交口径评审会（数据 + 财务 + 交付三方）。</div></div>
      </div>
    </div>`;
  }

  /* ============ 渲染与路由 ============ */

  const TITLES = {
    overview: '总览',
    'topic-variance': '成本偏差归因',
    'topic-lineage': '需求演进线路图',
    'topic-version': '版本方向决策',
    'topic-config': '初始化配置推导',
    'llm-config': '模型设置',
    sources: '数据源与接入',
    metrics: '指标口径字典'
  };

  function resolve(hash) {
    const h = (hash || '').replace(/^#\/?/, '');
    if (!h) return { page: 'overview' };
    if (h.startsWith('module/')) return { page: 'module', key: h.split('/')[1] };
    return { page: h };
  }

  function navHtml(activeKey) {
    return window.DB.nav.map((g) => `
      <div class="nav-group">
        <div class="nav-label">${g.g}</div>
        ${g.items.map((it) => {
          const isModule = !!window.DB.modules[it.k];
          const on = (isModule ? 'module-' + it.k : it.k) === activeKey ? 'on' : '';
          const href = isModule ? `#/module/${it.k}` : `#/${it.k}`;
          const lead = it.no ? `<span class="idx">${it.no}</span>` : `<span class="dot"></span>`;
          const tail = it.tail ? `<span class="tail ${it.warn ? 'warn' : ''}">${it.tail}</span>` : '';
          return `<a class="nav-item ${on}" href="${href}">${lead}<span>${it.t}</span>${tail}</a>`;
        }).join('')}
      </div>`).join('');
  }

  function render() {
    const route = resolve(location.hash);
    const key = route.page === 'module' ? 'module-' + route.key : route.page;
    const view = $('#view');
    const scrollTop = view.scrollTop;

    $('#nav').innerHTML = navHtml(key);

    let html = '';
    let crumb = '';
    // 报价 Agent 的两个页面在 pricing.js 里自包含（含异步 mount），这里只做转发
    const pg = window.PRICING && window.PRICING[route.page];
    if (pg) {
      html = pg.render();
      crumb = pg.crumb || '';
    } else if (route.page === 'overview') {
      html = viewOverview();
      crumb = '总览';
    } else if (route.page === 'module') {
      html = viewModule(route.key);
      const m = window.DB.modules[route.key];
      crumb = m ? `全流程模块 / ${m.name}` : '全流程模块';
    } else if (route.page === 'topic-variance') {
      html = viewVariance(); crumb = 'AI 决策专题 / 成本偏差归因';
    } else if (route.page === 'topic-lineage') {
      html = viewLineage(); crumb = 'AI 决策专题 / 需求演进线路图';
    } else if (route.page === 'topic-version') {
      html = viewVersion(); crumb = 'AI 决策专题 / 版本方向决策';
    } else if (route.page === 'llm-config') {
      html = '<div class="page" id="llm-root"></div>';
      crumb = '基础 / 模型设置';
    } else if (route.page === 'topic-config') {
      html = viewConfig(); crumb = 'AI 决策专题 / 初始化配置推导';
    } else if (route.page === 'sources') {
      html = viewSources(); crumb = '基础 / 数据源与接入';
    } else if (route.page === 'metrics') {
      html = viewMetrics(); crumb = '基础 / 指标口径字典';
    } else {
      html = viewOverview(); crumb = '总览';
    }

    $('#crumb').innerHTML = `<span>软件开发全流程决策系统</span><span class="sep">/</span><b>${crumb}</b>`;
    view.innerHTML = html;
    view.scrollTop = scrollTop;
    if (pg && pg.mount) pg.mount();

    // 数据源页：文件选择与拖拽
    if (route.page === 'sources') {
      const drop = $('#impDrop');
      const inp = $('#impFile');
      const pick = (file) => {
        if (!file) return;
        state.imp = { name: file.name, file: file, preview: null, busy: false };
        render();
        window.API.ingestPreview(file)
          .then((pv) => { state.imp.preview = pv; render(); })
          .catch((e) => { toast('校验失败：' + e.message); state.imp = null; render(); });
      };
      if (drop && inp) {
        drop.addEventListener('click', (e) => { if (e.target.tagName !== 'INPUT') inp.click(); });
        inp.addEventListener('change', (e) => pick(e.target.files[0]));
        drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
        drop.addEventListener('dragleave', () => drop.classList.remove('over'));
        drop.addEventListener('drop', (e) => {
          e.preventDefault();
          drop.classList.remove('over');
          pick(e.dataTransfer.files[0]);
        });
      }
    }

    // 指标搜索框焦点保持
    if (route.page === 'metrics') {
      const inp = $('#mq');
      if (inp) {
        inp.addEventListener('input', (e) => {
          state.metricQ = e.target.value;
          render();
          const n = $('#mq');
          if (n) { n.focus(); n.setSelectionRange(n.value.length, n.value.length); }
        });
      }
    }

    // 配置推导页面由 config96.js 自行挂载（含异步拉取与局部重绘）
    if (route.page === 'llm-config' && window.LLMConfig) window.LLMConfig.afterRender();
    if (route.page === 'topic-config' && window.Config96) {
      window.Config96.afterRender();
    }
  }

  /* ============ 事件委托 ============ */

  document.addEventListener('click', (e) => {
    // 专题④ 的 96 题问卷自己处理点击（config96.js）。这里必须让路——
    // v4 的选项按钮带 data-q / data-c96，一旦被下面这套老委托命中，
    // 就会往 state.ans 里塞值、触发 render() 整页重建，还会额外打一发
    // POST /api/config/derive，表现就是"点一下问卷，页面刷新一次"。
    // 专题④ 的问卷与弹窗有自己的事件处理，全局委托不介入
    if (e.target.closest('#c96-root, #c96-modal-host')) return;

    const t = e.target.closest('[data-proj],[data-req],[data-sort],[data-tab],[data-cat],[data-action],'
      + '[data-src-type],[data-src-mod],[data-src-sel],[data-probe],[data-metric],[data-imp-commit],[data-imp-clear]');
    if (!t) return;

    // ---- 数据源页 ----
    if (t.dataset.probe) {
      if (!apiOn()) { toast('未连接后端：请先运行 backend/run.py'); return; }
      const sid = t.dataset.probe;
      state.probing = sid;
      render();
      window.API.probe(sid)
        .then((r) => {
          state.probing = null;
          state.probeRes[sid] = {
            state: r.state,
            short: r.state === 'ok' ? '探活通过'
              : r.state === 'not_configured' ? '未配置' : '探活失败',
            detail: r.detail
          };
          toast(`${sid} 探活：${r.state} · ${r.detail}`);
          render();
        })
        .catch((err) => { state.probing = null; toast('探活失败：' + err.message); render(); });
      return;
    }

    if (t.dataset.metric) { state.metricSel = t.dataset.metric; render(); return; }

    if (t.dataset.srcSel) {
      state.srcSel = state.srcSel === t.dataset.srcSel ? null : t.dataset.srcSel;
      render();
      return;
    }

    if (t.dataset.srcMod !== undefined && t.dataset.srcMod === '') {
      state.srcModule = null;
      render();
      return;
    }

    if (t.dataset.srcType !== undefined) {
      const nt = t.dataset.srcType;
      const nm = t.dataset.srcMod || null;
      // 矩阵单元格：同格再点一次取消下钻
      if (nm && state.srcType === nt && state.srcModule === nm) {
        state.srcModule = null;
      } else {
        state.srcType = nt;
        state.srcModule = nm;
      }
      state.srcSel = null;
      render();
      return;
    }

    if (t.dataset.impCommit) {
      const imp = state.imp;
      if (!imp || !imp.file) return;
      if (!apiOn()) { toast('未连接后端：请先运行 backend/run.py'); return; }
      imp.busy = true;
      render();
      window.API.ingestCommit(imp.file)
        .then(async (r) => {
          imp.busy = false;
          if (r.ok) {
            imp.result = r;
            await window.API.reload();
            toast(`已导入 ${r.stats.ok} 行 · 批次 ${r.batch}`);
          } else {
            toast(r.message || r.fatal || '导入失败');
          }
          render();
        })
        .catch((err) => { imp.busy = false; toast('导入失败：' + err.message); render(); });
      return;
    }

    if (t.dataset.impClear) { state.imp = null; render(); return; }

    if (t.dataset.action === 'recompute') {
      if (!apiOn()) { toast('未连接后端：请先运行 backend/run.py'); return; }
      const pid = state.proj;
      t.disabled = true;
      const label = t.textContent;
      t.textContent = '重算中…';
      window.API.recompute(pid)
        .then((r) => {
          toast(`已重算 ${r.project} · 闭合 ${Number(r.closure).toFixed(1)}% · 审计记录已写入（${r.audited_by}）`);
          render();
        })
        .catch((err) => { toast('重算失败：' + err.message); t.disabled = false; t.textContent = label; });
      return;
    }

    if (t.dataset.action === 'export-config') {
      if (!apiOn()) { toast('未连接后端：请先运行 backend/run.py'); return; }
      window.API.exportConfig();
      return;
    }

    if (t.dataset.proj) { state.proj = t.dataset.proj; render(); }
    else if (t.dataset.req !== undefined) { state.req = Number(t.dataset.req); render(); }
    else if (t.dataset.sort) {
      const k = t.dataset.sort;
      if (state.sortKey === k) state.sortDir = state.sortDir === 'desc' ? 'asc' : 'desc';
      else { state.sortKey = k; state.sortDir = 'desc'; }
      render();
    }
    else if (t.dataset.tab) { state.cfgTab = t.dataset.tab; render(); }
    else if (t.dataset.cat) { state.metricCat = t.dataset.cat; render(); }
  });

  // 移动端侧栏
  $('#menuBtn').addEventListener('click', () => $('#sidebar').classList.toggle('open'));

  /* ============ 启动 ============ */

  window.addEventListener('hashchange', render);

  async function boot() {
    if (!location.hash) location.hash = '#/overview';
    paintSource();
    paintUser();
    bindUserMenu();
    render();
  }

  // 等后端首屏数据到位再渲染；未连接则直接用 data.js 的离线数据
  (window.DATA_READY || Promise.resolve()).then(boot).catch(boot);
})();
