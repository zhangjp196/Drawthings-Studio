// 短剧项目页 —— 本季预览侧栏（短剧专属，不与漫画共享）。
// 纯展示 + 事件：父组件传 items/urls/current/total，子组件只负责渲染与「平铺/画廊」切换。
// 短剧为视频：预览单元渲染 <video>（漫画为图片 + 放大），故与漫画组件刻意分开。
window.Views = window.Views || {};

Views.dramaSeasonPreview = {
  props: {
    items: { type: Array, default: () => [] },   // [{index,title,url,has,k}]
    urls: { type: Array, default: () => [] },    // 有媒体章节的 url（用于计数提示）
    current: { type: Number, default: 0 },       // 当前选中章（季内序号）
    total: { type: Number, default: 0 },         // 本季章节数
  },
  emits: ['select'],
  template: `
    <div class="pv-side">
      <div class="pv-side-head">
        <b>{{ I18N.t('p.tabPreview') }}</b>
        <el-radio-group v-model="pvMode" size="small">
          <el-radio-button value="tile">{{ I18N.t('p.pvTile') }}</el-radio-button>
          <el-radio-button value="gallery">{{ I18N.t('p.pvGallery') }}</el-radio-button>
        </el-radio-group>
      </div>
      <div class="muted small">{{ I18N.t('p.pvHintDrama', urls.length, total) }}</div>
      <div class="pv-side-body">
        <div class="pv-grid" :class="'pv-' + pvMode">
          <div v-for="(it, i) in items" :key="'pv' + it.index" class="pv-cell"
               :class="{ cur: i === current }" @click="$emit('select', i)">
            <video v-if="it.has" :src="it.url" class="pv-video" preload="metadata" playsinline></video>
            <div v-else class="pv-ph" :title="I18N.t('p.pvMissingDrama')">{{ it.index + 1 }}</div>
            <div class="pv-cap">{{ i + 1 }}. {{ it.title || I18N.t('p.ch', it.index + 1) }}</div>
          </div>
        </div>
      </div>
    </div>
  `,
  setup() {
    const pvMode = ref('tile');  // 平铺 / 画廊（纯展示状态，组件自持）
    return { pvMode };
  },
};
