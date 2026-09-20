// 章节卡片（手风琴）：折叠=只显照片缩略图+标题；展开=剧本/提示词/分辨率 + 多步操作（生成剧本/生成画面/重生成/保存/上下移/删除）
window.Views = window.Views || {};
Views.chapterCard = {
  props: {
    chapter: { type: Object, required: true },
    kind: { type: String, required: true },      // comic | drama
    projectId: { type: String, required: true },
    seasonId: { type: String, default: '' },
    seasonIndex: { type: Number, default: 0 },
    expanded: { type: Boolean, default: false },
    noToggle: { type: Boolean, default: false },    // 主从布局：常显详情、不可折叠
    isFirst: { type: Boolean, default: false },
    isLast: { type: Boolean, default: false },
    defW: { type: Number, default: 0 },             // 总体默认分辨率（仅显示，不可在此修改）
    defH: { type: Number, default: 0 },
  },
  emits: ['preview', 'reloaded', 'toggle'],
  template: `
    <el-card class="chapter" shadow="never" :class="{ 'is-expanded': expanded }">
      <div class="ch-head" :class="{ 'no-toggle': noToggle }" @click="!noToggle && $emit('toggle')">
        <div class="ch-thumb">
          <img v-if="done && kind === 'comic' && chapter.media_url" :src="chapter.media_url" :alt="chapter.title"
               loading="lazy" @click.stop="$emit('preview', chapter.media_url)">
          <div v-else class="ch-thumbph"><span class="ph-ico">{{ kind === 'drama' ? '🎬' : '🖼' }}</span></div>
        </div>
        <div class="ch-headtitle">
          <b>{{ I18N.t('p.ch', chapter.index + 1) }} · {{ chapter.title }}</b>
          <span class="sub muted small">{{ chapter.summary || chapter.description || I18N.t('p.chPending') }}</span>
        </div>
        <span class="ch-tags">
          <el-tag size="small" :type="statusType" effect="light">{{ statusLabel }}</el-tag>
        </span>
        <span v-if="!noToggle" class="ch-chev">{{ expanded ? '⌄' : '›' }}</span>
      </div>

      <div v-show="expanded" class="ch-detail">
        <div class="ch-media">
          <img v-if="done && kind === 'comic' && chapter.media_url" :src="chapter.media_url" :alt="chapter.title"
               @click="$emit('preview', chapter.media_url)">
          <video v-else-if="done && kind === 'drama' && chapter.media_url" :src="chapter.media_url" controls preload="metadata"></video>
          <el-empty v-else :description="I18N.t('p.chPending')" :image-size="48" />
        </div>
        <div class="ch-detail-body">
          <el-alert v-if="chapter.status === 'error'" type="error" :closable="false" class="mb8"
                    :title="I18N.t('p.chFail', chapter.error)" />
          <div v-if="chapter.summary" class="muted small">{{ I18N.t('p.chSummary') }}：{{ chapter.summary }}</div>
          <div class="muted small">{{ I18N.t('p.chScript') }}</div>
          <p class="desc">{{ chapter.description || chapter.summary || '—' }}</p>
          <div class="muted small">{{ I18N.t('p.chPrompt') }}</div>
          <el-input v-model="prompt" type="textarea" :rows="3" />
          <div class="res-row">
            <span class="muted small">{{ I18N.t('p.chResolution') }}</span>
            <span>{{ defW }}×{{ defH }}</span>
            <span class="muted small" style="margin-left:6px;">{{ I18N.t('p.planResHint') }}</span>
          </div>
          <div class="actions">
            <el-popconfirm :title="I18N.t('p.chGenConfirm')" @confirm="doGen">
              <template #reference>
                <el-button size="small" type="primary" :loading="busy === 'gen'">
                  {{ done ? I18N.t('p.chRegen') : I18N.t('p.chGen') }}
                </el-button>
              </template>
            </el-popconfirm>
            <el-popconfirm :title="I18N.t('p.chSaveConfirm')" @confirm="doSave">
              <template #reference><el-button size="small" :loading="busy === 'save'">{{ I18N.t('p.chSave') }}</el-button></template>
            </el-popconfirm>
            <el-popconfirm :title="I18N.t('p.chMoveUpConfirm')" @confirm="doMove('up')">
              <template #reference><el-button size="small" :disabled="isFirst">{{ I18N.t('p.chMoveUp') }}</el-button></template>
            </el-popconfirm>
            <el-popconfirm :title="I18N.t('p.chMoveDownConfirm')" @confirm="doMove('down')">
              <template #reference><el-button size="small" :disabled="isLast">{{ I18N.t('p.chMoveDown') }}</el-button></template>
            </el-popconfirm>
            <el-popconfirm :title="I18N.t('p.chDeleteConfirm')" @confirm="doDelete">
              <template #reference><el-button size="small" type="danger" plain>{{ I18N.t('p.chDelete') }}</el-button></template>
            </el-popconfirm>
          </div>
        </div>
      </div>
    </el-card>
  `,
  setup(props, { emit }) {
    const busy = ref('');
    const prompt = ref(props.chapter.prompt || '');
    const done = computed(() => props.chapter.status === 'done');
    const statusLabel = computed(() => I18N.t('p.chStatus.' + props.chapter.status) || props.chapter.status);
    const statusType = computed(() =>
      props.chapter.status === 'done' ? 'success' : (props.chapter.status === 'error' ? 'danger' : 'info'));

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
    async function doSave() {
      busy.value = 'save';
      try {
        await API.post(`/api/projects/${props.projectId}/edit/${props.seasonIndex}`,
                       { season_id: props.seasonId, prompt: prompt.value });
        ElementPlus.ElMessage.success(I18N.t('p.promptSaved'));
        props.chapter.prompt = prompt.value;
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally { busy.value = ''; }
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

    return { busy, prompt, done, statusLabel, statusType,
             doGen, doSave, doMove, doDelete };
  },
};
