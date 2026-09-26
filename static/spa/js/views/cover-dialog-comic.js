// 漫画项目页 —— 生成封面确认弹框（漫画专属，不与短剧共享）：勾选「包含标题」。
window.Views = window.Views || {};

Views.comicCoverDialog = {
  props: {
    dlg: { type: Object, required: true },   // {show,target,includeTitle}
    busy: { type: Boolean, default: false },
  },
  emits: ['confirm'],
  template: `
    <el-dialog :model-value="dlg.show" @update:model-value="dlg.show = $event"
               :title="dlg.target === 'season' ? I18N.t('p.genSeasonFirst') : I18N.t('p.genFirst')"
               width="440px">
      <el-checkbox v-model="dlg.includeTitle">{{ I18N.t('p.includeTitle') }}</el-checkbox>
      <p class="hint" style="margin-top:8px;">{{ I18N.t('p.includeTitleHint') }}</p>
      <template #footer>
        <el-button @click="dlg.show = false">{{ I18N.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy" @click="$emit('confirm')">{{ I18N.t('p.genStart') }}</el-button>
      </template>
    </el-dialog>
  `,
};
