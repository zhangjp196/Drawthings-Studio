// 新建创作表单（创作中心弹框与 /new 页共用）：类型 / LLM / DrawThings / 标题 / 主题 / 风格
window.Views = window.Views || {};
Views.createForm = {
  props: {
    preset: { type: Object, default: null }, // { kind, origin, title }
  },
  emits: ['created'],
  template: `
    <el-form label-position="top">
      <el-form-item :label="I18N.t('cf.type')">
        <el-radio-group v-model="f.kind" @change="f.dt = ''">
          <el-radio value="comic">{{ I18N.t('cf.comic') }}</el-radio>
          <el-radio value="drama">{{ I18N.t('cf.drama') }}</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item :label="I18N.t('cf.llm')" required>
        <el-select v-model="f.llm" :placeholder="I18N.t('cf.llmPh')" style="width: 100%">
          <el-option v-for="c in llms" :key="c.id" :value="c.id"
                     :label="c.name + '（' + c.model + '）'" />
          <el-option v-if="!llms.length" value="" :label="I18N.t('cf.llmNone')" />
        </el-select>
        <div class="hint">{{ I18N.t('cf.llmHint') }}<el-link :underline="false" type="primary" @click="toConfigs">{{ I18N.t('cf.newLlm') }}</el-link></div>
      </el-form-item>
      <el-form-item :label="I18N.t('cf.dt')" required>
        <el-select v-model="f.dt" :placeholder="I18N.t('cf.dtPh')" style="width: 100%">
          <el-option v-for="c in dts" :key="c.id" :value="c.id" :label="c.name" />
          <el-option v-if="!dts.length" value="" :label="I18N.t('cf.dtNone')" />
        </el-select>
        <div class="hint">{{ I18N.t('cf.dtHint') }}<el-link :underline="false" type="primary" @click="toConfigs">{{ I18N.t('cf.newCfg') }}</el-link></div>
      </el-form-item>
      <el-form-item :label="I18N.t('cf.title')">
        <el-input v-model="f.title" maxlength="100" :placeholder="I18N.t('cf.titlePh')" />
      </el-form-item>
      <el-form-item :label="I18N.t('cf.origin')" required>
        <el-input v-model="f.origin" type="textarea" :rows="3" :placeholder="I18N.t('cf.originPh')" />
      </el-form-item>
      <el-form-item :label="I18N.t('cf.style')">
        <el-select v-model="f.style" style="width: 100%">
          <el-option value="" :label="I18N.t('cf.styleAuto')" />
          <el-option v-for="(s, i) in presets" :key="s" :value="s" :label="s" />
          <el-option value="custom" :label="I18N.t('cf.styleCustom')" />
        </el-select>
        <el-input v-if="f.style === 'custom'" v-model="f.styleCustom" class="mt8" :placeholder="I18N.t('cf.styleCustomPh')" />
      </el-form-item>
      <el-button type="primary" :loading="saving" @click="submit">{{ I18N.t('cf.start') }}</el-button>
    </el-form>
  `,
  setup(props, { emit }) {
    const presets = computed(() => [0, 1, 2, 3, 4, 5].map(i => I18N.t('cf.preset.' + i)));
    const llms = ref([]);
    const dtsAll = ref([]);
    const saving = ref(false);
    const f = reactive({
      kind: (props.preset && props.preset.kind) || 'comic',
      llm: '',
      dt: '',
      title: (props.preset && props.preset.title) || '',
      origin: (props.preset && props.preset.origin) || '',
      style: '',
      styleCustom: '',
    });

    async function load() {
      const data = await API.get('/api/choices');
      llms.value = data.llm_configs;
      dtsAll.value = data.drawthing_configs;
      // 基础配置里的默认配置 → 预填（仅当对应配置仍存在时）
      try {
        const s = await API.get('/api/settings');
        if (s.default_llm_config_id && llms.value.some(c => c.id === s.default_llm_config_id)) {
          f.llm = s.default_llm_config_id;
        }
        if (s.default_dt_config_id && dtsAll.value.some(c => c.id === s.default_dt_config_id)) {
          f.dt = s.default_dt_config_id;
        }
      } catch (e) { /* 无默认配置则保持手选 */ }
    }

    const dts = computed(() => dtsAll.value);

    async function submit() {
      if (!f.llm) { ElementPlus.ElMessage.warning(I18N.t('cf.wLlm')); return; }
      if (!f.dt) { ElementPlus.ElMessage.warning(I18N.t('cf.wDt')); return; }
      if (!f.origin.trim()) { ElementPlus.ElMessage.warning(I18N.t('cf.wOrigin')); return; }
      saving.value = true;
      try {
        const data = await API.post('/api/projects', {
          kind: f.kind, origin: f.origin.trim(), title: f.title.trim(),
          llm_config_id: f.llm, drawthings_config_id: f.dt,
          style: f.style === 'custom' ? '' : f.style,
          style_custom: f.style === 'custom' ? f.styleCustom.trim() : '',
        });
        emit('created', data.id, f.kind);
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        saving.value = false;
      }
    }

    onMounted(load);
    return { f, llms, dts, presets, saving, submit,
             toConfigs: () => router.push('/configs?ctype=drawthings') };
  },
};
