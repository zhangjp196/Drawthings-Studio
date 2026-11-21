// /new/comic：新建漫画创作 v2（页面头 + 横向流程条 + 居中表单卡）
// 与旧版「左表单 + 右指引」不同：流程步骤前置为一条横向进度带，表单单栏居中。
window.Views = window.Views || {};
Views.newComic = {
  components: { 'create-form-comic': Views.createFormComic },
  template: `
    <div class="page new-page">
      <div class="ds-page-head">
        <div>
          <div class="ds-eyebrow">{{ I18N.t('nav.groupCreate') }}</div>
          <h1>{{ I18N.t('new.comicTitle') }}</h1>
          <p class="ds-page-sub">{{ I18N.t('new.comicHint') }}</p>
        </div>
        <div class="ds-page-actions">
          <el-button @click="router.back()">{{ I18N.t('new.back') }}</el-button>
        </div>
      </div>

      <div class="new-flow">
        <div class="new-flow-step" v-for="(s, i) in steps" :key="i"><b>{{ i + 1 }}</b><span>{{ s }}</span></div>
      </div>

      <section class="new-card">
        <div class="new-card-head">
          <span class="ds-eyebrow">{{ I18N.t('new.flow1') }}</span>
          <el-tag size="small" type="primary" effect="light">{{ I18N.t('proj.comic') }}</el-tag>
        </div>
        <create-form-comic @created="go" />
        <p class="hint" style="margin-top: 16px;">{{ I18N.t('new.guideHint') }}</p>
      </section>
    </div>
  `,
  setup() {
    const steps = computed(() => [
      I18N.t('new.flow1'), I18N.t('new.flowC2'), I18N.t('new.flowC3'), I18N.t('new.flowC4'),
    ]);
    return { steps, go: (id) => router.push('/comic/' + id) };
  },
};
