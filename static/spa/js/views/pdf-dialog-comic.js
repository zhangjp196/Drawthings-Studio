// 漫画项目页 —— PDF 预览弹框（漫画专属）：iframe 直接渲染导出端点返回的 inline PDF，关闭即销毁。
window.Views = window.Views || {};

Views.comicPdfDialog = {
  props: {
    modelValue: { type: Boolean, default: false },
    url: { type: String, default: '' },
  },
  emits: ['update:modelValue', 'closed'],
  template: `
    <el-dialog :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)"
               :title="I18N.t('p.previewPdf')" width="86%" top="4vh" destroy-on-close
               @closed="$emit('closed')">
      <iframe v-if="url" :src="url" class="pdf-frame" />
      <template #footer>
        <el-button @click="$emit('update:modelValue', false)">{{ I18N.t('common.cancel') }}</el-button>
      </template>
    </el-dialog>
  `,
};
