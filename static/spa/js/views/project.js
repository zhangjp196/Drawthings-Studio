// /project/:id 兼容入口：漫画 / 短剧（视频）详情页已完全拆分为独立页面
// （/comic/:id → project-comic.js，/drama/:id → project-drama.js）。
// 此处仅取项目类型后重定向到对应独立页；旧链接 / 深链 / 书签仍可正常进入。
window.Views = window.Views || {};
Views.project = {
  props: ['id'],
  template: `<div class="page loading"><el-skeleton :rows="4" animated /></div>`,
  setup(props) {
    onMounted(async () => {
      try {
        const data = await API.get('/api/projects/' + props.id);
        const kind = (data && data.project && data.project.kind) || '';
        router.replace((kind === 'comic' ? '/comic/' : '/drama/') + props.id);
      } catch (e) {
        router.replace('/projects');
      }
    });
    return {};
  },
};
