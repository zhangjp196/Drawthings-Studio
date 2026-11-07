// /new/comic：新建漫画创作（左侧表单卡 + 右侧创作流程指引）
window.Views = window.Views || {};
Views.newComic = {
  template: `
    <div class="page new-page">
      <div class="new-wrap">
        <section class="new-card">
          <header class="new-head">
            <el-tag size="small" type="primary" effect="light">{{ I18N.t('proj.comic') }}</el-tag>
            <h1>{{ I18N.t('new.comicTitle') }}</h1>
            <p class="muted small">{{ I18N.t('new.comicHint') }}</p>
          </header>
          <create-form-comic @created="go" />
        </section>
        <aside class="new-guide">
          <h3>{{ I18N.t('new.flowTitle') }}</h3>
          <ol class="flow">
            <li><b>1</b><span>{{ I18N.t('new.flow1') }}</span></li>
            <li><b>2</b><span>{{ I18N.t('new.flowC2') }}</span></li>
            <li><b>3</b><span>{{ I18N.t('new.flowC3') }}</span></li>
            <li><b>4</b><span>{{ I18N.t('new.flowC4') }}</span></li>
          </ol>
          <p class="hint">{{ I18N.t('new.guideHint') }}</p>
        </aside>
      </div>
    </div>
  `,
  components: { 'create-form-comic': Views.createFormComic },
  setup() {
    return { go: (id) => router.push('/comic/' + id) };
  },
};
