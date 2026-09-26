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

  get: async (u, timeout) => {
    try {
      return await API.req('GET', u, undefined, false, timeout);
    } catch (e) {
      // 冗余：GET 幂等，仅对网络/超时类失败（无 HTTP 状态）自动重试一次，缓解瞬时抖动
      if (e && !e.status) {
        await new Promise((r) => setTimeout(r, 400));
        return await API.req('GET', u, undefined, false, timeout);
      }
      throw e;
    }
  },
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

  // ---- CS 桌面客户端桥 ----
  // pywebview 注入 window.pywebview.api（浏览器里不存在）；桥方法名与 client.py 的 NativeApi 一致。
  native() {
    try {
      if (typeof window !== 'undefined' && window.pywebview && window.pywebview.api) {
        return window.pywebview.api;
      }
    } catch (e) { /* 非桌面环境 */ }
    return null;
  },
  isDesktop() { return !!API.native(); },
  // 系统通知（仅 CS 生效；浏览器里静默忽略）
  notify(title, message) {
    const n = API.native();
    if (n && n.notify) { try { n.notify(title, message || ''); } catch (e) {} }
  },
  // 外部链接：CS 用系统浏览器打开；返回是否已交给原生处理
  openExternal(url) {
    const n = API.native();
    if (n && n.openExternal && /^https?:\/\//i.test(url)) {
      try { n.openExternal(url); return true; } catch (e) {}
    }
    return false;
  },

  // Blob → base64（去掉 data:xxx;base64, 前缀），供 CS 桌面客户端原生「另存为」使用
  _blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result).split("base64,")[1]);
      fr.onerror = () => reject(new Error("read blob failed"));
      fr.readAsDataURL(blob);
    });
  },

  // 文件下载：GET -> Blob，按 Content-Disposition 命名并触发保存；非 2xx 抛本地化错误。
  // CS 桌面客户端（window.pywebview.api 可用）时改走系统「另存为」对话框；浏览器里保持原下载行为。
  download: async (url) => {
    // 导出（ZIP/PDF）可能较大：给独立超时，避免请求悬挂
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 10 * 60 * 1000);
    let resp;
    try {
      resp = await fetch(url, { headers: { 'Accept-Language': API._lang() }, signal: ctrl.signal });
    } catch (e) {
      clearTimeout(timer);
      if (e && e.name === 'AbortError') throw new Error(API._t('common.timeout'));
      throw new Error(API._t('common.netError', (e && e.message) || ''));
    }
    if (!resp.ok) {
      clearTimeout(timer);
      let detail = API._t('common.httpError', resp.status);
      try {
        const j = await resp.json();
        if (j && j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
      } catch (e) { /* 非 JSON 错误体 */ }
      throw new Error(detail);
    }
    let blob;
    try {
      blob = await resp.blob();
    } catch (e) {
      if (e && e.name === 'AbortError') throw new Error(API._t('common.timeout'));
      throw e;
    } finally {
      clearTimeout(timer);
    }
    const cd = resp.headers.get('content-disposition') || '';
    let name = 'download';
    let m = /filename\*=utf-8''([^;]+)/i.exec(cd);
    if (m) { try { name = decodeURIComponent(m[1].trim()); } catch (e) {} }
    else if ((m = /filename="?([^";]+)"?/i.exec(cd))) { name = m[1].trim(); }

    // CS 桌面客户端：优先系统「另存为」（用户取消则静默返回；桥异常/失败回退浏览器下载）
    const native = API.native();
    if (native && native.saveBlob) {
      try {
        const b64 = await API._blobToBase64(blob);
        const r = await native.saveBlob(name, b64);
        if (r && (r.ok || r.canceled)) return;
      } catch (e) { /* 原生桥异常 → 回退浏览器下载 */ }
    }

    const a = document.createElement('a');
    const obj = URL.createObjectURL(blob);
    a.href = obj; a.download = name; a.style.display = 'none';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(obj), 1000);
  },
};
