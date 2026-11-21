// 应用入口 v2：顶部导航 + 全宽内容 + 底部状态栏 + ⌘K 命令面板。
// 与旧版「左侧栏 + 工具栏」布局彻底不同；语言 / 主题仍由响应式状态原地刷新。
// 全局错误兜底、CS 外链桥接、任务中心挂载保持不变。
(function () {
  const root = {
    template: `
      <el-config-provider :locale="elLocale">
      <div class="ds-shell">
        <header class="ds-topbar">
          <router-link to="/" class="ds-brand" :title="I18N.t('app.tagline')">
            <span class="ds-brand-mark">✦</span>
            <span class="ds-brand-text">
              <span class="ds-brand-name">Drawthings Studio</span>
              <span class="ds-brand-tag">{{ I18N.t('app.tagline') }}</span>
            </span>
          </router-link>

          <nav class="ds-nav">
            <router-link v-for="n in navs" :key="n.to" :to="n.to"
                         class="ds-nav-item" :class="{ active: n.match() }" :title="I18N.t(n.key)">
              <span class="ds-nav-ico">{{ n.ico }}</span>
              <span class="ds-nav-label">{{ I18N.t(n.key) }}</span>
            </router-link>
          </nav>

          <div class="ds-top-actions">
            <button type="button" class="ds-cmd" :title="I18N.t('cmd.search')" @click="cmdOpen = true">
              <span>⌕</span><span>{{ I18N.t('cmd.search') }}</span><span class="ds-cmd-kbd">⌘K</span>
            </button>
            <button type="button" class="ds-task-btn" :title="I18N.t('nav.tasks')" @click="openTasks">
              <span>◷</span><span>{{ I18N.t('nav.tasksShort') }}</span>
              <span class="ds-count" :class="{ idle: !running }">{{ running }}</span>
            </button>
            <span class="ds-sep"></span>
            <button v-for="l in langs" :key="l.v" type="button" class="ds-iconbtn"
                    :title="l.label" :class="{ on: lang === l.v }" @click="I18N.set(l.v)">{{ l.flag }}</button>
            <span class="ds-sep"></span>
            <button v-for="t in themes" :key="t.v" type="button" class="ds-iconbtn sym"
                    :title="I18N.t(t.key)" :class="{ on: theme === t.v }" @click="Theme.set(t.v)">{{ t.sym }}</button>
          </div>
        </header>

        <div class="ds-body">
          <main class="ds-content">
            <router-view v-slot="{ Component }">
              <transition name="page" mode="out-in">
                <component :is="Component" />
              </transition>
            </router-view>
          </main>
        </div>

        <footer class="ds-statusbar">
          <div class="ds-status-group">
            <span class="ds-status-item"><i class="ds-conn" :class="{ off: !wsOk }"></i>{{ wsOk ? I18N.t('status.online') : I18N.t('status.offline') }}</span>
            <span class="ds-status-dot">·</span>
            <span class="ds-status-item">{{ I18N.t('status.tasks', running) }}</span>
            <span class="ds-status-dot optional">·</span>
            <span class="ds-status-item optional">{{ pageTitle }}</span>
          </div>
          <div class="ds-status-right">
            <span class="optional">{{ I18N.t('status.local') }}</span>
            <span class="optional">{{ lang.toUpperCase() }}</span>
            <span>v0.1</span>
          </div>
        </footer>
      </div>

      <task-center />

      <div class="ds-cmdk" v-if="cmdOpen" @keydown="onCmdKey">
        <div class="ds-cmdk-mask" @click="cmdOpen = false"></div>
        <div class="ds-cmdk-panel" role="dialog" aria-modal="true">
          <div class="ds-cmdk-head">
            <span>⌕</span>
            <input ref="cmdInput" class="ds-cmdk-input" v-model="cmdQuery"
                   :placeholder="I18N.t('cmd.placeholder')" @input="cmdSel = 0">
            <span class="ds-cmdk-kbd">ESC</span>
          </div>
          <div class="ds-cmdk-list">
            <template v-for="(it, i) in filtered" :key="it.to || it.action">
              <div class="ds-cmdk-group" v-if="i === 0 || filtered[i - 1].group !== it.group">{{ it.group }}</div>
              <button type="button" class="ds-cmdk-item" :class="{ sel: i === cmdSel }"
                      @mouseenter="cmdSel = i" @click="cmdGo(it)">
                <span class="ds-cmdk-ico">{{ it.ico }}</span>
                <span>{{ it.label }}</span>
                <span class="ds-cmdk-hint">{{ it.to }}</span>
              </button>
            </template>
            <div class="ds-cmdk-empty" v-if="!filtered.length">{{ I18N.t('cmd.empty') }}</div>
          </div>
          <div class="ds-cmdk-foot"><span>↑ ↓ {{ I18N.t('cmd.nav') }}</span><span>↵ {{ I18N.t('cmd.open') }}</span><span>esc {{ I18N.t('cmd.close') }}</span></div>
        </div>
      </div>
      </el-config-provider>
    `,
    setup() {
      const theme = ref(Theme.current());
      Theme.onChange(v => { theme.value = v; });

      const path = () => router.currentRoute.value.path;
      function studioMatch(kind) {
        if (path() === '/projects') return (router.currentRoute.value.query.kind || '') === kind;
        return path().startsWith('/' + (kind === 'comic' ? 'comic' : 'drama') + '/');
      }
      const navs = [
        { to: '/',                    ico: '⌂',  key: 'nav.workspace',   match: () => path() === '/' },
        { to: '/projects?kind=comic', ico: '🖼', key: 'nav.studioComic', match: () => studioMatch('comic') },
        { to: '/projects?kind=drama', ico: '🎬', key: 'nav.studioDrama', match: () => studioMatch('drama') },
        { to: '/micro',                ico: '✨', key: 'nav.quick',       match: () => path().startsWith('/micro') },
        { to: '/configs',              ico: '⚙', key: 'nav.settings',    match: () => path().startsWith('/configs') },
      ];

      const running = computed(() => (window.Tasks ? Tasks.activeCount() : 0));
      function openTasks() { Tasks.ui.centerOpen = true; }

      const pageTitle = computed(() => {
        const p = path();
        if (p === '/') return I18N.t('nav.workspace');
        if (p === '/projects') {
          const k = router.currentRoute.value.query.kind;
          if (k === 'comic') return I18N.t('nav.studioComic');
          if (k === 'drama') return I18N.t('nav.studioDrama');
          return I18N.t('nav.studio');
        }
        if (p.startsWith('/comic/')) return I18N.t('nav.studioComic');
        if (p.startsWith('/drama/')) return I18N.t('nav.studioDrama');
        if (p.startsWith('/new')) return I18N.t('nav.newProject');
        if (p.startsWith('/micro')) return I18N.t('nav.quick');
        if (p.startsWith('/configs')) return I18N.t('nav.settings');
        return 'Drawthings Studio';
      });

      // WS 连接状态不是响应式的：低频轮询同步到状态栏（1s，开销可忽略）
      const wsOk = ref(false);
      setInterval(() => { wsOk.value = !!(window.WS && WS.connected); }, 1000);

      // ---- ⌘K 命令面板 ----
      const cmdOpen = ref(false);
      const cmdQuery = ref('');
      const cmdSel = ref(0);
      const cmdInput = ref(null);
      const cmdItems = computed(() => {
        const g1 = I18N.t('cmd.groupNav'), g2 = I18N.t('cmd.groupCreate'), g3 = I18N.t('cmd.groupSystem');
        return [
          { group: g1, ico: '⌂',  label: I18N.t('nav.workspace'),   to: '/' },
          { group: g1, ico: '🖼', label: I18N.t('nav.studioComic'), to: '/projects?kind=comic' },
          { group: g1, ico: '🎬', label: I18N.t('nav.studioDrama'), to: '/projects?kind=drama' },
          { group: g1, ico: '✨', label: I18N.t('nav.quick'),       to: '/micro' },
          { group: g1, ico: '⚙', label: I18N.t('nav.settings'),    to: '/configs' },
          { group: g2, ico: '＋', label: I18N.t('proj.newDlgComic'), to: '/new/comic' },
          { group: g2, ico: '＋', label: I18N.t('proj.newDlgDrama'), to: '/new/drama' },
          { group: g3, ico: '◷', label: I18N.t('cmd.tasks'),        action: 'tasks' },
        ];
      });
      const filtered = computed(() => {
        const q = cmdQuery.value.trim().toLowerCase();
        if (!q) return cmdItems.value;
        return cmdItems.value.filter(it => (it.label + ' ' + (it.to || '')).toLowerCase().includes(q));
      });
      function cmdGo(it) {
        if (!it) return;
        cmdOpen.value = false;
        if (it.action === 'tasks') openTasks();
        else router.push(it.to);
      }
      function onCmdKey(e) {
        if (e.key === 'Escape') { e.preventDefault(); cmdOpen.value = false; return; }
        if (e.key === 'ArrowDown') { e.preventDefault(); cmdSel.value = Math.min(cmdSel.value + 1, filtered.value.length - 1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); cmdSel.value = Math.max(cmdSel.value - 1, 0); }
        else if (e.key === 'Enter') { e.preventDefault(); cmdGo(filtered.value[cmdSel.value]); }
      }
      watch(cmdOpen, (v) => {
        if (v) { cmdQuery.value = ''; cmdSel.value = 0; nextTick(() => { if (cmdInput.value) cmdInput.value.focus(); }); }
      });
      document.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
          e.preventDefault();
          cmdOpen.value = !cmdOpen.value;
        }
      });

      const lang = ref(I18N.current());
      I18N.onChange(v => { lang.value = v; });
      return {
        I18N, Theme, theme, navs, running, openTasks, pageTitle, wsOk,
        cmdOpen, cmdQuery, cmdSel, cmdInput, filtered, cmdGo, onCmdKey,
        elLocale: computed(() => (lang.value === 'en' ? window.ElementPlusLocaleEn : window.ElementPlusLocaleZhCn)),
        lang,
        langs: [{ v: 'zh', flag: '🇨🇳', label: '中文' }, { v: 'en', flag: '🇺🇸', label: 'English' }],
        themes: [
          { v: 'light', key: 'theme.light', sym: '☀' },
          { v: 'system', key: 'theme.system', sym: '⚙' },
          { v: 'dark', key: 'theme.dark', sym: '☾' },
        ],
      };
    },
  };

  // 全局错误兜底：任何未捕获的渲染/异步错误都记录并（去重后）提示用户
  const _errSeen = new Map();
  function reportError(err, where) {
    try { console.error('[Drawthings Studio]', where, err); } catch (e) { /* ignore */ }
    try {
      const msg = String((err && (err.message || err)) || 'unknown error').slice(0, 300);
      const now = Date.now();
      if (now - (_errSeen.get(msg) || 0) < 4000) return;
      _errSeen.set(msg, now);
      if (_errSeen.size > 200) _errSeen.clear();
      if (window.ElementPlus && ElementPlus.ElMessage) ElementPlus.ElMessage.error(msg);
    } catch (e) { /* 提示本身失败不影响应用 */ }
  }
  window.addEventListener('unhandledrejection', (e) => reportError(e && e.reason, 'promise'));
  window.addEventListener('error', (e) => {
    if (e && e.error) reportError(e.error, 'window');
  });

  function mount() {
    const app = Vue.createApp(root);
    app.config.globalProperties.I18N = I18N;
    app.config.globalProperties.router = router;
    app.component('task-center', Views.taskCenter);
    Tasks.ensure();
    app.use(ElementPlus, { locale: I18N.isEn() ? window.ElementPlusLocaleEn : window.ElementPlusLocaleZhCn });
    for (const [name, comp] of Object.entries(ElementPlusIconsVue)) app.component(name, comp);
    app.use(router);
    app.config.errorHandler = (err) => { reportError(err, 'vue'); };
    app.mount('#app');
    // 移除首屏骨架（等首帧渲染完成，避免出现空白间隙）
    Vue.nextTick(() => { const b = document.getElementById('boot'); if (b) b.remove(); });
  }
  mount();

  // CS 桌面客户端：正文里的外部链接交给系统浏览器打开
  document.addEventListener('click', (e) => {
    const a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;
    const href = a.getAttribute('href') || '';
    if (/^https?:\/\//i.test(href) && API.isDesktop()) {
      e.preventDefault();
      API.openExternal(href);
    }
  });
})();
