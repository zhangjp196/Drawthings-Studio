// 轻量 API 客户端：JSON 请求 + SSE 流（与后端 /api 约定一致）
// - 自动携带 Accept-Language：后端据此本地化错误文案，与界面语言保持一致
// - 请求超时保护（默认 5 分钟，足够单章 LLM/出图，批量走 SSE 不受影响）
// - SSE 支持 AbortSignal：组件卸载 / 重新请求时可主动断开（服务端随之清理后台任务）
window.API = {
  TIMEOUT: 5 * 60 * 1000,

  _lang() {
    try { return window.I18N ? I18N.current() : 'zh'; } catch (e) { return 'zh'; }
  },

  _t(key, ...args) {
    try { return I18N.t(key, ...args); } catch (e) { return key; }
  },

  async req(method, url, body, isForm, timeout) {
    const headers = { 'Accept-Language': API._lang() };
    const opt = { method, headers };
    if (body !== undefined) {
      if (isForm) {
        opt.body = body;
      } else {
        headers['Content-Type'] = 'application/json';
        opt.body = JSON.stringify(body);
      }
    }
    const ms = timeout === undefined ? API.TIMEOUT : timeout;
    const ctrl = new AbortController();
    opt.signal = ctrl.signal;
    const timer = ms > 0 ? setTimeout(() => ctrl.abort(), ms) : null;
    let r;
    try {
      r = await fetch(url, opt);
    } catch (e) {
      if (e && e.name === 'AbortError') throw new Error(API._t('common.timeout'));
      throw new Error(API._t('common.netError', (e && e.message) || ''));
    } finally {
      if (timer) clearTimeout(timer);
    }
    if (!r.ok) {
      let msg = API._t('common.httpError', r.status);
      try {
        const j = await r.json();
        if (j && (j.detail || j.message)) {
          const d = j.detail || j.message;
          msg = typeof d === 'string' ? d : JSON.stringify(d);
        }
      } catch (e) { /* 非 JSON 错误体：用默认文案 */ }
      const err = new Error(msg);
      err.status = r.status;
      throw err;
    }
    if (r.status === 204) return null;
    const ct = r.headers.get('content-type') || '';
    return ct.includes('application/json') ? r.json() : null;
  },

  get: (u, timeout) => API.req('GET', u, undefined, false, timeout),
  post: (u, body, timeout) => API.req('POST', u, body, false, timeout),
  put: (u, body, timeout) => API.req('PUT', u, body, false, timeout),
  patch: (u, body, timeout) => API.req('PATCH', u, body, false, timeout),
  del: (u, timeout) => API.req('DELETE', u, undefined, false, timeout),
  postForm: (u, fd, timeout) => API.req('POST', u, fd, true, timeout),

  // SSE：POST JSON，按帧回调 onEvent(event, data)；非 2xx 抛 Error；signal 可中断
  sse: async (url, body, onEvent, signal) => {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept-Language': API._lang() },
      body: JSON.stringify(body),
      signal,
    });
    if (!resp.ok || !resp.body) {
      let detail = API._t('common.httpError', resp.status);
      try {
        const j = await resp.json();
        if (j && j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
      } catch (e) { /* 非 JSON 错误体 */ }
      throw new Error(detail);
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let event = 'message', data = '';
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          else if (line.startsWith('data:')) data = line.slice(5).trim();
        }
        if (!data) continue;
        let d = {};
        try { d = JSON.parse(data); } catch (e) { d = { text: data }; }
        onEvent(event, d);
      }
    }
  },
};
