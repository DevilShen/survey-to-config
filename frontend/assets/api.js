/* 后端接入层。
   在线时用 API 数据替换 data.js 的演示数据；离线（或服务未启动）时静默回退到本地数据。
   页面本身的渲染逻辑不变，变的只是数据来源。 */
window.API = (function () {
  const isFile = location.protocol === 'file:';
  // 直接双击 index.html 打开时，服务在 8010
  const BASE = isFile ? 'http://127.0.0.1:8010' : '';

  /* ---------- 登录凭证 ----------
     勾了「记住登录」存 localStorage（关浏览器也在），否则存 sessionStorage（关标签即失效）。
     两处都读，是为了让"记住/不记住"切换后不会捡到上一次的旧凭证。 */
  const TOKEN_KEY = 'sdlc_token';
  const store = {
    get() {
      try { return localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY); }
      catch (e) { return null; }
    },
    set(token, remember) {
      try {
        localStorage.removeItem(TOKEN_KEY);
        sessionStorage.removeItem(TOKEN_KEY);
        (remember ? localStorage : sessionStorage).setItem(TOKEN_KEY, token);
      } catch (e) { /* 隐私模式禁用存储，退化为内存态 */ }
    },
    clear() {
      try { localStorage.removeItem(TOKEN_KEY); sessionStorage.removeItem(TOKEN_KEY); }
      catch (e) { /* 忽略 */ }
    }
  };

  const state = {
    online: false, token: store.get(), user: null, meta: null, error: null,
    authRequired: false
  };

  /* 未登录 / 登录过期 → 回登录页，并记住原来要看哪一页 */
  function goLogin(reason) {
    if (/login\.html/.test(location.pathname)) return;   // 已经在登录页，别再跳
    const next = encodeURIComponent(location.pathname + location.search + location.hash);
    const why = reason ? '&why=' + encodeURIComponent(reason) : '';
    location.replace('login.html?next=' + next + why);
  }

  function on401(msg) {
    store.clear();
    state.token = null;
    state.user = null;
    goLogin(msg);
  }

  async function req(path, opt) {
    opt = opt || {};
    const headers = Object.assign({ 'Content-Type': 'application/json' }, opt.headers || {});
    if (state.token) headers['Authorization'] = 'Bearer ' + state.token;
    const res = await fetch(BASE + path, {
      method: opt.method || 'GET',
      headers,
      body: opt.body ? JSON.stringify(opt.body) : undefined,
      signal: opt.signal
    });
    if (!res.ok) {
      let msg = res.status + '';
      try { msg = (await res.json()).detail || msg; } catch (e) { /* 非 JSON 错误 */ }
      if (res.status === 401 && !opt.noAuthRedirect) on401(msg);
      throw new Error(msg);
    }
    return res.json();
  }

  // 读 SSE 帧：一个 chunk 里可能并行多帧，按 \n\n 切开逐帧回调。
  async function readSSE(res, onEvent) {
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let event = 'message', data = '';
        raw.split('\n').forEach((line) => {
          if (line.indexOf('event:') === 0) event = line.slice(6).trim();
          else if (line.indexOf('data:') === 0) data += line.slice(5).trim();
        });
        if (!data) continue;
        try { onEvent(event, JSON.parse(data)); } catch (e) { /* 忽略坏帧 */ }
      }
    }
  }

  /* ---------- 登录 / 退出 ---------- */

  /** 账号密码换 JWT。登录页直接用它（登录页不加载本文件的自动启动逻辑）。 */
  async function login(username, password, remember) {
    const r = await req('/api/auth/login', {
      method: 'POST', body: { username, password }, noAuthRedirect: true
    });
    state.token = r.token;
    state.user = r.user;
    store.set(r.token, remember !== false);
    return r.user;
  }

  async function me() {
    const u = await req('/api/auth/me', { noAuthRedirect: true });
    state.user = u;
    return u;
  }

  async function logout() {
    try { await req('/api/auth/logout', { method: 'POST', noAuthRedirect: true }); }
    catch (e) { /* token 已失效也一样要能退出 */ }
    store.clear();
    state.token = null;
    state.user = null;
    location.replace('login.html');
  }

  /* 页面启动：没有凭证直接回登录页；有凭证就去后端验一次（本地看 token 有效期不算数，
     密钥轮换或用户被停用时本地凭据看起来仍然"没过期"）。 */
  async function boot() {
    if (!state.token) {
      state.authRequired = true;
      goLogin();
      return;
    }
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 4000);
      const data = await req('/api/bootstrap', { signal: ctrl.signal });
      clearTimeout(timer);
      window.DB = data;
      state.meta = data.meta;
      state.online = true;
      await me();
    } catch (e) {
      state.online = false;
      state.error = e.message || String(e);
    }
  }

  async function derive(answers) {
    const r = await req('/api/config/derive', { method: 'POST', body: { answers } });
    return {
      cfg: r.configs, issues: r.issues, flow: r.flow,
      stats: r.stats, inputHash: r.input_hash, outputHash: r.output_hash,
      ruleVersion: r.rule_version, server: true
    };
  }

  // overrides：人工确认的配置值 {C37:{v,why,at}}，有才带，不带就是纯规则推导
  async function configDeriveV4(answers, overrides) {
    const body = { template: 'survey_96', answers };
    if (overrides && Object.keys(overrides).length) body.overrides = overrides;
    return req('/api/config/derive', { method: 'POST', body });
  }

  // 推导过程流式回放。与 configDeriveV4 同一个后端生成器，过程与结果同源。
  async function configDeriveStream(answers, onEvent, overrides) {
    const headers = { 'Content-Type': 'application/json' };
    if (state.token) headers['Authorization'] = 'Bearer ' + state.token;
    const body = { template: 'survey_96', answers };
    if (overrides && Object.keys(overrides).length) body.overrides = overrides;
    const res = await fetch(BASE + '/api/config/derive/stream', {
      method: 'POST', headers,
      body: JSON.stringify(body)
    });
    if (!res.ok) {
      let msg = res.status + '';
      try { msg = (await res.json()).detail || msg; } catch (e) { /* 非 JSON */ }
      if (res.status === 401) on401(msg);      // SSE 通道同样要处理过期
      throw new Error(msg);
    }
    // 统一成 {type, ...data} 的形状，调用方只关心事件内容和类型
    await readSSE(res, (name, data) => onEvent(Object.assign({ type: name }, data)));
  }

  async function configTemplateV4() {
    return req('/api/config/v4/template');
  }

  async function configRulesV4(group) {
    return req('/api/config/v4/rules' + (group ? '?group=' + encodeURIComponent(group) : ''));
  }

  async function configConflictsV4() {
    return req('/api/config/v4/conflicts');
  }

  async function recompute(pid) {
    return req('/api/projects/' + encodeURIComponent(pid) + '/variance/recompute', { method: 'POST' });
  }

  function exportConfig() {
    window.open(BASE + '/api/config/export', '_blank');
  }

  // 上传：不能带 Content-Type，交给浏览器生成 multipart 边界
  async function upload(path, file, withAuth) {
    const headers = {};
    if (withAuth !== false && state.token) headers['Authorization'] = 'Bearer ' + state.token;
    const fd = new FormData();
    fd.append('file', file, file.name);
    const res = await fetch(BASE + path, { method: 'POST', headers, body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (res.status === 401) on401(data.detail);
      throw new Error(data.detail || ('HTTP ' + res.status));
    }
    return data;
  }

  // 上传一律带 token：网关对所有 /api/* 生效，包括 multipart 通道
  const ingestPreview = (file) => upload('/api/ingest/effort/preview', file, true);
  const ingestCommit = (file) => upload('/api/ingest/effort', file, true);
  // 问卷导入：只解析答案，不触发推导（推导由前端点「AI 推导」单独发起）
  const importSurvey = (file) => upload('/api/config/import-survey', file, true);

  // 大模型配置：页面里改，改完立即生效
  const llmGetConfig = () => req('/api/llm/config');
  const llmSaveConfig = (body) => req('/api/llm/config', { method: 'POST', body });
  const llmTestConfig = (body) => req('/api/llm/config/test', { method: 'POST', body: body || {} });
  const llmClearConfig = () => req('/api/llm/config', { method: 'DELETE' });
  // 问服务端有哪些模型名可用（模型名会随厂商迭代变，不能只在页面写死）
  const llmListModels = () => req('/api/llm/models');

  // AI 推导：逐组真实调用大模型，SSE
  async function configDeriveLLMStream(answers, onEvent, overrides) {
    const headers = { 'Content-Type': 'application/json' };
    if (state.token) headers['Authorization'] = 'Bearer ' + state.token;
    const body = { template: 'survey_96', answers };
    if (overrides && Object.keys(overrides).length) body.overrides = overrides;
    const res = await fetch(BASE + '/api/config/derive/llm-stream', {
      method: 'POST', headers, body: JSON.stringify(body),
    });
    if (!res.ok) {
      let msg = res.status + '';
      try { msg = (await res.json()).detail || msg; } catch (e) { /* 非 JSON */ }
      if (res.status === 401) on401(msg);      // SSE 通道同样要处理过期
      throw new Error(msg);
    }
    await readSSE(res, (name, data) => onEvent(Object.assign({ type: name }, data)));
  }

  // 草稿：「仅保存」通道。落盘但不推导，刷新页面还能捡回来。
  const getConfigDraft = () => req('/api/config/draft');
  const saveConfigDraft = (answers, overrides) =>
    req('/api/config/draft', { method: 'POST', body: { answers, overrides } });
  const clearConfigDraft = () => req('/api/config/draft', { method: 'DELETE' });
  const probe = (sid) => req('/api/sources/' + encodeURIComponent(sid) + '/probe', { method: 'POST' });
  const lineage = (metricId) => req('/api/sources/metrics/' + encodeURIComponent(metricId) + '/lineage');
  const templateUrl = () => BASE + '/api/ingest/template';

  // 写操作后重新拉一次首屏：保证页面数字与库一致，不做局部猜算
  async function reload() {
    try {
      const data = await req('/api/bootstrap');
      window.DB = data;
      state.meta = data.meta;
      return true;
    } catch (e) { return false; }
  }

  /* ---------- 市场报价 Agent ---------- */

  const pricingOverview = () => req('/api/pricing/overview');
  const pricingRun = (runId) => req('/api/pricing/runs/' + encodeURIComponent(runId));
  const pricingGo = (reqId, answers) =>
    req('/api/pricing/requests/' + encodeURIComponent(reqId) + '/run',
      { method: 'POST', body: { answers: answers || {} } });
  const pricingReview = (runId, body) =>
    req('/api/pricing/runs/' + encodeURIComponent(runId) + '/review',
      { method: 'POST', body });
  const pricingRules = () => req('/api/pricing/rules');
  const pricingSaveRules = (body) => req('/api/pricing/rules', { method: 'PUT', body });
  const pricingSimulate = (body) => req('/api/pricing/rules/simulate', { method: 'POST', body });
  const pricingResetRules = () => req('/api/pricing/rules/reset', { method: 'POST' });
  const pricingRegress = (rules) => req('/api/pricing/rules/regress', { method: 'POST', body: { rules } });
  const pricingRerun = (runId, comment) =>
    req('/api/pricing/runs/' + encodeURIComponent(runId) + '/rerun',
      { method: 'POST', body: { comment } });
  const pricingLlmCalls = () => req('/api/pricing/llm-calls');

  // 对话层（LLM 追问 / 槽位抽取 / 结论解读）
  const llmStatus = () => req('/api/pricing/dialogue/llm-status');
  const dialogueHistory = (rid) => req('/api/pricing/dialogue/' + encodeURIComponent(rid) + '/history');

  // SSE 流式对话。POST + fetch ReadableStream（不是 EventSource，因为要带 Authorization 头）。
  // 一个连接完成一个完整回合；onEvent(event, data) 逐事件回调，前端逐段渲染。
  async function dialogueTurn(rid, text, onEvent) {
    const headers = { 'Content-Type': 'application/json' };
    if (state.token) headers['Authorization'] = 'Bearer ' + state.token;
    const res = await fetch(BASE + '/api/pricing/dialogue/' + encodeURIComponent(rid) + '/turns', {
      method: 'POST', headers, body: JSON.stringify({ text })
    });
    if (!res.ok) {
      let msg = res.status + '';
      try { msg = (await res.json()).detail || msg; } catch (e) { /* 非 JSON */ }
      if (res.status === 401) on401(msg);      // SSE 通道同样要处理过期
      throw new Error(msg);
    }
    await readSSE(res, onEvent);
  }

  state.ready = boot();
  return Object.assign(state, { base: BASE, req, derive, recompute, exportConfig,
    login, logout, me, goLogin,
    configDeriveV4, configDeriveStream, configTemplateV4, configRulesV4, configConflictsV4,
    ingestPreview, ingestCommit, importSurvey, probe, lineage, templateUrl, reload,
    getConfigDraft, saveConfigDraft, clearConfigDraft,
    llmGetConfig, llmSaveConfig, llmTestConfig, llmClearConfig, llmListModels,
    configDeriveLLMStream,
    pricingOverview, pricingRun, pricingGo, pricingReview,
    pricingRules, pricingSaveRules, pricingSimulate, pricingResetRules,
    pricingRegress, pricingRerun, pricingLlmCalls,
    llmStatus, dialogueHistory, dialogueTurn });
})();

// app.js 会等这个 Promise 完成再渲染
window.DATA_READY = window.API.ready;
