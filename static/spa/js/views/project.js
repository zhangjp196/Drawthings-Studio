// 项目详情：头部/封面 + 三个页签（企划 / 章节 / 完成）
// 大纲：整体大纲 + 风格 + 角色设定 + 默认分辨率 + 章节规划（数量 + 每章标题/摘要），可保存/重新生成
// 章节：一键生成（剧本/画面）+ 手风琴卡片（多步）+ 返回大纲
// 完成：预览 + 导出 ZIP/PDF + 标记完成
window.Views = window.Views || {};
Views.project = {
  props: ['id'],
  components: { 'first-image': Views.firstImage, 'chapter-card': Views.chapterCard },
  template: `
    <div class="page" v-if="data">
      <div class="proj-head">
        <div>
          <el-tag size="small" :type="data.project.kind === 'comic' ? 'primary' : 'success'" effect="light">
            {{ data.project.kind === 'comic' ? I18N.t('proj.comic') : I18N.t('proj.drama') }}
          </el-tag>
          <h1 class="ptitle">{{ data.project.title || data.project.origin }}</h1>
          <p class="meta muted">{{ I18N.t('p.metaIdea', data.project.origin) }}
            · {{ I18N.t('p.metaLength', data.chapters.length) }}
            · {{ I18N.t('p.metaStyle', scope.style || '—') }}
            · {{ I18N.t('p.metaLlm', data.project.llm_name) }}
            · {{ I18N.t('p.metaDt', data.project.dt_name) }}</p>
        </div>
        <div class="proj-head-actions">
          <el-button @click="openCfg">{{ I18N.t('p.settings') }}</el-button>
          <el-button @click="openReset">{{ I18N.t('p.reset') }}</el-button>
          <el-button @click="router.push('/projects')">{{ I18N.t('p.back') }}</el-button>
          <el-popconfirm :title="I18N.t('proj.delConfirm')" @confirm="del">
            <template #reference><el-button type="danger" plain>{{ I18N.t('p.del') }}</el-button></template>
          </el-popconfirm>
        </div>
      </div>

      <el-tabs v-model="tab" class="proj-tabs">
        <!-- ============ 大纲（内部纵向子页签：故事大纲 / 角色设定 / 章节规划 / 封面）============ -->
        <el-tab-pane :label="I18N.t('p.tabOutline')" name="outline">
          <div class="subtabs">
            <nav class="subtabs-nav">
              <button type="button" class="subtabs-item" :class="{ active: oSub === 'story' }" @click="oSub = 'story'">{{ I18N.t('p.subStory') }}</button>
              <button type="button" class="subtabs-item" :class="{ active: oSub === 'chars' }" @click="oSub = 'chars'">{{ I18N.t('p.subChars') }}</button>
              <button type="button" class="subtabs-item" :class="{ active: oSub === 'plan' }" @click="oSub = 'plan'">{{ I18N.t('p.subPlan') }}</button>
              <button type="button" class="subtabs-item" :class="{ active: oSub === 'cover' }" @click="oSub = 'cover'">{{ I18N.t('p.subCover') }}</button>
            </nav>
            <div class="subtabs-body">
              <el-card v-if="oSub === 'story'" shadow="never">
                <el-form label-position="top">
                  <el-form-item :label="I18N.t('p.outStyle')">
                    <el-input v-model="oStyle" :placeholder="scope.style || ''" />
                  </el-form-item>
                  <el-form-item :label="I18N.t('p.arc')">
                    <el-input v-model="arcText" type="textarea" :rows="8" />
                  </el-form-item>
                  <el-form-item :label="I18N.t('p.outRes')">
                    <div class="res-row">
                      <el-input-number v-model="oW" :min="0" :max="4096" :step="64" size="small" />
                      <span>×</span>
                      <el-input-number v-model="oH" :min="0" :max="4096" :step="64" size="small" />
                      <span class="muted small" style="margin-left:6px;">{{ I18N.t('p.outResHint') }}</span>
                    </div>
                  </el-form-item>
                  <el-form-item :label="I18N.t('p.globalPrompt')">
                    <el-input v-model="oGlobal" type="textarea" :rows="4" :placeholder="I18N.t('p.globalPromptHint')" />
                  </el-form-item>
                </el-form>
                <div class="actions">
                  <el-popconfirm :title="I18N.t('p.outlineConfirm')" @confirm="genOutline">
                    <template #reference><el-button type="primary" :loading="actBusy">{{ I18N.t('p.confirmOutline') }}</el-button></template>
                  </el-popconfirm>
                  <el-popconfirm :title="I18N.t('p.saveOutlineConfirm')" @confirm="saveOutline">
                    <template #reference><el-button type="primary" :loading="busySave">{{ I18N.t('p.outSave') }}</el-button></template>
                  </el-popconfirm>
                  <span class="muted" v-if="actBusy || busySave" style="margin-left:10px;">{{ progress.text || I18N.t('p.busy') }}</span>
                </div>
              </el-card>
              <el-card v-else-if="oSub === 'chars'" shadow="never">
                <el-form label-position="top">
                  <el-form-item :label="I18N.t('p.outChars')">
                    <el-input v-model="oChars" type="textarea" :rows="10" />
                  </el-form-item>
                </el-form>
                <div class="actions">
                  <el-popconfirm :title="I18N.t('p.saveOutlineConfirm')" @confirm="saveOutline">
                    <template #reference><el-button type="primary" :loading="busySave">{{ I18N.t('p.outSave') }}</el-button></template>
                  </el-popconfirm>
                </div>
              </el-card>
              <el-card v-else-if="oSub === 'plan'" shadow="never">
                <el-form label-position="top">
                  <el-form-item :label="I18N.t('p.countMode')">
                    <div class="res-row">
                      <el-radio-group v-model="cMode" size="small">
                        <el-radio-button value="auto">{{ I18N.t('p.countAuto') }}</el-radio-button>
                        <el-radio-button value="range">{{ I18N.t('p.countRange') }}</el-radio-button>
                      </el-radio-group>
                      <template v-if="cMode === 'range'">
                        <el-input-number v-model="cMin" :min="1" :max="60" size="small" style="width:96px" />
                        <span>~</span>
                        <el-input-number v-model="cMax" :min="1" :max="60" size="small" style="width:96px" />
                      </template>
                    </div>
                  </el-form-item>
                </el-form>
                <div class="row-between" style="margin-bottom:8px;">
                  <b class="muted small">{{ I18N.t('p.chPlan') }}（{{ data.chapters.length }}）</b>
                  <el-button size="small" @click="addChapter">{{ I18N.t('p.addChapter') }}</el-button>
                </div>
                <div v-for="(c, i) in data.chapters" :key="'pl' + c.index" class="plan-row">
                  <el-input v-model="c.title" size="small" :placeholder="I18N.t('p.chPlanTitle')" style="width:180px" />
                  <el-input v-model="c.summary" size="small" type="textarea" :rows="2" :placeholder="I18N.t('p.chPlanSummary')" />
                  <el-button size="small" type="danger" plain @click="delChapter(i)">{{ I18N.t('p.chDelete') }}</el-button>
                </div>
                <el-empty v-if="!data.chapters.length" :description="I18N.t('p.chPlanEmpty')" :image-size="48" />
                <div class="actions">
                  <el-popconfirm :title="I18N.t('p.chRegenConfirm')" @confirm="planChapters">
                    <template #reference><el-button :loading="actBusy">{{ I18N.t('p.planChapters') }}</el-button></template>
                  </el-popconfirm>
                  <el-popconfirm :title="I18N.t('p.saveOutlineConfirm')" @confirm="saveOutline">
                    <template #reference><el-button type="primary" :loading="busySave">{{ I18N.t('p.outSave') }}</el-button></template>
                  </el-popconfirm>
                  <span class="muted" v-if="actBusy || busySave" style="margin-left:10px;">{{ progress.text || I18N.t('p.busy') }}</span>
                </div>
              </el-card>
              <first-image v-else :project="data.project" :project-id="data.project.id"
                           @preview="openLb([$event], 0)" @reloaded="load" />
            </div>
          </div>
        </el-tab-pane>

        <!-- ============ 章节 ============ -->
        <el-tab-pane :label="I18N.t('p.tabChapters')" name="chapters" :disabled="!data.chapters.length">
          <div class="actions mb8">
            <el-button size="small" @click="tab = 'outline'">{{ I18N.t('p.toOutline') }}</el-button>
            <el-checkbox :model-value="allSelected" @change="toggleAllSelect">{{ I18N.t('p.selAll') }}</el-checkbox>
            <el-popconfirm :title="I18N.t('p.genAllConfirm')" @confirm="genAll">
              <template #reference>
                <el-button size="small" type="primary" :loading="busyGenAll" :disabled="!data.chapters.length">
                  {{ selected.length ? I18N.t('p.genAllSel', selected.length) : I18N.t('p.genAll') }}
                </el-button>
              </template>
            </el-popconfirm>
            <span class="muted small" v-if="progress.text">{{ progress.text }}</span>
          </div>
          <div class="muted small mb8" v-if="data.chapters.length">
            {{ I18N.t('p.progress', doneCount, data.chapters.length) }}
          </div>
          <div class="md-layout">
            <div class="md-left">
              <div class="md-list">
                <div v-for="(c, pi) in pageChapters" :key="'md' + c.index" class="md-item"
                     :class="{ active: (pageStart + pi) === cur }" @click="cur = pageStart + pi">
                  <el-checkbox :model-value="isSel(pageStart + pi)" @click.stop @change="toggleSelect(pageStart + pi)" class="md-sel" />
                  <span class="md-idx">{{ c.index + 1 }}</span>
                  <span class="md-title">{{ c.title || I18N.t('p.ch', c.index + 1) }}</span>
                  <el-tag size="small" :type="c.status === 'done' ? 'success' : (c.status === 'error' ? 'danger' : 'info')"
                          effect="light">{{ I18N.t('p.chStatus.' + c.status) || c.status }}</el-tag>
                </div>
              </div>
              <div class="md-pager" v-if="totalPages > 1">
                <el-button size="small" :disabled="page === 1" @click="page--">‹</el-button>
                <span class="muted small">{{ page }} / {{ totalPages }}</span>
                <el-button size="small" :disabled="page === totalPages" @click="page++">›</el-button>
              </div>
            </div>
            <div class="md-detail">
              <chapter-card :key="curCh.index" v-if="curCh" :chapter="curCh" :kind="data.project.kind" :project-id="data.project.id"
                             :expanded="true" :no-toggle="true"
                             :is-first="curCh.index === 0" :is-last="curCh.index === data.chapters.length - 1"
                             @preview="openLb([$event], 0)" @reloaded="onChapterReloaded" />
              <el-empty v-else :description="I18N.t('p.chPlanEmpty')" :image-size="54" />
            </div>
          </div>
        </el-tab-pane>

        <!-- ============ 完成 ============ -->
        <el-tab-pane :label="I18N.t('p.tabDone')" name="done" :disabled="!canComplete">
          <el-card shadow="never">
            <template #header><b>{{ I18N.t('p.tabDone') }}</b></template>
            <el-alert v-if="data.project.status === 'done'" type="success" :closable="false"
                      :title="I18N.t('p.allDone')" class="mb8" />
            <p class="muted small mb8" v-else>{{ I18N.t('p.doneHint') }}</p>
            <div class="actions">
              <el-button type="primary" :disabled="!doneCount" @click="exportZip">{{ I18N.t('p.exportZip') }}</el-button>
              <el-tooltip v-if="data.project.kind === 'drama'" :content="I18N.t('p.exportPdfDrama')" placement="top">
                <el-button :disabled="true">{{ I18N.t('p.exportPdf') }}</el-button>
              </el-tooltip>
              <el-button v-else type="primary" :disabled="!doneCount" @click="exportPdf">{{ I18N.t('p.exportPdf') }}</el-button>
              <el-popconfirm :title="I18N.t('p.completeConfirm')" @confirm="complete">
                <template #reference><el-button type="success" :disabled="!doneCount">{{ I18N.t('p.complete') }}</el-button></template>
              </el-popconfirm>
            </div>
          </el-card>
        </el-tab-pane>
      </el-tabs>

      <el-dialog v-model="cfgDlg" :title="I18N.t('p.cfgTitle')" width="540px">
        <p class="hint">{{ I18N.t('p.cfgHint') }}</p>
        <el-form label-position="top">
          <el-form-item :label="I18N.t('cf.llm')">
            <el-select v-model="cfg.llm" style="width: 100%">
              <el-option v-for="c in data.llm_configs" :key="c.id" :value="c.id"
                         :label="c.name + '（' + c.model + (c.supports_vision === 'no' ? ' / ' + I18N.t('cfg.textOnly') : '') + '）'" />
            </el-select>
          </el-form-item>
          <el-form-item :label="I18N.t('cf.dt')">
            <el-select v-model="cfg.dt" style="width: 100%">
              <el-option v-for="c in data.drawthing_configs" :key="c.id" :value="c.id" :label="c.name" />
              <el-option v-if="!data.drawthing_configs.length" value="" :label="I18N.t('cf.dtNone')" />
            </el-select>
            <div class="hint">{{ I18N.t('cf.dtHint') }}<el-link :underline="false" type="primary" @click="router.push('/configs?ctype=drawthings')">{{ I18N.t('cf.newCfg') }}</el-link></div>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="cfgDlg = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" :loading="cfgBusy" @click="saveCfg">{{ I18N.t('p.cfgSave') }}</el-button>
        </template>
      </el-dialog>

      <el-dialog v-model="resetDlg" :title="I18N.t('p.reset')" width="540px">
        <p class="hint">{{ I18N.t('p.resetHint') }}</p>
        <el-form label-position="top">
          <el-form-item :label="I18N.t('p.resetTitle')">
            <el-input v-model="rtitle" maxlength="200" />
          </el-form-item>
          <el-form-item :label="I18N.t('p.resetOrigin')">
            <el-input v-model="rogin" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item :label="I18N.t('p.resetStyle')">
            <el-input v-model="rstyle" />
          </el-form-item>
        </el-form>
        <el-checkbox v-model="rclear" style="margin-bottom: 4px;">{{ I18N.t('p.resetClear') }}</el-checkbox>
        <template #footer>
          <el-button @click="resetDlg = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" @click="saveReset">{{ I18N.t('p.resetSave') }}</el-button>
        </template>
      </el-dialog>

      <el-image-viewer v-if="lb.show" :url-list="lb.list" :initial-index="lb.idx" @close="lb.show = false" />
    </div>
    <div v-else class="page loading"><el-skeleton :rows="6" animated /></div>
  `,
  setup(props) {
    const data = ref(null);
    const scope = computed(() => (data.value && data.value.project.scope) || {});
    const status = computed(() => data.value?.project.status);
    const doneCount = computed(() => (data.value?.chapters || []).filter(c => c.status === 'done').length);
    const canComplete = computed(() => doneCount.value > 0 || status.value === 'done');
    const tab = ref('outline');
    const tabInit = ref(false);

    const actBusy = ref(false);
    const busySave = ref(false);
    const busyGenAll = ref(false);
    const progress = reactive({ text: '' });

    // 大纲内部子页签（纵向）：story / chars / plan
    const oSub = ref('story');
    // 章节主从布局：当前选中章节序号
    const cur = ref(0);
    const curCh = computed(() => {
      const a = data.value?.chapters;
      return (a && a.length && cur.value >= 0 && cur.value < a.length) ? a[cur.value] : null;
    });
    // 左侧章节列表分页：10/页（选中章节变化时跳到对应页，便于生成时跟随）
    const pageSize = 10;
    const page = ref(1);
    const totalPages = computed(() => Math.max(1, Math.ceil((data.value?.chapters || []).length / pageSize)));
    const pageStart = computed(() => Math.min((page.value - 1) * pageSize, Math.max(0, (data.value?.chapters || []).length - 1)));
    const pageChapters = computed(() => (data.value?.chapters || []).slice(pageStart.value, pageStart.value + pageSize));
    watch(cur, (v) => {
      const n = (data.value?.chapters || []).length;
      page.value = n ? Math.min(totalPages.value, Math.floor(v / pageSize) + 1) : 1;
    });
    // 批量生成多选：勾选的章节序号（空 = 生成全部）
    const selected = ref([]);
    const allSelected = computed(() => {
      const n = (data.value?.chapters || []).length;
      return n > 0 && selected.value.length === n;
    });
    function isSel(i) {
      return selected.value.indexOf(data.value.chapters[i].index) >= 0;
    }
    function toggleSelect(i) {
      const idx = data.value.chapters[i].index;
      const k = selected.value.indexOf(idx);
      if (k >= 0) selected.value.splice(k, 1);
      else selected.value.push(idx);
    }
    function toggleAllSelect() {
      selected.value = allSelected.value ? [] : (data.value.chapters || []).map(c => c.index);
    }

    // 大纲表单
    const arcText = ref('');
    const oStyle = ref('');
    const oChars = ref('');
    const oGlobal = ref('');     // 全局提示词（要点/约束，注入每次章节 LLM 调用）
    const oW = ref(0);
    const oH = ref(0);
    const cMode = ref('auto');   // 章节数量：auto（模型决定）/ range（区间）
    const cMin = ref(3);
    const cMax = ref(5);

    const cfgDlg = ref(false);
    const cfgBusy = ref(false);
    const cfg = reactive({ llm: '', dt: '' });
    const lb = reactive({ show: false, list: [], idx: 0 });
    const resetDlg = ref(false);
    const rtitle = ref('');
    const rogin = ref('');
    const rstyle = ref('');
    const rclear = ref(false);

    function syncOutlineForm() {
      const p = data.value.project;
      arcText.value = p.arc || '';
      oStyle.value = (p.scope || {}).style || '';
      oChars.value = p.characters || '';
      oGlobal.value = p.global_prompt || '';
      oW.value = p.res_width || 0;
      oH.value = p.res_height || 0;
      cMode.value = p.count_mode === 'range' ? 'range' : 'auto';
      cMin.value = p.count_min || 3;
      cMax.value = p.count_max || 5;
    }
    function syncTab() {
      // 默认落在「大纲」页（含已规划章节的进行中项目）；仅「已完成」项目直接落到「完成」页
      if (status.value === 'done') tab.value = 'done';
      else tab.value = 'outline';
    }
    // 章节列表变化后，把选中序号钳制到有效范围
    function syncCur() {
      const n = (data.value?.chapters || []).length;
      if (cur.value >= n) cur.value = Math.max(0, n - 1);
    }

    async function load() {
      try {
        data.value = await API.get('/api/projects/' + props.id);
        syncOutlineForm();
        syncCur();
        if (!tabInit.value) { syncTab(); tabInit.value = true; }
      } catch (e) {
        if (e.status === 404) router.replace('/projects');
        ElementPlus.ElMessage.error(e.message);
      }
    }

    // 非流式推进：outline（大纲 + 章节规划）/ chapters（按大纲重拆章）
    async function doAction(step, payload) {
      actBusy.value = true;
      progress.text = '';
      try {
        await API.post(`/api/projects/${props.id}/action`, Object.assign({ step }, payload || {}));
        ElementPlus.ElMessage.success(I18N.t('p.msgDone'));
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        await load();
      } finally {
        actBusy.value = false;
        progress.text = '';
      }
    }
    function genOutline() {
      // 确定大纲：只生成 风格/大纲/角色/分辨率，不生成章节
      doAction('outline', { res_width: oW.value, res_height: oH.value });
    }
    function planChapters() {
      // 章节规划：按章节数量设定（auto / range min~max）单独生成章节
      doAction('chapters', {
        count_mode: cMode.value,
        count_min: cMode.value === 'range' ? cMin.value : 0,
        count_max: cMode.value === 'range' ? cMax.value : 0,
      });
    }

    async function saveOutline() {
      busySave.value = true;
      try {
        await API.post(`/api/projects/${props.id}/outline`, {
          arc: arcText.value, characters: oChars.value, style: oStyle.value,
          global_prompt: oGlobal.value,
          res_width: oW.value, res_height: oH.value,
          count_mode: cMode.value,
          count_min: cMode.value === 'range' ? cMin.value : 0,
          count_max: cMode.value === 'range' ? cMax.value : 0,
          chapters: data.value.chapters.map(c => ({ title: c.title, summary: c.summary })),
        });
        ElementPlus.ElMessage.success(I18N.t('p.outSaved'));
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        busySave.value = false;
      }
    }

    // 批量（SSE 进度）
    // 批量生成时单章两步完成：实时把该章提示词/媒体/状态写回本地并跟随定位，页面逐个刷新（后续章节仍会参考它）
    function applyChapterLive(d) {
      const arr = data.value?.chapters || [];
      const pos = arr.findIndex(c => c.index === d.index);
      if (pos < 0) return;
      const ch = arr[pos];
      ch.status = d.status;
      ch.error = d.error || '';
      if (d.media_url) ch.media_url = d.media_url;
      if (d.prompt !== undefined) ch.prompt = d.prompt;
      if (d.description !== undefined) ch.description = d.description;
      if (d.width) { ch.width = d.width; ch.height = d.height; }
      cur.value = pos;
    }
    // 生成画面：勾选章节则只生成它们，未勾选则生成全部（两步连贯、逐章进行、后章参考前章已生成图）
    async function genAll() {
      busyGenAll.value = true; progress.text = '';
      const indices = selected.value.length ? selected.value : null;
      try {
        await API.sse(`/api/projects/${props.id}/action-stream`, { step: 'generate', indices }, (ev, d) => {
          if (ev === 'progress') progress.text = I18N.t('p.genProgress', d.current, d.total, d.title);
          else if (ev === 'chapter') applyChapterLive(d);
          else if (ev === 'error') throw new Error(d.message);
        });
        ElementPlus.ElMessage.success(I18N.t('p.msgDone'));
        selected.value = [];
        await load();
      } catch (e) { ElementPlus.ElMessage.error(e.message); await load(); }
      finally { busyGenAll.value = false; progress.text = ''; }
    }

    async function complete() {
      try {
        await API.post(`/api/projects/${props.id}/complete`);
        ElementPlus.ElMessage.success(I18N.t('p.completedMsg'));
        await load();
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    function exportZip() { window.location.href = `/api/projects/${props.id}/export/zip`; }
    function exportPdf() { window.location.href = `/api/projects/${props.id}/export/pdf`; }

    async function addChapter() {
      try {
        await API.post(`/api/projects/${props.id}/chapters`);
        await load();
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    async function delChapter(i) {
      try {
        await API.del(`/api/projects/${props.id}/chapters/${i}`);
        await load(); // load() 内 syncCur() 会把选中钳制到有效范围
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    function onChapterReloaded(index) {
      if (typeof index === 'number' && index >= 0) cur.value = index; // 生成/写剧本后切到该章
      load();
    }

    function openReset() {
      rtitle.value = data.value.project.title || '';
      rogin.value = data.value.project.origin || '';
      rstyle.value = (data.value.project.scope || {}).style || '';
      rclear.value = false;
      resetDlg.value = true;
    }
    async function saveReset() {
      if (!rogin.value.trim()) {
        ElementPlus.ElMessage.warning(I18N.t('p.resetOriginReq'));
        return;
      }
      try {
        await API.post(`/api/projects/${props.id}/reset`, {
          title: rtitle.value, origin: rogin.value, style: rstyle.value,
          clear_downstream: rclear.value,
        });
        ElementPlus.ElMessage.success(I18N.t('p.resetSaved'));
        resetDlg.value = false;
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    function openCfg() {
      cfg.llm = data.value.project.llm_config_id;
      cfg.dt = data.value.project.drawthings_config_id;
      cfgDlg.value = true;
    }
    async function saveCfg() {
      cfgBusy.value = true;
      try {
        await API.post(`/api/projects/${props.id}/config`, {
          llm_config_id: cfg.llm, drawthings_config_id: cfg.dt,
        });
        ElementPlus.ElMessage.success(I18N.t('p.cfgSaved'));
        cfgDlg.value = false;
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        cfgBusy.value = false;
      }
    }

    async function del() {
      try {
        await API.post(`/api/projects/${props.id}/delete`);
        ElementPlus.ElMessage.success(I18N.t('proj.msgDeleted'));
        router.push('/projects');
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    function openLb(list, idx) {
      lb.list = list;
      lb.idx = idx;
      lb.show = true;
    }

    onMounted(load);
    return {
      data, scope, status, doneCount, canComplete, tab, oSub, cur, curCh, selected, allSelected,
      page, totalPages, pageStart, pageChapters,
      actBusy, busySave, busyGenAll, progress,
      arcText, oStyle, oChars, oGlobal, oW, oH, cMode, cMin, cMax,
      cfgDlg, cfgBusy, cfg, lb, resetDlg, rtitle, rogin, rstyle, rclear,
      genOutline, planChapters, saveOutline, doAction, genAll, isSel, toggleSelect, toggleAllSelect,
      complete, exportZip, exportPdf, addChapter, delChapter, onChapterReloaded,
      openReset, saveReset, openCfg, saveCfg, del, openLb, load, router,
    };
  },
};
