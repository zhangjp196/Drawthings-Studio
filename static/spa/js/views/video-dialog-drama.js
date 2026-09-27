// 短剧项目页 —— 合成视频预览弹框（短剧专属）：<video> 直接播放导出端点返回的 inline mp4，关闭即销毁。
window.Views = window.Views || {};

Views.dramaVideoDialog = {
  props: {
    modelValue: { type: Boolean, default: false },
    url: { type: String, default: '' },
  },
  emits: ['update:modelValue', 'closed'],
  template: `
    <el-dialog :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)"
               :title="I18N.t('p.previewVideo')" width="72%" top="6vh" destroy-on-close
               @closed="$emit('closed')">
      <video v-if="url" :src="url" controls autoplay class="compose-video" />
      <template #footer>
        <el-button @click="$emit('update:modelValue', false)">{{ I18N.t('common.cancel') }}</el-button>
      </template>
    </el-dialog>
  `,
};
