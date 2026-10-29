/* AI 智能问答平台 — 前端逻辑（原生 JS，无框架）
 *
 * 登录态由 auth-client.js 统一维护（Token 存储、自动刷新、401 跳转）。
 * 对接的接口：
 *   GET    /api/models[?include_disabled=true]
 *   POST   /api/models           PATCH/DELETE /api/models/{id}
 *   POST   /api/models/{id}/test
 *   POST   /api/chat
 *   GET    /api/history/{user_id}?limit=50  DELETE /api/history/{session_id}
 */

const $ = (sel) => document.querySelector(sel);

// ── 页面元素 ──────────────────────────────────────
const messagesEl = $('#messages');
const emptyEl = $('#emptyState');
const inputEl = $('#questionInput');
const sendBtn = $('#sendBtn');
const modelSelect = $('#modelSelect');
const historyList = $('#historyList');
const historyCount = $('#historyCount');
const statusDot = $('#statusDot');
const statusText = $('#statusText');
const sidebar = $('#sidebar');
const scrim = $('#scrim');
const modelModal = $('#modelModal');
const modelList = $('#modelList');
const modelForm = $('#modelForm');
const formTitle = $('#formTitle');
const trialBanner = $('#trialBanner');
const announceModal = $('#announceModal');
const announceList = $('#announceList');
const announceAllRead = $('#announceAllRead');
const usageModal = $('#usageModal');
const usageList = $('#usageList');
const usageEmpty = $('#usageEmpty');

// 本次登录已关闭的公告 ID：存储于 sessionStorage，重新登录由 login.js 清空
const ANNOUNCE_KEY = 'qa.announceDismissed';

// ── 全局状态 ──────────────────────────────────────
const state = {
  user: null,
  userId: null,
  models: [],          // 可用（启用）模型，供下拉框
  allModels: [],       // 含已停用，供模型管理
  modelName: localStorage.getItem('qa.modelName') || '',
  sessions: [],
  activeSessionId: null,
  editingId: null,     // null=新增，否则为正在编辑的配置 ID
  sending: false,
  announcements: [],   // 当前弹窗中的公告
};

const NAME_RE = /^[A-Za-z0-9._-]{2,40}$/;
const PROVIDER_LABEL = { mock: 'mock', deepseek: 'deepseek', local: 'local', custom: 'custom' };
const ROLE_LABEL = { super_admin: '超级管理员', admin: '管理员', user: '普通用户' };

// ══════════ 基础工具 ══════════

