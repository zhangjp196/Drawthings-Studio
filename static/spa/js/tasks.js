// 全局任务 store + 运行器（Phase 2）：任务脱离页面运行，WS 推送进度，切页/刷新不中断。
// - Tasks.run(url, body, opts)：POST 提交任务 → 订阅 WS → 分发事件；任务结束（done/error/cancelled）时 resolve/reject。
// - Tasks.store：任务快照（任务中心读取）；Tasks.cancel(id)：统一取消。
// - 组件只关心「事件回调 + 最终状态」，不再持有连接，也不再因卸载而中断任务。
window.Tasks = (function () {
  const store = Vue.reactive({ jobs: {}, order: [] }); // job_id → snapshot；order 按 created_at 倒序（Vue 全局在此已加载）
  const pending = new Map(); // job_id → 等待中的 run（回调 + resolve/reject）
  let inited = false;

  function upsert(job) {
    if (!job || !job.id) return;
    if (!store.jobs[job.id]) store.order.push(job.id);
    store.jobs[job.id] = Object.assign({}, store.jobs[job.id] || {}, job);
    store.order.sort((a, b) => {
      const ca = store.jobs[a].created_at || '';
      const cb = store.jobs[b].created_at || '';
      if (ca === cb) return 0;
      return ca < cb ? 1 : -1;
    });
  }

  function settle(p, job) {
    if (p.settled) return;
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
      const p = pending.get(msg.job);
      if (!p) return;
      if (p.onEvent) {
        try { p.onEvent(msg.event, msg.data || {}, msg.job); } catch (e) { console.error(e); }
      }
    }
  }

  async function run(url, body, opts = {}) {
    ensure();
    const res = await API.post(url, body);
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
    return store.order.filter((id) => store.jobs[id] && store.jobs[id].status === 'running').length;
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
    run,
    cancel,
    refresh,
    activeCount,
    ensure,
    get job() { return (id) => store.jobs[id] || null; },
  };
})();
