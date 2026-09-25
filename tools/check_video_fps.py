"""视频帧率 / 音画同步自检脚本（无需 Draw Things app，用合成帧+音轨走真实封装链路）。

用法：.venv/bin/python tools/check_video_fps.py

检查项：
1. 模型 → 原生帧率推断（LTX 25 / Hunyuan 30 / SkyReels 24 / Wan 16 / 未知 25）与合法帧数步长；
2. 「秒数 → 帧数」换算（LTX 25fps：2s=49 帧、4s=97 帧）；
3. 端到端音画同步：25 帧 + 1 秒音轨，按错误帧率（5 / 50）封装应被自检发现并重封装为正确长度；
   按正确帧率（25）封装应原样通过（不误伤）。

背景：drawthings-py 的 GenConfig 会给未声明 fps 的预设回填 schema 默认值 5（非真实帧率），
若把 25fps 的帧按 5fps 封装，视频被拉长 5 倍、音轨只覆盖开头（表现为「视频声音只有 1 秒」）。
"""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.drawthings import (  # noqa: E402
    DrawThingsClient,
    frame_step_for,
    probe_video_audio_durations,
    snap_frames,
    video_fps,
)

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f"（期望 {want!r}）"))
    if not ok:
        FAILED.append(name)


def check_fps_table() -> None:
    print("== 模型 → 帧率 / 步长 ==")
    cases = {
        "ltx_2.3_22b_distilled_q8p.ckpt": (25, 8),
        "ltx_2_3_dev": (25, 8),
        "hunyuan_video_1.5_q8p.ckpt": (30, 4),
        "wan_2_2_14b_t2v_q8p.ckpt": (16, 4),
        "skyreels_v2_i2v.ckpt": (24, 4),
        "some_unknown_model.ckpt": (25, 1),   # 未知家族：帧率按 25 兜底、帧数步长不约束（1）
    }
    for model, (fps, step) in cases.items():
        check(f"video_fps({model})", video_fps(model), fps)
        check(f"frame_step_for({model})", frame_step_for(model), step)


def check_frame_math() -> None:
    print("== 秒数 → 帧数（LTX 25fps，step 8n+1）==")
    for sec, frames in ((2, 49), (4, 97)):
        check(f"{sec}s → frames", snap_frames(int(sec * 25), 8, "nearest"), frames)


class _Frame:
    """仅提供 to_video/_to_frame 需要的字段（合成帧）。"""

    def __init__(self, w: int, h: int):
        self.width, self.height, self.channels = w, h, 3
        self.data = bytes(w * h * 3)


def check_av_sync() -> None:
    print("== 音画同步自检（25 帧 + 1 秒音轨）==")
    try:
        from drawthings_py.grpc.audio import AudioBuffer
        from drawthings_py.image_generation_result import ImageGenerationResult
    except Exception as e:  # drawthings-py 未安装
        print(f"skip drawthings-py 未安装：{e}")
        return
    import shutil
    if not shutil.which("ffprobe"):
        print("skip 未安装 ffprobe（无法校验时长）")
        return

    W, H, N = 128, 96, 25           # 25 帧 = LTX 真实内容 1 秒（25fps）
    audio = AudioBuffer(48000, sample_rate=48000)   # 1.0 秒音轨
    audio.data[:] = 0.01
    result = ImageGenerationResult(images=[_Frame(W, H) for _ in range(N)], audio=audio)

    tmp = Path(tempfile.mkdtemp())
    client = DrawThingsClient(
        SimpleNamespace(base_url="127.0.0.1:7859", model_image="", model_video="ltx.ckpt",
                        max_side=0, max_seconds=8),
        tmp)
    model = "ltx_2.3_22b_distilled_q8p.ckpt"

    # ① 错误帧率（5）：视频 5s / 音轨 1s → 自检应发现并重封装为 25fps（视频 1s / 音轨 1s）
    bad = tmp / "stamped_5fps.mp4"
    result.to_video(str(bad), fps=5)
    v0, a0 = probe_video_audio_durations(bad)
    check("错误帧率封装后视频时长≈5s", round(v0, 1), 5.0)
    check("错误帧率封装后音轨时长≈1s（复现「声音只有 1 秒」）", round(a0, 1), 1.0)
    fps_used = client._remux_if_av_desynced(result, bad, 5, model)
    v1, a1 = probe_video_audio_durations(bad)
    check("自检反推帧率", fps_used, 25)
    check("重封装后视频≈音轨（音画同步）", round(v1, 1) == round(a1, 1) and round(v1, 1) == 1.0, True)

    # ② 正确帧率（25）：应原样通过，不重封装、不误伤
    good = tmp / "stamped_25fps.mp4"
    result.to_video(str(good), fps=25)
    check("正确帧率下自检不改动帧率", client._remux_if_av_desynced(result, good, 25, model), 25)
    v2, a2 = probe_video_audio_durations(good)
    check("正确帧率下音画等长", round(v2, 1) == round(a2, 1) == 1.0, True)

    # ③ 帧率取高（50）：视频 0.5s / 音轨 1s（音轨反而长于视频）→ 同样应反推为 25 并重封装
    high = tmp / "stamped_50fps.mp4"
    result.to_video(str(high), fps=50)
    v3, a3 = probe_video_audio_durations(high)
    check("帧率取高时视频≈0.5s、音轨≈1s", (round(v3, 1), round(a3, 1)), (0.5, 1.0))
    check("帧率取高时自检反推帧率", client._remux_if_av_desynced(result, high, 50, model), 25)
    v4, a4 = probe_video_audio_durations(high)
    check("帧率取高时重封装后音画等长", round(v4, 1) == round(a4, 1) == 1.0, True)


def main() -> int:
    check_fps_table()
    check_frame_math()
    check_av_sync()
    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项未通过：" + "、".join(FAILED))
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
