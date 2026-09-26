// /new：新建创作（整页表单，与创作中心弹框同一组件）
window.Views = window.Views || {};
Views.newProject = {
  template: `
    <div class="page">
      <h1 style="margin: 0 0 14px; font-size: 22px;">{{ I18N.t('new.title') }}</h1>
      <el-card shadow="never" style="max-width: 960px;">
        <create-form @created="go" />
      </el-card>
    </div>
  `,
  components: { 'create-form': Views.createForm },
  setup() {
    const route = router.currentRoute;
    // 类型由入口决定：/new?kind=comic|drama（首页两个入口按钮）
    const preset = computed(() => {
      const k = route.value.query.kind;
      return (k === 'comic' || k === 'drama') ? { kind: k } : null;
    });
    return { preset, go: (id, kind) => router.push((kind === 'comic' ? '/comic/' : '/drama/') + id) };
  },
};
