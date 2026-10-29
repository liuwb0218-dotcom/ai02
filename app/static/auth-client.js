/* 认证与请求封装 — 被 index/admin 页面共用
 *
 * 职责：
 *   1. 本地保存 access/refresh token 与用户信息
 *   2. 所有请求自动带 Authorization
 *   3. access 过期（401）时自动用 refresh 换新 token 并重试一次
 *   4. 会话失效时清理本地状态并跳转登录页
 */
const Auth = (() => {
  const ACCESS = 'qa.accessToken';
  const REFRESH = 'qa.refreshToken';
  const USER = 'qa.user';
  let refreshing = null;

  const getAccess = () => localStorage.getItem(ACCESS) || '';
  const getRefresh = () => localStorage.getItem(REFRESH) || '';

  function getUser() {
    try {
      return JSON.parse(localStorage.getItem(USER) || 'null');
    } catch {
      return null;
    }
  }

  function save(data) {
    localStorage.setItem(ACCESS, data.access_token);
    localStorage.setItem(REFRESH, data.refresh_token);
    localStorage.setItem(USER, JSON.stringify(data.user));
  }

  function patchUser(user) {
    localStorage.setItem(USER, JSON.stringify(user));
  }

  function clear() {
    localStorage.removeItem(ACCESS);
    localStorage.removeItem(REFRESH);
    localStorage.removeItem(USER);
  }

  function toLogin() {
    clear();
    const next = encodeURIComponent(location.pathname + location.search);
    location.replace(`/login?next=${next}`);
  }

  /** 页面启动时调用：没有 Token 直接跳登录页 */
  function requireAuth() {
    if (!getAccess() && !getRefresh()) {
      toLogin();
      return null;
    }
    return getUser();
  }

  async function fetchJson(path, options = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (options.auth !== false && getAccess()) {
      headers.Authorization = `Bearer ${getAccess()}`;
    }
    const res = await fetch(path, {
      method: options.method || 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      // 非 JSON 响应保持 data 为 null
    }
    if (!res.ok) {
      const err = new Error(pickMessage(data, res.status));
      err.status = res.status;
      err.code = data?.code || `HTTP_${res.status}`;
      err.requestId = data?.request_id || res.headers.get('X-Request-Id') || '';
      err.detail = data?.detail ?? null;
      throw err;
    }
    return data;
  }

  function pickMessage(data, status) {
    if (!data) return `请求失败（HTTP ${status}）`;
    if (Array.isArray(data.detail)) {
      const msg = data.detail.map((d) => d.msg || '').filter(Boolean).join('；');
      if (msg) return msg;
    }
    return data.message || `请求失败（HTTP ${status}）`;
  }

  /** 单飞刷新：并发 401 共用一个 refresh 请求，避免轮换冲突 */
  function refresh() {
    if (refreshing) return refreshing;
    const token = getRefresh();
    if (!token) {
      clear();
      return Promise.resolve(false);
    }
    refreshing = fetchJson('/api/auth/refresh', {
      method: 'POST',
      auth: false,
      body: { refresh_token: token },
    })
      .then((data) => {
        save(data);
        return true;
      })
      .catch(() => {
        clear();
        return false;
      })
      .finally(() => {
        refreshing = null;
      });
    return refreshing;
  }

  /** 统一请求：401 时自动刷新一次并重试；仍失败则跳登录 */
  async function api(path, options = {}) {
    try {
      return await fetchJson(path, options);
    } catch (err) {
      if (err.status === 401 && getRefresh()) {
        const ok = await refresh();
        if (ok) return fetchJson(path, options);
        toLogin();
      }
      throw err;
    }
  }

  async function updateMe() {
    const user = await api('/api/auth/me');
    patchUser(user);
    return user;
  }

  async function logout() {
    try {
      await fetchJson('/api/auth/logout', {
        method: 'POST',
        body: { refresh_token: getRefresh() },
      });
    } catch {
      // 网络异常也要清本地状态，避免卡在已登录界面
    }
    clear();
    location.replace('/login');
  }

  return {
    api,
    getUser,
    save,
    clear,
    requireAuth,
    updateMe,
    logout,
    getAccess,
    getRefresh,
  };
})();
