// 微创作作品页（外壳 / 编排）：页头 + 三个 tab（对话 / 作品 / 设置）。
// 三个 tab 的具体内容拆分为子组件：
//   - 对话 = micro-session-list（会话侧栏）+ micro-chat（消息流 + 输入 + SSE）
//   - 作品 = micro-works-gallery（媒体画廊：导出 / 批量删除）
//   - 设置 = 本组件内的作品选项表单（作用于全部会话）
// 本组件持有：作品数据、会话增删改（路由跳转）、作品选项、图片灯箱。
window.Views = window.Views || {};

Views.microWork = {
  components: {
    MicroSessionList: Views.microSessionList,
    MicroChat: Views.microChat,
    MicroWorksGallery: Views.microWorksGallery,
  },
  props: ['id', 'sid'],
  template: `
    <div class="page mc-page" v-if="data">
      <div class="mc-head">
        <div>
          <el-tag size="small" :type="tagType" effect="plain">{{ tagLabel }}</el-tag>
          <h1 class="ptitle">{{ data.work.title || I18N.t('mw.unnamedWork') }}</h1>
          <p class="meta muted">{{ I18N.t('p.metaLlm', data.work.llm_name) }} · {{ I18N.t('p.metaDt', data.work.dt_name || I18N.t('mw.dtNone')) }}</p>
        </div>
        <div class="proj-head-actions">
          <el-button @click="tab = 'settings'">{{ I18N.t('mw.options') }}</el-button>
          <el-button @click="router.push('/micro')">{{ I18N.t('p.back') }}</el-button>
          <el-popconfirm :title="I18N.t('mw.delWorkConfirm')" @confirm="delWork">
            <template #reference><el-button type="danger" plain>{{ I18N.t('mw.delWork') }}</el-button></template>
          </el-popconfirm>
        </div>
      </div>

      <el-tabs v-model="tab" class="mc-tabs">
      <el-tab-pane :label="I18N.t('mw.tabChat')" name="chat">
      <div class="mc-pane mc-pane-chat">
      <div class="mc-body" :class="{ 'side-hidden': sideHidden }">
        <button v-if="sideHidden" type="button" class="mc-expand" :title="I18N.t('mw.expandTitle')" @click="setSide(false)">{{ I18N.t('mw.expand') }}</button>
        <micro-session-list :sessions="data.sessions" :sid="sid" :collapsed="sideHidden"
                            @select="pick" @new="openSess" @rename="askRename" @delete="delSess" @collapse="setSide" />
        <micro-chat :id="id" :sid="sid" :vision="!!data.work.vision" :has-session="hasSession" :msgs="msgs"
                    @sent="load" @preview="openLb" />
      </div>
      </div>
      </el-tab-pane>

      <!-- ============ 作品（该作品下所有会话生成的媒体，图片 / 视频 分区：导出 + 多选批量删除）============ -->
      <el-tab-pane :label="I18N.t('mw.tabWorks')" name="works">
        <micro-works-gallery v-if="tab === 'works'" :id="id" @preview="openLb" @changed="load" />
      </el-tab-pane>

      <!-- ============ 设置（作品选项，作用于全部会话）============ -->
      <el-tab-pane :label="I18N.t('mw.tabSettings')" name="settings">
        <div class="mc-pane mc-pane-scroll">
        <el-card shadow="never" style="max-width: 640px">
          <p class="hint">{{ I18N.t('mw.cfgHint') }}</p>
          <el-form label-position="top">
            <el-form-item :label="I18N.t('mc.fTitle')">
              <el-input v-model="cfg.title" maxlength="200" :placeholder="I18N.t('mc.fTitlePh')" />
            </el-form-item>
            <el-form-item :label="I18N.t('mc.llm')">
              <el-select v-model="cfg.llm" style="width: 100%">
                <el-option v-for="c in data.llm_configs" :key="c.id" :value="c.id"
                           :label="c.name + '（' + c.model + '）'" />
              </el-select>
            </el-form-item>
            <el-form-item :label="I18N.t('mc.dt')">
              <el-select v-model="cfg.dt" clearable style="width: 100%">
                <el-option value="" :label="I18N.t('mw.dtNone')" />
                <el-option v-for="c in data.drawthing_configs" :key="c.id" :value="c.id" :label="c.name" />
              </el-select>
              <div class="hint">{{ I18N.t('mc.dtHint') }}</div>
            </el-form-item>
            <el-form-item v-if="cfg.dt" :label="I18N.t('mc.dtModelImage')">
              <el-select v-model="cfg.mi" filterable allow-create clearable style="width: 100%"
                         :placeholder="I18N.t('cf.dtModelPh')">
                <el-option :value="''" :label="I18N.t('cf.dtModelFollow')" />
                <el-option v-for="m in imgChoices" :key="m.file" :value="m.file" :label="m.label" />
              </el-select>
              <el-checkbox v-model="cfg.ref_i" style="margin-top:4px;">{{ I18N.t('cfg.refImage') }}</el-checkbox>
            </el-form-item>
            <el-form-item v-if="cfg.dt" :label="I18N.t('mc.dtModelVideo')">
              <el-select v-model="cfg.mv" filterable allow-create clearable style="width: 100%"
                         :placeholder="I18N.t('cf.dtModelPh')">
                <el-option :value="''" :label="I18N.t('cf.dtModelFollow')" />
                <el-option v-for="m in vidChoices" :key="m.file" :value="m.file" :label="m.label" />
              </el-select>
              <el-checkbox v-model="cfg.ref_v" style="margin-top:4px;">{{ I18N.t('cfg.refVideo') }}</el-checkbox>
              <div class="hint">{{ I18N.t('cfg.refCrashHint') }}</div>
            </el-form-item>
          </el-form>
          <div class="actions">
            <el-button type="primary" :loading="cfgBusy" @click="saveCfg">{{ I18N.t('common.save') }}</el-button>
          </div>
        </el-card>
        </div>
      </el-tab-pane>
      </el-tabs>

      <el-dialog v-model="sessDlg" :title="I18N.t('mw.dlgSess')" width="440px">
        <el-input v-model="sessTitle" :placeholder="I18N.t('mw.sessTitlePh')" maxlength="200" />
        <template #footer>
          <el-button @click="sessDlg = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" @click="createSess">{{ I18N.t('common.create') }}</el-button>
        </template>
      </el-dialog>

      <el-dialog v-model="renameDlg" :title="I18N.t('mw.dlgRename')" width="440px">
        <el-input v-model="renameTitle" maxlength="200" />
        <template #footer>
          <el-button @click="renameDlg = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" @click="doRename">{{ I18N.t('common.save') }}</el-button>
        </template>
      </el-dialog>

      <el-image-viewer v-if="lb.show" :url-list="lb.list" :initial-index="lb.idx" @close="lb.show = false" />
    </div>
    <div v-else class="page loading"><el-skeleton :rows="8" animated /></div>
  `,
  setup(props) {
    const data = ref(null);
    const msgs = ref([]);
    const hasSession = ref(false);
    const tab = ref('chat');  // 作品详情页 tab：对话（默认）/ 作品 / 设置

    // 侧栏折叠（按作品记忆）
    const sideHidden = ref(false);
    const sideKey = computed(() => 'mc_side_' + props.id);
    function setSide(hidden) {
      sideHidden.value = hidden;
      try { localStorage.setItem(sideKey.value, hidden ? '1' : '0'); } catch (e) {}
    }

    // 会话操作（增删改 + 路由跳转）
    const sessDlg = ref(false);
    const sessTitle = ref('');
    function openSess() {
      sessTitle.value = '';
      sessDlg.value = true;
    }
    const renameDlg = ref(false);
    const renameTitle = ref('');
    const renameTarget = ref(null);

    // 作品选项（设置在「设置」tab 内，作用于全部会话）
    const cfgBusy = ref(false);
    const cfg = reactive({ title: '', llm: '', dt: '', mi: '', mv: '', ref_i: false, ref_v: false });
    // 功能级模型：按所选 DrawThings 配置的端点拉取 app 已下载模型（图像 / 视频分开列）
    const dtModels = ref([]);
    const imgChoices = computed(() => dtModels.value
      .filter(m => m.file && !m.video)
      .map(m => ({ file: m.file, label: m.file + (m.name ? ' · ' + m.name : '') })));
    const vidChoices = computed(() => dtModels.value
      .filter(m => m.file && m.video)
      .map(m => ({ file: m.file, label: m.file + (m.name ? ' · ' + m.name : '') })));
    async function fetchModels() {
      const c = (data.value && data.value.drawthing_configs || []).find(x => x.id === cfg.dt);
      if (!c || !c.base_url) { dtModels.value = []; return; }
      try {
        const r = await API.get('/api/dt-models?base_url=' + encodeURIComponent(c.base_url));
        dtModels.value = r.models || [];
      } catch (e) {
        dtModels.value = [];  // app 未开 gRPC 不阻塞：可手动输入模型文件名
      }
    }
    function syncCfg() {
      const w = data.value.work;
      cfg.title = w.title;
      cfg.llm = w.llm_config_id;
      cfg.dt = w.drawthings_config_id || '';
      const c = (data.value.drawthing_configs || []).find(x => x.id === cfg.dt);
      // 作品级未显式选模型时预填配置里的；参考图开关：作品显式值优先，否则跟随配置
      cfg.mi = w.dt_model_image || (c ? (c.model_image || '') : '');
      cfg.mv = w.dt_model_video || (c ? (c.model_video || '') : '');
      cfg.ref_i = (w.dt_ref_image === '1') || (w.dt_ref_image === '' && !!c && !!c.ref_image);
      cfg.ref_v = (w.dt_ref_video === '1') || (w.dt_ref_video === '' && !!c && !!c.ref_video);
      fetchModels();
    }
    watch(() => cfg.dt, (id) => {
      const w = data.value.work;
      const c = (data.value.drawthing_configs || []).find(x => x.id === id);
      cfg.mi = w.dt_model_image || (c ? (c.model_image || '') : '');
      cfg.mv = w.dt_model_video || (c ? (c.model_video || '') : '');
      cfg.ref_i = (w.dt_ref_image === '1') || (w.dt_ref_image === '' && !!c && !!c.ref_image);
      cfg.ref_v = (w.dt_ref_video === '1') || (w.dt_ref_video === '' && !!c && !!c.ref_video);
      fetchModels();
    });

    // 图片放大（灯箱）：子组件 emit('preview', list, idx)
    const lb = reactive({ show: false, list: [], idx: 0 });
    function openLb(list, idx) {
      lb.list = list;
      lb.idx = idx;
      lb.show = true;
    }

    // 持久化消息 -> 有序内容块（旧数据无 parts：文本在前、媒体在后，保持可读）
    function normalizeMsg(m) {
      if (m.role !== 'assistant') return m;
      let blocks = [];
      if (Array.isArray(m.parts) && m.parts.length) {
        blocks = m.parts;
      } else {
        if (m.content) blocks.push({ type: 'text', text: m.content });
        if (m.media_url) blocks.push({ type: 'media', url: m.media_url, prompt: m.prompt || '' });
      }
      return Object.assign({}, m, { blocks });
    }

    const tagLabel = computed(() => {
      if (!data.value) return '';
      return data.value.work.dt_name ? I18N.t('mw.tagGen') : I18N.t('mw.tagChat');
    });
    const tagType = computed(() => tagLabel.value === I18N.t('mw.tagChat') ? 'info' : 'primary');

    async function load() {
      try {
        if (props.sid) {
          data.value = await API.get(`/api/micro/${props.id}/${props.sid}`);
          msgs.value = (data.value.messages || []).map(normalizeMsg);
          hasSession.value = true;
          syncCfg();
        } else {
          data.value = await API.get(`/api/micro/${props.id}`);
          msgs.value = [];
          hasSession.value = false;
          syncCfg();
          if (data.value.sessions.length) {
            router.replace(`/micro/${props.id}/${data.value.sessions[0].id}`);
            return;
          }
        }
        try { sideHidden.value = localStorage.getItem(sideKey.value) === '1'; } catch (e) {}
      } catch (e) {
        if (e.status === 404) router.replace('/micro');
        ElementPlus.ElMessage.error(e.message);
      }
    }

    function pick(sid) {
      if (sid === props.sid) return;
      router.push(`/micro/${props.id}/${sid}`);
    }

    async function createSess() {
      try {
        const r = await API.post(`/api/micro/${props.id}/sessions`, { title: sessTitle.value });
        sessDlg.value = false;
        sessTitle.value = '';
        router.push(`/micro/${props.id}/${r.id}`);
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    function askRename(s) {
      renameTarget.value = s;
      renameTitle.value = s.title;
      renameDlg.value = true;
    }
    async function doRename() {
      try {
        await API.post(`/api/micro/${props.id}/${renameTarget.value.id}/rename`, { title: renameTitle.value });
        renameDlg.value = false;
        ElementPlus.ElMessage.success(I18N.t('mw.renamed'));
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }
    async function delSess(s) {
      try {
        await API.post(`/api/micro/${props.id}/${s.id}/delete`);
        ElementPlus.ElMessage.success(I18N.t('mw.sessDeleted'));
        if (s.id === props.sid) {
          const rest = data.value.sessions.filter(x => x.id !== s.id);
          if (rest.length) router.replace(`/micro/${props.id}/${rest[0].id}`);
          else load();
        } else {
          load();
        }
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    async function saveCfg() {
      cfgBusy.value = true;
      try {
        await API.post(`/api/micro/${props.id}/settings`, {
          title: cfg.title, llm_config_id: cfg.llm,
          drawthings_config_id: cfg.dt,
          dt_model_image: cfg.mi, dt_model_video: cfg.mv,
          dt_ref_image: cfg.ref_i ? 1 : 0, dt_ref_video: cfg.ref_v ? 1 : 0,
        });
        ElementPlus.ElMessage.success(I18N.t('mw.cfgSaved'));
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        cfgBusy.value = false;
      }
    }

    async function delWork() {
      try {
        await API.post(`/api/micro/${props.id}/delete`);
        ElementPlus.ElMessage.success(I18N.t('mw.workDeleted'));
        router.push('/micro');
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    // 切换作品 / 会话：重新加载数据（消息流由 micro-chat 自身在 sid 变化时重置）
    watch(() => [props.id, props.sid], () => {
      load();
    });
    onMounted(load);

    return {
      data, msgs, hasSession, sideHidden, setSide, tab,
      sessDlg, sessTitle, createSess, renameDlg, renameTitle, askRename, doRename, delSess,
      cfgBusy, cfg, imgChoices, vidChoices, saveCfg, delWork,
      lb, openLb, tagLabel, tagType,
      router, pick, openSess, load,
    };
  },
};
