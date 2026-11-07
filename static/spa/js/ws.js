// WebSocket 事件总线（Phase 2 起替代 SSE）：单例连接 + 自动重连（指数退避）+ 重连补订阅。
// 服务端协议见 main.py /ws：{type:'job'|'event'|'hello'|'ping'}；订阅 {"type":"sub","jobs":[...]}。
// 用法：
//   WS.onMessage(fn)      监听所有消息（任务 store 用）
//   WS.sub(id) / unsub(id) 订阅任务事件（服务端只推已订阅任务）
//   WS.subAll()            订阅全部任务状态（任务中心）
window.WS = (function () {
  let sock = null;
  let ready = false;
  let retry = 0;
  let timer = null;
  const subs = new Set(); // 显式订阅的任务 id
  let allJobs = false;
  const handlers = new Set();

  function url() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}/ws`;
  }

  function send(obj) {
    if (ready && sock && sock.readyState === WebSocket.OPEN) {
      try { sock.send(JSON.stringify(obj)); } catch (e) { /* 连接刚断：等重连补订阅 */ }
    }
  }

  function schedule() {
    if (timer) return;
    const ms = Math.min(15000, 500 * Math.pow(2, retry++)); // 0.5s → 1s → 2s … 封顶 15s
    timer = setTimeout(() => { timer = null; connect(); }, ms);
  }

  function connect() {
    if (sock && (sock.readyState === WebSocket.OPEN || sock.readyState === WebSocket.CONNECTING)) return;
    try {
      sock = new WebSocket(url());
    } catch (e) {
      schedule();
      return;
    }
    sock.onopen = () => {
      ready = true;
      retry = 0;
      if (allJobs) send({ type: 'sub', jobs: ['*'] });
      if (subs.size) send({ type: 'sub', jobs: [...subs] });
    };
    sock.onmessage = (ev) => {
      let msg = null;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      handlers.forEach((fn) => { try { fn(msg); } catch (e) { console.error(e); } });
    };
    sock.onclose = () => {
      ready = false;
      schedule();
    };
    sock.onerror = () => {
      try { sock.close(); } catch (e) { /* 已关闭 */ }
    };
  }

  return {
    start() { connect(); },
    onMessage(fn) { handlers.add(fn); return () => handlers.delete(fn); },
    sub(ids) {
      const list = (Array.isArray(ids) ? ids : [ids]).filter(Boolean);
      if (!list.length) return;
      list.forEach((i) => subs.add(i));
      send({ type: 'sub', jobs: list });
    },
    unsub(ids) {
      const list = (Array.isArray(ids) ? ids : [ids]).filter(Boolean);
      list.forEach((i) => subs.delete(i));
      send({ type: 'unsub', jobs: list });
    },
    subAll() {
      allJobs = true;
      send({ type: 'sub', jobs: ['*'] });
    },
    unsubAll() {
      allJobs = false;
      send({ type: 'unsub', jobs: ['*'] });
    },
    get connected() { return ready; },
  };
})();
