// 创作中心：列表（筛选/分页/缩略图）+ 新建弹框 + 重命名 + 删除
window.Views = window.Views || {};
Views.projects = {
  components: { 'create-form-comic': Views.createFormComic, 'create-form-drama': Views.createFormDrama },
  template: `
    <div class="page">
      <div class="list-toolbar">
        <div class="list-head">
          <h1>{{ kindTitle }}</h1>
          <span class="muted">{{ I18N.t('proj.total', total) }}</span>
        </div>
        <el-button type="primary" @click="openNew">{{ I18N.t('wb.newProject') }}</el-button>
      </div>

      <el-card class="filter-card" shadow="never">
        <div class="filter-row">
          <el-input v-model="f.q" :placeholder="I18N.t('proj.searchPh')" clearable style="width: 220px"
                    @input="onSearch" @keyup.enter="apply" @clear="apply" />
          <el-select v-model="f.kind" :placeholder="I18N.t('proj.type')" style="width: 120px" @change="apply">
            <el-option :label="I18N.t('proj.comic')" value="comic" />
            <el-option :label="I18N.t('proj.drama')" value="drama" />
          </el-select>
          <el-select v-model="f.status" :placeholder="I18N.t('proj.status')" clearable style="width: 140px" @change="apply">
            <el-option :label="I18N.t('proj.all')" value="" />
            <el-option v-for="(v, k) in statusLabels" :key="k" :label="v" :value="k" />
          </el-select>
          <el-select v-model="f.sort" style="width: 130px" @change="apply">
            <el-option :label="I18N.t('proj.sortNew')" value="desc" />
            <el-option :label="I18N.t('proj.sortOld')" value="asc" />
            <el-option :label="I18N.t('proj.sortActive')" value="active" />
          </el-select>
          <el-select v-model="f.size" style="width: 110px" @change="apply">
            <el-option v-for="n in [10, 20, 50]" :key="n" :label="I18N.t('proj.perPage', n)" :value="n" />
          </el-select>
          <el-button @click="reset">{{ I18N.t('proj.reset') }}</el-button>
        </div>
      </el-card>

      <div class="proj-grid" v-if="rows.length">
        <div class="proj-card" v-for="row in rows" :key="row.id" @click="open(row)">
          <div class="pc-thumb">
            <img v-if="row.first_image_url" :src="row.first_image_url" :alt="row.title || ''"
                 loading="lazy" decoding="async">
            <span v-else class="pc-ph">{{ row.kind === 'comic' ? '🖼' : '🎬' }}</span>
          </div>
          <div class="pc-body">
            <div class="pc-tags">
              <el-tag size="small" :type="row.kind === 'comic' ? 'primary' : 'success'" effect="light">
                {{ row.kind === 'comic' ? I18N.t('proj.comic') : I18N.t('proj.drama') }}
              </el-tag>
              <el-tag size="small" :type="statusTag(row.status)" effect="light">{{ statusLabels[row.status] || row.status }}</el-tag>
            </div>
            <div class="pc-title">{{ row.title || row.origin }}</div>
            <div class="pc-meta muted">{{ row.chapter_count }} · {{ row.updated_at.slice(0, 10) }}</div>
            <div class="pc-actions" @click.stop>
              <el-button size="small" @click="askRename(row)">{{ I18N.t('proj.rename') }}</el-button>
              <el-popconfirm :title="I18N.t('proj.delConfirm')" @confirm="del(row)">
                <template #reference><el-button size="small" type="danger" plain>{{ I18N.t('proj.delete') }}</el-button></template>
              </el-popconfirm>
            </div>
          </div>
        </div>
      </div>
      <el-empty v-else :description="I18N.t('proj.empty')" />

      <el-pagination v-if="totalPages > 1" class="pager" background layout="prev, pager, next"
                     :total="total" :page-size="f.size" :current-page="f.page" @current-change="load" />

      <!-- 新建弹框：漫画 / 短剧完全分开，各自独立组件 -->
      <el-dialog v-model="newComicDlg" :title="I18N.t('proj.newDlgComic')" width="880px">
        <create-form-comic @created="created" />
      </el-dialog>
      <el-dialog v-model="newDramaDlg" :title="I18N.t('proj.newDlgDrama')" width="880px">
        <create-form-drama @created="created" />
      </el-dialog>

      <el-dialog v-model="renameDlg" :title="I18N.t('proj.renameDlg')" width="440px">
        <el-input v-model="renameTitle" :placeholder="I18N.t('proj.newTitlePh')" maxlength="200" />
        <template #footer>
          <el-button @click="renameDlg = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" :loading="busy" @click="doRename">{{ I18N.t('common.save') }}</el-button>
        </template>
      </el-dialog>
    </div>
  `,
  setup() {
    const statusLabels = computed(() => {
      const t = I18N.t;
      return {
        planning: t('proj.status.planning'), arced: t('proj.status.arced'),
        chaptered: t('proj.status.chaptered'), done: t('proj.status.done'),
      };
    });
    const f = reactive({ page: 1, size: 10, q: '', kind: (router.currentRoute.value.query.kind === 'drama' ? 'drama' : 'comic'), status: '', sort: 'desc' });
    // 页标题随类型筛选变化：漫画创作 / 视频创作 / 创作中心（全部）
    const kindTitle = computed(() =>
      f.kind === 'comic' ? I18N.t('nav.studioComic')
        : f.kind === 'drama' ? I18N.t('nav.studioDrama')
        : I18N.t('proj.title'));
    // 新建弹框：漫画 / 短剧各一个独立弹框（各自组件）；类型跟随页面类型筛选，未筛选默认漫画
    const newComicDlg = ref(false);
    const newDramaDlg = ref(false);
    function openNew() { (f.kind === 'drama' ? newDramaDlg : newComicDlg).value = true; }
    const rows = ref([]);
    const total = ref(0);
    const totalPages = ref(1);
    const renameDlg = ref(false);
    const renameTitle = ref('');
    const renameId = ref('');
    const renameKind = ref('comic');
    const busy = ref(false);

    async function load() {
      try {
        // 创作中心按类型分开：漫画走 /api/comics、短剧走 /api/dramas
        const data = await API.get(`/api/${f.kind === 'drama' ? 'dramas' : 'comics'}?` + new URLSearchParams({
          page: f.page, size: f.size, q: f.q, status: f.status, sort: f.sort,
        }));
        rows.value = data.projects;
        total.value = data.total;
        totalPages.value = data.total_pages;
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }
    function apply() { f.page = 1; syncKindQuery(); load(); }
    // 类型筛选同步到 URL（?kind=）：侧边栏「漫画/视频创作」高亮与刷新后恢复依赖它
    function syncKindQuery() {
      const cur = router.currentRoute.value.query.kind || '';
      if (cur !== (f.kind || '')) {
        router.replace({ path: '/projects', query: f.kind ? { kind: f.kind } : {} });
      }
    }
    // 同页点击侧边栏「漫画/视频创作」：URL 变化时同步筛选（不重建组件）
    watch(() => router.currentRoute.value.query.kind, (k) => {
      k = (k === 'drama') ? 'drama' : 'comic';
      if (k !== f.kind) { f.kind = k; f.page = 1; load(); }
    });
    const onSearch = debounce(apply, 350);
    function reset() {
      Object.assign(f, { page: 1, size: 10, q: '', status: '', sort: 'desc' });
      syncKindQuery();
      load();
    }
    const statusTag = (s) => (s === 'done' ? 'success' : s === 'planning' ? 'info' : 'primary');

    function open(row) { router.push(row.kind === 'comic' ? '/comic/' + row.id : '/drama/' + row.id); }
    function askRename(row) {
      renameId.value = row.id;
      renameKind.value = row.kind === 'drama' ? 'drama' : 'comic';
      renameTitle.value = row.title || row.origin;
      renameDlg.value = true;
    }
    async function doRename() {
      busy.value = true;
      try {
        await API.post(`/api/${renameKind.value === 'drama' ? 'dramas' : 'comics'}/` + renameId.value + '/rename', { title: renameTitle.value });
        renameDlg.value = false;
        ElementPlus.ElMessage.success(I18N.t('proj.msgRenamed'));
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        busy.value = false;
      }
    }
    async function del(row) {
      try {
        await API.post(`/api/${row.kind === 'drama' ? 'dramas' : 'comics'}/` + row.id + '/delete');
        ElementPlus.ElMessage.success(I18N.t('proj.msgDeleted'));
        if (rows.value.length === 1 && f.page > 1) f.page--;
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }
    function created(id, kind) {
      newComicDlg.value = false;
      newDramaDlg.value = false;
      router.push((kind === 'comic' ? '/comic/' : '/drama/') + id);
    }

    // 创作中心按类型分开：无参进入时默认漫画，并把 ?kind= 写进 URL
    onMounted(() => { syncKindQuery(); load(); });
    return {
      f, kindTitle, rows, total, totalPages, statusLabels, statusTag,
      newComicDlg, newDramaDlg, openNew, renameDlg, renameTitle, busy,
      load, apply, onSearch, reset, open, askRename, doRename, del, created,
    };
  },
};
