/* 登录页逻辑。刻意不引用 api.js —— api.js 一加载就会拿 bootstrap，
   未登录时会把页面弹回登录页，形成跳转死循环。这里只做三件事：
   验已有凭证、换 token、跳回去。 */
(function () {
  'use strict';

  const isFile = location.protocol === 'file:';
  const BASE = isFile ? 'http://127.0.0.1:8010' : '';
  const TOKEN_KEY = 'sdlc_token';

  const $ = (id) => document.getElementById(id);
  const form = $('lgForm');
  const userEl = $('lgUser');
  const passEl = $('lgPass');
  const remEl = $('lgRemember');
  const btn = $('lgBtn');
  const errEl = $('lgErr');
  const fUser = $('fUser');
  const fPass = $('fPass');

  /* ---------- 跳回去的地址：只接受站内相对路径 ---------- */
  function nextUrl() {
    const raw = new URLSearchParams(location.search).get('next') || '';
    let v = raw;
    try { v = decodeURIComponent(raw); } catch (e) { /* 原样用 */ }
    // 挡掉 //evil.com 这种协议相对地址与绝对地址，避免开放重定向
    if (!v || v[0] !== '/' || v.slice(0, 2) === '//' || /^\/\w+:\/\//.test(v)) return 'index.html';
    if (/login\.html/.test(v)) return 'index.html';
    return v;
  }

  function showErr(msg, tipOnly) {
    errEl.textContent = msg;
    errEl.className = 'lg-err show' + (tipOnly ? ' tip-only' : '');
    if (!tipOnly) {
      fUser.classList.add('bad');
      fPass.classList.add('bad');
    }
  }

  function clearErr() {
    errEl.className = 'lg-err';
    errEl.textContent = '';
    fUser.classList.remove('bad');
    fPass.classList.remove('bad');
  }

  function token() {
    try { return localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY); }
    catch (e) { return null; }
  }

  function saveToken(t, remember) {
    try {
      localStorage.removeItem(TOKEN_KEY);
      sessionStorage.removeItem(TOKEN_KEY);
      (remember ? localStorage : sessionStorage).setItem(TOKEN_KEY, t);
    } catch (e) { /* 隐私模式下存不了，本次会话内仍可用 */ }
  }

  async function api(path, opt) {
    opt = opt || {};
    const res = await fetch(BASE + path, {
      method: opt.method || 'GET',
      headers: Object.assign({ 'Content-Type': 'application/json' }, opt.headers || {}),
      body: opt.body ? JSON.stringify(opt.body) : undefined,
    });
    let data = {};
    try { data = await res.json(); } catch (e) { /* 非 JSON */ }
    return { ok: res.ok, status: res.status, data };
  }

  /* ---------- 已有凭证：验一次再放行 ---------- */
  async function checkExisting() {
    const t = token();
    if (!t) return false;
    try {
      const r = await api('/api/auth/me', { headers: { Authorization: 'Bearer ' + t } });
      if (r.ok) { location.replace(nextUrl()); return true; }
      try { localStorage.removeItem(TOKEN_KEY); sessionStorage.removeItem(TOKEN_KEY); }
      catch (e) { /* 忽略 */ }
    } catch (e) { /* 后端没起，留在登录页让人看到错误 */ }
    return false;
  }

  /* ---------- 提交 ---------- */
  async function submit(e) {
    e.preventDefault();
    clearErr();

    const username = userEl.value.trim();
    const password = passEl.value;
    if (!username) { showErr('请填写账号'); userEl.focus(); return; }
    if (!password) { showErr('请填写密码'); passEl.focus(); return; }

    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = '登录中…';

    try {
      const r = await api('/api/auth/login', {
        method: 'POST', body: { username, password },
      });
      if (!r.ok) {
        showErr(r.data.detail || '登录失败（HTTP ' + r.status + '）');
        btn.disabled = false;
        btn.textContent = old;
        passEl.select();
        return;
      }
      saveToken(r.data.token, remEl.checked);
      location.replace(nextUrl());
    } catch (err) {
      showErr('连不上后端服务，确认 backend/run.py 已启动（' + (err.message || err) + '）');
      btn.disabled = false;
      btn.textContent = old;
    }
  }

  /* ---------- 演示账号 ---------- */
  async function loadDemo() {
    try {
      const r = await api('/api/auth/demo');
      if (r.ok && r.data.username) {
        $('lgDemoUser').textContent = r.data.username;
        $('lgDemoPass').textContent = r.data.password;
        return r.data;
      }
    } catch (e) { /* 后端未启动时用页面上的默认值 */ }
    return { username: $('lgDemoUser').textContent, password: $('lgDemoPass').textContent };
  }

  $('lgFill').addEventListener('click', async () => {
    const d = await loadDemo();
    userEl.value = d.username;
    passEl.value = d.password;
    clearErr();
    passEl.focus();          // 填完直接回车就能登
  });

  form.addEventListener('submit', submit);
  [userEl, passEl].forEach((el) => el.addEventListener('input', () => {
    fUser.classList.remove('bad');
    fPass.classList.remove('bad');
    if (errEl.classList.contains('show') && !errEl.classList.contains('tip-only')) {
      errEl.className = 'lg-err';
    }
  }));

  // 从业务页被踢回来时说明原因
  (function why() {
    const w = new URLSearchParams(location.search).get('why');
    if (w) showErr(w + '，请重新登录', true);
  })();

  loadDemo();
  checkExisting();
})();
