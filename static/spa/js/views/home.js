// 首页：软件介绍
window.Views = window.Views || {};
Views.home = {
  template: `
    <div class="page">
      <section class="hero">
        <h1>一句话创意，<span class="grad">连续漫画 / 短剧</span> 自动展开</h1>
        <p class="sub">标题 + 主题 → 设定篇幅 → 整体路线（总纲）→ 章节设定 → 剧本编写 → 逐章生成，一条流水线走完。<br>
          所有数据本地保存，无需联网；LLM 与生成服务均走本地。</p>
        <div class="hero-actions">
          <router-link to="/projects" class="btn-hero">进入创作中心 →</router-link>
          <router-link to="/micro" class="btn-hero ghost">✨ 微创作</router-link>
        </div>
      </section>

      <section class="sec">
        <h2>两种走向，一条流水线</h2>
        <div class="feat-grid">
          <div class="feat">
            <div class="feat-ico">🎬</div>
            <h3>漫画走向（连续生图）</h3>
            <p>同一角色/场景/画风逐章连贯：每章参考上一张生成，画面稳定不跳戏。</p>
          </div>
          <div class="feat">
            <div class="feat-ico">📽</div>
            <h3>短剧走向（连续出视频）</h3>
            <p>从首帧视频延续到下一段，参考上一视频末帧，角色动作与场景衔接自然。</p>
          </div>
          <div class="feat">
            <div class="feat-ico">⚙️</div>
            <h3>本地模型，数据不出机</h3>
            <p>LLM 与生图/生视频都走本地服务（Ollama / Draw Things / SD），配置随时切换。</p>
          </div>
        </div>
      </section>

      <section class="sec">
        <h2>一条流水线，六步走完</h2>
        <div class="flow">
          <div class="flow-step"><span class="n">1</span>一句话主题</div>
          <div class="flow-step"><span class="n">2</span>设定篇幅</div>
          <div class="flow-step"><span class="n">3</span>总纲</div>
          <div class="flow-step"><span class="n">4</span>章节</div>
          <div class="flow-step"><span class="n">5</span>剧本</div>
          <div class="flow-step"><span class="n">6</span>逐章生成</div>
        </div>
      </section>
    </div>
  `,
};
