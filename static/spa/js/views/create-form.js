// 新建创作表单（创作中心弹框与 /new 页共用）：类型由入口决定，LLM / DrawThings / 模型 / 参考图 / 标题 / 主题 / 风格
window.Views = window.Views || {};
Views.createForm = {
  props: {
    preset: { type: Object, default: null }, // { kind, origin, title }
  },
  emits: ['created'],
  template: `
    <el-form label-position="top">
      <el-row :gutter="24">
        <el-col :span="10">
          <!-- 左：配置（LLM / DrawThings / 模型 / 参考图）；类型由入口决定 -->
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
          <el-row :gutter="12" v-if="f.dt">
            <el-col :span="12">
              <el-form-item :label="I18N.t('cf.dtModelImage')">
                <el-select v-model="f.dt_model_i" filterable allow-create clearable style="width: 100%"
                           :placeholder="I18N.t('cf.dtModelPh')">
                  <el-option :value="''" :label="I18N.t('cf.dtModelFollow')" />
                  <el-option v-for="m in imgChoices" :key="m.file" :value="m.file" :label="m.label" />
                </el-select>
                <el-checkbox v-model="f.dt_ref_i" style="margin-top:4px;">{{ I18N.t('cfg.refImage') }}</el-checkbox>
              </el-form-item>
            </el-col>
            <el-col v-if="f.kind === 'drama'" :span="12">
              <el-form-item :label="I18N.t('cf.dtModelVideo')">
                <el-select v-model="f.dt_model_v" filterable allow-create clearable style="width: 100%"
                           :placeholder="I18N.t('cf.dtModelPh')">
                  <el-option :value="''" :label="I18N.t('cf.dtModelFollow')" />
                  <el-option v-for="m in vidChoices" :key="m.file" :value="m.file" :label="m.label" />
                </el-select>
                <el-checkbox v-model="f.dt_ref_v" style="margin-top:4px;">{{ I18N.t('cfg.refVideo') }}</el-checkbox>
              </el-form-item>
            </el-col>
          </el-row>
        </el-col>
        <el-col :span="14">
          <!-- 右：创作内容（标题 / 主题 / 风格） -->
          <el-form-item :label="I18N.t('cf.title')">
            <el-input v-model="f.title" maxlength="100" :placeholder="I18N.t('cf.titlePh')" />
          </el-form-item>
          <el-form-item :label="I18N.t('cf.origin')" required>
            <el-input v-model="f.origin" type="textarea" :rows="5" :placeholder="I18N.t('cf.originPh')" />
          </el-form-item>
          <el-form-item :label="I18N.t('cf.style')">
            <el-select v-model="f.style" style="width: 100%">
              <el-option value="" :label="I18N.t('cf.styleAuto')" />
              <el-option v-for="(s, i) in presets" :key="s" :value="s" :label="s" />
              <el-option value="custom" :label="I18N.t('cf.styleCustom')" />
            </el-select>
            <el-input v-if="f.style === 'custom'" v-model="f.styleCustom" class="mt8" :placeholder="I18N.t('cf.styleCustomPh')" />
          </el-form-item>
        </el-col>
      </el-row>
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
      dt_model_i: '',
      dt_model_v: '',
      dt_ref_i: false,
      dt_ref_v: false,
      title: (props.preset && props.preset.title) || '',
      origin: (props.preset && props.preset.origin) || '',
      style: '',
      styleCustom: '',
    });

    // 功能级模型：按所选 DrawThings 配置的端点拉取 app 已下载模型
    const dtModels = ref([]);
    const imgChoices = computed(() => dtModels.value
      .filter(m => m.file && !m.video)
      .map(m => ({ file: m.file, label: m.file + (m.name ? ' · ' + m.name : '') })));
    const vidChoices = computed(() => dtModels.value
      .filter(m => m.file && m.video)
      .map(m => ({ file: m.file, label: m.file + (m.name ? ' · ' + m.name : '') })));
    async function fetchModels() {
      const c = dtsAll.value.find(x => x.id === f.dt);
      if (!c || !c.base_url) { dtModels.value = []; return; }
      try {
        const data = await API.get('/api/dt-models?base_url=' + encodeURIComponent(c.base_url));
        dtModels.value = data.models || [];
      } catch (e) {
        dtModels.value = [];  // app 未开 gRPC 不阻塞表单：可手动输入模型文件名
      }
    }
    watch(() => f.dt, (id) => {
      f.dt_model_i = ''; f.dt_model_v = ''; f.dt_ref_i = false; f.dt_ref_v = false;
      const c = dtsAll.value.find(x => x.id === id);
      if (!c) return;
      // 预填配置里的模型 / 参考图开关（功能级可覆盖）；配置未设模型则保持空 = 必须自选
      f.dt_model_i = c.model_image || '';
      f.dt_model_v = c.model_video || '';
      f.dt_ref_i = !!c.ref_image;
      f.dt_ref_v = !!c.ref_video;
      fetchModels();
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
      // 功能级模型：未选且配置里也没有 → 前端先拦（后端同样校验：漫画=图像、短剧=视频）
      const c0 = dtsAll.value.find(x => x.id === f.dt);
      if (f.kind === 'comic') {
        if (!f.dt_model_i && !(c0 && c0.model_image)) {
          ElementPlus.ElMessage.warning(I18N.t('cf.wDtModel')); return;
        }
      } else if (!f.dt_model_v && !(c0 && c0.model_video)) {
        ElementPlus.ElMessage.warning(I18N.t('cf.wDtModelV')); return;
      }
      saving.value = true;
      try {
        const data = await API.post('/api/projects', {
          kind: f.kind, origin: f.origin.trim(), title: f.title.trim(),
          llm_config_id: f.llm, drawthings_config_id: f.dt,
          dt_model_image: f.dt_model_i,
          dt_model_video: f.kind === 'drama' ? f.dt_model_v : '',
          dt_ref_image: f.dt_ref_i ? 1 : 0,
          dt_ref_video: f.kind === 'drama' ? (f.dt_ref_v ? 1 : 0) : 0,
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
    return { f, llms, dts, presets, saving, submit, imgChoices, vidChoices,
             toConfigs: () => router.push('/configs?ctype=drawthings') };
  },
};
