// 章节卡片（手风琴）· 短剧（视频）版：折叠=缩略图占位+标题+摘要；展开=标题/摘要/剧本/提示词/分辨率 + 多步操作
//（生成画面/一次保存全字段/上移/下移/删除）；保存时标题/摘要交给父级保存季规划（saveplan 事件）。
// 与漫画版（chapter-card-comic.js）完全独立：本卡片媒体固定为视频（video），不含任何图片预览逻辑。
window.Views = window.Views || {};
Views.chapterCardDrama = {
  props: {
    chapter: { type: Object, required: true },
    projectId: { type: String, required: true },
    seasonId: { type: String, default: '' },
    seasonIndex: { type: Number, default: 0 },
    expanded: { type: Boolean, default: false },
    noToggle: { type: Boolean, default: false },    // 主从布局：常显详情、不可折叠
    locked: { type: Boolean, default: false },      // 作品已完结（锁定）：操作只读
    isFirst: { type: Boolean, default: false },
    isLast: { type: Boolean, default: false },
    defW: { type: Number, default: 0 },             // 总体默认分辨率（仅显示，不可在此修改）
    defH: { type: Number, default: 0 },
    scoreMin: { type: Number, default: 60 },        // 评分阈值（分值标签配色用）
    beats: { type: Array, default: () => [] },      // 本集关键剧情节点（按比例落位到本段）
    clipCount: { type: Number, default: 0 },        // 本集片段总数（用于节点按比例落位）
    refKind: { type: String, default: '' },         // 本段参考来源：prev|epcover|prevep|next|none
  },
  emits: ['preview', 'reloaded', 'toggle', 'saveplan'],
  template: `
    <el-card class="chapter" shadow="never" :class="{ 'is-expanded': expanded }">
      <div class="ch-head" :class="{ 'no-toggle': noToggle }" @click="!noToggle && $emit('toggle')">
        <div class="ch-thumb">
          <div class="ch-thumbph"><span class="ph-ico">🎬</span></div>
        </div>
        <div class="ch-headtitle">
          <b>{{ I18N.t('d.clip', chapter.index + 1) }} · {{ chapter.title }}</b>
          <span class="sub muted small">{{ summary || chapter.description || I18N.t('d.clipStatus.pending') }}</span>
        </div>
        <span class="ch-tags">
          <el-tag size="small" :type="statusType" effect="light">{{ statusLabel }}</el-tag>
          <el-tag v-if="chapter.seconds > 0" size="small" type="info" effect="plain">{{ chapter.seconds }}s</el-tag>
          <el-tag v-if="beat" size="small" type="warning" effect="plain" :title="beat">{{ I18N.t('d.beat') }}</el-tag>
        </span>
        <span class="ch-score" :class="scoreTone">{{ chapter.score > 0 ? (chapter.score + I18N.t('p.scoreUnit')) : I18N.t('d.clipScoreNone') }}</span>
        <span v-if="!noToggle" class="ch-chev">{{ expanded ? '⌄' : '›' }}</span>
      </div>

      <div v-show="expanded" class="ch-detail">
        <div class="ch-media">
          <video v-if="done && chapter.media_url" :src="chapter.media_url" controls preload="metadata"></video>
          <el-empty v-else :description="I18N.t('d.clipStatus.pending')" :image-size="48" />
        </div>
        <div class="ch-detail-body">
          <el-alert v-if="chapter.status === 'error'" type="error" :closable="false" class="mb8"
                    :title="I18N.t('d.clipFail', chapter.error)" />
          <!-- 连贯 / 参考信息：本段的参考来源与剧情节点落位 -->
          <div class="clip-cont">
            <el-tag size="small" effect="plain" :type="contType">{{ contLabel }}</el-tag>
            <span class="muted small">{{ I18N.t('d.contHint') }}</span>
            <template v-if="beat">
              <el-divider direction="vertical" />
              <span class="muted small">{{ I18N.t('d.beat') }}：</span>
              <span class="small">{{ beat }}</span>
            </template>
          </div>
          <div class="muted small">{{ I18N.t('d.clipPlanTitle') }}</div>
          <el-input v-model="title" size="small" :readonly="locked" :placeholder="I18N.t('d.clipPlanTitle')" />
          <div class="ch-tabs">
            <button type="button" class="ch-tab" :class="{ active: dTab === 'summary' }"
                    @click="dTab = 'summary'">{{ I18N.t('d.clipPlanSummary') }}</button>
            <button type="button" class="ch-tab" :class="{ active: dTab === 'script' }"
                    @click="dTab = 'script'">{{ I18N.t('d.clipScript') }}</button>
            <button type="button" class="ch-tab" :class="{ active: dTab === 'prompt' }"
                    @click="dTab = 'prompt'">{{ I18N.t('d.clipPromptTab') }}</button>
          </div>
          <div class="ch-tab-body">
            <el-input v-if="dTab === 'summary'" v-model="summary" type="textarea" :rows="5"
                      :readonly="locked" :placeholder="I18N.t('d.clipPlanSummary')" />
            <p v-else-if="dTab === 'script'" class="desc">{{ chapter.description || '—' }}</p>
            <template v-else>
              <div class="res-row">
                <div class="muted small">{{ I18N.t('d.clipPrompt') }}</div>
                <span style="margin-left:auto;"></span>
                <el-button size="small" text :disabled="!prompt" @click="copyPrompt">{{ I18N.t('d.clipPromptCopy') }}</el-button>
                <el-button size="small" text :disabled="!prompt || locked" @click="promptReset">{{ I18N.t('d.clipPromptReset') }}</el-button>
              </div>
              <el-input v-model="prompt" type="textarea" :rows="6" :readonly="locked"
                        :placeholder="I18N.t('d.clipPromptDefault')" />
            </template>
          </div>
          <div class="res-row">
            <span class="muted small">{{ I18N.t('d.clipResolution') }}</span>
            <span>{{ defW }}×{{ defH }}</span>
          </div>
          <div class="res-row">
            <span class="muted small">{{ I18N.t('d.clipSeconds') }}</span>
            <el-input-number v-model="seconds" :min="0" :max="10" size="small" :disabled="locked" style="width: 120px" />
            <span class="muted small">{{ I18N.t('d.clipSecondsHint') }}</span>
          </div>
          <div class="res-row">
            <span class="muted small">{{ I18N.t('d.clipScore') }}</span>
            <el-button size="small" :loading="busy === 'score'" :disabled="locked" @click="doScore">{{ I18N.t('p.vlmScore') }}</el-button>
            <span v-if="chapter.score_note" class="muted small" style="margin-left:6px;">{{ chapter.score_note }}</span>
          </div>
          <div class="actions">
            <el-button size="small" type="primary" :loading="busy === 'gen'" :disabled="locked" @click="openGen">
              {{ done ? I18N.t('d.clipRegen') : I18N.t('d.clipGen') }}
            </el-button>
            <el-popconfirm :title="I18N.t('d.clipSaveConfirm')" @confirm="doSave">
              <template #reference><el-button size="small" :loading="busy === 'save'" :disabled="locked">{{ I18N.t('d.clipSave') }}</el-button></template>
            </el-popconfirm>
            <el-popconfirm :title="I18N.t('d.clipMoveUpConfirm')" @confirm="doMove('up')">
              <template #reference><el-button size="small" :disabled="isFirst || locked">{{ I18N.t('d.clipMoveUp') }}</el-button></template>
            </el-popconfirm>
            <el-popconfirm :title="I18N.t('d.clipMoveDownConfirm')" @confirm="doMove('down')">
              <template #reference><el-button size="small" :disabled="isLast || locked">{{ I18N.t('d.clipMoveDown') }}</el-button></template>
            </el-popconfirm>
            <el-popconfirm :title="I18N.t('d.clipClearConfirm')" @confirm="doClear">
              <template #reference><el-button size="small" :loading="busy === 'clear'" :disabled="locked">{{ I18N.t('d.clipClear') }}</el-button></template>
            </el-popconfirm>
            <el-popconfirm :title="I18N.t('d.clipDeleteConfirm')" @confirm="doDelete">
              <template #reference><el-button size="small" type="danger" plain :disabled="locked">{{ I18N.t('d.clipDelete') }}</el-button></template>
            </el-popconfirm>
          </div>
        </div>
      </div>
      <!-- 重新生成片段：可填写补充提示词，人工修正/补充画面问题 -->
      <el-dialog v-model="genDlg" :title="done ? I18N.t('d.clipRegen') : I18N.t('d.clipGen')"
                 width="520px" append-to-body>
        <p class="hint mb8">{{ I18N.t('d.clipGenExtraHint') }}</p>
        <el-input v-model="genExtra" type="textarea" :rows="4" :placeholder="I18N.t('d.clipGenExtraPh')" />
        <template #footer>
          <el-button @click="genDlg = false">{{ I18N.t('p.cancel') }}</el-button>
          <el-button type="primary" :loading="busy === 'gen'" @click="confirmGen">{{ I18N.t('p.confirm') }}</el-button>
        </template>
      </el-dialog>
    </el-card>
  `,
  setup(props, { emit }) {
    const busy = ref('');
    const dTab = ref('summary');                 // 卡片内子页签：summary | script | prompt
    const title = ref(props.chapter.title || '');
    const summary = ref(props.chapter.summary || '');
    const prompt = ref(props.chapter.prompt || '');
    const seconds = ref(parseInt(props.chapter.seconds, 10) || 0);   // 短剧：本章时长（秒；0=跟随配置上限）
    const done = computed(() => props.chapter.status === 'done');
    const statusLabel = computed(() => I18N.t('d.clipStatus.' + props.chapter.status) || props.chapter.status);
    const statusType = computed(() =>
      props.chapter.status === 'done' ? 'success' : (props.chapter.status === 'error' ? 'danger' : 'info'));
    // 头部独立评分徽标：达标绿 / 低于阈值橙 / 未评分灰
    const scoreTone = computed(() => {
      const s = props.chapter.score;
      if (!(s > 0)) return 'none';
      return s >= props.scoreMin ? 'ok' : 'low';
    });
    // 本段对应的关键剧情节点：按 序号/总段数 比例落位（第 ⌈i/n × 节点数⌉ 个）
    const beat = computed(() => {
      const n = props.beats || [];
      if (!n.length) return '';
      const total = props.clipCount || 0;
      const i = props.seasonIndex + 1;                       // 段序号（1 起）
      if (total <= 0) return '';
      const k = Math.min(n.length, Math.max(1, Math.ceil(i / total * n.length)));
      return n[k - 1] || '';
    });
    // 参考来源标签（连贯信息）
    const contLabel = computed(() => {
      switch (props.refKind) {
        case 'epcover': return I18N.t('d.contEpCover');
        case 'prevep': return I18N.t('d.contPrevEp');
        case 'next': return I18N.t('d.contNextClip');
        case 'prev': return I18N.t('d.contPrevFrom');
        default: return I18N.t('d.contNone');
      }
    });
    const contType = computed(() => (props.refKind && props.refKind !== 'none' ? 'success' : 'info'));
    // 重新生成画面：弹框补充提示词（人工修正/补充画面问题）
    const genDlg = ref(false);
    const genExtra = ref('');
    function openGen() { genDlg.value = true; }
    // 父级重新加载（生成/清空后）时同步服务端最新值：提示词与时长（含「生成片段」由 LLM 写回的时长）
    watch(() => props.chapter.prompt, v => { prompt.value = v || ''; });
    watch(() => props.chapter.seconds, v => { seconds.value = parseInt(v, 10) || 0; });
    async function confirmGen() { genDlg.value = false; await doGen(); }
    // 提示词：复制到剪贴板 / 还原为服务端最新值
    async function copyPrompt() {
      const text = (prompt.value || '').trim();
      if (!text) return;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
        } else {
          const ta = document.createElement('textarea');
          ta.value = text; document.body.appendChild(ta); ta.select();
          document.execCommand('copy'); ta.remove();
        }
        ElementPlus.ElMessage.success(I18N.t('d.clipPromptCopied'));
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    function promptReset() { prompt.value = props.chapter.prompt || ''; }

    async function doGen() {
      busy.value = 'gen';
      try {
        await API.post(`/api/dramas/${props.projectId}/gen/${props.seasonIndex}`,
                       { season_id: props.seasonId, extra_prompt: genExtra.value || '' }, 0);
        emit('reloaded', props.seasonIndex);
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        emit('reloaded');
      } finally { busy.value = ''; }
    }
    // 一次「保存」提交本段全字段：提示词走片段编辑接口；标题/摘要写回集列表并交给父级保存规划（saveplan）
    async function doSave() {
      busy.value = 'save';
      try {
        await API.post(`/api/dramas/${props.projectId}/edit/${props.seasonIndex}`,
                       { season_id: props.seasonId, prompt: prompt.value, seconds: seconds.value });
        props.chapter.prompt = prompt.value;
        props.chapter.title = title.value;
        props.chapter.summary = summary.value;
        props.chapter.seconds = seconds.value;
        emit('saveplan');
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally { busy.value = ''; }
    }
    // VLM 手动评分：调用 VLM 给本段评分，成功后刷新分值/评语
    async function doScore() {
      busy.value = 'score';
      try {
        const r = await API.post(`/api/dramas/${props.projectId}/chapters/${props.seasonIndex}/score`,
                                 { season_id: props.seasonId });
        props.chapter.score = (r && r.score) || 0;
        props.chapter.score_note = (r && r.note) || '';
        ElementPlus.ElMessage.success(I18N.t('p.scoreResult', props.chapter.title, props.chapter.score));
        emit('reloaded');
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
      finally { busy.value = ''; }
    }
    async function doMove(dir) {
      try {
        await API.post(`/api/dramas/${props.projectId}/chapters/${props.seasonIndex}/move`, { season_id: props.seasonId, direction: dir });
        emit('reloaded');
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    async function doDelete() {
      try {
        await API.del(`/api/dramas/${props.projectId}/chapters/${props.seasonIndex}?season_id=${props.seasonId}`);
        emit('reloaded');
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    // 清空本段产物：清掉媒体、出视频提示词与评分（保留标题/摘要/剧本），成功后重置本地展示
    async function doClear() {
      busy.value = 'clear';
      try {
        await API.post(`/api/dramas/${props.projectId}/chapters/${props.seasonIndex}/clear`,
                       { season_id: props.seasonId });
        props.chapter.media_path = '';
        props.chapter.media_url = '';
        props.chapter.prompt = '';
        props.chapter.score = 0;
        props.chapter.score_note = '';
        props.chapter.status = 'pending';
        props.chapter.error = '';
        prompt.value = '';
        ElementPlus.ElMessage.success(I18N.t('d.clipCleared'));
        emit('reloaded');
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
      finally { busy.value = ''; }
    }

    return { busy, dTab, title, summary, prompt, seconds, done, statusLabel, statusType, scoreTone,
             beat, contLabel, contType, copyPrompt, promptReset,
             genDlg, genExtra, openGen, confirmGen,
             doGen, doSave, doScore, doMove, doClear, doDelete };
  },
};
