// 短剧项目页 —— 章节工具条（短剧专属，不与漫画共享）。
// 纯展示 + 事件：所有动作（规划/保存/批量生成/批量评分/停止）交由父组件执行。
window.Views = window.Views || {};

Views.dramaChapterToolbar = {
  props: {
    locked: { type: Boolean, default: false },
    actBusy: { type: Boolean, default: false },
    busySave: { type: Boolean, default: false },
    busyGenAll: { type: Boolean, default: false },
    busyScoreAll: { type: Boolean, default: false },
    selectedCount: { type: Number, default: 0 },
    scoreFilter: { type: String, default: 'all' },
    scoreFilterOptions: { type: Array, default: () => [] },
    oW: { type: Number, default: 0 },
    oH: { type: Number, default: 0 },
    hasChapters: { type: Boolean, default: false },
    total: { type: Number, default: 0 },
    doneCount: { type: Number, default: 0 },
    progressText: { type: String, default: '' },
    allSelected: { type: Boolean, default: false },
  },
  emits: ['plan', 'save-plan', 'update:scoreFilter', 'toggle-all', 'gen-all', 'score-all', 'stop'],
  template: `
    <div class="ch-toolbar">
      <div class="ch-tb-row">
        <span class="ch-tb-cap">{{ I18N.t('p.chPlan') }}</span>
        <el-button size="small" type="primary" :loading="actBusy" :disabled="locked" @click="$emit('plan')">
          {{ selectedCount ? I18N.t('p.planSel', selectedCount) : I18N.t('p.planChapters') }}
        </el-button>
        <el-popconfirm :title="I18N.t('p.planSaveConfirm')" @confirm="$emit('save-plan')">
          <template #reference><el-button size="small" :loading="busySave" :disabled="locked">{{ I18N.t('p.planSave') }}</el-button></template>
        </el-popconfirm>
        <span class="ch-tb-sep"></span>
        <span class="muted small">{{ I18N.t('p.outRes') }} {{ oW }}×{{ oH }}</span>
        <span class="muted small" v-if="actBusy || busySave">{{ progressText || I18N.t('p.busy') }}</span>
      </div>
      <div class="ch-tb-row">
        <span class="ch-tb-cap">{{ I18N.t('p.tabChapters') }}</span>
        <el-select :model-value="scoreFilter" @update:model-value="$emit('update:scoreFilter', $event)" size="small" style="width: 118px">
          <el-option v-for="o in scoreFilterOptions" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-checkbox :model-value="allSelected" @change="$emit('toggle-all')">{{ I18N.t('p.selAll') }}</el-checkbox>
        <el-popconfirm :title="I18N.t('p.genAllConfirm')" @confirm="$emit('gen-all')">
          <template #reference>
            <el-button size="small" type="primary" :loading="busyGenAll" :disabled="!hasChapters || locked">
              {{ selectedCount ? I18N.t('p.genAllSel', selectedCount) : I18N.t('p.genAll') }}
            </el-button>
          </template>
        </el-popconfirm>
        <el-popconfirm :title="I18N.t('p.scoreAllConfirm')" @confirm="$emit('score-all')">
          <template #reference>
            <el-button size="small" type="primary" plain :loading="busyScoreAll" :disabled="!hasChapters || locked">
              {{ selectedCount ? I18N.t('p.scoreAllSel', selectedCount) : I18N.t('p.scoreAll') }}
            </el-button>
          </template>
        </el-popconfirm>
        <el-button v-if="busyGenAll || busyScoreAll" size="small" type="danger" plain @click="$emit('stop')">
          {{ busyScoreAll ? I18N.t('p.scoreStop') : I18N.t('p.genStop') }}
        </el-button>
        <span class="muted small" v-if="hasChapters">{{ I18N.t('p.progress', doneCount, total) }}</span>
        <span class="muted small" v-if="progressText">{{ progressText }}</span>
      </div>
    </div>
  `,
};
