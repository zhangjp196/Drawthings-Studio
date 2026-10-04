// 短剧项目页 —— 季封面卡片（短剧专属，不与漫画共享）：预览 + 上传 + 提示词 +「作为第 1 章参考」。
// 「生成季封面」按钮在季封面子页顶部操作栏（父组件持有提示词）。
window.Views = window.Views || {};

Views.dramaSeasonCover = {
  props: {
    season: { type: Object, required: true },        // data.seasons 中当前季
    projectId: { type: String, required: true },
    seasonId: { type: String, required: true },
    prompt: { type: String, required: true },        // 生成提示词（v-model:prompt，父组件持有）
  },
  emits: ['preview', 'reloaded', 'overlay', 'update:prompt'],
  template: `
    <el-card class="first-card" shadow="never">
      <template #header>
        <b>{{ I18N.t('d.epCover') }}</b>
        <span class="muted small" style="margin-left: 8px;">{{ I18N.t('d.epCoverHint') }}</span>
      </template>
      <div class="first-row">
        <div class="first-preview">
          <img v-if="season.first_image_url" :src="season.first_image_url" :alt="I18N.t('d.epCover')"
               loading="lazy" decoding="async" @click="$emit('preview', season.first_image_url)">
          <el-empty v-else :description="I18N.t('p.noSeasonCover')" :image-size="54" />
        </div>
        <div class="first-forms">
          <div class="frow">
            <span class="k">{{ I18N.t('p.upload') }}</span>
            <el-upload :auto-upload="false" :show-file-list="false" accept="image/*" :on-change="onFile">
              <el-button size="small" >{{ I18N.t('p.uploadBtn') }}</el-button>
            </el-upload>
          </div>
          <div class="frow">
            <span class="k">{{ I18N.t('p.overlayTitleK') }}</span>
            <el-button size="small" :disabled="!season.first_image_url" @click="$emit('overlay')">{{ I18N.t('p.overlayTitleBtn') }}</el-button>
          </div>
          <div class="frow">
            <span class="k">{{ I18N.t('p.genPrompt') }}</span>
            <el-input :model-value="prompt" type="textarea" :rows="2" :placeholder="I18N.t('d.epCoverPromptPh')"
                      @update:modelValue="(v) => $emit('update:prompt', v)" />
          </div>
          <div class="frow">
            <el-checkbox v-model="coverRef"  @change="onCoverRefChange">{{ I18N.t('d.epCoverRef') }}</el-checkbox>
          </div>
        </div>
      </div>
    </el-card>
  `,
  setup(props, { emit }) {
    const coverRef = ref(!!props.season.cover_as_first_ref);
    // 父组件复用实例（按 seasonId 重挂时 props 变了，本地开关要跟着新季同步）
    watch(() => props.season.id, () => { coverRef.value = !!props.season.cover_as_first_ref; });

    async function onCoverRefChange(v) {
      try {
        await API.post(`/api/dramas/${props.projectId}/seasons/${props.seasonId}/first-image/ref`,
          { enabled: !!v });
      } catch (e) {
        coverRef.value = !v;   // 失败回滚：开关跟服务端真实状态保持一致
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
      API.postForm(`/api/dramas/${props.projectId}/seasons/${props.seasonId}/first-image`, fd)
        .then(() => { ElementPlus.ElMessage.success(I18N.t('p.msgSeasonFirstUploaded')); emit('reloaded'); })
        .catch(e => ElementPlus.ElMessage.error(e.message));
    }

    return { coverRef, onCoverRefChange, onFile };
  },
};