/** 统一请求：自动带 Token，401 时由 Auth 刷新并重试 */
function api(path, options = {}) {
  return Auth.api(path, options);
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function toast(message, type = 'error', requestId = '') {
  const node = el('div', `toast ${type}`, message);
  if (requestId) node.appendChild(el('span', 'req', `request_id: ${requestId}`));
  $('#toasts').appendChild(node);
  setTimeout(() => node.remove(), type === 'success' ? 2800 : 6000);
}

function setStatus(text, busy = false) {
  statusText.textContent = text;
  statusDot.classList.toggle('busy', busy);
}

// ══════════ 当前用户与试用提示 ══════════

function renderUser(user) {
  if (!user) return;
  state.user = user;
  state.userId = user.id;

  $('#userName').textContent = user.username;
  $('#userAvatar').textContent = (user.username || 'U').slice(0, 1).toUpperCase();
  $('#roleBadge').textContent = ROLE_LABEL[user.role] || user.role;
  $('#roleBadge').className = `role-badge role-${user.role}`;
  // 侧栏入口按角色切换：管理员进管理后台，普通用户进自己的数据页
  const isAdmin = user.role === 'admin' || user.role === 'super_admin';
  const workspaceBtn = $('#workspaceBtn');
  workspaceBtn.href = isAdmin ? '/admin' : '/me';
  workspaceBtn.textContent = isAdmin ? '管理后台' : '我的数据';
  renderTrialBanner();
}

function daysLeft(expiresAt) {
  if (!expiresAt) return 0;
  const expires = new Date(expiresAt.replace(' ', 'T'));
  const diff = expires.getTime() - Date.now();
  return Math.max(0, Math.ceil(diff / 86400000));
}

function renderTrialBanner() {
  const user = state.user;
  if (!user || user.status !== 'pending') {
    trialBanner.hidden = true;
    return;
  }
  trialBanner.hidden = false;
  if (user.trial_expired) {
    trialBanner.className = 'trial-banner danger';
    trialBanner.textContent = '7 天试用期已结束：仍可查看历史记录，联系管理员审核通过后恢复提问。';
    return;
  }
  trialBanner.className = 'trial-banner';
  const left = daysLeft(user.trial_expires_at);
  trialBanner.textContent =
    `试用期剩余 ${left} 天（至 ${user.trial_expires_at}），管理员审核通过后永久可用。`;
}

function nowTime() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function hhmm(value) {
  return typeof value === 'string' && value.length >= 16 ? value.slice(11, 16) : '';
}

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// ══════════ 消息渲染 ══════════

function hideEmpty() {
  emptyEl.hidden = true;
}

function clearMessages() {
  messagesEl.querySelectorAll('.msg').forEach((n) => n.remove());
  emptyEl.hidden = false;
}

function addMessage(role, text, meta = {}) {
  hideEmpty();

  const wrap = el('article', `msg ${role}${meta.error ? ' error' : ''}`);
  wrap.appendChild(el('div', 'avatar', role === 'user' ? '我' : 'AI'));

  const bubble = el('div', 'bubble', text);
  const bits = [];
  if (meta.model) bits.push(meta.model);
  if (meta.latency != null) bits.push(`${meta.latency} ms`);
  if (meta.time) bits.push(meta.time);
  if (bits.length) bubble.appendChild(el('div', 'meta', bits.join(' · ')));

  wrap.appendChild(bubble);
  messagesEl.appendChild(wrap);
  scrollBottom();
  return wrap;
}

function showTyping() {
  hideEmpty();
  const wrap = el('article', 'msg assistant');
  wrap.innerHTML =
    '<div class="avatar">AI</div>' +
    '<div class="bubble"><span class="typing"><span></span><span></span><span></span></span></div>';
  messagesEl.appendChild(wrap);
  scrollBottom();
  return wrap;
}

// ══════════ 模型列表 ══════════

async function loadModels() {
  const [enabled, all] = await Promise.all([
    api('/api/models'),
    api('/api/models?include_disabled=true'),
  ]);
  state.models = enabled.models || [];
  state.allModels = all.models || [];

  renderModelSelect();
  if (modelModal.classList.contains('open')) renderModelCards();
}

function renderModelSelect() {
  modelSelect.innerHTML = '';

  if (!state.models.length) {
    const opt = el('option', null, '暂无可用模型');
    opt.value = '';
    modelSelect.appendChild(opt);
    state.modelName = '';
    return;
  }

  const names = state.models.map((m) => m.model_name);
  // 上次选的模型不存在（被删除/停用/切换用户）时回落到第一个可用模型
  if (!names.includes(state.modelName)) {
    state.modelName = names[0];
    localStorage.setItem('qa.modelName', state.modelName);
  }

  for (const m of state.models) {
    const opt = el('option', null, `${m.display_name} · ${m.provider}`);
    opt.value = m.model_name;
    modelSelect.appendChild(opt);
  }
  modelSelect.value = state.modelName;
}

// ══════════ 历史记录 ══════════

async function loadHistory() {
  const data = await api(`/api/history/${state.userId}?limit=50&offset=0`);
  state.sessions = data.items || [];
  renderHistory();
}

function renderHistory() {
  historyList.innerHTML = '';
  historyCount.textContent = String(state.sessions.length);

  if (!state.sessions.length) {
    historyList.appendChild(el('li', 'history-empty', '还没有提问记录，发送第一条问题试试。'));
    return;
  }

  for (const item of state.sessions) {
    const li = el('li', 'history-item');
    if (item.session_id === state.activeSessionId) li.classList.add('active');
    li.title = item.question;

    li.appendChild(el('span', 'title', item.question));
    li.appendChild(el('span', 'time', hhmm(item.created_at)));

    const del = el('button', 'del');
    del.title = '删除这条记录';
    del.setAttribute('aria-label', '删除这条记录');
    del.innerHTML =
      '<svg viewBox="0 0 24 24" width="13" height="13"><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      deleteSession(item.session_id, item.question);
    });

    li.appendChild(del);
    li.addEventListener('click', () => openSession(item.session_id));
    historyList.appendChild(li);
  }
}

