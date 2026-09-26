// 漫画项目页 —— 叠加标题弹框（漫画专属，不与短剧共享）。
// 位置自由拖动 + 字号 / 样式 / 颜色 / 底条；拖动与预览字号换算在本组件内自持，
// 仅通过传入的 dlg（reactive 对象）读写数据，apply 交由父组件执行。
window.Views = window.Views || {};

Views.comicOverlayDialog = {
  props: {
    dlg: { type: Object, required: true },   // {show,target,titleText,coverUrl,x,y,sizePct,style,color,band}
    busy: { type: Boolean, default: false },
  },
  emits: ['apply'],
  template: `
    <el-dialog :model-value="dlg.show" @update:model-value="dlg.show = $event"
               :title="I18N.t('p.overlayDlgTitle')" width="640px">
      <div class="ovl-dlg">
        <div class="ovl-row">
          <div class="ovl-label">{{ I18N.t('p.overlayPos') }}</div>
          <div ref="ovlBox" class="ovl-stage" @pointerdown="ovlDragStart" @pointermove="ovlDragMove"
               @pointerup="ovlDragEnd" @pointercancel="ovlDragEnd">
            <img :src="dlg.coverUrl" draggable="false" alt="" />
            <div class="ovl-text" :style="ovlTextStyle">{{ dlg.titleText }}</div>
          </div>
        </div>
        <div class="ovl-row">
          <div class="ovl-label">{{ I18N.t('p.overlaySize') }}</div>
          <el-slider v-model="dlg.sizePct" :min="4" :max="20" :step="1" style="flex:1" />
          <span class="muted small" style="width:46px;text-align:right;">{{ dlg.sizePct }}%</span>
        </div>
        <div class="ovl-row">
          <div class="ovl-label">{{ I18N.t('p.overlayStyle') }}</div>
          <el-select v-model="dlg.style" style="width:150px">
            <el-option value="bold_outline" :label="I18N.t('p.overlayStyleBold')" />
            <el-option value="outline" :label="I18N.t('p.overlayStyleOutline')" />
            <el-option value="shadow" :label="I18N.t('p.overlayStyleShadow')" />
            <el-option value="plain" :label="I18N.t('p.overlayStylePlain')" />
          </el-select>
          <el-checkbox v-model="dlg.band" style="margin-left:16px;">{{ I18N.t('p.overlayBand') }}</el-checkbox>
        </div>
        <div class="ovl-row">
          <div class="ovl-label">{{ I18N.t('p.overlayColor') }}</div>
          <el-color-picker v-model="dlg.color" />
          <span class="muted small">{{ I18N.t('p.overlayDragHint') }}</span>
        </div>
        <p class="hint">{{ I18N.t('p.overlayBaseHint') }}</p>
      </div>
      <template #footer>
        <el-button @click="dlg.show = false">{{ I18N.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy" @click="$emit('apply')">{{ I18N.t('p.overlayApply') }}</el-button>
      </template>
    </el-dialog>
  `,
  setup(props) {
    const ovlBox = ref(null);      // 预览容器（拖动坐标系）
    const ovlBoxW = ref(300);      // 预览宽度（按比例换算预览字号）
    let dragging = false;

    // 弹框渲染后量取预览宽度，用于把字号百分比换算成预览像素
    watch(() => props.dlg.show, (v) => {
      if (v) nextTick(() => { if (ovlBox.value) ovlBoxW.value = ovlBox.value.clientWidth || ovlBoxW.value; });
    });

    function ovlDragStart(e) {
      dragging = true;
      try { e.currentTarget.setPointerCapture(e.pointerId); } catch (_) { /* 忽略 */ }
      ovlDragMove(e);
      e.preventDefault();
    }
    function ovlDragMove(e) {
      if (!dragging || !ovlBox.value) return;
      const r = ovlBox.value.getBoundingClientRect();
      if (!r.width || !r.height) return;
      props.dlg.x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      props.dlg.y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    }
    function ovlDragEnd(e) {
      dragging = false;
      try { e.currentTarget.releasePointerCapture(e.pointerId); } catch (_) { /* 忽略 */ }
    }

    const ovlTextStyle = computed(() => {
      const fs = Math.max(9, Math.round(ovlBoxW.value * props.dlg.sizePct / 100));
      const s = {
        left: (props.dlg.x * 100) + '%',
        top: (props.dlg.y * 100) + '%',
        color: props.dlg.color,
        fontSize: fs + 'px',
        transform: 'translate(-50%, -50%)',
      };
      if (props.dlg.style === 'bold_outline') {
        s.fontWeight = 900;
        s.textShadow = '0 0 2px #000, 0 0 3px #000, 1px 1px 2px #000, -1px -1px 2px #000';
      } else if (props.dlg.style === 'outline') {
        s.textShadow = '1px 0 0 #000, -1px 0 0 #000, 0 1px 0 #000, 0 -1px 0 #000, '
                     + '1px 1px 0 #000, -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000';
      } else if (props.dlg.style === 'shadow') {
        s.textShadow = '3px 3px 4px rgba(0,0,0,.75)';
      }
      return s;
    });

    return { ovlBox, ovlDragStart, ovlDragMove, ovlDragEnd, ovlTextStyle };
  },
};
