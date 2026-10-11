"""drawthings 模型名归一化 / 预设推断 / 参考图开关 —— 回归测试。

最近多个 commit 都在修这里的推断 bug（d9aaae4 / e6350ca / bfec9b1），
这些是纯函数，值得锁死。
"""

from services.drawthings import (
    _family_fixups,
    _native_size,
    _norm_model,
    _norm_variants,
    _rgba_alpha_first_to_rgba,
    infer_preset,
    norm_ref_flag,
)


class TestNormModel:
    def test_unify_separators(self):
        # 连字符 / 下划线 / 空格 / 点 → 下划线（预设表按 _ 索引）
        assert _norm_model("qwen-image-2.1") == "qwen_image_2_1"
        assert _norm_model("qwen_image_2.1") == "qwen_image_2_1"
        assert _norm_model("qwen image 2.1") == "qwen_image_2_1"

    def test_strip_extension_and_quant(self):
        assert _norm_model("ltx_2_3_22b.safetensors") == "ltx_2_3_22b"
        assert _norm_model("SDXL_1.0_f16") == "sdxl_1_0"
        assert _norm_model("foo_q8_1.0") == "foo_1_0"

    def test_keep_version_number(self):
        # 版本号与型号标识都保留；剥号由 _norm_variants 的候选键负责
        assert _norm_model("qwen_image_2512_lightning") == "qwen_image_2512_lightning"


class TestNormVariants:
    def test_precise_first(self):
        vs = _norm_variants("qwen_image_2512_lightning")
        assert vs[0] == "qwen_image_2512_lightning"

    def test_drop_trailing_version(self):
        vs = _norm_variants("ltx_2_3_22b_distilled_1_1")
        assert vs[0] == "ltx_2_3_22b_distilled_1_1"
        assert "ltx_2_3_22b_distilled_1" in vs


class TestInferPreset:
    def test_real_presets_resolve(self):
        """真实 drawthings-py 预设表：常见管线模型必须能推断出预设。"""
        for model in ("ltx_2_3_22b_distilled_1_1", "qwen_image_2512_lightning", "flux_1_schnell"):
            assert infer_preset(model), f"应能推断 {model} 的预设"

    def test_hyphen_variants_map_to_underscore_presets(self):
        # 用户填连字符/空格写法，也应命中同一预设（归一化查表）
        assert infer_preset("qwen-image") == infer_preset("qwen_image")

    def test_unknown_returns_empty(self):
        assert infer_preset("no_such_model_xyz") == ""


class TestQwen21Family:
    """Qwen-Image 2.1：满血版走官方配方，蒸馏版保持少步，且绝不挂 2512 的 lightning LoRA。"""

    def test_full_21_gets_official_recipe(self):
        for m in ("qwen_image_2.1_q6p.ckpt", "qwen-image-2.1", "qwen image 2.1"):
            assert _family_fixups(m) == {"steps": 40, "guidance": 1.0}

    def test_distilled_21_keeps_few_steps(self):
        # 满血 40 步套到 4 步蒸馏模型上是错的：应保持族内少步，仅统一 CFG=1.0
        f = _family_fixups("qwen_image_2.1_lightning")
        assert f["guidance"] == 1.0
        assert f["steps"] <= 8

    def test_earlier_qwen_models_untouched(self):
        assert _family_fixups("qwen_image_2512_q6p.ckpt") == {}
        assert _family_fixups("qwen_image_edit_2511_q8p.ckpt") == {}

    def test_21_never_resolves_to_2512_lightning_preset(self):
        # 2.1 蒸馏版绝不能回落到带 2512 专用 LoRA 的 lightning 预设
        assert infer_preset("qwen_image_2.1_lightning") == "qwen_image_2512"
        assert infer_preset("qwen_image_2.1_q6p.ckpt") == "qwen_image_2512"

    def test_2512_lightning_still_uses_lightning_preset(self):
        assert infer_preset("qwen_image_2512_lightning") == "qwen_image_2512_lightning"

    def test_native_2k_matches_official_aspect_ratios(self):
        # Qwen-Image 2.1 官方 2K 档：16:9 = 2752×1536、9:16 = 1536×2752
        assert _native_size("qwen_image_2.1", 1920, 1080, 2048) == (2752, 1536)
        assert _native_size("qwen_image_2.1", 1080, 1920, 2048) == (1536, 2752)


class TestAlphaFirstChannelFix:
    """Qwen-Image 2.1：Draw Things 下发的 4 通道是 (A,R,G,B)，须重排为 (R,G,B,A)。

    否则蓝通道被当成 alpha —— 暖色图蓝通道低，整图被合成为「几乎全透明」。"""

    def test_argb_rotated_to_rgba(self):
        # 源按 (A,R,G,B)：像素0 = (A=255,R=10,G=20,B=30)、像素1 = (0,200,100,50)
        src = bytes([255, 10, 20, 30, 0, 200, 100, 50])
        out = _rgba_alpha_first_to_rgba(src, 2, 1)
        assert out == bytes([10, 20, 30, 255, 200, 100, 50, 0])


class TestNormRefFlag:
    def test_none_means_follow(self):
        assert norm_ref_flag(None) is None
        assert norm_ref_flag("") is None

    def test_bool_and_int(self):
        assert norm_ref_flag(True) == 1
        assert norm_ref_flag(False) == 0
        assert norm_ref_flag(1) == 1
        assert norm_ref_flag(0) == 0

    def test_string(self):
        assert norm_ref_flag("1") == 1
        assert norm_ref_flag("true") == 1
        assert norm_ref_flag("yes") == 1
        assert norm_ref_flag("on") == 1
        assert norm_ref_flag("0") == 0
        assert norm_ref_flag("off") == 0
