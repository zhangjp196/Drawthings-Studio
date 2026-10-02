// 片段工具条（短剧专属，不与漫画共享）。
// 纯展示 + 事件：所有动作（片段规划/保存/批量生成/批量评分/批量清空/批量时长）交由父组件执行。
window.Views = window.Views || {};

Views.dramaChapterToolbar = {
  props: {
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
  emits: ['plan', 'save-plan', 'update:scoreFilter', 'toggle-all', 'gen-all', 'score-all',
          'clear-all', 'plan-selected', 'delete-selected', 'set-seconds', 'stop'],
  data() { return { batchSec: 4 }; },
  template: `
    <div class="ch-toolbar">
      <div class="ch-tb-row">
        <span class="ch-tb-cap">{{ I18N.t('d.planChaptersDrama') }}</span>
        <el-button size="small" type="primary" :loading="actBusy"  @click="$emit('plan')">
          {{ selectedCount ? I18N.t('d.planSelDrama', selectedCount) : I18N.t('d.planChaptersDrama') }}
        </el-button>
        <el-popconfirm :title="I18N.t('d.planSaveConfirmDrama')" @confirm="$emit('save-plan')">
          <template #reference><el-button size="small" :loading="busySave" >{{ I18N.t('d.planSaveDrama') }}</el-button></template>
        </el-popconfirm>
        <span class="ch-tb-sep"></span>
        <span class="muted small">{{ I18N.t('p.outRes') }} {{ oW }}×{{ oH }}</span>
        <span class="muted small" v-if="actBusy || busySave">{{ progressText || I18N.t('p.busy') }}</span>
      </div>
      <div class="ch-tb-row">
        <span class="ch-tb-cap">{{ I18N.t('d.tabClips') }}</span>
        <el-select :model-value="scoreFilter" @update:model-value="$emit('update:scoreFilter', $event)" size="small" style="width: 118px">
          <el-option v-for="o in scoreFilterOptions" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-checkbox :model-value="allSelected" @change="$emit('toggle-all')">{{ I18N.t('p.selAll') }}</el-checkbox>
        <span class="ch-tb-sep"></span>
        <el-popconfirm :title="I18N.t('d.genAllConfirm')" @confirm="$emit('gen-all')">
          <template #reference>
            <el-button size="small" type="primary" :loading="busyGenAll" :disabled="!hasChapters">
              {{ selectedCount ? I18N.t('d.genAllSelDrama', selectedCount) : I18N.t('d.genAllDrama') }}
            </el-button>
          </template>
        </el-popconfirm>
        <el-popconfirm :title="I18N.t('p.scoreAllConfirm')" @confirm="$emit('score-all')">
          <template #reference>
            <el-button size="small" type="primary" plain :loading="busyScoreAll" :disabled="!hasChapters">
              {{ selectedCount ? I18N.t('d.scoreAllSelDrama', selectedCount) : I18N.t('d.scoreAllDrama') }}
            </el-button>
          </template>
        </el-popconfirm>
        <el-button v-if="busyGenAll || busyScoreAll" size="small" type="danger" plain @click="$emit('stop')">
          {{ busyScoreAll ? I18N.t('p.scoreStop') : I18N.t('d.genStopped') }}
        </el-button>
        <span class="muted small" v-if="hasChapters">{{ I18N.t('p.progress', doneCount, total) }}</span>
        <span class="muted small" v-if="progressText">{{ progressText }}</span>
      </div>
      <div class="ch-tb-row">
        <span class="ch-tb-cap">{{ I18N.t('d.batchOps') }}</span>
        <el-popconfirm :title="selectedCount ? I18N.t('d.clearAllConfirmSel', selectedCount) : I18N.t('d.clearAllConfirm')" @confirm="$emit('clear-all')">
          <template #reference>
            <el-button size="small" type="warning" plain :disabled="!hasChapters">
              {{ selectedCount ? I18N.t('d.clearAllSel', selectedCount) : I18N.t('d.clearAll') }}
            </el-button>
          </template>
        </el-popconfirm>
        <el-popconfirm :title="I18N.t('d.replanSelConfirm', selectedCount)" @confirm="$emit('plan-selected')">
          <template #reference>
            <el-button size="small" type="success" plain :disabled="!selectedCount">
              {{ I18N.t('d.replanSelBtn', selectedCount) }}
            </el-button>
          </template>
        </el-popconfirm>
        <el-popconfirm :title="I18N.t('d.delSelConfirm', selectedCount)" @confirm="$emit('delete-selected')">
          <template #reference>
            <el-button size="small" type="danger" plain :disabled="!selectedCount">
              {{ I18N.t('d.delSelBtn', selectedCount) }}
            </el-button>
          </template>
        </el-popconfirm>
        <span class="ch-tb-sep"></span>
        <el-input-number v-model="batchSec" :min="1" :max="10" size="small" style="width: 110px" :disabled="!hasChapters" />
        <el-popconfirm :title="I18N.t('d.batchSecondsConfirm', batchSec)" @confirm="$emit('set-seconds', batchSec)">
          <template #reference>
            <el-button size="small" :disabled="!hasChapters">{{ I18N.t('d.batchSecondsApply', batchSec) }}</el-button>
          </template>
        </el-popconfirm>
        <span class="muted small">{{ I18N.t('d.batchRefHint') }}</span>
      </div>
    </div>
  `,
};
