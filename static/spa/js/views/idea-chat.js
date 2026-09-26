// 「AI 生成标题/主题」对话框（类型无关，漫画/短剧新建表单共用）：
// 多轮对话 + 流式输出；模型可先提问，信息足够时 function call 写回标题/主题（fields 事件 → emit('apply')）。
window.Views = window.Views || {};

Views.ideaChat = {
  props: {
    llm: { type: String, default: '' },      // 当前表单所选的 LLM 配置 id
    kind: { type: String, default: 'comic' },
  },
  emits: ['apply'],
  template: `
    <div class="idea-wrap">
      <el-button size="small" type="primary" plain @click="open">✨ {{ I18N.t('idea.btn') }}</el-button>
      <el-dialog v-model="dlg" :title="I18N.t('idea.title')" width="620px" append-to-body @opened="scroll">
        <div class="idea-hint muted small">{{ I18N.t('idea.hint') }}</div>
        <div class="idea-body" ref="body">
          <el-empty v-if="!msgs.length && !streamText" :description="I18N.t('idea.empty')" :image-size="48" />
          <div v-for="(m, i) in msgs" :key="i" class="idea-msg" :class="m.role">
            <div class="md" v-html="renderMd(m.content)"></div>
          </div>
          <div v-if="busy" class="idea-msg assistant">
            <div class="md" v-html="renderMd(streamText)"></div>
          </div>
        </div>
        <div class="idea-input">
          <el-input v-model="input" type="textarea" :rows="2" :placeholder="I18N.t('idea.ph')"
                    @keydown.enter.exact.prevent="send" />
          <el-button type="primary" :loading="busy" :disabled="!input.trim()" @click="send">{{ I18N.t('idea.send') }}</el-button>
        </div>
      </el-dialog>
    </div>
  `,
  setup(props, { emit }) {
    const dlg = ref(false);
    const msgs = ref([]);          // [{role:'user'|'assistant', content}]
    const input = ref('');
    const busy = ref(false);
    const streamText = ref('');
    const body = ref(null);
    let sseCtrl = null;

    function scroll() {
      nextTick(() => { const el = body.value; if (el) el.scrollTop = el.scrollHeight; });
    }
    function open() { dlg.value = true; scroll(); }

    async function send() {
      const text = input.value.trim();
      if (!text || busy.value) return;
      if (!props.llm) { ElementPlus.ElMessage.warning(I18N.t('idea.needLlm')); return; }
      const history = msgs.value.map(m => ({ role: m.role, content: m.content }));
      msgs.value.push({ role: 'user', content: text });
      input.value = '';
      busy.value = true;
      streamText.value = '';
      scroll();

      const ctrl = new AbortController();
      sseCtrl = ctrl;
      try {
        await API.sse('/api/idea/chat', {
          llm_config_id: props.llm, message: text, history, kind: props.kind,
        }, (ev, d) => {
          if (ev === EVENTS.TOKEN) {
            streamText.value += d.text;
            scroll();
          } else if (ev === EVENTS.FIELDS) {
            emit('apply', { title: d.title || '', origin: d.origin || '' });
            ElementPlus.ElMessage.success(I18N.t('idea.applied'));
          } else if (ev === EVENTS.ERROR) {
            ElementPlus.ElMessage.error(d.message);
          }
        }, ctrl.signal);
      } catch (e) {
        if (!ctrl.signal.aborted) ElementPlus.ElMessage.error(e.message);
      } finally {
        if (streamText.value) msgs.value.push({ role: 'assistant', content: streamText.value });
        streamText.value = '';
        busy.value = false;
        scroll();
        if (sseCtrl === ctrl) sseCtrl = null;
      }
    }

    onBeforeUnmount(() => { if (sseCtrl) { sseCtrl.abort(); sseCtrl = null; } });

    return { dlg, msgs, input, busy, streamText, body, open, send, scroll, renderMd };
  },
};
