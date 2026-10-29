/* 我的数据 — 账号信息、近 30 天调用统计、最近调用与历史会话
 *
 * 数据来源都是现有接口，不新增后端能力：
 *   GET /api/auth/me
 *   GET /api/logs/summary?days=30
 *   GET /api/logs?page=1&page_size=10
 *   GET /api/history/{user_id}?limit=10&offset=0
 */
const $ = (sel) => document.querySelector(sel);

const ROLE_LABEL = { super_admin: '超级管理员', admin: '管理员', user: '普通用户' };
const STATUS_LABEL = { pending: '试用中（待审核）', active: '已审核', disabled: '已禁用' };
const LOG_STATUS_LABEL = { success: '成功', error: '失败' };

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function toast(message, type = 'error') {
  const node = el('div', `toast ${type}`, message);
  $('#toasts').appendChild(node);
  setTimeout(() => node.remove(), type === 'success' ? 2600 : 6000);
}

function fmt(value) {
  return value ? String(value).slice(0, 16) : '—';
}

function num(value) {
  return Number(value || 0).toLocaleString('zh-CN');
}

/** 摘要压缩：换行压成空格，超长省略，避免表格被撑开 */
function snippet(text, max = 40) {
  const flat = String(text || '').replace(/\s+/g, ' ');
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

// ══════════ 渲染 ══════════

function trialText(u) {
  if (u.status === 'pending') {
    return u.trial_expired
      ? '试用已结束，等待管理员审核'
      : `试用中（至 ${fmt(u.trial_expires_at)}）`;
  }
  if (u.status === 'disabled') {
    const reason = u.disabled_reason || '管理员禁用';
    return u.disabled_until ? `${reason}（至 ${fmt(u.disabled_until)}）` : `${reason}（永久）`;
  }
  return '永久可用';
}

function renderAccount(u) {
  $('#profileUserChip').textContent = u.username;
  $('#profileName').textContent = u.username;
  $('#profileRole').textContent = ROLE_LABEL[u.role] || u.role;
  $('#profileStatus').textContent = STATUS_LABEL[u.status] || u.status;
  $('#profileCreated').textContent = fmt(u.created_at);
  $('#profileLastLogin').textContent = fmt(u.last_login_at);
  $('#profileTrial').textContent = trialText(u);
}

function renderSummary(s) {
  $('#statCalls').textContent = num(s.total_calls);
  $('#statRate').textContent = `${Math.round((s.success_rate || 0) * 100)}%`;
  $('#statLatency').textContent = `${num(s.avg_latency_ms)} ms`;
  $('#statTokens').textContent = num(s.total_tokens);
}

function renderLogs(items) {
  const tbody = $('#profileLogRows');
  tbody.innerHTML = '';
  $('#profileLogEmpty').hidden = items.length > 0;

  for (const log of items) {
    const tr = el('tr');
    tr.appendChild(el('td', 'muted', fmt(log.created_at)));
    tr.appendChild(el('td', null, log.model_name));

    const statusCell = el('td');
    statusCell.appendChild(el('span', `status-tag status-${log.status}`,
      LOG_STATUS_LABEL[log.status] || log.status));
    tr.appendChild(statusCell);

    tr.appendChild(el('td', 'muted', `${num(log.latency_ms)} ms`));
    tr.appendChild(el('td', 'muted', num(log.prompt_tokens)));
    tr.appendChild(el('td', 'muted', num(log.completion_tokens)));
    tr.appendChild(el('td', 'log-total', num(log.total_tokens)));
    tr.appendChild(el('td', 'log-error', log.error_message || '—'));
    tbody.appendChild(tr);
  }
}

function renderHistory(items) {
  const tbody = $('#profileHistoryRows');
  tbody.innerHTML = '';
  $('#profileHistoryEmpty').hidden = items.length > 0;

  for (const item of items) {
    const tr = el('tr');
    tr.appendChild(el('td', 'muted', fmt(item.created_at)));

    const questionCell = el('td', 'profile-cell-ellipsis');
    questionCell.textContent = snippet(item.question, 36);
    questionCell.title = item.question;
    tr.appendChild(questionCell);

    const answerCell = el('td', 'profile-cell-ellipsis muted');
    answerCell.textContent = snippet(item.answer, 48);
    answerCell.title = item.answer;
    tr.appendChild(answerCell);

    tr.appendChild(el('td', null, item.model_name));
    tbody.appendChild(tr);
  }
}

// ══════════ 初始化 ══════════

async function init() {
  const cached = Auth.requireAuth();
  if (!cached) return;

  $('#profileUserChip').textContent = cached.username;
  $('#profileLogout').addEventListener('click', () => Auth.logout());

  try {
    const [me, summary, logs, history] = await Promise.all([
      Auth.api('/api/auth/me'),
      Auth.api('/api/logs/summary?days=30'),
      Auth.api('/api/logs?page=1&page_size=10'),
      Auth.api(`/api/history/${cached.id}?limit=10&offset=0`),
    ]);
    renderAccount(me);
    renderSummary(summary);
    renderLogs(logs.items || []);
    renderHistory(history.items || []);
  } catch (err) {
    toast(err.message || '加载失败');
  }
}

init();
