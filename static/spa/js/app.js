// 应用入口：根组件（顶栏 + 路由出口）+ Element Plus / 图标 / 路由挂载
// 品牌：Drawthings Studio；语言 / 主题在「配置 → 基础配置」里切换（Element Plus locale 随语言重建生效）。
(function () {
  const root = {
    template: `
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
        </div>
      </header>
      <main>
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </main>
    `,
    setup() {
      return {
        I18N,
        Theme,
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
    const locale = I18N.isEn() ? window.ElementPlusLocaleEn : window.ElementPlusLocaleZhCn;
    app = Vue.createApp(root);
    app.config.globalProperties.I18N = I18N;  // 各视图模板统一可访问 I18N / router（无需在每个 setup 里重复返回）
    app.config.globalProperties.router = router;
    app.use(ElementPlus, { locale });
    for (const [name, comp] of Object.entries(ElementPlusIconsVue)) {
      app.component(name, comp);
    }
    app.use(router);
    app.config.errorHandler = (err) => { console.error('[Drawthings Studio]', err); };
    app.mount('#app');
  }
  mount();
  // 语言切换：重建应用实例以应用 Element Plus 语言包（组件文案随之刷新，数据经 onMounted 重拉）
  I18N.onChange(() => {
    if (app) app.unmount();
    mount();
  });
})();
