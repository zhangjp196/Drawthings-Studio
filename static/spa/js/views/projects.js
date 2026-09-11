// 创作中心：列表（筛选/分页/缩略图）+ 新建弹框 + 重命名 + 删除
window.Views = window.Views || {};
Views.projects = {
  components: { 'create-form': Views.createForm },
  template: `
    <div class="page">
      <div class="list-toolbar">
        <div class="list-head">
          <h1>创作中心</h1>
          <span class="muted">共 {{ total }} 个</span>
        </div>
        <el-button type="primary" @click="newDlg = true">＋ 新建创作</el-button>
      </div>

      <el-card class="filter-card" shadow="never">
        <div class="filter-row">
          <el-select v-model="f.kind" placeholder="类型" clearable style="width: 120px" @change="apply">
            <el-option label="全部" value="" />
            <el-option label="漫画" value="comic" />
            <el-option label="短剧" value="drama" />
          </el-select>
          <el-select v-model="f.status" placeholder="状态" clearable style="width: 140px" @change="apply">
            <el-option label="全部" value="" />
            <el-option v-for="(v, k) in statusLabels" :key="k" :label="v" :value="k" />
          </el-select>
          <el-select v-model="f.sort" style="width: 130px" @change="apply">
            <el-option label="最新在前" value="desc" />
            <el-option label="最早在前" value="asc" />
          </el-select>
          <el-select v-model="f.size" style="width: 110px" @change="apply">
            <el-option v-for="n in [10, 20, 50]" :key="n" :label="n + ' / 页'" :value="n" />
          </el-select>
          <el-button @click="reset">重置</el-button>
        </div>
      </el-card>

      <el-table v-if="rows.length" :data="rows" style="width: 100%;">
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.kind === 'comic' ? 'primary' : 'success'" effect="light">{{ row.kind === 'comic' ? '漫画' : '短剧' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="标题 / 主题" min-width="240">
          <template #default="{ row }">
            <el-link type="primary" :underline="false" @click="open(row)">{{ row.title || row.origin }}</el-link>
            <div class="muted small" v-if="row.title && row.origin">{{ row.origin }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTag(row.status)" effect="light">{{ statusLabels[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="章节" width="90" align="center">
          <template #default="{ row }">{{ row.chapter_count }}{{ row.total_chapters ? ' / ' + row.total_chapters : '' }}</template>
        </el-table-column>
        <el-table-column label="首图" width="100">
          <template #default="{ row }">
            <el-image v-if="row.first_image_url" :src="row.first_image_url" :preview-src-list="[row.first_image_url]"
                      fit="cover" class="thumb" :preview-teleported="true" />
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="创建" width="110">
          <template #default="{ row }">{{ row.created_at.slice(0, 10) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="open(row)">打开</el-button>
            <el-button size="small" @click="askRename(row)">重命名</el-button>
            <el-popconfirm title="删除该创作？全部章节与已生成的图/视频将一并删除，不可恢复。" @confirm="del(row)">
              <template #reference><el-button size="small" type="danger" plain>删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="还没有创作。点击「＋ 新建创作」，从一句话开始。" />

      <el-pagination v-if="totalPages > 1" class="pager" background layout="prev, pager, next"
                     :total="total" :page-size="f.size" :current-page="f.page" @current-change="load" />

      <el-dialog v-model="newDlg" title="新建创作" width="580px">
        <create-form @created="created" />
      </el-dialog>

      <el-dialog v-model="renameDlg" title="重命名创作" width="440px">
        <el-input v-model="renameTitle" placeholder="新标题" maxlength="200" />
        <template #footer>
          <el-button @click="renameDlg = false">取消</el-button>
          <el-button type="primary" :loading="busy" @click="doRename">保存</el-button>
        </template>
      </el-dialog>
    </div>
  `,
  setup() {
    const statusLabels = {
      planning: '规划中', scoped: '篇幅已定', arced: '总纲已定',
      chaptered: '章节已定', scripted: '剧本已定', done: '已完成',
    };
    const f = reactive({ page: 1, size: 10, kind: '', status: '', sort: 'desc' });
    const rows = ref([]);
    const total = ref(0);
    const totalPages = ref(1);
    const newDlg = ref(false);
    const renameDlg = ref(false);
    const renameTitle = ref('');
    const renameId = ref('');
    const busy = ref(false);

    async function load() {
      try {
        const data = await API.get('/api/projects?' + new URLSearchParams({
          page: f.page, size: f.size, kind: f.kind, status: f.status, sort: f.sort,
        }));
        rows.value = data.projects;
        total.value = data.total;
        totalPages.value = data.total_pages;
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }
    function apply() { f.page = 1; load(); }
    function reset() {
      Object.assign(f, { page: 1, size: 10, kind: '', status: '', sort: 'desc' });
      load();
    }
    const statusTag = (s) => (s === 'done' ? 'success' : s === 'planning' ? 'info' : 'primary');

    function open(row) { router.push('/project/' + row.id); }
    function askRename(row) {
      renameId.value = row.id;
      renameTitle.value = row.title || row.origin;
      renameDlg.value = true;
    }
    async function doRename() {
      busy.value = true;
      try {
        await API.post('/api/projects/' + renameId.value + '/rename', { title: renameTitle.value });
        renameDlg.value = false;
        ElementPlus.ElMessage.success('已重命名');
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        busy.value = false;
      }
    }
    async function del(row) {
      try {
        await API.post('/api/projects/' + row.id + '/delete');
        ElementPlus.ElMessage.success('已删除');
        if (rows.value.length === 1 && f.page > 1) f.page--;
        load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }
    function created(id) {
      newDlg.value = false;
      router.push('/project/' + id);
    }

    onMounted(load);
    return {
      f, rows, total, totalPages, statusLabels, statusTag,
      newDlg, renameDlg, renameTitle, busy,
      load, apply, reset, open, askRename, doRename, del, created,
    };
  },
};
