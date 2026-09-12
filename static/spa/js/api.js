// 轻量 API 客户端：JSON 请求 + SSE 流（与后端 /api 约定一致）
window.API = {
  async req(method, url, body, isForm) {
    const opt = { method };
    if (body !== undefined) {
      if (isForm) {
        opt.body = body;
      } else {
        opt.headers = { 'Content-Type': 'application/json' };
        opt.body = JSON.stringify(body);
      }
    }
    const r = await fetch(url, opt);
    if (!r.ok) {
      let msg = '请求失败（' + r.status + '）';
      try { const j = await r.json(); msg = (j && (j.detail || j.message)) || msg; } catch (e) {}
      const err = new Error(msg);
      err.status = r.status;
      throw err;
    }
    return r.status === 204 ? null : r.json();
  },

  get: (u) => API.req('GET', u),
  post: (u, body) => API.req('POST', u, body),
  put: (u, body) => API.req('PUT', u, body),
  postForm: (u, fd) => API.req('POST', u, fd, true),

  // SSE：POST JSON，按帧回调 onEvent(event, data)；非 2xx 抛 Error
  sse: async (url, body, onEvent) => {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok || !resp.body) {
      let detail = '请求失败（' + resp.status + '）';
      try { detail = (await resp.json()).detail || detail; } catch (e) {}
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
