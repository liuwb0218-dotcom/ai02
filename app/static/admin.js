/* 管理后台 — 用户审核队列（默认待审核，每页 20 条） */
const $ = (sel) => document.querySelector(sel);

const state = {
  page: 1,
  pageSize: 20,
  status: 'pending',
  keyword: '',
  managedBy: null,
  managers: [],
  total: 0,
  items: [],
  selected: new Set(),
  user: null,
  groups: {
    items: [],
    unassigned: 0,
    unassignedPending: 0,
    loaded: false,
  },
  ann: {
    page: 1,
    pageSize: 20,
    status: '',
    keyword: '',
    total: 0,
    userTotal: 0,
    items: [],
    editing: null,   // null=新建，否则为正在编辑的公告
    loaded: false,
  },
  logs: {
    page: 1,
    pageSize: 20,
    userId: '',
    status: '',
    model: '',
    days: '30',
    total: 0,
    items: [],
    loaded: false,
  },
};

const STATUS_LABEL = { pending: '待审核', active: '已审核', disabled: '已禁用' };
const ROLE_LABEL = { super_admin: '超级管理员', admin: '管理员', user: '普通用户' };
const LEVEL_LABEL = { info: '公告', warning: '重要', critical: '紧急' };
const ANN_STATUS_LABEL = { draft: '草稿', published: '已发布', revoked: '已撤回' };
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
  return value ? value.slice(0, 16) : '—';
}

function trialText(u) {
  if (u.status === 'pending') {
    if (u.trial_expired) return '试用已结束，等待审核';
    return `剩余 ${u.trial_days_left ?? 0} 天（至 ${fmt(u.trial_expires_at)}）`;
  }
  if (u.status === 'disabled') {
    const reason = u.disabled_reason || '管理员禁用';
    return u.disabled_until ? `${reason}（至 ${fmt(u.disabled_until)}）` : `${reason}（永久）`;
  }
  return '永久可用';
}

function isSuper(u) {
  return u.role === 'super_admin';
}

/** 非管理员访问时原地展示无权限提示页，避免自动跳转造成"一闪而过" */
function showDenied() {
  $('#adminApp').hidden = true;
  $('#adminDenied').hidden = false;
  document.title = '无权限 · AI 智能问答平台';
}

// ══════════ 数据加载 ══════════

async function load() {
  const params = new URLSearchParams({
    page: String(state.page),
    page_size: String(state.pageSize),
  });
  if (state.status) params.set('status', state.status);
  if (state.keyword) params.set('keyword', state.keyword);
  if (state.managedBy !== null) params.set('managed_by', String(state.managedBy));

  try {
    const data = await Auth.api(`/api/admin/users?${params.toString()}`);
    state.items = data.items || [];
    state.total = data.total || 0;
    state.selected.clear();
    renderStats(data.counts || {});
    renderRows();
    renderPager();
  } catch (err) {
    if (err.status === 403) {
      showDenied();
      return;
    }
    toast(err.message || '加载失败');
  }
}

function renderStats(counts) {
  $('#statPending').textContent = counts.pending ?? 0;
  $('#statActive').textContent = counts.active ?? 0;
  $('#statDisabled').textContent = counts.disabled ?? 0;
  $('#statTotal').textContent = counts.total ?? 0;
}

