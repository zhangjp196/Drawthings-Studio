// 项目详情：头部/首图/步骤/下一步动作/总纲/章节（生成·重生成·编辑提示词）/创作配置
window.Views = window.Views || {};
Views.project = {
  props: ['id'],
  template: `
    <div class="page" v-if="data">
      <div class="proj-head">
        <div>
          <el-tag size="small" :type="data.project.kind === 'comic' ? 'primary' : 'success'" effect="light">
            {{ data.project.kind === 'comic' ? '漫画' : '短剧' }}
          </el-tag>
          <h1 class="ptitle">{{ data.project.title || data.project.origin }}</h1>
          <p class="meta muted">主题：{{ data.project.origin }}
            · 篇幅：{{ scope.total_chapters || '—' }} 章
            · 风格：{{ scope.style || '—' }}
            · LLM：{{ data.project.llm_name }}
            · DrawThings：{{ data.project.dt_name }}</p>
        </div>
        <div class="proj-head-actions">
          <el-button @click="openCfg">⚙ 创作配置</el-button>
          <el-button @click="router.push('/projects')">← 返回</el-button>
          <el-popconfirm title="删除该创作？全部章节与已生成的图/视频将一并删除，不可恢复。" @confirm="del">
            <template #reference><el-button type="danger" plain>🗑 删除该创作</el-button></template>
          </el-popconfirm>
        </div>
      </div>

      <el-card class="first-card" shadow="never">
        <template #header>
          <b>首图</b>
          <span class="muted small" style="margin-left: 8px;">第 1 章参考（漫画=图生图参考图 / 短剧=视频首帧），并作为全片角色/风格基准</span>
        </template>
        <div class="first-row">
          <div class="first-preview">
            <img v-if="data.project.first_image_url" :src="data.project.first_image_url" alt="首图"
                 @click="openLb([data.project.first_image_url], 0)">
            <el-empty v-else description="暂无首图" :image-size="54" />
          </div>
          <div class="first-forms">
            <div class="frow">
              <span class="k">上传首图</span>
              <el-upload :auto-upload="false" :show-file-list="false" accept="image/*" :on-change="onFile">
                <el-button size="small">选择图片并上传</el-button>
              </el-upload>
            </div>
            <div class="frow">
              <span class="k">提示词生成（留空 = LLM 按主题+风格自动撰写）</span>
              <el-input v-model="fprompt" type="textarea" :rows="2"
                        placeholder="例如：一只橘猫站在雪山之巅，回望旅途，电影感光影" />
            </div>
            <div class="actions">
              <el-button size="small" type="primary" :loading="genBusy" @click="genFirst">生成首图</el-button>
            </div>
          </div>
        </div>
      </el-card>

      <el-steps :active="activeStep" align-center class="steps">
        <el-step title="新建" />
        <el-step title="篇幅" />
        <el-step title="总纲" />
        <el-step title="章节" />
        <el-step title="剧本" />
        <el-step title="完成" />
      </el-steps>

      <el-card class="next-card" shadow="never" v-if="nextStep">
        <el-button type="primary" size="large" :loading="actBusy" @click="doAction(nextStep)">
          ▶ 下一步：{{ nextLabel }}
        </el-button>
        <span class="muted" v-if="actBusy" style="margin-left: 10px;">LLM 正在处理，可能需要几十秒…</span>
      </el-card>
      <el-alert v-else-if="data.project.status === 'done'" type="success" :closable="false"
                title="✅ 全部生成完成。" class="next-card" />

      <el-card v-if="data.project.arc" class="arc-card" shadow="never">
        <template #header>
          <b>整体故事总纲</b>
          <span class="muted small" style="margin-left: 8px;">全篇路线（开端 → 发展 → 高潮 → 结局），可直接编辑</span>
        </template>
        <el-input v-model="arcText" type="textarea" :rows="8" />
        <div class="actions">
          <el-button size="small" type="primary" @click="saveArc">保存总纲</el-button>
          <el-popconfirm title="重新生成整体总纲？（不改动章节）" @confirm="doAction('arc')">
            <template #reference><el-button size="small" :loading="actBusy">重新生成总纲</el-button></template>
          </el-popconfirm>
          <el-popconfirm title="重新生成章节将清空现有章节数据（包括已生成的图/视频）。确定继续？" @confirm="doAction('chapters')">
            <template #reference><el-button size="small" :loading="actBusy">按总纲重新生成章节</el-button></template>
          </el-popconfirm>
        </div>
      </el-card>

      <h2 style="font-size: 18px;">章节（{{ data.chapters.length }}）</h2>
      <el-card v-for="c in data.chapters" :key="c.index" class="chapter" shadow="never">
        <template #header>
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <b>第{{ c.index + 1 }}章 · {{ c.title }}</b>
            <el-tag size="small" :type="c.status === 'done' ? 'success' : (c.status === 'error' ? 'danger' : 'info')" effect="light">{{ c.status }}</el-tag>
          </div>
        </template>
        <el-alert v-if="c.status === 'error'" type="error" :closable="false" class="mb8"
                  :title="'生成失败：' + c.error" />
        <div class="ch-body">
          <div class="ch-prev">
            <img v-if="c.status === 'done' && data.project.kind === 'comic'" :src="c.media_url" :alt="c.title"
                 loading="lazy" @click="openLb([c.media_url], 0)">
            <video v-else-if="c.status === 'done' && data.project.kind === 'drama'" :src="c.media_url" controls preload="metadata"></video>
            <el-empty v-else description="待生成" :image-size="48" />
          </div>
          <div class="ch-info">
            <div class="k muted small">剧本描述</div>
            <p class="desc">{{ c.description || '—' }}</p>
            <div class="k muted small">提示词（可直接修改后保存，再点「重生成」应用）</div>
            <el-input v-model="c.prompt" type="textarea" :rows="3" />
            <div class="actions">
              <el-button size="small" :loading="c._saving" @click="savePrompt(c)">保存提示词</el-button>
              <el-button size="small" :loading="c._regen" @click="regen(c)">重生成</el-button>
              <el-button v-if="c.status !== 'done'" size="small" :loading="c._gen" @click="genOne(c)">生成本章</el-button>
            </div>
          </div>
        </div>
      </el-card>
      <el-empty v-if="!data.chapters.length" description="还没有章节，先点「下一步」设定篇幅/总纲/章节。" />

      <el-dialog v-model="cfgDlg" title="创作配置" width="540px">
        <p class="hint">保存后，后续步骤/重生成即使用新配置。</p>
        <el-form label-position="top">
          <el-form-item label="LLM 配置">
            <el-select v-model="cfg.llm" style="width: 100%">
              <el-option v-for="c in data.llm_configs" :key="c.id" :value="c.id"
                         :label="c.name + '（' + c.model + (c.supports_vision === 'no' ? ' / 纯文本' : '') + '）'" />
            </el-select>
          </el-form-item>
          <el-form-item label="DrawThings 配置">
            <el-select v-model="cfg.dt" style="width: 100%">
              <el-option v-for="c in data.drawthing_configs" :key="c.id" :value="c.id"
                         :label="c.name + '（' + (c.media_type === 'image' ? '图像' : '视频') + '模型 / ' + c.protocol + '）'" />
              <el-option v-if="!data.drawthing_configs.length" value="" label="（无匹配的 DrawThings 配置，请先创建）" />
            </el-select>
            <div class="hint">当前项目类型只列出匹配的模型配置。<el-link :underline="false" type="primary" @click="router.push('/configs?ctype=drawthings')">＋ 新建配置</el-link></div>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="cfgDlg = false">取消</el-button>
          <el-button type="primary" :loading="cfgBusy" @click="saveCfg">保存配置</el-button>
        </template>
      </el-dialog>

      <el-image-viewer v-if="lb.show" :url-list="lb.list" :initial-index="lb.idx" @close="lb.show = false" />
    </div>
    <div v-else class="page loading"><el-skeleton :rows="6" animated /></div>
  `,
  setup(props) {
    const ORDER = ['planning', 'scoped', 'arced', 'chaptered', 'scripted', 'done'];
    const NEXT = {
      planning: ['scope', '设定篇幅'], scoped: ['arc', '整体路线（总纲）'],
      arced: ['chapters', '章节设定'], chaptered: ['script', '剧本编写'], scripted: ['generate', '开始生成'],
    };

    const data = ref(null);
    const scope = computed(() => (data.value && data.value.project.scope) || {});
    const activeStep = computed(() => {
      const i = ORDER.indexOf(data.value?.project.status);
      return i === -1 ? 0 : (data.value.project.status === 'done' ? 6 : i);
    });
    const nextStep = computed(() => NEXT[data.value?.project.status]?.[0] || '');
    const nextLabel = computed(() => NEXT[data.value?.project.status]?.[1] || '');

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
        ElementPlus.ElMessage.success('完成');
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
        ElementPlus.ElMessage.warning('请上传图片文件');
        return;
      }
      const fd = new FormData();
      fd.append('file', file);
      API.postForm(`/api/projects/${props.id}/first-image`, fd)
        .then(() => { ElementPlus.ElMessage.success('首图已上传'); return load(); })
        .catch(e => ElementPlus.ElMessage.error(e.message));
    }

    async function genFirst() {
      genBusy.value = true;
      try {
        await API.post(`/api/projects/${props.id}/first-image/generate`, { prompt: fprompt.value });
        ElementPlus.ElMessage.success('首图已生成');
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
        ElementPlus.ElMessage.success('总纲已保存');
        data.value.project.arc = arcText.value;
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      }
    }

    async function savePrompt(c) {
      c._saving = true;
      try {
        await API.post(`/api/projects/${props.id}/edit/${c.index}`, { prompt: c.prompt });
        ElementPlus.ElMessage.success('提示词已保存（点「重生成」应用）');
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
        ElementPlus.ElMessage.success('配置已更新，后续步骤将使用新配置');
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
        ElementPlus.ElMessage.success('已删除');
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
      data, scope, activeStep, nextStep, nextLabel,
      actBusy, genBusy, fprompt, arcText, cfgDlg, cfgBusy, cfg, lb,
      doAction, onFile, genFirst, saveArc, savePrompt, genOne, regen,
      openCfg, saveCfg, del, openLb, router,
    };
  },
};
