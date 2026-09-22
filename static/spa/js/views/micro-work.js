// 微创作作品页：tab = 对话（左会话列表 + 右对话）/ 作品（生成媒体画廊，多选批量删）/ 设置（作品选项）
// 对话：SSE 流式（token/tool/media/tool_error/error/done），按「有序内容块」渲染——
// 文本与生成结果严格按发生顺序排列，工具块按 id 从「生成中」收束为「已生成 / 失败」，
// 提示词只展示一次（可折叠），刷新后与流式过程完全一致。
// 另含 Markdown、附图（视觉模型：选择/粘贴/拖拽）、图片放大、贴底自动滚动。
window.Views = window.Views || {};

// 单个内容块：text（Markdown）/ tool（生成中→已生成/失败 + 媒体 + 可折叠提示词）/ media（旧数据兼容）/ error
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

Views.microWork = {
  components: { MicroBlock: Views.microBlock },
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
      <div class="mc-pane mc-pane-chat">
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
          <div class="mc-chat" ref="chatBox" @scroll="onChatScroll">
            <div class="mc-chat-inner" ref="chatInner">
            <el-empty v-if="!hasSession" :description="I18N.t('mw.noSessionEmpty')" :image-size="72" />
            <template v-else>
              <div v-for="(m, mi) in msgs" :key="mi" class="msg" :class="m.role">
                <template v-if="m.role === 'user'">
                  <div class="msg-text" v-if="m.content">{{ m.content }}</div>
                  <div class="msg-imgs" v-if="m.images.length">
                    <img v-for="(u, i) in m.images" :key="i" :src="u" :alt="I18N.t('mw.attachAlt')" loading="lazy" decoding="async" @click="openLb(m.images, i)">
                  </div>
                </template>
                <template v-else>
                  <micro-block v-for="(b, bi) in m.blocks" :key="bi" :b="b" @preview="openLb" />
                  <div class="msg-time" v-if="m.duration">{{ m.duration }}s</div>
                </template>
              </div>

              <div v-if="streaming" class="msg assistant">
                <micro-block v-for="(b, bi) in stream.parts" :key="bi" :b="b" :cursor="streamCursor(bi)" @preview="openLb" />
                <div v-if="showTyping" class="md streaming-tail"><span class="typing"><i></i><i></i><i></i></span></div>
                <div class="msg-time">{{ streamElapsed }}s</div>
              </div>
            </template>
            </div>
          </div>

          <button v-if="hasSession && !atBottom" type="button" class="scroll-btn" :title="I18N.t('mw.scrollBottom')" @click="scrollBottom(true)">↓</button>

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
              <textarea ref="inputEl" v-model="input" class="mc-input" rows="1" :placeholder="ph"
                        @keydown.enter.exact="onEnter" @input="autoResize" @paste="onPaste"></textarea>
              <el-button type="primary" :disabled="busy" @click="send">{{ I18N.t('mw.send') }}</el-button>
            </div>
            <div class="mc-status" v-if="status">{{ status }}</div>
          </div>
        </section>
      </div>
      </div>
      </el-tab-pane>

      <!-- ============ 作品（该作品下所有会话生成的媒体，图片 / 视频 分区：导出 + 多选批量删除）============ -->
      <el-tab-pane :label="I18N.t('mw.tabWorks')" name="works">
        <div class="mc-pane mc-pane-scroll">
        <div class="works-toolbar">
          <b class="muted small">{{ I18N.t('mw.works', works.length) }}</b>
          <div style="flex: 1"></div>
          <el-popconfirm :title="I18N.t('mw.worksDelConfirm', selWorks.length)" @confirm="delWorks">
            <template #reference>
              <el-button type="danger" plain :disabled="!selWorks.length || worksBusy">{{ I18N.t('mw.worksDelSel', selWorks.length) }}</el-button>
            </template>
          </el-popconfirm>
        </div>

        <el-tabs v-model="worksKind" class="works-tabs">
          <el-tab-pane v-for="sec in sections" :key="sec.kind" :name="sec.kind"
                       :label="I18N.t(sec.kind === 'image' ? 'mw.imagesN' : 'mw.videosN', sec.items.length)">
            <div class="works-sec-actions">
              <el-checkbox :model-value="sec.allSel" :disabled="!sec.items.length" @change="toggleAllKind(sec.kind)">{{ I18N.t('mw.worksSelAll') }}</el-checkbox>
              <el-button size="small" :disabled="!sec.selIds.length || exporting" @click="exportWorks(sec.selIds, 'zip')">{{ I18N.t('mw.exportSelZip') }}</el-button>
              <el-button v-if="sec.kind === 'image'" size="small" :disabled="!sec.selIds.length || exporting" @click="exportWorks(sec.selIds, 'pdf')">{{ I18N.t('mw.exportSelPdf') }}</el-button>
              <el-button size="small" :disabled="!sec.items.length || exporting" @click="exportWorks(sec.ids, 'zip')">{{ I18N.t('mw.exportAllZip') }}</el-button>
              <el-button v-if="sec.kind === 'image'" size="small" :disabled="!sec.items.length || exporting" @click="exportWorks(sec.ids, 'pdf')">{{ I18N.t('mw.exportAllPdf') }}</el-button>
            </div>
            <div class="works-grid" v-if="sec.items.length">
              <div v-for="w in sec.items" :key="w.id" class="work-piece" :class="{ sel: isWSel(w.id) }">
                <div class="wp-media">
                  <video v-if="sec.kind === 'video'" :src="w.media_url" controls preload="metadata"></video>
                  <img v-else :src="w.media_url" :alt="I18N.t('mw.resultAlt')" loading="lazy" decoding="async" @click="openLb([w.media_url], 0)">
                  <el-checkbox class="wp-check" :model-value="isWSel(w.id)" @click.stop @change="toggleWSel(w.id)"></el-checkbox>
                </div>
                <div class="wp-cap" v-if="w.prompt || w.content">{{ w.prompt || w.content }}</div>
                <div class="wp-src muted">{{ (w.session_title || I18N.t('mw.newSession')) }} · {{ w.created_at.slice(0, 10) }}</div>
                <div class="wp-actions">
                  <el-button size="small" text :disabled="exporting" @click="exportWorks([w.id], 'zip')">{{ I18N.t('mw.exportZip') }}</el-button>
                  <el-button v-if="sec.kind === 'image'" size="small" text :disabled="exporting" @click="exportWorks([w.id], 'pdf')">{{ I18N.t('mw.exportPdf') }}</el-button>
                </div>
              </div>
            </div>
            <el-empty v-else :description="I18N.t(sec.kind === 'image' ? 'mw.noImages' : 'mw.noVideos')" :image-size="48" />
          </el-tab-pane>
        </el-tabs>
        </div>
      </el-tab-pane>

      <!-- ============ 设置（作品选项，作用于全部会话）============ -->
      <el-tab-pane :label="I18N.t('mw.tabSettings')" name="settings">
        <div class="mc-pane mc-pane-scroll">
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
        </div>
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
    const worksKind = ref('image');  // 作品页签内的二级 tab：图片 / 视频
    function isWSel(id) { return selWorks.value.indexOf(id) >= 0; }
    function toggleWSel(id) {
      const k = selWorks.value.indexOf(id);
      if (k >= 0) selWorks.value.splice(k, 1);
      else selWorks.value.push(id);
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

    // 作品分区：图片 / 视频 各自 单个 / 勾选 / 全部 导出
    const exporting = ref(false);
    const sections = computed(() => {
      const mk = (kind, items) => ({
        kind, items,
        ids: items.map(w => w.id),
        selIds: items.filter(w => selWorks.value.includes(w.id)).map(w => w.id),
        allSel: items.length > 0 && items.every(w => selWorks.value.includes(w.id)),
      });
      return [
        mk('image', works.value.filter(w => !isMediaVideo(w.media_url))),
        mk('video', works.value.filter(w => isMediaVideo(w.media_url))),
      ];
    });
    function toggleAllKind(kind) {
      const items = works.value.filter(w => (kind === 'video') === isMediaVideo(w.media_url));
      const allSel = items.length > 0 && items.every(w => selWorks.value.includes(w.id));
      const ids = items.map(w => w.id);
      selWorks.value = allSel
        ? selWorks.value.filter(id => !ids.includes(id))
        : Array.from(new Set([...selWorks.value, ...ids]));
    }
    async function exportWorks(ids, format) {
      if (!ids.length || exporting.value) return;
      exporting.value = true;
      try {
        await API.download(`/api/micro/${props.id}/works/export?ids=${ids.join(',')}&format=${format}`);
        API.notify(I18N.t('app.title'), I18N.t('p.exportDone'));
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        exporting.value = false;
      }
    }

    // 图片放大
    const lb = reactive({ show: false, list: [], idx: 0 });
    function openLb(list, idx) {
      lb.list = list;
      lb.idx = idx;
      lb.show = true;
    }

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

    function isMediaVideo(url) {  // 按文件扩展名判断（媒体落盘时按实际内容定扩展名；URL 可能带 ?v= 缓存参数）
      return /\.(mp4|mov|webm|gif)(\?|$)/i.test(url || '');
    }

    // 持久化消息 -> 有序内容块（旧数据无 parts：文本在前、媒体在后，保持可读）
    function normalizeMsg(m) {
      if (m.role !== 'assistant') return m;
      let blocks = [];
      if (Array.isArray(m.parts) && m.parts.length) {
        blocks = m.parts;
      } else {
        if (m.content) blocks.push({ type: 'text', text: m.content });
        if (m.media_url) blocks.push({ type: 'media', url: m.media_url, prompt: m.prompt || '' });
      }
      return Object.assign({}, m, { blocks });
    }

    const tagLabel = computed(() => {
      if (!data.value) return '';
      return data.value.work.dt_name ? I18N.t('mw.tagGen') : I18N.t('mw.tagChat');
    });
    const tagType = computed(() => tagLabel.value === I18N.t('mw.tagChat') ? 'info' : 'primary');
    const ph = computed(() => (data.value?.work.vision ? I18N.t('mw.phVision') : I18N.t('mw.phPlain')));

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

    async function load() {
      try {
        if (props.sid) {
          data.value = await API.get(`/api/micro/${props.id}/${props.sid}`);
          msgs.value = (data.value.messages || []).map(normalizeMsg);
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
        atBottom.value = true;
        pinned = true;
        scrollBottom(true);
        setupObserver();
      } catch (e) {
        if (e.status === 404) router.replace('/micro');
        ElementPlus.ElMessage.error(e.message);
      }
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
    function onError(d) {
      stream.parts.push({ type: 'error', message: d.message || I18N.t('mw.err') });
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
          if (ev === 'token') {
            pushText(d.text);
            status.value = '';
            scrollBottom(false);
          } else if (ev === 'tool') {
            onTool(d);
            status.value = '';
            scrollBottom(false);
          } else if (ev === 'media') {
            onMedia(d);
            scrollBottom(false);
          } else if (ev === 'tool_error') {
            onToolError(d);
          } else if (ev === 'error') {
            failed = true;
            onError(d);
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
          onError({ message: e.message });
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
    // 切换作品 / 会话：中断进行中的流并重新加载（不再静默卡住）
    watch(() => [props.id, props.sid], () => {
      if (busy.value && sseCtrl) sseCtrl.abort();
      stopTimer();
      busy.value = false;
      streaming.value = false;
      stream.parts = [];
      load();
    });
    watch(tab, (v) => { if (v === 'works' && !worksLoaded.value) loadWorks(); });
    onMounted(() => {
      load();
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
      data, msgs, hasSession, sideHidden, setSide, tab,
      sessDlg, sessTitle, createSess, renameDlg, renameTitle, askRename, doRename, delSess,
      cfgBusy, cfg, saveCfg, delWork,
      works, worksBusy, worksKind, selWorks, isWSel, toggleWSel, delWorks,
      sections, toggleAllKind, exportWorks, exporting,
      lb, openLb, input, attached, busy, status, drag, streaming, stream,
      isMediaVideo, tagLabel, tagType, ph,
      chatBox, inputEl, fileInput, onFiles, onPaste, onDrop, autoResize, onEnter, send,
      streamElapsed, streamCursor, showTyping, atBottom, onChatScroll, scrollBottom, chatInner,
      router, pick, openSess,
      EditPen: ElementPlusIconsVue.EditPen, Delete: ElementPlusIconsVue.Delete,
    };
  },
};
