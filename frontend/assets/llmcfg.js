/* 模型设置页
 *
 * 为什么单独做一页：以前配模型要改环境变量重启服务，实施到客户现场没法这么干。
 * 这里填完点保存立即生效（后端读 DB 优先），并带连通性测试——key 错、base 错、
 * 模型名错三种情况要能当场说清是哪一种，而不是等推导跑到一半再报错。
 */
(function () {
  const S = {
    cfg: null,        // 后端返回的配置（key 已脱敏）
    form: {},         // 正在编辑的值
    testing: false,
    saving: false,
    notice: null,     // {tone, title, msg, extra}
    live: null,       // 服务端探测到的可用模型 {ok, models, base_url, error}
    probing: false,
  };

  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  const api = () => window.API || {};

  /* 非 ASCII 字符定位：HTTP header 只吃 ASCII，key 里混进中文/零宽字符时
   * 后端会拒绝，这里先把位置找出来，省得用户反复粘贴试。 */
  const badChars = (v) => {
    const out = [];
    Array.from(String(v == null ? '' : v)).forEach((ch, i) => {
      if (ch.codePointAt(0) > 127) out.push([i, ch]);
    });
    return out;
  };
  const hex4 = (ch) => ch.codePointAt(0).toString(16).toUpperCase().padStart(4, '0');

  /* 渲染用：按当前值给出 hint（重绘后红字也不会消失） */
  function hintHtml(k, tip) {
    const bad = badChars(S.form[k]);
    const head = `<div class="llm-hint${bad.length ? ' red' : ''}" data-hint="${k}"`;
    const attr = ` data-tip="${esc(tip)}"`;
    if (!bad.length) return `${head}${attr}>${esc(tip)}</div>`;
    const [i, ch] = bad[0];
    const msg = `检测到 ${bad.length} 个非 ASCII 字符（第 ${i + 1} 位 U+${hex4(ch)}），保存会被拒绝`;
    return `${head}${attr}>${esc(msg)}</div>`;
  }

  function refreshHint(k) {
    const el = document.querySelector(`[data-hint="${k}"]`);
    if (!el) return;
    const bad = badChars(S.form[k]);
    if (bad.length) {
      const [i, ch] = bad[0];
      el.className = 'llm-hint red';
      el.textContent = `检测到 ${bad.length} 个非 ASCII 字符（第 ${i + 1} 位 U+${hex4(ch)}），保存会被拒绝`;
    } else {
      el.className = 'llm-hint';
      el.textContent = el.dataset.tip || '';
    }
  }

  function providers() {
    return (S.cfg && S.cfg.providers) || [];
  }

  function curProvider() {
    return providers().filter((p) => p.k === S.form.provider)[0] || null;
  }

  /* 选了服务商就带出它的 base 与推荐模型，手填过的不覆盖 */
  function pickProvider(k) {
    S.form.provider = k;
    const p = curProvider();
    if (p) {
      S.form.base_url = p.base;
      if (p.models && p.models.length && p.models.indexOf(S.form.model) < 0) {
        S.form.model = p.models[0];
      }
    }
    paint();
  }

  function statusChip() {
    const c = S.cfg;
    if (!c) return '<span class="llm-chip">读取中…</span>';
    if (!c.online) return '<span class="llm-chip off">● 未配置</span>';
    const src = c.source === 'db' ? '页面配置' : (c.source === 'env' ? '环境变量' : '');
    return `<span class="llm-chip on">● ${esc(c.model)}</span>
      <span class="llm-src">${esc(c.base_url)}${src ? ' · ' + src : ''}</span>`;
  }

  /* 探测结果只对同一个 base 有效，切了服务商就当没有 */
  function liveModels() {
    const live = S.live;
    if (!live || !live.ok || !live.models || !live.models.length) return null;
    const norm = (u) => String(u || '').replace(/\/+$/, '');
    if (live.base_url && norm(live.base_url) !== norm(S.form.base_url)) return null;
    return live.models;
  }

  function modelHint() {
    if (S.probing) return '正在问服务端有哪些模型可用…（服务端列表比页面写死的准）';
    const live = liveModels();
    if (live) {
      const miss = S.form.model && live.indexOf(S.form.model) < 0;
      return `<span class="${miss ? 'red' : ''}">服务端可用 ${live.length} 个：${esc(live.join(' / '))}`
        + `${miss ? `　当前填的「${esc(S.form.model)}」不在其中，保存后必然 400` : ''}</span>`;
    }
    if (S.live && !S.live.ok && S.live.error) {
      return `拿不到服务端模型列表：${esc(S.live.error)}`;
    }
    return '模型名以厂商可用列表为准，名字错了「测试连接」会直接告诉你是谁的问题';
  }

  function render() {
    const c = S.cfg;
    if (!c) return '<div class="note"><div>正在读取配置…</div></div>';

    const p = curProvider();
    // 服务端探到了就用真的，没探到才用页面里的推荐值
    const models = liveModels() || (p && p.models) || [];
    const keyVal = S.form.api_key || '';

    return `<div class="llm-wrap">
      <div class="card-h" style="padding:0 0 10px">
        <h3>模型设置</h3><span class="sub">保存后立即生效，不用重启服务</span>
        <div class="spacer"></div>${statusChip()}
      </div>

      <div class="llm-grid">
        <div class="llm-form">
          <div class="llm-f">
            <label>服务商</label>
            <div class="llm-provs">${providers().map((x) => `
              <button class="llm-prov ${S.form.provider === x.k ? 'on' : ''}"
                data-llm="prov" data-k="${x.k}">
                <b>${esc(x.t)}</b>
                <span>${esc(x.base || '自定义地址')}</span>
              </button>`).join('')}</div>
            ${p && p.hint ? `<div class="llm-hint">${esc(p.hint)}</div>` : ''}
          </div>

          <div class="llm-f">
            <label>Base URL</label>
            <input class="llm-in" data-llm="base_url" value="${esc(S.form.base_url || '')}"
              placeholder="https://api.deepseek.com">
            ${hintHtml('base_url', '只填到域名或 /v1，不要带 /chat/completions')}
          </div>

          <div class="llm-f">
            <label>模型
              <em style="float:right">
                <button class="llm-linkbtn" data-llm="reprobe" ${S.probing ? 'disabled' : ''}>
                  ${S.probing ? '获取中…' : '从服务端刷新可用列表'}</button>
              </em></label>
            ${models.length ? `<div class="llm-models">${models.map((m) => `
              <button class="llm-model ${S.form.model === m ? 'on' : ''}"
                data-llm="model" data-v="${esc(m)}">${esc(m)}</button>`).join('')}</div>` : ''}
            <input class="llm-in" data-llm="model" value="${esc(S.form.model || '')}"
              placeholder="deepseek-flash">
            <div class="llm-hint">${modelHint()}</div>
          </div>

          <div class="llm-f">
            <label>API Key</label>
            <div class="llm-keyrow">
              <input class="llm-in" type="password" data-llm="api_key" value="${esc(keyVal)}"
                placeholder="${c.has_key ? '已保存（留空则不修改）' : 'sk-...'}">
              <button class="btn sm" data-llm="reveal">显示</button>
              <button class="btn sm" data-llm="clean" title="去掉中文、全角与零宽字符">清理</button>
              ${c.has_key ? `<button class="btn sm" data-llm="usekey">用已存的</button>` : ''}
            </div>
            ${hintHtml('api_key', c.has_key
              ? `当前已保存：${c.key_masked}（改动才需重填）`
              : '去 platform.deepseek.com 生成，只存本机数据库')}
          </div>

          <div class="llm-f">
            <label>温度 <em>${Number(S.form.temperature).toFixed(2)}</em></label>
            <input class="llm-range" type="range" min="0" max="1" step="0.1"
              data-llm="temperature" value="${Number(S.form.temperature)}">
            <div class="llm-hint">配置推导建议 0.1–0.3：越低温越稳定、越可复现</div>
          </div>

          <div class="llm-acts">
            <button class="btn sm" data-llm="test" ${S.testing ? 'disabled' : ''}>
              ${S.testing ? '测试中…' : '测试连接'}</button>
            <button class="btn sm primary" data-llm="save" ${S.saving ? 'disabled' : ''}>
              ${S.saving ? '保存中…' : '保存配置'}</button>
            ${c.has_key ? '<button class="btn sm" data-llm="clear">清除配置</button>' : ''}
          </div>
        </div>

        <div class="llm-side">
          ${renderNotice()}
          <div class="llm-card">
            <div class="llm-ct">这个配置用在哪</div>
            <ul class="llm-ul">
              <li><b>初始化配置推导</b> 里的「AI 推导」会逐组调用它，把问卷答案翻成 81 条配置</li>
              <li>规则引擎仍然在跑：模型漏项、给错编号时按规则值兜底，不会出现"少了三条没人知道"</li>
              <li>模型只出内容，配置项的编号与集合由清单锁死</li>
            </ul>
          </div>
          <div class="llm-card">
            <div class="llm-ct">成本参考</div>
            <ul class="llm-ul">
              <li>一次完整推导 = 7 次调用（每组一次），约 6–9k 输入 token</li>
              <li>DeepSeek 约 ¥2/百万输入 token，单次推导成本不到 2 分钱</li>
              <li>推导过程页会显示每次调用的 tokens 与耗时，可对账</li>
            </ul>
          </div>
        </div>
      </div>
    </div>`;
  }

  function renderNotice() {
    const n = S.notice;
    if (!n) return '';
    return `<div class="llm-note ${n.tone || ''}">
      <b>${esc(n.title)}</b>
      <span>${esc(n.msg)}</span>
      ${n.extra ? `<em>${esc(n.extra)}</em>` : ''}
    </div>`;
  }

  function paint() {
    const root = document.getElementById('llm-root');
    if (!root) return;
    root.innerHTML = render();
  }

  function fillForm(cfg) {
    S.cfg = cfg;
    S.form = {
      provider: cfg.provider || 'deepseek',
      base_url: cfg.base_url || 'https://api.deepseek.com',
      model: cfg.model || 'deepseek-flash',
      api_key: '',
      temperature: cfg.temperature == null ? 0.2 : cfg.temperature,
    };
    // 服务商没匹配上（例如从环境变量来的）就落到 custom，避免下拉空着
    if (!providers().some((p) => p.k === S.form.provider)) S.form.provider = 'custom';
  }

  async function load() {
    if (!api().llmGetConfig) { S.notice = { tone: 'bad', title: '未连接后端', msg: '接口不可用' }; paint(); return; }
    try {
      fillForm(await api().llmGetConfig());
      paint();
      if (S.cfg && S.cfg.has_key) probe();
    } catch (e) {
      S.notice = { tone: 'bad', title: '读取配置失败', msg: String(e.message || e) };
      paint();
    }
  }

  /* 拉服务端真实可用的模型名。失败不算错误，只是回到推荐列表 */
  async function probe() {
    if (!api().llmListModels) return;
    S.probing = true;
    paint();
    try {
      S.live = await api().llmListModels();
    } catch (e) {
      S.live = { ok: false, models: [], error: String(e.message || e) };
    }
    S.probing = false;
    paint();
  }

  async function doTest() {
    S.testing = true;
    S.notice = null;
    paint();
    const body = {
      base_url: S.form.base_url,
      model: S.form.model,
      api_key: S.form.api_key || '',
    };
    try {
      const r = await api().llmTestConfig(body);
      S.notice = r.ok
        ? {
          tone: 'ok', title: `连接正常 · ${r.model}`,
          msg: `延迟 ${r.latency_ms} ms · tokens ${r.tokens_in}/${r.tokens_out}`,
          extra: r.sample ? `模型回话：${r.sample}` : '',
        }
        : { tone: 'bad', title: '连接失败', msg: r.error || '未知错误' };
    } catch (e) {
      S.notice = { tone: 'bad', title: '连接失败', msg: String(e.message || e) };
    }
    S.testing = false;
    paint();
    // 模型名错是最常见的一种失败：当场把服务端真实列表拉回来，别让人去猜
    if (S.notice && S.notice.tone === 'bad'
        && /model|模型/i.test(S.notice.msg || '') && api().llmListModels) {
      probe();
    }
  }

  async function doSave() {
    if (!S.form.api_key && !(S.cfg && S.cfg.has_key)) {
      S.notice = { tone: 'bad', title: '缺少 API Key', msg: '先填 Key 再保存；只想改模型名就点「用已存的」' };
      paint();
      return;
    }
    S.saving = true;
    S.notice = null;
    paint();
    try {
      const r = await api().llmSaveConfig({
        provider: S.form.provider,
        base_url: S.form.base_url,
        model: S.form.model,
        api_key: S.form.api_key || (S.cfg && S.cfg.has_key ? S.cfg.key_masked : ''),
        temperature: Number(S.form.temperature),
      });
      fillForm(r);
      S.notice = { tone: 'ok', title: '已保存并生效', msg: `${r.model} · ${r.base_url}`, extra: '刷新推导页即可用「AI 推导」' };
    } catch (e) {
      S.notice = { tone: 'bad', title: '保存失败', msg: String(e.message || e) };
    }
    S.saving = false;
    paint();
  }

  async function doClear() {
    if (!window.confirm('清除后回到未配置状态，AI 推导将不可用。确定？')) return;
    try {
      fillForm(await api().llmClearConfig());
      S.notice = { tone: '', title: '已清除', msg: '现在只能跑纯规则推导' };
    } catch (e) {
      S.notice = { tone: 'bad', title: '清除失败', msg: String(e.message || e) };
    }
    paint();
  }

  function onClick(e) {
    const el = e.target.closest('[data-llm]');
    if (!el) return;
    const act = el.dataset.llm;
    if (act === 'prov') { pickProvider(el.dataset.k); return; }
    if (act === 'model') { S.form.model = el.dataset.v; paint(); return; }
    if (act === 'test') { doTest(); return; }
    if (act === 'save') { doSave(); return; }
    if (act === 'clear') { doClear(); return; }
    if (act === 'reprobe') { probe(); return; }
    if (act === 'clean') {
      // 显式操作：用户点了才清，绝不偷偷改他粘贴的内容
      const before = String(S.form.api_key || '');
      const after = before.replace(/[^\x20-\x7E]/g, '').trim();
      S.form.api_key = after;
      const n = before.length - after.length;
      S.notice = n
        ? { tone: 'ok', title: `已去掉 ${n} 个非英文字符`, msg: '清理后点「测试连接」确认能不能通' }
        : { tone: '', title: '没发现需要清理的字符', msg: '当前内容都是英文字符与符号' };
      paint();
      return;
    }
    if (act === 'reveal') {
      const inp = document.querySelector('[data-llm="api_key"]');
      if (inp) inp.type = inp.type === 'password' ? 'text' : 'password';
      return;
    }
    if (act === 'usekey') {
      S.form.api_key = (S.cfg && S.cfg.key_masked) || '';
      paint();
      return;
    }
  }

  function onInput(e) {
    const el = e.target.closest('[data-llm]');
    if (!el) return;
    const k = el.dataset.llm;
    if (k === 'temperature') {
      S.form.temperature = Number(el.value);
      const lab = document.querySelector('.llm-f label em');
      if (lab) lab.textContent = Number(el.value).toFixed(2);
      return;
    }
    if (k === 'api_key' || k === 'base_url' || k === 'model') {
      S.form[k] = el.value;
      // 输入框正在焦点上，不整块重绘
      if (k !== 'model') refreshHint(k);
    }
  }

  window.LLMConfig = {
    afterRender() {
      const root = document.getElementById('llm-root');
      if (!root) return;
      if (!root.dataset.bound) {
        root.dataset.bound = '1';
        root.addEventListener('click', onClick);
        root.addEventListener('input', onInput);
      }
      if (!S.cfg) load();
      else paint();
    },
    state: S,
  };
})();
