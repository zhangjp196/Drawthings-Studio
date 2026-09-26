// 配置管理：基础配置 / LLM / DrawThings 三个分区（无 tab，同页展示）
// - 基础配置：语言、主题（本地偏好）+ 新建创作默认 LLM / DrawThings（存后端 /api/settings）
// - LLM 最多 3 个、DrawThings 最多 3 个（后端强制，前端达上限禁用「新建」）；卡片支持编辑/删除（引用检查在后端）
// - 新建/编辑弹框（动态字段，类型由所在分区决定，不再可选）
window.Views = window.Views || {};
Views.configs = {
  template: `
    <div class="page">
      <div class="list-toolbar">
        <h1 style="margin: 0; font-size: 22px;">{{ I18N.t('cfg.title') }}</h1>
      </div>

      <section class="cfg-sec" id="sec-basic">
        <div class="cfg-sec-head">
          <div class="cfg-sec-title">
            <span class="cfg-ico cfg-ico-basic">⚙</span>
            <h3>{{ I18N.t('cfg.secBasic') }}</h3>
            <span class="cfg-sec-sub muted">{{ I18N.t('cfg.secBasicSub') }}</span>
          </div>
        </div>
        <div class="cfg-sec-body">
          <el-form label-position="top">
            <el-row :gutter="16">
              <el-col :xs="24" :md="12">
                <el-form-item :label="I18N.t('cfg.lang')">
                  <el-radio-group v-model="lang" @change="setLang">
                    <el-radio-button value="zh">中文</el-radio-button>
                    <el-radio-button value="en">English</el-radio-button>
                  </el-radio-group>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :md="12">
                <el-form-item :label="I18N.t('cfg.themeOpt')">
                  <el-radio-group v-model="theme" @change="setTheme">
                    <el-radio-button value="light">☀ {{ I18N.t('theme.light') }}</el-radio-button>
                    <el-radio-button value="system">⚙ {{ I18N.t('theme.system') }}</el-radio-button>
                    <el-radio-button value="dark">☾ {{ I18N.t('theme.dark') }}</el-radio-button>
                  </el-radio-group>
                </el-form-item>
              </el-col>
            </el-row>
            <el-row :gutter="16">
              <el-col :xs="24" :md="12">
                <el-form-item :label="I18N.t('cfg.defLlm')">
                  <el-select v-model="s.default_llm_config_id" clearable style="width: 100%" :placeholder="I18N.t('cfg.defNone')">
                    <el-option v-for="c in llmItems" :key="c.id" :value="c.id"
                               :label="c.name + '（' + c.model + '）'" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :md="12">
                <el-form-item :label="I18N.t('cfg.defDt')">
                  <el-select v-model="s.default_dt_config_id" clearable style="width: 100%" :placeholder="I18N.t('cfg.defNone')">
                    <el-option v-for="c in dtItems" :key="c.id" :value="c.id" :label="c.name" />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>
            <div class="actions">
              <el-button type="primary" :loading="savingBasic" @click="saveBasic">{{ I18N.t('common.save') }}</el-button>
            </div>
          </el-form>
        </div>
      </section>

      <section class="cfg-sec" id="sec-llm">
        <div class="cfg-sec-head">
          <div class="cfg-sec-title">
            <span class="cfg-ico cfg-ico-llm">LLM</span>
            <h3>{{ I18N.t('cfg.secLlm') }}</h3>
            <span class="cfg-quota" :class="{ full: llmCount >= llmMax }">{{ llmCount }}/{{ llmMax }}</span>
            <span class="cfg-sec-sub muted">{{ I18N.t('cfg.secLlmSub') }}</span>
          </div>
          <el-button size="small" type="primary" :disabled="llmCount >= llmMax"
                     :title="llmCount >= llmMax ? I18N.t('cfg.full', llmMax) : ''"
                     @click="openNew('llm')">{{ I18N.t('cfg.new') }}</el-button>
        </div>
        <div class="cfg-sec-body">
          <div class="cfg-grid" v-if="llmItems.length">
            <div class="cfg-card" v-for="row in llmItems" :key="row.id">
              <div class="wc-top"><span class="cfg-name">{{ row.name }}</span></div>
              <div class="cfg-meta muted">
                {{ I18N.t('cfg.modelName', row.model) }} · {{ I18N.t('cfg.vision') }}<template v-if="row.thinking && row.thinking !== 'default'"> · {{ row.thinking === 'yes' ? I18N.t('cfg.thinkingYes') : I18N.t('cfg.thinkingNo') }}</template>
              </div>
              <div class="cfg-url" :title="row.base_url">{{ row.base_url }}</div>
              <div class="wc-meta muted">{{ I18N.t('cfg.created', fmt(row.created_at)) }}</div>
              <div class="wc-actions">
                <el-button size="small" @click="openEdit('llm', row)">{{ I18N.t('cfg.edit') }}</el-button>
                <el-popconfirm :title="I18N.t('cfg.delConfirm')" @confirm="del('llm', row)">
                  <template #reference><el-button size="small" type="danger" plain>{{ I18N.t('proj.delete') }}</el-button></template>
                </el-popconfirm>
              </div>
            </div>
          </div>
          <el-empty v-else :image-size="56" :description="I18N.t('cfg.empty', 'VLM')" />
        </div>
      </section>

      <section class="cfg-sec" id="sec-dt">
        <div class="cfg-sec-head">
          <div class="cfg-sec-title">
            <span class="cfg-ico cfg-ico-dt">DT</span>
            <h3>{{ I18N.t('cfg.secDt') }}</h3>
            <span class="cfg-quota" :class="{ full: dtCount >= dtMax }">{{ dtCount }}/{{ dtMax }}</span>
            <span class="cfg-sec-sub muted">{{ I18N.t('cfg.secDtSub') }}</span>
          </div>
          <el-button size="small" type="primary" :disabled="dtCount >= dtMax"
                     :title="dtCount >= dtMax ? I18N.t('cfg.full', dtMax) : ''"
                     @click="openNew('drawthings')">{{ I18N.t('cfg.new') }}</el-button>
        </div>
        <div class="cfg-sec-body">
          <div class="cfg-grid" v-if="dtItems.length">
            <div class="cfg-card" v-for="row in dtItems" :key="row.id">
              <div class="wc-top"><span class="cfg-name">{{ row.name }}</span></div>
              <div class="cfg-meta muted">{{ dtMeta(row) }}</div>
              <div class="cfg-url" :title="row.base_url">{{ row.base_url }}</div>
              <div class="wc-meta muted">{{ I18N.t('cfg.created', fmt(row.created_at)) }}</div>
              <div class="wc-actions">
                <el-button size="small" @click="openEdit('drawthings', row)">{{ I18N.t('cfg.edit') }}</el-button>
                <el-popconfirm :title="I18N.t('cfg.delConfirm')" @confirm="del('drawthings', row)">
                  <template #reference><el-button size="small" type="danger" plain>{{ I18N.t('proj.delete') }}</el-button></template>
                </el-popconfirm>
              </div>
            </div>
          </div>
          <el-empty v-else :image-size="56" :description="I18N.t('cfg.empty', 'DrawThings')" />
        </div>
      </section>

      <el-dialog v-model="dlg" :title="editId ? I18N.t('cfg.dlgEdit') : I18N.t('cfg.dlgNew')" width="580px">
        <el-form label-position="top">
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
            <el-form-item :label="I18N.t('cfg.thinking')">
              <el-select v-model="f.thinking" style="width: 100%">
                <el-option value="default" :label="I18N.t('cfg.thinkingDefault')" />
                <el-option value="yes" :label="I18N.t('cfg.thinkingYes')" />
                <el-option value="no" :label="I18N.t('cfg.thinkingNo')" />
              </el-select>
              <div class="hint">{{ I18N.t('cfg.thinkingHint') }}</div>
            </el-form-item>
            <el-form-item v-if="f.thinking !== 'default'" :label="I18N.t('cfg.thinkingParam')">
              <el-select v-model="f.thinking_param" style="width: 100%">
                <el-option value="auto" :label="I18N.t('cfg.thinkingParamAuto')" />
                <el-option value="reasoning_effort" :label="I18N.t('cfg.thinkingParamReasoning')" />
                <el-option value="enable_thinking" :label="I18N.t('cfg.thinkingParamEnable')" />
              </el-select>
              <div class="hint">{{ I18N.t('cfg.thinkingParamHint') }}</div>
            </el-form-item>
          </template>
          <template v-else>
            <el-form-item :label="I18N.t('cfg.refImageSec')">
              <el-checkbox v-model="f.ref_image" style="margin-right: 20px;">
                {{ I18N.t('cfg.refImage') }}
              </el-checkbox>
              <el-checkbox v-model="f.ref_video">
                {{ I18N.t('cfg.refVideo') }}
              </el-checkbox>
              <div class="hint" style="margin-top:2px;">{{ I18N.t('cfg.refModelHint') }}</div>
              <div class="hint">{{ I18N.t('cfg.refCrashHint') }}</div>
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
            <el-form-item :label="I18N.t('cfg.maxSeconds')">
              <el-input-number v-model="f.max_seconds" :min="0" :max="8" :step="1" controls-position="right" style="width: 110px;" />
              <div class="hint">{{ I18N.t('cfg.maxSecondsHint') }}</div>
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
    const llmItems = ref([]);
    const dtItems = ref([]);
    const llmCount = ref(0);
    const dtCount = ref(0);
    const llmMax = ref(3);
    const dtMax = ref(1);

    const dlg = ref(false);
    const saving = ref(false);
    const editId = ref('');          // 非空 = 编辑模式（值为配置 id）
    const modelOpts = ref([]);      // 从 /models 拉取的模型 id 列表
    const loadingModels = ref(false);
    const f = reactive({
      config_type: 'llm', name: '', base_url: '', api_key: '', model: '',
      thinking: 'default', thinking_param: 'auto',
      model_image: '', model_video: '', max_side: 0, max_seconds: 8,
      ref_image: false, ref_video: false,   // 勾选后才图生图 / 图生视频（默认不勾选 = 文生图 / 文生视频）
    });

    async function load() {
      try {
        const data = await API.get('/api/configs');
        llmItems.value = data.llm_items;
        dtItems.value = data.dt_items;
        llmCount.value = data.llm_count;
        dtCount.value = data.drawthing_count;
        llmMax.value = data.llm_max;
        dtMax.value = data.dt_max;
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    // 基础配置（语言/主题为本地偏好，不走后端；默认配置/默认参数存后端）
    const lang = ref(I18N.current());
    const theme = ref(Theme.current());
    // 顶栏也能切换语言/主题：订阅变更，保持本页单选组同步（不重建组件）
    I18N.onChange(v => { lang.value = v; });
    Theme.onChange(v => { theme.value = v; });
    const s = reactive({
      default_llm_config_id: '', default_dt_config_id: '',
    });
    const savingBasic = ref(false);

    function setLang(l) { I18N.set(l); }
    function setTheme(t) { Theme.set(t); }

    async function loadBasic() {
      try {
        Object.assign(s, await API.get('/api/settings'));
      } catch (e) { /* 拉取失败用默认值即可 */ }
    }

    async function saveBasic() {
      savingBasic.value = true;
      try {
        Object.assign(s, await API.put('/api/settings', { ...s }));
        ElementPlus.ElMessage.success(I18N.t('cfg.saved'));
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        savingBasic.value = false;
      }
    }

    // 深链 /configs?ctype=drawthings（来自项目/新建页）→ 滚动到 DrawThings 分区
    function scrollByQuery() {
      if (route.value.query.ctype === 'drawthings') {
        const el = document.getElementById('sec-dt');
        if (el) setTimeout(() => el.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60);
      }
    }
    watch(() => route.value.query.ctype, scrollByQuery);  // 已在配置页时点深链也能定位

    const urlPh = computed(() => {
      if (f.config_type === 'llm') return 'http://127.0.0.1:11434/v1';
      return '127.0.0.1:7859';
    });
    const urlHint = computed(() => {
      if (f.config_type === 'llm') return I18N.t('cfg.urlHintLlm');
      return I18N.t('cfg.urlHintGrpc');
    });

    function openNew(type) {
      editId.value = '';
      Object.assign(f, {
        config_type: type, name: '', base_url: '', api_key: '', model: '',
        thinking: 'default', thinking_param: 'auto',
        model_image: '', model_video: '', max_side: 0, max_seconds: 8,
        ref_image: false, ref_video: false,
      });
      modelOpts.value = [];
      dlg.value = true;
    }

    function openEdit(type, row) {
      editId.value = row.id;
      if (type === 'drawthings') {
        Object.assign(f, {
          config_type: 'drawthings', name: row.name, base_url: row.base_url,
          model_image: row.model_image || '', model_video: row.model_video || '',
          max_side: row.max_side || 0,
          max_seconds: row.max_seconds == null ? 8 : row.max_seconds,
          ref_image: !!row.ref_image, ref_video: !!row.ref_video,
        });
      } else {
        Object.assign(f, {
          config_type: 'llm', name: row.name, base_url: row.base_url,
          api_key: '', model: row.model,
          thinking: row.thinking || 'default',
          thinking_param: row.thinking_param || 'auto',
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

    async function del(ctype, row) {
      try {
        await API.post(`/api/configs/${ctype}/${row.id}/delete`);
        ElementPlus.ElMessage.success(I18N.t('cfg.deleted'));
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    const fmt = (s) => (s || '').slice(0, 19).replace('T', ' ');
    function dtMeta(r) {
      const p = [];
      if (r.model_image) p.push(I18N.t(r.ref_image ? 'cfg.dtMetaImageRef' : 'cfg.dtMetaImage', r.model_image));
      if (r.model_video) p.push(I18N.t(r.ref_video ? 'cfg.dtMetaVideoRef' : 'cfg.dtMetaVideo', r.model_video));
      p.push(I18N.t(r.max_side ? 'cfg.maxSide' : 'cfg.maxSideNone', r.max_side ? r.max_side : ''));
      p.push(r.max_seconds ? I18N.t('cfg.seconds', r.max_seconds) : I18N.t('cfg.secondsNone'));
      return p.join(' · ');
    }
    onMounted(() => { load(); loadBasic(); scrollByQuery(); });
    return {
      llmItems, dtItems, llmCount, dtCount, llmMax, dtMax, dlg, saving, f, editId, modelOpts, loadingModels,
      lang, theme, s, savingBasic, setLang, setTheme, saveBasic,
      urlPh, urlHint, load, openNew, openEdit, fetchModels, save, del, fmt, dtMeta,
    };
  },
};
