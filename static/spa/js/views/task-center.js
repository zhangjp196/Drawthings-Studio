// 全局任务中心（右下角悬浮 dock + 抽屉）：展示所有后台任务（生成/规划/评分/对话），可取消。
// 数据来自全局 Tasks store（WS 推送 + /api/tasks 兜底），与具体页面无关，路由切换不销毁。
window.Views = window.Views || {};
Views.taskCenter = {
  template: `
    <div class="tc-wrap">
      <button type="button" class="tc-fab" :class="{ on: running > 0 }"
              :title="I18N.t('tc.title')" @click="open = true">
        <span class="tc-fab-ico">◷</span>
        <span class="tc-badge" v-if="running">{{ running }}</span>
      </button>
    </div>
    <el-drawer v-model="open" :title="I18N.t('tc.title')" size="420px" append-to-body class="tc-drawer">
      <div class="tc-hint muted small">{{ I18N.t('tc.hint') }}</div>
      <div class="tc-list">
        <div v-for="j in jobs" :key="j.id" class="tc-row" :class="'st-' + j.status">
          <div class="tc-row-top">
            <span class="tc-name" :title="j.title">{{ j.title || j.target_id }}</span>
            <span class="tc-status" :class="'st-' + j.status">{{ statusText(j.status) }}</span>
          </div>
          <div class="tc-meta muted small">
            {{ kindText(j.kind) }}<template v-if="j.note"> · {{ j.note }}</template>
          </div>
          <div class="tc-err" v-if="j.error">{{ j.error }}</div>
          <div class="tc-actions" v-if="j.status === 'running'">
            <el-button size="small" type="danger" plain @click="cancel(j)">{{ I18N.t('tc.cancel') }}</el-button>
          </div>
        </div>
        <el-empty v-if="!jobs.length" :description="I18N.t('tc.empty')" :image-size="56" />
      </div>
    </el-drawer>
  `,
  setup() {
    const open = ref(false);
    const jobs = computed(() =>
      Tasks.store.order
        .map((id) => Tasks.store.jobs[id])
        .filter(Boolean)
        .slice(0, 50)
    );
    const running = computed(() => jobs.value.filter((j) => j.status === 'running').length);
    function statusText(s) {
      const t = I18N.t('tc.status.' + s);
      return t && t !== 'tc.status.' + s ? t : (s || '');
    }
    function kindText(k) {
      const t = I18N.t('tc.kind.' + k);
      return t && t !== 'tc.kind.' + k ? t : (k || '');
    }
    async function cancel(j) { await Tasks.cancel(j.id); }
    onMounted(() => Tasks.refresh());
    return { open, jobs, running, statusText, kindText, cancel };
  },
};
