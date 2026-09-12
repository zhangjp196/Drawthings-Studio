// 微创作作品列表：卡片网格 + 分页 + 新建弹框 + 删除
window.Views = window.Views || {};
Views.micro = {
  template: `
    <div class="page">
      <div class="list-toolbar">
        <div class="list-head">
          <h1>✨ 微创作</h1>
          <span class="muted">共 {{ total }} 个</span>
        </div>
        <el-button type="primary" @click="openNew">＋ 新建作品</el-button>
      </div>
      <p class="hint" style="margin-top: 0;">作品 → 多会话 → 消息：作品可反复创作、独立会话互不串扰，生成配置随作品保存。</p>

      <div class="work-grid" v-if="works.length">
        <div class="work-card" v-for="w in works" :key="w.id">
          <div class="wc-top">
            <el-tag v-if="w.drawthings_config_id" size="small" type="primary" effect="light">生成</el-tag>
            <el-tag v-else size="small" type="info" effect="light">对话</el-tag>
            <el-link :underline="false" type="primary" style="flex: 1; min-width: 0;" @click="enter(w)">
              {{ w.title || '（未命名）' }}
            </el-link>
          </div>
          <div class="wc-meta muted">{{ w.session_count }} 个会话 · {{ w.updated_at.slice(0, 10) }}</div>
          <div class="wc-actions">
            <el-button size="small" type="primary" plain @click="enter(w)">进入</el-button>
            <el-popconfirm title="删除该作品？全部会话/消息与已生成的媒体将一并删除。" @confirm="del(w)">
              <template #reference><el-button size="small" type="danger" plain>删除</el-button></template>
            </el-popconfirm>
          </div>
        </div>
      </div>
      <el-empty v-else description="还没有作品。点击「＋ 新建作品」开始。" />

      <el-pagination v-if="totalPages > 1" class="pager" background layout="prev, pager, next"
                     :total="total" :page-size="10" :current-page="page" @current-change="load" />

      <el-dialog v-model="dlg" title="新建作品" width="540px">
        <el-form label-position="top">
          <el-form-item label="标题（可选）">
            <el-input v-model="f.title" maxlength="200" placeholder="留空自动取首条消息" />
          </el-form-item>
          <el-form-item label="LLM 配置" required>
            <el-select v-model="f.llm" placeholder="选择 LLM 配置" style="width: 100%">
              <el-option v-for="c in llms" :key="c.id" :value="c.id"
                         :label="c.name + '（' + c.model + (c.supports_vision === 'no' ? ' / 纯文本' : '') + '）'" />
              <el-option v-if="!llms.length" value="" label="（无 LLM 配置，请先创建）" />
            </el-select>
          </el-form-item>
          <el-form-item label="DrawThings 配置">
            <el-select v-model="f.dt" clearable style="width: 100%">
              <el-option value="" label="不选（纯对话，不出媒体）" />
              <el-option v-for="c in dts" :key="c.id" :value="c.id" :label="c.name" />
            </el-select>
            <div class="hint">产出类型（图像/视频）由 app 当前加载的模型自动判断，无需选择。</div>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="dlg = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="create">创建</el-button>
        </template>
      </el-dialog>
    </div>
  `,
  setup() {
    const works = ref([]);
    const llms = ref([]);
    const dts = ref([]);
    const total = ref(0);
    const totalPages = ref(1);
    const page = ref(1);
    const dlg = ref(false);
    const saving = ref(false);
    const f = reactive({ title: '', llm: '', dt: '' });

    async function load() {
      try {
        const data = await API.get('/api/micro?page=' + page.value);
        works.value = data.works;
        llms.value = data.llm_configs;
        dts.value = data.drawthing_configs;
        total.value = data.total;
        totalPages.value = data.total_pages;
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    function openNew() {
      Object.assign(f, { title: '', llm: '', dt: '' });
      dlg.value = true;
    }

    async function create() {
      if (!f.llm) { ElementPlus.ElMessage.warning('请选择 LLM 配置'); return; }
      saving.value = true;
      try {
        const data = await API.post('/api/micro', {
          title: f.title, llm_config_id: f.llm,
          drawthings_config_id: f.dt,
        });
        dlg.value = false;
        router.push('/micro/' + data.id);
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        saving.value = false;
      }
    }

    function enter(w) { router.push('/micro/' + w.id); }

    async function del(w) {
      try {
        await API.post('/api/micro/' + w.id + '/delete');
        ElementPlus.ElMessage.success('已删除');
        if (works.value.length === 1 && page.value > 1) page.value--;
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    onMounted(load);
    return {
      works, llms, dts, total, totalPages, page, dlg, saving, f,
      load, openNew, create, enter, del,
    };
  },
};
