// 主题：浅色 / 深色 / 跟随系统（localStorage 持久化）。
// 同时驱动 Element Plus 暗色模式（html.dark + vendor 的 dark.css 变量）。
window.Theme = (function () {
  let cur = 'system';
  const listeners = [];
  const mq = matchMedia('(prefers-color-scheme: dark)');

  function apply() {
    document.documentElement.setAttribute('data-theme', cur);
    const dark = cur === 'dark' || (cur === 'system' && mq.matches);
    document.documentElement.classList.toggle('dark', dark);
  }
  function emit() { listeners.forEach(f => f(cur)); }
  function set(t) {
    cur = t;
    try { localStorage.setItem('theme', t); } catch (e) {}
    apply();
    emit();
  }
  if (mq.addEventListener) {
    mq.addEventListener('change', () => { if (cur === 'system') { apply(); emit(); } });
  }
  try { cur = localStorage.getItem('theme') || 'system'; } catch (e) {}
  apply();
  return {
    current: () => cur,
    set,
    onChange: (f) => listeners.push(f),
  };
})();
