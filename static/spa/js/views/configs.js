// 配置管理：LLM / DrawThings 两个标签页 + 表格 + 删除（引用检查在后端）+ 新建弹框（动态字段）
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

      <el-table v-if="items.length" :data="items" style="width: 100%;">
        <el-table-column prop="name" label="名称" min-width="140">
          <template #default="{ row }"><b>{{ row.name }}</b></template>
        </el-table-column>
        <el-table-column prop="base_url" label="端点" min-width="200" class-name="muted" />
        <template v-if="ctype === 'llm'">
          <el-table-column prop="model" label="模型" min-width="140" class-name="muted" />
          <el-table-column label="图片输入" width="100" align="center">
            <template #default="{ row }">{{ row.supports_vision === 'yes' ? '✓ 支持' : '— 纯文本' }}</template>
          </el-table-column>
        </template>
        <template v-else>
          <el-table-column label="类型" width="90" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.media_type === 'image' ? 'primary' : 'success'" effect="light">
                {{ row.media_type === 'image' ? '图像' : '视频' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="protocol" label="协议" width="100" class-name="muted" />
        </template>
        <el-table-column label="创建时间" width="170" class-name="muted">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-popconfirm :title="'确定删除该配置？若仍有项目选用将无法运行。'" @confirm="del(row)">
              <template #reference><el-button size="small" type="danger" plain>删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else :description="'还没有 ' + (ctype === 'llm' ? 'LLM' : 'DrawThings') + ' 配置，点击「＋ 新建」添加。'" />

      <el-dialog v-model="dlg" title="新建配置" width="580px">
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
              <el-input v-model="f.api_key" placeholder="本地端点可留空（真实 API 请填写）" />
            </el-form-item>
            <el-form-item label="模型">
              <el-input v-model="f.model" placeholder="例如：qwen2.5:7b" />
            </el-form-item>
            <el-form-item label="图片输入">
              <el-select v-model="f.supports_vision" style="width: 100%">
                <el-option value="yes" label="支持图片输入（多模态，剧本阶段可参考上一帧/首图）" />
                <el-option value="no" label="纯文本（不支持图片输入）" />
              </el-select>
            </el-form-item>
          </template>
          <template v-else>
            <el-form-item label="模型类型">
              <el-select v-model="f.media_type" style="width: 100%">
                <el-option value="image" label="图像模型（用于漫画项目，连续生图）" />
                <el-option value="video" label="视频模型（用于短剧项目，连续出视频）" />
              </el-select>
            </el-form-item>
            <el-form-item label="API 协议">
              <el-select v-model="f.protocol" style="width: 100%" @change="syncUrl">
                <el-option value="http" label="HTTP（A1111 兼容，推荐，图/视频都走这个）" />
                <el-option value="grpc" label="gRPC（ImageGenerationService，7859 端口，TLS）" />
              </el-select>
            </el-form-item>
            <el-form-item label="模型（可选）">
              <el-input v-model="f.dt_model_name" placeholder="留空 = 用 app 里当前选中的模型" />
            </el-form-item>
            <el-form-item label="共享密钥（gRPC 可选）">
              <el-input v-model="f.dt_shared_secret" placeholder="app 设置了共享密钥才需要填" />
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
    const f = reactive({
      config_type: 'llm', name: '', base_url: '', api_key: '', model: '',
      supports_vision: 'yes', media_type: 'image', protocol: 'http',
      dt_model_name: '', dt_shared_secret: '',
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
      return f.protocol === 'grpc' ? '127.0.0.1:7859' : 'http://127.0.0.1:8888';
    });
    const urlHint = computed(() => {
      if (f.config_type === 'llm') return 'OpenAI 协议 URL（兼容 Ollama / vLLM / 云端 OpenAI）。';
      return f.protocol === 'grpc'
        ? 'gRPC 填 host:port（默认 7859，TLS 默认开）。'
        : 'HTTP 填完整 URL（端口以 Draw Things app 显示为准）。';
    });
    function syncUrl() { /* 提示随 protocol 联动（computed 自动响应） */ }

    function openNew() {
      Object.assign(f, {
        config_type: ctype.value === 'drawthings' ? 'drawthings' : 'llm',
        name: '', base_url: '', api_key: '', model: '', supports_vision: 'yes',
        media_type: 'image', protocol: 'http', dt_model_name: '', dt_shared_secret: '',
      });
      dlg.value = true;
    }

    async function save() {
      saving.value = true;
      try {
        await API.post('/api/configs', { ...f });
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
    onMounted(load);
    return {
      ctype, items, llmCount, dtCount, dlg, saving, f,
      urlPh, urlHint, syncUrl, load, tabChange, openNew, save, del, fmt,
    };
  },
};
