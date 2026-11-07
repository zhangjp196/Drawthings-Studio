// 首页：工作台（Hero + 统计 + 最近创作 + 右侧任务/快捷入口）—— CS 桌面风格
window.Views = window.Views || {};
Views.home = {
  template: `
    <div class="page home-page">
      <section class="home-hero">
        <div class="hero-main">
          <div class="hero-eyebrow">{{ I18N.t('home.eyebrow') }}</div>
          <h1>{{ I18N.t('nav.workspace') }}</h1>
          <p class="hero-sub">{{ I18N.t('wb.sub') }}</p>
          <div class="hero-actions">
            <button type="button" class="wb-action primary" @click="goNew">＋ {{ I18N.t('wb.newProject') }}</button>
            <el-radio-group v-model="newKind" size="small">
              <el-radio-button value="comic">{{ I18N.t('proj.comic') }}</el-radio-button>
              <el-radio-button value="drama">{{ I18N.t('proj.drama') }}</el-radio-button>
            </el-radio-group>
            <router-link to="/micro" class="wb-action">✨ {{ I18N.t('wb.newMicro') }}</router-link>
            <router-link to="/configs" class="wb-action">⚙ {{ I18N.t('wb.settings') }}</router-link>
          </div>
        </div>
        <div class="hero-stats">
          <div class="hero-stat"><b>{{ stat.comic }}</b><span>{{ I18N.t('wb.statComic') }}</span></div>
          <div class="hero-stat"><b>{{ stat.drama }}</b><span>{{ I18N.t('wb.statDrama') }}</span></div>
          <div class="hero-stat accent" :title="I18N.t('home.openCenter')" @click="openTasks">
            <b>{{ running }}</b><span>{{ I18N.t('wb.statTasks') }}</span>
          </div>
        </div>
      </section>

      <div class="home-grid">
        <section class="home-main">
          <div class="home-sec-head">
            <h2>{{ I18N.t('wb.recent') }}</h2>
            <router-link to="/projects" class="wb-more">{{ I18N.t('wb.all') }} →</router-link>
          </div>
          <div v-if="loading" class="loading"><el-skeleton :rows="2" animated /></div>
          <div v-else-if="projects.length" class="wb-grid">
            <div v-for="p in projects" :key="p.id" class="wb-card" @click="open(p)">
              <div class="wb-thumb">
                <img v-if="p.first_image_url" :src="p.first_image_url" :alt="p.title || p.origin" loading="lazy">
                <span v-else class="wb-ph">{{ p.kind === 'comic' ? '🖼' : '🎬' }}</span>
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
          <el-empty v-else-if="loadErr" :image-size="80">
            <template #default>
              <p class="muted small mb8">{{ I18N.t('wb.loadFail') }}</p>
              <el-button size="small" type="primary" plain @click="retry">{{ I18N.t('wb.retry') }}</el-button>
            </template>
          </el-empty>
          <el-empty v-else :description="I18N.t('wb.empty')" :image-size="80" />
        </section>

        <aside class="home-rail">
          <section class="rail-card">
            <div class="rail-head">
              <h3>{{ I18N.t('nav.tasks') }}</h3>
              <a @click="openTasks">{{ I18N.t('home.openCenter') }}</a>
            </div>
            <div v-if="jobs.length">
              <div v-for="j in jobs" :key="j.id" class="rail-job">
                <span class="rail-job-dot" :class="'st-' + j.status"></span>
                <span class="rail-job-name" :title="j.title">{{ j.title || j.target_id }}</span>
                <span class="rail-job-state">{{ statusText(j.status) }}</span>
              </div>
            </div>
            <p v-else class="muted small">{{ I18N.t('home.noTasks') }}</p>
          </section>

          <section class="rail-card">
            <div class="rail-head"><h3>{{ I18N.t('home.quick') }}</h3></div>
            <router-link to="/projects?kind=comic" class="rail-link">🖼 {{ I18N.t('nav.studioComic') }}</router-link>
            <router-link to="/projects?kind=drama" class="rail-link">🎬 {{ I18N.t('nav.studioDrama') }}</router-link>
            <router-link to="/configs" class="rail-link">⚙ {{ I18N.t('cfg.title') }}</router-link>
          </section>
        </aside>
      </div>
    </div>
  `,
  setup() {
    const projects = ref([]);
    const loading = ref(true);
    // 新建创作类型：按钮旁切换（选哪个就是哪个）
    const newKind = ref('comic');
    const loadErr = ref('');
    const stat = reactive({ comic: 0, drama: 0 });
    const running = computed(() => Tasks.activeCount()); // 后台任务（跨页面，实时）
    const jobs = computed(() =>
      Tasks.store.order.map((id) => Tasks.store.jobs[id]).filter(Boolean).slice(0, 4)
    );
    function openTasks() { Tasks.ui.centerOpen = true; }
    function statusText(s) { return I18N.t('tc.status.' + s) || s; }
    function goNew() { router.push('/new/' + newKind.value); }
    async function load() {
      loading.value = true;
      loadErr.value = '';
      try {
        // 列表已按类型分开：工作台是跨类型的，故各取最近 6 条再合并
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
        // 网络失败若被静默吞掉会显示成「还没有作品」，误导用户以为没数据
        loadErr.value = e.message || 'error';
        projects.value = [];
      } finally {
        loading.value = false;
      }
    }
    function retry() { load(); }
    function open(p) { router.push(p.kind === 'comic' ? '/comic/' + p.id : '/drama/' + p.id); }
    onMounted(load);
    return { projects, loading, loadErr, newKind, goNew, open, retry, stat, running, jobs, openTasks, statusText };
  },
};
