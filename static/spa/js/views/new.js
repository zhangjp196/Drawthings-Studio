// /new：新建创作（整页表单，与创作中心弹框同一组件）
window.Views = window.Views || {};
Views.newProject = {
  template: `
    <div class="page">
      <h1 style="margin: 0 0 4px; font-size: 22px;">{{ I18N.t('new.title') }}</h1>
      <p class="hint" style="margin-top: 0;">{{ I18N.t('new.hint') }}</p>
      <el-card shadow="never" style="max-width: 720px;">
        <create-form @created="go" />
      </el-card>
    </div>
  `,
  components: { 'create-form': Views.createForm },
  setup() {
    return { go: (id) => router.push('/project/' + id) };
  },
};
