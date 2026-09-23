// 首页：工作台（快捷入口 + 最近创作）—— CS 桌面风格，去掉营销式 hero / 功能 / 流水线介绍
window.Views = window.Views || {};
Views.home = {
  template: `
    <div class="page">
      <div class="wb-head">
        <h1>{{ I18N.t('nav.workspace') }}</h1>
        <p class="muted small">{{ I18N.t('wb.sub') }}</p>
      </div>

      <div class="wb-actions">
        <router-link to="/new" class="wb-action primary">{{ I18N.t('wb.newProject') }}</router-link>
        <router-link to="/micro" class="wb-action">{{ I18N.t('wb.newMicro') }}</router-link>
        <router-link to="/configs" class="wb-action">{{ I18N.t('wb.settings') }}</router-link>
      </div>

      <section class="wb-sec">
        <div class="wb-sec-head">
          <h2>{{ I18N.t('wb.recent') }}</h2>
          <router-link to="/projects" class="wb-more">{{ I18N.t('wb.all') }}</router-link>
        </div>
        <div v-if="loading" class="loading"><el-skeleton :rows="2" animated /></div>
        <div v-else-if="projects.length" class="wb-grid">
          <div v-for="p in projects" :key="p.id" class="wb-card" @click="open(p)">
            <div class="wb-thumb">
              <img v-if="p.first_image_url" :src="p.first_image_url" :alt="p.title || p.origin" loading="lazy">
              <span v-else class="wb-ph">{{ p.kind === 'comic' ? '🎬' : '📽' }}</span>
            </div>
            <div class="wb-card-body">
              <div class="wb-card-title">{{ p.title || p.origin }}</div>
              <div class="wb-card-meta">
                <el-tag size="small" :type="p.kind === 'comic' ? 'primary' : 'success'" effect="light">
                  {{ p.kind === 'comic' ? I18N.t('proj.comic') : I18N.t('proj.drama') }}
                </el-tag>
                <span class="muted small">{{ p.chapter_count }} · {{ (p.updated_at || '').slice(0, 10) }}</span>
              </div>
            </div>
          </div>
        </div>
        <el-empty v-else :description="I18N.t('wb.empty')" :image-size="80" />
      </section>
    </div>
  `,
  setup() {
    const projects = ref([]);
    const loading = ref(true);
    async function load() {
      loading.value = true;
      try {
        const data = await API.get('/api/projects?' + new URLSearchParams({ page: 1, size: 6, sort: 'active' }));
        projects.value = data.projects || [];
      } catch (e) {
        /* 工作台加载失败不打断使用（可去「创作中心」重试） */
      } finally {
        loading.value = false;
      }
    }
    function open(p) { router.push('/project/' + p.id); }
    onMounted(load);
    return { projects, loading, open };
  },
};
