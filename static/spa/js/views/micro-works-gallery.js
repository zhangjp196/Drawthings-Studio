// 微创作「作品」画廊：该作品下所有会话生成的媒体，按图片 / 视频二级分区，
// 支持 单个 / 勾选 / 全部 导出（图 ZIP·PDF、视频 ZIP）与多选批量删除。
// 自包含数据加载（props.id）；删除后 emit('changed') 让父组件刷新会话计数。
window.Views = window.Views || {};

Views.microWorksGallery = {
  props: {
    id: { type: String, required: true },
  },
  emits: ['preview', 'changed'],
  template: `
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
              <img v-else :src="w.media_url" :alt="I18N.t('mw.resultAlt')" loading="lazy" decoding="async" @click="$emit('preview', [w.media_url], 0)">
              <el-checkbox class="wp-check" :model-value="isWSel(w.id)" @click.stop @change="toggleWSel(w.id)"></el-checkbox>
            </div>
            <div class="wp-cap" v-if="w.prompt || w.content">{{ w.prompt || w.content }}</div>
            <div class="wp-src muted">{{ (w.session_title || I18N.t('mw.newSession')) }} · {{ (w.created_at || '').slice(0, 10) }}</div>
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
  `,
  setup(props, { emit }) {
    const works = ref([]);
    const selWorks = ref([]);
    const worksBusy = ref(false);
    const worksKind = ref('image');  // 作品页签内的二级 tab：图片 / 视频
    const exporting = ref(false);

    function isMediaVideo(url) {  // 按文件扩展名判断（媒体落盘时按实际内容定扩展名；URL 可能带 ?v= 缓存参数）
      return /\.(mp4|mov|webm|gif)(\?|$)/i.test(url || '');
    }
    function isWSel(id) { return selWorks.value.indexOf(id) >= 0; }
    function toggleWSel(id) {
      const k = selWorks.value.indexOf(id);
      if (k >= 0) selWorks.value.splice(k, 1);
      else selWorks.value.push(id);
    }
    async function load() {
      try {
        const r = await API.get(`/api/micro/${props.id}/works`);
        works.value = r.works || [];
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
        await load();
        emit('changed');  // 同步左侧会话列表的消息数
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        worksBusy.value = false;
      }
    }

    // 作品分区：图片 / 视频 各自 单个 / 勾选 / 全部 导出
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

    onMounted(load);

    return {
      works, selWorks, worksBusy, worksKind, exporting,
      isMediaVideo, isWSel, toggleWSel, delWorks, sections, toggleAllKind, exportWorks,
    };
  },
};
