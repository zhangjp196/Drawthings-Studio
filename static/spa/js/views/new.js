// /new：新建创作（整页表单，与创作中心弹框同一组件）
window.Views = window.Views || {};
Views.newProject = {
  template: `
    <div class="page">
      <h1 style="margin: 0 0 4px; font-size: 22px;">新建创作</h1>
      <p class="hint" style="margin-top: 0;">标题 + 主题（一句话）→ 设定篇幅 → 总纲 → 章节 → 剧本 → 逐章生成。点击「开始创作」后进入项目页。</p>
      <el-card shadow="never" style="max-width: 720px;">
        <create-form @created="go" />
      </el-card>
    </div>
  `,
  components: { 'create-form': Views.createForm },
  setup() {
    return { go: (id) => router.push('/project/' + id) };
  },
};
