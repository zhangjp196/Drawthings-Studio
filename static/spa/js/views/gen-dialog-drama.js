// 短剧项目页 —— 「生成」确认弹框（短剧专属，不与漫画共享）：显示步骤标题 + 可选补充提示词。
window.Views = window.Views || {};

Views.dramaGenDialog = {
  props: {
    modelValue: { type: Boolean, default: false },
    title: { type: String, default: '' },
    extra: { type: String, default: '' },
    busy: { type: Boolean, default: false },
  },
  emits: ['update:modelValue', 'update:extra', 'confirm'],
  template: `
    <el-dialog :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)"
               :title="title" width="540px">
      <p class="hint">{{ I18N.t('p.genDlgHint') }}</p>
      <el-form label-position="top">
        <el-form-item :label="I18N.t('p.genExtraPrompt')">
          <el-input :model-value="extra" @update:model-value="$emit('update:extra', $event)"
                    type="textarea" :rows="4" :placeholder="I18N.t('p.genExtraPromptPh')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="$emit('update:modelValue', false)">{{ I18N.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy" @click="$emit('confirm')">{{ I18N.t('p.genStart') }}</el-button>
      </template>
    </el-dialog>
  `,
};
