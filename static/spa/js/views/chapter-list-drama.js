// 短剧项目页 —— 左栏章节列表（短剧专属，不与漫画共享）。
// 纯展示 + 事件：勾选用于批量生成，点击选中当前章。
window.Views = window.Views || {};

Views.dramaChapterList = {
  props: {
    chapters: { type: Array, default: () => [] },  // 可见章节（已按评分筛选）
    current: { type: Number, default: 0 },         // 当前选中章（季内序号）
    selected: { type: Array, default: () => [] },  // 勾选用于批量生成
    scoreMin: { type: Number, default: 60 },       // 评分达标阈值
  },
  emits: ['select', 'toggle'],
  template: `
    <div class="md-left">
      <div class="md-list" v-if="chapters.length">
        <div v-for="c in chapters" :key="'md' + c.index" class="md-item"
             :class="{ active: c.index === current }" @click="$emit('select', c.index)">
          <el-checkbox :model-value="isSel(c.index)" @click.stop @change="$emit('toggle', c.index)" />
          <span class="md-idx">{{ c.index + 1 }}</span>
          <span class="md-title">{{ c.title || I18N.t('p.ch', c.index + 1) }}</span>
          <el-tag size="small" effect="light"
                  :type="c.status === 'done' ? 'success' : (c.status === 'error' ? 'danger' : 'info')">
            {{ I18N.t('p.chStatus.' + c.status) || c.status }}
          </el-tag>
          <el-tag v-if="c.score > 0" size="small" effect="light"
                  :type="c.score >= scoreMin ? 'success' : 'warning'">
            {{ c.score }}{{ I18N.t('p.scoreUnit') }}
          </el-tag>
        </div>
        <div v-if="!chapters.length" class="muted small" style="padding: 10px;">{{ I18N.t('p.filterEmpty') }}</div>
      </div>
      <el-empty v-else :description="I18N.t('p.chPlanEmpty')" :image-size="48" />
    </div>
  `,
  setup(props) {
    function isSel(idx) { return props.selected.indexOf(idx) >= 0; }
    return { isSel };
  },
};