function renderRows() {
  const tbody = $('#userRows');
  tbody.innerHTML = '';
  $('#adminEmpty').hidden = state.items.length > 0;

  for (const u of state.items) {
    const tr = el('tr');

    const checkCell = el('td', 'col-check');
    const check = el('input');
    check.type = 'checkbox';
    check.checked = state.selected.has(u.id);
    check.addEventListener('change', () => {
      if (check.checked) state.selected.add(u.id);
      else state.selected.delete(u.id);
      syncSelection();
    });
    checkCell.appendChild(check);
    tr.appendChild(checkCell);

    const userCell = el('td');
    userCell.appendChild(el('strong', null, u.username));
    userCell.appendChild(el('small', 'muted', ` #${u.id}`));
    tr.appendChild(userCell);

    const roleCell = el('td');
    roleCell.appendChild(el('span', `role-badge role-${u.role}`,
      ROLE_LABEL[u.role] || u.role));
    tr.appendChild(roleCell);
    tr.appendChild(managerCell(u));
    tr.appendChild(el('td', null, STATUS_LABEL[u.status] || u.status));
    tr.appendChild(el('td', 'muted', trialText(u)));
    tr.appendChild(el('td', 'muted', fmt(u.created_at)));
    tr.appendChild(el('td', 'muted', fmt(u.last_login_at)));

    const actions = el('td', 'col-actions');
    if (u.status === 'pending') {
      actions.appendChild(btn('通过', () => review(u, 'active'), 'btn-primary'));
      actions.appendChild(btn('拒绝', () => review(u, 'disabled'), 'danger'));
    } else if (u.status === 'active' && !isSuper(u)) {
      actions.appendChild(btn('禁用', () => review(u, 'disabled'), 'danger'));
    } else if (u.status === 'disabled' && !isSuper(u)) {
      actions.appendChild(btn('启用', () => review(u, 'active')));
    }
    // 角色任命只对超管开放，且只作用于已审核的成员
    if (isSuper(state.user) && u.status === 'active' && !isSuper(u)) {
      if (u.role === 'admin') {
        actions.appendChild(btn('取消管理员', () => changeRole(u, 'user'), 'danger'));
      } else {
        actions.appendChild(btn('设为管理员', () => changeRole(u, 'admin')));
      }
    }
    tr.appendChild(actions);

    tbody.appendChild(tr);
  }
  syncSelection();
}

/** 归属列：超管可用下拉直接调整，其他角色只读 */
function managerCell(u) {
  if (u.role !== 'user') return el('td', 'muted', '—');
  if (!isSuper(state.user)) return el('td', 'muted', u.manager_name || '未分配');

  const sel = el('select', 'select manager-select');
  sel.title = '调整归属管理员';
  const options = [{ id: '', name: '未分配' }].concat(
    state.managers.map((m) => ({ id: String(m.manager_id), name: m.manager_name })),
  );
  for (const opt of options) {
    const node = el('option', null, opt.name);
    node.value = opt.id;
    sel.appendChild(node);
  }
  sel.value = u.managed_by == null ? '' : String(u.managed_by);
  sel.addEventListener('change', () => changeManager(u, sel));
  const cell = el('td');
  cell.appendChild(sel);
  return cell;
}

async function changeManager(u, sel) {
  const value = sel.value === '' ? null : Number(sel.value);
  sel.disabled = true;
  try {
    const updated = await Auth.api(`/api/admin/users/${u.id}/manager`, {
      method: 'PATCH',
      body: { managed_by: value },
    });
    u.managed_by = updated.managed_by;
    u.manager_name = updated.manager_name || null;
    toast(value === null ? '已移回未分配' : `已调整到 ${updated.manager_name}`, 'success');
    refreshGroups();
  } catch (err) {
    toast(err.message || '调整归属失败');
    sel.value = u.managed_by == null ? '' : String(u.managed_by);
  } finally {
    sel.disabled = false;
  }
}

function btn(text, onClick, extra = '') {
  const b = el('button', `small-btn ${extra}`.trim(), text);
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}

function syncSelection() {
  const count = state.selected.size;
  $('#selectedTip').textContent = count ? `已选择 ${count} 个用户` : '未选择';
  $('#batchApprove').disabled = count === 0;
  $('#batchReject').disabled = count === 0;
  $('#checkAll').checked = state.items.length > 0
    && state.items.every((u) => state.selected.has(u.id));
}

function renderPager() {
  const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
  $('#pageInfo').textContent = `第 ${state.page} / ${pages} 页 · 共 ${state.total} 人`;
  $('#prevPage').disabled = state.page <= 1;
  $('#nextPage').disabled = state.page >= pages;
}

// ══════════ 公告管理 ══════════

function pad(n) {
  return String(n).padStart(2, '0');
}