function openSession(sessionId) {
  const item = state.sessions.find((s) => s.session_id === sessionId);
  if (!item) return;

  state.activeSessionId = sessionId;
  clearMessages();
  addMessage('user', item.question, { time: hhmm(item.created_at) });
  addMessage('assistant', item.answer, {
    model: item.model_name,
    time: hhmm(item.created_at),
  });

  // 把顶部模型切换成这条记录用的模型（若仍可用）
  if (state.models.some((m) => m.model_name === item.model_name)) {
    state.modelName = item.model_name;
    localStorage.setItem('qa.modelName', state.modelName);
    modelSelect.value = state.modelName;
  }

  renderHistory();
  if (window.innerWidth <= 860) closeSidebar();
}

async function deleteSession(sessionId, question) {
  if (!window.confirm(`删除这条记录？\n\n${question.slice(0, 40)}`)) return;
  try {
    await api(`/api/history/${sessionId}`, { method: 'DELETE' });
    state.sessions = state.sessions.filter((s) => s.session_id !== sessionId);
    if (state.activeSessionId === sessionId) {
      state.activeSessionId = null;
      clearMessages();
    }
    renderHistory();
    toast('已删除', 'success');
  } catch (err) {
    toast(err.message, 'error', err.requestId);
  }
}

// ══════════ 问答 ══════════

async function send() {
  const question = inputEl.value.trim();
  if (!question || state.sending) return;

  if (!state.modelName) {
    toast('没有可用模型，请先在「模型管理」中添加');
    return;
  }

  state.sending = true;
  sendBtn.disabled = true;
  setStatus('调用中…', true);

  inputEl.value = '';
  autoGrow();
  state.activeSessionId = null;

  addMessage('user', question, { time: nowTime() });
  const typing = showTyping();

  try {
    const res = await api('/api/chat', {
      method: 'POST',
      body: { question, model_name: state.modelName },
    });
    typing.remove();
    addMessage('assistant', res.answer, {
      model: res.model_name,
      latency: res.latency_ms,
      time: nowTime(),
    });
    await loadHistory();
  } catch (err) {
    typing.remove();
    addMessage('assistant', `调用失败：${err.message}（${err.code}）`, {
      error: true,
      time: nowTime(),
    });
    toast(err.message, 'error', err.requestId);
    if (err.code === 'TRIAL_EXPIRED') renderTrialBanner();
  } finally {
    state.sending = false;
    sendBtn.disabled = false;
    setStatus('就绪', false);
    inputEl.focus();
  }
}

function autoGrow() {
  inputEl.style.height = 'auto';
  inputEl.style.height = `${Math.min(inputEl.scrollHeight, 180)}px`;
}

// ══════════ 模型管理 ══════════

function openModelModal() {
  modelModal.classList.add('open');
  modelModal.setAttribute('aria-hidden', 'false');
  // 先用启动时已加载的列表立即渲染，再后台刷新一次，避免空白等待
  renderModelCards();
  loadModels().catch((err) => toast(err.message, 'error', err.requestId));
}

function closeModelModal() {
  modelModal.classList.remove('open');
  modelModal.setAttribute('aria-hidden', 'true');
  resetForm();
}

