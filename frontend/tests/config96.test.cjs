/* 配置推导页 · 交互回归测试（jsdom）
 *
 * 锁死三件曾经出过事的事：
 * 1. 打开页面不自动推导（老版本一进页面就跑一遍，还会在每次 input 后重跑）；
 * 2. 在问卷里输入时**不重绘 DOM**——输入框节点不变、焦点不丢（老版本每敲一个字整页重建）；
 * 3. 点选项只就地改样式，分支没变就不重绘左栏；
 * 4. 只有点「AI 推导」才真的调后端，且推导过程事件会逐条上屏、最后落到结果。
 *
 * 跑法：NODE_PATH=<managed node_modules> node frontend/tests/config96.test.cjs
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ASSETS = path.resolve(__dirname, '..', 'assets');
let pass = 0;
const fails = [];

function ok(name, cond, extra) {
  if (cond) { pass += 1; console.log('  ✓ ' + name); }
  else { fails.push(name + (extra ? ' — ' + extra : '')); console.log('  ✗ ' + name + (extra ? ' — ' + extra : '')); }
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function waitFor(fn, ms) {
  const t0 = Date.now();
  while (Date.now() - t0 < (ms || 6000)) {
    if (fn()) return true;
    await sleep(30);
  }
  return false;
}

function fakeResult(win) {
  const ans = JSON.parse(JSON.stringify(win.Config96.state.ans));
  const configs = [
    { code: 'C01', group: 'org', k: 'company_subject', name: '公司主体', v: '示例物业', st: 'ok', src: '第 1 题', rule: 'R-ORG-01', note: '' },
    { code: 'C11', group: 'catalog', k: 'residential', name: '住宅业态', v: '不适用（本次问卷未涉及该配置）', st: 'def', src: '—', rule: 'R-NA-01', note: '' },
  ];
  return {
    rule_version: 'rule_v4.0', survey_version: 'survey_96.v1',
    configs,
    // 两条待确认：A8 影响 C01、A10 影响 C11；涉及题目都在模板里（27/33/66）
    issues: [
      { id: 'A8', title: '升级链路与关闭审批重叠', block: false,
        msg: '无人接单自动升级与预警升级按角色通知两条链路叠加。',
        q: [27, 33], affect: ['C01'], suggest: '只保留一条升级链路' },
      { id: 'A10', title: '评价时限单位口径未明确', block: false,
        msg: '第 66 题评价时限以「月」为单位，未说明是自然月还是 30 天。',
        q: [66], affect: ['C11'], suggest: '确认按 30 天折算还是自然月' },
      { id: 'M-C29', title: '节点时限表需补齐', block: false,
        msg: '第 39 题节点时限表只填了 2 行，剩余节点没有时限。',
        q: [39], affect: ['C01'], suggest: '补齐各节点超时时长' },
    ],
    answers: ans,
    flow: { nodes: [{ id: 'accept', t: '受理', src: '标准流程', alert: '', manual: false }], edges: [], branches: [], mode: '标准', note: 'x' },
    stats: { confirmed: 1, default: 1, tbd: 0, total: 2, blocking: 0, conflicts: 0, missing: 0 },
    groups: [{ k: 'org', t: '组织与基础参数', n: 1 }, { k: 'catalog', t: '报事报修类型字典', n: 1 }],
    input_hash: 'aaaa1111', output_hash: 'bbbb2222',
  };
}

function boot() {
  const dom = new JSDOM(
    '<!doctype html><html><body><div class="page" id="c96-root"></div></body></html>',
    { runScripts: 'outside-only', url: 'http://127.0.0.1:8010/#/topic-config' });
  const win = dom.window;
  const calls = { derive: 0, stream: 0, once: 0, rules: 0, imports: 0, draftSaves: 0, draftCleared: 0, llmStream: 0 };

  win.API = {
    importSurvey: async (file) => {
      calls.imports += 1;
      calls.importedName = file && file.name;
      return {
        ok: true,
        filename: (file && file.name) || 'survey.docx',
        doc_title: '报事报修配置项目需求调查问卷',
        answers: { Q1: '导入的公司', Q2: ['residential'], Q5: 'nosplit', Q7: 'multi_level' },
        report: {
          total: 105, filled: 4,
          skipped: [{ code: 'Q14', no: 14, why: '本行没有勾选项' },
                    { code: 'Q29', no: 29, why: '表格是空的' }],
          mismatch: [{ code: 'Q30b', no: 30, sim: 0.17, doc: '补充说明：', tpl: '（补充）接单范围是否限制？' }],
          inferred_unit: [{ code: 'Q76', no: 76, unit: 'day' }],
          notes: [], items: [],
        },
      };
    },
    configDeriveStream: async (ans, onEvent, overrides) => {
      calls.stream += 1;
      calls.derive += 1;
      calls.lastAns = ans;
      calls.lastOv = overrides || null;
      onEvent({ type: 'step', n: 1, total: 3, title: '读取问卷答案', detail: '归一化完成', rules: [], codes: [], ms: 0.1 });
      onEvent({ type: 'step', n: 2, total: 3, title: '组织与基础参数', detail: '6 条规则 → 1 条配置', rules: ['R-ORG-01'], codes: ['C01'], ms: 0.2 });
      onEvent({ type: 'step', n: 3, total: 3, title: '指纹固化', detail: 'aaaa/bbbb', rules: [], codes: [], ms: 0.3 });
      onEvent({ type: 'done', result: fakeResult(win), total_ms: 0.4 });
    },
    configDeriveV4: async () => { calls.once += 1; return fakeResult(win); },
    configRulesV4: async () => { calls.rules += 1; return { version: 'rule_v4.0', total: 80, rules: [] }; },
    // 现在「AI 推导」是唯一入口，默认让模型处于已配置状态；
    // 要测未配置的拦截路径，置 calls.llmOffline = true
    llmGetConfig: async () => (calls.llmOffline
      ? { online: false, model: '', provider: '', base_url: '', providers: [] }
      : { online: true, model: 'deepseek-chat', provider: 'deepseek', base_url: 'https://api.deepseek.com', providers: [] }),
    configDeriveLLMStream: async (ans, onEvent, ov) => {
      calls.llmStream += 1;
      calls.derive += 1;
      calls.llmAns = ans;
      calls.lastAns = ans;          // 与规则通道同名，两种入口都能查提交内容
      calls.lastOv = ov || null;
      if (calls.llmNoKey) {
        onEvent({ type: 'error', code: 'llm_failed',
          message: '尚未配置大模型：请到「模型设置」填写 DeepSeek API Key' });
      }
      onEvent({ type: 'step', n: 1, total: 3, title: '读取问卷答案', detail: '归一化完成', rules: [], codes: [], ms: 0.1 });
      onEvent({ type: 'step', n: 2, total: 3, title: '组织与基础参数 · 大模型', detail: '6/6 条由 deepseek-chat 生成 · 1832 ms',
        rules: ['R-ORG-01'], codes: ['C01'], sample: ['C01=示例'], llm: { model: 'deepseek-chat', ok: true, ms: 1832 }, ms: 1832 });
      onEvent({ type: 'step', n: 3, total: 3, title: '指纹固化', detail: 'aaaa/bbbb', rules: [], codes: [], ms: 0.3 });
      const r = fakeResult(win);
      r.engine = 'llm';
      r.llm = { model: 'deepseek-chat', calls: 7, ok: 7, failed: 0, tokens_in: 800, tokens_out: 400, latency_ms: 9000 };
      r.configs = r.configs.map((c) => Object.assign({}, c, { by: 'llm' }));
      onEvent({ type: 'done', result: r, total_ms: 9000 });
    },
    // 草稿通道：只存不算
    saveConfigDraft: async (ans, ov) => {
      calls.draftSaves += 1;
      calls.draftAns = JSON.parse(JSON.stringify(ans || {}));
      calls.draftOv = JSON.parse(JSON.stringify(ov || {}));
      return { ok: true, saved_at: '2026-10-19 19:45:00', answers: 0, overrides: 0 };
    },
    getConfigDraft: async () => (calls.draftSeed
      ? { exists: true, answers: calls.draftSeed, overrides: {}, saved_at: '2026-10-19 19:40:00' }
      : { exists: false }),
    clearConfigDraft: async () => { calls.draftCleared += 1; return { ok: true }; },
    configTemplateV4: async () => ({}),
  };

  win.eval(fs.readFileSync(path.join(ASSETS, 'survey96.js'), 'utf8'));
  win.eval(fs.readFileSync(path.join(ASSETS, 'config96.js'), 'utf8'));
  return { win, doc: win.document, calls };
}

function fire(win, el, type) {
  el.dispatchEvent(new win.Event(type, { bubbles: true }));
}

async function main() {
  console.log('\n[0] app.js 的全局点击委托不与问卷属性冲突');
  {
    // 曾经的 bug：app.js 的全局委托选择器含 [data-q]，问卷选项也带 data-q，
    // 于是一点选项就被 app.js 抢走 → render() 整页重建 + POST /api/config/derive。
    const app = fs.readFileSync(path.join(ASSETS, 'app.js'), 'utf8');
    const sel = /e\.target\.closest\(([\s\S]{0,400}?)\);\s*\n\s*if \(!t\) return;/.exec(app);
    ok('拿得到 app.js 的委托选择器', !!sel);
    const s = sel ? sel[1] : '';
    ok('选择器不含 [data-q]', s.indexOf('[data-q]') < 0);
    ok('选择器不含 [data-opt]', s.indexOf('[data-opt]') < 0);
    ok('全局委托对 #c96-root 与弹窗直接让路', /closest\('#c96-root, #c96-modal-host'\)\)\s*return/.test(app));
    ok('app.js 不再调用 refreshDerived', app.indexOf('refreshDerived') < 0);
    ok('app.js 不再直接 POST 推导', app.indexOf('API.derive(') < 0);
  }

  console.log('\n[1] 打开页面');
  {
    const { win, doc, calls } = boot();
    win.Config96.afterRender();
    await sleep(120);
    ok('挂载出左栏与右栏', !!doc.querySelector('#c96-left') && !!doc.querySelector('#c96-right'));
    ok('渲染出问卷部分（空答案时 18 个，S2/S4 分支题全隐）',
      doc.querySelectorAll('.c96-sech').length === 18,
      '实际 ' + doc.querySelectorAll('.c96-sech').length);
    ok('没有自动推导（stream 调用 0 次）', calls.derive === 0, '实际 ' + calls.derive);
    ok('没有走一次性接口', calls.once === 0, '实际 ' + calls.once);
    ok('右栏是「等待计算」空态', /等待计算/.test(doc.querySelector('#c96-right').textContent));
    ok('按钮文案是「AI 推导」', /^AI 推导$/.test(doc.querySelector('[data-c96="run"]').textContent.trim()),
      doc.querySelector('[data-c96="run"]').textContent.trim());
    ok('页面上不再有第二个计算按钮', !doc.querySelector('[data-c96="runllm"]'));

    console.log('\n[2] 在问卷录入框里输入');
    const q1 = doc.querySelector('[data-c96="text"][data-q="Q1"]');
    ok('找到第 1 题输入框', !!q1);
    q1.focus();
    q1.value = '示例物业';
    fire(win, q1, 'input');
    const again = doc.querySelector('[data-c96="text"][data-q="Q1"]');
    ok('输入后 DOM 节点没被替换（不重绘）', again === q1);
    ok('焦点还在输入框里（不失焦）', doc.activeElement === q1);
    ok('输入值进入 state', win.Config96.state.ans.Q1 === '示例物业');
    ok('输入不触发推导', calls.derive === 0);

    q1.value = '示例物业二期';
    fire(win, q1, 'input');
    ok('第二次输入仍不重绘', doc.querySelector('[data-c96="text"][data-q="Q1"]') === q1);
    ok('第二次输入仍在同一个框里', doc.activeElement === q1);
    ok('必答进度就地刷新（1/29）', /1\/29/.test(doc.querySelector('.c96-hintnum').textContent),
      doc.querySelector('.c96-hintnum').textContent);

    console.log('\n[3] 点单选选项');
    const opt = doc.querySelector('[data-c96="pick"][data-q="Q5"]');
    ok('找到第 5 题选项（无分支依赖）', !!opt);
    opt.click();
    const q1b = doc.querySelector('[data-c96="text"][data-q="Q1"]');
    ok('分支没变时不重绘左栏（Q1 输入框仍是原节点）', q1b === q1);
    ok('选项就地变选中态', doc.querySelector('[data-c96="pick"][data-q="Q5"]').classList.contains('on'));
    ok('点击选项不触发推导', calls.derive === 0);

    console.log('\n[3b] 点会触发分支的选项');
    const before = doc.querySelectorAll('.c96-sech').length;
    const q2opt = doc.querySelector('[data-c96="toggle"][data-q="Q2"][data-v="residential"]');
    ok('找到第 2 题业态选项', !!q2opt);
    q2opt.click();
    ok('业态勾上后分支题出现（部分数变化）', doc.querySelectorAll('.c96-sech').length > before,
      before + ' → ' + doc.querySelectorAll('.c96-sech').length);
    ok('重绘后已填的答案没丢', doc.querySelector('[data-c96="text"][data-q="Q1"]').value === '示例物业二期');
    ok('勾选业态也不触发推导', calls.derive === 0);

    console.log('\n[4] 点「确认计算」');
    const runBtn = doc.querySelector('[data-c96="run"]');
    ok('点之前按钮是「AI 推导」', /AI 推导/.test(runBtn.textContent), runBtn.textContent);
    runBtn.click();
    await sleep(60);
    const agent = doc.querySelector('.c96-agent');
    ok('右栏切成 Agent 推导面板', !!agent);
    ok('面板里出现步骤行', doc.querySelectorAll('.c96-agstep').length >= 1);
    const first = doc.querySelector('.c96-agstep');
    ok('步骤标题不是 undefined', !/undefined/.test(first.textContent), first.textContent.slice(0, 40));
    ok('步骤带真实毫秒数', /ms/.test(first.textContent));

    const done = await waitFor(() => win.Config96.state.phase === 'idle' && !!win.Config96.state.result, 8000);
    ok('推导结束并落到结果', done);
    ok('只调了一次流式接口', calls.derive === 1, '实际 ' + calls.derive);
    ok('结果渲染出配置清单', doc.querySelectorAll('.c96-cfg').length === 2,
      '实际 ' + doc.querySelectorAll('.c96-cfg').length);
    ok('推导结束后按钮回到「AI 推导」', /^AI 推导$/.test(doc.querySelector('[data-c96="run"]').textContent.trim()),
      doc.querySelector('[data-c96="run"]').textContent);
    ok('顶栏显示已计算耗时', /已计算/.test(doc.querySelector('#c96-acts').textContent));
    ok('推导过程页签保留 3 步', /推导过程/.test(doc.querySelector('#c96-right').textContent)
      && win.Config96.state.steps.length === 3, String(win.Config96.state.steps.length));

    console.log('\n[5] 计算后再改答案');
    const q1c = doc.querySelector('[data-c96="text"][data-q="Q1"]');
    q1c.focus();
    q1c.value = '示例物业三期';
    fire(win, q1c, 'input');
    ok('改答案不自动重算', calls.derive === 1, '实际 ' + calls.derive);
    ok('按钮提示「AI 推导 (1)」', /AI 推导 \(1\)/.test(doc.querySelector('[data-c96="run"]').textContent),
      doc.querySelector('[data-c96="run"]').textContent);
    ok('顶栏提示 1 项改动未计算', /1 项改动未计算/.test(doc.querySelector('#c96-acts').textContent));
    ok('改答案时结果还在（没被清空）', doc.querySelectorAll('.c96-cfg').length === 2);
  }

  console.log('\n[6] 表格题：真才实料的单元格，不是文本框');
  {
    const { win, doc, calls } = boot();
    win.Config96.afterRender();
    await sleep(120);

    const q3 = doc.querySelector('.c96-q[data-qq="Q3"]');
    ok('第 3 题渲染出表格', !!q3 && !!q3.querySelector('.c96-tbl'));
    ok('不再有 textarea 粘贴框', doc.querySelectorAll('textarea').length === 0,
      '实际 ' + doc.querySelectorAll('textarea').length);
    const heads = [].map.call(q3.querySelectorAll('thead th'), (th) => th.textContent.trim());
    ok('表头就是问卷定义的 5 列', heads.slice(0, 5).join('|') === '所属公司/部门|项目名称|项目业态|项目户数|项目人数', heads.join('|'));
    ok('min_rows=1 → 初始 1 行', q3.querySelectorAll('tbody tr').length === 1,
      '实际 ' + q3.querySelectorAll('tbody tr').length);
    ok('每格是独立 input', q3.querySelectorAll('.c96-cell').length === 5,
      '实际 ' + q3.querySelectorAll('.c96-cell').length);

    console.log('\n[6b] 在单元格里打字');
    const c0 = q3.querySelector('.c96-cell[data-r="0"][data-c="0"]');
    c0.focus();
    c0.value = '南通示例物业物业服务';
    fire(win, c0, 'input');
    ok('值进了 state', (win.Config96.state.ans.Q3 || [])[0]['所属公司/部门'] === '南通示例物业物业服务',
      JSON.stringify(win.Config96.state.ans.Q3));
    ok('输入时单元格没被重绘（同一节点）', q3.querySelector('.c96-cell[data-r="0"][data-c="0"]') === c0);
    ok('焦点没丢', doc.activeElement === c0);
    ok('在最后一行落笔后自动补一行', q3.querySelectorAll('tbody tr').length === 2,
      '实际 ' + q3.querySelectorAll('tbody tr').length);
    ok('补行后行数提示跟着变', /共\s*2\s*行/.test(q3.querySelector('.c96-tbln').textContent),
      q3.querySelector('.c96-tbln').textContent);
    ok('表格题计入必答进度（只填了这道 → 1/29）', /1\/29/.test(doc.querySelector('.c96-hintnum').textContent),
      doc.querySelector('.c96-hintnum').textContent);

    console.log('\n[6c] 补行 / 删行 / 序号重排');
    const addBtn = q3.querySelector('[data-c96="rowadd"]');
    addBtn.click();
    ok('「+ 添加一行」后 3 行', q3.querySelectorAll('tbody tr').length === 3,
      '实际 ' + q3.querySelectorAll('tbody tr').length);
    const del = q3.querySelector('tbody tr:last-child [data-c96="rowdel"]');
    del.click();
    ok('删行后回 2 行', q3.querySelectorAll('tbody tr').length === 2,
      '实际 ' + q3.querySelectorAll('tbody tr').length);
    ok('删行不影响已填内容',
      (win.Config96.state.ans.Q3 || [])[0]['所属公司/部门'] === '南通示例物业物业服务');

    console.log('\n[6d] 序号列是只读的');
    // 展开第 8 部分（S8 默认收起），Q39 的列是 序号 / 节点名称 / 超时时长
    doc.querySelector('.c96-sech[data-k="S8"]').click();
    const q39 = doc.querySelector('.c96-q[data-qq="Q39"]');
    ok('第 39 题渲染出表格', !!q39 && !!q39.querySelector('.c96-tbl'));
    ok('序号列没有输入框（列 0 只读）',
      !q39.querySelector('.c96-cell[data-c="0"]'));
    ok('序号列显示行号', q39.querySelector('.c96-seq').textContent.trim() === '1',
      q39.querySelector('.c96-seq').textContent);
    ok('其余两列可输入', !!q39.querySelector('.c96-cell[data-c="1"]') && !!q39.querySelector('.c96-cell[data-c="2"]'));

    console.log('\n[6e] Enter 跳到下一行同列');
    const nameCell = q39.querySelector('.c96-cell[data-r="0"][data-c="1"]');
    nameCell.focus();
    nameCell.value = '待接收';
    fire(win, nameCell, 'input');
    const kd = new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
    nameCell.dispatchEvent(kd);
    const nextCell = doc.activeElement;
    ok('焦点落到下一行同一列', nextCell && nextCell.dataset.c === '1' && nextCell.dataset.r === '1',
      nextCell ? ('c=' + nextCell.dataset.c + ',r=' + nextCell.dataset.r) : 'null');

    console.log('\n[6f] 提交前裁掉空行');
    // 回到 Q3，多留两条空行
    doc.querySelector('.c96-sech[data-k="S1"]').click();
    const add2 = doc.querySelector('.c96-q[data-qq="Q3"] [data-c96="rowadd"]');
    add2.click();
    add2.click();
    ok('界面上留了空行', doc.querySelectorAll('.c96-q[data-qq="Q3"] tbody tr').length >= 3);
    doc.querySelector('[data-c96="run"]').click();
    await sleep(60);
    const sent = calls.lastAns || {};
    ok('提交的 Q3 只有 1 条实质行', Array.isArray(sent.Q3) && sent.Q3.length === 1,
      JSON.stringify(sent.Q3));
    ok('空行没被当成已答', !sent.Q3 || sent.Q3.every((r) => Object.keys(r).length > 0), JSON.stringify(sent.Q3));
    ok('只填了内容的列才提交', sent.Q3 && sent.Q3[0]['项目名称'] === undefined && sent.Q3[0]['所属公司/部门'],
      JSON.stringify(sent.Q3 && sent.Q3[0]));
    await waitFor(() => win.Config96.state.phase === 'idle', 8000);
  }

  console.log('\n[6g] 表头多两个空格的旧数据也要能显示');
  {
    // docx 里的表头是「服务项  /  工单类型」，问卷模板是「服务项 / 工单类型」，
    // 严格按列名取值会渲染成空白 —— 曾真实发生过。
    const { win, doc } = boot();
    win.Config96.afterRender();
    await sleep(120);
    doc.querySelector('[data-c96="demo"]').click();
    await waitFor(() => win.Config96.state.phase === 'idle', 6000);
    doc.querySelector('.c96-sech[data-k="S18"]').click();
    const q86 = doc.querySelector('.c96-q[data-qq="Q86"]');
    ok('第 86 题渲染出 5 行表格', q86 && q86.querySelectorAll('tbody tr').length === 5,
      q86 ? String(q86.querySelectorAll('tbody tr').length) : 'no table');
    const first = q86 && q86.querySelector('.c96-cell[data-r="0"][data-c="1"]');
    ok('服务项列读到了值（列名多空格也能匹配）', !!first && /强电维修/.test(first.value),
      first ? first.value : 'no cell');
    const fee = q86 && q86.querySelector('.c96-cell[data-r="0"][data-c="2"]');
    ok('收费标准列也正常', !!fee && /30 元\/次/.test(fee.value), fee ? fee.value : 'no cell');
  }

  console.log('\n[7] 导入问卷：只填表单，不跑规则');
  {
    const { win, doc, calls } = boot();
    win.Config96.afterRender();
    await sleep(120);

    ok('顶栏有「导入问卷」按钮', !!doc.querySelector('[data-c96="import"]'));
    doc.querySelector('[data-c96="import"]').click();
    const inp = doc.getElementById('c96-filepick');
    ok('点按钮弹出文件选择（限定 .docx）', !!inp && /\.docx/.test(inp.accept || ''));

    const file = new win.File(['fake'], '客户问卷.docx',
      { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
    Object.defineProperty(inp, 'files', { value: [file], configurable: true });
    fire(win, inp, 'change');
    ok('导入中提示条先出现', /正在解析/.test(doc.querySelector('#c96-left').textContent)
      || calls.imports === 1);

    await waitFor(() => win.Config96.state.ans.Q1 === '导入的公司', 4000);
    ok('答案被写进 state', win.Config96.state.ans.Q1 === '导入的公司',
      JSON.stringify(win.Config96.state.ans));
    ok('多选题也进来了', JSON.stringify(win.Config96.state.ans.Q2) === '["residential"]');
    ok('表单输入框已回填', doc.querySelector('[data-c96="text"][data-q="Q1"]').value === '导入的公司',
      doc.querySelector('[data-c96="text"][data-q="Q1"]').value);

    // 这条是本次需求的核心：导入不等于计算
    ok('导入没有触发任何推导请求', calls.derive === 0, '实际 ' + calls.derive);
    ok('导入没走一次性接口', calls.once === 0, '实际 ' + calls.once);
    ok('右栏仍是未计算空态（没有偷偷算）',
      /等待计算/.test(doc.querySelector('#c96-right').textContent));

    const note = doc.querySelector('.c96-imp');
    ok('左栏顶部有导入结果条', !!note && note.classList.contains('ok'));
    ok('结果条写明答了多少项', /4 \/ 105/.test(note.textContent), note.textContent.slice(0, 60));
    ok('结果条提醒「没有跑推导」', /没有跑推导/.test(note.textContent));
    ok('结果条列出未识别题号', /14/.test(note.textContent) && /29/.test(note.textContent));
    ok('结果条列出题干不一致', /30/.test(note.textContent));
    ok('结果条提示单位异常', /Q76/.test(note.textContent) || /76/.test(note.textContent));

    doc.querySelector('.c96-impclose').click();
    ok('关闭按钮能收起提示条', !doc.querySelector('.c96-imp'));
    ok('关闭提示不影响已填答案', win.Config96.state.ans.Q1 === '导入的公司');
  }

  console.log('\n[7b] 导入 docx 以外的文件');
  {
    const { win, doc, calls } = boot();
    win.Config96.afterRender();
    await sleep(120);
    doc.querySelector('[data-c96="import"]').click();
    const inp = doc.getElementById('c96-filepick');
    const pdf = new win.File(['x'], '问卷.pdf', { type: 'application/pdf' });
    Object.defineProperty(inp, 'files', { value: [pdf], configurable: true });
    fire(win, inp, 'change');
    await sleep(60);
    ok('非 docx 直接报错，不发请求', calls.imports === 0, '实际 ' + calls.imports);
    const note = doc.querySelector('.c96-imp.bad');
    ok('显示失败提示条', !!note);
    ok('提示里说明只支持 docx', /docx/.test(note.textContent), note.textContent.slice(0, 50));
    ok('失败时没有清空已有答案之外的东西', win.Config96.state.result === null);
  }

  console.log('\n[8] 待确认弹窗：看详情 + 就地改答案');
  {
    const { win, doc, calls } = boot();
    win.Config96.afterRender();
    await sleep(120);
    doc.querySelector('[data-c96="run"]').click();
    await waitFor(() => win.Config96.state.phase === 'idle' && !!win.Config96.state.result, 8000);

    // 切到待确认页
    doc.querySelector('[data-c96="tab"][data-k="issues"]').click();
    const rows = doc.querySelectorAll('.c96-issue');
    ok('待确认列表渲染出 3 条', rows.length === 3, '实际 ' + rows.length);
    ok('列表项可点（带 issueopen）', rows[0].getAttribute('data-c96') === 'issueopen');

    rows[0].click();
    const mdl = doc.querySelector('#c96-modal-host .c96-mdl');
    ok('弹出正经弹窗（不再是 alert）', !!mdl);
    ok('弹窗标题是冲突标题', /升级链路与关闭审批重叠/.test(mdl.textContent));
    ok('弹窗显示冲突编号', /A8/.test(mdl.textContent));
    ok('弹窗显示问题说明', /两条链路叠加/.test(mdl.textContent));
    ok('弹窗显示建议', /只保留一条升级链路/.test(mdl.textContent));
    ok('弹窗列出受影响配置', !!mdl.querySelector('.c96-affi'));

    const eqs = mdl.querySelectorAll('.c96-eq');
    ok('涉及题目渲染成可编辑控件（27 主问+追问、33，共 3 块）', eqs.length === 3, '实际 ' + eqs.length);
    ok('题目带选项按钮', mdl.querySelectorAll('.c96-eopt').length > 0);
    ok('底栏初始是「还没改动」', /还没改动/.test(mdl.textContent));
    ok('保存按钮初始禁用', mdl.querySelector('[data-c96m="save"]').disabled);

    // 改第 27 题的答案
    const opt = mdl.querySelector('.c96-eopt[data-q="Q27"]');
    ok('找到第 27 题选项', !!opt);
    opt.click();
    const mdl2 = doc.querySelector('#c96-modal-host .c96-mdl');
    ok('改完底栏变成「已改动 1 处」', /已改动 <b>1<\/b> 处|已改动 1 处/.test(mdl2.querySelector('.c96-mnote').innerHTML),
      mdl2.querySelector('.c96-mnote').textContent);
    ok('保存按钮启用', !mdl2.querySelector('[data-c96m="save"]').disabled);
    ok('改过的题目有「已改」标记', !!mdl2.querySelector('.c96-chgflag'));

    const before = calls.derive;
    mdl2.querySelector('[data-c96m="save"]').click();
    await sleep(80);
    ok('保存后弹窗关闭', !doc.querySelector('#c96-modal-host .c96-mdl'));
    ok('改的答案写回 state', !!win.Config96.state.ans.Q27, JSON.stringify(win.Config96.state.ans.Q27));
    ok('保存触发一次重新计算', calls.derive === before + 1, '实际 ' + calls.derive);
    await waitFor(() => win.Config96.state.phase === 'idle', 8000);
  }

  console.log('\n[8b] 弹窗里直接人工确认配置值');
  {
    const { win, doc, calls } = boot();
    win.Config96.afterRender();
    await sleep(120);
    doc.querySelector('[data-c96="run"]').click();
    await waitFor(() => win.Config96.state.phase === 'idle' && !!win.Config96.state.result, 8000);
    doc.querySelector('[data-c96="tab"][data-k="issues"]').click();

    // A10 只影响 C11，弹窗里应有一条人工确认输入
    doc.querySelectorAll('.c96-issue')[1].click();
    const mdl = doc.querySelector('#c96-modal-host .c96-mdl');
    const ov = mdl.querySelector('[data-c96m="ov"]');
    ok('弹窗提供人工确认输入框', !!ov);
    ok('人工确认对象是 C11', ov.dataset.code === 'C11', ov.dataset.code);

    // 只填值不填理由 → 不允许保存
    ov.value = '按 30 天折算';
    fire(win, ov, 'input');
    mdl.querySelector('[data-c96m="save"]').click();
    await sleep(60);
    const m2 = doc.querySelector('#c96-modal-host .c96-mdl');
    ok('没写理由时弹窗不关', !!m2);
    ok('底栏提示补理由', /没写理由/.test(m2.querySelector('.c96-mnote').textContent),
      m2.querySelector('.c96-mnote').textContent);

    // 补上理由再保存
    const why = m2.querySelector('[data-c96m="ovwhy"]');
    why.value = '客户确认按 30 天折算';
    fire(win, why, 'input');
    const before = calls.derive;
    m2.querySelector('[data-c96m="save"]').click();
    await sleep(80);
    ok('弹窗关闭', !doc.querySelector('#c96-modal-host .c96-mdl'));
    ok('人工确认随推导提交', !!calls.lastOv && calls.lastOv.C11
      && calls.lastOv.C11.v === '按 30 天折算', JSON.stringify(calls.lastOv));
    ok('确认值带上理由', calls.lastOv && calls.lastOv.C11.why === '客户确认按 30 天折算');
    ok('确认值带上时间戳', !!(calls.lastOv && calls.lastOv.C11.at));
    ok('重新算了一次', calls.derive === before + 1);
    await waitFor(() => win.Config96.state.phase === 'idle', 8000);
  }

  console.log('\n[8c] 配置清单点开也能看详情（替代原来的 alert）');
  {
    const { win, doc } = boot();
    win.Config96.afterRender();
    await sleep(120);
    doc.querySelector('[data-c96="run"]').click();
    await waitFor(() => win.Config96.state.phase === 'idle' && !!win.Config96.state.result, 8000);

    const row = doc.querySelector('.c96-cfg[data-code="C01"]');
    ok('配置清单可点', !!row);
    row.click();
    const mdl = doc.querySelector('#c96-modal-host .c96-mdl');
    ok('弹出配置详情弹窗', !!mdl);
    ok('显示配置编号与名称', /C01/.test(mdl.textContent) && /公司主体/.test(mdl.textContent));
    ok('显示来源与规则', /来源/.test(mdl.textContent) && /R-ORG-01/.test(mdl.textContent));
    ok('显示关联待确认 A8', /A8/.test(mdl.textContent));

    // 从配置弹窗跳到同类冲突
    const rel = mdl.querySelector('[data-c96m="issue"]');
    if (rel) {
      rel.click();
      ok('可从配置跳进冲突处理', /升级链路/.test(doc.querySelector('.c96-mdl').textContent));
    } else { ok('可从配置跳进冲突处理', false, '没有关联待确认条目'); }

    // 关闭
    doc.querySelector('.c96-mx').click();
    ok('关闭按钮生效', !doc.querySelector('#c96-modal-host .c96-mdl'));
  }

  console.log('\n[8d] 「定位到原题」');
  {
    const { win, doc } = boot();
    win.Config96.afterRender();
    await sleep(120);
    doc.querySelector('[data-c96="run"]').click();
    await waitFor(() => win.Config96.state.phase === 'idle' && !!win.Config96.state.result, 8000);
    doc.querySelector('[data-c96="tab"][data-k="issues"]').click();
    doc.querySelectorAll('.c96-issue')[0].click();
    const mdl = doc.querySelector('#c96-modal-host .c96-mdl');
    const btn33 = mdl.querySelector('[data-c96m="gotoq"][data-no="33"]');
    ok('弹窗里有「定位到原题」', !!btn33);
    btn33.click();
    await sleep(250);
    ok('题目可见时：弹窗关闭并跳过去', !doc.querySelector('#c96-modal-host .c96-mdl'));
    ok('问卷展开到该题所在部分', !!doc.querySelector('.c96-q[data-qq="Q33"]'),
      'Q33 未展开');

    // 第 27 题被分支挡住（还没选「需要自定义流程」）
    doc.querySelector('[data-c96="tab"][data-k="issues"]').click();
    doc.querySelectorAll('.c96-issue')[0].click();
    const m2 = doc.querySelector('#c96-modal-host .c96-mdl');
    const btn27 = m2.querySelector('[data-c96m="gotoq"][data-no="27"]');
    ok('第 27 题在前置未满足时不可见', !doc.querySelector('.c96-q[data-qq="Q27"]'));
    btn27.click();
    await sleep(200);
    ok('不可见时不假装跳过去（弹窗不关）', !!doc.querySelector('#c96-modal-host .c96-mdl'));
    ok('弹窗说明该题不适用并提示先改前置题',
      /不适用/.test(doc.querySelector('.c96-mnote').textContent),
      doc.querySelector('.c96-mnote').textContent);
  }

  console.log('\n[9] 弹窗「仅保存」：存下来，但不计算');
  {
    const { win, doc, calls } = boot();
    win.Config96.afterRender();
    await sleep(120);
    doc.querySelector('[data-c96="run"]').click();
    await waitFor(() => win.Config96.state.phase === 'idle' && !!win.Config96.state.result, 8000);
    const streamsAfterRun = calls.derive;

    doc.querySelector('[data-c96="tab"][data-k="issues"]').click();
    doc.querySelectorAll('.c96-issue')[0].click();
    const mdl = doc.querySelector('#c96-modal-host .c96-mdl');
    ok('弹窗里有两个保存按钮', !!mdl.querySelector('[data-c96m="save"]')
      && !!mdl.querySelector('[data-c96m="saveonly"]'));
    ok('「仅保存」与「保存并 AI 推导」文案区分清楚',
      /仅保存/.test(mdl.querySelector('[data-c96m="saveonly"]').textContent)
      && /保存并 AI 推导/.test(mdl.querySelector('[data-c96m="save"]').textContent));
    ok('未改动时两个保存按钮都禁用',
      mdl.querySelector('[data-c96m="saveonly"]').disabled
      && mdl.querySelector('[data-c96m="save"]').disabled);

    // 改一处答案，两个按钮都该启用
    mdl.querySelector('.c96-eopt[data-q="Q27"]').click();
    const m2 = doc.querySelector('#c96-modal-host .c96-mdl');
    ok('改动后两个按钮都启用',
      !m2.querySelector('[data-c96m="saveonly"]').disabled
      && !m2.querySelector('[data-c96m="save"]').disabled);

    const beforeStream = calls.derive;
    m2.querySelector('[data-c96m="saveonly"]').click();
    await sleep(120);

    ok('仅保存后弹窗关闭', !doc.querySelector('#c96-modal-host .c96-mdl'));
    ok('调了一次草稿接口', calls.draftSaves === 1, '实际 ' + calls.draftSaves);
    ok('草稿里带着改后的答案', calls.draftAns && !!calls.draftAns.Q27,
      JSON.stringify(calls.draftAns && calls.draftAns.Q27));
    // 核心断言：只保存，不计算
    ok('没有触发推导（推导次数没变）', calls.derive === beforeStream, '实际 ' + calls.derive);
    ok('没有走一次性接口', calls.once === 0);
    ok('答案已写进 state', !!win.Config96.state.ans.Q27);
    ok('顶栏显示「草稿 HH:MM 已存」', /草稿 \d\d:\d\d 已存/.test(doc.querySelector('#c96-acts').textContent),
      doc.querySelector('#c96-acts').textContent.slice(0, 40));
    ok('原结果还在（没被清空）', !!win.Config96.state.result);
    ok('按钮变成「AI 推导 (N)」', /AI 推导 \(\d+\)/.test(doc.querySelector('[data-c96="run"]').textContent),
      doc.querySelector('[data-c96="run"]').textContent);
    ok('第一次推导之后只算过那一次', calls.derive === streamsAfterRun);
  }

  console.log('\n[9b] 打开页面能捡回草稿，但不会自动算');
  {
    const { win, doc, calls } = boot();
    calls.draftSeed = { Q1: '草稿里的公司', Q5: 'nosplit' };
    win.Config96.afterRender();
    await waitFor(() => win.Config96.state.ans.Q1 === '草稿里的公司', 3000);
    ok('草稿被恢复到 state', win.Config96.state.ans.Q1 === '草稿里的公司',
      JSON.stringify(win.Config96.state.ans));
    ok('表单同步显示草稿值',
      doc.querySelector('[data-c96="text"][data-q="Q1"]').value === '草稿里的公司');
    ok('恢复草稿不自动推导', calls.derive === 0, '实际 ' + calls.derive);
    ok('顶栏提示草稿已存未计算', /草稿/.test(doc.querySelector('#c96-acts').textContent));
    ok('右栏仍是未计算空态',
      /等待计算/.test(doc.querySelector('#c96-right').textContent));
  }

  console.log('\n[9c] 计算后草稿作废 / 清空重填清草稿');
  {
    const { win, doc, calls } = boot();
    win.Config96.afterRender();
    await sleep(120);
    doc.querySelector('[data-c96="run"]').click();
    await waitFor(() => win.Config96.state.phase === 'idle' && !!win.Config96.state.result, 8000);
    doc.querySelector('[data-c96="tab"][data-k="issues"]').click();
    doc.querySelectorAll('.c96-issue')[0].click();
    doc.querySelector('.c96-mdl .c96-eopt[data-q="Q27"]').click();
    doc.querySelector('[data-c96m="saveonly"]').click();
    await sleep(120);
    ok('存过草稿', calls.draftSaves === 1 && !!win.Config96.state.savedAt);

    // 再点确认计算 → 草稿应作废
    doc.querySelector('[data-c96="run"]').click();
    await waitFor(() => win.Config96.state.phase === 'idle', 8000);
    ok('计算后草稿标记清空', win.Config96.state.savedAt === null);
    ok('计算后调用了一次清除草稿', calls.draftCleared >= 1, '实际 ' + calls.draftCleared);

    const before = calls.draftCleared;
    doc.querySelector('[data-c96="reset"]').click();
    await sleep(60);
    ok('清空重填也清草稿', calls.draftCleared > before, '实际 ' + calls.draftCleared);
    ok('清空后答案为空', Object.keys(win.Config96.state.ans).length === 0);
  }

  console.log('\n[10] AI 推导入口（唯一计算入口）');
  {
    const { win, doc, calls } = boot();
    calls.llmOffline = true;                 // 模拟「还没配 DeepSeek Key」
    win.Config96.afterRender();
    await sleep(150);

    const btns = doc.querySelectorAll('#c96-acts [data-c96="run"]');
    const btn = btns[0];
    ok('顶栏只有一个计算按钮', btns.length === 1, '实际 ' + btns.length);
    ok('按钮就是「AI 推导」', !!btn && /AI 推导/.test(btn.textContent), btn && btn.textContent);
    ok('没有「重新计算 / 确认计算」按钮',
      !/重新计算|确认计算/.test(doc.querySelector('#c96-acts').textContent),
      doc.querySelector('#c96-acts').textContent);
    ok('未配置时按钮不置灰（点了给明确提示，不做死按钮）', !btn.disabled);
    ok('未配置原因写在 title 上', /模型设置/.test(btn.getAttribute('title') || ''),
      btn.getAttribute('title'));

    // 点了不能偷偷走规则通道，但必须把原因显示出来
    btn.click();
    await sleep(80);
    ok('未配置时点了不推导', calls.derive === 0 && calls.llmStream === 0 && calls.stream === 0,
      `derive=${calls.derive} llm=${calls.llmStream} rule=${calls.stream}`);
    ok('未配置的提示真的显示在页面上',
      /模型设置/.test((doc.querySelector('#c96-err') || doc.body).textContent),
      (doc.querySelector('#c96-err') || doc.body).textContent.slice(0, 60));
    ok('提示不影响左栏输入（不整页重绘）',
      !!doc.querySelector('[data-c96="text"][data-q="Q1"]'));
  }

  console.log('\n[10b] 配好模型后点 AI 推导');
  {
    const { win, doc, calls } = boot();
    win.Config96.afterRender();
    await waitFor(() => win.Config96.state.llmCfg && win.Config96.state.llmCfg.online, 3000);

    const btn = doc.querySelector('[data-c96="run"]');
    ok('配好后按钮可用', !btn.disabled, btn.outerHTML.slice(0, 60));
    ok('按钮说明会调大模型', /大模型/.test(btn.getAttribute('title') || ''), btn.getAttribute('title'));

    btn.click();
    await waitFor(() => win.Config96.state.phase === 'idle' && !!win.Config96.state.result, 8000);

    ok('走了 AI 推导通道', calls.llmStream === 1, '实际 ' + calls.llmStream);
    ok('没有回落到纯规则通道', calls.stream === 0, '实际 ' + calls.stream);
    ok('推导过程里出现「大模型」步骤',
      /大模型/.test(doc.querySelector('#c96-right').textContent)
      || win.Config96.state.steps.some((s) => /大模型/.test(s.title || '')));
    ok('结果标记为 AI 生成', win.Config96.state.result.engine === 'llm');
    ok('统计条显示模型名', /AI · deepseek-chat/.test(doc.querySelector('#c96-right').textContent),
      doc.querySelector('#c96-right').textContent.slice(0, 80));
    ok('配置项带 by=llm 标记',
      (win.Config96.state.result.configs || []).every((c) => c.by === 'llm'));
  }

  console.log('\n[10c] 模型报错当场回显（不等推导结束）');
  {
    const { win, doc, calls } = boot();
    calls.llmNoKey = true;                   // mock 里先吐一条 error 事件再 done
    win.Config96.afterRender();
    await sleep(120);
    doc.querySelector('[data-c96="run"]').click();
    await waitFor(() => /尚未配置|额度|失败/.test(
      (doc.querySelector('#c96-err') || doc.body).textContent), 4000);
    ok('模型错误事件立即出现在错误条',
      /尚未配置|额度|失败/.test((doc.querySelector('#c96-err') || doc.body).textContent),
      (doc.querySelector('#c96-err') || doc.body).textContent.slice(0, 80));
    await waitFor(() => win.Config96.state.phase === 'idle', 8000);
    ok('出错后仍拿到兜底结果', !!win.Config96.state.result);
  }

  console.log('\n[11] 弹窗里直接编辑表格题');
  {
    const { win, doc, calls } = boot();
    win.Config96.afterRender();
    await sleep(120);
    // 先给第 39 题塞一行数据，模拟"表格填了一半"
    win.Config96.state.ans.Q39 = [{ 节点名称: '接单', 超时时长: '30 分钟' }];
    doc.querySelector('[data-c96="run"]').click();
    await waitFor(() => win.Config96.state.phase === 'idle' && !!win.Config96.state.result, 8000);

    doc.querySelector('[data-c96="tab"][data-k="issues"]').click();
    const idx = [...doc.querySelectorAll('.c96-issue')]
      .findIndex((el) => /节点时限表/.test(el.textContent));
    ok('找到涉及表格的待确认', idx >= 0, String(idx));
    doc.querySelectorAll('.c96-issue')[idx].click();

    const mdl = doc.querySelector('#c96-modal-host .c96-mdl');
    ok('弹窗里渲染出表格（不再是「放不下」）', !!mdl.querySelector('.c96-et'));
    ok('不再出现「放不下」文案', !/放不下/.test(mdl.textContent));
    ok('表格题有可编辑单元格', mdl.querySelectorAll('.c96-ecell').length >= 2,
      String(mdl.querySelectorAll('.c96-ecell').length));
    ok('序号列是只读的（没有输入框）', !mdl.querySelector('.c96-ecell[data-c="0"]'));
    ok('已有一行的值被带进来', mdl.querySelector('.c96-ecell[data-r="0"][data-c="1"]').value === '接单',
      mdl.querySelector('.c96-ecell[data-r="0"][data-c="1"]').value);

    console.log('\n[11b] 改单元格 / 加行 / 删行');
    const cell = mdl.querySelector('.c96-ecell[data-r="0"][data-c="2"]');
    cell.focus();
    cell.value = '45 分钟';
    fire(win, cell, 'input');
    ok('单元格值写进 draft',
      JSON.stringify((win.Config96.state.modal.draft.Q39 || [])[0]) === '{"节点名称":"接单","超时时长":"45 分钟"}',
      JSON.stringify(win.Config96.state.modal.draft.Q39));
    ok('输入时不重绘（节点没被替换）',
      doc.querySelector('.c96-ecell[data-r="0"][data-c="2"]') === cell);
    ok('底栏提示已改动', /已改动/.test(doc.querySelector('.c96-mnote').textContent));

    doc.querySelector('.c96-tadd').click();
    await sleep(30);
    ok('「+ 添加一行」后变 2 行',
      doc.querySelectorAll('.c96-et tbody tr').length === 2,
      String(doc.querySelectorAll('.c96-et tbody tr').length));
    ok('新行也是空的',
      doc.querySelector('.c96-ecell[data-r="1"][data-c="1"]').value === '');

    const cell2 = doc.querySelector('.c96-ecell[data-r="1"][data-c="1"]');
    cell2.value = '处理';
    fire(win, cell2, 'input');
    const cell3 = doc.querySelector('.c96-ecell[data-r="1"][data-c="2"]');
    cell3.value = '4 小时';
    fire(win, cell3, 'input');

    // 删最后一行（保住第一行「接单」，后面要验它改的值还在）
    doc.querySelector('.c96-et tbody tr:last-child [data-c96m="tdel"]').click();
    await sleep(30);
    ok('删行后回 1 行', doc.querySelectorAll('.c96-et tbody tr').length === 1,
      String(doc.querySelectorAll('.c96-et tbody tr').length));

    doc.querySelector('.c96-tadd').click();
    await sleep(30);
    const c1 = doc.querySelector('.c96-ecell[data-r="1"][data-c="1"]');
    c1.value = '处理';
    fire(win, c1, 'input');
    const c2 = doc.querySelector('.c96-ecell[data-r="1"][data-c="2"]');
    c2.value = '4 小时';
    fire(win, c2, 'input');

    console.log('\n[11c] 保存后进 state，且空行不残留');
    const before = calls.derive;
    doc.querySelector('[data-c96m="save"]').click();
    await sleep(100);
    const saved = win.Config96.state.ans.Q39;
    ok('表格答案已保存', Array.isArray(saved) && saved.length === 2, JSON.stringify(saved));
    ok('改的值生效', saved[0]['超时时长'] === '45 分钟', JSON.stringify(saved[0]));
    ok('新增行也存下来了', saved[1]['节点名称'] === '处理' && saved[1]['超时时长'] === '4 小时',
      JSON.stringify(saved[1]));
    ok('保存后触发一次重算', calls.derive === before + 1);
    await waitFor(() => win.Config96.state.phase === 'idle', 8000);
  }

  console.log('\n' + '='.repeat(52));
  if (fails.length) {
    console.log('FAILED  ' + fails.length + ' 项 / 通过 ' + pass + ' 项');
    fails.forEach((f) => console.log('  - ' + f));
    process.exit(1);
  }
  console.log('ALL PASSED  ' + pass + ' 项');
}

main().catch((e) => { console.error(e); process.exit(1); });
