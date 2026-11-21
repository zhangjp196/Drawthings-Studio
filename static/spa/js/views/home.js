// 首页 / 工作台 v2：Hero（欢迎 + 统计）→ 双创作入口 → 最近作品 + 活动侧栏。
// 布局与旧版（Hero + 网格 + rail）不同：入口前置为两张大卡，统计改为独立数值条。
window.Views = window.Views || {};
Views.home = {
  template: `
    <div class="page home-page">
      <section class="ds-hero">
        <div class="ds-hero-inner">
          <div class="ds-hero-main">
            <div class="ds-eyebrow">{{ I18N.t('home.eyebrow') }}</div>
            <h1 class="ds-hero-title">{{ I18N.t('nav.workspace') }}</h1>
            <p class="ds-hero-desc">{{ I18N.t('wb.sub') }}</p>
            <div class="ds-hero-actions">
              <button type="button" class="ds-btn ds-btn-primary ds-btn-lg" @click="goNew">{{ I18N.t('wb.newProject') }}</button>
              <el-radio-group v-model="newKind" size="small">
                <el-radio-button value="comic">{{ I18N.t('proj.comic') }}</el-radio-button>
                <el-radio-button value="drama">{{ I18N.t('proj.drama') }}</el-radio-button>
              </el-radio-group>
              <router-link to="/micro" class="ds-btn ds-btn-ghost ds-btn-lg">{{ I18N.t('wb.newMicro') }}</router-link>
              <router-link to="/configs" class="ds-btn ds-btn-ghost ds-btn-lg">{{ I18N.t('wb.settings') }}</router-link>
            </div>
          </div>
          <div class="ds-hero-side">
            <div class="ds-mini-stat"><b>{{ stat.comic }}</b><span>{{ I18N.t('wb.statComic') }}</span></div>
            <div class="ds-mini-stat"><b>{{ stat.drama }}</b><span>{{ I18N.t('wb.statDrama') }}</span></div>
            <div class="ds-mini-stat accent clickable" :title="I18N.t('home.openCenter')" @click="openTasks">
              <b>{{ running }}</b><span>{{ I18N.t('wb.statTasks') }}</span>
            </div>
          </div>
        </div>
      </section>

      <section class="ds-section">
        <div class="ds-section-head">
          <h2 class="ds-section-title">{{ I18N.t('home.start') }}</h2>
          <span class="muted small">{{ I18N.t('home.recentDesc') }}</span>
        </div>
        <div class="ds-launch-grid">
          <div class="ds-launch comic" @click="openStudio('comic')">
            <span class="ds-launch-ico">🖼</span>
            <div class="ds-launch-text">
              <div class="ds-launch-title">{{ I18N.t('nav.studioComic') }}</div>
              <div class="ds-launch-desc">{{ I18N.t('home.launchComicDesc') }}</div>
            </div>
            <span class="ds-launch-arrow">→</span>
          </div>
          <div class="ds-launch drama" @click="openStudio('drama')">
            <span class="ds-launch-ico">🎬</span>
            <div class="ds-launch-text">
              <div class="ds-launch-title">{{ I18N.t('nav.studioDrama') }}</div>
              <div class="ds-launch-desc">{{ I18N.t('home.launchDramaDesc') }}</div>
            </div>
            <span class="ds-launch-arrow">→</span>
          </div>
        </div>
      </section>

      <div class="ds-home-grid">
        <section class="ds-section">
          <div class="ds-section-head">
            <h2 class="ds-section-title">{{ I18N.t('wb.recent') }}</h2>
            <router-link to="/projects" class="ds-section-link">{{ I18N.t('home.allProjects') }} →</router-link>
          </div>
          <div v-if="loading" class="loading"><el-skeleton :rows="2" animated /></div>
          <div v-else-if="projects.length" class="ds-grid">
            <div v-for="p in projects" :key="p.id" class="ds-card" @click="open(p)">
              <div class="ds-card-media">
                <img v-if="p.first_image_url" :src="p.first_image_url" :alt="p.title || p.origin" loading="lazy" decoding="async">
                <span v-else class="ds-card-ph">{{ p.kind === 'comic' ? '🖼' : '🎬' }}</span>
              </div>
              <div class="ds-card-body">
                <div class="ds-card-title">{{ p.title || p.origin }}</div>
                <div class="ds-card-meta">
                  <el-tag size="small" :type="p.kind === 'comic' ? 'primary' : 'success'" effect="light">
                    {{ p.kind === 'comic' ? I18N.t('proj.comic') : I18N.t('proj.drama') }}
                  </el-tag>
                  <span>{{ p.chapter_count }} · {{ (p.updated_at || '').slice(0, 10) }}</span>
                </div>
              </div>
            </div>
          </div>
          <el-empty v-else-if="loadErr" :image-size="80">
            <template #default>
              <p class="muted small mb8">{{ I18N.t('wb.loadFail') }}</p>
              <el-button size="small" type="primary" plain @click="retry">{{ I18N.t('wb.retry') }}</el-button>
            </template>
          </el-empty>
          <el-empty v-else :description="I18N.t('wb.empty')" :image-size="80" />
        </section>

        <aside class="ds-rail">
          <section class="ds-rail-card">
            <div class="ds-rail-head">
              <h3>{{ I18N.t('home.activity') }}</h3>
              <a @click="openTasks">{{ I18N.t('home.openCenter') }}</a>
            </div>
            <div v-if="jobs.length">
              <div v-for="j in jobs" :key="j.id" class="ds-rail-job">
                <span class="ds-rail-dot" :class="'st-' + j.status"></span>
                <span class="ds-rail-job-name" :title="j.title">{{ j.title || j.target_id }}</span>
                <span class="ds-rail-job-state">{{ statusText(j.status) }}</span>
              </div>
            </div>
            <p v-else class="muted small">{{ I18N.t('home.noTasks') }}</p>
          </section>

          <section class="ds-rail-card">
            <div class="ds-rail-head"><h3>{{ I18N.t('home.quick') }}</h3></div>
            <router-link to="/new/comic" class="ds-rail-link">🖼 {{ I18N.t('proj.newDlgComic') }}</router-link>
            <router-link to="/new/drama" class="ds-rail-link">🎬 {{ I18N.t('proj.newDlgDrama') }}</router-link>
            <router-link to="/micro" class="ds-rail-link">✨ {{ I18N.t('nav.quick') }}</router-link>
            <router-link to="/configs" class="ds-rail-link">⚙ {{ I18N.t('cfg.title') }}</router-link>
          </section>
        </aside>
      </div>
    </div>
  `,
  setup() {
    const projects = ref([]);
    const loading = ref(true);
    const newKind = ref('comic');
    const loadErr = ref('');
    const stat = reactive({ comic: 0, drama: 0 });
    const running = computed(() => Tasks.activeCount());
    const jobs = computed(() =>
      Tasks.store.order.map((id) => Tasks.store.jobs[id]).filter(Boolean).slice(0, 4)
    );
    function openTasks() { Tasks.ui.centerOpen = true; }
    function statusText(s) { return I18N.t('tc.status.' + s) || s; }
    function goNew() { router.push('/new/' + newKind.value); }
    function openStudio(kind) { router.push('/projects?kind=' + kind); }
    async function load() {
      loading.value = true;
      loadErr.value = '';
      try {
        const qs = new URLSearchParams({ page: 1, size: 6, sort: 'active' });
        const [c, d] = await Promise.all([
          API.get('/api/comics?' + qs),
          API.get('/api/dramas?' + qs),
        ]);
        stat.comic = c.total || 0;
        stat.drama = d.total || 0;
        const all = [...(c.projects || []), ...(d.projects || [])];
        all.sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
        projects.value = all.slice(0, 6);
      } catch (e) {
        loadErr.value = e.message || 'error';
        projects.value = [];
      } finally {
        loading.value = false;
      }
    }
    function retry() { load(); }
    function open(p) { router.push(p.kind === 'comic' ? '/comic/' + p.id : '/drama/' + p.id); }
    onMounted(load);
    return { projects, loading, loadErr, newKind, goNew, openStudio, open, retry, stat, running, jobs, openTasks, statusText };
  },
};
