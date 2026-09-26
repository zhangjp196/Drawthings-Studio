// 短剧项目页 —— 项目设置弹框（短剧专属，不与漫画共享）：LLM / DrawThings / 图像+视频模型 / 参考图开关。
window.Views = window.Views || {};

Views.dramaCfgDialog = {
  props: {
    modelValue: { type: Boolean, default: false },
    cfg: { type: Object, required: true },       // {llm,dt,dt_model_i,dt_ref_i,dt_model_v,dt_ref_v}
    llmConfigs: { type: Array, default: () => [] },
    dtConfigs: { type: Array, default: () => [] },
    imgChoices: { type: Array, default: () => [] },
    vidChoices: { type: Array, default: () => [] },
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
          <el-select v-model="cfg.dt_model_i" filterable allow-create clearable style="width: 100%"
                     :placeholder="I18N.t('cf.dtModelPh')">
            <el-option :value="''" :label="I18N.t('cf.dtModelFollow')" />
            <el-option v-for="m in imgChoices" :key="'i' + m.file" :value="m.file" :label="m.label" />
          </el-select>
          <el-checkbox v-model="cfg.dt_ref_i" style="margin-top:4px;">{{ I18N.t('cfg.refImage') }}</el-checkbox>
        </el-form-item>
        <el-form-item v-if="cfg.dt" :label="I18N.t('cf.dtModelVideo')">
          <el-select v-model="cfg.dt_model_v" filterable allow-create clearable style="width: 100%"
                     :placeholder="I18N.t('cf.dtModelPh')">
            <el-option :value="''" :label="I18N.t('cf.dtModelFollow')" />
            <el-option v-for="m in vidChoices" :key="'v' + m.file" :value="m.file" :label="m.label" />
          </el-select>
          <el-checkbox v-model="cfg.dt_ref_v" style="margin-top:4px;">{{ I18N.t('cfg.refVideo') }}</el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="$emit('update:modelValue', false)">{{ I18N.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy" @click="$emit('save')">{{ I18N.t('p.cfgSave') }}</el-button>
      </template>
    </el-dialog>
  `,
};
