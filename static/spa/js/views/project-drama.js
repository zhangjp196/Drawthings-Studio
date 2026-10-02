// 项目详情 · 短剧（视频）版：头部/封面 + 季选择器（仅各季）+ 固定的「总体」入口（与各季用竖线分隔）
// 总体：单页（无二级页签）—— 基本信息（标题 / 全局要求 / 分辨率）与封面合并在同一张卡片
// 季：二级页签（企划 / 章节 / 预览 / 导出）；企划内含子页签（本季大纲 / 季角色 / 季封面）；角色只挂在季上，参考图与 AI 描述入口在季角色页
// 章节：一键生成（剧本/画面）+ 手风琴卡片（多步，短剧版 chapter-card-drama）
// 导出：当前所选季的完成情况（X/Y、整季完成提示）+ 按季导出 ZIP（视频不支持 PDF）
// 与漫画版（project-comic.js）完全独立：章节/预览固定为视频（video），不含任何图片放大逻辑
window.Views = window.Views || {};
Views.projectDrama = {
  props: ['id'],
  components: { 'first-image': Views.dramaFirstImage, 'season-cover': Views.dramaSeasonCover, 'chapter-card': Views.chapterCardDrama, 'season-preview': Views.dramaSeasonPreview, 'season-export': Views.dramaSeasonExport, 'chapter-list': Views.dramaChapterList, 'chapter-toolbar': Views.dramaChapterToolbar, 'plan-dialog': Views.dramaPlanDialog, 'gen-dialog': Views.dramaGenDialog, 'overlay-dialog': Views.dramaOverlayDialog, 'cover-dialog': Views.dramaCoverDialog, 'cfg-dialog': Views.dramaCfgDialog, 'video-dialog': Views.dramaVideoDialog },
  template: `
    <div class="page" v-if="data">
      <div class="proj-head">
        <div>
          <el-tag size="small" type="success" effect="light">{{ I18N.t('proj.drama') }}</el-tag>
          <h1 class="ptitle">{{ data.project.title || data.project.origin }}</h1>
          <p class="meta muted">{{ I18N.t('p.metaIdea', data.project.origin) }}
            · {{ I18N.t('d.metaLength', data.chapters.length) }}
            · {{ I18N.t('p.metaLlm', data.project.llm_name) }}
            · {{ I18N.t('p.metaDt', data.project.dt_name) }}</p>
        </div>
        <div class="proj-head-actions">
          <el-button  @click="openCfg">{{ I18N.t('p.settings') }}</el-button>
          <el-button @click="router.push(backTo())">{{ I18N.t('p.back') }}</el-button>
        </div>
      </div>



      <!-- 季（篇章）选择栏：最左为独立的「总体」入口（固定，不随各季 tab 切换），其后为各季 -->
      <div class="season-bar">
        <button type="button" class="season-item season-overall" :class="{ active: isOverall }" @click="selectSeason('overall')">
          {{ I18N.t('p.tabOverall') }}
        </button>
        <span class="season-sep" aria-hidden="true"></span>
        <button v-for="s in seasons" :key="s.id" type="button" class="season-item" :class="{ active: seasonId === s.id }" @click="selectSeason(s.id)">
          {{ s.title || I18N.t('d.ep', s.number) }}
        </button>
        <el-popconfirm :title="I18N.t('d.epAddConfirm')" @confirm="addSeason">
          <template #reference><button type="button" class="season-item season-add" >{{ I18N.t('d.epAdd') }}</button></template>
        </el-popconfirm>
        <el-popconfirm v-if="seasons.length > 1 && !isOverall" :title="I18N.t('d.epDelConfirm')" @confirm="delSeason">
          <template #reference><button type="button" class="season-item season-del">{{ I18N.t('d.epDel') }}</button></template>
        </el-popconfirm>
      </div>

      <!-- ============ 总体：单页（无二级页签）—— 标题 / 全局要求 / 分辨率 / 封面 ============ -->
      <div v-if="isOverall" class="subtabs">
        <div class="subtabs-body">
          <el-card shadow="never">
            <div class="actions outline-bar">
              <el-popconfirm :title="I18N.t('p.saveOutlineConfirm')" @confirm="saveStory">
                <template #reference><el-button type="primary" :loading="busySave">{{ I18N.t('p.outSave') }}</el-button></template>
              </el-popconfirm>
              <el-button :loading="busyFirst" @click="openCoverGenDlg('project')">{{ I18N.t('p.genFirst') }}</el-button>
              <span class="muted" v-if="busySave || busyFirst" style="margin-left:10px;">{{ progress.text || I18N.t('p.busy') }}</span>
            </div>
            <el-form label-position="top">
              <el-form-item :label="I18N.t('p.outTitle')" class="out-title">
                <el-input v-model="oTitle" size="large" maxlength="100" :placeholder="I18N.t('p.outTitlePh')" @blur="saveTitle" />
              </el-form-item>
              <el-form-item :label="I18N.t('p.globalReq')">
                <el-input v-model="oGlobal" type="textarea" :rows="5" :placeholder="I18N.t('p.globalReqHint')" />
              </el-form-item>
              <el-form-item :label="I18N.t('p.outRes')">
                <div class="res-row">
                  <el-input-number v-model="oW" :min="0" :max="4096" :step="64" size="small" />
                  <span>×</span>
                  <el-input-number v-model="oH" :min="0" :max="4096" :step="64" size="small" />
                  <span class="muted small" style="margin-left:6px;">{{ I18N.t('p.outResHint') }}</span>
                </div>
              </el-form-item>
            </el-form>
            <div class="divider"></div>
            <first-image :project="data.project" :project-id="data.project.id" v-model:prompt="coverPrompt" @preview="openLb([$event], 0)" @reloaded="load"
                         @overlay="openOvlDlg('project')" />
          </el-card>
        </div>
      </div>


      <!-- ============ 季：二级页签 企划 / 章节 / 预览 / 完成（随所选季作用域）============ -->
      <el-tabs v-else v-model="tab" class="proj-tabs">
        <el-tab-pane :label="I18N.t('p.tabOutline')" name="outline">
          <div class="subtabs">
            <nav class="subtabs-nav">
              <button type="button" class="subtabs-item" :class="{ active: oSub === 'arc' }" @click="oSub = 'arc'">{{ I18N.t('d.epArc') }}</button>
              <button type="button" class="subtabs-item" :class="{ active: oSub === 'chars' }" @click="oSub = 'chars'">{{ I18N.t('d.epChars') }}</button>
              <button type="button" class="subtabs-item" :class="{ active: oSub === 'cover' }" @click="oSub = 'cover'">{{ I18N.t('d.epCover') }}</button>
            </nav>
            <div class="subtabs-body">
              <el-card v-if="oSub === 'arc'" shadow="never">
                  <div class="actions outline-bar">
                    <el-button type="primary" :loading="actBusy"  @click="openGenDlg('season_arc', I18N.t('d.epArcGen'))">{{ I18N.t('d.epArcGen') }}</el-button>
                    <el-popconfirm :title="I18N.t('d.epArcSaveConfirm')" @confirm="saveSeasonArc">
                      <template #reference><el-button :loading="busySave" >{{ I18N.t('d.epArcSave') }}</el-button></template>
                    </el-popconfirm>
                    <span class="muted" v-if="actBusy || busySave" style="margin-left:10px;">{{ progress.text || I18N.t('p.busy') }}</span>
                  </div>
                   <el-form label-position="top">
                     <el-form-item :label="I18N.t('d.epTitle')">
                       <el-input v-model="seasonTitleText" :placeholder="I18N.t('d.epTitlePh')" />
                     </el-form-item>
                     <el-form-item :label="I18N.t('d.epArc')">
                       <el-input v-model="seasonArcText" type="textarea" :rows="10" :placeholder="I18N.t('d.epArcPh')" />
                       <div class="hint">{{ I18N.t('d.epArcHint') }}</div>
                     </el-form-item>
                   </el-form>
                </el-card>
                <el-card v-else-if="oSub === 'chars'" shadow="never">
                  <div class="actions outline-bar">
                    <el-button type="primary" :loading="actBusy"  @click="openGenDlg('season_chars', I18N.t('d.epCharsGen'))">{{ I18N.t('d.epCharsGen') }}</el-button>
                    <el-popconfirm :title="I18N.t('d.epCharsSaveConfirm')" @confirm="saveSeasonChars">
                      <template #reference><el-button :loading="busySave" >{{ I18N.t('d.epCharsSave') }}</el-button></template>
                    </el-popconfirm>
                    <span class="muted" v-if="actBusy || busySave" style="margin-left:10px;">{{ progress.text || I18N.t('p.busy') }}</span>
                  </div>
                  <div v-for="(c, i) in seasonChars" :key="'sc' + (c.id || i)" class="char-card">
                    <div class="row-between" style="margin-bottom:6px;">
                      <b class="muted small">{{ I18N.t('p.char', i + 1) }}</b>
                      <el-button size="small" type="danger" plain @click="delSeasonChar(i)">{{ I18N.t('p.charDel') }}</el-button>
                    </div>
                    <el-form label-position="top">
                      <el-form-item :label="I18N.t('p.charName')">
                        <el-input v-model="c.name" size="small" :placeholder="I18N.t('p.charNamePh')" />
                      </el-form-item>
                      <el-form-item :label="I18N.t('p.charDesc')">
                        <div class="row" style="gap:6px;">
                          <el-input v-model="c.description" type="textarea" :rows="3" :placeholder="I18N.t('p.charDescPh')" />
                          <el-button size="small" :loading="genDescBusy === (c.id || '')" @click="genSeasonCharDesc(i)">{{ I18N.t('p.charDescGen') }}</el-button>
                        </div>
                      </el-form-item>
                      <el-form-item :label="I18N.t('p.charRef')">
                        <div class="char-ref">
                          <img v-if="c.image_url" :src="c.image_url" class="char-ref-img" :alt="c.name || ''"
                               loading="lazy" decoding="async" @click="openLb([c.image_url], 0)">
                          <el-upload :auto-upload="false" :show-file-list="false" accept="image/*" :on-change="(f) => uploadSeasonCharImage(i, f)">
                            <el-button size="small" >{{ c.image_url ? I18N.t('p.charRefChange') : I18N.t('p.uploadBtn') }}</el-button>
                          </el-upload>
                          <el-button v-if="c.image_url" size="small" type="danger" plain
                                     @click="removeSeasonCharImage(i)">{{ I18N.t('p.charRefDel') }}</el-button>
                        </div>
                        <div class="hint" style="margin-top:4px;">{{ I18N.t('p.charRefHint') }}</div>
                      </el-form-item>
                    </el-form>
                  </div>
                  <div class="actions" style="margin-top:10px;">
                    <el-button size="small" @click="addSeasonChar">{{ I18N.t('p.charAdd') }}</el-button>
                  </div>
                  <el-empty v-if="!seasonChars.length" :description="I18N.t('d.epCharsEmpty')" :image-size="48" />
                </el-card>
                <template v-else-if="oSub === 'cover'">
                  <div class="actions outline-bar">
                    <el-button type="primary" :loading="busySeasonFirst"  @click="openCoverGenDlg('season')">{{ I18N.t('d.genEpCover') }}</el-button>
                    <span class="muted" v-if="busySeasonFirst" style="margin-left:10px;">{{ I18N.t('p.busy') }}</span>
                  </div>
                  <season-cover :season="curSeason" :project-id="data.project.id" :season-id="seasonId"
                                v-model:prompt="seasonCoverPrompt"
                                @preview="openLb([$event], 0)" @reloaded="load"
                                @overlay="openOvlDlg('season')" />
                </template>
            </div>
          </div>
        </el-tab-pane>

        <!-- ============ 片段：片段规划 / 片段详情 / 预览 一体（集作用域，一屏内完成）============ -->
        <el-tab-pane :label="I18N.t('d.tabClips')" name="chapters">
          <chapter-toolbar :act-busy="actBusy" :busy-save="busySave" :busy-gen-all="busyGenAll" :busy-score-all="busyScoreAll" :selected-count="selected.length" :score-filter="scoreFilter" :score-filter-options="scoreFilterOptions" :o-w="oW" :o-h="oH" :has-chapters="!!seasonChapters.length" :total="seasonChapters.length" :done-count="seasonDoneCount" :progress-text="progress.text" :all-selected="allSelected"
                           @plan="openPlanDlg" @save-plan="savePlan" @update:score-filter="setScoreFilter"
                           @toggle-all="toggleAllSelect" @gen-all="genAll" @score-all="scoreAll"
                           @clear-all="clearAll" @plan-selected="planSelected" @delete-selected="deleteSelected"
                           @set-seconds="setBatchSeconds" @stop="stopGen" />

          <!-- 上：片段列表（横向，短剧专属子组件） -->
          <div class="ch-strip-wrap">
            <chapter-list :chapters="visibleChapters" :current="cur" :selected="selected" :score-min="data.project.score_min || 60"
                          @select="pickChapter" @toggle="toggleSelect" />
          </div>

          <div class="md-layout ch-work">
            <!-- 中：选中片段详情（标题 + 摘要/剧本/提示词 三个子页签，一次「保存」提交） -->
            <div class="md-detail">
              <chapter-card :key="curCh.index" v-if="curCh" :chapter="curCh" :project-id="data.project.id" :season-id="seasonId" :season-index="cur" :def-w="oW" :def-h="oH" :score-min="(data.project.score_min || 60)" :beats="epBeats" :clip-count="seasonChapters.length" :ref-kind="curRefKind(cur)" :expanded="true" :no-toggle="true" :is-first="cur === 0" :is-last="cur === seasonChapters.length - 1"
                            @preview="openLb([$event], 0)" @reloaded="onChapterReloaded"
                            @saveplan="savePlan" />
              <el-empty v-else :description="I18N.t('d.clipPlanEmpty')" :image-size="54" />
            </div>

            <!-- 右：本集预览（短剧专属子组件） -->
            <season-preview :items="pvItems" :urls="pvUrls" :current="cur" :total="seasonChapters.length"
                            @select="gotoChapter" />
          </div>
        </el-tab-pane>

        <el-tab-pane :label="I18N.t('p.tabExport')" name="done">
          <season-export :done="seasonDoneCount" :total="seasonChapters.length" :completed="seasonCompleted" :exporting="exporting"
                         @zip="exportZip" @preview-video="previewVideo" @export-video="exportVideo" />
        </el-tab-pane>
      </el-tabs>

      <cfg-dialog v-model="cfgDlg" :cfg="cfg" :llm-configs="data.llm_configs" :dt-configs="data.drawthing_configs" :img-choices="imgChoices" :vid-choices="vidChoices" :busy="cfgBusy" @save="saveCfg"
                  @new-config="router.push('/configs?ctype=drawthings')" />

      <gen-dialog v-model="genDlg" v-model:extra="genDlgExtra" :title="genDlgTitle" :busy="actBusy" @confirm="confirmGen" />

      <!-- 片段规划：数量 + 方式（新增 / 覆盖），确认后执行 SSE 逐段规划 -->
      <plan-dialog v-model="planDlg" v-model:count="planCount" v-model:mode="planMode" :title="planDlgTitle" :hint="planModeHint" :busy="actBusy" @confirm="confirmPlan" />

      <!-- 生成封面：勾选「包含标题」= 生成后自动叠加作品标题 / 季名 -->
      <cover-dialog :dlg="coverGenDlg" :busy="busyFirst || busySeasonFirst" @confirm="confirmCoverGen" />

      <!-- 叠加标题：位置（自由拖动）+ 字号 / 样式 / 颜色 / 底条 -->
      <overlay-dialog :dlg="ovlDlg" :busy="ovlBusy" @apply="applyOvl" />

      <!-- 合成视频预览：inline mp4 直接播放 -->
      <video-dialog v-model="videoDlg" :url="videoUrl" @closed="videoUrl = ''" />

      <el-image-viewer v-if="lb.show" :url-list="lb.list" :initial-index="lb.idx" @close="lb.show = false" />
    </div>
    <div v-else class="page loading"><el-skeleton :rows="6" animated /></div>
  `,
  setup(props) {
    const data = ref(null);
    const tab = ref('outline');
    const tabInit = ref(false);
    // 返回/删除/404 一律回到「视频创作」列表（带 ?kind=drama，保持侧边栏高亮）
    const backTo = () => '/projects?kind=drama';

    const actBusy = ref(false);
    const busySave = ref(false);
    const busyGenAll = ref(false);
    const busyScoreAll = ref(false);
    const exporting = ref(false);  // 导出（ZIP/合成视频）进行中（CS 走系统「另存为」，耗时期间禁用按钮）
    const videoDlg = ref(false);   // 合成视频预览弹框
    const videoUrl = ref('');      // 合成视频预览地址（inline mp4）
    const busyFirst = ref(false);
    const busySeasonFirst = ref(false);
    // 生成封面弹框：包含标题（生成后自动叠加作品标题 / 季名）
    const coverGenDlg = reactive({ show: false, target: 'project', includeTitle: true });
    // 叠加标题弹框：位置（自由拖动）/ 字号 / 样式 / 颜色 / 底条
    const ovlDlg = reactive({ show: false, target: 'project', titleText: '', coverUrl: '',
                              x: 0.5, y: 1 / 3, sizePct: 8, style: 'bold_outline',
                              color: '#ffffff', band: true });
    const ovlBusy = ref(false);
    const genDescBusy = ref(null);  // 正在 AI 生成描述的字符 id（null = 空闲）
    const coverPrompt = ref('');   // 封面生成提示词（first-image 子组件持有输入，顶部「生成封面」按钮使用）
    const seasonCoverPrompt = ref('');   // 季封面生成提示词（season-cover 子组件持有输入，季封面子页「生成季封面」按钮使用）
    const progress = reactive({ text: '' });
    let sseCtrl = null;            // 当前流式任务（规划/生成）：离开页面或重开时中断，服务端随之清理

    // 企划子页签：总体模式 story（故事大纲）/ chars（角色）/ cover（封面）；季模式 arc（本季大纲）/ chars（季角色）/ plan（章节规划）
    const oSub = ref('arc');
    // 季（篇章）：seasonId = 'overall' 表示选中「总体」（全局），否则为某季 id
    // 注意：必须声明在引用 seasonChapters 的 computed / watch 之前——
    // Vue 的 watch 注册时会立即执行一次 getter 取旧值，声明靠后会触发 TDZ ReferenceError
    const seasonId = ref('overall');
    const isOverall = computed(() => seasonId.value === 'overall');
    const seasonArcText = ref('');
    const seasonTitleText = ref('');
    const seasonChars = ref([]);
    const seasons = computed(() => (data.value?.seasons || []));
    const curSeason = computed(() => seasons.value.find(x => x.id === seasonId.value) || null);
    const seasonChapters = computed(() => {
      if (isOverall.value) return [];
      return (data.value?.chapters || []).filter(c => c.season_id === seasonId.value);
    });
    const seasonDoneCount = computed(() => seasonChapters.value.filter(c => c.status === 'done').length);
    // 本集关键剧情节点：从本集大纲中解析「关键剧情节点：」小节下的编号条目（用于片段卡片按比例落位）
    const epBeats = computed(() => {
      const arc = (curSeason.value?.arc || '').trim();
      if (!arc) return [];
      const marker = arc.indexOf('关键剧情节点');
      if (marker < 0) return [];
      const body = arc.slice(marker).split('\n').slice(1);
      return body.map(l => l.replace(/^\s*\d+[.、)]\s*/, '').trim()).filter(Boolean);
    });
    // 某片段的参考来源种类（连贯信息）：prev | epcover | prevep | next | none
    function curRefKind(i) {
      const list = seasonChapters.value;
      if (!list.length) return 'none';
      if (i > 0) return 'prev';
      // i === 0：本集首段 → 本集封面（若开启）→ 上一集末段 → 下一段（首集首段回退）
      const s = curSeason.value;
      if (s && s.cover_as_first_ref && s.first_image_url) return 'epcover';
      if (s && s.number > 1) return 'prevep';
      return list.length > 1 ? 'next' : 'none';
    }
    // 批量设置所选（或整集）片段时长：selected 存的是全局扁平 index，需映射回该集内的局部位置
    async function setBatchSeconds(sec) {
      if (!seasonId.value) return;
      const s = parseInt(sec, 10);
      if (!(s > 0)) return;
      const list = seasonChapters.value;
      const local = list.map((c, i) => i);
      const targets = selected.value.length
        ? local.filter(i => selected.value.indexOf(list[i].index) >= 0)
        : local;
      try {
        for (const i of targets) {
          await API.post(`/api/dramas/${props.id}/edit/${i}`, { season_id: seasonId.value, seconds: s });
        }
        ElementPlus.ElMessage.success(I18N.t('d.batchSecondsDone'));
        selected.value = [];
        await load();
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    // 评分筛选：'' 全部 / none 未评分 / low 低于阈值 / pass 达标（仅过滤左侧列表显示，
    // 不影响整季进度统计与「生成/评分全部」——它们仍作用于整季）
    const scoreFilter = ref('');
    function setScoreFilter(v) { scoreFilter.value = v || ''; }
    const scoreFilterOptions = computed(() => [
      { value: '', label: I18N.t('p.filterAll') },
      { value: 'none', label: I18N.t('p.filterUnscored') },
      { value: 'low', label: I18N.t('p.filterBelowMin') },
      { value: 'pass', label: I18N.t('p.filterPass') },
    ]);
    const visibleChapters = computed(() => {
      const a = seasonChapters.value;
      const f = scoreFilter.value;
      if (!f) return a;
      const th = data.value?.project?.score_min || 60;
      if (f === 'none') return a.filter(c => !(c.score > 0));
      if (f === 'low') return a.filter(c => c.score > 0 && c.score < th);
      return a.filter(c => c.score >= th);
    });
    // 预览页签：平铺 / 画廊；pvItems 带「有图序号 k」（无图章节不进放大列表），缺图白色占位
    const pvItems = computed(() => {
      let k = -1;
      return seasonChapters.value.map(c => {
        const has = !!(c.media_url || '').trim();
        if (has) k += 1;
        return { index: c.index, title: c.title, url: c.media_url || '', has, k };
      });
    });
    const pvUrls = computed(() => pvItems.value.filter(x => x.has).map(x => x.url));
    // 季完成 = 本季章节全部生成（派生值，不存储）；空季不算完成
    const seasonCompleted = computed(() => seasonChapters.value.length > 0 && seasonDoneCount.value === seasonChapters.value.length);

    // 章节主从布局：当前选中章节序号
    const cur = ref(0);
    const curCh = computed(() => {
      const a = seasonChapters.value;
      return a.find(c => c.index === cur.value) || null;
    });
    // 切换评分筛选后，若当前章节不在可见列表内，跳到第一个可见章节
    watch(scoreFilter, () => {
      if (curCh.value && !visibleChapters.value.some(c => c.index === cur.value)) {
        const v = visibleChapters.value;
        if (v.length) cur.value = v[0].index;
      }
    });
    // 左列表点击选中章节（不滚动；滚动跟随由 scrollActiveIntoView 处理）
    function pickChapter(i) { cur.value = i; }
    // 预览点图 → 切到对应章节（左列表页码随 cur 自动同步），并滚回章节工作区
    function gotoChapter(i) {
      cur.value = i;
      const work = document.querySelector('.md-layout');
      if (work && work.scrollIntoView) work.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    // 左侧章节列表：不分页、整季滚动；当前章自动滚到可见（批量生成 / 预览点击时跟随）
    function scrollActiveIntoView() {
      const el = document.querySelector('.md-list .md-item.active');
      if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
    }
    watch(cur, () => nextTick(scrollActiveIntoView));
    // 批量生成多选：勾选的章节序号（季内 0 起；空 = 生成全部）
    const selected = ref([]);
    const allSelected = computed(() => {
      const v = visibleChapters.value;
      return v.length > 0 && v.every(c => selected.value.indexOf(c.index) >= 0)
        && selected.value.length === v.length;
    });
    function isSel(i) {
      return selected.value.indexOf(i) >= 0;
    }
    function toggleSelect(i) {
      const k = selected.value.indexOf(i);
      if (k >= 0) selected.value.splice(k, 1);
      else selected.value.push(i);
    }
    function toggleAllSelect() {
      selected.value = allSelected.value ? [] : visibleChapters.value.map(c => c.index);
    }

    // 总体表单
    const oTitle = ref('');        // 作品标题（放大输入；随「生成本集大纲」一并生成）
    const oGlobal = ref('');     // 全局要求（风格 + 要点/约束，注入每次 LLM 调用）
    const oW = ref(0);            // 默认分辨率宽（0 = 跟随出图端/智能体）
    const oH = ref(0);            // 默认分辨率高
    // 自动评分设置在项目「设置」弹框内维护（见 cfg / saveCfg），此处不再单独保留表单字段。
    // 片段数量（固定值）与规划方式（新增：现有片段之后续加 / 覆盖：全集重规划）
    const planCount = ref(12);
    const planMode = ref('append');
    // 片段规划弹框：数量 + 方式只在弹框内调整，确认后执行
    const planDlg = ref(false);
    const planDlgTitle = computed(() =>
      selected.value.length ? I18N.t('d.planSelDrama', selected.value.length) : I18N.t('d.planChaptersDrama'));
    const planModeHint = computed(() => {
      if (planMode.value === 'append') return I18N.t('d.planAppendHint', planCount.value);
      if (selected.value.length) return I18N.t('d.planSelConfirm', selected.value.length);
      return I18N.t('d.planRedoHint', planCount.value);
    });



    // 生成弹框（生成大纲 / 生成本季大纲 / 生成角色 共用）：额外提示词
    const genDlg = ref(false);
    const genDlgStep = ref('');   // 'season_arc' / 'season_chars'
    const genDlgTitle = ref('');
    const genDlgExtra = ref('');

    const cfgDlg = ref(false);
    const cfgBusy = ref(false);
    const cfg = reactive({ llm: '', dt: '', dt_model_i: '', dt_model_v: '', dt_ref_i: false, dt_ref_v: false, dt_steps_i: 0, dt_steps_v: 0,
                        score: true, score_min: 60, redo: true, stop_low: false });
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
    watch(() => cfg.dt, (id) => {
      const c = (data.value && data.value.drawthing_configs || []).find(x => x.id === id);
      const p = data.value.project;
      // 项目未显式选过模型时预填配置里的；参考图开关：项目显式值优先，否则跟随配置
      if (!p.dt_model_image) cfg.dt_model_i = c ? (c.model_image || '') : '';
      if (!p.dt_model_video) cfg.dt_model_v = c ? (c.model_video || '') : '';
      cfg.dt_ref_i = (p.dt_ref_image === '1') || (p.dt_ref_image === '' && !!c && !!c.ref_image);
      cfg.dt_ref_v = (p.dt_ref_video === '1') || (p.dt_ref_video === '' && !!c && !!c.ref_video);
      fetchModels();
    });
    const lb = reactive({ show: false, list: [], idx: 0 });

    function syncOutlineForm() {
      const p = data.value.project;
      oTitle.value = p.title || '';
      oGlobal.value = p.global_prompt || '';
      oW.value = p.res_width || 0;
      oH.value = p.res_height || 0;
    }
    function syncTab() {
      // 默认落在「大纲」页（含已规划章节的进行中项目）；完成已是季级，不再按作品状态自动跳「完成」
      tab.value = 'outline';
    }
    // 章节列表变化后，把选中序号钳制到有效范围
    function syncCur() {
      const n = seasonChapters.value.length;
      if (cur.value >= n) cur.value = Math.max(0, n - 1);
    }

    async function load() {
      try {
        data.value = await API.get('/api/dramas/' + props.id);
        syncOutlineForm();
        const s = data.value.seasons || [];
        // 保持当前选择（'overall' 或有效季 id）；无效则回落到「总体」
        const valid = seasonId.value === 'overall' || s.find(x => x.id === seasonId.value);
        if (!valid) {
          seasonId.value = 'overall';
          if (tab.value === 'chapters' || tab.value === 'done') tab.value = 'outline';  // 所选季被删除后「章节」「完成」无作用域
        }
        if (!isOverall.value) syncSeasonForm();
        syncCur();
        if (!tabInit.value) { syncTab(); tabInit.value = true; }
      } catch (e) {
        if (e.status === 404) router.replace(backTo());
        ElementPlus.ElMessage.error(e.message);
      }
    }

    // 非流式推进：arc（故事大纲）/ chars（角色设定）
    async function doAction(step, payload) {
      actBusy.value = true;
      progress.text = '';
      try {
        await API.post(`/api/dramas/${props.id}/action`, Object.assign({ step }, payload || {}), 0);
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
    // 生成弹框：生成大纲 / 生成本季大纲 / 生成角色 共用（框内可填额外提示词）
    function openGenDlg(step, title) {
      genDlgStep.value = step;
      genDlgTitle.value = title;
      genDlgExtra.value = '';
      genDlg.value = true;
    }
    function confirmGen() {
      const extra = genDlgExtra.value.trim();
      const step = genDlgStep.value;
      genDlg.value = false;
      if (step === 'season_arc') {
        if (isOverall.value) return;
        doAction('season_arc', { season_id: seasonId.value, extra_prompt: extra });
      } else if (step === 'season_chars') {
        if (isOverall.value) return;
        doAction('season_chars', { season_id: seasonId.value, extra_prompt: extra });
      }
    }
    // 季（篇章）管理
    function selectSeason(id) {
      seasonId.value = id;
      selected.value = [];
      cur.value = 0;
      if (id !== 'overall') {
        oSub.value = 'arc';     // 季模式首个子页签（总体为单页，无子页签）
        syncSeasonForm();
      }
      // 总体模式没有「章节 / 完成」（两者都按具体季作用域），切到总体时回落到「企划」
      if (id === 'overall' && (tab.value === 'chapters' || tab.value === 'done')) tab.value = 'outline';
    }
    async function addSeason() {
      try {
        await API.post(`/api/dramas/${props.id}/seasons`, { title: '' });
        ElementPlus.ElMessage.success(I18N.t('p.msgDone'));
        await load();
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    async function delSeason() {
      if (!seasonId.value) return;
      try {
        await API.del(`/api/dramas/${props.id}/seasons/${seasonId.value}`);
        ElementPlus.ElMessage.success(I18N.t('p.msgDone'));
        await load();
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    function syncSeasonForm() {
      const s = seasons.value.find(x => x.id === seasonId.value);
      if (!s) {
        seasonArcText.value = '';
        seasonTitleText.value = '';
        seasonChars.value = [];
        planCount.value = 12;
        planMode.value = 'append';
        return;
      }
      seasonArcText.value = s.arc || '';
      seasonTitleText.value = s.title || '';
      seasonChars.value = (s.characters || []).map(c => ({ id: c.id || '', name: c.name || '', description: c.description || '', image_url: c.image_url || '' }));
      planCount.value = s.count_max || 12;   // 固定值：读已存的 count_max（min=max）
      planMode.value = 'append';
    }
    function addSeasonChar() {
      seasonChars.value.push({ id: '', name: '', description: '', image_url: '' });
    }
    function delSeasonChar(i) {
      seasonChars.value.splice(i, 1);
    }
    // 季角色：AI 生成描述（未保存的新角色先落盘取 id）/ 上传或清除参考图（季级角色唯一支持参考图）
    async function genSeasonCharDesc(i) {
      const c = await ensureSeasonCharSaved(i);
      if (!c) return;
      genDescBusy.value = c.id;
      try {
        const r = await API.post(`/api/dramas/${props.id}/seasons/${seasonId.value}/characters/${c.id}/gen-desc`, {}, 0);
        if (r && r.description) c.description = r.description;
        ElementPlus.ElMessage.success(I18N.t('p.msgCharDescGenerated'));
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        genDescBusy.value = null;
      }
    }
    async function uploadSeasonCharImage(i, uploadFile) {
      const file = uploadFile && uploadFile.raw;
      if (!file) return;
      if (!file.type || !file.type.startsWith('image/')) {
        ElementPlus.ElMessage.warning(I18N.t('p.uploadWarn'));
        return;
      }
      const c = await ensureSeasonCharSaved(i);
      if (!c) return;
      const fd = new FormData();
      fd.append('file', file);
      try {
        const r = await API.postForm(`/api/dramas/${props.id}/seasons/${seasonId.value}/characters/${c.id}/image`, fd);
        c.image_url = r.url || '';
        ElementPlus.ElMessage.success(I18N.t('p.msgCharImageUploaded'));
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }
    async function removeSeasonCharImage(i) {
      const c = seasonChars.value[i];
      if (!c || !c.id) return;
      try {
        await API.del(`/api/dramas/${props.id}/seasons/${seasonId.value}/characters/${c.id}/image`);
        c.image_url = '';
        ElementPlus.ElMessage.success(I18N.t('p.msgCharImageRemoved'));
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }
    // 参考图 / AI 描述都挂在已落库的角色上：新增角色（id 为空）先保存本集角色再继续
    async function ensureSeasonCharSaved(i) {
      let c = seasonChars.value[i];
      if (!c) return null;
      if (c.id) return c;
      // saveSeasonChars 失败时内部已提示并返回 null，这里不重复弹
      const saved = await saveSeasonChars();
      if (!saved) return null;
      c = saved[i];                      // load() 后是后端返回的新对象，顺序不变
      if (!c || !c.id) {
        ElementPlus.ElMessage.warning(I18N.t('p.msgCharSaveFirst'));
        return null;
      }
      return c;
    }
    async function genFirst(includeTitle = true) {
      busyFirst.value = true;
      try {
        await API.post(`/api/dramas/${props.id}/first-image/generate`,
          { prompt: coverPrompt.value, include_title: includeTitle !== false }, 0);
        ElementPlus.ElMessage.success(I18N.t('p.msgFirstGenerated'));
        coverPrompt.value = '';
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        await load();
      } finally {
        busyFirst.value = false;
      }
    }
    async function genSeasonFirst(includeTitle = true) {
      busySeasonFirst.value = true;
      try {
        await API.post(`/api/dramas/${props.id}/seasons/${seasonId.value}/first-image/generate`,
          { prompt: seasonCoverPrompt.value, include_title: includeTitle !== false }, 0);
        ElementPlus.ElMessage.success(I18N.t('p.msgSeasonFirstGenerated'));
        seasonCoverPrompt.value = '';
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        await load();
      } finally {
        busySeasonFirst.value = false;
      }
    }

    // ---------------- 生成封面弹框（包含标题勾选） ----------------
    function openCoverGenDlg(target) {
      coverGenDlg.target = target;
      coverGenDlg.includeTitle = true;
      coverGenDlg.show = true;
    }
    function confirmCoverGen() {
      coverGenDlg.show = false;
      if (coverGenDlg.target === 'season') genSeasonFirst(coverGenDlg.includeTitle);
      else genFirst(coverGenDlg.includeTitle);
    }

    // ---------------- 叠加标题弹框（位置自由拖动 + 字号/样式/颜色） ----------------
    function openOvlDlg(target) {
      ovlDlg.target = target;
      if (target === 'season') {
        const s = curSeason.value;
        ovlDlg.titleText = (s && s.title) || I18N.t('d.ep', (s && s.number) || 1);
        ovlDlg.coverUrl = (s && s.first_image_url) || '';
      } else {
        ovlDlg.titleText = (data.value && data.value.project.title) || '';
        ovlDlg.coverUrl = (data.value && data.value.project.first_image_url) || '';
      }
      ovlDlg.x = 0.5; ovlDlg.y = 1 / 3; ovlDlg.sizePct = 8;
      ovlDlg.style = 'bold_outline'; ovlDlg.color = '#ffffff'; ovlDlg.band = true;
      ovlDlg.show = true;
    }
    async function applyOvl() {
      ovlBusy.value = true;
      const body = { x: ovlDlg.x, y: ovlDlg.y, size_pct: ovlDlg.sizePct,
                     style: ovlDlg.style, color: ovlDlg.color, band: ovlDlg.band };
      try {
        const url = ovlDlg.target === 'season'
          ? `/api/dramas/${props.id}/seasons/${seasonId.value}/first-image/overlay-title`
          : `/api/dramas/${props.id}/first-image/overlay-title`;
        await API.post(url, body, 0);
        ElementPlus.ElMessage.success(I18N.t('p.msgOverlayTitle'));
        ovlDlg.show = false;
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        ovlBusy.value = false;
      }
    }
    async function planChapters(opts = {}) {
      // 片段规划（逐段）：先定总段数再逐段规划（每段参考前段承接剧情），SSE 实时逐个补入。
      // 方式：append（新增）= 不清空，现有片段之后续加；replan（覆盖）= 未勾选=全集覆盖重规划，勾选=仅重写所选。
      // opts.mode 覆盖弹框方式；opts.indices 非空则只规划这些已有片段（批量重新规划，不清空其它/不新增）。
      if (!seasonId.value) {
        ElementPlus.ElMessage.warning(I18N.t('d.epAdd'));
        return;
      }
      if (!(data.value?.project?.arc || '').trim() && !(seasonArcText.value || '').trim()) {
        ElementPlus.ElMessage.warning(I18N.t('d.planNeedArcB'));
        return;
      }
      const mode = opts.mode || planMode.value;
      const sel = (opts.indices != null ? opts.indices.slice() : selected.value.slice()).sort((a, b) => a - b);
      const subset = sel.length > 0;
      const isAppend = mode === 'append';
      actBusy.value = true; progress.text = '';
      if (!isAppend && !subset) {
        // 全集覆盖：先清空本集旧片段（媒体保留，预览重新生成），SSE 逐个补入
        data.value.chapters = data.value.chapters.filter(c => c.season_id !== seasonId.value);
        cur.value = 0;
      }
      const ctrl = new AbortController(); sseCtrl = ctrl;
      try {
        await API.sse(`/api/dramas/${props.id}/action-stream`, {
          step: 'chapters',
          season_id: seasonId.value,
          count_min: planCount.value,
          count_max: planCount.value,
          mode: isAppend ? 'append' : 'replan',
          ...(isAppend ? {} : (subset ? { indices: sel } : {})),
        }, (ev, d) => {
          if (ev === EVENTS.PROGRESS) progress.text = I18N.t('d.planProgress', d.current, d.total, d.title);
          else if (ev === EVENTS.CHAPTER) applyChapterPlan(d);
          else if (ev === EVENTS.ERROR) throw new Error(d.message);
        }, ctrl.signal);
        ElementPlus.ElMessage.success(I18N.t('d.planSaved'));
        selected.value = [];
        await load();
      } catch (e) {
        if (!ctrl.signal.aborted) { ElementPlus.ElMessage.error(e.message); await load(); }
      }
      finally { actBusy.value = false; progress.text = ''; if (sseCtrl === ctrl) sseCtrl = null; }
    }
    // 工具条「片段规划 / 规划所选」：弹出规划弹框（数量 + 新增/覆盖 选择）
    function openPlanDlg() { planDlg.value = true; }
    function confirmPlan() { planDlg.value = false; planChapters(); }
    // 批量重新规划所选：仅对已选中的现有片段重写标题与摘要（保留已生成视频；不新增、不清空其它片段）
    async function planSelected() {
      if (!seasonId.value || !selected.value.length) return;
      await planChapters({ mode: 'replan', indices: selected.value });
    }
    // 批量删除所选片段（连同视频文件，其余片段重新编号）
    async function deleteSelected() {
      if (!seasonId.value || !selected.value.length) return;
      try {
        await API.post(`/api/dramas/${props.id}/chapters/delete-batch`, {
          season_id: seasonId.value,
          indices: selected.value.slice().sort((a, b) => a - b),
        });
        ElementPlus.ElMessage.success(I18N.t('d.delSelDone'));
        selected.value = [];
        await load();
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }

    // 分页保存：每个子页只提交自己的字段（后端 /outline 部分更新，未提交的字段不动）
    async function doSave(payload, okMsg) {
      busySave.value = true;
      try {
        await API.post(`/api/dramas/${props.id}/outline`, payload);
        ElementPlus.ElMessage.success(okMsg);
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        busySave.value = false;
      }
    }
    // 作品标题：失焦即保存（标题也可随「生成大纲」一并生成）
    async function saveTitle() {
      const t = (oTitle.value || '').trim();
      if (t === (data.value?.project?.title || '')) return;
      try {
        await API.post(`/api/dramas/${props.id}/rename`, { title: t });
        if (data.value) data.value.project.title = t;
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    // 总体（全局）：只保存全局字段（全局要求 / 分辨率）
    function saveStory() {
      busySave.value = true;
      (async () => {
        try {
          await API.post(`/api/dramas/${props.id}/outline`, {
            global_prompt: oGlobal.value, res_width: oW.value, res_height: oH.value,
          });
          ElementPlus.ElMessage.success(I18N.t('p.outSaved'));
          await load();
        } catch (e) { ElementPlus.ElMessage.error(e.message); }
        finally { busySave.value = false; }
      })();
    }
    // 企划（每季）：保存本季大纲
    function saveSeasonArc() {
      if (!seasonId.value) return;
      busySave.value = true;
      (async () => {
        try {
          await API.patch(`/api/dramas/${props.id}/seasons/${seasonId.value}`, { title: seasonTitleText.value, arc: seasonArcText.value });
          ElementPlus.ElMessage.success(I18N.t('d.epSaved'));
          await load();
        } catch (e) { ElementPlus.ElMessage.error(e.message); }
        finally { busySave.value = false; }
      })();
    }
    // 企划（每季）：保存本季角色
    function saveSeasonChars() {
      if (!seasonId.value) return Promise.resolve();
      busySave.value = true;
      // 返回 promise：新增角色存参考图 / AI 描述前需先落盘取 id（ensureSeasonCharSaved 会 await）
      return (async () => {
        try {
          await API.patch(`/api/dramas/${props.id}/seasons/${seasonId.value}`, {
            characters: seasonChars.value.map(c => ({ id: c.id, name: c.name, description: c.description })),
          });
          ElementPlus.ElMessage.success(I18N.t('d.epSaved'));
          await load();
          return seasonChars.value;   // 保存成功：把刷新后的角色交给调用方（ensureSeasonCharSaved）
        } catch (e) {
          ElementPlus.ElMessage.error(e.message);
          return null;               // 失败返回 null（不抛异常：按钮 @confirm 直接调用它）
        } finally { busySave.value = false; }
      })();
    }
    function savePlan() {
      if (!seasonId.value) return;
      busySave.value = true;
      (async () => {
        try {
          await API.patch(`/api/dramas/${props.id}/seasons/${seasonId.value}`, {
            count_mode: 'range',
            count_min: planCount.value,
            count_max: planCount.value,
            chapters: seasonChapters.value.map(c => ({ title: c.title, summary: c.summary })),
          });
          ElementPlus.ElMessage.success(I18N.t('d.planSaved'));
          await load();
        } catch (e) { ElementPlus.ElMessage.error(e.message); }
        finally { busySave.value = false; }
      })();
    }

    // 批量（SSE 进度）
    // 批量生成时单章两步完成：实时把该章提示词/媒体/状态写回本地并跟随定位，页面逐个刷新（后续章节仍会参考它）
    // 自动评分事件：刷新进度文案（评分中 / 低于阈值重做中 / 评分结果）
    function applyScoreLive(d) {
      if (d.phase === 'scoring') progress.text = I18N.t('p.scoreDoing', d.title);
      else if (d.phase === 'redo') progress.text = I18N.t('p.scoreRedoDo', d.title, d.redo, 2);
      else if (d.phase === 'stopped') {
        progress.text = I18N.t('p.scoreStopMsg', d.title, d.score);
        ElementPlus.ElMessage.warning(I18N.t('p.scoreStopMsg', d.title, d.score));
      }
      else if (d.phase === 'error') {
        progress.text = I18N.t('p.scoreFailMsg', d.title, d.note);
        ElementPlus.ElMessage.warning(I18N.t('p.scoreFailMsg', d.title, d.note));
      }
      else progress.text = I18N.t('p.scoreResult', d.title, d.score) + (d.note ? ' · ' + d.note : '');
    }
    function applyChapterLive(d) {
      const arr = data.value?.chapters || [];
      const pos = arr.findIndex(c => c.index === d.index && c.season_id === seasonId.value);
      if (pos < 0) return;
      const ch = arr[pos];
      ch.status = d.status;
      ch.error = d.error || '';
      if (d.media_url) ch.media_url = d.media_url;
      if (d.prompt !== undefined) ch.prompt = d.prompt;
      if (d.description !== undefined) ch.description = d.description;
      if (d.width) { ch.width = d.width; ch.height = d.height; }
      if (d.score !== undefined) { ch.score = d.score; ch.score_note = d.score_note || ''; }
      if (visibleChapters.value.some(c => c.index === d.index)) cur.value = d.index;
    }
    // 章节规划逐章回调：后端先清空旧章节，这里把规划出的章节按序号补入（新增或更新）并跟随定位到最新章节
    function applyChapterPlan(d) {
      const arr = data.value?.chapters || [];
      const pos = arr.findIndex(c => c.index === d.index && c.season_id === seasonId.value);
      const sec = (d.seconds === undefined || d.seconds === null) ? 0 : d.seconds;
      if (pos >= 0) {
        const ch = arr[pos];
        ch.title = d.title; ch.summary = d.summary; ch.status = d.status;
        if (sec > 0) ch.seconds = sec;                 // 规划时一并给出的建议时长
      } else {
        arr.push({ index: d.index, season_id: seasonId.value, title: d.title, summary: d.summary,
                   seconds: sec, status: d.status, description: '', prompt: '', media_url: '',
                   error: '', width: 0, height: 0 });
        arr.sort((a, b) => a.index - b.index);
      }
      if (visibleChapters.value.some(c => c.index === d.index)) cur.value = d.index;
    }
    // 停止批量生成：中断 SSE → 后端停止并提交已完成章节；页面同步刷新
    function stopGen() {
      if (sseCtrl) {
        sseCtrl.abort();
        ElementPlus.ElMessage.info(I18N.t('d.genStoppedDrama'));
      }
    }
    // 生成画面：勾选章节则只生成它们，未勾选则生成全部（两步连贯、逐章进行、后章参考前章已生成图）
    async function genAll() {
      if (!seasonId.value) return;
      busyGenAll.value = true; progress.text = '';
      const indices = selected.value.length ? selected.value : null;
      const ctrl = new AbortController(); sseCtrl = ctrl;
      try {
        await API.sse(`/api/dramas/${props.id}/action-stream`, { step: 'generate', season_id: seasonId.value, indices }, (ev, d) => {
          if (ev === EVENTS.PROGRESS) progress.text = I18N.t('d.genProgress', d.current, d.total, d.title);
          else if (ev === EVENTS.SCORE) applyScoreLive(d);
          else if (ev === EVENTS.CHAPTER) applyChapterLive(d);
          else if (ev === EVENTS.ERROR) throw new Error(d.message);
        }, ctrl.signal);
        ElementPlus.ElMessage.success(I18N.t('p.msgDone'));
        selected.value = [];
        await load();
      } catch (e) {
        if (ctrl.signal.aborted) { await load(); }  // 用户停止：后端已提交进度，同步刷新
        else { ElementPlus.ElMessage.error(e.message); await load(); }
      }
      finally { busyGenAll.value = false; progress.text = ''; if (sseCtrl === ctrl) sseCtrl = null; }
    }
    // VLM 批量评分：勾选章节则只评它们，未勾选则评全部（逐章进行；单章失败不阻塞后续）
    async function scoreAll() {
      if (!seasonId.value) return;
      busyScoreAll.value = true; progress.text = '';
      const indices = selected.value.length ? selected.value : null;
      const ctrl = new AbortController(); sseCtrl = ctrl;
      try {
        await API.sse(`/api/dramas/${props.id}/action-stream`, { step: 'score', season_id: seasonId.value, indices }, (ev, d) => {
          if (ev === EVENTS.PROGRESS) progress.text = I18N.t('d.scoreProgress', d.current, d.total, d.title);
          else if (ev === EVENTS.SCORE) applyScoreLive(d);
          else if (ev === EVENTS.CHAPTER) applyChapterLive(d);
          else if (ev === EVENTS.ERROR) throw new Error(d.message);
        }, ctrl.signal);
        ElementPlus.ElMessage.success(I18N.t('p.msgDone'));
        selected.value = [];
        await load();
      } catch (e) {
        if (ctrl.signal.aborted) { await load(); }  // 用户停止：后端已提交进度，同步刷新
        else { ElementPlus.ElMessage.error(e.message); await load(); }
      }
      finally { busyScoreAll.value = false; progress.text = ''; if (sseCtrl === ctrl) sseCtrl = null; }
    }

    // 批量清空章节产物：勾选章节则只清它们，未勾选则清全部（清理产物/出视频提示词/评分，保留标题/摘要/剧本）
    async function clearAll() {
      if (!seasonId.value) return;
      try {
        const indices = selected.value.length ? selected.value : null;
        await API.post(`/api/dramas/${props.id}/chapters/clear`, { season_id: seasonId.value, indices });
        ElementPlus.ElMessage.success(I18N.t('p.clearedAll'));
        selected.value = [];
        await load();
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }

    // 导出作用域为当前所选季（「完成」页签仅在选定季时可用）
    // 统一走 API.download：浏览器 = 常规下载；CS 桌面客户端 = 系统「另存为」对话框
    async function exportMedia(fmt) {
      if (exporting.value) return;
      exporting.value = true;
      try {
        await API.download(`/api/dramas/${props.id}/export/${fmt}?season_id=${encodeURIComponent(seasonId.value)}`);
        API.notify(I18N.t('app.title'), I18N.t('p.exportDone'));
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        exporting.value = false;
      }
    }
    function exportZip() { return exportMedia('zip'); }
    // 导出合成视频：把该季各章视频合成为一个 mp4（可能较慢，走系统「另存为」/下载）
    function exportVideo() { return exportMedia('video'); }
    // 预览合成视频：弹框内 <video> 播放 inline mp4（前端不下载）
    function previewVideo() {
      videoUrl.value = `/api/dramas/${props.id}/export/video?season_id=${encodeURIComponent(seasonId.value)}&preview=1`;
      videoDlg.value = true;
    }

    async function delChapter(i) {
      if (!seasonId.value) return;
      try {
        await API.del(`/api/dramas/${props.id}/chapters/${i}?season_id=${seasonId.value}`);
        await load(); // load() 内 syncCur() 会把选中钳制到有效范围
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    function onChapterReloaded(index) {
      if (typeof index === 'number' && index >= 0) cur.value = index; // 生成/写剧本后切到该章
      load();
    }

    function openCfg() {
      cfg.llm = data.value.project.llm_config_id;
      cfg.dt = data.value.project.drawthings_config_id;
      const c = data.value.drawthing_configs.find(x => x.id === cfg.dt);
      const p = data.value.project;
      cfg.dt_model_i = p.dt_model_image || (c ? (c.model_image || '') : '');
      cfg.dt_model_v = p.dt_model_video || (c ? (c.model_video || '') : '');
      cfg.dt_ref_i = (p.dt_ref_image === '1') || (p.dt_ref_image === '' && !!c && !!c.ref_image);
      cfg.dt_ref_v = (p.dt_ref_video === '1') || (p.dt_ref_video === '' && !!c && !!c.ref_video);
      cfg.dt_steps_i = p.dt_max_steps_image || 0;
      cfg.dt_steps_v = p.dt_max_steps_video || 0;
      // 自动评分设置（原「总体 → 自动评分」子页签，已并入本弹框）
      cfg.score = !!p.auto_score;
      cfg.score_min = p.score_min || 60;
      cfg.redo = !!p.auto_redo;
      cfg.stop_low = !!p.stop_on_low;
      fetchModels();
      cfgDlg.value = true;
    }
    async function saveCfg() {
      cfgBusy.value = true;
      try {
        await API.post(`/api/dramas/${props.id}/config`, {
          llm_config_id: cfg.llm, drawthings_config_id: cfg.dt,
          dt_model_image: cfg.dt_model_i,
          dt_model_video: cfg.dt_model_v,
          dt_ref_image: cfg.dt_ref_i ? 1 : 0,
          dt_ref_video: cfg.dt_ref_v ? 1 : 0,
          dt_max_steps_image: cfg.dt_steps_i,
          dt_max_steps_video: cfg.dt_steps_v,
          auto_score: cfg.score, score_min: cfg.score_min,
          auto_redo: cfg.redo, stop_on_low: cfg.stop_low,
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

    function openLb(list, idx) {
      lb.list = list;
      lb.idx = idx;
      lb.show = true;
    }

    onMounted(load);
    watch(() => props.id, () => { tabInit.value = false; load(); });  // 同一路由切换不同项目时重载
    onBeforeUnmount(() => { if (sseCtrl) { sseCtrl.abort(); sseCtrl = null; } });
    return {
      data, tab, oSub, isOverall, cur, curCh, selected, allSelected, scoreFilter, setScoreFilter, scoreFilterOptions, visibleChapters,
      seasons, seasonId, seasonArcText, seasonTitleText, seasonChars, seasonChapters, seasonDoneCount, epBeats, curRefKind, setBatchSeconds, seasonCompleted,
      selectSeason, addSeason, delSeason, curSeason, addSeasonChar, delSeasonChar, genSeasonCharDesc, uploadSeasonCharImage, removeSeasonCharImage,
      actBusy, busySave, busyGenAll, busyScoreAll, exporting, busyFirst, busySeasonFirst, genDescBusy, coverPrompt, seasonCoverPrompt, progress,
      coverGenDlg, ovlDlg, ovlBusy, pvItems, pvUrls, gotoChapter, pickChapter,
      oGlobal, oW, oH, oTitle, saveTitle, planCount, planMode, planDlg, planDlgTitle, planModeHint, openPlanDlg, confirmPlan, planSelected, deleteSelected,
      cfgDlg, cfgBusy, cfg, imgChoices, vidChoices, lb, genDlg, genDlgTitle, genDlgExtra,
      openGenDlg, confirmGen, genFirst, genSeasonFirst, openCoverGenDlg, confirmCoverGen, openOvlDlg, applyOvl, planChapters, saveStory, saveSeasonArc, saveSeasonChars, savePlan, doAction, genAll, scoreAll, clearAll, stopGen, isSel, toggleSelect, toggleAllSelect,
      exportZip, exportVideo, previewVideo, videoDlg, videoUrl, delChapter, onChapterReloaded,
      openCfg, saveCfg, openLb, load, backTo, router,
    };
  },
};
