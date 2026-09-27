// 短剧项目页 —— 片段列表（短剧专属，不与漫画共享）：**横向**排列，置于章节区上方。
// 纯展示 + 事件：点选片段、勾选用于批量生成；状态标签。
window.Views = window.Views || {};

Views.dramaChapterList = {
  props: {
    chapters: { type: Array, default: () => [] },  // 可见章节
    current: { type: Number, default: 0 },         // 当前选中章（季内序号）
    selected: { type: Array, default: () => [] },  // 勾选用于批量生成
    scoreMin: { type: Number, default: 60 },       // 评分阈值（分值标签配色）
  },
  emits: ['select', 'toggle'],
  template: `
    <div class="ch-strip">
      <div v-for="c in chapters" :key="'md' + c.index" class="cs-item"
           :class="{ active: c.index === current }" @click="$emit('select', c.index)">
        <el-checkbox :model-value="isSel(c.index)" @click.stop @change="$emit('toggle', c.index)" />
        <span class="cs-idx">{{ c.index + 1 }}</span>
        <span class="cs-title">{{ c.title || I18N.t('d.clip', c.index + 1) }}</span>
        <el-tag size="small" effect="light"
                :type="c.status === 'done' ? 'success' : (c.status === 'error' ? 'danger' : 'info')">
          {{ I18N.t('d.clipStatus.' + c.status) || c.status }}
        </el-tag>
        <el-tag v-if="c.score > 0" size="small" effect="light"
                :type="c.score >= scoreMin ? 'success' : 'warning'">{{ c.score }}</el-tag>
      </div>
      <div v-if="!chapters.length" class="muted small" style="padding: 6px 2px;">{{ I18N.t('d.filterEmpty') }}</div>
    </div>
  `,
  setup(props) {
    function isSel(idx) { return props.selected.indexOf(idx) >= 0; }
    return { isSel };
  },
};
