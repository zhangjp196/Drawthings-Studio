// 应用入口：根组件（顶栏 + 路由出口）+ Element Plus / 图标 / 路由挂载
(function () {
  const app = Vue.createApp({
    template: `
      <header class="topbar">
        <div class="topbar-inner">
          <router-link to="/" class="brand">🎬 漫剧坊</router-link>
          <nav class="topnav">
            <router-link to="/projects" class="tnav" :class="{ active: isProjects }">创作中心</router-link>
            <router-link to="/micro" class="tnav" :class="{ active: isMicro }">✨ 微创作</router-link>
            <router-link to="/configs" class="tnav" :class="{ active: isConfigs }">⚙ 配置</router-link>
          </nav>
          <div class="theme-group">
            <button v-for="t in themes" :key="t.v" type="button" class="tbtn" :class="{ on: theme === t.v }" @click="Theme.set(t.v)">{{ t.label }}</button>
          </div>
        </div>
      </header>
      <main>
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </main>
    `,
    setup() {
      const theme = ref(Theme.current());
      Theme.onChange(v => { theme.value = v; });
      return {
        theme,
        Theme,
        themes: [
          { v: 'light', label: '☀ 浅色' },
          { v: 'system', label: '⚙ 系统' },
          { v: 'dark', label: '☾ 深色' },
        ],
        isProjects: computed(() => {
          const p = router.currentRoute.value.path;
          return p.startsWith('/projects') || p.startsWith('/project/');
        }),
        isMicro: computed(() => router.currentRoute.value.path.startsWith('/micro')),
        isConfigs: computed(() => router.currentRoute.value.path.startsWith('/configs')),
      };
    },
  });

  app.use(ElementPlus, { locale: window.ElementPlusLocaleZhCn });
  for (const [name, comp] of Object.entries(ElementPlusIconsVue)) {
    app.component(name, comp);
  }
  app.use(router);
  app.config.errorHandler = (err) => { console.error('[漫剧坊]', err); };
  app.mount('#app');
})();
