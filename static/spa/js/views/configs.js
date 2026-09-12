// 配置管理：LLM / DrawThings 两个标签页 + 卡片列表（编辑/删除，引用检查在后端）+ 新建/编辑弹框（动态字段）
window.Views = window.Views || {};
Views.configs = {
  template: `
    <div class="page">
      <div class="list-toolbar">
        <h1 style="margin: 0; font-size: 22px;">配置管理</h1>
        <el-button type="primary" @click="openNew">＋ 新建</el-button>
      </div>
      <p class="hint" style="margin-top: 0;">配置存于本地 SQLite，可在不同项目/作品间复用。</p>

      <el-tabs v-model="ctype" @tab-change="tabChange">
        <el-tab-pane :label="'LLM 配置（' + llmCount + '）'" name="llm" />
        <el-tab-pane :label="'DrawThings 配置（' + dtCount + '）'" name="drawthings" />
      </el-tabs>

      <div class="cfg-grid" v-if="items.length">
        <div class="cfg-card" v-for="row in items" :key="row.id">
          <div class="wc-top">
            <el-tag v-if="ctype === 'llm'" size="small" type="primary" effect="light">LLM</el-tag>
            <el-tag v-else size="small" type="primary" effect="light">DrawThings</el-tag>
            <span class="cfg-name">{{ row.name }}</span>
          </div>
          <div class="cfg-meta muted" v-if="ctype === 'llm'">
            模型 {{ row.model }} · {{ row.supports_vision === 'yes' ? '✓ 支持图片输入' : '纯文本' }}
          </div>
          <div class="cfg-meta muted" v-else>{{ dtMeta(row) }}</div>
          <div class="cfg-url" :title="row.base_url">{{ row.base_url }}</div>
          <div class="wc-meta muted">创建于 {{ fmt(row.created_at) }}</div>
          <div class="wc-actions">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-popconfirm :title="'确定删除该配置？若仍有项目选用将无法运行。'" @confirm="del(row)">
              <template #reference><el-button size="small" type="danger" plain>删除</el-button></template>
            </el-popconfirm>
          </div>
        </div>
      </div>
      <el-empty v-else :description="'还没有 ' + (ctype === 'llm' ? 'LLM' : 'DrawThings') + ' 配置，点击「＋ 新建」添加。'" />

      <el-dialog v-model="dlg" :title="editId ? '编辑配置' : '新建配置'" width="580px">
        <el-form label-position="top">
          <el-form-item label="配置类型">
            <el-select v-model="f.config_type" style="width: 100%">
              <el-option value="llm" label="LLM 配置（OpenAI 协议）" />
              <el-option value="drawthings" label="DrawThings 配置" />
            </el-select>
          </el-form-item>
          <el-form-item label="名称">
            <el-input v-model="f.name" placeholder="例如：本地 Ollama / 我的 DrawThings" />
          </el-form-item>
          <el-form-item label="端点地址">
            <el-input v-model="f.base_url" :placeholder="urlPh" />
            <div class="hint">{{ urlHint }}</div>
          </el-form-item>
          <template v-if="f.config_type === 'llm'">
            <el-form-item label="API Key">
              <el-input v-model="f.api_key" :placeholder="editId ? '留空 = 保持原 Key 不变' : '本地端点可留空（真实 API 请填写）'" />
            </el-form-item>
            <el-form-item label="模型">
              <div style="display: flex; gap: 8px; width: 100%;">
                <el-select v-model="f.model" filterable allow-create clearable :loading="loadingModels"
                           placeholder="选择或输入模型 id" style="flex: 1;">
                  <el-option v-for="m in modelOpts" :key="m" :value="m" :label="m" />
                </el-select>
                <el-button :loading="loadingModels" @click="fetchModels">获取模型</el-button>
              </div>
              <div class="hint">点击「获取模型」从端点 /models 自动拉取（OpenAI 协议）；也可手动输入。编辑时 Key 留空 = 用已存 Key。</div>
            </el-form-item>
            <el-form-item label="图片输入">
              <el-select v-model="f.supports_vision" style="width: 100%">
                <el-option value="yes" label="支持图片输入（多模态，剧本阶段可参考上一帧/首图）" />
                <el-option value="no" label="纯文本（不支持图片输入）" />
              </el-select>
            </el-form-item>
          </template>
          <template v-else>
            <el-form-item label="模型">
              <div class="hint">跟随 app 里当前选中的模型（API 不支持指定模型）。</div>
            </el-form-item>
            <el-form-item label="最大分辨率（仅最长边，可选）">
              <el-select v-model="f.max_side" style="width: 220px;">
                <el-option :value="0" label="不限（跟随 app）" />
                <el-option :value="512" label="512" />
                <el-option :value="768" label="768" />
                <el-option :value="1024" label="1024" />
              </el-select>
              <div class="hint">具体分辨率由智能体按场景决定，最长边受此上限约束；「不限」= 跟随 app 当前值。</div>
            </el-form-item>
            <el-form-item label="最大帧数上限（视频，可选）">
              <el-input-number v-model="f.max_frames" :min="0" :max="2048" :step="1" controls-position="right" style="width: 110px;" />
              <div class="hint">视频帧数上限：实际帧数 = min(app 当前帧数, 此值)；0 = 不设上限</div>
            </el-form-item>
          </template>
        </el-form>
        <template #footer>
          <el-button @click="dlg = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
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
      if (f.config_type === 'llm') return 'OpenAI 协议 URL（兼容 Ollama / vLLM / 云端 OpenAI）。';
      return 'HTTP 端点 URL（端口以 Draw Things app 显示为准，如 http://127.0.0.1:7860）。';
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
        ElementPlus.ElMessage.warning('请先填写端点地址');
        return;
      }
      loadingModels.value = true;
      try {
        let qs = '/api/llm/models?base_url=' + encodeURIComponent(f.base_url) +
                 '&api_key=' + encodeURIComponent(f.api_key || '');
        if (editId.value) qs += '&config_id=' + editId.value;  // 编辑时：Key 留空则用库里已存的
        const data = await API.get(qs);
        modelOpts.value = data.models;
        if (!data.models.length) ElementPlus.ElMessage.warning('该端点未返回可用模型');
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
        ElementPlus.ElMessage.success('已保存');
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
        ElementPlus.ElMessage.success('已删除');
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    const fmt = (s) => (s || '').slice(0, 19).replace('T', ' ');
    function dtMeta(r) {
      const p = [];
      p.push('最大分辨率 ' + (r.max_side ? r.max_side + '（最长边）' : '不限'));
      if (r.max_frames) p.push('帧数上限 ' + r.max_frames);
      return p.join(' · ');
    }
    onMounted(load);
    return {
      ctype, items, llmCount, dtCount, dlg, saving, f, editId, modelOpts, loadingModels,
      urlPh, urlHint, load, tabChange, openNew, openEdit, fetchModels, save, del, fmt, dtMeta,
    };
  },
};