function renderModelCards() {
  modelList.innerHTML = '';

  if (!state.allModels.length) {
    modelList.appendChild(el('li', 'muted', '暂无模型配置'));
    return;
  }

  for (const m of state.allModels) modelList.appendChild(buildModelCard(m));
}

function buildModelCard(m) {
  const li = el('li', `model-card${m.is_enabled ? '' : ' disabled'}`);

  const head = el('div', 'model-card-head');
  head.appendChild(el('span', 'name', m.display_name || m.model_name));
  head.appendChild(el('span', 'tag', PROVIDER_LABEL[m.provider] || m.provider));
  if (m.is_builtin) head.appendChild(el('span', 'tag tag-muted', '内置'));
  if (!m.is_enabled) head.appendChild(el('span', 'tag tag-off', '已停用'));
  li.appendChild(head);

  const bits = [`标识 ${m.model_name}`];
  if (m.base_url) bits.push(`地址 ${m.base_url}`);
  if (m.upstream_model) bits.push(`上游 ${m.upstream_model}`);
  bits.push(m.has_api_key ? 'Key 已配置' : '免鉴权');
  li.appendChild(el('div', 'model-meta', bits.join(' · ')));

  const actions = el('div', 'model-actions');
  actions.appendChild(smallBtn('测试连通性', () => testModel(m, li)));
  if (!m.is_builtin) {
    actions.appendChild(smallBtn('编辑', () => startEdit(m)));
    actions.appendChild(smallBtn(m.is_enabled ? '停用' : '启用', () => toggleModel(m)));
    actions.appendChild(smallBtn('删除', () => removeModel(m), 'danger'));
  }
  li.appendChild(actions);

  return li;
}

function smallBtn(text, onClick, extraCls = '') {
  const btn = el('button', `small-btn ${extraCls}`.trim(), text);
  btn.type = 'button';
  btn.addEventListener('click', onClick);
  return btn;
}

async function testModel(m, card) {
  let box = card.querySelector('.test-result');
  if (!box) {
    box = el('p', 'test-result');
    card.appendChild(box);
  }
  box.className = 'test-result';
  box.textContent = '正在测试…';

  try {
    const res = await api(`/api/models/${m.id}/test`, { method: 'POST' });
    box.className = 'test-result ok';
    box.textContent = `连通正常 · ${res.latency_ms} ms · 返回：${res.answer_preview}`;
  } catch (err) {
    box.className = 'test-result bad';
    box.textContent = `连接失败：${err.message}`
      + (err.code ? `（${err.code}）` : '')
      + (err.requestId ? ` request_id: ${err.requestId}` : '');
  }
}

async function toggleModel(m) {
  try {
    await api(`/api/models/${m.id}`, {
      method: 'PATCH',
      body: { is_enabled: !m.is_enabled },
    });
    await loadModels();
    toast(m.is_enabled ? '已停用' : '已启用', 'success');
  } catch (err) {
    toast(err.message, 'error', err.requestId);
  }
}

async function removeModel(m) {
  if (!window.confirm(`删除模型「${m.display_name || m.model_name}」？\n删除后不可恢复。`)) return;
  try {
    await api(`/api/models/${m.id}`, { method: 'DELETE' });
    await loadModels();
    toast('已删除', 'success');
  } catch (err) {
    toast(err.message, 'error', err.requestId);
  }
}

// ── 表单：新增 / 编辑 ──

const fName = $('#fName');
const fDisplay = $('#fDisplay');
const fProvider = $('#fProvider');
const fUpstream = $('#fUpstream');
const fBaseUrl = $('#fBaseUrl');
const fApiKey = $('#fApiKey');

const URL_PRESET = {
  local: 'http://127.0.0.1:11434/v1',
  deepseek: 'https://api.deepseek.com/v1',
  custom: 'https://api.example.com/v1',
};

