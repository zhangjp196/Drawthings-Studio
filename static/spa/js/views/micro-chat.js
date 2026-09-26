// 微创作对话区（消息流 + 输入框 + SSE 流式）。
// 持久化消息（msgs）由父组件加载并传入；本组件负责流式叠加、附图、贴底滚动与发送。
// 发送结束后 emit('sent')，由父组件重新拉取持久化历史与会话列表（标题/计数同步）。
// 事件契约见 window.EVENTS（services/events.py 的前端镜像）。
window.Views = window.Views || {};

Views.microChat = {
  props: {
    id: { type: String, required: true },
    sid: { type: String, default: '' },
    vision: { type: Boolean, default: false },
    hasSession: { type: Boolean, default: false },
    msgs: { type: Array, default: () => [] },
  },
  emits: ['sent', 'preview'],
  components: { MicroBlock: window.Views.microBlock },
  template: `
    <section class="mc-main">
      <div class="mc-chat" ref="chatBox" @scroll="onChatScroll">
        <div class="mc-chat-inner" ref="chatInner">
        <el-empty v-if="!hasSession" :description="I18N.t('mw.noSessionEmpty')" :image-size="72" />
        <template v-else>
          <div v-for="(m, mi) in msgs" :key="mi" class="msg" :class="m.role">
            <template v-if="m.role === 'user'">
              <div class="msg-text" v-if="m.content">{{ m.content }}</div>
              <div class="msg-imgs" v-if="m.images.length">
                <img v-for="(u, i) in m.images" :key="i" :src="u" :alt="I18N.t('mw.attachAlt')" loading="lazy" decoding="async" @click="$emit('preview', m.images, i)">
              </div>
            </template>
            <template v-else>
              <micro-block v-for="(b, bi) in m.blocks" :key="bi" :b="b" @preview="(l, i) => $emit('preview', l, i)" @rerun="rerun" />
              <div v-if="m.status && m.status !== 'done'" class="tool-note">{{ I18N.t('mw.interrupted') }}</div>
              <div class="msg-time" v-if="m.duration">{{ m.duration }}s</div>
            </template>
          </div>

          <div v-if="streaming" class="msg assistant">
            <micro-block v-for="(b, bi) in stream.parts" :key="bi" :b="b" :cursor="streamCursor(bi)" @preview="(l, i) => $emit('preview', l, i)" @rerun="rerun" />
            <div v-if="showTyping" class="md streaming-tail"><span class="typing"><i></i><i></i><i></i></span></div>
            <div class="msg-time">{{ streamElapsed }}s</div>
          </div>
        </template>
        </div>
      </div>

      <button v-if="hasSession && !atBottom" type="button" class="scroll-btn" :title="I18N.t('mw.scrollBottom')" @click="scrollBottom(true)">↓</button>

      <div class="mc-status" v-if="hasSession && activeJob && !busy">
        <span class="muted small">{{ I18N.t('mw.jobRunning') }}</span>
        <el-button size="small" type="danger" plain @click="stopJob">{{ I18N.t('mw.jobStop') }}</el-button>
      </div>

      <div class="mc-inputbar" v-if="hasSession" :class="{ drag }"
           @dragover.prevent="drag = true" @dragleave="drag = false" @drop.prevent="onDrop">
        <div class="mc-previews" v-if="attached.length">
          <div v-for="(d, i) in attached" :key="i" class="mc-prev">
            <img :src="d" :alt="I18N.t('mw.attachAlt')">
            <button type="button" class="mc-prev-x" :title="I18N.t('mw.delete')" @click="attached.splice(i, 1)">×</button>
          </div>
        </div>
        <div class="mc-inputrow">
          <button v-if="vision" type="button" class="mc-attach" :title="I18N.t('mw.attachTip')"
                  @click="fileInput.click()">🖼</button>
          <input type="file" ref="fileInput" accept="image/*" multiple hidden @change="onFiles">
          <textarea ref="inputEl" v-model="input" class="mc-input" rows="1" :placeholder="ph"
                    @keydown.enter.exact="onEnter" @input="autoResize" @paste="onPaste"></textarea>
          <el-button type="primary" :disabled="busy" @click="send">{{ I18N.t('mw.send') }}</el-button>
        </div>
        <div class="mc-status" v-if="status">{{ status }}</div>
      </div>
    </section>
  `,
  setup(props, { emit }) {
    // 输入与流
    const input = ref('');
    const attached = ref([]);
    const busy = ref(false);
    const streaming = ref(false);
    const status = ref('');
    const drag = ref(false);
    const fileInput = ref(null);
    const inputEl = ref(null);
    const chatBox = ref(null);
    const chatInner = ref(null);
    let sseCtrl = null;  // 当前对话流：组件卸载 / 切换会话时中断，服务端随之清理后台生成任务
    const stream = reactive({ parts: [] });
    const ph = computed(() => (props.vision ? I18N.t('mw.phVision') : I18N.t('mw.phPlain')));

    // 滚动：贴底自动跟随（含图片陆续加载）；用户上翻后不再打扰，显示「回到底部」
    const atBottom = ref(true);
    let pinned = true;   // 跟随底部：为 true 时内容增高（图片/视频加载、流式输出）自动贴底
    let ro = null;
    function onChatScroll() {
      const el = chatBox.value;
      if (!el) return;
      atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      pinned = atBottom.value;
    }
    function scrollBottom(force) {
      if (!force && !atBottom.value) return;
      nextTick(() => {
        const el = chatBox.value;
        if (el) el.scrollTop = el.scrollHeight;
      });
    }
    function setupObserver() {
      if (!window.ResizeObserver) return;
      nextTick(() => {
        if (!chatInner.value) return;
        if (ro) ro.disconnect();
        ro = new ResizeObserver(() => {
          if (pinned) {
            const el = chatBox.value;
            if (el) el.scrollTop = el.scrollHeight;
          }
        });
        ro.observe(chatInner.value);
      });
    }

    // 流式块：末块为文本时显示光标；否则在等待后续文本时显示打字点
    function streamCursor(bi) {
      return streaming.value && bi === stream.parts.length - 1 && stream.parts[bi].type === 'text';
    }
    const showTyping = computed(() => {
      if (!streaming.value) return false;
      const parts = stream.parts;
      if (!parts.length) return true;
      const last = parts[parts.length - 1];
      if (last.type === 'text') return false;
      if (last.type === 'tool' && last.status === 'running') return false;
      return true;
    });

    // 助手回复实时秒表：从 0 开始，输出过程中每 100ms 刷新，完成后由落库的 duration 定格
    const streamElapsed = ref(0);
    let timer = null;
    function startTimer() {
      stopTimer();
      const t0 = Date.now();
      streamElapsed.value = 0;
      timer = setInterval(() => {
        streamElapsed.value = ((Date.now() - t0) / 1000).toFixed(1);
      }, 100);
    }
    function stopTimer() {
      if (timer) { clearInterval(timer); timer = null; }
    }

    // ---------- 流式事件 -> 有序内容块 ----------
    function pushText(delta) {
      const parts = stream.parts;
      if (parts.length && parts[parts.length - 1].type === 'text') {
        parts[parts.length - 1].text += delta;
      } else {
        parts.push({ type: 'text', text: delta });
      }
    }
    function findTool(id) {
      for (let i = stream.parts.length - 1; i >= 0; i--) {
        const p = stream.parts[i];
        if (p.type === 'tool' && p.id === id) return p;
      }
      return null;
    }
    function onTool(d) {
      stream.parts.push({ type: 'tool', id: d.id, label: d.label || '', prompt: d.prompt || '',
                          status: 'running', media: d.media || 'image', url: '', message: '' });
    }
    function onMedia(d) {
      const t = d.id ? findTool(d.id) : null;
      if (t) { t.status = 'ok'; t.url = d.url; t.media = d.media || t.media; }
      else stream.parts.push({ type: 'media', media: d.media, url: d.url, prompt: d.prompt || '' });
    }
    function onToolError(d) {
      const t = d.id ? findTool(d.id) : null;
      if (t) { t.status = 'error'; t.message = d.message || ''; }
      else stream.parts.push({ type: 'error', message: d.message || I18N.t('mw.genFail') });
    }
    function onToolStatus(d) {
      // 生成进行中状态（如「正在等待 Draw Things 恢复…」）：只更新文案，气泡保持 running
      const t = d.id ? findTool(d.id) : null;
      if (t && t.status === 'running') { t.message = d.message || ''; }
    }
    function onError(d) {
      stream.parts.push({ type: 'error', message: d.message || I18N.t('mw.err') });
    }

    // ---------- 发送（SSE 流式） ----------
    async function send() {
      if (busy.value || !props.hasSession) return;
      const message = input.value.trim();
      if (!message && !attached.value.length) return;

      busy.value = true;
      const shot = attached.value.slice();
      input.value = '';
      attached.value = [];
      autoResize();
      startTimer();

      props.msgs.push({ index: Date.now(), role: 'user', content: message || I18N.t('mw.image'),
                        images: shot, media_url: '' });
      streaming.value = true;
      stream.parts = [];
      status.value = '';
      atBottom.value = true;
      pinned = true;
      scrollBottom(true);

      let failed = false;
      const ctrl = new AbortController();
      sseCtrl = ctrl;
      try {
        await API.sse(`/api/micro/${props.id}/${props.sid}/chat`, { message, images: shot }, (ev, d) => {
          if (ev === EVENTS.TOKEN) {
            pushText(d.text);
            status.value = '';
            scrollBottom(false);
          } else if (ev === EVENTS.TOOL) {
            onTool(d);
            status.value = '';
            scrollBottom(false);
          } else if (ev === EVENTS.MEDIA) {
            onMedia(d);
            scrollBottom(false);
          } else if (ev === EVENTS.TOOL_STATUS) {
            onToolStatus(d);
          } else if (ev === EVENTS.TOOL_ERROR) {
            onToolError(d);
          } else if (ev === EVENTS.ERROR) {
            failed = true;
            onError(d);
          }
        }, ctrl.signal);
        if (failed) {
          status.value = I18N.t('mw.errRetry');
        } else {
          // 落库完成：由父组件重新拉取持久化历史（含左侧列表/标题同步）
          emit('sent');
        }
      } catch (e) {
        if (!ctrl.signal.aborted) {
          onError({ message: e.message });
          status.value = I18N.t('mw.errRetry');
        }
      } finally {
        stopTimer();
        busy.value = false;
        streaming.value = false;
        if (sseCtrl === ctrl) sseCtrl = null;
        checkJob();
      }
    }

    // 一键重跑：跳过 LLM，按内容块保存的参数直接重生成（结果可复现）
    async function rerun(b) {
      if (busy.value || !props.hasSession || !b || !b.prompt) return;
      busy.value = true;
      startTimer();
      streaming.value = true;
      stream.parts = [];
      status.value = '';
      atBottom.value = true;
      pinned = true;
      scrollBottom(true);

      let failed = false;
      const ctrl = new AbortController();
      sseCtrl = ctrl;
      try {
        await API.sse(`/api/micro/${props.id}/${props.sid}/regenerate`, {
          prompt: b.prompt, media: b.media || 'image',
          width: b.width || 0, height: b.height || 0, seconds: b.seconds || 0,
          ref_url: b.ref_url || '',
        }, (ev, d) => {
          if (ev === EVENTS.TOOL) {
            onTool(d);
            status.value = '';
            scrollBottom(false);
          } else if (ev === EVENTS.MEDIA) {
            onMedia(d);
            scrollBottom(false);
          } else if (ev === EVENTS.TOOL_STATUS) {
            onToolStatus(d);
          } else if (ev === EVENTS.TOOL_ERROR) {
            onToolError(d);
          } else if (ev === EVENTS.ERROR) {
            failed = true;
            onError(d);
          }
        }, ctrl.signal);
        if (failed) {
          status.value = I18N.t('mw.errRetry');
        } else {
          emit('sent');  // 落库完成：父组件刷新持久化历史
        }
      } catch (e) {
        if (!ctrl.signal.aborted) {
          onError({ message: e.message });
          status.value = I18N.t('mw.errRetry');
        }
      } finally {
        stopTimer();
        busy.value = false;
        streaming.value = false;
        if (sseCtrl === ctrl) sseCtrl = null;
        checkJob();
      }
    }

    // 代码块一键复制（事件委托）
    function onChatClick(e) {
      const btn = e.target.closest && e.target.closest('.copy-code');
      if (!btn) return;
      const code = btn.parentElement.querySelector('code');
      if (code && navigator.clipboard) {
        navigator.clipboard.writeText(code.textContent).then(() => {
          btn.textContent = I18N.t('common.copied');
          setTimeout(() => { btn.textContent = I18N.t('common.copy'); }, 1500);
        });
      }
    }

    // ---------- 生成任务（Job）：可观测 / 可取消（不依赖本连接的 AbortController） ----------
    const activeJob = ref(null);
    async function checkJob() {
      if (!props.hasSession) { activeJob.value = null; return; }
      try {
        const r = await API.get(`/api/micro/${props.id}/jobs?active=1`);
        activeJob.value = (r.jobs || []).find(j => j.session_id === props.sid) || null;
      } catch (e) {
        activeJob.value = null;
      }
    }
    async function stopJob() {
      const j = activeJob.value;
      if (!j) return;
      try {
        await API.post(`/api/micro/${props.id}/jobs/${j.id}/cancel`);
        activeJob.value = null;
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    // ---------- 附图（仅视觉模型）：选择 / 粘贴 / 拖拽 ----------
    function addFiles(files) {
      for (const f of files) {
        if (!f.type || !f.type.startsWith('image/')) continue;
        if (attached.value.length >= 4) { status.value = I18N.t('mw.maxAttach'); break; }
        const r = new FileReader();
        r.onload = () => { attached.value.push(r.result); };
        r.readAsDataURL(f);
      }
    }
    function onFiles(e) {
      addFiles(Array.from(e.target.files || []));
      e.target.value = '';
    }
    function onPaste(e) {
      const files = Array.from(e.clipboardData ? e.clipboardData.files : []).filter(f => f.type.startsWith('image/'));
      if (files.length) { e.preventDefault(); addFiles(files); }
    }
    function onDrop(e) {
      drag.value = false;
      addFiles(Array.from(e.dataTransfer.files || []));
    }

    function autoResize() {
      nextTick(() => {
        const el = inputEl.value;
        if (el) {
          el.style.height = 'auto';
          el.style.height = Math.min(el.scrollHeight, 140) + 'px';
        }
      });
    }

    // Enter 发送：中文输入法（拼音候选词）组合中按回车仅确认候选、不发送
    function onEnter(e) {
      if (e.isComposing || e.keyCode === 229) return;
      e.preventDefault();
      send();
    }

    // 切换会话：中断进行中的流并重置流式状态（不再静默卡住）
    watch(() => props.sid, () => {
      if (busy.value && sseCtrl) sseCtrl.abort();
      stopTimer();
      busy.value = false;
      streaming.value = false;
      stream.parts = [];
      atBottom.value = true;
      pinned = true;
      scrollBottom(true);
      setupObserver();
      checkJob();
    });

    onMounted(() => {
      atBottom.value = true;
      pinned = true;
      scrollBottom(true);
      setupObserver();
      checkJob();
      nextTick(() => {
        const el = chatBox.value;
        if (el) el.addEventListener('click', onChatClick);
      });
    });
    onBeforeUnmount(() => {
      stopTimer();
      if (sseCtrl) { sseCtrl.abort(); sseCtrl = null; }
      if (ro) { ro.disconnect(); ro = null; }
      const el = chatBox.value;
      if (el) el.removeEventListener('click', onChatClick);
    });

    return {
      ph, input, attached, busy, status, drag, streaming, stream, streamElapsed,
      chatBox, chatInner, inputEl, fileInput, onFiles, onPaste, onDrop, autoResize, onEnter,
      send, rerun, activeJob, stopJob, streamCursor, showTyping, atBottom, onChatScroll, scrollBottom,
    };
  },
};
