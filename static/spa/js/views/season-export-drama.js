// 短剧项目页 —— 「完成 / 导出」页签内容（短剧专属，不与漫画共享）。
// 纯展示 + 事件：ZIP（打包素材）+ 合成视频（预览 / 导出单个 mp4）。
window.Views = window.Views || {};

Views.dramaSeasonExport = {
  props: {
    done: { type: Number, default: 0 },          // 已生成章节数
    total: { type: Number, default: 0 },         // 本季章节数
    completed: { type: Boolean, default: false },// 本季是否全部完成
    exporting: { type: Boolean, default: false },
  },
  emits: ['zip', 'preview-video', 'export-video'],
  template: `
    <el-card shadow="never">
      <template #header><b>{{ I18N.t('p.tabExport') }}</b></template>
      <template v-if="total">
        <p class="muted small mb8">{{ I18N.t('d.epDoneProgress', done, total) }}</p>
        <el-alert v-if="completed" type="success" :closable="false"
                  :title="I18N.t('d.epDoneMsg')" class="mb8" />
      </template>
      <p class="muted small mb8" v-else>{{ I18N.t('d.epNoClips') }}</p>
      <div class="actions">
        <el-button type="primary" :loading="exporting" :disabled="!done" @click="$emit('export-video')">{{ I18N.t('p.exportVideo') }}</el-button>
        <el-button :disabled="!done" @click="$emit('preview-video')">{{ I18N.t('p.previewVideo') }}</el-button>
        <el-button :loading="exporting" :disabled="!done" @click="$emit('zip')">{{ I18N.t('p.exportZip') }}</el-button>
      </div>
      <p class="muted small" style="margin-top:8px;">{{ I18N.t('d.exportVideoHint2') }}</p>
    </el-card>
  `,
};
