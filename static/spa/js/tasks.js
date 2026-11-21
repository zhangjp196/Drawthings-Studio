// 全局任务 store + 运行器：任务脱离页面运行，WS 推送进度，切页/刷新不中断。
// - Tasks.run(url, body, opts)：POST 提交任务 → 订阅 WS → 分发事件；任务结束时 resolve/reject。
// - Tasks.store：任务快照（任务中心读取）；Tasks.cancel(id)：统一取消。
// 性能要点：
//  1) 高频 token 事件按动画帧合并为一次回调（流式输出时避免每 token 一次 Markdown 重排 + 强制滚动）；
//  2) running 计数用响应式 Set 维护，不再每次渲染遍历全部任务；
//  3) order 只在「新任务首次出现」时排序，进度快照不再触发任务中心/首页重排。
window.Tasks = (function () {
  const store = Vue.reactive({ jobs: {}, order: [] }); // job_id → snapshot；order 按 created_at 倒序
  const ui = Vue.reactive({ centerOpen: false });       // 任务中心抽屉开关
  const pending = new Map();                            // job_id → 等待中的 run
  const runningIds = Vue.reactive(new Set());           // 运行中的 job_id（activeCount 的 O(1) 数据源）
  const tokenBuf = new Map();                           // job_id → { texts, raf }
  let inited = false;

  const raf = window.requestAnimationFrame
    ? window.requestAnimationFrame.bind(window)
    : (fn) => setTimeout(fn, 16);
  const cancelRaf = window.cancelAnimationFrame
    ? window.cancelAnimationFrame.bind(window)
    : clearTimeout;

  function upsert(job) {
    if (!job || !job.id) return;
    const isNew = !store.jobs[job.id];
    const merged = Object.assign({}, store.jobs[job.id] || {}, job);
    store.jobs[job.id] = merged;
    if (merged.status === 'running') runningIds.add(job.id);
    else if (merged.status) runningIds.delete(job.id);
    if (isNew) {
      store.order.push(job.id);
      store.order.sort((a, b) => {
        const ca = store.jobs[a].created_at || '';
        const cb = store.jobs[b].created_at || '';
        if (ca === cb) return 0;
        return ca < cb ? 1 : -1;
      });
    }
  }

  // ---- token 帧合并 ----
  function flushTokens(jobId) {
    const b = tokenBuf.get(jobId);
    if (!b || !b.texts.length) return;
    const text = b.texts.join('');
    b.texts.length = 0;
    if (b.raf) { cancelRaf(b.raf); b.raf = 0; }
    const p = pending.get(jobId);
    if (p && p.onEvent) {
      try { p.onEvent('token', { text }, jobId); } catch (e) { console.error(e); }
    }
  }

  function queueToken(jobId, text) {
    let b = tokenBuf.get(jobId);
    if (!b) { b = { texts: [], raf: 0 }; tokenBuf.set(jobId, b); }
    b.texts.push(text);
    if (b.raf) return;
    b.raf = raf(() => { b.raf = 0; flushTokens(jobId); });
  }

  function settle(p, job) {
    if (p.settled) return;
    flushTokens(job.id);            // 先把尾部 token 交给消费方，再结束
    tokenBuf.delete(job.id);
    p.settled = true;
    pending.delete(job.id);
    WS.unsub(job.id);
    if (job.status === 'cancelled') {
      if (p.onCancelled) { try { p.onCancelled(job); } catch (e) { console.error(e); } }
      p.resolve({ status: 'cancelled', job });
    } else if (job.status === 'error') {
      const err = new Error(job.error || 'Task failed');
      err.job = job;
      err.status = 'error';
      if (p.onError) { try { p.onError(err, job); } catch (e) { console.error(e); } }
      p.reject(err);
    } else {
      if (p.onDone) { try { p.onDone(job); } catch (e) { console.error(e); } }
      p.resolve({ status: job.status, job });
    }
  }

  function onMessage(msg) {
    if (msg.type === 'job') {
      upsert(msg.job);
      const p = pending.get(msg.job.id);
      if (p && msg.job.status && msg.job.status !== 'running') settle(p, msg.job);
    } else if (msg.type === 'event') {
      const jid = msg.job;
      // token 合并；其它事件到达前先 flush，保证事件顺序不被打乱
      if (msg.event === 'token') {
        queueToken(jid, (msg.data && msg.data.text) || '');
        return;
      }
      flushTokens(jid);
      const p = pending.get(jid);
      if (!p) return;
      if (p.onEvent) {
        try { p.onEvent(msg.event, msg.data || {}, jid); } catch (e) { console.error(e); }
      }
    }
  }

  function start(res, opts) {
    const id = res && (res.job_id || res.id);
    if (!id) throw new Error('任务提交失败：缺少 job_id');
    if (opts.onJobId) { try { opts.onJobId(id, res); } catch (e) { console.error(e); } }
    WS.sub(id);
    return new Promise((resolve, reject) => {
      const p = {
        onEvent: opts.onEvent, onDone: opts.onDone, onError: opts.onError, onCancelled: opts.onCancelled,
        resolve, reject, settled: false,
      };
      pending.set(id, p);
      const snap = store.jobs[id];
      if (snap && snap.status && snap.status !== 'running') settle(p, snap); // 极快任务的兜底
    });
  }

  async function run(url, body, opts = {}) {
    ensure();
    return start(await API.post(url, body), opts);
  }

  // 表单提交（文档导入等 multipart）：POST 后同样走 WS 订阅与事件分发
  async function runForm(url, formData, opts = {}) {
    ensure();
    return start(await API.postForm(url, formData), opts);
  }

  async function cancel(id) {
    if (!id) return;
    try { await API.post(`/api/tasks/${id}/cancel`, {}); } catch (e) { /* 已结束 */ }
  }

  async function refresh() {
    ensure();
    try {
      const d = await API.get('/api/tasks?limit=50');
      (d.jobs || []).forEach(upsert);
    } catch (e) { /* 首屏失败：等 WS 推送 */ }
  }

  function activeCount() {
    return runningIds.size;
  }

  function ensure() {
    if (inited) return;
    inited = true;
    WS.onMessage(onMessage);
    WS.start();
    WS.subAll(); // 任务中心需要所有任务的状态变化
    refresh();
    // 兜底：WS 断线期间靠轮询保持任务中心与实际一致
    setInterval(() => { if (!WS.connected) refresh(); }, 15000);
  }

  return {
    store,
    ui,
    run,
    runForm,
    cancel,
    refresh,
    activeCount,
    ensure,
    flushTokens,
    get job() { return (id) => store.jobs[id] || null; },
  };
})();
