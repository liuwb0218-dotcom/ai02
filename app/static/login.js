/* 登录 / 注册页逻辑 — 复用 Auth 的存储格式，但不自动刷新 Token */
const $ = (sel) => document.querySelector(sel);

const tabLogin = $('#tabLogin');
const tabRegister = $('#tabRegister');
const form = $('#authForm');
const usernameEl = $('#username');
const passwordEl = $('#password');
const confirmWrap = $('#confirmWrap');
const confirmEl = $('#confirmPassword');
const errorEl = $('#authError');
const submitBtn = $('#submitBtn');
const tipEl = $('#authTip');

let mode = 'login';

const params = new URLSearchParams(location.search);
const nextUrl = params.get('next') || '/';

// 已登录则直接进入目标页（由问答页负责 Token 过期后的刷新）
if (localStorage.getItem('qa.accessToken') || localStorage.getItem('qa.refreshToken')) {
  location.replace(nextUrl.startsWith('/') ? nextUrl : '/');
}

function setMode(value) {
  mode = value;
  const isLogin = value === 'login';
  tabLogin.classList.toggle('active', isLogin);
  tabRegister.classList.toggle('active', !isLogin);
  confirmWrap.hidden = isLogin;
  passwordEl.autocomplete = isLogin ? 'current-password' : 'new-password';
  submitBtn.textContent = isLogin ? '登录' : '注册并开始试用';
  tipEl.innerHTML = isLogin
    ? '还没有账号？<a href="#" id="switchLink">立即注册</a>'
    : '已有账号？<a href="#" id="switchLink">直接登录</a>';
  tipEl.querySelector('#switchLink').addEventListener('click', (e) => {
    e.preventDefault();
    setMode(isLogin ? 'register' : 'login');
  });
  hideError();
}

function showError(message) {
  errorEl.textContent = message;
  errorEl.hidden = false;
}

function hideError() {
  errorEl.hidden = true;
  errorEl.textContent = '';
}

async function submit(event) {
  event.preventDefault();
  hideError();

  const username = usernameEl.value.trim();
  const password = passwordEl.value;

  if (mode === 'register') {
    if (!/^[A-Za-z0-9_]{3,32}$/.test(username)) {
      showError('用户名需为 3~32 位字母、数字或下划线');
      return;
    }
    if (password.length < 8) {
      showError('密码至少 8 位');
      return;
    }
    if (password !== confirmEl.value) {
      showError('两次输入的密码不一致');
      return;
    }
  }

  submitBtn.disabled = true;
  submitBtn.textContent = mode === 'login' ? '登录中…' : '注册中…';

  try {
    const data = await fetchJson(
      mode === 'login' ? '/api/auth/login' : '/api/auth/register',
      { username, password },
    );
    localStorage.setItem('qa.accessToken', data.access_token);
    localStorage.setItem('qa.refreshToken', data.refresh_token);
    localStorage.setItem('qa.user', JSON.stringify(data.user));
    // 每次登录都重新弹公告：清空本次会话内的关闭记录
    sessionStorage.removeItem('qa.announceDismissed');
    location.replace(nextUrl.startsWith('/') ? nextUrl : '/');
  } catch (err) {
    showError(err.message + (err.code === 'ACCOUNT_DISABLED' && err.detail?.disabled_until
      ? `（限制至 ${err.detail.disabled_until}）` : ''));
    submitBtn.disabled = false;
    submitBtn.textContent = mode === 'login' ? '登录' : '注册并开始试用';
  }
}

async function fetchJson(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const err = new Error(
      data?.message
      || (Array.isArray(data?.detail) ? data.detail.map((d) => d.msg).join('；') : '')
      || `请求失败（HTTP ${res.status}）`,
    );
    err.code = data?.code || `HTTP_${res.status}`;
    err.detail = data?.detail ?? null;
    throw err;
  }
  return data;
}

tabLogin.addEventListener('click', () => setMode('login'));
tabRegister.addEventListener('click', () => setMode('register'));
$('#switchLink').addEventListener('click', (e) => {
  e.preventDefault();
  setMode(mode === 'login' ? 'register' : 'login');
});
form.addEventListener('submit', submit);

setMode('login');
usernameEl.focus();
