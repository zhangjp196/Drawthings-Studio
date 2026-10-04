// 新建漫画创作表单（仅漫画走向）：模型（LLM 配置）+ 项目名称。
// 标题改在「总体 → 故事大纲」中编辑/随「生成大纲」一起生成；一句话创意 / AI 生成标题主题已移除。
// 与短剧创作完全独立（不共用组件）：短剧另有 create-form-drama.js
window.Views = window.Views || {};

Views.createFormComic = {
  emits: ['created'],
  template: `
    <el-form label-position="top">
      <el-form-item :label="I18N.t('cf.llm')" required>
        <el-select v-model="f.llm" :placeholder="I18N.t('cf.llmPh')" style="width: 100%">
          <el-option v-for="c in llms" :key="c.id" :value="c.id"
                     :label="c.name + '（' + c.model + '）'" />
          <el-option v-if="!llms.length" value="" :label="I18N.t('cf.llmNone')" />
        </el-select>
        <div class="hint">{{ I18N.t('cf.llmHint') }}<el-link :underline="false" type="primary" @click="toConfigs">{{ I18N.t('cf.newLlm') }}</el-link></div>
      </el-form-item>
      <el-form-item :label="I18N.t('cf.pName')" required>
        <el-input v-model="f.name" maxlength="100" :placeholder="I18N.t('cf.pNamePh')" />
        <div class="hint">{{ I18N.t('cf.pNamePh') }}</div>
      </el-form-item>
      <el-button type="primary" :loading="saving" @click="submit">{{ I18N.t('cf.start') }}</el-button>
    </el-form>
  `,
  setup(props, { emit }) {
    const llms = ref([]);
    const saving = ref(false);
    const f = reactive({ llm: '', name: '' });

    async function load() {
      try {
        const data = await API.get('/api/choices');
        llms.value = data.llm_configs;
      } catch (e) {
        llms.value = [];
        ElementPlus.ElMessage.error(I18N.t('cf.loadFail', e.message));
      }
      try {
        const s = await API.get('/api/settings');
        if (s.default_llm_config_id && llms.value.some(c => c.id === s.default_llm_config_id)) {
          f.llm = s.default_llm_config_id;
        }
      } catch (e) { /* 无默认配置则保持手选 */ }
    }

    async function submit() {
      if (!f.llm) { ElementPlus.ElMessage.warning(I18N.t('cf.wLlm')); return; }
      if (!f.name.trim()) { ElementPlus.ElMessage.warning(I18N.t('cf.pNameReq')); return; }
      saving.value = true;
      try {
        const data = await API.post('/api/comics', {
          name: f.name.trim(),
          llm_config_id: f.llm,
        });
        emit('created', data.id, 'comic');
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        saving.value = false;
      }
    }

    onMounted(load);
    return { f, llms, saving, submit,
             toConfigs: () => router.push('/configs?ctype=llm') };
  },
};