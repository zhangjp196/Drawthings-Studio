// 应用入口：根组件（顶栏 + 路由出口）+ Element Plus / 图标 / 路由挂载
// 品牌：Drawthings Studio；语言 / 主题在「配置 → 基础配置」或顶栏切换。
// 语言/主题均由响应式状态驱动原地刷新（ElConfigProvider 提供 EP locale），无需重建应用。
(function () {
  const root = {
    template: `
      <el-config-provider :locale="elLocale">
      <header class="topbar">
        <div class="topbar-inner">
          <router-link to="/" class="brand">
            <span class="brand-mark">✦</span>
            <span class="brand-name">Drawthings Studio</span>
          </router-link>
          <nav class="topnav">
            <router-link to="/projects" class="tnav" :class="{ active: isProjects }">{{ I18N.t('nav.studio') }}</router-link>
            <router-link to="/micro" class="tnav" :class="{ active: isMicro }">{{ I18N.t('nav.quick') }}</router-link>
            <router-link to="/configs" class="tnav" :class="{ active: isConfigs }">{{ I18N.t('nav.settings') }}</router-link>
          </nav>
          <div class="theme-group">
            <button v-for="l in langs" :key="l.v" type="button" class="tbtn icon" :title="l.label" :class="{ on: I18N.current() === l.v }" @click="I18N.set(l.v)">{{ l.flag }}</button>
            <span class="tsep"></span>
            <button v-for="t in themes" :key="t.v" type="button" class="tbtn icon" :title="I18N.t(t.key)" :class="{ on: theme === t.v }" @click="Theme.set(t.v)">{{ t.sym }}</button>
          </div>
        </div>
      </header>
      <main>
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </main>
      </el-config-provider>
    `,
    setup() {
      const theme = ref(Theme.current());
      Theme.onChange(v => { theme.value = v; });
      // 语言用响应式 ref 承载：切换时仅更新 ElConfigProvider 的 locale，
      // 组件文案由 I18N.t() 自身的响应式依赖原地刷新（不再重建整个应用实例）。
      const lang = ref(I18N.current());
      I18N.onChange(v => { lang.value = v; });
      return {
        I18N,
        Theme,
        theme,
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
        isProjects: computed(() => {
          const p = router.currentRoute.value.path;
          return p.startsWith('/projects') || p.startsWith('/project/');
        }),
        isMicro: computed(() => router.currentRoute.value.path.startsWith('/micro')),
        isConfigs: computed(() => router.currentRoute.value.path.startsWith('/configs')),
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
})();
