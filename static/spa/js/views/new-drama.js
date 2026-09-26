// /new/drama：新建短剧创作（整页表单）
window.Views = window.Views || {};
Views.newDrama = {
  template: `
    <div class="page">
      <h1 style="margin: 0 0 14px; font-size: 22px;">{{ I18N.t('new.dramaTitle') }}</h1>
      <el-card shadow="never" style="max-width: 960px;">
        <create-form-drama @created="go" />
      </el-card>
    </div>
  `,
  components: { 'create-form-drama': Views.createFormDrama },
  setup() {
    return { go: (id) => router.push('/drama/' + id) };
  },
};
