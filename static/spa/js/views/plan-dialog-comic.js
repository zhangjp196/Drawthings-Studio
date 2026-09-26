// 漫画项目页 —— 章节规划弹框（漫画专属，不与短剧共享）：章节数量固定值 + 方式（新增/重做）。
window.Views = window.Views || {};

Views.comicPlanDialog = {
  props: {
    modelValue: { type: Boolean, default: false },
    title: { type: String, default: '' },
    count: { type: Number, default: 0 },
    mode: { type: String, default: 'append' },
    hint: { type: String, default: '' },
    busy: { type: Boolean, default: false },
  },
  emits: ['update:modelValue', 'update:count', 'update:mode', 'confirm'],
  template: `
    <el-dialog :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)"
               :title="title" width="480px">
      <el-form label-position="top">
        <el-form-item :label="I18N.t('p.countMode')">
          <el-input-number :model-value="count" @update:model-value="$emit('update:count', $event)"
                           :min="1" :max="99" size="small" style="width:96px" />
        </el-form-item>
        <el-form-item :label="I18N.t('p.planMode')">
          <el-radio-group :model-value="mode" @update:model-value="$emit('update:mode', $event)">
            <el-radio value="append">{{ I18N.t('p.planModeAppend') }}</el-radio>
            <el-radio value="replan">{{ I18N.t('p.planModeRedo') }}</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <p class="hint">{{ hint }}</p>
      <template #footer>
        <el-button @click="$emit('update:modelValue', false)">{{ I18N.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy" @click="$emit('confirm')">{{ I18N.t('p.planStart') }}</el-button>
      </template>
    </el-dialog>
  `,
};
