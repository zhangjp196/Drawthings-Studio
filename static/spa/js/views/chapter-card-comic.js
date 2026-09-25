// 章节卡片（手风琴）· 漫画版：折叠=缩略图+标题+摘要；展开=标题/摘要/剧本/提示词/分辨率 + 多步操作
//（生成画面/一次保存全字段/上移/下移/删除）；保存时标题/摘要交给父级保存季规划（saveplan 事件）。
// 与短剧版（chapter-card-drama.js）完全独立：本卡片媒体固定为图片（img），不含任何视频逻辑。
window.Views = window.Views || {};
Views.chapterCardComic = {
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
  },
  emits: ['preview', 'reloaded', 'toggle', 'saveplan'],
  template: `
    <el-card class="chapter" shadow="never" :class="{ 'is-expanded': expanded }">
      <div class="ch-head" :class="{ 'no-toggle': noToggle }" @click="!noToggle && $emit('toggle')">
        <div class="ch-thumb">
          <img v-if="done && chapter.media_url" :src="chapter.media_url" :alt="chapter.title"
               loading="lazy" @click.stop="$emit('preview', chapter.media_url)">
          <div v-else class="ch-thumbph"><span class="ph-ico">🖼</span></div>
        </div>
        <div class="ch-headtitle">
          <b>{{ I18N.t('p.ch', chapter.index + 1) }} · {{ chapter.title }}</b>
          <span class="sub muted small">{{ summary || chapter.description || I18N.t('p.chPending') }}</span>
        </div>
        <span class="ch-tags">
          <el-tag size="small" :type="statusType" effect="light">{{ statusLabel }}</el-tag>
        </span>
        <span class="ch-score" :class="scoreTone">{{ chapter.score > 0 ? (chapter.score + I18N.t('p.scoreUnit')) : I18N.t('p.scoreNone') }}</span>
        <span v-if="!noToggle" class="ch-chev">{{ expanded ? '⌄' : '›' }}</span>
      </div>

      <div v-show="expanded" class="ch-detail">
        <div class="ch-media">
          <img v-if="done && chapter.media_url" :src="chapter.media_url" :alt="chapter.title"
               @click="$emit('preview', chapter.media_url)">
          <el-empty v-else :description="I18N.t('p.chPending')" :image-size="48" />
        </div>
        <div class="ch-detail-body">
          <el-alert v-if="chapter.status === 'error'" type="error" :closable="false" class="mb8"
                    :title="I18N.t('p.chFail', chapter.error)" />
          <div class="muted small">{{ I18N.t('p.chPlanTitle') }}</div>
          <el-input v-model="title" size="small" :readonly="locked" :placeholder="I18N.t('p.chPlanTitle')" />
          <div class="ch-tabs">
            <button type="button" class="ch-tab" :class="{ active: dTab === 'summary' }"
                    @click="dTab = 'summary'">{{ I18N.t('p.chPlanSummary') }}</button>
            <button type="button" class="ch-tab" :class="{ active: dTab === 'script' }"
                    @click="dTab = 'script'">{{ I18N.t('p.chScript') }}</button>
            <button type="button" class="ch-tab" :class="{ active: dTab === 'prompt' }"
                    @click="dTab = 'prompt'">{{ I18N.t('p.chPromptTab') }}</button>
          </div>
          <div class="ch-tab-body">
            <el-input v-if="dTab === 'summary'" v-model="summary" type="textarea" :rows="5"
                      :readonly="locked" :placeholder="I18N.t('p.chPlanSummary')" />
            <p v-else-if="dTab === 'script'" class="desc">{{ chapter.description || '—' }}</p>
            <template v-else>
              <div class="muted small mb8">{{ I18N.t('p.chPrompt') }}</div>
              <el-input v-model="prompt" type="textarea" :rows="5" :readonly="locked" />
            </template>
          </div>
          <div class="res-row">
            <span class="muted small">{{ I18N.t('p.chResolution') }}</span>
            <span>{{ defW }}×{{ defH }}</span>
            <span class="muted small" style="margin-left:6px;">{{ I18N.t('p.planResHint') }}</span>
          </div>
          <div class="res-row">
            <span class="muted small">{{ I18N.t('p.chScore') }}</span>
            <el-button size="small" :loading="busy === 'score'" :disabled="locked" @click="doScore">{{ I18N.t('p.vlmScore') }}</el-button>
            <span v-if="chapter.score_note" class="muted small" style="margin-left:6px;">{{ chapter.score_note }}</span>
          </div>
          <div class="actions">
            <el-popconfirm :title="I18N.t('p.chGenConfirm')" @confirm="doGen">
              <template #reference>
                <el-button size="small" type="primary" :loading="busy === 'gen'" :disabled="locked">
                  {{ done ? I18N.t('p.chRegen') : I18N.t('p.chGen') }}
                </el-button>
              </template>
            </el-popconfirm>
            <el-popconfirm :title="I18N.t('p.chSaveConfirm')" @confirm="doSave">
              <template #reference><el-button size="small" :loading="busy === 'save'" :disabled="locked">{{ I18N.t('p.chSave') }}</el-button></template>
            </el-popconfirm>
            <el-popconfirm :title="I18N.t('p.chMoveUpConfirm')" @confirm="doMove('up')">
              <template #reference><el-button size="small" :disabled="isFirst || locked">{{ I18N.t('p.chMoveUp') }}</el-button></template>
            </el-popconfirm>
            <el-popconfirm :title="I18N.t('p.chMoveDownConfirm')" @confirm="doMove('down')">
              <template #reference><el-button size="small" :disabled="isLast || locked">{{ I18N.t('p.chMoveDown') }}</el-button></template>
            </el-popconfirm>
            <el-popconfirm :title="I18N.t('p.chDeleteConfirm')" @confirm="doDelete">
              <template #reference><el-button size="small" type="danger" plain :disabled="locked">{{ I18N.t('p.chDelete') }}</el-button></template>
            </el-popconfirm>
          </div>
        </div>
      </div>
    </el-card>
  `,
  setup(props, { emit }) {
    const busy = ref('');
    const dTab = ref('summary');                 // 卡片内子页签：summary | script | prompt
    const title = ref(props.chapter.title || '');
    const summary = ref(props.chapter.summary || '');
    const prompt = ref(props.chapter.prompt || '');
    const done = computed(() => props.chapter.status === 'done');
    const statusLabel = computed(() => I18N.t('p.chStatus.' + props.chapter.status) || props.chapter.status);
    const statusType = computed(() =>
      props.chapter.status === 'done' ? 'success' : (props.chapter.status === 'error' ? 'danger' : 'info'));
    // 头部独立评分徽标：达标绿 / 低于阈值橙 / 未评分灰
    const scoreTone = computed(() => {
      const s = props.chapter.score;
      if (!(s > 0)) return 'none';
      return s >= props.scoreMin ? 'ok' : 'low';
    });

    async function doGen() {
      busy.value = 'gen';
      try {
        await API.post(`/api/projects/${props.projectId}/gen/${props.seasonIndex}`, { season_id: props.seasonId }, 0);
        emit('reloaded', props.seasonIndex);
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        emit('reloaded');
      } finally { busy.value = ''; }
    }
    // 一次「保存」提交本章全字段：提示词走章节编辑接口；标题/摘要写回季列表并交给父级保存规划（saveplan）
    async function doSave() {
      busy.value = 'save';
      try {
        await API.post(`/api/projects/${props.projectId}/edit/${props.seasonIndex}`,
                       { season_id: props.seasonId, prompt: prompt.value });
        props.chapter.prompt = prompt.value;
        props.chapter.title = title.value;
        props.chapter.summary = summary.value;
        emit('saveplan');
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally { busy.value = ''; }
    }
    // VLM 自动评分：调用 VLM 重新给本章评分（与流水线自动评分同款），成功后刷新分值/评语
    async function doScore() {
      busy.value = 'score';
      try {
        const r = await API.post(`/api/projects/${props.projectId}/chapters/${props.seasonIndex}/score`,
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
        await API.post(`/api/projects/${props.projectId}/chapters/${props.seasonIndex}/move`, { season_id: props.seasonId, direction: dir });
        emit('reloaded');
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    async function doDelete() {
      try {
        await API.del(`/api/projects/${props.projectId}/chapters/${props.seasonIndex}?season_id=${props.seasonId}`);
        emit('reloaded');
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }

    return { busy, dTab, title, summary, prompt, done, statusLabel, statusType, scoreTone,
             doGen, doSave, doScore, doMove, doDelete };
  },
};
