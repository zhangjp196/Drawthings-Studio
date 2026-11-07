// 片段文档导入（短剧版）：上传 txt / md / pdf / docx → 后台解析拆章 → 预览（可改标题 / 剔除）→ 确认写入。
// 与漫画版（import-dialog-comic.js）刻意分开：文案与接口各自独立（AGENTS：两条线不合并）。
window.Views = window.Views || {};
Views.dramaImportDialog = {
  props: {
    modelValue: { type: Boolean, default: false },
    projectId: { type: String, required: true },
    seasonId: { type: String, default: '' },
  },
  emits: ['update:modelValue', 'reloaded'],
  template: `
    <el-dialog :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)"
               :title="I18N.t('d.impTitle')" width="760px" append-to-body @closed="reset">
      <el-upload drag :auto-upload="false" :show-file-list="false" :accept="accept" :on-change="onPick">
        <div class="imp-drop">
          <div class="imp-drop-ico">📄</div>
          <div>{{ I18N.t('d.impDrop') }}</div>
          <div class="hint">{{ I18N.t('d.impTypes') }}</div>
        </div>
      </el-upload>

      <div class="imp-status" v-if="fileName">
        <span class="imp-file" :title="fileName">{{ fileName }}</span>
        <span class="muted small">{{ progressText }}</span>
      </div>
      <el-progress v-if="parsing" :percentage="pct" :stroke-width="6" :show-text="false" />

      <el-table ref="tableRef" v-if="rows.length" :data="rows" height="300" size="small" class="imp-table">
        <el-table-column type="selection" width="42" />
        <el-table-column :label="I18N.t('d.impColTitle')" min-width="220">
          <template #default="{ row }"><el-input v-model="row.title" size="small" /></template>
        </el-table-column>
        <el-table-column :label="I18N.t('d.impColSummary')" min-width="320">
          <template #default="{ row }"><el-input v-model="row.summary" size="small" type="textarea" :rows="2" /></template>
        </el-table-column>
      </el-table>
      <el-empty v-else-if="!parsing" :description="I18N.t('d.impEmpty')" :image-size="48" />

      <template #footer>
        <span class="muted small imp-foot">{{ I18N.t('d.impHint') }}</span>
        <el-button @click="$emit('update:modelValue', false)">{{ I18N.t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!rows.length || parsing" :loading="applying" @click="apply">
          {{ I18N.t('d.impApply') }}
        </el-button>
      </template>
    </el-dialog>
  `,
  setup(props, { emit }) {
    const accept = '.txt,.md,.markdown,.text,.pdf,.docx';
    const fileName = ref('');
    const parsing = ref(false);
    const applying = ref(false);
    const progressText = ref('');
    const pct = ref(0);
    const rows = ref([]);
    const tableRef = ref(null);
    const jobId = ref('');

    function reset() {
      fileName.value = '';
      parsing.value = false;
      applying.value = false;
      progressText.value = '';
      pct.value = 0;
      rows.value = [];
      jobId.value = '';
    }

    function selectAll() {
      nextTick(() => {
        const t = tableRef.value;
        if (!t) return;
        rows.value.forEach((row) => t.toggleRowSelection(row, true));
      });
    }

    async function onPick(uf) {
      const file = uf && uf.raw;
      if (!file) return;
      fileName.value = file.name;
      rows.value = [];
      pct.value = 0;
      parsing.value = true;
      progressText.value = I18N.t('d.impParsing');
      const fd = new FormData();
      fd.append('file', file);
      fd.append('season_id', props.seasonId);
      try {
        const r = await Tasks.runForm(`/api/dramas/${props.projectId}/import-doc`, fd, {
          onJobId: (id) => { jobId.value = id; },
          onEvent: (ev, d) => {
            if (ev === EVENTS.PROGRESS) {
              const total = d.total || 1;
              pct.value = Math.min(99, Math.round(((d.current || 0) / total) * 100));
              progressText.value = I18N.t('d.impProgress', d.current || 0, total);
            } else if (ev === EVENTS.IMPORT_ITEM) {
              rows.value.push({ title: d.title || '', summary: d.summary || '' });
              selectAll();
            }
          },
        });
        if (r.status === 'cancelled') return;
        pct.value = 100;
        progressText.value = I18N.t('d.impParsed', rows.value.length);
        if (!rows.value.length) ElementPlus.ElMessage.warning(I18N.t('d.impEmpty'));
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        progressText.value = '';
      } finally {
        parsing.value = false;
      }
    }

    async function apply() {
      const t = tableRef.value;
      const picked = t ? t.getSelectionRows() : rows.value.slice();
      const chapters = (picked || [])
        .map((row) => ({ title: (row.title || '').trim(), summary: (row.summary || '').trim() }))
        .filter((row) => row.title || row.summary);
      if (!chapters.length) {
        ElementPlus.ElMessage.warning(I18N.t('d.impSelectNone'));
        return;
      }
      applying.value = true;
      try {
        const r = await API.post(`/api/dramas/${props.projectId}/import-apply`,
          { job_id: jobId.value, season_id: props.seasonId, chapters });
        ElementPlus.ElMessage.success(I18N.t('d.impDone', (r && r.count) || chapters.length));
        emit('reloaded');
        emit('update:modelValue', false);
        reset();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        applying.value = false;
      }
    }

    return { accept, fileName, parsing, applying, progressText, pct, rows, tableRef, onPick, apply, reset };
  },
};
