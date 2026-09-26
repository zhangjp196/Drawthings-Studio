// 漫画项目页 —— 本季预览侧栏（漫画专属，不与短剧共享）。
// 纯展示 + 事件：父组件传 items/urls/current/total，子组件只负责渲染与「平铺/画廊」切换。
window.Views = window.Views || {};

Views.comicSeasonPreview = {
  props: {
    items: { type: Array, default: () => [] },   // [{index,title,url,has,k}]
    urls: { type: Array, default: () => [] },    // 有图章节的 url（放大列表）
    current: { type: Number, default: 0 },       // 当前选中章（季内序号）
    total: { type: Number, default: 0 },         // 本季章节数
  },
  emits: ['select', 'preview'],
  template: `
    <div class="pv-side">
      <div class="pv-side-head">
        <b>{{ I18N.t('p.tabPreview') }}</b>
        <el-radio-group v-model="pvMode" size="small">
          <el-radio-button value="tile">{{ I18N.t('p.pvTile') }}</el-radio-button>
          <el-radio-button value="gallery">{{ I18N.t('p.pvGallery') }}</el-radio-button>
        </el-radio-group>
      </div>
      <div class="muted small">{{ I18N.t('p.pvHint', urls.length, total) }}</div>
      <div class="pv-side-body">
        <div class="pv-grid" :class="'pv-' + pvMode">
          <div v-for="(it, i) in items" :key="'pv' + it.index" class="pv-cell"
               :class="{ cur: i === current }" @click="$emit('select', i)">
            <div class="pv-media">
              <img v-if="it.has" :src="it.url" :alt="it.title || I18N.t('p.ch', it.index + 1)"
                   loading="lazy" decoding="async">
              <div v-else class="pv-ph" :title="I18N.t('p.pvMissing')">{{ it.index + 1 }}</div>
              <button v-if="it.has" type="button" class="pv-zoom" :title="I18N.t('p.pvZoom')"
                      @click.stop="$emit('preview', urls, it.k)">⤢</button>
            </div>
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
