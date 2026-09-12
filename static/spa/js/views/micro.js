// 微创作作品列表：卡片网格 + 分页 + 新建弹框 + 删除
window.Views = window.Views || {};
Views.micro = {
  template: `
    <div class="page">
      <div class="list-toolbar">
        <div class="list-head">
          <h1>{{ I18N.t('mc.title') }}</h1>
          <span class="muted">{{ I18N.t('mc.total', total) }}</span>
        </div>
        <el-button type="primary" @click="openNew">{{ I18N.t('mc.new') }}</el-button>
      </div>
      <p class="hint" style="margin-top: 0;">{{ I18N.t('mc.hint') }}</p>

      <div class="work-grid" v-if="works.length">
        <div class="work-card" v-for="w in works" :key="w.id">
          <div class="wc-top">
            <el-tag v-if="w.drawthings_config_id" size="small" type="primary" effect="light">{{ I18N.t('mc.tagGen') }}</el-tag>
            <el-tag v-else size="small" type="info" effect="light">{{ I18N.t('mc.tagChat') }}</el-tag>
            <el-link :underline="false" type="primary" style="flex: 1; min-width: 0;" @click="enter(w)">
              {{ w.title || I18N.t('common.unnamed') }}
            </el-link>
          </div>
          <div class="wc-meta muted">{{ I18N.t('mc.sessions', w.session_count) }} · {{ w.updated_at.slice(0, 10) }}</div>
          <div class="wc-actions">
            <el-button size="small" type="primary" plain @click="enter(w)">{{ I18N.t('mc.enter') }}</el-button>
            <el-popconfirm :title="I18N.t('mc.delConfirm')" @confirm="del(w)">
              <template #reference><el-button size="small" type="danger" plain>{{ I18N.t('proj.delete') }}</el-button></template>
            </el-popconfirm>
          </div>
        </div>
      </div>
      <el-empty v-else :description="I18N.t('mc.empty')" />

      <el-pagination v-if="totalPages > 1" class="pager" background layout="prev, pager, next"
                     :total="total" :page-size="10" :current-page="page" @current-change="load" />

      <el-dialog v-model="dlg" :title="I18N.t('mc.dlg')" width="540px">
        <el-form label-position="top">
          <el-form-item :label="I18N.t('mc.fTitle')">
            <el-input v-model="f.title" maxlength="200" :placeholder="I18N.t('mc.fTitlePh')" />
          </el-form-item>
          <el-form-item :label="I18N.t('mc.llm')" required>
            <el-select v-model="f.llm" :placeholder="I18N.t('mc.llmPh')" style="width: 100%">
              <el-option v-for="c in llms" :key="c.id" :value="c.id"
                         :label="c.name + '（' + c.model + (c.supports_vision === 'no' ? ' / ' + I18N.t('cfg.textOnly') : '') + '）'" />
              <el-option v-if="!llms.length" value="" :label="I18N.t('mc.llmNone')" />
            </el-select>
          </el-form-item>
          <el-form-item :label="I18N.t('mc.dt')">
            <el-select v-model="f.dt" clearable style="width: 100%">
              <el-option value="" :label="I18N.t('mc.dtNone')" />
              <el-option v-for="c in dts" :key="c.id" :value="c.id" :label="c.name" />
            </el-select>
            <div class="hint">{{ I18N.t('mc.dtHint') }}</div>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="dlg = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" :loading="saving" @click="create">{{ I18N.t('common.create') }}</el-button>
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
      if (!f.llm) { ElementPlus.ElMessage.warning(I18N.t('mc.llmRequired')); return; }
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
        ElementPlus.ElMessage.success(I18N.t('mc.deleted'));
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
