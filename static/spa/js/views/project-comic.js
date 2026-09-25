// 项目详情 · 漫画版：头部/封面 + 季选择器（仅各季）+ 固定的「总体」入口（与各季用竖线分隔）
// 总体：独立入口，直接显示写作子页签（风格/整体故事大纲/全局提示词/分辨率/角色/封面/完结），无二级页签
// 季：二级页签（企划 / 章节 / 预览 / 导出）；企划内含子页签（本季大纲 / 季角色 / 季封面 / 章节规划）
// 章节：一键生成（剧本/画面）+ 手风琴卡片（多步，漫画版 chapter-card-comic）+ 返回企划
// 导出：当前所选季的完成情况（X/Y、整季完成提示）+ 按季导出 ZIP/PDF（漫画支持 PDF）+ PDF 预览（新标签直接查看）
// 与短剧版（project-drama.js）完全独立：章节固定为图片预览、PDF 导出恒可用，不含任何视频逻辑
window.Views = window.Views || {};
Views.projectComic = {
  props: ['id'],
  components: { 'first-image': Views.firstImage, 'season-cover': Views.seasonCover, 'chapter-card': Views.chapterCardComic },
  template: `
    <div class="page" v-if="data">
      <div class="proj-head">
        <div>
          <el-tag size="small" type="primary" effect="light">{{ I18N.t('proj.comic') }}</el-tag>
          <h1 class="ptitle">{{ data.project.title || data.project.origin }}</h1>
          <p class="meta muted">{{ I18N.t('p.metaIdea', data.project.origin) }}
            · {{ I18N.t('p.metaLength', data.chapters.length) }}
            · {{ I18N.t('p.metaStyle', scope.style || '—') }}
            · {{ I18N.t('p.metaLlm', data.project.llm_name) }}
            · {{ I18N.t('p.metaDt', data.project.dt_name) }}</p>
        </div>
        <div class="proj-head-actions">
          <el-button :disabled="locked" @click="openCfg">{{ I18N.t('p.settings') }}</el-button>
          <el-button :disabled="locked" @click="openReset">{{ I18N.t('p.reset') }}</el-button>
          <el-button @click="router.push(backTo())">{{ I18N.t('p.back') }}</el-button>
          <el-popconfirm :title="I18N.t('proj.delConfirm')" @confirm="del">
            <template #reference><el-button type="danger" plain>{{ I18N.t('p.del') }}</el-button></template>
          </el-popconfirm>
        </div>
      </div>

      <!-- 已完结（锁定）：内容只读，点「解锁」后恢复操作 -->
      <el-alert v-if="locked" type="success" :closable="false" class="finish-banner">
        <div class="finish-banner-body">
          <span>{{ I18N.t('p.lockedMsg') }}</span>
          <el-popconfirm :title="I18N.t('p.unlockConfirm')" @confirm="unlock">
            <template #reference>
              <el-button size="small" type="primary" plain style="margin-left: 12px;">{{ I18N.t('p.unlockBtn') }}</el-button>
            </template>
          </el-popconfirm>
        </div>
      </el-alert>

      <!-- 季（篇章）选择栏：最左为独立的「总体」入口（固定，不随各季 tab 切换），其后为各季 -->
      <div class="season-bar">
        <button type="button" class="season-item season-overall" :class="{ active: isOverall }" @click="selectSeason('overall')">
          {{ I18N.t('p.tabOverall') }}
        </button>
        <span class="season-sep" aria-hidden="true"></span>
        <button v-for="s in seasons" :key="s.id" type="button" class="season-item"
                :class="{ active: seasonId === s.id }" @click="selectSeason(s.id)">
          {{ s.title || I18N.t('p.season', s.number) }}
        </button>
        <el-popconfirm :title="I18N.t('p.seasonAddConfirm')" @confirm="addSeason">
          <template #reference><button type="button" class="season-item season-add" :disabled="locked">{{ I18N.t('p.seasonAdd') }}</button></template>
        </el-popconfirm>
        <el-popconfirm v-if="seasons.length > 1 && !isOverall && !locked" :title="I18N.t('p.seasonDelConfirm')" @confirm="delSeason">
          <template #reference><button type="button" class="season-item season-del">{{ I18N.t('p.seasonDel') }}</button></template>
        </el-popconfirm>
      </div>

      <!-- ============ 总体：独立入口，直接显示写作子页签（无 企划/章节/预览/完成 二级页签，那些均为季作用域）============ -->
      <div v-if="isOverall" class="subtabs">
        <nav class="subtabs-nav">
          <button type="button" class="subtabs-item" :class="{ active: oSub === 'story' }" @click="oSub = 'story'">{{ I18N.t('p.subStory') }}</button>
          <button type="button" class="subtabs-item" :class="{ active: oSub === 'chars' }" @click="oSub = 'chars'">{{ I18N.t('p.subChars') }}</button>
          <button type="button" class="subtabs-item" :class="{ active: oSub === 'cover' }" @click="oSub = 'cover'">{{ I18N.t('p.subCover') }}</button>
          <button type="button" class="subtabs-item" :class="{ active: oSub === 'finish' }" @click="oSub = 'finish'">{{ I18N.t('p.subFinish') }}</button>
        </nav>
        <div class="subtabs-body">
              <!-- 全局字段：风格 / 整体故事大纲 / 全局提示词 / 分辨率 / 角色 / 封面 / 完结 -->
                <el-card v-if="oSub === 'story'" shadow="never">
                  <div class="actions outline-bar">
                    <el-button type="primary" :loading="actBusy" :disabled="locked" @click="openGenDlg('arc', I18N.t('p.genArc'))">{{ I18N.t('p.genArc') }}</el-button>
                    <el-popconfirm :title="I18N.t('p.saveOutlineConfirm')" @confirm="saveStory">
                      <template #reference><el-button :loading="busySave" :disabled="locked">{{ I18N.t('p.outSave') }}</el-button></template>
                    </el-popconfirm>
                    <span class="muted" v-if="actBusy || busySave" style="margin-left:10px;">{{ progress.text || I18N.t('p.busy') }}</span>
                  </div>
                  <el-form label-position="top">
                    <el-form-item :label="I18N.t('p.outStyle')">
                      <el-input v-model="oStyle" :placeholder="scope.style || ''" />
                    </el-form-item>
                    <el-form-item :label="I18N.t('p.arc')">
                      <el-input v-model="arcText" type="textarea" :rows="6" :placeholder="I18N.t('p.arcPh')" />
                    </el-form-item>
                    <el-form-item :label="I18N.t('p.globalPrompt')">
                      <el-input v-model="oGlobal" type="textarea" :rows="4" :placeholder="I18N.t('p.globalPromptHint')" />
                    </el-form-item>
                    <el-form-item :label="I18N.t('p.outRes')">
                      <div class="res-row">
                        <span class="muted small">{{ I18N.t('p.resRatio') }}</span>
                        <el-select v-model="oRatio" size="small" style="width:150px" @change="onRatioChange">
                          <el-option :label="I18N.t('p.resAuto')" value="" />
                          <el-option v-for="r in resRatios" :key="r.key" :label="I18N.t(r.label)" :value="r.key" />
                          <el-option :label="I18N.t('p.resCustom')" value="custom" />
                        </el-select>
                        <span class="muted small" style="margin-left:8px;">{{ I18N.t('p.resSize') }}</span>
                        <el-select v-if="oRatio !== 'custom'" v-model="oRes" size="small" style="width:150px" @change="onResChange">
                          <el-option v-for="s in resOptions" :key="s" :label="s" :value="s" />
                        </el-select>
                        <template v-else>
                          <el-input-number v-model="oW" :min="0" :max="4096" :step="64" size="small" />
                          <span>×</span>
                          <el-input-number v-model="oH" :min="0" :max="4096" :step="64" size="small" />
                        </template>
                        <span class="muted small" style="margin-left:6px;">{{ I18N.t('p.outResHint') }}</span>
                      </div>
                    </el-form-item>
                    <el-form-item :label="I18N.t('p.scoreAuto')">
                      <div class="res-row">
                        <el-checkbox v-model="oScore">{{ I18N.t('p.scoreAutoLabel') }}</el-checkbox>
                        <el-checkbox v-model="oRedo" :disabled="!oScore">{{ I18N.t('p.scoreRedoLabel') }}</el-checkbox>
                        <span class="muted small">{{ I18N.t('p.scoreMin') }}</span>
                        <el-input-number v-model="oScoreMin" :min="0" :max="100" size="small" :disabled="!oScore" />
                      </div>
                    </el-form-item>
                  </el-form>
                </el-card>
                <el-card v-else-if="oSub === 'chars'" shadow="never">
                  <div class="actions outline-bar">
                    <el-button type="primary" :loading="actBusy" :disabled="locked" @click="openGenDlg('chars', I18N.t('p.genChars'))">{{ I18N.t('p.genChars') }}</el-button>
                    <el-popconfirm :title="I18N.t('p.charsSaveConfirm')" @confirm="saveChars">
                      <template #reference><el-button :loading="busySave" :disabled="locked">{{ I18N.t('p.charsSave') }}</el-button></template>
                    </el-popconfirm>
                    <span class="muted" v-if="actBusy || busySave" style="margin-left:10px;">{{ progress.text || I18N.t('p.busy') }}</span>
                  </div>
                  <div v-for="(c, i) in chars" :key="c.id || ('new' + i)" class="char-card">
                    <div class="row-between" style="margin-bottom:6px;">
                      <b class="muted small">{{ I18N.t('p.char', i + 1) }}</b>
                      <el-button size="small" type="danger" plain :disabled="locked" @click="delChar(i)">{{ I18N.t('p.charDel') }}</el-button>
                    </div>
                    <el-form label-position="top">
                      <el-form-item :label="I18N.t('p.charName')">
                        <el-input v-model="c.name" size="small" :placeholder="I18N.t('p.charNamePh')" />
                      </el-form-item>
                      <el-form-item>
                        <template #label>
                          <span>{{ I18N.t('p.charDesc') }}</span>
                          <el-popconfirm :title="I18N.t('p.charDescGenConfirm')" @confirm="genCharDesc(i)">
                            <template #reference>
                              <el-button size="small" type="primary" plain :loading="genDescBusy === c.id"
                                         style="margin-left:8px;">{{ I18N.t('p.charDescGen') }}</el-button>
                            </template>
                          </el-popconfirm>
                        </template>
                        <el-input v-model="c.description" type="textarea" :rows="4" :placeholder="I18N.t('p.charDescPh')" />
                      </el-form-item>
                      <el-form-item :label="I18N.t('p.charRef')">
                        <div class="char-ref">
                          <img v-if="c.image_url" :src="c.image_url" class="char-ref-img" :alt="c.name || ''"
                               loading="lazy" decoding="async" @click="openLb([c.image_url], 0)">
                          <el-upload :auto-upload="false" :show-file-list="false" accept="image/*" :disabled="locked"
                                     :on-change="(f) => uploadCharImage(i, f)">
                            <el-button size="small" :disabled="locked">{{ c.image_url ? I18N.t('p.charRefChange') : I18N.t('p.uploadBtn') }}</el-button>
                          </el-upload>
                          <el-button v-if="c.image_url" size="small" type="danger" plain
                                     :disabled="locked"
                                     @click="removeCharImage(i)">{{ I18N.t('p.charRefDel') }}</el-button>
                        </div>
                        <div class="hint" style="margin-top:4px;">{{ I18N.t('p.charRefHint') }}</div>
                      </el-form-item>
                    </el-form>
                  </div>
                  <div class="actions" style="margin-top:10px;">
                    <el-button :disabled="locked" @click="addChar">{{ I18N.t('p.charAdd') }}</el-button>
                  </div>
                  <el-empty v-if="!chars.length" :description="I18N.t('p.charsEmpty')" :image-size="48" />
                </el-card>
                <!-- ============ 完结：全部季章节完成才可完结；完结后锁定，需解锁才能操作 ============ -->
                <el-card v-else-if="oSub === 'finish'" shadow="never">
                  <template #header><b>{{ I18N.t('p.subFinish') }}</b></template>
                  <p class="hint mb8">{{ I18N.t('p.finishDesc') }}</p>
                  <p class="muted small mb8">{{ I18N.t('p.finishTotal', totalDoneCount, totalChCount) }}</p>
                  <el-alert v-if="finishReady" type="success" :closable="false" class="mb8"
                            :title="I18N.t('p.finishReadyMsg')" />
                  <div v-for="r in finishRows" :key="r.id" class="finish-row">
                    <b class="finish-row-name">{{ r.label }}</b>
                    <span class="muted small">{{ I18N.t('p.finishSeasonProgress', r.done, r.total) }}</span>
                    <el-tag size="small" :type="r.ok ? 'success' : (r.total === 0 ? 'info' : 'warning')"
                             effect="light" style="margin-left: auto;">
                      {{ r.ok ? I18N.t('p.finishTagOk') : (r.total === 0 ? I18N.t('p.finishNoCh') : I18N.t('p.finishTagDoing')) }}
                    </el-tag>
                  </div>
                  <el-empty v-if="!finishRows.length" :description="I18N.t('p.seasonNoChapters')" :image-size="48" />
                  <div class="actions" style="margin-top: 12px;">
                    <template v-if="locked">
                      <el-popconfirm :title="I18N.t('p.unlockConfirm')" @confirm="unlock">
                        <template #reference>
                          <el-button type="primary" plain :loading="finishBusy">{{ I18N.t('p.unlockBtn') }}</el-button>
                        </template>
                      </el-popconfirm>
                      <span class="muted small" style="margin-left: 10px;">{{ I18N.t('p.lockedMsg') }}</span>
                    </template>
                    <el-tooltip v-else :content="finishIssues || I18N.t('p.finishReadyMsg')" placement="top" :disabled="finishReady">
                      <span>
                        <el-popconfirm :title="I18N.t('p.finishConfirm')" @confirm="markFinished">
                          <template #reference>
                            <el-button type="primary" :loading="finishBusy" :disabled="!finishReady">{{ I18N.t('p.finishBtn') }}</el-button>
                          </template>
                        </el-popconfirm>
                      </span>
                    </el-tooltip>
                  </div>
                </el-card>
                <div v-else>
                  <div class="actions outline-bar">
                    <el-button type="primary" :loading="busyFirst" :disabled="locked" @click="openCoverGenDlg('project')">{{ I18N.t('p.genFirst') }}</el-button>
                    <span class="muted" v-if="busyFirst" style="margin-left:10px;">{{ I18N.t('p.busy') }}</span>
                  </div>
                  <first-image :project="data.project" :project-id="data.project.id" v-model:prompt="coverPrompt"
                               :locked="locked" @preview="openLb([$event], 0)" @reloaded="load"
                               @overlay="openOvlDlg('project')" />
                </div>
              </div>
            </div>

      <!-- ============ 季：二级页签 企划 / 章节 / 预览 / 完成（随所选季作用域）============ -->
      <el-tabs v-else v-model="tab" class="proj-tabs">
        <el-tab-pane :label="I18N.t('p.tabOutline')" name="outline">
          <div class="subtabs">
            <nav class="subtabs-nav">
              <button type="button" class="subtabs-item" :class="{ active: oSub === 'arc' }" @click="oSub = 'arc'">{{ I18N.t('p.seasonArc') }}</button>
              <button type="button" class="subtabs-item" :class="{ active: oSub === 'chars' }" @click="oSub = 'chars'">{{ I18N.t('p.seasonChars') }}</button>
              <button type="button" class="subtabs-item" :class="{ active: oSub === 'cover' }" @click="oSub = 'cover'">{{ I18N.t('p.seasonCover') }}</button>
            </nav>
            <div class="subtabs-body">
              <el-card v-if="oSub === 'arc'" shadow="never">
                  <div class="actions outline-bar">
                    <el-button type="primary" :loading="actBusy" :disabled="locked" @click="openGenDlg('season_arc', I18N.t('p.seasonArcGen'))">{{ I18N.t('p.seasonArcGen') }}</el-button>
                    <el-popconfirm :title="I18N.t('p.seasonArcSaveConfirm')" @confirm="saveSeasonArc">
                      <template #reference><el-button :loading="busySave" :disabled="locked">{{ I18N.t('p.seasonArcSave') }}</el-button></template>
                    </el-popconfirm>
                    <span class="muted" v-if="actBusy || busySave" style="margin-left:10px;">{{ progress.text || I18N.t('p.busy') }}</span>
                  </div>
                   <el-form label-position="top">
                     <el-form-item :label="I18N.t('p.seasonTitle')">
                       <el-input v-model="seasonTitleText" :placeholder="I18N.t('p.seasonTitlePh')" />
                     </el-form-item>
                     <el-form-item :label="I18N.t('p.seasonArc')">
                       <el-input v-model="seasonArcText" type="textarea" :rows="8" :placeholder="I18N.t('p.seasonArcPh')" />
                     </el-form-item>
                   </el-form>
                </el-card>
                <el-card v-else-if="oSub === 'chars'" shadow="never">
                  <div class="actions outline-bar">
                    <el-button type="primary" :loading="actBusy" :disabled="locked" @click="openGenDlg('season_chars', I18N.t('p.seasonCharsGen'))">{{ I18N.t('p.seasonCharsGen') }}</el-button>
                    <el-popconfirm :title="I18N.t('p.seasonCharsSaveConfirm')" @confirm="saveSeasonChars">
                      <template #reference><el-button :loading="busySave" :disabled="locked">{{ I18N.t('p.seasonCharsSave') }}</el-button></template>
                    </el-popconfirm>
                    <span class="muted" v-if="actBusy || busySave" style="margin-left:10px;">{{ progress.text || I18N.t('p.busy') }}</span>
                  </div>
                  <div v-for="(c, i) in seasonChars" :key="'sc' + (c.id || i)" class="char-card">
                    <div class="row-between" style="margin-bottom:6px;">
                      <b class="muted small">{{ I18N.t('p.char', i + 1) }}</b>
                      <el-button size="small" type="danger" plain @click="delSeasonChar(i)">{{ I18N.t('p.charDel') }}</el-button>
                    </div>
                    <el-form label-position="top">
                      <el-form-item :label="I18N.t('p.charName')">
                        <el-input v-model="c.name" size="small" :placeholder="I18N.t('p.charNamePh')" />
                      </el-form-item>
                      <el-form-item :label="I18N.t('p.charDesc')">
                        <el-input v-model="c.description" type="textarea" :rows="3" :placeholder="I18N.t('p.charDescPh')" />
                      </el-form-item>
                    </el-form>
                  </div>
                  <div class="actions" style="margin-top:10px;">
                    <el-button size="small" @click="addSeasonChar">{{ I18N.t('p.charAdd') }}</el-button>
                  </div>
                  <el-empty v-if="!seasonChars.length" :description="I18N.t('p.seasonCharsEmpty')" :image-size="48" />
                </el-card>
                <template v-else-if="oSub === 'cover'">
                  <div class="actions outline-bar">
                    <el-button type="primary" :loading="busySeasonFirst" :disabled="locked" @click="openCoverGenDlg('season')">{{ I18N.t('p.genSeasonFirst') }}</el-button>
                    <span class="muted" v-if="busySeasonFirst" style="margin-left:10px;">{{ I18N.t('p.busy') }}</span>
                  </div>
                  <season-cover :season="curSeason" :project-id="data.project.id" :season-id="seasonId"
                                v-model:prompt="seasonCoverPrompt" :locked="locked"
                                @preview="openLb([$event], 0)" @reloaded="load"
                                @overlay="openOvlDlg('season')" />
                </template>
            </div>
          </div>
        </el-tab-pane>

        <!-- ============ 章节：章节规划 / 章节详情 / 预览 一体（季作用域，一屏内完成）============ -->
        <el-tab-pane :label="I18N.t('p.tabChapters')" name="chapters">
          <div class="ch-toolbar">
            <div class="ch-tb-row">
              <span class="ch-tb-cap">{{ I18N.t('p.chPlan') }}</span>
              <el-popconfirm :title="selected.length ? I18N.t('p.planSelConfirm', selected.length) : I18N.t('p.chRegenConfirm')"
                             @confirm="planChapters">
                <template #reference>
                  <el-button size="small" type="primary" :loading="actBusy" :disabled="locked">
                    {{ selected.length ? I18N.t('p.planSel', selected.length) : I18N.t('p.planChapters') }}
                  </el-button>
                </template>
              </el-popconfirm>
              <el-popconfirm :title="I18N.t('p.planSaveConfirm')" @confirm="savePlan">
                <template #reference><el-button size="small" :loading="busySave" :disabled="locked">{{ I18N.t('p.planSave') }}</el-button></template>
              </el-popconfirm>
              <span class="ch-tb-sep"></span>
              <span class="muted small">{{ I18N.t('p.countMode') }}</span>
              <el-input-number v-model="cMin" :min="1" :max="99" size="small" style="width:96px" />
              <span class="muted small">~</span>
              <el-input-number v-model="cMax" :min="1" :max="99" size="small" style="width:96px" />
              <span class="ch-tb-sep"></span>
              <span class="muted small">{{ I18N.t('p.outRes') }} {{ oW }}×{{ oH }}（{{ I18N.t('p.planResHint') }}）</span>
              <span class="ch-tb-sep"></span>
              <el-button size="small" :disabled="locked" @click="addChapter">{{ I18N.t('p.addChapter') }}</el-button>
              <span class="muted small" v-if="actBusy || busySave">{{ progress.text || I18N.t('p.busy') }}</span>
            </div>
            <div class="ch-tb-row">
              <span class="ch-tb-cap">{{ I18N.t('p.tabChapters') }}</span>
              <el-checkbox :model-value="allSelected" @change="toggleAllSelect">{{ I18N.t('p.selAll') }}</el-checkbox>
              <el-popconfirm :title="I18N.t('p.genAllConfirm')" @confirm="genAll">
                <template #reference>
                  <el-button size="small" type="primary" :loading="busyGenAll" :disabled="!seasonChapters.length || locked">
                    {{ selected.length ? I18N.t('p.genAllSel', selected.length) : I18N.t('p.genAll') }}
                  </el-button>
                </template>
              </el-popconfirm>
              <el-popconfirm :title="I18N.t('p.scoreAllConfirm')" @confirm="scoreAll">
                <template #reference>
                  <el-button size="small" type="primary" plain :loading="busyScoreAll" :disabled="!seasonChapters.length || locked">
                    {{ selected.length ? I18N.t('p.scoreAllSel', selected.length) : I18N.t('p.scoreAll') }}
                  </el-button>
                </template>
              </el-popconfirm>
              <el-button v-if="busyGenAll || busyScoreAll" size="small" type="danger" plain @click="stopGen">
                {{ busyScoreAll ? I18N.t('p.scoreStop') : I18N.t('p.genStop') }}
              </el-button>
              <span class="muted small" v-if="seasonChapters.length">{{ I18N.t('p.progress', seasonDoneCount, seasonChapters.length) }}</span>
              <span class="muted small" v-if="progress.text">{{ progress.text }}</span>
              <span class="ch-tb-sep"></span>
              <el-button size="small" @click="tab = 'outline'">{{ I18N.t('p.toOutline') }}</el-button>
            </div>
          </div>

          <div class="md-layout ch-work">
            <!-- 左：章节列表（整季滚动、不分页；勾选用于批量生成；点击选中，当前章自动滚到可见） -->
            <div class="md-left">
              <div class="md-list" v-if="seasonChapters.length">
                <div v-for="(c, i) in seasonChapters" :key="'md' + c.index" class="md-item"
                     :class="{ active: i === cur }" @click="cur = i">
                  <el-checkbox :model-value="isSel(i)" @click.stop @change="toggleSelect(i)" />
                  <span class="md-idx">{{ c.index + 1 }}</span>
                  <span class="md-title">{{ c.title || I18N.t('p.ch', c.index + 1) }}</span>
                  <el-tag size="small" effect="light"
                          :type="c.status === 'done' ? 'success' : (c.status === 'error' ? 'danger' : 'info')">
                    {{ I18N.t('p.chStatus.' + c.status) || c.status }}
                  </el-tag>
                  <el-tag v-if="c.score > 0" size="small" effect="light"
                          :type="c.score >= (data.project.score_min || 80) ? 'success' : 'warning'">
                    {{ c.score }}{{ I18N.t('p.scoreUnit') }}
                  </el-tag>
                </div>
              </div>
              <el-empty v-else :description="I18N.t('p.chPlanEmpty')" :image-size="48" />
            </div>

            <!-- 中：选中章详情（标题 + 摘要/剧本/提示词 三个子页签，一次「保存」提交） -->
            <div class="md-detail">
              <chapter-card :key="curCh.index" v-if="curCh" :chapter="curCh" :project-id="data.project.id"
                            :season-id="seasonId" :season-index="cur"
                            :def-w="oW" :def-h="oH"
                            :score-min="(data.project.score_min || 80)"
                            :expanded="true" :no-toggle="true" :locked="locked"
                            :is-first="cur === 0" :is-last="cur === seasonChapters.length - 1"
                            @preview="openLb([$event], 0)" @reloaded="onChapterReloaded"
                            @saveplan="savePlan" />
              <el-empty v-else :description="I18N.t('p.chPlanEmpty')" :image-size="54" />
            </div>

            <!-- 右：本季预览（按章节顺序、不分页；缺图占位；点击切到对应章节） -->
            <div class="pv-side">
              <div class="pv-side-head">
                <b>{{ I18N.t('p.tabPreview') }}</b>
                <el-radio-group v-model="pvMode" size="small">
                  <el-radio-button value="tile">{{ I18N.t('p.pvTile') }}</el-radio-button>
                  <el-radio-button value="gallery">{{ I18N.t('p.pvGallery') }}</el-radio-button>
                </el-radio-group>
              </div>
              <div class="muted small">{{ I18N.t('p.pvHint', pvUrls.length, seasonChapters.length) }}</div>
              <div class="pv-side-body">
                <div class="pv-grid" :class="'pv-' + pvMode">
                  <div v-for="(it, i) in pvItems" :key="'pv' + it.index" class="pv-cell"
                       :class="{ cur: i === cur }" @click="gotoChapter(i)">
                    <div class="pv-media">
                      <img v-if="it.has" :src="it.url" :alt="it.title || I18N.t('p.ch', it.index + 1)"
                           loading="lazy" decoding="async">
                      <div v-else class="pv-ph" :title="I18N.t('p.pvMissing')">{{ it.index + 1 }}</div>
                      <button v-if="it.has" type="button" class="pv-zoom" :title="I18N.t('p.pvZoom')"
                              @click.stop="openLb(pvUrls, it.k)">⤢</button>
                    </div>
                    <div class="pv-cap">{{ i + 1 }}. {{ it.title || I18N.t('p.ch', it.index + 1) }}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane :label="I18N.t('p.tabExport')" name="done">
          <el-card shadow="never">
            <template #header><b>{{ I18N.t('p.tabExport') }}</b></template>
            <template v-if="seasonChapters.length">
              <p class="muted small mb8">{{ I18N.t('p.seasonDoneProgress', seasonDoneCount, seasonChapters.length) }}</p>
              <el-alert v-if="seasonCompleted" type="success" :closable="false"
                        :title="I18N.t('p.seasonDoneMsg')" class="mb8" />
            </template>
            <p class="muted small mb8" v-else>{{ I18N.t('p.seasonNoChapters') }}</p>
            <div class="actions">
              <el-button type="primary" :loading="exporting" :disabled="!seasonDoneCount" @click="exportZip">{{ I18N.t('p.exportZip') }}</el-button>
              <el-button type="primary" :loading="exporting" :disabled="!seasonDoneCount" @click="exportPdf">{{ I18N.t('p.exportPdf') }}</el-button>
              <el-button type="primary" plain :loading="exporting" :disabled="!seasonDoneCount" @click="previewPdf">{{ I18N.t('p.previewPdf') }}</el-button>
            </div>
          </el-card>
        </el-tab-pane>
      </el-tabs>

      <el-dialog v-model="cfgDlg" :title="I18N.t('p.cfgTitle')" width="540px">
        <p class="hint">{{ I18N.t('p.cfgHint') }}</p>
        <el-form label-position="top">
          <el-form-item :label="I18N.t('cf.llm')">
            <el-select v-model="cfg.llm" style="width: 100%">
              <el-option v-for="c in data.llm_configs" :key="c.id" :value="c.id"
                         :label="c.name + '（' + c.model + '）'" />
            </el-select>
          </el-form-item>
          <el-form-item :label="I18N.t('cf.dt')">
            <el-select v-model="cfg.dt" style="width: 100%">
              <el-option v-for="c in data.drawthing_configs" :key="c.id" :value="c.id" :label="c.name" />
              <el-option v-if="!data.drawthing_configs.length" value="" :label="I18N.t('cf.dtNone')" />
            </el-select>
            <div class="hint">{{ I18N.t('cf.dtHint') }}<el-link :underline="false" type="primary" @click="router.push('/configs?ctype=drawthings')">{{ I18N.t('cf.newCfg') }}</el-link></div>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="cfgDlg = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" :loading="cfgBusy" @click="saveCfg">{{ I18N.t('p.cfgSave') }}</el-button>
        </template>
      </el-dialog>

      <el-dialog v-model="resetDlg" :title="I18N.t('p.reset')" width="540px">
        <p class="hint">{{ I18N.t('p.resetHint') }}</p>
        <el-form label-position="top">
          <el-form-item :label="I18N.t('p.resetTitle')">
            <el-input v-model="rtitle" maxlength="200" />
          </el-form-item>
          <el-form-item :label="I18N.t('p.resetOrigin')">
            <el-input v-model="rogin" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item :label="I18N.t('p.resetStyle')">
            <el-select v-model="rstyle" style="width: 100%">
              <el-option value="" :label="I18N.t('cf.styleAuto')" />
              <el-option v-for="s in stylePresets" :key="s" :value="s" :label="s" />
              <el-option value="custom" :label="I18N.t('cf.styleCustom')" />
            </el-select>
            <el-input v-if="rstyle === 'custom'" v-model="rstyleCustom" class="mt8" :placeholder="I18N.t('cf.styleCustomPh')" />
          </el-form-item>
        </el-form>
        <el-checkbox v-model="rclear" style="margin-bottom: 4px;">{{ I18N.t('p.resetClear') }}</el-checkbox>
        <template #footer>
          <el-button @click="resetDlg = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" @click="saveReset">{{ I18N.t('p.resetSave') }}</el-button>
        </template>
      </el-dialog>

      <el-dialog v-model="genDlg" :title="genDlgTitle" width="540px">
        <p class="hint">{{ I18N.t('p.genDlgHint') }}</p>
        <el-form label-position="top">
          <el-form-item :label="I18N.t('p.genExtraPrompt')">
            <el-input v-model="genDlgExtra" type="textarea" :rows="4" :placeholder="I18N.t('p.genExtraPromptPh')" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="genDlg = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" :loading="actBusy" @click="confirmGen">{{ I18N.t('p.genStart') }}</el-button>
        </template>
      </el-dialog>

      <!-- 生成封面：勾选「包含标题」= 生成后自动叠加作品标题 / 季名 -->
      <el-dialog v-model="coverGenDlg.show"
                 :title="coverGenDlg.target === 'season' ? I18N.t('p.genSeasonFirst') : I18N.t('p.genFirst')"
                 width="440px">
        <el-checkbox v-model="coverGenDlg.includeTitle">{{ I18N.t('p.includeTitle') }}</el-checkbox>
        <p class="hint" style="margin-top:8px;">{{ I18N.t('p.includeTitleHint') }}</p>
        <template #footer>
          <el-button @click="coverGenDlg.show = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" :loading="busyFirst || busySeasonFirst" @click="confirmCoverGen">{{ I18N.t('p.genStart') }}</el-button>
        </template>
      </el-dialog>

      <!-- 叠加标题：位置（自由拖动）+ 字号 / 样式 / 颜色 / 底条 -->
      <el-dialog v-model="ovlDlg.show" :title="I18N.t('p.overlayDlgTitle')" width="640px">
        <div class="ovl-dlg">
          <div class="ovl-row">
            <div class="ovl-label">{{ I18N.t('p.overlayPos') }}</div>
            <div ref="ovlBox" class="ovl-stage" @pointerdown="ovlDragStart" @pointermove="ovlDragMove"
                 @pointerup="ovlDragEnd" @pointercancel="ovlDragEnd">
              <img :src="ovlDlg.coverUrl" draggable="false" alt="" />
              <div class="ovl-text" :style="ovlTextStyle">{{ ovlDlg.titleText }}</div>
            </div>
          </div>
          <div class="ovl-row">
            <div class="ovl-label">{{ I18N.t('p.overlaySize') }}</div>
            <el-slider v-model="ovlDlg.sizePct" :min="4" :max="20" :step="1" style="flex:1" />
            <span class="muted small" style="width:46px;text-align:right;">{{ ovlDlg.sizePct }}%</span>
          </div>
          <div class="ovl-row">
            <div class="ovl-label">{{ I18N.t('p.overlayStyle') }}</div>
            <el-select v-model="ovlDlg.style" style="width:150px">
              <el-option value="bold_outline" :label="I18N.t('p.overlayStyleBold')" />
              <el-option value="outline" :label="I18N.t('p.overlayStyleOutline')" />
              <el-option value="shadow" :label="I18N.t('p.overlayStyleShadow')" />
              <el-option value="plain" :label="I18N.t('p.overlayStylePlain')" />
            </el-select>
            <el-checkbox v-model="ovlDlg.band" style="margin-left:16px;">{{ I18N.t('p.overlayBand') }}</el-checkbox>
          </div>
          <div class="ovl-row">
            <div class="ovl-label">{{ I18N.t('p.overlayColor') }}</div>
            <el-color-picker v-model="ovlDlg.color" />
            <span class="muted small">{{ I18N.t('p.overlayDragHint') }}</span>
          </div>
          <p class="hint">{{ I18N.t('p.overlayBaseHint') }}</p>
        </div>
        <template #footer>
          <el-button @click="ovlDlg.show = false">{{ I18N.t('common.cancel') }}</el-button>
          <el-button type="primary" :loading="ovlBusy" @click="applyOvl">{{ I18N.t('p.overlayApply') }}</el-button>
        </template>
      </el-dialog>

      <!-- PDF 预览：iframe 直接渲染导出端点返回的 inline PDF（关闭弹框即销毁） -->
      <el-dialog v-model="pdfDlg" :title="I18N.t('p.previewPdf')" width="86%" top="4vh" destroy-on-close
                 @closed="pdfUrl = ''">
        <iframe v-if="pdfUrl" :src="pdfUrl" class="pdf-frame" />
        <template #footer>
          <el-button @click="pdfDlg = false">{{ I18N.t('common.cancel') }}</el-button>
        </template>
      </el-dialog>

      <el-image-viewer v-if="lb.show" :url-list="lb.list" :initial-index="lb.idx" @close="lb.show = false" />
    </div>
    <div v-else class="page loading"><el-skeleton :rows="6" animated /></div>
  `,
  setup(props) {
    const data = ref(null);
    const scope = computed(() => (data.value && data.value.project.scope) || {});
    const tab = ref('outline');
    const tabInit = ref(false);
    // 返回/删除/404 一律回到「漫画创作」列表（带 ?kind=comic，保持侧边栏高亮）
    const backTo = () => '/projects?kind=comic';

    const actBusy = ref(false);
    const busySave = ref(false);
    const busyGenAll = ref(false);
    const busyScoreAll = ref(false);
    const exporting = ref(false);  // 导出 ZIP/PDF 进行中（CS 走系统「另存为」，耗时期间禁用按钮）
    const busyFirst = ref(false);
    const busySeasonFirst = ref(false);
    // 生成封面弹框：包含标题（生成后自动叠加作品标题 / 季名）
    const coverGenDlg = reactive({ show: false, target: 'project', includeTitle: true });
    // 叠加标题弹框：位置（自由拖动）/ 字号 / 样式 / 颜色 / 底条
    const ovlDlg = reactive({ show: false, target: 'project', titleText: '', coverUrl: '',
                              x: 0.5, y: 1 / 3, sizePct: 8, style: 'bold_outline',
                              color: '#ffffff', band: true });
    const ovlBox = ref(null);      // 预览容器（拖动坐标系）
    const ovlBoxW = ref(300);      // 预览宽度（按比例换算预览字号）
    const ovlBusy = ref(false);
    let ovlDragging = false;
    const genDescBusy = ref(null);  // 正在 AI 生成描述的字符 id（null = 空闲）
    const coverPrompt = ref('');   // 封面生成提示词（first-image 子组件持有输入，顶部「生成封面」按钮使用）
    const seasonCoverPrompt = ref('');   // 季封面生成提示词（season-cover 子组件持有输入，季封面子页「生成季封面」按钮使用）
    const progress = reactive({ text: '' });
    let sseCtrl = null;            // 当前流式任务（规划/生成）：离开页面或重开时中断，服务端随之清理

    // 企划子页签：总体模式 story（故事大纲）/ chars（角色）/ cover（封面）；季模式 arc（本季大纲）/ chars（季角色）/ plan（章节规划）
    const oSub = ref('story');
    // 季（篇章）：seasonId = 'overall' 表示选中「总体」（全局），否则为某季 id
    // 注意：必须声明在引用 seasonChapters 的 computed / watch 之前——
    // Vue 的 watch 注册时会立即执行一次 getter 取旧值，声明靠后会触发 TDZ ReferenceError
    const seasonId = ref('overall');
    const isOverall = computed(() => seasonId.value === 'overall');
    const seasonArcText = ref('');
    const seasonTitleText = ref('');
    const seasonChars = ref([]);
    const seasons = computed(() => (data.value?.seasons || []));
    const curSeason = computed(() => seasons.value.find(x => x.id === seasonId.value) || null);
    const seasonChapters = computed(() => {
      if (isOverall.value) return [];
      return (data.value?.chapters || []).filter(c => c.season_id === seasonId.value);
    });
    const seasonDoneCount = computed(() => seasonChapters.value.filter(c => c.status === 'done').length);
    // 预览页签：平铺 / 画廊；pvItems 带「有图序号 k」（无图章节不进放大列表），缺图白色占位
    const pvMode = ref('tile');
    const pvItems = computed(() => {
      let k = -1;
      return seasonChapters.value.map(c => {
        const has = !!(c.media_url || '').trim();
        if (has) k += 1;
        return { index: c.index, title: c.title, url: c.media_url || '', has, k };
      });
    });
    const pvUrls = computed(() => pvItems.value.filter(x => x.has).map(x => x.url));
    // 季完成 = 本季章节全部生成（派生值，不存储）；空季不算完成
    const seasonCompleted = computed(() => seasonChapters.value.length > 0 && seasonDoneCount.value === seasonChapters.value.length);

    // 章节主从布局：当前选中章节序号
    const cur = ref(0);
    const curCh = computed(() => {
      const a = seasonChapters.value;
      return (a.length && cur.value >= 0 && cur.value < a.length) ? a[cur.value] : null;
    });
    // 预览点图 → 切到对应章节（左列表页码随 cur 自动同步），并滚回章节工作区
    function gotoChapter(i) {
      cur.value = i;
      const work = document.querySelector('.md-layout');
      if (work && work.scrollIntoView) work.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    // 左侧章节列表：不分页、整季滚动；当前章自动滚到可见（批量生成 / 预览点击时跟随）
    function scrollActiveIntoView() {
      const el = document.querySelector('.md-list .md-item.active');
      if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
    }
    watch(cur, () => nextTick(scrollActiveIntoView));
    // 批量生成多选：勾选的章节序号（季内 0 起；空 = 生成全部）
    const selected = ref([]);
    const allSelected = computed(() => {
      const n = seasonChapters.value.length;
      return n > 0 && selected.value.length === n;
    });
    function isSel(i) {
      return selected.value.indexOf(i) >= 0;
    }
    function toggleSelect(i) {
      const k = selected.value.indexOf(i);
      if (k >= 0) selected.value.splice(k, 1);
      else selected.value.push(i);
    }
    function toggleAllSelect() {
      selected.value = allSelected.value ? [] : seasonChapters.value.map((_, i) => i);
    }

    // 大纲表单
    const arcText = ref('');
    const oStyle = ref('');
    const chars = ref([]);       // 角色设定：[{id, name, description, image_url}]（id 为空 = 未保存的新角色）
    const oGlobal = ref('');     // 全局提示词（要点/约束，注入每次章节 LLM 调用）
    const oW = ref(0);
    const oH = ref(0);
    // 自动评分：生成画面后按 0-100 评分；低于阈值自动重做（最多 2 次）
    const oScore = ref(true);
    const oScoreMin = ref(80);
    const oRedo = ref(true);
    // 分辨率：先选比例、再选固定分辨率（均为 64 的倍数；0×0 = 跟随出图端/智能体）
    const RES_RATIOS = [
      { key: '1:1',  label: 'p.ratio11',  sizes: ['512×512', '768×768', '1024×1024'] },
      { key: '3:4',  label: 'p.ratio34',  sizes: ['576×768', '768×1024', '864×1152', '960×1280'] },
      { key: '4:3',  label: 'p.ratio43',  sizes: ['768×576', '1024×768', '1152×864', '1280×960'] },
      { key: '2:3',  label: 'p.ratio23',  sizes: ['512×768', '640×960', '768×1152', '896×1344'] },
      { key: '3:2',  label: 'p.ratio32',  sizes: ['768×512', '960×640', '1152×768', '1344×896'] },
      { key: '9:16', label: 'p.ratio916', sizes: ['576×1024', '720×1280', '864×1536', '1080×1920'] },
      { key: '16:9', label: 'p.ratio169', sizes: ['1024×576', '1280×720', '1536×864', '1920×1080'] },
    ];
    const oRatio = ref('');   // '' = 自动（跟随出图端）；'custom' = 旧数据里的自定义值
    const oRes = ref('0×0');  // 当前选中的分辨率（'W×H'）
    const resOptions = computed(() => {
      if (oRatio.value === '') return ['0×0'];
      const r = RES_RATIOS.find(x => x.key === oRatio.value);
      const cur = `${oW.value}×${oH.value}`;
      if (!r) return [cur];  // custom：仅展示当前（旧数据）值
      const opts = r.sizes.slice();
      if (oW.value && oH.value && !opts.includes(cur)) opts.push(cur);
      return opts;
    });
    function applyRes() {
      const m = /^(\d+)×(\d+)$/.exec(oRes.value || '');
      oW.value = m ? parseInt(m[1], 10) : 0;
      oH.value = m ? parseInt(m[2], 10) : 0;
    }
    function onRatioChange(key) {
      if (key === '') { oRes.value = '0×0'; applyRes(); return; }
      const r = RES_RATIOS.find(x => x.key === key);
      if (!r) return;
      const cur = `${oW.value}×${oH.value}`;
      oRes.value = r.sizes.includes(cur) ? cur : r.sizes[0];
      applyRes();
    }
    function onResChange() { applyRes(); }
    // 章节数量（仅范围 min~max；0/未设置时后端回退默认 6~12）
    const cMin = ref(6);
    const cMax = ref(12);

    // 整部作品完结：locked = 已完结（status=done，锁定只读）；finishRows = 各季完成进度
    const locked = computed(() => (data.value?.project.status) === 'done');
    const totalChCount = computed(() => (data.value?.chapters || []).length);
    const totalDoneCount = computed(() => (data.value?.chapters || []).filter(c => c.status === 'done').length);
    const finishRows = computed(() => (data.value?.seasons || []).map(s => {
      const chs = (data.value?.chapters || []).filter(c => c.season_id === s.id);
      const done = chs.filter(c => c.status === 'done').length;
      return { id: s.id, label: s.title || I18N.t('p.season', s.number),
               total: chs.length, done, ok: chs.length > 0 && done === chs.length };
    }));
    // 可完结 = 每季都有章节且全部已生成（与后端 complete 校验一致）
    const finishReady = computed(() => finishRows.value.length > 0 && finishRows.value.every(r => r.ok));
    const finishIssues = computed(() => finishRows.value.filter(r => !r.ok)
      .map(r => `${r.label}：${r.total === 0 ? I18N.t('p.finishNoCh')
        : I18N.t('p.finishSeasonProgress', r.done, r.total)}`)
      .join(I18N.isEn() ? '; ' : '；'));
    const finishBusy = ref(false);
    async function markFinished() {
      finishBusy.value = true;
      try {
        await API.post(`/api/projects/${props.id}/complete`);
        ElementPlus.ElMessage.success(I18N.t('p.msgFinished'));
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        finishBusy.value = false;
        await load();
      }
    }
    async function unlock() {
      finishBusy.value = true;
      try {
        await API.post(`/api/projects/${props.id}/unlock`);
        ElementPlus.ElMessage.success(I18N.t('p.msgUnlocked'));
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        finishBusy.value = false;
        await load();
      }
    }

    // 生成弹框（生成大纲 / 生成本季大纲 / 生成角色 共用）：额外提示词
    const genDlg = ref(false);
    const genDlgStep = ref('');   // 'arc' / 'season_arc' / 'chars'
    const genDlgTitle = ref('');
    const genDlgExtra = ref('');

    const cfgDlg = ref(false);
    const cfgBusy = ref(false);
    const cfg = reactive({ llm: '', dt: '' });
    const pdfDlg = ref(false);
    const pdfUrl = ref('');
    const lb = reactive({ show: false, list: [], idx: 0 });
    const resetDlg = ref(false);
    const rtitle = ref('');
    const rogin = ref('');
    const rstyle = ref('');
    const rstyleCustom = ref('');   // 风格选「自定义」时的描述
    const rclear = ref(false);
    // 与「新建创作」一致的风格预设下拉
    const stylePresets = computed(() => [0, 1, 2, 3, 4, 5].map(i => I18N.t('cf.preset.' + i)));

    function syncOutlineForm() {
      const p = data.value.project;
      arcText.value = p.arc || '';
      oStyle.value = (p.scope || {}).style || '';
      chars.value = (p.characters || []).map(c => ({ id: c.id || '', name: c.name || '',
                                                      description: c.description || '', image_url: c.image_url || '' }));
      oGlobal.value = p.global_prompt || '';
      oW.value = p.res_width || 0;
      oH.value = p.res_height || 0;
      oScore.value = !!p.auto_score;
      oScoreMin.value = p.score_min || 80;
      oRedo.value = !!p.auto_redo;
      // 由已存 W×H 反推比例与分辨率选项（0×0=自动；不在固定列表的旧值=自定义）
      const cur = `${oW.value}×${oH.value}`;
      const r = RES_RATIOS.find(x => x.sizes.includes(cur));
      oRatio.value = (oW.value || oH.value) ? (r ? r.key : 'custom') : '';
      oRes.value = (oW.value || oH.value) ? cur : '0×0';
    }
    function syncTab() {
      // 默认落在「大纲」页（含已规划章节的进行中项目）；完成已是季级，不再按作品状态自动跳「完成」
      tab.value = 'outline';
    }
    // 章节列表变化后，把选中序号钳制到有效范围
    function syncCur() {
      const n = seasonChapters.value.length;
      if (cur.value >= n) cur.value = Math.max(0, n - 1);
    }

    async function load() {
      try {
        data.value = await API.get('/api/projects/' + props.id);
        syncOutlineForm();
        const s = data.value.seasons || [];
        // 保持当前选择（'overall' 或有效季 id）；无效则回落到「总体」
        const valid = seasonId.value === 'overall' || s.find(x => x.id === seasonId.value);
        if (!valid) {
          seasonId.value = 'overall';
          if (tab.value === 'chapters' || tab.value === 'done') tab.value = 'outline';  // 所选季被删除后「章节」「完成」无作用域
        }
        if (!isOverall.value) syncSeasonForm();
        syncCur();
        if (!tabInit.value) { syncTab(); tabInit.value = true; }
      } catch (e) {
        if (e.status === 404) router.replace(backTo());
        ElementPlus.ElMessage.error(e.message);
      }
    }

    // 非流式推进：arc（故事大纲）/ chars（角色设定）
    async function doAction(step, payload) {
      actBusy.value = true;
      progress.text = '';
      try {
        await API.post(`/api/projects/${props.id}/action`, Object.assign({ step }, payload || {}), 0);
        ElementPlus.ElMessage.success(I18N.t('p.msgDone'));
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        await load();
      } finally {
        actBusy.value = false;
        progress.text = '';
      }
    }
    // 生成弹框：生成大纲 / 生成本季大纲 / 生成角色 共用（框内可填额外提示词）
    function openGenDlg(step, title) {
      genDlgStep.value = step;
      genDlgTitle.value = title;
      genDlgExtra.value = '';
      genDlg.value = true;
    }
    function confirmGen() {
      const extra = genDlgExtra.value.trim();
      const step = genDlgStep.value;
      genDlg.value = false;
      if (step === 'arc') {
        doAction('arc', { res_width: oW.value, res_height: oH.value, extra_prompt: extra });
      } else if (step === 'season_arc') {
        if (isOverall.value) return;
        doAction('season_arc', { season_id: seasonId.value, extra_prompt: extra });
      } else if (step === 'season_chars') {
        if (isOverall.value) return;
        doAction('season_chars', { season_id: seasonId.value, extra_prompt: extra });
      } else if (step === 'chars') {
        doAction('chars', { extra_prompt: extra });
      }
    }
    // 季（篇章）管理
    function selectSeason(id) {
      seasonId.value = id;
      selected.value = [];
      cur.value = 0;
      if (id === 'overall') {
        oSub.value = 'story';   // 总体模式首个子页签
      } else {
        oSub.value = 'arc';     // 季模式首个子页签
        syncSeasonForm();
      }
      // 总体模式没有「章节 / 完成」（两者都按具体季作用域），切到总体时回落到「企划」
      if (id === 'overall' && (tab.value === 'chapters' || tab.value === 'done')) tab.value = 'outline';
    }
    async function addSeason() {
      try {
        await API.post(`/api/projects/${props.id}/seasons`, { title: '' });
        ElementPlus.ElMessage.success(I18N.t('p.msgDone'));
        await load();
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    async function delSeason() {
      if (!seasonId.value) return;
      try {
        await API.del(`/api/projects/${props.id}/seasons/${seasonId.value}`);
        ElementPlus.ElMessage.success(I18N.t('p.msgDone'));
        await load();
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    function syncSeasonForm() {
      const s = seasons.value.find(x => x.id === seasonId.value);
      if (!s) {
        seasonArcText.value = '';
        seasonTitleText.value = '';
        seasonChars.value = [];
        cMin.value = 6;
        cMax.value = 12;
        return;
      }
      seasonArcText.value = s.arc || '';
      seasonTitleText.value = s.title || '';
      seasonChars.value = (s.characters || []).map(c => ({ id: c.id || '', name: c.name || '', description: c.description || '' }));
      cMin.value = s.count_min || 6;
      cMax.value = s.count_max || 12;
    }
    function addSeasonChar() {
      seasonChars.value.push({ id: '', name: '', description: '' });
    }
    function delSeasonChar(i) {
      seasonChars.value.splice(i, 1);
    }
    // 角色设定：多个角色（名字 / 形象性格 / 参考图）；新增角色未保存前 id 为空，存图前先落盘取 id
    function addChar() {
      chars.value.push({ id: '', name: '', description: '', image_url: '' });
    }
    function delChar(i) {
      chars.value.splice(i, 1);
    }
    async function uploadCharImage(i, uploadFile) {
      const file = uploadFile && uploadFile.raw;
      if (!file) return;
      if (!file.type || !file.type.startsWith('image/')) {
        ElementPlus.ElMessage.warning(I18N.t('p.uploadWarn'));
        return;
      }
      let c = chars.value[i];
      if (!c) return;
      if (!c.id) {
        try {
          await API.post(`/api/projects/${props.id}/outline`, {
            characters: chars.value.map(x => ({ id: x.id, name: x.name, description: x.description })),
          });
          await load();
          c = chars.value[i];
          if (!c) return;
        } catch (e) {
          ElementPlus.ElMessage.error(e.message);
          return;
        }
      }
      const fd = new FormData();
      fd.append('file', file);
      API.postForm(`/api/projects/${props.id}/characters/${c.id}/image`, fd)
        .then(r => {
          const t = chars.value[i];
          if (t) t.image_url = r.url || '';
          ElementPlus.ElMessage.success(I18N.t('p.msgCharImageUploaded'));
        })
        .catch(e => ElementPlus.ElMessage.error(e.message));
    }
    function removeCharImage(i) {
      const c = chars.value[i];
      if (!c || !c.id) return;
      API.del(`/api/projects/${props.id}/characters/${c.id}/image`)
        .then(() => {
          c.image_url = '';
          ElementPlus.ElMessage.success(I18N.t('p.msgCharImageRemoved'));
        })
        .catch(e => ElementPlus.ElMessage.error(e.message));
    }
    // AI 生成单个角色的形象/性格描述：有参考图且 LLM 支持视觉时以图为准；新角色未保存前先落盘取 id
    async function genCharDesc(i) {
      let c = chars.value[i];
      if (!c) return;
      if (!c.id) {
        try {
          await API.post(`/api/projects/${props.id}/outline`, {
            characters: chars.value.map(x => ({ id: x.id, name: x.name, description: x.description })),
          });
          await load();
          c = chars.value[i];
          if (!c) return;
        } catch (e) {
          ElementPlus.ElMessage.error(e.message);
          return;
        }
      }
      genDescBusy.value = c.id;
      try {
        const r = await API.post(`/api/projects/${props.id}/characters/${c.id}/gen-desc`, {}, 0);
        if (r && r.description) c.description = r.description;
        ElementPlus.ElMessage.success(I18N.t('p.msgCharDescGenerated'));
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        genDescBusy.value = null;
      }
    }
    async function genFirst(includeTitle = true) {
      busyFirst.value = true;
      try {
        await API.post(`/api/projects/${props.id}/first-image/generate`,
          { prompt: coverPrompt.value, include_title: includeTitle !== false }, 0);
        ElementPlus.ElMessage.success(I18N.t('p.msgFirstGenerated'));
        coverPrompt.value = '';
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        await load();
      } finally {
        busyFirst.value = false;
      }
    }
    async function genSeasonFirst(includeTitle = true) {
      busySeasonFirst.value = true;
      try {
        await API.post(`/api/projects/${props.id}/seasons/${seasonId.value}/first-image/generate`,
          { prompt: seasonCoverPrompt.value, include_title: includeTitle !== false }, 0);
        ElementPlus.ElMessage.success(I18N.t('p.msgSeasonFirstGenerated'));
        seasonCoverPrompt.value = '';
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
        await load();
      } finally {
        busySeasonFirst.value = false;
      }
    }

    // ---------------- 生成封面弹框（包含标题勾选） ----------------
    function openCoverGenDlg(target) {
      coverGenDlg.target = target;
      coverGenDlg.includeTitle = true;
      coverGenDlg.show = true;
    }
    function confirmCoverGen() {
      coverGenDlg.show = false;
      if (coverGenDlg.target === 'season') genSeasonFirst(coverGenDlg.includeTitle);
      else genFirst(coverGenDlg.includeTitle);
    }

    // ---------------- 叠加标题弹框（位置自由拖动 + 字号/样式/颜色） ----------------
    function openOvlDlg(target) {
      ovlDlg.target = target;
      if (target === 'season') {
        const s = curSeason.value;
        ovlDlg.titleText = (s && s.title) || I18N.t('p.season', (s && s.number) || 1);
        ovlDlg.coverUrl = (s && s.first_image_url) || '';
      } else {
        ovlDlg.titleText = (data.value && data.value.project.title) || '';
        ovlDlg.coverUrl = (data.value && data.value.project.first_image_url) || '';
      }
      ovlDlg.x = 0.5; ovlDlg.y = 1 / 3; ovlDlg.sizePct = 8;
      ovlDlg.style = 'bold_outline'; ovlDlg.color = '#ffffff'; ovlDlg.band = true;
      ovlDlg.show = true;
      // 弹框渲染后量取预览宽度，用于把字号百分比换算成预览像素
      setTimeout(syncOvlBoxW, 0);
    }
    function syncOvlBoxW() {
      if (ovlBox.value) ovlBoxW.value = ovlBox.value.clientWidth || ovlBoxW.value;
    }
    function ovlDragStart(e) {
      ovlDragging = true;
      try { e.currentTarget.setPointerCapture(e.pointerId); } catch (_) { /* 忽略 */ }
      ovlDragMove(e);
      e.preventDefault();
    }
    function ovlDragMove(e) {
      if (!ovlDragging || !ovlBox.value) return;
      const r = ovlBox.value.getBoundingClientRect();
      if (!r.width || !r.height) return;
      ovlDlg.x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      ovlDlg.y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    }
    function ovlDragEnd(e) {
      ovlDragging = false;
      try { e.currentTarget.releasePointerCapture(e.pointerId); } catch (_) { /* 忽略 */ }
    }
    const ovlTextStyle = computed(() => {
      const fs = Math.max(9, Math.round(ovlBoxW.value * ovlDlg.sizePct / 100));
      const s = {
        left: (ovlDlg.x * 100) + '%',
        top: (ovlDlg.y * 100) + '%',
        color: ovlDlg.color,
        fontSize: fs + 'px',
        transform: 'translate(-50%, -50%)',
      };
      if (ovlDlg.style === 'bold_outline') {
        s.fontWeight = 900;
        s.textShadow = '0 0 2px #000, 0 0 3px #000, 1px 1px 2px #000, -1px -1px 2px #000';
      } else if (ovlDlg.style === 'outline') {
        s.textShadow = '1px 0 0 #000, -1px 0 0 #000, 0 1px 0 #000, 0 -1px 0 #000, '
                     + '1px 1px 0 #000, -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000';
      } else if (ovlDlg.style === 'shadow') {
        s.textShadow = '3px 3px 4px rgba(0,0,0,.75)';
      }
      return s;
    });
    async function applyOvl() {
      ovlBusy.value = true;
      const body = { x: ovlDlg.x, y: ovlDlg.y, size_pct: ovlDlg.sizePct,
                     style: ovlDlg.style, color: ovlDlg.color, band: ovlDlg.band };
      try {
        const url = ovlDlg.target === 'season'
          ? `/api/projects/${props.id}/seasons/${seasonId.value}/first-image/overlay-title`
          : `/api/projects/${props.id}/first-image/overlay-title`;
        await API.post(url, body, 0);
        ElementPlus.ElMessage.success(I18N.t('p.msgOverlayTitle'));
        ovlDlg.show = false;
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        ovlBusy.value = false;
      }
    }
    async function planChapters() {
      // 章节规划（逐章）：先定总章数再逐章规划（每章参考前章承接剧情），SSE 实时逐个补入
      if (!seasonId.value) {
        ElementPlus.ElMessage.warning(I18N.t('p.seasonAdd'));
        return;
      }
      if (!(data.value?.project?.arc || '').trim() && !(seasonArcText.value || '').trim()) {
        ElementPlus.ElMessage.warning(I18N.t('p.planNeedArc'));
        return;
      }
      // 勾选章节 = 只重规划选中的章节（保留其余章节与已生成画面）；未勾选 = 全季重规划
      const sel = selected.value.slice().sort((a, b) => a - b);
      const subset = sel.length > 0;
      actBusy.value = true; progress.text = '';
      if (!subset) {
        data.value.chapters = data.value.chapters.filter(c => c.season_id !== seasonId.value);
        cur.value = 0;
      }
      const ctrl = new AbortController(); sseCtrl = ctrl;
      try {
        await API.sse(`/api/projects/${props.id}/action-stream`, {
          step: 'chapters',
          season_id: seasonId.value,
          count_min: cMin.value,
          count_max: cMax.value,
          ...(subset ? { indices: sel } : {}),
        }, (ev, d) => {
          if (ev === 'progress') progress.text = I18N.t('p.planProgress', d.current, d.total, d.title);
          else if (ev === 'chapter') applyChapterPlan(d);
          else if (ev === 'error') throw new Error(d.message);
        }, ctrl.signal);
        ElementPlus.ElMessage.success(I18N.t('p.msgDone'));
        await load();
      } catch (e) {
        if (!ctrl.signal.aborted) { ElementPlus.ElMessage.error(e.message); await load(); }
      }
      finally { actBusy.value = false; progress.text = ''; if (sseCtrl === ctrl) sseCtrl = null; }
    }

    // 分页保存：每个子页只提交自己的字段（后端 /outline 部分更新，未提交的字段不动）
    async function doSave(payload, okMsg) {
      busySave.value = true;
      try {
        await API.post(`/api/projects/${props.id}/outline`, payload);
        ElementPlus.ElMessage.success(okMsg);
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        busySave.value = false;
      }
    }
    // 总体（全局）：只保存全局字段（风格 / 整体大纲 / 全局提示词 / 分辨率）
    function saveStory() {
      busySave.value = true;
      (async () => {
        try {
          await API.post(`/api/projects/${props.id}/outline`, {
            arc: arcText.value, style: oStyle.value, global_prompt: oGlobal.value,
            res_width: oW.value, res_height: oH.value,
            auto_score: oScore.value, score_min: oScoreMin.value, auto_redo: oRedo.value,
          });
          ElementPlus.ElMessage.success(I18N.t('p.outSaved'));
          await load();
        } catch (e) { ElementPlus.ElMessage.error(e.message); }
        finally { busySave.value = false; }
      })();
    }
    // 总体（全局）：只保存核心角色设定
    function saveChars() {
      busySave.value = true;
      (async () => {
        try {
          await API.post(`/api/projects/${props.id}/outline`, {
            characters: chars.value.map(c => ({ id: c.id, name: c.name, description: c.description })),
          });
          ElementPlus.ElMessage.success(I18N.t('p.charsSaved'));
          await load();
        } catch (e) { ElementPlus.ElMessage.error(e.message); }
        finally { busySave.value = false; }
      })();
    }
    // 企划（每季）：保存本季大纲
    function saveSeasonArc() {
      if (!seasonId.value) return;
      busySave.value = true;
      (async () => {
        try {
          await API.patch(`/api/projects/${props.id}/seasons/${seasonId.value}`, { title: seasonTitleText.value, arc: seasonArcText.value });
          ElementPlus.ElMessage.success(I18N.t('p.seasonSaved'));
          await load();
        } catch (e) { ElementPlus.ElMessage.error(e.message); }
        finally { busySave.value = false; }
      })();
    }
    // 企划（每季）：保存本季角色
    function saveSeasonChars() {
      if (!seasonId.value) return;
      busySave.value = true;
      (async () => {
        try {
          await API.patch(`/api/projects/${props.id}/seasons/${seasonId.value}`, {
            characters: seasonChars.value.map(c => ({ id: c.id, name: c.name, description: c.description })),
          });
          ElementPlus.ElMessage.success(I18N.t('p.seasonSaved'));
          await load();
        } catch (e) { ElementPlus.ElMessage.error(e.message); }
        finally { busySave.value = false; }
      })();
    }
    function savePlan() {
      if (!seasonId.value) return;
      busySave.value = true;
      (async () => {
        try {
          await API.patch(`/api/projects/${props.id}/seasons/${seasonId.value}`, {
            count_mode: 'range',
            count_min: cMin.value,
            count_max: cMax.value,
            chapters: seasonChapters.value.map(c => ({ title: c.title, summary: c.summary })),
          });
          ElementPlus.ElMessage.success(I18N.t('p.planSaved'));
          await load();
        } catch (e) { ElementPlus.ElMessage.error(e.message); }
        finally { busySave.value = false; }
      })();
    }

    // 批量（SSE 进度）
    // 批量生成时单章两步完成：实时把该章提示词/媒体/状态写回本地并跟随定位，页面逐个刷新（后续章节仍会参考它）
    // 自动评分事件：刷新进度文案（评分中 / 低于阈值重做中 / 评分结果）
    function applyScoreLive(d) {
      if (d.phase === 'scoring') progress.text = I18N.t('p.scoreDoing', d.title);
      else if (d.phase === 'redo') progress.text = I18N.t('p.scoreRedoDo', d.title, d.redo, 2);
      else if (d.phase === 'error') {
        progress.text = I18N.t('p.scoreFailMsg', d.title, d.note);
        ElementPlus.ElMessage.warning(I18N.t('p.scoreFailMsg', d.title, d.note));
      }
      else progress.text = I18N.t('p.scoreResult', d.title, d.score) + (d.note ? ' · ' + d.note : '');
    }
    function applyChapterLive(d) {
      const arr = data.value?.chapters || [];
      const pos = arr.findIndex(c => c.index === d.index && c.season_id === seasonId.value);
      if (pos < 0) return;
      const ch = arr[pos];
      ch.status = d.status;
      ch.error = d.error || '';
      if (d.media_url) ch.media_url = d.media_url;
      if (d.prompt !== undefined) ch.prompt = d.prompt;
      if (d.description !== undefined) ch.description = d.description;
      if (d.width) { ch.width = d.width; ch.height = d.height; }
      if (d.score !== undefined) { ch.score = d.score; ch.score_note = d.score_note || ''; }
      const sPos = seasonChapters.value.findIndex(c => c.index === d.index);
      if (sPos >= 0) cur.value = sPos;
    }
    // 章节规划逐章回调：后端先清空旧章节，这里把规划出的章节按序号补入（新增或更新）并跟随定位到最新章节
    function applyChapterPlan(d) {
      const arr = data.value?.chapters || [];
      const pos = arr.findIndex(c => c.index === d.index && c.season_id === seasonId.value);
      if (pos >= 0) {
        const ch = arr[pos];
        ch.title = d.title; ch.summary = d.summary; ch.status = d.status;
      } else {
        arr.push({ index: d.index, season_id: seasonId.value, title: d.title, summary: d.summary,
                   status: d.status, description: '', prompt: '', media_url: '', error: '', width: 0, height: 0 });
        arr.sort((a, b) => a.index - b.index);
      }
      const sPos = seasonChapters.value.findIndex(c => c.index === d.index);
      if (sPos >= 0) cur.value = sPos;
    }
    // 停止批量生成：中断 SSE → 后端停止并提交已完成章节；页面同步刷新
    function stopGen() {
      if (sseCtrl) {
        sseCtrl.abort();
        ElementPlus.ElMessage.info(I18N.t('p.genStopped'));
      }
    }
    // 生成画面：勾选章节则只生成它们，未勾选则生成全部（两步连贯、逐章进行、后章参考前章已生成图）
    async function genAll() {
      if (!seasonId.value) return;
      busyGenAll.value = true; progress.text = '';
      const indices = selected.value.length ? selected.value : null;
      const ctrl = new AbortController(); sseCtrl = ctrl;
      try {
        await API.sse(`/api/projects/${props.id}/action-stream`, { step: 'generate', season_id: seasonId.value, indices }, (ev, d) => {
          if (ev === 'progress') progress.text = I18N.t('p.genProgress', d.current, d.total, d.title);
          else if (ev === 'score') applyScoreLive(d);
          else if (ev === 'chapter') applyChapterLive(d);
          else if (ev === 'error') throw new Error(d.message);
        }, ctrl.signal);
        ElementPlus.ElMessage.success(I18N.t('p.msgDone'));
        selected.value = [];
        await load();
      } catch (e) {
        if (ctrl.signal.aborted) { await load(); }  // 用户停止：后端已提交进度，同步刷新
        else { ElementPlus.ElMessage.error(e.message); await load(); }
      }
      finally { busyGenAll.value = false; progress.text = ''; if (sseCtrl === ctrl) sseCtrl = null; }
    }
    // VLM 批量评分：勾选章节则只评它们，未勾选则评全部（逐章进行；单章失败不阻塞后续）
    async function scoreAll() {
      if (!seasonId.value) return;
      busyScoreAll.value = true; progress.text = '';
      const indices = selected.value.length ? selected.value : null;
      const ctrl = new AbortController(); sseCtrl = ctrl;
      try {
        await API.sse(`/api/projects/${props.id}/action-stream`, { step: 'score', season_id: seasonId.value, indices }, (ev, d) => {
          if (ev === 'progress') progress.text = I18N.t('p.scoreProgress', d.current, d.total, d.title);
          else if (ev === 'score') applyScoreLive(d);
          else if (ev === 'chapter') applyChapterLive(d);
          else if (ev === 'error') throw new Error(d.message);
        }, ctrl.signal);
        ElementPlus.ElMessage.success(I18N.t('p.msgDone'));
        selected.value = [];
        await load();
      } catch (e) {
        if (ctrl.signal.aborted) { await load(); }  // 用户停止：后端已提交进度，同步刷新
        else { ElementPlus.ElMessage.error(e.message); await load(); }
      }
      finally { busyScoreAll.value = false; progress.text = ''; if (sseCtrl === ctrl) sseCtrl = null; }
    }

    // 导出作用域为当前所选季（「完成」页签仅在选定季时可用）
    // 统一走 API.download：浏览器 = 常规下载；CS 桌面客户端 = 系统「另存为」对话框
    async function exportMedia(fmt) {
      if (exporting.value) return;
      exporting.value = true;
      try {
        await API.download(`/api/projects/${props.id}/export/${fmt}?season_id=${encodeURIComponent(seasonId.value)}`);
        API.notify(I18N.t('app.title'), I18N.t('p.exportDone'));
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
      } finally {
        exporting.value = false;
      }
    }
    function exportZip() { return exportMedia('zip'); }
    function exportPdf() { return exportMedia('pdf'); }
    // PDF 预览：弹框内 iframe 渲染 inline PDF（不再新标签 window.open，避免桌面端弹窗被拦截报错）
    function previewPdf() {
      pdfUrl.value = `/api/projects/${props.id}/export/pdf?season_id=${encodeURIComponent(seasonId.value)}&preview=1`;
      pdfDlg.value = true;
    }

    async function addChapter() {
      if (!seasonId.value) return;
      try {
        await API.post(`/api/projects/${props.id}/chapters`, { season_id: seasonId.value });
        await load();
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    async function delChapter(i) {
      if (!seasonId.value) return;
      try {
        await API.del(`/api/projects/${props.id}/chapters/${i}?season_id=${seasonId.value}`);
        await load(); // load() 内 syncCur() 会把选中钳制到有效范围
      } catch (e) { ElementPlus.ElMessage.error(e.message); }
    }
    function onChapterReloaded(index) {
      if (typeof index === 'number' && index >= 0) cur.value = index; // 生成/写剧本后切到该章
      load();
    }

    function openReset() {
      rtitle.value = data.value.project.title || '';
      rogin.value = data.value.project.origin || '';
      // 现有风格：命中预设则选中该预设；否则归入「自定义」并回填原文
      const cur = (data.value.project.scope || {}).style || '';
      const presets = stylePresets.value;
      if (cur && presets.includes(cur)) {
        rstyle.value = cur; rstyleCustom.value = '';
      } else if (cur) {
        rstyle.value = 'custom'; rstyleCustom.value = cur;
      } else {
        rstyle.value = ''; rstyleCustom.value = '';
      }
      rclear.value = false;
      resetDlg.value = true;
    }
    async function saveReset() {
      if (!rogin.value.trim()) {
        ElementPlus.ElMessage.warning(I18N.t('p.resetOriginReq'));
        return;
      }
      try {
        await API.post(`/api/projects/${props.id}/reset`, {
          title: rtitle.value, origin: rogin.value,
          style: rstyle.value === 'custom' ? rstyleCustom.value.trim() : rstyle.value,
          clear_downstream: rclear.value,
        });
        ElementPlus.ElMessage.success(I18N.t('p.resetSaved'));
        resetDlg.value = false;
        await load();
      } catch (e) {
        ElementPlus.ElMessage.error(e.message);
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
        ElementPlus.ElMessage.success(I18N.t('p.cfgSaved'));
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
        ElementPlus.ElMessage.success(I18N.t('proj.msgDeleted'));
        router.push(backTo());
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
    watch(() => props.id, () => { tabInit.value = false; load(); });  // 同一路由切换不同项目时重载
    onBeforeUnmount(() => { if (sseCtrl) { sseCtrl.abort(); sseCtrl = null; } });
    return {
      data, scope, tab, oSub, isOverall, cur, curCh, selected, allSelected,
      seasons, seasonId, seasonArcText, seasonTitleText, seasonChars, seasonChapters, seasonDoneCount, seasonCompleted,
      locked, totalChCount, totalDoneCount, finishRows, finishReady, finishIssues, finishBusy, markFinished, unlock,
      selectSeason, addSeason, delSeason, curSeason, addSeasonChar, delSeasonChar,
      actBusy, busySave, busyGenAll, busyScoreAll, exporting, busyFirst, busySeasonFirst, genDescBusy, coverPrompt, seasonCoverPrompt, progress,
      coverGenDlg, ovlDlg, ovlBox, ovlBusy, ovlTextStyle, pvMode, pvItems, pvUrls, gotoChapter,
      arcText, oStyle, chars, oGlobal, oW, oH, oRatio, oRes, resRatios: RES_RATIOS, resOptions, onRatioChange, onResChange, cMin, cMax,
      cfgDlg, cfgBusy, cfg, lb, resetDlg, rtitle, rogin, rstyle, rstyleCustom, stylePresets, rclear, genDlg, genDlgTitle, genDlgExtra,
      openGenDlg, confirmGen, genFirst, genSeasonFirst, openCoverGenDlg, confirmCoverGen, openOvlDlg, applyOvl, ovlDragStart, ovlDragMove, ovlDragEnd, planChapters, saveStory, saveChars, saveSeasonArc, saveSeasonChars, savePlan, doAction, genAll, scoreAll, stopGen, isSel, toggleSelect, toggleAllSelect,
      addChar, delChar, uploadCharImage, removeCharImage, genCharDesc,
      exportZip, exportPdf, previewPdf, pdfDlg, pdfUrl, addChapter, delChapter, onChapterReloaded,
      openReset, saveReset, openCfg, saveCfg, del, openLb, load, backTo, router,
    };
  },
};
