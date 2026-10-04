"""纯工具函数回归：i18n / 文件名安全 / 章节数量 / 图片 data URI 压缩。"""

import base64

from i18n import lang_of
from services.agent import image_data_uri
from services.media_files import is_video_url, safe_file_base
from services.pipeline_common import count_range, hex_to_rgb


class TestLangOf:
    def test_chinese_default(self):
        assert lang_of("") == "zh"
        assert lang_of("fr-FR,fr;q=0.9") == "zh"  # 未识别语言 → 默认中文

    def test_english(self):
        assert lang_of("en-US,en;q=0.9") == "en"
        assert lang_of("en") == "en"

    def test_qvalue_tiebreak(self):
        # en-US 隐式 q=1.0，zh-CN q=0.9 → en 赢（HTTP 规范）；zh 只有 q 更高才赢
        assert lang_of("en-US,en;q=0.5,zh-CN;q=0.9") == "en"
        assert lang_of("zh;q=0.5,en;q=0.9") == "en"
        assert lang_of("en;q=0.5,zh;q=0.9") == "zh"


class TestSafeFileBase:
    def test_blocks_path_traversal(self):
        # rename 接口只挡空串，title 可以带 / 和 .. —— 导出文件名主干必须在这里兜住
        assert "/" not in safe_file_base("../../../../tmp/pwn", "f")
        assert ".." not in safe_file_base("../../../../tmp/pwn", "f")
        assert "/" not in safe_file_base("/etc/passwd", "f")

    def test_fallback_on_empty(self):
        assert safe_file_base("", "fallback") == "fallback"
        assert safe_file_base("   ", "fallback") == "fallback"

    def test_caps_length(self):
        assert len(safe_file_base("a" * 200, "fallback")) <= 40


class TestCountRange:
    def test_defaults(self):
        assert count_range(0, 0) == (6, 12)

    def test_bad_input_falls_back(self):
        # 数值字段传 "abc"（设置弹框）应回退默认而不是抛 500
        assert count_range("abc", "xyz") == (6, 12)
        assert count_range(None, None) == (6, 12)

    def test_hi_never_below_lo(self):
        assert count_range(20, 3) == (20, 20)


class TestHexToRgb:
    def test_formats(self):
        assert hex_to_rgb("#fff") == (255, 255, 255)
        assert hex_to_rgb("#010203") == (1, 2, 3)
        assert hex_to_rgb("#01020304") == (1, 2, 3)

    def test_bad_falls_white(self):
        assert hex_to_rgb("") == (255, 255, 255)
        assert hex_to_rgb("nonsense") == (255, 255, 255)


class TestIsVideoUrl:
    def test_extensions(self):
        assert is_video_url("/media/a.mp4")
        assert is_video_url("/media/a.mp4?v=123")
        assert not is_video_url("/media/a.png")
        assert not is_video_url("")


class TestImageDataUri:
    def test_caps_large_image(self, tmp_path, monkeypatch):
        """4096 的图必须被压成小体积 JPEG，而不是 13MB 原样 base64。"""
        import PIL.Image as PIL

        big = tmp_path / "big.png"
        PIL.new("RGB", (4096, 4096), (90, 40, 200)).save(big)
        uri = image_data_uri(str(big))
        assert uri.startswith("data:image/jpeg;base64,")
        assert len(uri) < 2_000_000, f"应显著小于 13MB：{len(uri)}"

    def test_small_image_kept(self, tmp_path):
        import PIL.Image as PIL

        small = tmp_path / "small.png"
        PIL.new("RGB", (640, 480), (10, 200, 40)).save(small)
        uri = image_data_uri(str(small))
        assert uri.startswith("data:image/png;base64,")

    def test_corrupt_file_reads_raw(self, tmp_path):
        """存在但损坏/不可读的文件退化为原样读取（不抛异常，由调用方/模型端兜底）。"""
        bad = tmp_path / "corrupt.png"
        bad.write_bytes(b"not an image at all")
        uri = image_data_uri(str(bad))
        assert uri == "data:image/png;base64," + base64.b64encode(b"not an image at all").decode()
