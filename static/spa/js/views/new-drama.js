// /new/drama：新建短剧创作（左侧表单卡 + 右侧创作流程指引）
window.Views = window.Views || {};
Views.newDrama = {
  template: `
    <div class="page new-page">
      <div class="new-wrap">
        <section class="new-card">
          <header class="new-head">
            <el-tag size="small" type="success" effect="light">{{ I18N.t('proj.drama') }}</el-tag>
            <h1>{{ I18N.t('new.dramaTitle') }}</h1>
            <p class="muted small">{{ I18N.t('new.dramaHint') }}</p>
          </header>
          <create-form-drama @created="go" />
        </section>
        <aside class="new-guide">
          <h3>{{ I18N.t('new.flowTitle') }}</h3>
          <ol class="flow">
            <li><b>1</b><span>{{ I18N.t('new.flow1') }}</span></li>
            <li><b>2</b><span>{{ I18N.t('new.flowD2') }}</span></li>
            <li><b>3</b><span>{{ I18N.t('new.flowD3') }}</span></li>
            <li><b>4</b><span>{{ I18N.t('new.flowD4') }}</span></li>
          </ol>
          <p class="hint">{{ I18N.t('new.guideHint') }}</p>
        </aside>
      </div>
    </div>
  `,
  components: { 'create-form-drama': Views.createFormDrama },
  setup() {
    return { go: (id) => router.push('/drama/' + id) };
  },
};
