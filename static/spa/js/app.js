// 应用入口：CS 桌面外壳（左栏侧边导航 + 紧凑工具栏 + 内容区）+ Element Plus / 图标 / 路由挂载
// 品牌：Drawthings Studio；语言 / 主题在顶部工具栏切换。
// 语言/主题均由响应式状态驱动原地刷新（ElConfigProvider 提供 EP locale），无需重建应用。
(function () {
  const root = {
    template: `
      <el-config-provider :locale="elLocale">
      <div class="app-shell">
        <aside class="sidebar" :class="{ collapsed: sbCollapsed }">
          <div class="sb-head">
            <router-link to="/" class="brand">
              <span class="brand-mark">✦</span>
              <span class="brand-name" v-show="!sbCollapsed">Drawthings Studio</span>
            </router-link>
          </div>
          <nav class="sb-nav">
            <router-link v-for="n in navs" :key="n.to" :to="n.to" class="sb-item" :class="{ active: n.match() }" :title="I18N.t(n.key)">
              <span class="sb-ico">{{ n.ico }}</span>
              <span class="sb-txt">{{ I18N.t(n.key) }}</span>
            </router-link>
          </nav>
          <div class="sb-foot">
            <button type="button" class="sb-collapse" :title="I18N.t('nav.collapse')" @click="toggleSidebar">{{ sbCollapsed ? '»' : '«' }}</button>
          </div>
        </aside>
        <div class="app-main">
          <header class="toolbar">
            <div class="tb-title">{{ pageTitle }}</div>
            <div class="tb-actions">
              <button v-for="l in langs" :key="l.v" type="button" class="tbtn icon" :title="l.label" :class="{ on: I18N.current() === l.v }" @click="I18N.set(l.v)">{{ l.flag }}</button>
              <span class="tsep"></span>
              <button v-for="t in themes" :key="t.v" type="button" class="tbtn icon" :title="I18N.t(t.key)" :class="{ on: theme === t.v }" @click="Theme.set(t.v)">{{ t.sym }}</button>
            </div>
          </header>
          <main class="app-content">
            <router-view v-slot="{ Component }">
              <component :is="Component" />
            </router-view>
          </main>
        </div>
      </div>
      </el-config-provider>
    `,
    setup() {
      const theme = ref(Theme.current());
      Theme.onChange(v => { theme.value = v; });

      // 侧边栏折叠（按浏览器 / 应用持久化）
      const sbCollapsed = ref(false);
      try { sbCollapsed.value = localStorage.getItem('sbCollapsed') === '1'; } catch (e) {}
      function toggleSidebar() {
        sbCollapsed.value = !sbCollapsed.value;
        try { localStorage.setItem('sbCollapsed', sbCollapsed.value ? '1' : '0'); } catch (e) {}
      }

      // 导航项（图标 + 文案 + 高亮匹配）
      const path = () => router.currentRoute.value.path;
      const navs = [
        { to: '/',         ico: '⌂', key: 'nav.workspace', match: () => path() === '/' },
        { to: '/projects', ico: '🎬', key: 'nav.studio',    match: () => path().startsWith('/projects') || path().startsWith('/project/') },
        { to: '/micro',    ico: '✨', key: 'nav.quick',     match: () => path().startsWith('/micro') },
        { to: '/configs',  ico: '⚙', key: 'nav.settings',  match: () => path().startsWith('/configs') },
      ];
      const pageTitle = computed(() => {
        const p = path();
        if (p === '/') return I18N.t('nav.workspace');
        if (p.startsWith('/project') || p.startsWith('/projects')) return I18N.t('nav.studio');
        if (p.startsWith('/new')) return I18N.t('nav.newProject');
        if (p.startsWith('/micro')) return I18N.t('nav.quick');
        if (p.startsWith('/configs')) return I18N.t('nav.settings');
        return 'Drawthings Studio';
      });

      // 语言用响应式 ref 承载：切换时仅更新 ElConfigProvider 的 locale，
      // 组件文案由 I18N.t() 自身的响应式依赖原地刷新（不再重建整个应用实例）。
      const lang = ref(I18N.current());
      I18N.onChange(v => { lang.value = v; });
      return {
        I18N,
        Theme,
        theme,
        sbCollapsed,
        toggleSidebar,
        navs,
        pageTitle,
        elLocale: computed(() => (lang.value === 'en'
          ? window.ElementPlusLocaleEn : window.ElementPlusLocaleZhCn)),
        langs: [
          { v: 'zh', flag: '🇨🇳', label: '中文' },
          { v: 'en', flag: '🇺🇸', label: 'English' },
        ],
        themes: [
          { v: 'light', key: 'theme.light', sym: '☀' },
          { v: 'system', key: 'theme.system', sym: '⚙' },
          { v: 'dark', key: 'theme.dark', sym: '☾' },
        ],
      };
    },
  };

  let app = null;
  function mount() {
    app = Vue.createApp(root);
    app.config.globalProperties.I18N = I18N;  // 各视图模板统一可访问 I18N / router（无需在每个 setup 里重复返回）
    app.config.globalProperties.router = router;
    app.use(ElementPlus, {
      locale: I18N.isEn() ? window.ElementPlusLocaleEn : window.ElementPlusLocaleZhCn,
    });
    for (const [name, comp] of Object.entries(ElementPlusIconsVue)) {
      app.component(name, comp);
    }
    app.use(router);
    app.config.errorHandler = (err) => { console.error('[Drawthings Studio]', err); };
    app.mount('#app');
  }
  mount();

  // CS 桌面客户端：正文里的外部链接（Markdown 渲染出的 http(s)）交给系统浏览器打开，
  // 避免在原生窗口内跳走；浏览器环境不拦截，保持默认新标签页行为。
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
