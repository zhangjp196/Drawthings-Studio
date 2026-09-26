// 微创作会话侧栏（纯展示）：作品下的独立会话列表 + 折叠控制。
// 数据/增删改由父组件（micro-work）持有，本组件只发事件，避免业务逻辑分散。
window.Views = window.Views || {};

Views.microSessionList = {
  props: {
    sessions: { type: Array, default: () => [] },
    sid: { type: String, default: '' },
    collapsed: { type: Boolean, default: false },
  },
  emits: ['select', 'new', 'rename', 'delete', 'collapse'],
  template: `
    <aside class="mc-side" v-show="!collapsed">
      <div class="mc-side-head">
        <b>{{ I18N.t('mw.sessions', sessions.length) }}</b>
        <div style="display: flex; gap: 4px;">
          <el-button size="small" type="primary" plain @click="$emit('new')">{{ I18N.t('mw.new') }}</el-button>
          <el-button size="small" text :title="I18N.t('mw.collapse')" @click="$emit('collapse', true)">«</el-button>
        </div>
      </div>
      <div class="mc-sess-list">
        <div v-for="s in sessions" :key="s.id" class="mc-sess" :class="{ active: s.id === sid }" @click="$emit('select', s.id)">
          <div class="ms-title">{{ s.title || I18N.t('mw.newSession') }}</div>
          <div class="ms-meta muted">{{ (s.created_at || '').slice(0, 10) }} · {{ I18N.t('mw.msgs', s.msg_count) }}</div>
          <div class="ms-actions">
            <el-button size="small" text :icon="EditPen" @click.stop="$emit('rename', s)">{{ I18N.t('mw.rename') }}</el-button>
            <el-popconfirm :title="I18N.t('mw.delSessConfirm')" @confirm="$emit('delete', s)">
              <template #reference>
                <el-button size="small" text type="danger" :icon="Delete" @click.stop>{{ I18N.t('mw.delete') }}</el-button>
              </template>
            </el-popconfirm>
          </div>
        </div>
        <el-empty v-if="!sessions.length" :description="I18N.t('mw.noSessions')" :image-size="48" />
      </div>
    </aside>
  `,
  setup() {
    return {
      EditPen: ElementPlusIconsVue.EditPen,
      Delete: ElementPlusIconsVue.Delete,
    };
  },
};
