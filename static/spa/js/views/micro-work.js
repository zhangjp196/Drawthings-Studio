// 微创作作品页：tab = 对话（左会话列表+右对话）/ 作品（生成媒体画廊，多选批量删）/ 设置（作品选项）
// 对话：SSE 流式（token/tool/media/tool_error/error/done）+ Markdown + 附图（视觉模型：选择/粘贴/拖拽）+ 图片放大
window.Views = window.Views || {};
Views.microWork = {
  props: ['id', 'sid'],
  template: `
    <div class="page mc-page" v-if="data">
      <div class="mc-head">
        <div>
          <el-tag size="small" :type="tagType" effect="plain">{{ tagLabel }}</el-tag>
          <h1 class="ptitle">{{ data.work.title || I18N.t('mw.unnamedWork') }}</h1>
          <p class="meta muted">{{ I18N.t('p.metaLlm', data.work.llm_name) }} · {{ I18N.t('p.metaDt', data.work.dt_name || I18N.t('mw.dtNone')) }}</p>
        </div>
        <div class="proj-head-actions">
          <el-button @click="tab = 'settings'">{{ I18N.t('mw.options') }}</el-button>
          <el-button @click="router.push('/micro')">{{ I18N.t('p.back') }}</el-button>
          <el-popconfirm :title="I18N.t('mw.delWorkConfirm')" @confirm="delWork">
            <template #reference><el-button type="danger" plain>{{ I18N.t('mw.delWork') }}</el-button></template>
          </el-popconfirm>
        </div>
      </div>

      <el-tabs v-model="tab" class="mc-tabs">
      <el-tab-pane :label="I18N.t('mw.tabChat')" name="chat">
      <div class="mc-body" :class="{ 'side-hidden': sideHidden }">
        <button v-if="sideHidden" type="button" class="mc-expand" :title="I18N.t('mw.expandTitle')" @click="setSide(false)">{{ I18N.t('mw.expand') }}</button>
        <aside class="mc-side" v-show="!sideHidden">
          <div class="mc-side-head">
            <b>{{ I18N.t('mw.sessions', data.sessions.length) }}</b>
            <div style="display: flex; gap: 4px;">
              <el-button size="small" type="primary" plain @click="openSess">{{ I18N.t('mw.new') }}</el-button>
              <el-button size="small" text :title="I18N.t('mw.collapse')" @click="setSide(true)">«</el-button>
            </div>
          </div>
          <div class="mc-sess-list">
            <div v-for="s in data.sessions" :key="s.id" class="mc-sess" :class="{ active: s.id === sid }" @click="pick(s.id)">
              <div class="ms-title">{{ s.title || I18N.t('mw.newSession') }}</div>
              <div class="ms-meta muted">{{ s.created_at.slice(0, 10) }} · {{ I18N.t('mw.msgs', s.msg_count) }}</div>
              <div class="ms-actions">
                <el-button size="small" text :icon="EditPen" @click.stop="askRename(s)">{{ I18N.t('mw.rename') }}</el-button>
                <el-popconfirm :title="I18N.t('mw.delSessConfirm')" @confirm="delSess(s)">
                  <template #reference>
                    <el-button size="small" text type="danger" :icon="Delete" @click.stop>{{ I18N.t('mw.delete') }}</el-button>
                  </template>
                </el-popconfirm>
              </div>
            </div>
            <el-empty v-if="!data.sessions.length" :description="I18N.t('mw.noSessions')" :image-size="48" />
          </div>
        </aside>

        <section class="mc-main">
          <div class="mc-chat" ref="chatBox">
            <el-empty v-if="!hasSession" :description="I18N.t('mw.noSessionEmpty')" :image-size="72" />
            <template v-else>
              <div v-for="m in msgs" :key="m.index" class="msg" :class="m.role">
                <div v-if="m.role === 'user'">
                  <span class="msg-text">{{ m.content || I18N.t('mw.image') }}</span>
                  <div class="msg-imgs" v-if="m.images.length">
                    <img v-for="(u, i) in m.images" :key="i" :src="u" :alt="I18N.t('mw.attachAlt')" loading="lazy" decoding="async" @click="openLb(m.images, i)">
                  </div>
                </div>
                <div v-else>
                  <div class="msg-media" v-if="m.media_url">
                    <video v-if="isMediaVideo(m.media_url)" :src="m.media_url" controls preload="metadata"></video>
                    <img v-else :src="m.media_url" :alt="I18N.t('mw.resultAlt')" loading="lazy" decoding="async" @click="openLb([m.media_url], 0)">
                    <div class="media-cap" v-if="m.prompt">{{ m.prompt }}</div>
                  </div>
                  <div class="md" v-html="renderMd(m.content)"></div>
                  <div class="msg-time" v-if="m.duration">{{ m.duration }}s</div>
                </div>
              </div>
              <div v-if="streaming" class="msg assistant">
                <div v-for="(c, i) in stream.chips" :key="'c' + i" class="tool-chip" :class="{ error: c.err }">
                  <span v-if="!c.err" class="spinner"></span>{{ c.text }}
                  <div class="tool-prompt" v-if="c.prompt">{{ c.prompt }}</div>
                </div>
                <div v-for="(md2, i) in stream.media" :key="'m' + i" class="msg-media">
                  <video v-if="md2.media === 'video'" :src="md2.url" controls preload="metadata"></video>
                  <img v-else :src="md2.url" :alt="I18N.t('mw.resultAlt')" @click="openLb([md2.url], 0)">
                  <div class="media-cap" v-if="md2.prompt">{{ md2.prompt }}</div>
                </div>
                <div class="md" v-html="streamHtml"></div>
                <div class="msg-time">{{ streamElapsed }}s</div>
              </div>
            </template>
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
              <button v-if="data.work.vision" type="button" class="mc-attach" :title="I18N.t('mw.attachTip')"
                      @click="fileInput.click()">🖼</button>
              <input type="file" ref="fileInput" accept="image/*" multiple hidden @change="onFiles">
              <textarea v-model="input" class="mc-input" rows="1" :placeholder="ph"
                        @keydown.enter.exact="onEnter" @input="autoResize" @paste="onPaste"></textarea>
              <el-button type="primary" :disabled="busy" @click="send">{{ I18N.t('mw.send') }}</el-button>
            </div>
            <div class="mc-status">{{ status }}</div>
          </div>
        </section>
      </div>
      </el-tab-pane>

      <!-- ============ 作品（该作品下所有会话生成的媒体：图/视频，多选批量删除）============ -->
      <el-tab-pane :label="I18N.t('mw.tabWorks')" name="works">
        <div class="works-toolbar">
          <b class="muted small">{{ I18N.t('mw.works', works.length) }}</b>
          <div style="flex: 1"></div>
          <el-checkbox :model-value="allWSel" :disabled="!works.length" @change="toggleAllWorks">{{ I18N.t('mw.worksSelAll') }}</el-checkbox>
          <el-popconfirm :title="I18N.t('mw.worksDelConfirm', selWorks.length)" @confirm="delWorks">
            <template #reference>
              <el-button type="danger" plain :disabled="!selWorks.length || worksBusy">{{ I18N.t('mw.worksDelSel', selWorks.length) }}</el-button>
            </template>
          </el-popconfirm>
        </div>
        <div class="works-grid" v-if="works.length">
          <div v-for="w in works" :key="w.id" class="work-piece" :class="{ sel: isWSel(w.id) }">
            <div class="wp-media">
              <video v-if="isMediaVideo(w.media_url)" :src="w.media_url" controls preload="metadata"></video>
              <img v-else :src="w.media_url" :alt="I18N.t('mw.resultAlt')" loading="lazy" decoding="async" @click="openLb([w.media_url], 0)">
              <el-checkbox class="wp-check" :model-value="isWSel(w.id)" @click.stop @change="toggleWSel(w.id)"></el-checkbox>
            </div>
            <div class="wp-cap" v-if="w.prompt || w.content">{{ w.prompt || w.content }}</div>
            <div class="wp-src muted">{{ (w.session_title || I18N.t('mw.newSession')) }} · {{ w.created_at.slice(0, 10) }}</div>
          </div>
        </div>
        <el-empty v-else :description="I18N.t('mw.worksEmpty')" :image-size="72" />
      </el-tab-pane>

      <!-- ============ 设置（作品选项，作用于全部会话）============ -->
      <el-tab-pane :label="I18N.t('mw.tabSettings')" name="settings">
        <el-card shadow="never" style="max-width: 640px">
          <p class="hint">{{ I18N.t('mw.cfgHint') }}</p>
          <el-form label-position="top">
            <el-form-item :label="I18N.t('mc.fTitle')">
              <el-input v-model="cfg.title" maxlength="200" :placeholder="I18N.t('mc.fTitlePh')" />
            </el-form-item>
            <el-form-item :label="I18N.t('mc.llm')">
              <el-select v-model="cfg.llm" style="width: 100%">
                <el-option v-for="c in data.llm_configs" :key="c.id" :value="c.id"
                           :label="c.name + '（' + c.model + (c.supports_vision === 'no' ? ' / ' + I18N.t('cfg.textOnly') : '') + '）'" />
              </el-select>
            </el-form-item>
            <el-form-item :label="I18N.t('mc.dt')">
              <el-select v-model="cfg.dt" clearable style="width: 100%">
                <el-option value="" :label="I18N.t('mw.dtNone')" />
                <el-option v-for="c in data.drawthing_configs" :key="c.id" :value="c.id" :label="c.name" />
              </el-select>
              <div class="hint">{{ I18N.t('mc.dtHint') }}</div>
            </el-form-item>
          </el-form>
          <div class="actions">
            <el-button type="primary" :loading="cfgBusy" @click="saveCfg">{{ I18N.t('common.save') }}</el-button>
          </div>
        </el-card>
      </el-tab-pane>
      </el-tabs>

      <el-dialog v-model="sessDlg" :title="I18N.t('mw.dlgSess')" width="440px">
        <el-input v-model="sessTitle" :placeholder="I18N.t('mw.sessTitlePh')" maxlength="200" />
        <template #footer>
          <el-button @click="sessDlg = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" @click="createSess">{{ I18N.t('common.create') }}</el-button>
        </template>
      </el-dialog>

      <el-dialog v-model="renameDlg" :title="I18N.t('mw.dlgRename')" width="440px">
        <el-input v-model="renameTitle" maxlength="200" />
        <template #footer>
          <el-button @click="renameDlg = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" @click="doRename">{{ I18N.t('common.save') }}</el-button>
        </template>
      </el-dialog>

      <el-image-viewer v-if="lb.show" :url-list="lb.list" :initial-index="lb.idx" @close="lb.show = false" />
    </div>
    <div v-else class="page loading"><el-skeleton :rows="8" animated /></div>
  `,
  setup(props) {
    const data = ref(null);
    const msgs = ref([]);
    const hasSession = ref(false);
    const tab = ref('chat');  // 作品详情页 tab：对话（默认）/ 作品 / 设置

    // 侧栏折叠（按作品记忆）
    const sideHidden = ref(false);
    const sideKey = computed(() => 'mc_side_' + props.id);
    function setSide(hidden) {
      sideHidden.value = hidden;
      try { localStorage.setItem(sideKey.value, hidden ? '1' : '0'); } catch (e) {}
    }

    // 会话操作
    const sessDlg = ref(false);
    const sessTitle = ref('');
    function openSess() {
      sessTitle.value = '';
      sessDlg.value = true;
    }
    const renameDlg = ref(false);
    const renameTitle = ref('');
    const renameTarget = ref(null);

    // 作品选项（设置在「设置」tab 内，作用于全部会话）
    const cfgBusy = ref(false);
    const cfg = reactive({ title: '', llm: '', dt: '' });
    function syncCfg() {
      cfg.title = data.value.work.title;
      cfg.llm = data.value.work.llm_config_id;
      cfg.dt = data.value.work.drawthings_config_id || '';
    }

    // 作品（该作品下所有会话生成的媒体：图/视频）画廊：多选批量删除
    const works = ref([]);
    const worksLoaded = ref(false);
    const selWorks = ref([]);
    const worksBusy = ref(false);
    const allWSel = computed(() => works.value.length > 0 && selWorks.value.length === works.value.length);
    function isWSel(id) { return selWorks.value.indexOf(id) >= 0; }
    function toggleWSel(id) {
      const k = selWorks.value.indexOf(id);
      if (k >= 0) selWorks.value.splice(k, 1);
      else selWorks.value.push(id);
    }
    function toggleAllWorks() {
      selWorks.value = allWSel.value ? [] : works.value.map(w => w.id);
    }
    async function loadWorks() {
      try {
        const r = await API.get(`/api/micro/${props.id}/works`);
        works.value = r.works || [];
        worksLoaded.value = true;
        selWorks.value = [];
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }
    async function delWorks() {
      if (!selWorks.value.length) return;
      worksBusy.value = true;
      try {
        await API.post(`/api/micro/${props.id}/works/delete`, { ids: selWorks.value });
        ElementPlus.ElMessage.success(I18N.t('mw.worksDeleted'));
        selWorks.value = [];
        await loadWorks();
        await load();  // 同步左侧会话列表的消息数
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        worksBusy.value = false;
      }
    }

    // 图片放大
    const lb = reactive({ show: false, list: [], idx: 0 });

    // 输入与流
    const input = ref('');
    const attached = ref([]);
    const busy = ref(false);
    const streaming = ref(false);
    const status = ref('');
    const drag = ref(false);
    const fileInput = ref(null);
    const chatBox = ref(null);
    let sseCtrl = null;  // 当前对话流：组件卸载时中断，服务端随之清理后台生成任务
    const stream = reactive({ text: '', chips: [], media: [] });
    const streamHtml = computed(() => stream.text
      ? renderMd(stream.text) + '<span class="cursor"></span>'
      : '<span class="typing"><i></i><i></i><i></i></span>');
    function isMediaVideo(url) {  // 按文件扩展名判断（媒体落盘时按实际内容定扩展名）
      return /\.(mp4|mov|webm|gif)$/i.test(url || '');
    }
    const tagLabel = computed(() => {
      if (!data.value) return '';
      return data.value.work.dt_name ? I18N.t('mw.tagGen') : I18N.t('mw.tagChat');
    });
    const tagType = computed(() => tagLabel.value === I18N.t('mw.tagChat') ? 'info' : 'primary');
    const ph = computed(() => (data.value?.work.vision ? I18N.t('mw.phVision') : I18N.t('mw.phPlain')));
    const mwDtNone = computed(() => I18N.t('mw.dtNone'));

    async function load() {
      try {
        if (props.sid) {
          data.value = await API.get(`/api/micro/${props.id}/${props.sid}`);
          msgs.value = data.value.messages || [];
          hasSession.value = true;
          syncCfg();
        } else {
          data.value = await API.get(`/api/micro/${props.id}`);
          msgs.value = [];
          hasSession.value = false;
          syncCfg();
          if (data.value.sessions.length) {
            router.replace(`/micro/${props.id}/${data.value.sessions[0].id}`);
            return;
          }
        }
        try { sideHidden.value = localStorage.getItem(sideKey.value) === '1'; } catch (e) {}
        scrollBottom();
      } catch (e) {
        if (e.status === 404) router.replace('/micro');
        ElementPlus.ElMessage.error(e.message);
      }
    }

  function scrollBottom() {
    nextTick(() => {
      const el = chatBox.value || document.querySelector('.mc-chat');
      if (el) el.scrollTop = el.scrollHeight;
    });
  }

    function pick(sid) {
      if (sid === props.sid) return;
      router.push(`/micro/${props.id}/${sid}`);
    }

    async function createSess() {
      try {
        const r = await API.post(`/api/micro/${props.id}/sessions`, { title: sessTitle.value });
        sessDlg.value = false;
        sessTitle.value = '';
        router.push(`/micro/${props.id}/${r.id}`);
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    function askRename(s) {
      renameTarget.value = s;
      renameTitle.value = s.title;
      renameDlg.value = true;
    }
    async function doRename() {
      try {
        await API.post(`/api/micro/${props.id}/${renameTarget.value.id}/rename`, { title: renameTitle.value });
        renameDlg.value = false;
        ElementPlus.ElMessage.success(I18N.t('mw.renamed'));
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }
    async function delSess(s) {
      try {
        await API.post(`/api/micro/${props.id}/${s.id}/delete`);
        ElementPlus.ElMessage.success(I18N.t('mw.sessDeleted'));
        if (s.id === props.sid) {
          const rest = data.value.sessions.filter(x => x.id !== s.id);
          if (rest.length) router.replace(`/micro/${props.id}/${rest[0].id}`);
          else load();
        } else {
          load();
        }
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    async function saveCfg() {
      cfgBusy.value = true;
      try {
        await API.post(`/api/micro/${props.id}/settings`, {
          title: cfg.title, llm_config_id: cfg.llm,
          drawthings_config_id: cfg.dt,
        });
        ElementPlus.ElMessage.success(I18N.t('mw.cfgSaved'));
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        cfgBusy.value = false;
      }
    }

    async function delWork() {
      try {
        await API.post(`/api/micro/${props.id}/delete`);
        ElementPlus.ElMessage.success(I18N.t('mw.workDeleted'));
        router.push('/micro');
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
        const el = document.querySelector('.mc-inputbar .mc-input');
        if (el) {
          el.style.height = 'auto';
          el.style.height = Math.min(el.scrollHeight, 140) + 'px';
        }
      });
    }

    function openLb(list, idx) {
      lb.list = list;
      lb.idx = idx;
      lb.show = true;
    }

    // Enter 发送：中文输入法（拼音候选词）组合中按回车仅确认候选、不发送
    function onEnter(e) {
      if (e.isComposing || e.keyCode === 229) return;
      e.preventDefault();
      send();
    }

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

    // ---------- 发送（SSE 流式） ----------
    async function send() {
      if (busy.value || !hasSession.value) return;
      const message = input.value.trim();
      if (!message && !attached.value.length) return;

      busy.value = true;
      const shot = attached.value.slice();
      input.value = '';
      attached.value = [];
      autoResize();
      startTimer();

      msgs.value.push({ index: Date.now(), role: 'user', content: message || I18N.t('mw.image'),
                        images: shot, media_url: '' });
      streaming.value = true;
      stream.text = '';
      stream.chips = [];
      stream.media = [];
      status.value = '';
      scrollBottom();

      let failed = false;
      const ctrl = new AbortController();
      sseCtrl = ctrl;
      try {
        await API.sse(`/api/micro/${props.id}/${props.sid}/chat`, { message, images: shot }, (ev, d) => {
          if (ev === 'token') {
            stream.text += d.text;
            status.value = '';
            scrollBottom();
          } else if (ev === 'tool') {
            stream.chips.push({ text: d.label || I18N.t('mw.saving'), err: false, prompt: d.prompt || '' });
            status.value = '';
          } else if (ev === 'media') {
            stream.media.push(d);
            scrollBottom();
          } else if (ev === 'tool_error') {
            stream.chips.push({ text: '⚠ ' + (d.message || I18N.t('mw.genFail')), err: true, prompt: d.prompt || '' });
          } else if (ev === 'error') {
            failed = true;
            stream.chips.push({ text: '⚠ ' + (d.message || I18N.t('mw.err')), err: true });
          }
        }, ctrl.signal);
        if (failed) {
          status.value = I18N.t('mw.errRetry');
        } else {
          // 落库完成：重新拉取持久化历史（含左侧列表/标题同步）
          await load();
        }
      } catch (e) {
        if (!ctrl.signal.aborted) {
          stream.chips.push({ text: '⚠ ' + e.message, err: true });
          status.value = I18N.t('mw.errRetry');
        }
      } finally {
        stopTimer();
        busy.value = false;
        streaming.value = false;
        if (sseCtrl === ctrl) sseCtrl = null;
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

    watch(() => props.id, () => {
      worksLoaded.value = false;
      selWorks.value = [];
    });
    watch(() => [props.id, props.sid], () => {
      if (!busy.value) load();
    });
    watch(tab, (v) => { if (v === 'works' && !worksLoaded.value) loadWorks(); });
    onMounted(load);
    onBeforeUnmount(() => {
      stopTimer();
      if (sseCtrl) { sseCtrl.abort(); sseCtrl = null; }
      const el = chatBox.value;
      if (el) el.removeEventListener('click', onChatClick);
    });
    onMounted(() => {
      nextTick(() => {
        const el = chatBox.value;
        if (el) el.addEventListener('click', onChatClick);
      });
    });

    return {
      data, msgs, hasSession, sideHidden, setSide, tab,
      sessDlg, sessTitle, createSess, renameDlg, renameTitle, askRename, doRename, delSess,
      cfgBusy, cfg, saveCfg, delWork,
      works, worksBusy, selWorks, allWSel, isWSel, toggleWSel, toggleAllWorks, delWorks,
      lb, openLb, input, attached, busy, status, drag, streaming, stream, streamHtml,
      isMediaVideo, tagLabel, tagType, ph, mwDtNone,
      chatBox, fileInput, onFiles, onPaste, onDrop, autoResize, onEnter, send,
      streamElapsed,
      renderMd, router, pick, openSess,
      EditPen: ElementPlusIconsVue.EditPen, Delete: ElementPlusIconsVue.Delete,
    };
  },
};
