// 短剧项目页 —— 本季预览侧栏（短剧专属，不与漫画共享）。
// 纯展示 + 事件：父组件传 items/urls/current/total；短剧为视频，**单列、一行一个可播放视频**（点标题切到该章），
// 与漫画（图片平铺/画廊 + 放大）刻意分开。
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
      </div>
      <div class="muted small">{{ I18N.t('d.pvHint') }}</div>
      <div class="pv-side-body">
        <div class="pv-list">
          <div v-for="(it, i) in items" :key="'pv' + it.index" class="pv-row" :class="{ cur: i === current }">
            <video v-if="it.has" :src="it.url" class="pv-video" preload="metadata" controls playsinline></video>
            <div v-else class="pv-ph pv-ph-row" :title="I18N.t('d.pvMissing')">{{ it.index + 1 }}</div>
            <div class="pv-cap" @click="$emit('select', i)">{{ i + 1 }}. {{ it.title || I18N.t('d.clip', it.index + 1) }}</div>
          </div>
        </div>
      </div>
    </div>
  `,
  setup() {
    return {};
  },
};
