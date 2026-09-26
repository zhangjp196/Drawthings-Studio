// 短剧项目页 —— 重设弹框（短剧专属，不与漫画共享）：标题 / 主题 / 风格（+ 可选清空重建）。
window.Views = window.Views || {};

Views.dramaResetDialog = {
  props: {
    modelValue: { type: Boolean, default: false },
    title: { type: String, default: '' },
    origin: { type: String, default: '' },
    style: { type: String, default: '' },
    custom: { type: String, default: '' },
    clear: { type: Boolean, default: false },
    stylePresets: { type: Array, default: () => [] },
  },
  emits: ['update:modelValue', 'update:title', 'update:origin', 'update:style', 'update:custom', 'update:clear', 'save'],
  template: `
    <el-dialog :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)"
               :title="I18N.t('p.reset')" width="540px">
      <p class="hint">{{ I18N.t('p.resetHint') }}</p>
      <el-form label-position="top">
        <el-form-item :label="I18N.t('p.resetTitle')">
          <el-input :model-value="title" @update:model-value="$emit('update:title', $event)" maxlength="200" />
        </el-form-item>
        <el-form-item :label="I18N.t('p.resetOrigin')">
          <el-input :model-value="origin" @update:model-value="$emit('update:origin', $event)" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item :label="I18N.t('p.resetStyle')">
          <el-select :model-value="style" @update:model-value="$emit('update:style', $event)" style="width: 100%">
            <el-option value="" :label="I18N.t('cf.styleAuto')" />
            <el-option v-for="s in stylePresets" :key="s" :value="s" :label="s" />
            <el-option value="custom" :label="I18N.t('cf.styleCustom')" />
          </el-select>
          <el-input v-if="style === 'custom'" :model-value="custom" @update:model-value="$emit('update:custom', $event)"
                    class="mt8" :placeholder="I18N.t('cf.styleCustomPh')" />
        </el-form-item>
      </el-form>
      <el-checkbox :model-value="clear" @update:model-value="$emit('update:clear', $event)" style="margin-bottom: 4px;">{{ I18N.t('p.resetClear') }}</el-checkbox>
      <template #footer>
        <el-button @click="$emit('update:modelValue', false)">{{ I18N.t('common.cancel') }}</el-button>
        <el-button type="primary" @click="$emit('save')">{{ I18N.t('p.resetSave') }}</el-button>
      </template>
    </el-dialog>
  `,
};
