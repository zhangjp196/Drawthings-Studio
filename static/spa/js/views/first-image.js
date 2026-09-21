// 封面卡片（项目详情页子组件）：预览 + 上传 + 提示词 +「作为第 1 章参考」开关
// 提示词由父组件（project.js）持有，「生成封面」按钮在企划页顶部操作栏
window.Views = window.Views || {};
Views.firstImage = {
  props: {
    project: { type: Object, required: true },   // data.project
    projectId: { type: String, required: true },
    prompt: { type: String, required: true },    // 生成提示词（v-model:prompt，父组件持有）
    locked: { type: Boolean, default: false },   // 作品已完结（锁定）：操作只读
  },
  emits: ['preview', 'reloaded', 'update:prompt'],
  template: `
    <el-card class="first-card" shadow="never">
      <template #header>
        <b>{{ I18N.t('p.first') }}</b>
        <span class="muted small" style="margin-left: 8px;">{{ I18N.t('p.firstHint') }}</span>
      </template>
      <div class="first-row">
        <div class="first-preview">
          <img v-if="project.first_image_url" :src="project.first_image_url" :alt="I18N.t('p.first')"
               loading="lazy" decoding="async" @click="$emit('preview', project.first_image_url)">
          <el-empty v-else :description="I18N.t('p.noFirst')" :image-size="54" />
        </div>
        <div class="first-forms">
          <div class="frow">
            <span class="k">{{ I18N.t('p.upload') }}</span>
            <el-upload :auto-upload="false" :show-file-list="false" accept="image/*" :disabled="locked" :on-change="onFile">
              <el-button size="small" :disabled="locked">{{ I18N.t('p.uploadBtn') }}</el-button>
            </el-upload>
          </div>
          <div class="frow">
            <span class="k">{{ I18N.t('p.genPrompt') }}</span>
            <el-input :model-value="prompt" type="textarea" :rows="2" :placeholder="I18N.t('p.genPromptPh')"
                      @update:modelValue="(v) => $emit('update:prompt', v)" />
          </div>
          <div class="frow">
            <el-checkbox v-model="coverRef" :disabled="locked" @change="onCoverRefChange">{{ I18N.t('p.coverAsFirstRef') }}</el-checkbox>
          </div>
        </div>
      </div>
    </el-card>
  `,
  setup(props, { emit }) {
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

    return { coverRef, onCoverRefChange, onFile };
  },
};
