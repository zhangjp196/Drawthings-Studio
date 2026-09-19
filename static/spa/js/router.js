// 全局路由（history 模式；未知路径由 FastAPI 外壳兜底，路由内重定向到首页）
// 同时把常用 Vue helper / 路由挂到 window，供各视图脚本（无构建的普通 <script>）使用。
// 注意：需在所有视图脚本之后加载（index.html 中顺序已保证），视图组件直接引用。
window.router = VueRouter.createRouter({
  history: VueRouter.createWebHistory(),
  routes: [
    { path: '/', component: Views.home },
    { path: '/projects', component: Views.projects },
    { path: '/new', component: Views.newProject },
    { path: '/project/:id', component: Views.project, props: true },
    { path: '/configs', component: Views.configs },
    { path: '/micro', component: Views.micro },
    { path: '/micro/:id', component: Views.microWork, props: true },
    { path: '/micro/:id/:sid', component: Views.microWork, props: true },
    { path: '/playground', redirect: '/micro' },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
});

window.ref = Vue.ref;
window.reactive = Vue.reactive;
window.computed = Vue.computed;
window.watch = Vue.watch;
window.onMounted = Vue.onMounted;
window.onBeforeUnmount = Vue.onBeforeUnmount;
window.nextTick = Vue.nextTick;

// 输入防抖：用于搜索框等高频输入，避免每次按键都打后端
window.debounce = (fn, wait = 300) => {
  let timer = null;
  return function () {
    const ctx = this, args = arguments;
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(ctx, args), wait);
  };
};
