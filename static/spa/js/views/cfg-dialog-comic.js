// 漫画项目页 —— 项目设置弹框（漫画专属，不与短剧共享）：LLM / DrawThings / 模型 / 参考图开关。
window.Views = window.Views || {};

Views.comicCfgDialog = {
  props: {
    modelValue: { type: Boolean, default: false },
    cfg: { type: Object, required: true },       // {llm,dt,dt_model,dt_ref}
    llmConfigs: { type: Array, default: () => [] },
    dtConfigs: { type: Array, default: () => [] },
    modelChoices: { type: Array, default: () => [] },
    busy: { type: Boolean, default: false },
  },
  emits: ['update:modelValue', 'save', 'new-config'],
  template: `
    <el-dialog :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)"
               :title="I18N.t('p.cfgTitle')" width="540px">
      <p class="hint">{{ I18N.t('p.cfgHint') }}</p>
      <el-form label-position="top">
        <el-form-item :label="I18N.t('cf.llm')">
          <el-select v-model="cfg.llm" style="width: 100%">
            <el-option v-for="c in llmConfigs" :key="c.id" :value="c.id"
                       :label="c.name + '（' + c.model + '）'" />
          </el-select>
        </el-form-item>
        <el-form-item :label="I18N.t('cf.dt')">
          <el-select v-model="cfg.dt" style="width: 100%">
            <el-option v-for="c in dtConfigs" :key="c.id" :value="c.id" :label="c.name" />
            <el-option v-if="!dtConfigs.length" value="" :label="I18N.t('cf.dtNone')" />
          </el-select>
          <div class="hint">{{ I18N.t('cf.dtHint') }}<el-link :underline="false" type="primary" @click="$emit('new-config')">{{ I18N.t('cf.newCfg') }}</el-link></div>
        </el-form-item>
        <el-form-item v-if="cfg.dt" :label="I18N.t('cf.dtModelImage')">
          <el-select v-model="cfg.dt_model" filterable allow-create clearable style="width: 100%"
                     :placeholder="I18N.t('cf.dtModelPh')">
            <el-option :value="''" :label="I18N.t('cf.dtModelFollow')" />
            <el-option v-for="m in modelChoices" :key="m.file" :value="m.file" :label="m.label" />
          </el-select>
          <el-checkbox v-model="cfg.dt_ref" style="margin-top:4px;">{{ I18N.t('cfg.refImage') }}</el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="$emit('update:modelValue', false)">{{ I18N.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy" @click="$emit('save')">{{ I18N.t('p.cfgSave') }}</el-button>
      </template>
    </el-dialog>
  `,
};
