// 封面卡片（项目详情页子组件）：预览 + 上传 + 提示词生成 +「作为第 1 章参考」开关
window.Views = window.Views || {};
Views.firstImage = {
  props: {
    project: { type: Object, required: true },   // data.project
    projectId: { type: String, required: true },
  },
  emits: ['preview', 'reloaded'],
  template: `
    <el-card class="first-card" shadow="never">
      <template #header>
        <b>{{ I18N.t('p.first') }}</b>
        <span class="muted small" style="margin-left: 8px;">{{ I18N.t('p.firstHint') }}</span>
      </template>
      <div class="first-row">
        <div class="first-preview">
          <img v-if="project.first_image_url" :src="project.first_image_url" :alt="I18N.t('p.first')"
               @click="$emit('preview', project.first_image_url)">
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
          <div class="frow">
            <el-checkbox v-model="coverRef" @change="onCoverRefChange">{{ I18N.t('p.coverAsFirstRef') }}</el-checkbox>
          </div>
          <div class="actions">
            <el-popconfirm :title="I18N.t('p.firstImageConfirm')" @confirm="genFirst">
              <template #reference><el-button size="small" type="primary" :loading="genBusy">{{ I18N.t('p.genFirst') }}</el-button></template>
            </el-popconfirm>
          </div>
        </div>
      </div>
    </el-card>
  `,
  setup(props, { emit }) {
    const fprompt = ref('');
    const genBusy = ref(false);
    const coverRef = ref(!!props.project.cover_as_first_ref);

    async function onCoverRefChange(v) {
      try {
        await API.post(`/api/projects/${props.projectId}/first-image/ref`, { enabled: !!v });
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
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
      API.postForm(`/api/projects/${props.projectId}/first-image`, fd)
        .then(() => { ElementPlus.ElMessage.success(I18N.t('p.msgFirstUploaded')); emit('reloaded'); })
        .catch(e => ElementPlus.ElMessage.error(e.message));
    }

    async function genFirst() {
      genBusy.value = true;
      try {
        await API.post(`/api/projects/${props.projectId}/first-image/generate`, { prompt: fprompt.value });
        ElementPlus.ElMessage.success(I18N.t('p.msgFirstGenerated'));
        fprompt.value = '';
        emit('reloaded');
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        emit('reloaded');
      } finally {
        genBusy.value = false;
      }
    }

    return { fprompt, genBusy, coverRef, onCoverRefChange, onFile, genFirst };
  },
};
