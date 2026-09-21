// 季封面卡片（项目详情页子组件）：预览 + 上传 + 提示词
// 「生成季封面」按钮在季封面子页顶部操作栏（project.js 持有提示词）
window.Views = window.Views || {};
Views.seasonCover = {
  props: {
    season: { type: Object, required: true },        // data.seasons 中当前季
    projectId: { type: String, required: true },
    seasonId: { type: String, required: true },
    prompt: { type: String, required: true },        // 生成提示词（v-model:prompt，父组件持有）
    locked: { type: Boolean, default: false },       // 作品已完结（锁定）：操作只读
  },
  emits: ['preview', 'reloaded', 'update:prompt'],
  template: `
    <el-card class="first-card" shadow="never">
      <template #header>
        <b>{{ I18N.t('p.seasonCover') }}</b>
        <span class="muted small" style="margin-left: 8px;">{{ I18N.t('p.seasonCoverHint') }}</span>
      </template>
      <div class="first-row">
        <div class="first-preview">
          <img v-if="season.first_image_url" :src="season.first_image_url" :alt="I18N.t('p.seasonCover')"
               loading="lazy" decoding="async" @click="$emit('preview', season.first_image_url)">
          <el-empty v-else :description="I18N.t('p.noSeasonCover')" :image-size="54" />
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
            <el-input :model-value="prompt" type="textarea" :rows="2" :placeholder="I18N.t('p.seasonGenPromptPh')"
                      @update:modelValue="(v) => $emit('update:prompt', v)" />
          </div>
        </div>
      </div>
    </el-card>
  `,
  setup(props, { emit }) {
    function onFile(uploadFile) {
      const file = uploadFile.raw;
      if (!file) return;
      if (!file.type || !file.type.startsWith('image/')) {
        ElementPlus.ElMessage.warning(I18N.t('p.uploadWarn'));
        return;
      }
      const fd = new FormData();
      fd.append('file', file);
      API.postForm(`/api/projects/${props.projectId}/seasons/${props.seasonId}/first-image`, fd)
        .then(() => { ElementPlus.ElMessage.success(I18N.t('p.msgSeasonFirstUploaded')); emit('reloaded'); })
        .catch(e => ElementPlus.ElMessage.error(e.message));
    }

    return { onFile };
  },
};
