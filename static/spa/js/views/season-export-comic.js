// 漫画项目页 —— 「完成 / 导出」页签内容（漫画专属，不与短剧共享）。
// 纯展示 + 事件：ZIP / PDF / PDF 预览由父组件执行。
window.Views = window.Views || {};

Views.comicSeasonExport = {
  props: {
    done: { type: Number, default: 0 },          // 已生成章节数
    total: { type: Number, default: 0 },         // 本季章节数
    completed: { type: Boolean, default: false },// 本季是否全部完成
    exporting: { type: Boolean, default: false },
  },
  emits: ['zip', 'pdf', 'preview'],
  template: `
    <el-card shadow="never">
      <template #header><b>{{ I18N.t('p.tabExport') }}</b></template>
      <template v-if="total">
        <p class="muted small mb8">{{ I18N.t('p.seasonDoneProgress', done, total) }}</p>
        <el-alert v-if="completed" type="success" :closable="false"
                  :title="I18N.t('p.seasonDoneMsg')" class="mb8" />
      </template>
      <p class="muted small mb8" v-else>{{ I18N.t('p.seasonNoChapters') }}</p>
      <div class="actions">
        <el-button type="primary" :loading="exporting" :disabled="!done" @click="$emit('zip')">{{ I18N.t('p.exportZip') }}</el-button>
        <el-button type="primary" :loading="exporting" :disabled="!done" @click="$emit('pdf')">{{ I18N.t('p.exportPdf') }}</el-button>
        <el-button type="primary" plain :loading="exporting" :disabled="!done" @click="$emit('preview')">{{ I18N.t('p.previewPdf') }}</el-button>
      </div>
    </el-card>
  `,
};