function startCreate() {
  state.editingId = null;
  formTitle.textContent = '新增模型';
  modelForm.hidden = false;

  fName.value = '';
  fDisplay.value = '';
  fProvider.value = 'local';
  fProvider.disabled = false;
  fUpstream.value = 'qwen2.5:7b';
  fBaseUrl.value = URL_PRESET.local;
  fApiKey.value = '';
  fApiKey.placeholder = 'sk-...（本地模型留空）';

  fName.disabled = false;
  fName.focus();
}

function startEdit(m) {
  state.editingId = m.id;
  formTitle.textContent = `编辑模型：${m.display_name || m.model_name}`;
  modelForm.hidden = false;

  fName.value = m.model_name;
  fName.disabled = true;
  fDisplay.value = m.display_name || '';
  fProvider.value = m.provider;
  fProvider.disabled = true;
  fUpstream.value = m.upstream_model || '';
  fBaseUrl.value = m.base_url || '';
  fApiKey.value = '';
  fApiKey.placeholder = m.has_api_key ? '留空表示不修改已保存的 Key' : '未配置 Key';

  fDisplay.focus();
}

function resetForm() {
  state.editingId = null;
  modelForm.hidden = true;
  fName.disabled = false;
  fProvider.disabled = false;
}

async function submitForm(event) {
  event.preventDefault();

  const displayName = fDisplay.value.trim();
  const upstream = fUpstream.value.trim();
  const baseUrl = fBaseUrl.value.trim();
  const apiKey = fApiKey.value.trim();
  const isEdit = state.editingId !== null;

  if (!isEdit && !NAME_RE.test(fName.value.trim())) {
    toast('标识只允许字母、数字、点、下划线、短横线，长度 2~40');
    return;
  }
  if (!displayName) {
    toast('请填写展示名称');
    return;
  }
  if (!upstream) {
    toast('请填写上游模型名，例如 qwen2.5:7b');
    return;
  }
  if (!/^https?:\/\/.+/.test(baseUrl)) {
    toast('服务地址必须以 http:// 或 https:// 开头');
    return;
  }

  const saveBtn = $('#saveModelBtn');
  saveBtn.disabled = true;

  try {
    if (isEdit) {
      const body = { display_name: displayName, base_url: baseUrl, upstream_model: upstream };
      // Key 留空表示不修改，只有填写了新 Key 才提交
      if (apiKey) body.api_key = apiKey;
      await api(`/api/models/${state.editingId}`, { method: 'PATCH', body });
    } else {
      await api('/api/models', {
        method: 'POST',
        body: {
          user_id: state.userId,
          name: fName.value.trim(),
          display_name: displayName,
          provider: fProvider.value,
          base_url: baseUrl,
          upstream_model: upstream,
          api_key: apiKey,
        },
      });
    }
    resetForm();
    await loadModels();
    toast(isEdit ? '已更新' : '已新增', 'success');
  } catch (err) {
    toast(err.message, 'error', err.requestId);
  } finally {
    saveBtn.disabled = false;
  }
}

// ══════════ 平台公告 ══════════

const LEVEL_LABEL = { info: '公告', warning: '重要', critical: '紧急' };

function dismissedIds() {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(ANNOUNCE_KEY) || '[]'));
  } catch {
    return new Set();
  }
}

function rememberDismissed(ids) {
  const set = dismissedIds();
  ids.forEach((id) => set.add(id));
  sessionStorage.setItem(ANNOUNCE_KEY, JSON.stringify([...set]));
}

function openAnnounceModal(items) {
  state.announcements = items;
  announceList.innerHTML = '';
  items.forEach(renderAnnounceItem);
  announceModal.classList.add('open');
  announceModal.setAttribute('aria-hidden', 'false');
}

function closeAnnounceModal() {
  announceModal.classList.remove('open');
  announceModal.setAttribute('aria-hidden', 'true');
}