function nowText() {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
    + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function toLocalInput(value) {
  return value ? value.replace(' ', 'T').slice(0, 16) : '';
}

function fromLocalInput(value) {
  if (!value) return null;
  return `${value.replace('T', ' ')}:00`;
}

function snippet(text) {
  return (text || '').replace(/\s+/g, ' ').slice(0, 60);
}

const TABS = {
  users: ['#tabUsers', '#usersPanel'],
  groups: ['#tabGroups', '#groupsPanel'],
  announcements: ['#tabAnnouncements', '#announcePanel'],
  logs: ['#tabLogs', '#logsPanel'],
};

function switchTab(name) {
  for (const [key, [tabSel, panelSel]] of Object.entries(TABS)) {
    $(tabSel).classList.toggle('active', key === name);
    $(panelSel).hidden = key !== name;
  }
  // 面板首次打开时才加载，避免进入后台就发三个请求
  if (name === 'announcements' && !state.ann.loaded) {
    state.ann.loaded = true;
    loadAnnouncements();
  }
  if (name === 'groups' && !state.groups.loaded) {
    state.groups.loaded = true;
    loadGroups();
  }
  if (name === 'logs' && !state.logs.loaded) {
    state.logs.loaded = true;
    loadLogs();
  }
}

// ══════════ 分组视图（仅超管） ══════════

async function loadGroups() {
  try {
    const data = await Auth.api('/api/admin/groups');
    state.groups.items = data.items || [];
    state.managers = state.groups.items;
    state.groups.unassigned = data.unassigned || 0;
    state.groups.unassignedPending = data.unassigned_pending || 0;
    renderGroups();
  } catch (err) {
    toast(err.message || '分组加载失败');
  }
}

function renderGroups() {
  const tbody = $('#groupRows');
  tbody.innerHTML = '';

  const items = state.groups.items;
  const assigned = items.reduce((sum, g) => sum + g.total, 0);
  $('#groupManagers').textContent = items.length;
  $('#groupAssigned').textContent = assigned;
  $('#groupUnassigned').textContent = state.groups.unassigned;
  $('#groupUnassignedPending').textContent = state.groups.unassignedPending;
  $('#groupEmpty').hidden = items.length > 0 || state.groups.unassigned > 0;

  for (const g of items) {
    const tr = el('tr');
    const nameCell = el('td');
    nameCell.appendChild(el('strong', null, g.manager_name));
    nameCell.appendChild(el('small', 'muted', ` #${g.manager_id}`));
    tr.appendChild(nameCell);
    tr.appendChild(el('td', null, g.active));
    tr.appendChild(el('td', null, g.pending));
    tr.appendChild(el('td', null, g.disabled));
    tr.appendChild(el('td', null, g.total));
    const actions = el('td', 'col-actions');
    actions.appendChild(btn('查看本组', () => viewManaged(g.manager_id, g.manager_name)));
    tr.appendChild(actions);
    tbody.appendChild(tr);
  }

  if (state.groups.unassigned > 0) {
    const tr = el('tr');
    tr.appendChild(el('td', null, '未分配'));
    tr.appendChild(el('td', 'muted', '—'));
    tr.appendChild(el('td', null, state.groups.unassignedPending));
    tr.appendChild(el('td', 'muted', '—'));
    tr.appendChild(el('td', null, state.groups.unassigned));
    const actions = el('td', 'col-actions');
    actions.appendChild(btn('查看未分配', () => viewManaged(0, '未分配')));
    tr.appendChild(actions);
    tbody.appendChild(tr);
  }
}

function viewManaged(managerId, name) {
  state.managedBy = managerId;
  state.page = 1;
  state.status = '';
  state.keyword = '';
  $('#statusFilter').value = '';
  $('#keyword').value = '';
  $('#managedFilterBar').hidden = false;
  $('#managedFilterText').textContent =
    managerId === 0 ? '未分配用户' : `管理员：${name}`;
  switchTab('users');
  load();
}

function clearManagedFilter() {
  state.managedBy = null;
  state.page = 1;
  $('#managedFilterBar').hidden = true;
  load();
}

function refreshGroups() {
  if (state.groups.loaded) loadGroups();
}

async function loadAnnouncements() {
  const params = new URLSearchParams({
    page: String(state.ann.page),
    page_size: String(state.ann.pageSize),
  });
  if (state.ann.status) params.set('status', state.ann.status);
  if (state.ann.keyword) params.set('keyword', state.ann.keyword);

  try {
    const data = await Auth.api(`/api/admin/announcements?${params.toString()}`);
    state.ann.items = data.items || [];
    state.ann.total = data.total || 0;
    state.ann.userTotal = data.user_total || 0;
    renderAnnSummary(data.counts || {});
    renderAnnRows();
    renderAnnPager();
  } catch (err) {
    toast(err.message || '公告加载失败');
  }
}

function renderAnnSummary(counts) {
  $('#annSummary').textContent =
    `共 ${counts.total ?? 0} 条 · 已发布 ${counts.published ?? 0} · `
    + `用户 ${state.ann.userTotal} 人`;
}

function renderAnnRows() {
  const tbody = $('#annRows');
  tbody.innerHTML = '';
  $('#annEmpty').hidden = state.ann.items.length > 0;

  for (const a of state.ann.items) {
    const tr = el('tr');

    const titleCell = el('td', 'announce-title-cell');
    titleCell.appendChild(el('strong', null, a.title));
    titleCell.appendChild(el('small', null, snippet(a.content)));
    tr.appendChild(titleCell);

    const levelCell = el('td');
    levelCell.appendChild(el('span', `level-tag level-${a.level}`,
      LEVEL_LABEL[a.level] || a.level));
    tr.appendChild(levelCell);

    const statusCell = el('td');
    statusCell.appendChild(el('span', `status-tag status-${a.status}`,
      ANN_STATUS_LABEL[a.status] || a.status));
    tr.appendChild(statusCell);

    tr.appendChild(el('td', 'muted', fmt(a.publish_at)));
    tr.appendChild(el('td', 'muted', a.expire_at ? fmt(a.expire_at) : '长期有效'));

    const readCell = el('td');
    readCell.textContent = `${a.read_users} / ${state.ann.userTotal} 人`;
    readCell.title = `累计阅读 ${a.read_count} 次`;
    tr.appendChild(readCell);

    tr.appendChild(el('td', 'muted', a.creator_name || `#${a.created_by}`));

    const actions = el('td', 'col-actions');
    if (a.status !== 'revoked') {
      actions.appendChild(btn('编辑', () => openAnnEditor(a)));
      if (a.status === 'published') {
        actions.appendChild(btn('撤回', () => revokeAnnouncement(a), 'danger'));
      }
    } else {
      actions.appendChild(el('span', 'muted', '已撤回'));
    }
    tr.appendChild(actions);

    tbody.appendChild(tr);
  }
}

function renderAnnPager() {
  const pages = Math.max(1, Math.ceil(state.ann.total / state.ann.pageSize));
  $('#annPageInfo').textContent =
    `第 ${state.ann.page} / ${pages} 页 · 共 ${state.ann.total} 条`;
  $('#annPrevPage').disabled = state.ann.page <= 1;
  $('#annNextPage').disabled = state.ann.page >= pages;
}

// ══════════ 调用日志 ══════════

async function loadLogs() {
  const params = new URLSearchParams({
    page: String(state.logs.page),
    page_size: String(state.logs.pageSize),
  });
  if (state.logs.userId) params.set('user_id', state.logs.userId);
  if (state.logs.status) params.set('status', state.logs.status);
  if (state.logs.model) params.set('model_name', state.logs.model);
  if (state.logs.days) params.set('days', state.logs.days);

  try {
    const data = await Auth.api(`/api/admin/logs?${params.toString()}`);
    state.logs.items = data.items || [];
    state.logs.total = data.total || 0;
    renderLogRows();
    renderLogPager();
  } catch (err) {
    toast(err.message || '日志加载失败');
  }
}

function renderLogRows() {
  const tbody = $('#logRows');
  tbody.innerHTML = '';
  $('#logEmpty').hidden = state.logs.items.length > 0;

  for (const log of state.logs.items) {
    const tr = el('tr');
    tr.appendChild(el('td', 'muted', fmt(log.created_at)));

    const userCell = el('td');
    userCell.appendChild(el('strong', null, log.username || `#${log.user_id}`));
    userCell.appendChild(el('small', 'muted', ` #${log.user_id}`));
    tr.appendChild(userCell);

    tr.appendChild(el('td', null, log.model_name));

    const statusCell = el('td');
    statusCell.appendChild(el('span', `status-tag status-${log.status}`,
      LOG_STATUS_LABEL[log.status] || log.status));
    tr.appendChild(statusCell);

    tr.appendChild(el('td', 'muted', `${log.latency_ms} ms`));
    tr.appendChild(el('td', 'muted', String(log.prompt_tokens)));
    tr.appendChild(el('td', 'muted', String(log.completion_tokens)));
    tr.appendChild(el('td', 'log-total', String(log.total_tokens)));
    tr.appendChild(el('td', 'log-error', log.error_message || '—'));

    tbody.appendChild(tr);
  }
}

function renderLogPager() {
  const pages = Math.max(1, Math.ceil(state.logs.total / state.logs.pageSize));
  $('#logSummary').textContent = `共 ${state.logs.total} 条`;
  $('#logPageInfo').textContent =
    `第 ${state.logs.page} / ${pages} 页 · 共 ${state.logs.total} 条`;
  $('#logPrevPage').disabled = state.logs.page <= 1;
  $('#logNextPage').disabled = state.logs.page >= pages;
}

// ── 编辑弹窗 ──

function syncPublishMode() {
  const mode = $('#annPublishMode').value;
  $('#annPublishAt').disabled = mode !== 'schedule';
  if (mode === 'schedule' && !$('#annPublishAt').value) {
    $('#annPublishAt').value = toLocalInput(nowText()).slice(0, 16);
  }
}

function openAnnEditor(a) {
  state.ann.editing = a || null;

  const isEdit = Boolean(a);
  $('#annModalTitle').textContent = isEdit ? '编辑公告' : '发布公告';
  $('#annTitle').value = a?.title || '';
  $('#annContent').value = a?.content || '';
  $('#annLevel').value = a?.level || 'info';
  $('#annExpireAt').value = a?.expire_at ? toLocalInput(a.expire_at) : '';

  if (!isEdit) {
    $('#annPublishMode').value = 'now';
    $('#annPublishAt').value = '';
  } else if (a.status === 'draft') {
    $('#annPublishMode').value = 'draft';
    $('#annPublishAt').value = toLocalInput(a.publish_at);
  } else if (a.publish_at > nowText()) {
    $('#annPublishMode').value = 'schedule';
    $('#annPublishAt').value = toLocalInput(a.publish_at);
  } else {
    $('#annPublishMode').value = 'now';
    $('#annPublishAt').value = toLocalInput(a.publish_at);
  }
  syncPublishMode();

  $('#annFormTip').textContent = isEdit
    ? '保存后立即生效；已撤回的公告不可编辑。'
    : '发布后所有可登录用户下次进入页面时都会看到弹窗；撤回后立即不再弹出。';
  $('#annSaveBtn').textContent = isEdit ? '保存' : '发布';

  $('#annModal').classList.add('open');
  $('#annModal').setAttribute('aria-hidden', 'false');
  $('#annTitle').focus();
}

function closeAnnEditor() {
  $('#annModal').classList.remove('open');
  $('#annModal').setAttribute('aria-hidden', 'true');
  state.ann.editing = null;
}

async function submitAnnouncement(event) {
  event.preventDefault();

  const title = $('#annTitle').value.trim();
  const content = $('#annContent').value.trim();
  const mode = $('#annPublishMode').value;
  const editing = state.ann.editing;

  if (!title) return toast('请填写公告标题');
  if (!content) return toast('请填写公告正文');

  let publishAt = null;
  if (mode === 'schedule') {
    publishAt = fromLocalInput($('#annPublishAt').value);
    if (!publishAt) return toast('定时发布请选择生效时间');
  } else if (mode === 'now' && (!editing || editing.status !== 'published'
             || editing.publish_at > nowText())) {
    // 新建、草稿转发布、定时转立即发布都需要把生效时间设为当前
    publishAt = nowText();
  }

  const body = {
    title,
    content,
    level: $('#annLevel').value,
    status: mode === 'draft' ? 'draft' : 'published',
    expire_at: fromLocalInput($('#annExpireAt').value) || '',
  };
  if (publishAt) body.publish_at = publishAt;

  const saveBtn = $('#annSaveBtn');
  saveBtn.disabled = true;
  try {
    if (editing) {
      await Auth.api(`/api/admin/announcements/${editing.id}`, {
        method: 'PATCH',
        body,
      });
    } else {
      await Auth.api('/api/admin/announcements', { method: 'POST', body });
    }
    closeAnnEditor();
    toast(editing ? '公告已更新' : (mode === 'draft' ? '已保存草稿' : '公告已发布'),
      'success');
    await loadAnnouncements();
  } catch (err) {
    toast(err.message || '保存失败');
  } finally {
    saveBtn.disabled = false;
  }
}

async function revokeAnnouncement(a) {
  if (!window.confirm(`撤回公告「${a.title}」？\n撤回后用户端立即不再弹出，已读记录保留。`)) {
    return;
  }
  try {
    await Auth.api(`/api/admin/announcements/${a.id}/revoke`, { method: 'POST' });
    toast('已撤回', 'success');
    await loadAnnouncements();
  } catch (err) {
    toast(err.message || '撤回失败');
  }
}

// ══════════ 操作 ══════════

async function review(user, status) {
  let reason = null;
  if (status === 'disabled') {
    const input = window.prompt(
      `确定${user.status === 'pending' ? '拒绝' : '禁用'}用户「${user.username}」？\n可填写原因（留空使用默认说明）`,
      user.status === 'pending' ? '审核未通过，请联系管理员' : '管理员已禁用该账号',
    );
    if (input === null) return;
    reason = input.trim() || null;
  } else if (!window.confirm(`确定通过/启用用户「${user.username}」？通过后将永久可用。`)) {
    return;
  }

  try {
    await Auth.api(`/api/admin/users/${user.id}/status`, {
      method: 'PATCH',
      body: { status, reason },
    });
    toast(status === 'active' ? '已通过审核' : '已更新为禁用', 'success');
    await load();
    refreshGroups();
  } catch (err) {
    toast(err.message || '操作失败');
  }
}

async function changeRole(user, role) {
  const label = role === 'admin' ? '设为管理员' : '取消管理员';
  const tip = role === 'admin'
    ? '将获得审核用户、管理公告、查看全员日志的权限。'
    : '将失去管理后台权限，回到普通用户。';
  if (!window.confirm(`确定将用户「${user.username}」${label}？\n${tip}\n该用户需要重新登录后生效。`)) {
    return;
  }

  try {
    await Auth.api(`/api/admin/users/${user.id}/role`, {
      method: 'PATCH',
      body: { role },
    });
    toast(`${label}成功`, 'success');
    await load();
    refreshGroups();
  } catch (err) {
    toast(err.message || '操作失败');
  }
}

async function batch(action) {
  const ids = Array.from(state.selected);
  if (!ids.length) return;

  let reason = null;
  if (action === 'reject') {
    const input = window.prompt(
      `批量拒绝 ${ids.length} 个用户，可填写原因（留空使用默认说明）`,
      '审核未通过，请联系管理员',
    );
    if (input === null) return;
    reason = input.trim() || null;
  } else if (!window.confirm(`确认通过选中的 ${ids.length} 个用户？`)) {
    return;
  }

  try {
    const res = await Auth.api('/api/admin/users/batch-review', {
      method: 'POST',
      body: { user_ids: ids, action, reason },
    });
    const skipped = res.skipped?.length
      ? `，跳过 ${res.skipped.length} 个（${res.skipped[0].reason}）`
      : '';
    toast(`已处理 ${res.updated} 个用户${skipped}`, 'success');
    await load();
    refreshGroups();
  } catch (err) {
    toast(err.message || '批量操作失败');
  }
}

// ══════════ 事件绑定 ══════════

function bindEvents() {
  $('#statusFilter').addEventListener('change', (e) => {
    state.status = e.target.value;
    state.page = 1;
    load();
  });

  const doSearch = () => {
    state.keyword = $('#keyword').value.trim();
    state.page = 1;
    load();
  };
  $('#searchBtn').addEventListener('click', doSearch);
  $('#keyword').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doSearch();
  });

  $('#checkAll').addEventListener('change', (e) => {
    state.selected.clear();
    if (e.target.checked) state.items.forEach((u) => state.selected.add(u.id));
    renderRows();
  });

  $('#batchApprove').addEventListener('click', () => batch('approve'));
  $('#batchReject').addEventListener('click', () => batch('reject'));

  $('#prevPage').addEventListener('click', () => {
    if (state.page > 1) {
      state.page -= 1;
      load();
    }
  });
  $('#nextPage').addEventListener('click', () => {
    state.page += 1;
    load();
  });

  $('#adminLogout').addEventListener('click', () => Auth.logout());

  // ── 标签页切换 ──
  $('#tabUsers').addEventListener('click', () => switchTab('users'));
  $('#tabGroups').addEventListener('click', () => switchTab('groups'));
  $('#tabAnnouncements').addEventListener('click', () => switchTab('announcements'));
  $('#tabLogs').addEventListener('click', () => switchTab('logs'));
  $('#clearManagedFilter').addEventListener('click', clearManagedFilter);

  // ── 公告管理 ──
  $('#newAnnBtn').addEventListener('click', () => openAnnEditor(null));
  $('#annForm').addEventListener('submit', submitAnnouncement);
  $('#annPublishMode').addEventListener('change', syncPublishMode);

  $('#annStatusFilter').addEventListener('change', (e) => {
    state.ann.status = e.target.value;
    state.ann.page = 1;
    loadAnnouncements();
  });

  const doAnnSearch = () => {
    state.ann.keyword = $('#annKeyword').value.trim();
    state.ann.page = 1;
    loadAnnouncements();
  };
  $('#annSearchBtn').addEventListener('click', doAnnSearch);
  $('#annKeyword').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doAnnSearch();
  });

  $('#annPrevPage').addEventListener('click', () => {
    if (state.ann.page > 1) {
      state.ann.page -= 1;
      loadAnnouncements();
    }
  });
  $('#annNextPage').addEventListener('click', () => {
    state.ann.page += 1;
    loadAnnouncements();
  });

  $('#annModal').addEventListener('click', (e) => {
    if (e.target.closest('[data-ann-close]')) closeAnnEditor();
  });

  // ── 调用日志 ──
  const doLogSearch = () => {
    const raw = $('#logUser').value.trim();
    if (raw && !/^\d+$/.test(raw)) {
      toast('用户 ID 必须是正整数');
      return;
    }
    state.logs.userId = raw;
    state.logs.model = $('#logModel').value.trim();
    state.logs.page = 1;
    loadLogs();
  };
  $('#logSearchBtn').addEventListener('click', doLogSearch);
  $('#logUser').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doLogSearch();
  });
  $('#logModel').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doLogSearch();
  });

  $('#logStatusFilter').addEventListener('change', (e) => {
    state.logs.status = e.target.value;
    state.logs.page = 1;
    loadLogs();
  });
  $('#logDays').addEventListener('change', (e) => {
    state.logs.days = e.target.value;
    state.logs.page = 1;
    loadLogs();
  });

  $('#logPrevPage').addEventListener('click', () => {
    if (state.logs.page > 1) {
      state.logs.page -= 1;
      loadLogs();
    }
  });
  $('#logNextPage').addEventListener('click', () => {
    state.logs.page += 1;
    loadLogs();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $('#annModal').classList.contains('open')) {
      closeAnnEditor();
    }
  });
}

async function init() {
  const cached = Auth.requireAuth();
  if (!cached) return;

  if (!['admin', 'super_admin'].includes(cached.role)) {
    showDenied();
    return;
  }
  state.user = cached;
  $('#adminUserName').textContent =
    `${cached.username} · ${ROLE_LABEL[cached.role] || cached.role}`;
  if (cached.role === 'super_admin') {
    $('#tabGroups').hidden = false;
    // 预载管理员列表，用户表的「归属」下拉才有选项
    state.groups.loaded = true;
    await loadGroups();
  }

  bindEvents();
  await load();
}

init();
