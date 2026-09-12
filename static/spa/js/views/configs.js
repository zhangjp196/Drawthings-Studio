// 配置管理：LLM / DrawThings 两个标签页 + 卡片列表（编辑/删除，引用检查在后端）+ 新建/编辑弹框（动态字段）
window.Views = window.Views || {};
Views.configs = {
  template: `
    <div class="page">
      <div class="list-toolbar">
        <h1 style="margin: 0; font-size: 22px;">{{ I18N.t('cfg.title') }}</h1>
        <el-button type="primary" @click="openNew">{{ I18N.t('cfg.new') }}</el-button>
      </div>
      <p class="hint" style="margin-top: 0;">{{ I18N.t('cfg.hint') }}</p>

      <el-tabs v-model="ctype" @tab-change="tabChange">
        <el-tab-pane :label="I18N.t('cfg.tabLlm', llmCount)" name="llm" />
        <el-tab-pane :label="I18N.t('cfg.tabDt', dtCount)" name="drawthings" />
      </el-tabs>

      <div class="cfg-grid" v-if="items.length">
        <div class="cfg-card" v-for="row in items" :key="row.id">
          <div class="wc-top">
            <el-tag v-if="ctype === 'llm'" size="small" type="primary" effect="light">LLM</el-tag>
            <el-tag v-else size="small" type="primary" effect="light">DrawThings</el-tag>
            <span class="cfg-name">{{ row.name }}</span>
          </div>
          <div class="cfg-meta muted" v-if="ctype === 'llm'">
            {{ I18N.t('cfg.model', row.model) }} · {{ row.supports_vision === 'yes' ? I18N.t('cfg.vision') : I18N.t('cfg.textOnly') }}
          </div>
          <div class="cfg-meta muted" v-else>{{ dtMeta(row) }}</div>
          <div class="cfg-url" :title="row.base_url">{{ row.base_url }}</div>
          <div class="wc-meta muted">{{ I18N.t('cfg.created', fmt(row.created_at)) }}</div>
          <div class="wc-actions">
            <el-button size="small" @click="openEdit(row)">{{ I18N.t('cfg.edit') }}</el-button>
            <el-popconfirm :title="I18N.t('cfg.delConfirm')" @confirm="del(row)">
              <template #reference><el-button size="small" type="danger" plain>{{ I18N.t('proj.delete') }}</el-button></template>
            </el-popconfirm>
          </div>
        </div>
      </div>
      <el-empty v-else :description="I18N.t('cfg.empty', ctype === 'llm' ? 'LLM' : 'DrawThings')" />

      <el-dialog v-model="dlg" :title="editId ? I18N.t('cfg.dlgEdit') : I18N.t('cfg.dlgNew')" width="580px">
        <el-form label-position="top">
          <el-form-item :label="I18N.t('cfg.type')">
            <el-select v-model="f.config_type" style="width: 100%">
              <el-option value="llm" :label="I18N.t('cfg.typeLlm')" />
              <el-option value="drawthings" :label="I18N.t('cfg.typeDt')" />
            </el-select>
          </el-form-item>
          <el-form-item :label="I18N.t('cfg.name')">
            <el-input v-model="f.name" :placeholder="I18N.t('cfg.namePh')" />
          </el-form-item>
          <el-form-item :label="I18N.t('cfg.url')">
            <el-input v-model="f.base_url" :placeholder="urlPh" />
            <div class="hint">{{ urlHint }}</div>
          </el-form-item>
          <template v-if="f.config_type === 'llm'">
            <el-form-item :label="I18N.t('cfg.key')">
              <el-input v-model="f.api_key" :placeholder="editId ? I18N.t('cfg.keyPhEdit') : I18N.t('cfg.keyPhNew')" />
            </el-form-item>
            <el-form-item :label="I18N.t('cfg.model')">
              <div style="display: flex; gap: 8px; width: 100%;">
                <el-select v-model="f.model" filterable allow-create clearable :loading="loadingModels"
                           :placeholder="I18N.t('cfg.modelPh')" style="flex: 1;">
                  <el-option v-for="m in modelOpts" :key="m" :value="m" :label="m" />
                </el-select>
                <el-button :loading="loadingModels" @click="fetchModels">{{ I18N.t('cfg.fetchModels') }}</el-button>
              </div>
              <div class="hint">{{ I18N.t('cfg.modelHint') }}</div>
            </el-form-item>
            <el-form-item :label="I18N.t('cfg.visionOpt')">
              <el-select v-model="f.supports_vision" style="width: 100%">
                <el-option value="yes" :label="I18N.t('cfg.visionYes')" />
                <el-option value="no" :label="I18N.t('cfg.visionNo')" />
              </el-select>
            </el-form-item>
          </template>
          <template v-else>
            <el-form-item :label="I18N.t('cfg.model')">
              <div class="hint">{{ I18N.t('cfg.dtModelHint') }}</div>
            </el-form-item>
            <el-form-item :label="I18N.t('cfg.maxSideOpt')">
              <el-select v-model="f.max_side" style="width: 220px;">
                <el-option :value="0" :label="I18N.t('cfg.maxSideNoneOpt')" />
                <el-option :value="512" label="512" />
                <el-option :value="768" label="768" />
                <el-option :value="1024" label="1024" />
              </el-select>
              <div class="hint">{{ I18N.t('cfg.maxSideHint') }}</div>
            </el-form-item>
            <el-form-item :label="I18N.t('cfg.maxFrames')">
              <el-input-number v-model="f.max_frames" :min="0" :max="2048" :step="1" controls-position="right" style="width: 110px;" />
              <div class="hint">{{ I18N.t('cfg.maxFramesHint') }}</div>
            </el-form-item>
          </template>
        </el-form>
        <template #footer>
          <el-button @click="dlg = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" :loading="saving" @click="save">{{ I18N.t('common.save') }}</el-button>
        </template>
      </el-dialog>
    </div>
  `,
  setup() {
    const route = router.currentRoute;
    const ctype = ref(route.value.query.ctype === 'drawthings' ? 'drawthings' : 'llm');
    const items = ref([]);
    const llmCount = ref(0);
    const dtCount = ref(0);

    const dlg = ref(false);
    const saving = ref(false);
    const editId = ref('');          // 非空 = 编辑模式（值为配置 id）
    const modelOpts = ref([]);      // 从 /models 拉取的模型 id 列表
    const loadingModels = ref(false);
    const f = reactive({
      config_type: 'llm', name: '', base_url: '', api_key: '', model: '',
      supports_vision: 'yes',
      max_side: 0, max_frames: 0,
    });

    async function load() {
      try {
        const data = await API.get('/api/configs?ctype=' + ctype.value);
        items.value = data.items;
        llmCount.value = data.llm_count;
        dtCount.value = data.drawthing_count;
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    function tabChange() {
      router.replace({ query: ctype.value === 'llm' ? {} : { ctype: 'drawthings' } });
      load();
    }

    const urlPh = computed(() => {
      if (f.config_type === 'llm') return 'http://127.0.0.1:11434/v1';
      return 'http://127.0.0.1:7860';
    });
    const urlHint = computed(() => {
      if (f.config_type === 'llm') return I18N.t('cfg.urlHintLlm');
      return I18N.t('cfg.urlHintDt');
    });

    function openNew() {
      editId.value = '';
      Object.assign(f, {
        config_type: ctype.value === 'drawthings' ? 'drawthings' : 'llm',
        name: '', base_url: '', api_key: '', model: '', supports_vision: 'yes',
        max_side: 0, max_frames: 0,
      });
      modelOpts.value = [];
      dlg.value = true;
    }

    function openEdit(row) {
      editId.value = row.id;
      if (ctype.value === 'drawthings') {
        Object.assign(f, {
          config_type: 'drawthings', name: row.name, base_url: row.base_url,
          max_side: row.max_side || 0,
          max_frames: row.max_frames || 0,
        });
      } else {
        Object.assign(f, {
          config_type: 'llm', name: row.name, base_url: row.base_url,
          api_key: '', model: row.model, supports_vision: row.supports_vision,
        });
        modelOpts.value = row.model ? [row.model] : [];
      }
      dlg.value = true;
    }

    async function fetchModels() {
      if (!f.base_url) {
        ElementPlus.ElMessage.warning(I18N.t('cfg.urlRequired'));
        return;
      }
      loadingModels.value = true;
      try {
        let qs = '/api/llm/models?base_url=' + encodeURIComponent(f.base_url) +
                 '&api_key=' + encodeURIComponent(f.api_key || '');
        if (editId.value) qs += '&config_id=' + editId.value;  // 编辑时：Key 留空则用库里已存的
        const data = await API.get(qs);
        modelOpts.value = data.models;
        if (!data.models.length) ElementPlus.ElMessage.warning(I18N.t('cfg.noModels'));
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        loadingModels.value = false;
      }
    }

    async function save() {
      saving.value = true;
      try {
        if (editId.value) {
          await API.put(`/api/configs/${f.config_type}/${editId.value}`, { ...f });
        } else {
          await API.post('/api/configs', { ...f });
        }
        ElementPlus.ElMessage.success(I18N.t('cfg.saved'));
        dlg.value = false;
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        saving.value = false;
      }
    }

    async function del(row) {
      try {
        await API.post(`/api/configs/${ctype.value}/${row.id}/delete`);
        ElementPlus.ElMessage.success(I18N.t('cfg.deleted'));
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    const fmt = (s) => (s || '').slice(0, 19).replace('T', ' ');
    function dtMeta(r) {
      const p = [];
      p.push(I18N.t(r.max_side ? 'cfg.maxSide' : 'cfg.maxSideNone', r.max_side ? r.max_side : ''));
      if (r.max_frames) p.push(I18N.t('cfg.frames', r.max_frames));
      return p.join(' · ');
    }
    onMounted(load);
    return {
      ctype, items, llmCount, dtCount, dlg, saving, f, editId, modelOpts, loadingModels,
      urlPh, urlHint, load, tabChange, openNew, openEdit, fetchModels, save, del, fmt, dtMeta,
    };
  },
};
