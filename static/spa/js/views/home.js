// 首页：软件介绍（Drawthings Studio）
window.Views = window.Views || {};
Views.home = {
  template: `
    <div class="page">
      <section class="hero">
        <h1>{{ I18N.t('home.title1') }}<span class="grad">{{ I18N.t('home.title2') }}</span>{{ I18N.t('home.title3') }}</h1>
        <p class="sub">{{ I18N.t('home.sub1') }}<br>
          {{ I18N.t('home.sub2') }}</p>
        <div class="hero-actions">
          <router-link to="/projects" class="btn-hero">{{ I18N.t('home.cta1') }}</router-link>
          <router-link to="/micro" class="btn-hero ghost">{{ I18N.t('home.cta2') }}</router-link>
        </div>
      </section>

      <section class="sec">
        <h2>{{ I18N.t('home.paths') }}</h2>
        <div class="feat-grid">
          <div class="feat">
            <div class="feat-ico">🎬</div>
            <h3>{{ I18N.t('home.feat1t') }}</h3>
            <p>{{ I18N.t('home.feat1d') }}</p>
          </div>
          <div class="feat">
            <div class="feat-ico">📽</div>
            <h3>{{ I18N.t('home.feat2t') }}</h3>
            <p>{{ I18N.t('home.feat2d') }}</p>
          </div>
          <div class="feat">
            <div class="feat-ico">⚙️</div>
            <h3>{{ I18N.t('home.feat3t') }}</h3>
            <p>{{ I18N.t('home.feat3d') }}</p>
          </div>
        </div>
      </section>

      <section class="sec">
        <h2>{{ I18N.t('home.flow') }}</h2>
        <div class="flow">
          <div class="flow-step"><span class="n">1</span>{{ I18N.t('home.flow1') }}</div>
          <div class="flow-step"><span class="n">2</span>{{ I18N.t('home.flow2') }}</div>
          <div class="flow-step"><span class="n">3</span>{{ I18N.t('home.flow3') }}</div>
          <div class="flow-step"><span class="n">4</span>{{ I18N.t('home.flow4') }}</div>
          <div class="flow-step"><span class="n">5</span>{{ I18N.t('home.flow5') }}</div>
          <div class="flow-step"><span class="n">6</span>{{ I18N.t('home.flow6') }}</div>
        </div>
      </section>
    </div>
  `,
};