function renderAnnounceItem(item) {
  const li = el('li', `announce-item level-${item.level}`);

  const head = el('div', 'announce-item-head');
  head.appendChild(el('span', 'announce-level', LEVEL_LABEL[item.level] || '公告'));
  head.appendChild(el('strong', null, item.title));
  li.appendChild(head);

  // 纯文本渲染，换行由 CSS white-space: pre-line 保留
  li.appendChild(el('p', 'announce-content', item.content));

  const foot = el('div', 'announce-item-foot');
  foot.appendChild(el('span', 'muted', `${item.publish_at.slice(0, 16)} 发布`));
  const ok = el('button', 'small-btn', '知道了');
  ok.type = 'button';
  ok.addEventListener('click', () => readAnnouncement(item, li));
  foot.appendChild(ok);
  li.appendChild(foot);

  announceList.appendChild(li);
}

async function readAnnouncement(item, node) {
  try {
    await api(`/api/announcements/${item.id}/read`, { method: 'POST' });
  } catch {
    // 公告可能已下线或网络异常，不影响本次阅读
  }
  rememberDismissed([item.id]);
  node.remove();
  state.announcements = state.announcements.filter((a) => a.id !== item.id);
  if (!state.announcements.length) closeAnnounceModal();
}

async function readAllAnnouncements() {
  const items = state.announcements.slice();
  announceAllRead.disabled = true;
  try {
    await Promise.all(items.map((a) =>
      api(`/api/announcements/${a.id}/read`, { method: 'POST' }).catch(() => null)));
  } finally {
    rememberDismissed(items.map((a) => a.id));
    state.announcements = [];
    announceList.innerHTML = '';
    announceAllRead.disabled = false;
    closeAnnounceModal();
  }
}

/** 稍后再说 / 点遮罩 / 按 ESC：不记已读，仅本次登录不再弹 */
function dismissAnnouncements() {
  rememberDismissed(state.announcements.map((a) => a.id));
  closeAnnounceModal();
}

async function loadAnnouncements() {
  const data = await api('/api/announcements/pending');
  const dismissed = dismissedIds();
  const items = (data.items || []).filter((a) => !dismissed.has(a.id));
  state.announcements = items;
  if (items.length) openAnnounceModal(items);
}

// ══════════ 使用统计 ══════════

const LOG_STATUS_LABEL = { success: '成功', error: '失败' };

/** 数字加千分位，Token 动辄上万，可读性更好 */
function num(value) {
  return Number(value || 0).toLocaleString('zh-CN');
}

async function openUsageModal() {
  usageModal.classList.add('open');
  usageModal.setAttribute('aria-hidden', 'false');
  await loadUsage();
}

function closeUsageModal() {
  usageModal.classList.remove('open');
  usageModal.setAttribute('aria-hidden', 'true');
}

async function loadUsage() {
  usageList.innerHTML = '';
  usageEmpty.hidden = true;
  try {
    // 汇总与列表并行请求：同一用户、同一窗口，口径一致
    const [summary, logs] = await Promise.all([
      api('/api/logs/summary?days=30'),
      api('/api/logs?page=1&page_size=20'),
    ]);
    renderUsageSummary(summary);
    renderUsageLogs(logs.items || []);
  } catch (err) {
    toast(err.message, 'error', err.requestId);
  }
}

function renderUsageSummary(s) {
  $('#usageCalls').textContent = num(s.total_calls);
  $('#usageRate').textContent = `${Math.round((s.success_rate || 0) * 100)}%`;
  $('#usageLatency').textContent = `${num(s.avg_latency_ms)} ms`;
  $('#usageTokens').textContent = num(s.total_tokens);
}

function renderUsageLogs(items) {
  usageEmpty.hidden = items.length > 0;
  for (const item of items) usageList.appendChild(buildUsageItem(item));
}

