// 项目详情：头部/首图/步骤/下一步动作/总纲/章节（生成·重生成·编辑提示词）/创作配置
window.Views = window.Views || {};
Views.project = {
  props: ['id'],
  template: `
    <div class="page" v-if="data">
      <div class="proj-head">
        <div>
          <el-tag size="small" :type="data.project.kind === 'comic' ? 'primary' : 'success'" effect="light">
            {{ data.project.kind === 'comic' ? I18N.t('proj.comic') : I18N.t('proj.drama') }}
          </el-tag>
          <h1 class="ptitle">{{ data.project.title || data.project.origin }}</h1>
          <p class="meta muted">{{ I18N.t('p.metaIdea', data.project.origin) }}
            · {{ I18N.t('p.metaLength', scope.total_chapters || '—') }}
            · {{ I18N.t('p.metaStyle', scope.style || '—') }}
            · {{ I18N.t('p.metaLlm', data.project.llm_name) }}
            · {{ I18N.t('p.metaDt', data.project.dt_name) }}</p>
        </div>
        <div class="proj-head-actions">
          <el-button @click="openCfg">{{ I18N.t('p.settings') }}</el-button>
          <el-button @click="router.push('/projects')">{{ I18N.t('p.back') }}</el-button>
          <el-popconfirm :title="I18N.t('proj.delConfirm')" @confirm="del">
            <template #reference><el-button type="danger" plain>{{ I18N.t('p.del') }}</el-button></template>
          </el-popconfirm>
        </div>
      </div>

      <el-card class="first-card" shadow="never">
        <template #header>
          <b>{{ I18N.t('p.first') }}</b>
          <span class="muted small" style="margin-left: 8px;">{{ I18N.t('p.firstHint') }}</span>
        </template>
        <div class="first-row">
          <div class="first-preview">
            <img v-if="data.project.first_image_url" :src="data.project.first_image_url" :alt="I18N.t('p.first')"
                 @click="openLb([data.project.first_image_url], 0)">
            <el-empty v-else :description="I18N.t('p.noFirst')" :image-size="54" />
          </div>
          <div class="first-forms">
            <div class="frow">
              <span class="k">{{ I18N.t('p.upload') }}</span>
              <el-upload :auto-upload="false" :show-file-list="false" accept="image/*" :on-change="onFile">
                <el-button size="small">{{ I18N.t('p.uploadBtn') }}</el-button>
              </el-upload>
            </div>
            <div class="frow">
              <span class="k">{{ I18N.t('p.genPrompt') }}</span>
              <el-input v-model="fprompt" type="textarea" :rows="2" :placeholder="I18N.t('p.genPromptPh')" />
            </div>
            <div class="actions">
              <el-button size="small" type="primary" :loading="genBusy" @click="genFirst">{{ I18N.t('p.genFirst') }}</el-button>
            </div>
          </div>
        </div>
      </el-card>

      <el-steps :active="activeStep" align-center class="steps">
        <el-step :title="I18N.t('p.step.new')" />
        <el-step :title="I18N.t('p.step.length')" />
        <el-step :title="I18N.t('p.step.arc')" />
        <el-step :title="I18N.t('p.step.chapters')" />
        <el-step :title="I18N.t('p.step.script')" />
        <el-step :title="I18N.t('p.step.done')" />
      </el-steps>

      <el-card class="next-card" shadow="never" v-if="nextStep">
        <el-button type="primary" size="large" :loading="actBusy" @click="doAction(nextStep)">
          {{ I18N.t('p.next', nextLabel) }}
        </el-button>
        <span class="muted" v-if="actBusy" style="margin-left: 10px;">{{ I18N.t('p.busy') }}</span>
      </el-card>
      <el-alert v-else-if="data.project.status === 'done'" type="success" :closable="false"
                :title="I18N.t('p.allDone')" class="next-card" />

      <el-card v-if="data.project.arc" class="arc-card" shadow="never">
        <template #header>
          <b>{{ I18N.t('p.arc') }}</b>
          <span class="muted small" style="margin-left: 8px;">{{ I18N.t('p.arcHint') }}</span>
        </template>
        <el-input v-model="arcText" type="textarea" :rows="8" />
        <div class="actions">
          <el-button size="small" type="primary" @click="saveArc">{{ I18N.t('p.arcSave') }}</el-button>
          <el-popconfirm :title="I18N.t('p.arcRegenConfirm')" @confirm="doAction('arc')">
            <template #reference><el-button size="small" :loading="actBusy">{{ I18N.t('p.arcRegen') }}</el-button></template>
          </el-popconfirm>
          <el-popconfirm :title="I18N.t('p.chRegenConfirm')" @confirm="doAction('chapters')">
            <template #reference><el-button size="small" :loading="actBusy">{{ I18N.t('p.chRegen') }}</el-button></template>
          </el-popconfirm>
        </div>
      </el-card>

      <h2 style="font-size: 18px;">{{ I18N.t('p.chapters') }}（{{ data.chapters.length }}）</h2>
      <el-card v-for="c in data.chapters" :key="c.index" class="chapter" shadow="never">
        <template #header>
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <b>{{ I18N.t('p.ch', c.index + 1) }} · {{ c.title }}</b>
            <span>
              <el-tag v-if="c.width && c.height" size="small" effect="plain" style="margin-right: 6px;">{{ c.width }}×{{ c.height }}</el-tag>
              <el-tag size="small" :type="c.status === 'done' ? 'success' : (c.status === 'error' ? 'danger' : 'info')" effect="light">{{ chStatus(c.status) }}</el-tag>
            </span>
          </div>
        </template>
        <el-alert v-if="c.status === 'error'" type="error" :closable="false" class="mb8"
                  :title="I18N.t('p.chFail', c.error)" />
        <div class="ch-body">
          <div class="ch-prev">
            <img v-if="c.status === 'done' && data.project.kind === 'comic'" :src="c.media_url" :alt="c.title"
                 loading="lazy" @click="openLb([c.media_url], 0)">
            <video v-else-if="c.status === 'done' && data.project.kind === 'drama'" :src="c.media_url" controls preload="metadata"></video>
            <el-empty v-else :description="I18N.t('p.chStatus.pending')" :image-size="48" />
          </div>
          <div class="ch-info">
            <div class="k muted small">{{ I18N.t('p.chScript') }}</div>
            <p class="desc">{{ c.description || '—' }}</p>
            <div class="k muted small">{{ I18N.t('p.chPromptHint') }}</div>
            <el-input v-model="c.prompt" type="textarea" :rows="3" />
            <div class="actions">
              <el-button size="small" :loading="c._saving" @click="savePrompt(c)">{{ I18N.t('p.chSavePrompt') }}</el-button>
              <el-button size="small" :loading="c._regen" @click="regen(c)">{{ I18N.t('p.chRegen') }}</el-button>
              <el-button v-if="c.status !== 'done'" size="small" :loading="c._gen" @click="genOne(c)">{{ I18N.t('p.chGen') }}</el-button>
            </div>
          </div>
        </div>
      </el-card>
      <el-empty v-if="!data.chapters.length" :description="I18N.t('p.chEmpty')" />

      <el-dialog v-model="cfgDlg" :title="I18N.t('p.cfgTitle')" width="540px">
        <p class="hint">{{ I18N.t('p.cfgHint') }}</p>
        <el-form label-position="top">
          <el-form-item :label="I18N.t('cf.llm')">
            <el-select v-model="cfg.llm" style="width: 100%">
              <el-option v-for="c in data.llm_configs" :key="c.id" :value="c.id"
                         :label="c.name + '（' + c.model + (c.supports_vision === 'no' ? ' / ' + I18N.t('cfg.textOnly') : '') + '）'" />
            </el-select>
          </el-form-item>
          <el-form-item :label="I18N.t('cf.dt')">
            <el-select v-model="cfg.dt" style="width: 100%">
              <el-option v-for="c in data.drawthing_configs" :key="c.id" :value="c.id" :label="c.name" />
              <el-option v-if="!data.drawthing_configs.length" value="" :label="I18N.t('cf.dtNone')" />
            </el-select>
            <div class="hint">{{ I18N.t('cf.dtHint') }}<el-link :underline="false" type="primary" @click="router.push('/configs?ctype=drawthings')">{{ I18N.t('cf.newCfg') }}</el-link></div>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="cfgDlg = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" :loading="cfgBusy" @click="saveCfg">{{ I18N.t('p.cfgSave') }}</el-button>
        </template>
      </el-dialog>

      <el-image-viewer v-if="lb.show" :url-list="lb.list" :initial-index="lb.idx" @close="lb.show = false" />
    </div>
    <div v-else class="page loading"><el-skeleton :rows="6" animated /></div>
  `,
  setup(props) {
    const ORDER = ['planning', 'scoped', 'arced', 'chaptered', 'scripted', 'done'];
    const NEXT = {
      planning: ['scope', 'p.next.scope'], scoped: ['arc', 'p.next.arc'],
      arced: ['chapters', 'p.next.chapters'], chaptered: ['script', 'p.next.script'], scripted: ['generate', 'p.next.generate'],
    };

    const data = ref(null);
    const scope = computed(() => (data.value && data.value.project.scope) || {});
    const activeStep = computed(() => {
      const i = ORDER.indexOf(data.value?.project.status);
      return i === -1 ? 0 : (data.value.project.status === 'done' ? 6 : i);
    });
    const nextStep = computed(() => NEXT[data.value?.project.status]?.[0] || '');
    const nextLabel = computed(() => I18N.t(NEXT[data.value?.project.status]?.[1] || ''));
    const chStatus = (s) => I18N.t('p.chStatus.' + s) || s;

    const actBusy = ref(false);
    const genBusy = ref(false);
    const fprompt = ref('');
    const arcText = ref('');
    const cfgDlg = ref(false);
    const cfgBusy = ref(false);
    const cfg = reactive({ llm: '', dt: '' });
    const lb = reactive({ show: false, list: [], idx: 0 });

    async function load() {
      try {
        data.value = await API.get('/api/projects/' + props.id);
        arcText.value = data.value.project.arc;
      } catch (e) {
        if (e.status === 404) router.replace('/projects');
        ElementPlus.ElMessage.error(e.message);
      }
    }

    async function doAction(step) {
      actBusy.value = true;
      try {
        await API.post(`/api/projects/${props.id}/action`, { step });
        ElementPlus.ElMessage.success(I18N.t('p.msgDone'));
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        await load(); // 刷新：章节错误等已持久化的状态
      } finally {
        actBusy.value = false;
      }
    }

    function onFile(uploadFile) {
      const file = uploadFile.raw;
      if (!file) return;
      if (!file.type || !file.type.startsWith('image/')) {
        ElementPlus.ElMessage.warning(I18N.t('p.uploadWarn'));
        return;
      }
      const fd = new FormData();
      fd.append('file', file);
      API.postForm(`/api/projects/${props.id}/first-image`, fd)
        .then(() => { ElementPlus.ElMessage.success(I18N.t('p.msgFirstUploaded')); return load(); })
        .catch(e => ElementPlus.ElMessage.error(e.message));
    }

    async function genFirst() {
      genBusy.value = true;
      try {
        await API.post(`/api/projects/${props.id}/first-image/generate`, { prompt: fprompt.value });
        ElementPlus.ElMessage.success(I18N.t('p.msgFirstGenerated'));
        fprompt.value = '';
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        await load();
      } finally {
        genBusy.value = false;
      }
    }

    async function saveArc() {
      try {
        await API.post(`/api/projects/${props.id}/arc`, { arc: arcText.value });
        ElementPlus.ElMessage.success(I18N.t('p.arcSaved'));
        data.value.project.arc = arcText.value;
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    async function savePrompt(c) {
      c._saving = true;
      try {
        await API.post(`/api/projects/${props.id}/edit/${c.index}`, { prompt: c.prompt });
        ElementPlus.ElMessage.success(I18N.t('p.promptSaved'));
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        c._saving = false;
      }
    }

    async function genOne(c) {
      c._gen = true;
      try {
        await API.post(`/api/projects/${props.id}/gen/${c.index}`);
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        await load();
      } finally {
        c._gen = false;
      }
    }

    async function regen(c) {
      c._regen = true;
      try {
        await API.post(`/api/projects/${props.id}/regenerate/${c.index}`);
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        await load();
      } finally {
        c._regen = false;
      }
    }

    function openCfg() {
      cfg.llm = data.value.project.llm_config_id;
      cfg.dt = data.value.project.drawthings_config_id;
      cfgDlg.value = true;
    }
    async function saveCfg() {
      cfgBusy.value = true;
      try {
        await API.post(`/api/projects/${props.id}/config`, {
          llm_config_id: cfg.llm, drawthings_config_id: cfg.dt,
        });
        ElementPlus.ElMessage.success(I18N.t('p.cfgSaved'));
        cfgDlg.value = false;
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        cfgBusy.value = false;
      }
    }

    async function del() {
      try {
        await API.post(`/api/projects/${props.id}/delete`);
        ElementPlus.ElMessage.success(I18N.t('proj.msgDeleted'));
        router.push('/projects');
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    function openLb(list, idx) {
      lb.list = list;
      lb.idx = idx;
      lb.show = true;
    }

    onMounted(load);
    return {
      data, scope, activeStep, nextStep, nextLabel, chStatus,
      actBusy, genBusy, fprompt, arcText, cfgDlg, cfgBusy, cfg, lb,
      doAction, onFile, genFirst, saveArc, savePrompt, genOne, regen,
      openCfg, saveCfg, del, openLb, router,
    };
  },
};
