// /project/:id 兼容入口：漫画 / 短剧（视频）详情页已完全拆分为独立页面
// （/comic/:id → project-comic.js，/drama/:id → project-drama.js）。
// 此处仅取项目类型后重定向到对应独立页；旧链接 / 深链 / 书签仍可正常进入。
window.Views = window.Views || {};
Views.project = {
  props: ['id'],
  template: `<div class="page loading"><el-skeleton :rows="4" animated /></div>`,
  setup(props) {
    onMounted(async () => {
      // 详情 API 已按类型分开：先按漫画查，失败再按短剧查（兼容旧链接）
      for (const kind of ['comic', 'drama']) {
        try {
          await API.get(`/api/${kind}s/${props.id}`);
          router.replace((kind === 'comic' ? '/comic/' : '/drama/') + props.id);
          return;
        } catch (e) { /* 不是该类型，继续试下一个 */ }
      }
      router.replace('/projects');
    });
    return {};
  },
};
