// 单个助手内容块：text（Markdown）/ tool（生成中→已生成/失败 + 媒体 + 可折叠提示词）/ media（旧数据兼容）/ error
// 由微创作对话（micro-chat）渲染，严格保持「文本 / 生成」的发生顺序。
window.Views = window.Views || {};

Views.microBlock = {
  props: {
    b: { type: Object, required: true },
    cursor: { type: Boolean, default: false },
  },
  template: `
    <div class="blk" :class="'blk-' + b.type">
      <template v-if="b.type === 'text'">
        <div class="md" v-html="html"></div>
      </template>

      <template v-else-if="b.type === 'tool'">
        <div class="tool-chip" :class="{ ok: b.status === 'ok', error: b.status === 'error' }">
          <span v-if="b.status === 'running'" class="spinner"></span>
          <span v-else-if="b.status === 'ok'" class="tick">✓</span>
          <span v-else class="warn">⚠</span>
          <span class="tool-label">{{ statusLabel }}</span>
          <button v-if="b.prompt" type="button" class="tool-prompt-toggle" @click="showPrompt = !showPrompt">
            {{ showPrompt ? I18N.t('mw.hidePrompt') : I18N.t('mw.prompt') }}
          </button>
          <div v-if="showPrompt && b.prompt" class="tool-prompt">{{ b.prompt }}</div>
          <div v-if="b.status === 'running' && b.message" class="tool-note">{{ b.message }}</div>
          <div v-if="b.status === 'error' && b.message" class="tool-err">{{ b.message }}</div>
        </div>
        <div class="msg-media" v-if="b.status === 'ok' && b.url">
          <video v-if="isVideo(b.url)" :src="b.url" controls preload="metadata"></video>
          <img v-else :src="b.url" :alt="I18N.t('mw.resultAlt')" loading="lazy" decoding="async"
               @click="$emit('preview', [b.url], 0)">
        </div>
      </template>

      <template v-else-if="b.type === 'media'">
        <div class="msg-media" v-if="b.url">
          <video v-if="isVideo(b.url)" :src="b.url" controls preload="metadata"></video>
          <img v-else :src="b.url" :alt="I18N.t('mw.resultAlt')" loading="lazy" decoding="async"
               @click="$emit('preview', [b.url], 0)">
          <div class="media-cap" v-if="b.prompt">{{ b.prompt }}</div>
        </div>
      </template>

      <template v-else-if="b.type === 'error'">
        <div class="tool-chip error"><span class="warn">⚠</span><span class="tool-label">{{ b.message }}</span></div>
      </template>
    </div>
  `,
  setup(props) {
    const showPrompt = ref(false);
    const html = computed(() => props.b.type === 'text'
      ? renderMd(props.b.text || '') + (props.cursor ? '<span class="cursor"></span>' : '')
      : '');
    const statusLabel = computed(() => {
      if (props.b.status === 'running') return props.b.label || I18N.t('mw.saving');
      if (props.b.status === 'error') return I18N.t('mw.toolError');
      return I18N.t('mw.toolOk');
    });
    function isVideo(url) { return /\.(mp4|mov|webm|gif)(\?|$)/i.test(url || ''); }
    return { showPrompt, html, statusLabel, isVideo, I18N };
  },
};