function buildUsageItem(item) {
  const li = el('li', 'usage-item');

  const head = el('div', 'usage-item-head');
  head.appendChild(el('span', 'tag', item.model_name));
  head.appendChild(el('span', `status-tag status-${item.status}`,
    LOG_STATUS_LABEL[item.status] || item.status));
  head.appendChild(el('span', 'usage-time', item.created_at.slice(0, 16)));
  li.appendChild(head);

  const bits = [`耗时 ${num(item.latency_ms)} ms`];
  if (item.status === 'success') {
    bits.push(`token ${num(item.prompt_tokens)} + ${num(item.completion_tokens)}`
      + ` = ${num(item.total_tokens)}`);
  }
  li.appendChild(el('div', 'usage-meta', bits.join(' · ')));

  // 失败原因对用户可读，后端已做脱敏，直接展示
  if (item.status === 'error' && item.error_message) {
    li.appendChild(el('p', 'usage-error', item.error_message));
  }
  return li;
}

// ══════════ 事件绑定 ══════════

function bindEvents() {
  sendBtn.addEventListener('click', send);

  inputEl.addEventListener('input', autoGrow);
  inputEl.addEventListener('keydown', (e) => {
    // 输入法选词时不触发发送
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send();
    }
  });

  $('#chips').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    inputEl.value = chip.textContent;
    autoGrow();
    send();
  });

  modelSelect.addEventListener('change', () => {
    state.modelName = modelSelect.value;
    localStorage.setItem('qa.modelName', state.modelName);
  });

  $('#logoutBtn').addEventListener('click', () => {
    if (!window.confirm('确定退出登录？')) return;
    Auth.logout();
  });

  $('#newChatBtn').addEventListener('click', () => {
    state.activeSessionId = null;
    clearMessages();
    renderHistory();
    inputEl.focus();
    if (window.innerWidth <= 860) closeSidebar();
  });

  $('#openModelBtn').addEventListener('click', openModelModal);
  $('#openUsageBtn').addEventListener('click', openUsageModal);

  modelModal.addEventListener('click', (e) => {
    if (e.target.closest('[data-close]')) closeModelModal();
  });

  usageModal.addEventListener('click', (e) => {
    if (e.target.closest('[data-usage-close]')) closeUsageModal();
  });

  announceModal.addEventListener('click', (e) => {
    if (e.target.closest('[data-announce-close]')) dismissAnnouncements();
  });
  announceAllRead.addEventListener('click', readAllAnnouncements);

  modelForm.addEventListener('submit', submitForm);
  $('#addModelBtn').addEventListener('click', startCreate);
  $('#cancelFormBtn').addEventListener('click', resetForm);

  fProvider.addEventListener('change', () => {
    const preset = URL_PRESET[fProvider.value] || '';
    if (!fBaseUrl.value.trim()) fBaseUrl.value = preset;
  });

  $('#menuBtn').addEventListener('click', openSidebar);
  scrim.addEventListener('click', closeSidebar);

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    // 公告弹窗层级更高，优先关闭
    if (announceModal.classList.contains('open')) {
      dismissAnnouncements();
    } else if (usageModal.classList.contains('open')) {
      closeUsageModal();
    } else if (modelModal.classList.contains('open')) {
      closeModelModal();
    }
  });
}

function openSidebar() {
  sidebar.classList.add('open');
  scrim.classList.add('show');
}

function closeSidebar() {
  sidebar.classList.remove('open');
  scrim.classList.remove('show');
}

// ══════════ 启动 ══════════

async function init() {
  const user = Auth.requireAuth();
  if (!user) return;

  renderUser(user);
  bindEvents();
  setStatus('就绪');

  // 后台同步最新状态：管理员审核/禁用后，页面无需重新登录即可感知
  Auth.updateMe().then(renderUser).catch(() => {});

  try {
    await loadModels();
    await loadHistory();
    await loadAnnouncements();
  } catch (err) {
    toast(`初始化失败：${err.message}`, 'error', err.requestId);
  }

  inputEl.focus();
}

init();
