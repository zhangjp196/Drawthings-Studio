// /new/drama：新建短剧创作 v2（页面头 + 横向流程条 + 居中表单卡）
// 与漫画线对称维护：结构一致，术语与流程文案走短剧专用 key。
window.Views = window.Views || {};
Views.newDrama = {
  components: { 'create-form-drama': Views.createFormDrama },
  template: `
    <div class="page new-page">
      <div class="ds-page-head">
        <div>
          <div class="ds-eyebrow">{{ I18N.t('nav.groupCreate') }}</div>
          <h1>{{ I18N.t('new.dramaTitle') }}</h1>
          <p class="ds-page-sub">{{ I18N.t('new.dramaHint') }}</p>
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
          <el-tag size="small" type="success" effect="light">{{ I18N.t('proj.drama') }}</el-tag>
        </div>
        <create-form-drama @created="go" />
        <p class="hint" style="margin-top: 16px;">{{ I18N.t('new.guideHint') }}</p>
      </section>
    </div>
  `,
  setup() {
    const steps = computed(() => [
      I18N.t('new.flow1'), I18N.t('new.flowD2'), I18N.t('new.flowD3'), I18N.t('new.flowD4'),
    ]);
    return { steps, go: (id) => router.push('/drama/' + id) };
  },
};
