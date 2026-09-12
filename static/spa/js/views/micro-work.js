// 微创作作品页：左=会话列表（折叠记忆），右=当前会话对话
// 对话：SSE 流式（token/tool/media/tool_error/error/done）+ Markdown + 附图（视觉模型：选择/粘贴/拖拽）+ 图片放大
window.Views = window.Views || {};
Views.microWork = {
  props: ['id', 'sid'],
  template: `
    <div class="page mc-page" v-if="data">
      <div class="mc-head">
        <div>
          <el-tag size="small" :type="tagType" effect="plain">{{ tagLabel }}</el-tag>
          <h1 class="ptitle">{{ data.work.title || '（未命名作品）' }}</h1>
          <p class="meta muted">LLM：{{ data.work.llm_name }} · DrawThings：{{ data.work.dt_name }}</p>
        </div>
        <div class="proj-head-actions">
          <el-button @click="openCfg">⚙ 作品选项</el-button>
          <el-button @click="router.push('/micro')">← 返回</el-button>
          <el-popconfirm title="删除该作品？全部会话/消息与已生成的媒体将一并删除。" @confirm="delWork">
            <template #reference><el-button type="danger" plain>🗑 删除</el-button></template>
          </el-popconfirm>
        </div>
      </div>

      <div class="mc-body" :class="{ 'side-hidden': sideHidden }">
        <button v-if="sideHidden" type="button" class="mc-expand" title="展开会话列表" @click="setSide(false)">‹ 会话</button>
        <aside class="mc-side" v-show="!sideHidden">
          <div class="mc-side-head">
            <b>会话（{{ data.sessions.length }}）</b>
            <div style="display: flex; gap: 4px;">
              <el-button size="small" type="primary" plain @click="openSess">＋ 新建</el-button>
              <el-button size="small" text title="收起" @click="setSide(true)">«</el-button>
            </div>
          </div>
          <div class="mc-sess-list">
            <div v-for="s in data.sessions" :key="s.id" class="mc-sess" :class="{ active: s.id === sid }" @click="pick(s.id)">
              <div class="ms-title">{{ s.title || '（新会话）' }}</div>
              <div class="ms-meta muted">{{ s.created_at.slice(0, 10) }} · {{ s.msg_count }} 条消息</div>
              <div class="ms-actions">
                <el-button size="small" text :icon="EditPen" @click.stop="askRename(s)">重命名</el-button>
                <el-popconfirm title="删除该会话？消息与媒体将一并删除。" @confirm="delSess(s)">
                  <template #reference>
                    <el-button size="small" text type="danger" :icon="Delete" @click.stop>删除</el-button>
                  </template>
                </el-popconfirm>
              </div>
            </div>
            <el-empty v-if="!data.sessions.length" description="暂无会话，点「＋ 新建」" :image-size="48" />
          </div>
        </aside>

        <section class="mc-main">
          <div class="mc-chat" ref="chatBox">
            <el-empty v-if="!hasSession" description="暂无会话。点左上「＋ 新建」开始对话。" :image-size="72" />
            <template v-else>
              <div v-for="m in msgs" :key="m.index" class="msg" :class="m.role">
                <div v-if="m.role === 'user'">
                  <span class="msg-text">{{ m.content || '（图片）' }}</span>
                  <div class="msg-imgs" v-if="m.images.length">
                    <img v-for="(u, i) in m.images" :key="i" :src="u" alt="附图" @click="openLb(m.images, i)">
                  </div>
                </div>
                <div v-else>
                  <div class="msg-media" v-if="m.media_url">
                    <video v-if="isMediaVideo(m.media_url)" :src="m.media_url" controls preload="metadata"></video>
                    <img v-else :src="m.media_url" alt="生成结果" @click="openLb([m.media_url], 0)">
                    <div class="media-cap" v-if="m.prompt">{{ m.prompt }}</div>
                  </div>
                  <div class="md" v-html="renderMd(m.content)"></div>
                </div>
              </div>
              <div v-if="streaming" class="msg assistant">
                <div v-for="(c, i) in stream.chips" :key="'c' + i" class="tool-chip" :class="{ error: c.err }">
                  <span v-if="!c.err" class="spinner"></span>{{ c.text }}
                </div>
                <div v-for="(md2, i) in stream.media" :key="'m' + i" class="msg-media">
                  <video v-if="md2.media === 'video'" :src="md2.url" controls preload="metadata"></video>
                  <img v-else :src="md2.url" alt="生成结果" @click="openLb([md2.url], 0)">
                  <div class="media-cap" v-if="md2.prompt">{{ md2.prompt }}</div>
                </div>
                <div class="md" v-html="streamHtml"></div>
              </div>
            </template>
          </div>

          <div class="mc-inputbar" v-if="hasSession" :class="{ drag }"
               @dragover.prevent="drag = true" @dragleave="drag = false" @drop.prevent="onDrop">
            <div class="mc-previews" v-if="attached.length">
              <div v-for="(d, i) in attached" :key="i" class="mc-prev">
                <img :src="d" alt="附图">
                <button type="button" class="mc-prev-x" title="移除" @click="attached.splice(i, 1)">×</button>
              </div>
            </div>
            <div class="mc-inputrow">
              <button v-if="data.work.vision" type="button" class="mc-attach" title="附图（仅视觉模型；可粘贴 / 拖拽，最多 4 张）"
                      @click="fileInput.click()">🖼</button>
              <input type="file" ref="fileInput" accept="image/*" multiple hidden @change="onFiles">
              <textarea v-model="input" class="mc-input" rows="1" :placeholder="ph"
                        @keydown.enter.exact.prevent="send" @input="autoResize" @paste="onPaste"></textarea>
              <el-button type="primary" :disabled="busy" @click="send">发送</el-button>
            </div>
            <div class="mc-status">{{ status }}</div>
          </div>
        </section>
      </div>

      <el-dialog v-model="sessDlg" title="新建会话" width="440px">
        <el-input v-model="sessTitle" placeholder="标题（可选，留空自动取首条消息）" maxlength="200" />
        <template #footer>
          <el-button @click="sessDlg = false">取消</el-button>
          <el-button type="primary" @click="createSess">创建</el-button>
        </template>
      </el-dialog>

      <el-dialog v-model="renameDlg" title="重命名会话" width="440px">
        <el-input v-model="renameTitle" maxlength="200" />
        <template #footer>
          <el-button @click="renameDlg = false">取消</el-button>
          <el-button type="primary" @click="doRename">保存</el-button>
        </template>
      </el-dialog>

      <el-dialog v-model="cfgDlg" title="作品选项" width="560px">
        <p class="hint">作用于该作品的全部会话。</p>
        <el-form label-position="top">
          <el-form-item label="标题">
            <el-input v-model="cfg.title" maxlength="200" placeholder="留空自动取首条消息" />
          </el-form-item>
          <el-form-item label="LLM 配置">
            <el-select v-model="cfg.llm" style="width: 100%">
              <el-option v-for="c in data.llm_configs" :key="c.id" :value="c.id"
                         :label="c.name + '（' + c.model + (c.supports_vision === 'no' ? ' / 纯文本' : '') + '）'" />
            </el-select>
          </el-form-item>
          <el-form-item label="DrawThings 配置">
            <el-select v-model="cfg.dt" clearable style="width: 100%">
              <el-option value="" label="不选（纯对话，不出媒体）" />
              <el-option v-for="c in data.drawthing_configs" :key="c.id" :value="c.id" :label="c.name" />
            </el-select>
            <div class="hint">产出类型（图像/视频）由 app 当前加载的模型自动判断，无需选择。</div>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="cfgDlg = false">取消</el-button>
          <el-button type="primary" :loading="cfgBusy" @click="saveCfg">保存</el-button>
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

    // 作品选项
    const cfgDlg = ref(false);
    const cfgBusy = ref(false);
    const cfg = reactive({ title: '', llm: '', dt: '' });

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
    const stream = reactive({ text: '', chips: [], media: [] });
    const streamHtml = computed(() => stream.text
      ? renderMd(stream.text) + '<span class="cursor"></span>'
      : '<span class="typing"><i></i><i></i><i></i></span>');
    function isMediaVideo(url) {  // 按文件扩展名判断（媒体落盘时按实际内容定扩展名）
      return /\.(mp4|mov|webm|gif)$/i.test(url || '');
    }
    const tagLabel = computed(() => {
      if (!data.value) return '';
      return (data.value.work.dt_name && data.value.work.dt_name !== '不选（纯对话）') ? '生成' : '纯对话';
    });
    const tagType = computed(() => tagLabel.value === '纯对话' ? 'info' : 'primary');
    const ph = computed(() => '对话…（Enter 发送，Shift+Enter 换行' + (data.value?.work.vision ? '，可附图）' : '）'));

    async function load() {
      try {
        if (props.sid) {
          data.value = await API.get(`/api/micro/${props.id}/${props.sid}`);
          msgs.value = data.value.messages || [];
          hasSession.value = true;
        } else {
          data.value = await API.get(`/api/micro/${props.id}`);
          msgs.value = [];
          hasSession.value = false;
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
        ElementPlus.ElMessage.success('已重命名');
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }
    async function delSess(s) {
      try {
        await API.post(`/api/micro/${props.id}/${s.id}/delete`);
        ElementPlus.ElMessage.success('已删除会话');
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

    function openCfg() {
      cfg.title = data.value.work.title;
      cfg.llm = data.value.work.llm_config_id;
      cfg.dt = data.value.work.drawthings_config_id || '';
      cfgDlg.value = true;
    }
    async function saveCfg() {
      cfgBusy.value = true;
      try {
        await API.post(`/api/micro/${props.id}/settings`, {
          title: cfg.title, llm_config_id: cfg.llm,
          drawthings_config_id: cfg.dt,
        });
        ElementPlus.ElMessage.success('已保存，作用于全部会话');
        cfgDlg.value = false;
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
        ElementPlus.ElMessage.success('已删除作品');
        router.push('/micro');
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    // ---------- 附图（仅视觉模型）：选择 / 粘贴 / 拖拽 ----------
    function addFiles(files) {
      for (const f of files) {
        if (!f.type || !f.type.startsWith('image/')) continue;
        if (attached.value.length >= 4) { status.value = '最多附带 4 张图片'; break; }
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

      msgs.value.push({ index: Date.now(), role: 'user', content: message || '（图片）', images: shot, media_url: '' });
      streaming.value = true;
      stream.text = '';
      stream.chips = [];
      stream.media = [];
      status.value = '';
      scrollBottom();

      let failed = false;
      try {
        await API.sse(`/api/micro/${props.id}/${props.sid}/chat`, { message, images: shot }, (ev, d) => {
          if (ev === 'token') {
            stream.text += d.text;
            status.value = '';
            scrollBottom();
          } else if (ev === 'tool') {
            stream.chips.push({ text: d.label || '生成中…', err: false });
            status.value = '';
          } else if (ev === 'media') {
            stream.media.push(d);
            scrollBottom();
          } else if (ev === 'tool_error') {
            stream.chips.push({ text: '⚠ ' + (d.message || '生成失败'), err: true });
          } else if (ev === 'error') {
            failed = true;
            stream.chips.push({ text: '⚠ ' + (d.message || '出错了'), err: true });
          }
        });
        if (failed) {
          status.value = '出错了，可修改后重试';
        } else {
          // 落库完成：重新拉取持久化历史（含左侧列表/标题同步）
          await load();
        }
      } catch (e) {
        stream.chips.push({ text: '⚠ ' + e.message, err: true });
        status.value = '出错了，可修改后重试';
      } finally {
        busy.value = false;
        streaming.value = false;
      }
    }

    // 代码块一键复制（事件委托）
    function onChatClick(e) {
      const btn = e.target.closest && e.target.closest('.copy-code');
      if (!btn) return;
      const code = btn.parentElement.querySelector('code');
      if (code && navigator.clipboard) {
        navigator.clipboard.writeText(code.textContent).then(() => {
          btn.textContent = '已复制';
          setTimeout(() => { btn.textContent = '复制'; }, 1500);
        });
      }
    }

    watch(() => [props.id, props.sid], () => {
      if (!busy.value) load();
    });
    onMounted(load);
    onBeforeUnmount(() => {
      // 组件卸载时解绑委托事件（若有）
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
      data, msgs, hasSession, sideHidden, setSide,
      sessDlg, sessTitle, createSess, renameDlg, renameTitle, askRename, doRename, delSess,
      cfgDlg, cfgBusy, cfg, openCfg, saveCfg, delWork,
      lb, openLb, input, attached, busy, status, drag, streaming, stream, streamHtml,
      isMediaVideo, tagLabel, tagType, ph,
      chatBox, fileInput, onFiles, onPaste, onDrop, autoResize, send,
      renderMd, router, pick, openSess,
      EditPen: ElementPlusIconsVue.EditPen, Delete: ElementPlusIconsVue.Delete,
    };
  },
};
