// 新建创作表单（创作中心弹框与 /new 页共用）：类型 / LLM / DrawThings / 标题 / 主题 / 风格
window.Views = window.Views || {};
Views.createForm = {
  props: {
    preset: { type: Object, default: null }, // { kind, origin, title }
  },
  emits: ['created'],
  template: `
    <el-form label-position="top">
      <el-form-item label="选择类型">
        <el-radio-group v-model="f.kind" @change="f.dt = ''">
          <el-radio value="comic">漫画走向（连续生图）</el-radio>
          <el-radio value="drama">短剧走向（连续出视频）</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="LLM 配置" required>
        <el-select v-model="f.llm" placeholder="选择 LLM 配置" style="width: 100%">
          <el-option v-for="c in llms" :key="c.id" :value="c.id"
                     :label="c.name + '（' + c.model + (c.supports_vision === 'no' ? ' / 纯文本' : '') + '）'" />
          <el-option v-if="!llms.length" value="" label="（无 LLM 配置，请先创建）" />
        </el-select>
        <div class="hint">「支持图片输入」的模型可在剧本阶段看参考帧。<el-link :underline="false" type="primary" @click="toConfigs">＋ 新建 LLM 配置</el-link></div>
      </el-form-item>
      <el-form-item label="DrawThings 配置" required>
        <el-select v-model="f.dt" placeholder="选择 DrawThings 配置" style="width: 100%">
          <el-option v-for="c in dts" :key="c.id" :value="c.id" :label="c.name" />
          <el-option v-if="!dts.length" value="" label="（无 DrawThings 配置，请先创建）" />
        </el-select>
        <div class="hint">出图/出视频由 app 里当前加载的模型决定。<el-link :underline="false" type="primary" @click="toConfigs">＋ 新建配置</el-link></div>
      </el-form-item>
      <el-form-item label="标题">
        <el-input v-model="f.title" maxlength="100" placeholder="例如：猫的四季旅行（留空则以主题作为标题）" />
      </el-form-item>
      <el-form-item label="主题" required>
        <el-input v-model="f.origin" type="textarea" :rows="3"
                  placeholder="用一句话描述故事，例如：一只猫旅行穿越四季，从春到冬" />
      </el-form-item>
      <el-form-item label="风格">
        <el-select v-model="f.style" style="width: 100%">
          <el-option value="" label="自动（由 LLM 推荐）" />
          <el-option v-for="s in presets" :key="s" :value="s" :label="s" />
          <el-option value="custom" label="自定义…" />
        </el-select>
        <el-input v-if="f.style === 'custom'" v-model="f.styleCustom" class="mt8"
                  placeholder="自定义风格描述，例如：吉卜力式暖色调手绘" />
      </el-form-item>
      <el-button type="primary" :loading="saving" @click="submit">开始创作</el-button>
    </el-form>
  `,
  setup(props, { emit }) {
    const presets = ['日系漫画风', '国风水墨', 'Q版可爱', '写实电影感', '皮克斯3D风', '赛博朋克风'];
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
    }

    const dts = computed(() => dtsAll.value);

    async function submit() {
      if (!f.llm) { ElementPlus.ElMessage.warning('请选择 LLM 配置'); return; }
      if (!f.dt) { ElementPlus.ElMessage.warning('请选择 DrawThings 配置'); return; }
      if (!f.origin.trim()) { ElementPlus.ElMessage.warning('请填写主题（一句话）'); return; }
      saving.value = true;
      try {
        const data = await API.post('/api/projects', {
          kind: f.kind, origin: f.origin.trim(), title: f.title.trim(),
          llm_config_id: f.llm, drawthings_config_id: f.dt,
          style: f.style === 'custom' ? '' : f.style,
          style_custom: f.style === 'custom' ? f.styleCustom.trim() : '',
        });
        emit('created', data.id);
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
