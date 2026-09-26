// /new/comic：新建漫画创作（整页表单）
window.Views = window.Views || {};
Views.newComic = {
  template: `
    <div class="page">
      <h1 style="margin: 0 0 14px; font-size: 22px;">{{ I18N.t('new.comicTitle') }}</h1>
      <el-card shadow="never" style="max-width: 960px;">
        <create-form-comic @created="go" />
      </el-card>
    </div>
  `,
  components: { 'create-form-comic': Views.createFormComic },
  setup() {
    return { go: (id) => router.push('/comic/' + id) };
  },
};
