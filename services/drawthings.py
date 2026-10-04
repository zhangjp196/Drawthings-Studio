"""Draw Things（Mac 本地出图/出视频）客户端 —— 仅 gRPC。

app 的 API server 设为 **gRPC**（默认端口 7859）；本应用只用这一种传输：
- gRPC 返回**帧序列**（`ImageGenerationResponse.generatedImages`），视频由客户端用 ffmpeg
  合成为 mp4（LTX 等还会带回音轨），因此**出图 / 出视频都支持**。
  （HTTP API 对视频模型只回单帧，已弃用并移除。）
- gRPC 请求必须自带完整生成配置（FlatBuffer）：用 `drawthings-py` 的**预设**（preset）提供
  steps / sampler / guidance / 尺寸等，再用本配置里的**图像模型 / 视频模型**覆盖 model
  （可只填其一 = 只支持该类型）。
- 参考图（图生图 / 图生视频）需在配置里**勾选「支持参考图片」**（ref_image / ref_video）：
  勾选后把上一章媒体作为 init_image 传入；未勾选一律文生图 / 文生视频（参考图被忽略）。
- 崩溃自愈：生成中 app 闪退 / 断连（Draw Things 图生图已知缺陷，社区 issue #121）时，自动等待 app
  重启（每轮上限 2 分钟）并自动重试生成（最多 2 轮），无法恢复才报错；等待过程经 on_status 回调上报。
- 模型清单可从 app 读取（`get_models`，需 refresh_cache），生成前会校验模型已下载。
- 在线状态检测：`probe_endpoint` 连一次 gRPC 判在线/离线（配置页徽标用），带超时与短 TTL 缓存，不触发生成。

图像分辨率：调用方 params > 预设，受 max_side（最长边）限幅。
视频尺寸：调用方 params（width/height）> 预设，同样受 max_side 限幅（0 = 用预设尺寸）。
视频帧率：预设显式 fps > 按模型族推断（LTX 25 / Hunyuan 30 / SkyReels 24 / Wan 16）。
视频帧数：调用方 params > 预设；受 max_seconds（秒）上限与「10 秒硬上限」（fps × 10）双重约束，并吸附到模型合法帧数（LTX 8n+1）。
音画同步自检：合成后用 ffprobe 校验音轨与视频是否等长，明显不等长（= 帧率取错）则按音轨反推帧率重封装。
`drawthings-py` 为懒加载：未安装时只有实际生成会报错。
"""
import asyncio
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

logger = logging.getLogger("drawthings")

DEFAULT_GRPC_PORT = 7859


class GenerationCancelled(RuntimeError):
    """生成被调用方主动取消（协作式取消：cancel_event 置位）。"""

# Draw Things 已知缺陷：图生图（带 init_image）时 app 可能闪退（官方社区 issue #121，未修复）。
# 连接中断后自动等待用户重启 app（端口恢复，每轮上限 RECOVERY_TIMEOUT 秒），再自动重试生成
# （最多 MAX_RECOVERY_RETRIES 轮）；始终未恢复 / 重试仍失败才报错。
RECOVERY_POLL = 3.0           # 秒：等待恢复期间的端口探测间隔
RECOVERY_TIMEOUT = 120.0      # 秒：单轮等待 app 恢复的最长时间
MAX_RECOVERY_RETRIES = 2      # 断连后自动重试的轮数（含首次共最多 3 次生成尝试）
RECOVERY_SETTLE = 3.0         # 秒：端口恢复后先等 app 稳定，再发起重试


def _looks_disconnected(e: Exception) -> bool:
    """按异常文本判断是否为连接类错误（app 可能闪退 / 被关闭）。"""
    s = str(e)
    return any(k in s for k in ("UNAVAILABLE", "closed", "Connection", "Reset",
                                 "Cancelled", "Broken pipe", "EOF", "incomplete"))


def _is_no_frames(e: Exception) -> bool:
    """是否为「服务端未返回任何帧/图」错误（Draw Things app 侧：未加载模型 / 显存不足 / 当前无法生成）。"""
    s = str(e)
    return ("No images received from server" in s
            or "未返回任何图像" in s
            or "returned no image" in s)

# 视频模型名关键词：子串匹配（无歧义）+ 词元匹配（易混短词，按 _/-/数字 切分后整词比较）。
# 覆盖 Draw Things 常见视频模型：LTX-Video(ltx)、SVD、Wan、HunyuanVideo、CogVideoX、Mochi、
# FramePack、AnimateDiff、DynamiCrafter、EasyAnimate 等；新增模型时在此补充关键词即可。
_VIDEO_SUBSTR = (
    "video", "svd", "i2v", "t2v", "v2v", "img2vid", "vid2vid",
    "cogvideo", "sora", "kling", "vidu", "ltx", "mochi",
    "framepack", "dynamicrafter", "easyanimate", "hunyuanvideo", "seaweed",
)
_VIDEO_TOKENS = {"wan", "ltx", "animate", "animation", "motion", "animatediff", "framepack"}

# 单视频时长硬上限（秒）：上限帧数 = fps × MAX_VIDEO_SECONDS（fps 由 video_fps(model) / 预设显式值确定）。
# 10 秒 = 可配置时长上限；LTX-2 官方支持更长，但为兼容多数模型/显存，这里取 10s。
MAX_VIDEO_SECONDS = 10

# max_side（最大分辨率 · 最长边）可配置上限：4096 = 4K。0 = 不限（跟随 app / 预设）。
MAX_SIDE_LIMIT = 4096

# 功能级「不启用」哨兵：与空串区分 —— 空串 = 跟随 DrawThings 配置里的模型，
# 该值 = 明确禁用（即便配置里有图像/视频模型，本作品也不出图 / 不出视频，只出文本）。
MODEL_NONE = "__none__"

# 功能级 max_steps（最大 Step 数）可配上限：0 = 跟随预设自带步数。
# 图像与视频**分开**配置：两者需求差别很大（图像如 qwen-image 需补到 50~100；
# 视频如 LTX 蒸馏版预设仅 8 步，反而宜少步）。取 200 封顶，避免误设上千步跑数小时。
# 步数不足时去噪不彻底 —— 典型表现是出图半透明、颜色发灰。
MAX_STEPS_LIMIT = 200


def is_video_model(model_name: str) -> bool:
    """按模型名判断是否视频模型（不确定时按图像）。"""
    name = (model_name or "").lower()
    if any(s in name for s in _VIDEO_SUBSTR):
        return True
    tokens = set(re.split(r"[^0-9a-z]+", name))
    return bool(tokens & _VIDEO_TOKENS)


def cap_size(w: int, h: int, max_side: int) -> tuple[int, int]:
    """最大分辨率约束：最长边超过 max_side 时等比缩小（取整到 64 的倍数，最小 64）。"""
    if not max_side or max(w, h) <= max_side:
        return w, h
    s = max_side / max(w, h)
    cap = lambda v: max(64, int(v * s / 64 + 0.5) * 64)
    w, h = cap(w), cap(h)
    if max(w, h) > max_side:  # 取整后仍超 → 最长边强制等于上限
        w = max_side if w >= h else w
        h = max_side if h > w else h
    return w, h


def frame_step_for(model: str) -> int:
    """视频模型的合法帧数步长：合法帧数 = step×n + 1（LTX 为 8n+1，Wan/Hunyuan 等为 4n+1）。"""
    n = (model or "").lower()
    if "ltx" in n:
        return 8
    if any(k in n for k in ("wan", "hunyuan", "svd", "i2v", "t2v", "cogvideo",
                            "mochi", "framepack", "animatediff", "dynamicrafter")):
        return 4
    return 1


# 视频模型原生帧率：drawthings-py 预设未声明 fps 时，GenConfig 会回填 schema 默认值 5（并非真实帧率），
# 因此按模型族推断真实帧率。用于「秒数 → 帧数」换算与 mp4 封装帧率（错了会拉长视频、音画不同步）。
_VIDEO_FPS = (("ltx", 25), ("hunyuan", 30), ("skyreels", 24), ("wan", 16))


def video_fps(model: str) -> int:
    """视频模型帧率（fps）：按模型族推断（LTX 25 / Hunyuan 30 / SkyReels 24 / Wan 16），未知按 25。"""
    n = (model or "").lower()
    for key, fps in _VIDEO_FPS:
        if key in n:
            return fps
    return 25


# 合理帧率区间：按音轨时长反推帧率时的合法性校验（超出即视为推测不可靠，不改动原文件）。
_FPS_MIN, _FPS_MAX = 6, 60
# 音画同步容差：音轨/视频时长比在 0.8~1.25 内视为正常（一体化模型的音轨应与视频等长）。
_AV_SYNC_RATIO = 0.8


def probe_video_audio_durations(path: str | Path) -> tuple[float, float]:
    """用 ffprobe 读取媒体文件的（视频时长, 音频时长）秒；无 ffprobe / 无对应轨道返回 (0, 0)。"""
    if not shutil.which("ffprobe"):
        return 0.0, 0.0
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return 0.0, 0.0
    v = a = 0.0
    for line in out.splitlines():
        parts = [p for p in line.strip().split(",") if p]
        if len(parts) != 2:
            continue
        kind, raw = parts
        try:
            dur = float(raw)
        except ValueError:
            continue  # duration 可能为 N/A
        if kind == "video" and not v:
            v = dur
        elif kind == "audio" and not a:
            a = dur
    return v, a


def snap_frames(frames: int, step: int, mode: str = "nearest") -> int:
    """把帧数吸附到合法值（step×n + 1）：mode = nearest（就近）| floor（不超过）。"""
    frames = max(1, int(frames))
    if step <= 1 or frames <= 1:
        return frames
    k = round((frames - 1) / step) if mode == "nearest" else (frames - 1) // step
    return max(1, step * int(k) + 1)


def _cv2():
    """惰性导入 OpenCV（可选依赖）；未安装返回 None。"""
    try:
        import cv2  # noqa: F401  可选依赖：用于抽帧 / 无 ffmpeg 时合成视频
        return cv2
    except Exception:
        return None


_FFMPEG_PREPARED = False


def ensure_ffmpeg_on_path() -> str | None:
    """确保有一个可用的 `ffmpeg` 命令（用于合成含音轨的视频）。

    优先级：系统 PATH 中的 ffmpeg → `imageio-ffmpeg` 自带的静态 ffmpeg（pip 安装，无需系统安装）。
    自带二进制文件名不是 `ffmpeg`，这里将其链接/复制为一个名为 `ffmpeg` 的文件并加入 PATH，
    使 drawthings-py 等内部 subprocess 调用也能找到它。返回可用路径或 None。
    """
    global _FFMPEG_PREPARED
    exe = shutil.which("ffmpeg")
    if exe:
        _FFMPEG_PREPARED = True
        return exe
    if _FFMPEG_PREPARED:
        return shutil.which("ffmpeg")
    try:
        import imageio_ffmpeg
        src = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    try:
        if not (src and Path(src).is_file()):
            return None
        d = Path(tempfile.gettempdir()) / "dts_ffbin"
        d.mkdir(parents=True, exist_ok=True)
        link = d / "ffmpeg"
        if not link.is_file():
            try:
                link.symlink_to(src)
            except Exception:
                shutil.copy2(src, link)
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
        _FFMPEG_PREPARED = True
        return shutil.which("ffmpeg")
    except Exception:
        return None


def _frames_to_video_cv2(result, out_path: Path, fps: int) -> bool:
    """无 ffmpeg 时用 OpenCV 把帧序列写成 mp4（**不含音频**）。成功返回 True。"""
    cv2 = _cv2()
    if cv2 is None:
        return False
    try:
        frames = []
        for i in range(len(result)):
            tmp = Path(out_path).parent / f"_f_{uuid.uuid4().hex[:8]}.png"
            result[i].to_file(str(tmp))
            img = cv2.imread(str(tmp))
            try:
                tmp.unlink()
            except Exception:
                pass
            if img is not None:
                frames.append(img)
        if not frames:
            return False
        h, w = frames[0].shape[:2]
        vw = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             float(fps or 25), (w, h))
        if not vw.isOpened():
            return False
        try:
            for f in frames:
                vw.write(f)
        finally:
            vw.release()
        return Path(out_path).is_file()
    except Exception as e:
        logger.warning("OpenCV 合成视频失败：%s", e)
        return False


def concat_videos(video_paths: list[str], out_path: Path) -> bool:
    """把多段视频按顺序首尾相接合成为一段 mp4（短剧「合成视频」导出用）。

    **仅用 ffmpeg concat demuxer**（保留各段音轨）：先 `-c copy` 流拷贝，失败则重编码为
    统一的 h264/aac。无可用 ffmpeg 则返回 False。片段缺失/损坏会被跳过；全部无效也返回 False。
    """
    paths = [str(p) for p in (video_paths or []) if p and Path(p).is_file()]
    if not paths:
        return False
    exe = ensure_ffmpeg_on_path()
    if not exe:
        logger.warning("合成视频失败：未找到可用的 ffmpeg")
        return False
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    listfile = out_path.parent / f"_concat_{uuid.uuid4().hex[:8]}.txt"
    try:
        # concat demuxer 需要转义单引号；用绝对路径
        with open(listfile, "w", encoding="utf-8") as fh:
            for p in paths:
                ap = Path(p).resolve().as_posix().replace("'", "'\\''")
                fh.write(f"file '{ap}'\n")
        # 1) 流拷贝（编码一致时最快且保留音轨）
        cmd = [exe, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "concat", "-safe", "0", "-i", str(listfile),
               "-c", "copy", str(out_path)]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 0:
            return True
        # 2) 流拷贝失败（各段编码不一致等）：统一重编码为 h264/aac
        cmd = [exe, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "concat", "-safe", "0", "-i", str(listfile),
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out_path)]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 0:
            return True
        logger.warning("ffmpeg 合成视频失败：%s", (proc.stderr or b"")[:300])
        return False
    except Exception as e:
        logger.warning("ffmpeg 合成视频异常：%s", e)
        return False
    finally:
        try:
            listfile.unlink()
        except OSError:
            pass



def extract_last_frame(video_path: str, media_dir: Path) -> str | None:
    """上一段视频 → 末帧图（供下一段参考 / 喂多模态 LLM）。

    图片直接返回；GIF 用 PIL 取最后一帧；视频优先用 **OpenCV** 取末帧（不依赖 ffmpeg），
    OpenCV 不可用时回退 ffmpeg；都失败返回 None（调用方降级为不附带参考帧）。
    """
    p = Path(video_path)
    if not p.is_file():
        return None
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    ext = p.suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".webp"):
        return str(p)
    if ext == ".gif":
        try:
            gif = Image.open(p)
            gif.seek(gif.n_frames - 1)
            out = media_dir / f"lastframe_{uuid.uuid4().hex[:8]}.png"
            gif.convert("RGB").save(out)
            return str(out)
        except Exception:
            return None
    # 视频：优先 OpenCV（自带解码，无需系统 ffmpeg）
    cv2 = _cv2()
    if cv2 is not None:
        cap = None
        try:
            cap = cv2.VideoCapture(str(p))
            if cap.isOpened():
                frame = None
                n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if n > 0:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, n - 1))
                    _, frame = cap.read()
                if frame is None:
                    # 容器帧数不准：顺序读到最后一帧
                    while True:
                        ok, f2 = cap.read()
                        if not ok:
                            break
                        frame = f2
                if frame is not None:
                    out = media_dir / f"lastframe_{uuid.uuid4().hex[:8]}.png"
                    cv2.imwrite(str(out), frame)
                    return str(out) if out.is_file() else None
        except Exception:
            pass
        finally:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
    # 回退：ffmpeg（系统或 imageio-ffmpeg 自带）
    if not ensure_ffmpeg_on_path():
        return None  # 降级：无参考帧
    out = media_dir / f"lastframe_{uuid.uuid4().hex[:8]}.png"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-sseof", "-0.5",
             "-i", str(p), "-frames:v", "1", str(out)],
            check=True, timeout=60)
        return str(out) if out.is_file() else None
    except Exception:
        return None


def parse_endpoint(base_url: str, default_port: int = DEFAULT_GRPC_PORT) -> tuple[str, int]:
    """把 base_url（可含 scheme）解析为 (host, port)。无端口用 default_port。"""
    s = (base_url or "").strip()
    if "://" not in s:
        s = "grpc://" + s
    u = urlparse(s)
    host = u.hostname or "127.0.0.1"
    try:
        port = int(u.port or default_port)
    except (TypeError, ValueError):
        port = default_port
    return host, port


def _norm_model(model: str) -> str:
    """模型名归一化：统一分隔符 + 去扩展名 / 量化精度，便于跨变体匹配预设。

    **分隔符统一为 `_`**：同一模型名有人写连字符、有人写下划线、导出工具还会用空格
    （`qwen-image-2.1` / `qwen_image_2.1` / `qwen image 2.1`），不统一会导致查表不中 ——
    预设表里是 `qwen_image`，而用户填 `qwen-image-2.1` 就会推断失败、直接无法生成。

    末尾版本号**不在这里剥**：`_1.1` / `_2.3` 之类该剥，但 `2512` / `2511` 是 qwen-image
    的**型号标识**，剥掉会让 `qwen_image_2512_lightning` 塌成 `qwen_image` 而错配到非
    lightning 预设（30 步跑 4 步蒸馏模型）。改由 `_norm_variants` 以候选键的方式处理。"""
    s = (model or "").lower()
    s = re.sub(r"\.(ckpt|safetensors)$", "", s)
    sep = r"[._\-\s]"                            # 模型名里可能出现的分隔符：点 / 下划线 / 连字符 / 空格
    s = re.sub(sep + r"(q\d+p|i\d+x|f16|bf16|fp16|f8|q\d|i\d)(?=" + sep + r"|$)", "", s)
    s = re.sub(r"[\s.\-]+", "_", s)              # 空格 / 点 / 连字符 → 下划线（最后统一，保证查表命中）
    return s.strip("._-")


def _norm_variants(model: str) -> list[str]:
    """归一化候选键，按「精确优先」排序：原名 → 逐级剥掉末尾版本号。

    两者都登记进查表，才能既认 `ltx_2_3_22b_distilled_1_1`（要剥 `_1_1`）
    又认 `qwen_image_2512_lightning`（不能剥 `_2512`）。"""
    base = _norm_model(model)
    out = [base]
    for tail in re.findall(r"_\d+(?:\.\d+)*$", base):     # 如 ["_1_1"] / ["_2512"]
        short = base[: -len(tail)]
        if short and short not in out:
            out.append(short)
    return out


_PRESET_MODEL_MAP: dict[str, str] | None = None


def _preset_model_map() -> dict[str, str]:
    """{归一化模型名: drawthings-py 预设名}（懒加载缓存；未安装返回空）。"""
    global _PRESET_MODEL_MAP
    if _PRESET_MODEL_MAP is not None:
        return _PRESET_MODEL_MAP
    m: dict[str, str] = {}
    try:
        from drawthings_py import Configs
        from drawthings_py.configs.presets import Presets
        for p in Presets:
            name = str(p.value)
            try:
                model = str(Configs.from_preset(name)["model"] or "")
            except Exception:
                continue
            if model:
                for key in _norm_variants(model):
                    cur = m.get(key)
                    # 同一模型有多个预设时优先非 lightning
                    if cur is None or ("lightning" in cur and "lightning" not in name):
                        m[key] = name
    except Exception:
        pass
    _PRESET_MODEL_MAP = m
    return m


def infer_preset(model: str) -> str:
    """按模型文件名推断 drawthings-py 预设（归一化匹配预设模型 + 关键词兜底）。

    例：ltx_2.3_22b_distilled_1.1_q6p.ckpt → ltx_2_3_distilled；
        flux_2_klein_9b_q6p.ckpt → flux_2_klein_9b；z_image_turbo_1.0_q6p.ckpt → z_image_turbo。
    推断不到返回 ""（调用方给出明确错误）。"""
    n = (model or "").strip()
    if not n:
        return ""
    m = _preset_model_map()
    for key in _norm_variants(n):
        hit = m.get(key)
        if hit:
            return hit
    # ---- 关键词兜底 ----
    # drawthings-py 里 *lightning 预设和它非 lightning 的 counterparts 共用同一个 model
    # 文件名（如 `qwen_image_2512_lightning` 的 model 字段也是 `qwen_image_2512_q6p.ckpt`），
    # 所以查表在原理上区分不了蒸馏变体 —— 只能靠模型名里的标志位。
    low = n.lower()
    # 蒸馏 / 少步变体步数极低（4~8），错配到多步预设（30 步）出图会严重不对，故逐族分支。
    fast = any(k in low for k in ("lightning", "turbo", "schnell", "distill"))
    if "ltx" in low:
        return "ltx_2_3_dev" if "dev" in low else "ltx_2_3_distilled"
    if "wan" in low:
        base = "wan_2_2_14b_i2v" if "i2v" in low else "wan_2_2_14b_t2v"
        return f"{base}_lightning" if fast else base
    if "hunyuan" in low and "video" in low:
        return "hunyuan_video"
    if "qwen" in low:
        base = "qwen_image_edit_2511" if "edit" in low else "qwen_image_2512"
        return f"{base}_lightning" if fast else base
    if "z_image" in low or "z-image" in low:
        return "z_image_turbo" if fast else "z_image_base"
    return ""


def _model_files(mi) -> set[str]:
    """从 ModelsInfo 取基座模型文件名集合（兼容 dict / 对象两种返回）。"""
    out: set[str] = set()
    for m in getattr(mi, "models", []) or []:
        f = ""
        if isinstance(m, dict):
            f = str(m.get("file") or "")
        else:
            f = str(getattr(m, "file", "") or "")
        if f:
            out.add(f)
    return out


async def _fetch_models_async(host: str, port: int, refresh: bool = True) -> list[dict]:
    """连接 gRPC 取模型清单（`get_models` 首次可能是空缓存，必须 refresh）。

    返回 [{file, name, version, video}]；仅含基座模型（不含 VAE / 文本编码器等 files）。"""
    from drawthings_py import DrawThings
    svc = DrawThings.grpc(host=host, port=port, progressbar=False, disable_messages=True)
    await svc.connect()
    try:
        mi = await svc.get_models(refresh_cache=refresh)
    finally:
        try:
            await svc.close()
        except Exception:
            pass
    out: list[dict] = []
    for m in getattr(mi, "models", []) or []:
        if isinstance(m, dict):
            f = str(m.get("file") or ""); nm = str(m.get("name") or ""); ver = str(m.get("version") or "")
        else:
            f = str(getattr(m, "file", "") or ""); nm = str(getattr(m, "name", "") or ""); ver = str(getattr(m, "version", "") or "")
        if not f:
            continue
        out.append({"file": f, "name": nm, "version": ver, "video": is_video_model(f)})
    return out


def fetch_models(host: str, port: int, refresh: bool = True) -> list[dict]:
    """同步包装：gRPC 模型清单。"""
    return asyncio.run(_fetch_models_async(host, port, refresh))


_PROBE_TTL = 3.0   # 在线检测结果缓存秒数：配置页多张卡片同时检测时避免反复建连
_PROBE_TIMEOUT = 4.0   # 单次检测超时（秒）：离线端点尽快判负，不拖住页面
_PROBE_CACHE: dict[str, tuple[float, dict]] = {}


def probe_endpoint(host: str, port: int, timeout: float = _PROBE_TIMEOUT) -> dict:
    """Draw Things 在线状态检测：连得上 gRPC = 在线（顺带读 app 当前已缓存的模型数量）。

    返回 {online, models, elapsed_ms, error}，**离线不抛异常**（前端要显示「离线 + 原因」）。
    按 host:port 缓存 _PROBE_TTL 秒，避免同一端点被多张卡片反复建连。"""
    key = f"{host}:{port}"
    now = time.monotonic()
    hit = _PROBE_CACHE.get(key)
    if hit and now - hit[0] < _PROBE_TTL:
        return dict(hit[1])

    async def _probe() -> int:
        from drawthings_py import DrawThings
        svc = DrawThings.grpc(host=host, port=port, progressbar=False, disable_messages=True)
        await svc.connect()
        try:
            mi = await svc.get_models(refresh_cache=False)   # 不刷新缓存：只读 app 当前可见的模型
        finally:
            try:
                await svc.close()
            except Exception:
                pass
        return len(_model_files(mi))

    t0 = time.monotonic()
    online, models, err = False, 0, ""
    try:
        models = asyncio.run(asyncio.wait_for(_probe(), timeout))
        online = True
    except Exception as e:
        err = str(e).strip() or e.__class__.__name__
    out = {"online": online, "models": models,
           "elapsed_ms": int((time.monotonic() - t0) * 1000), "error": err}
    _PROBE_CACHE[key] = (time.monotonic(), out)
    return dict(out)


# Qwen-Image 系列的出图**对分辨率敏感**：只在原生档位上布局正常，任意尺寸会崩布局，
# 且**文字最先崩**（字形被拉成又高又窄、挤成一团）。官方原生档位（Qwen 文档）：
#   1024 档：1:1=1024×1024  4:3=1152×864  3:4=864×1152  3:2=1248×832  2:3=832×1248
#            16:9=1536×864  9:16=864×1536
#   2K 原生：2048×2048 / 2400×1792 / 1792×2400 / 2528×1696 / 1696×2528 / 2752×1536 / 1536×2752
# 之前按 64 的倍数自由下发（如 768×512）虽然比例对、也是 32 的倍数，但绝对尺寸不在
# 任何原生档上 —— 这正是 qwen 出图字形崩坏的原因。故按比例吸附到原生档。
_QWEN_NATIVE_SIZES: tuple[tuple[int, int], ...] = (
    (1024, 1024), (1152, 864), (864, 1152), (1248, 832), (832, 1248), (1536, 864), (864, 1536),
)
_QWEN_NATIVE_SIZES_2K: tuple[tuple[int, int], ...] = (
    (2048, 2048), (2400, 1792), (1792, 2400), (2528, 1696), (1696, 2528), (2752, 1536), (1536, 2752),
)


def _check_preset_fit(model: str, preset: str, cfg) -> None:
    """预设与模型是否真的配套 —— 不配套只告警不阻断，但必须让日志里看得见。

    静默套错预算是最难查的一类问题：出图能出来、只是「看着不对」，没有任何报错。
    这里覆盖两类实测踩到的坑：
      ① **蒸馏变体拿到多步预设**（lightning / turbo / schnell / distilled 预设只有 4~8 步，
         套成 30 步会出废图）；
      ② **拿旧型号预设套新版本模型**（如 Qwen-Image 2.1 套 `qwen_image_2512`）——
         参数不是为该模型调的，出图布局/色彩都可能不对。
    """
    low = (model or "").lower()
    try:
        steps = int(cfg["steps"])
    except Exception:
        steps = 0
    fast = any(k in low for k in ("lightning", "turbo", "schnell", "distill"))
    if fast and steps > 8:
        logger.warning("模型 %s 是少步/蒸馏变体，却匹配到多步预设 %s（%d 步），出图可能严重不对。",
                       model, preset, steps)
    fx = _family_fixups(model)
    if fx and not _same_version_family(low, preset):
        logger.warning("模型 %s 疑似比预设 %s 更新，参数未必适配（已按族修正：%s）。"
                       "若出图仍异常，说明该版本尚无配套预设，属预期内。",
                       model, preset, ", ".join(f"{k}={v}" for k, v in fx.items()))
    try:
        guidance = cfg["guidance"]
    except Exception:
        guidance = "?"
    logger.debug("预设原始参数：模型 %s → 预设 %s（steps=%s guidance=%s）", model, preset, steps, guidance)


def _same_version_family(model_low: str, preset: str) -> bool:
    """模型名里的版本标识（`2512` / `2_1` / `2511`）是否出现在预设名里 —— 用来判断是否拿旧预设套新模型。"""
    nums = set(re.findall(r"\d+", model_low))
    return bool(nums & set(re.findall(r"\d+", preset)))


def _native_size(model: str, w: int, h: int, max_side: int) -> tuple[int, int] | None:
    """把请求尺寸吸附到模型原生分辨率档位（按宽高比选最近的档）；无需吸附返回 None。

    `max_side ≥ 2048` 时用 2K 原生档，否则用省显存的 1024 档。"""
    if "qwen" not in (model or "").lower():
        return None
    table = _QWEN_NATIVE_SIZES_2K if max_side and max_side >= 2048 else _QWEN_NATIVE_SIZES
    tw, th = (w, h) if (w and h) else (1, 1)            # 未指定尺寸 → 按 1:1
    want = tw / th
    return min(table, key=lambda s: abs(math.log((s[0] / s[1]) / want)))


# Qwen-Image 2.1 是 2512 **之后**的新版本，而 drawthings-py 0.4.x 的 qwen 预设只到
# `qwen_image_2512` / `qwen_image_edit_2511`（PyPI 最新 0.4.0、GitHub v0.4.1 预设清单相同），
# 上游没有 2.1 预设。直接套 2512 会用错参数：2512 是 guidanceScale=4，而 2.1 官方配方是
# **steps=40、CFG=1.0（关闭 guidance）、negative 留空**（2.1 底模已做 guidance 蒸馏）。
# CFG 拉太高正是「出图发灰 / 半透明 / 颜色不饱和」的典型成因，故在此按族修正。
_QWEN_21_RE = re.compile(r"(?:^|[^0-9])2[._\- ]?1(?![0-9])")


def _family_fixups(model: str) -> dict:
    """按模型族修正预设参数：上游预设缺型号 / 参数不匹配时兜底。空 = 不修正。"""
    low = (model or "").lower()
    if "qwen" not in low or not _QWEN_21_RE.search(low):
        return {}
    return {"steps": 40, "guidance": 1.0}


class DrawThingsClient:
    """Draw Things gRPC 客户端（出图 / 出视频）。"""

    def __init__(self, cfg, data_dir: Path):
        self.host, self.port = parse_endpoint(getattr(cfg, "base_url", ""))
        self.model_image = str(getattr(cfg, "model_image", "") or "").strip()
        self.model_video = str(getattr(cfg, "model_video", "") or "").strip()
        self.max_side = 0             # 功能级（随项目 / 作品走，见 build_drawthings_client）
        self.max_seconds = 0# 功能级视频秒数上限（同上）
        self.max_steps_image = 0   # 功能级（随模型走，见 build_drawthings_client）
        self.max_steps_video = 0   # 图像 / 视频分开，两者步数需求差别很大
        self.ref_image = bool(getattr(cfg, "ref_image", 0))   # 图像模型支持参考图片（图生图）
        self.ref_video = bool(getattr(cfg, "ref_video", 0))   # 视频模型支持参考图片（图生视频）
        self.media_dir = Path(data_dir) / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        # 生成过程中的状态回调（如「正在等待 Draw Things 恢复…」）；调用方按需要设置，可为 None
        self.on_status: Callable[[str], None] | None = None
        # 协作式取消：置位后本次生成尽快停止（threading.Event，跨线程安全）；None = 不支持取消
        self.cancel_event = None

    def cancel(self) -> None:
        """请求取消本次生成（若无 cancel_event 则无操作）。"""
        ev = self.cancel_event
        if ev is not None:
            try:
                ev.set()
            except Exception:
                pass

    def _cancelled(self) -> bool:
        ev = self.cancel_event
        return bool(ev is not None and ev.is_set())

    # ---------------- 能力 ----------------
    def supports_image(self) -> bool:
        return bool(self.model_image)

    def supports_video(self) -> bool:
        return bool(self.model_video)

    def supports_image_ref(self) -> bool:
        """图像模型是否支持参考图片（图生图）：勾选「支持参考图片」且配了图像模型才生效。"""
        return bool(self.model_image) and self.ref_image

    def supports_video_ref(self) -> bool:
        """视频模型是否支持参考图片（图生视频）：勾选「支持参考图片」且配了视频模型才生效。"""
        return bool(self.model_video) and self.ref_video

    def current_model(self) -> str:
        return self.model_video or self.model_image

    def _model_for(self, video: bool) -> str:
        return self.model_video if video else self.model_image

    def detect_media_type(self) -> str:
        """配了视频模型即按视频（否则图像）。"""
        return "video" if self.model_video else "image"

    # ---------------- 对外接口 ----------------
    def generate_image(self, prompt: str, ref_path: str | None = None,
                       params: dict | None = None) -> str:
        """返回生成图片的绝对路径。
        开启「支持参考图片」（ref_image）且 ref_path 非空 = 图生图（参考上一章）；未开启则忽略参考图，按文生图。
        冗余防错：参考图相关的失败（含 Draw Things 图生图闪退缺陷）→ 自动降级为「无参考图」重试一次，
        避免单张参考图问题导致整章失败。"""
        if not self.supports_image_ref():
            ref_path = None
        try:
            return asyncio.run(self._generate(prompt, video=False, ref_path=ref_path, params=params or {}))
        except Exception:
            if not ref_path:
                raise
            logger.warning("图生图（参考图 %s）生成失败，自动降级为无参考图重试", ref_path, exc_info=True)
            self._report("参考图生成失败，已自动改为「无参考图」重试一次…")
            return asyncio.run(self._generate(prompt, video=False, ref_path=None, params=params or {}))

    def generate_video(self, prompt: str, ref_video_path: str | None = None,
                       params: dict | None = None) -> str:
        """返回生成视频的绝对路径。
        开启「支持参考图片」（ref_video）时 ref_video_path = 上一章视频，先抽末帧作为参考（短剧帧连续的关键）；
        未开启则忽略参考图，按文生视频（不抽帧、不传 init_image）。
        冗余防错：参考图相关的失败 → 自动降级为「无参考图」重试一次。"""
        ref_frame = None
        if ref_video_path and self.supports_video_ref():
            ref_frame = extract_last_frame(ref_video_path, self.media_dir)
        try:
            return asyncio.run(self._generate(prompt, video=True, ref_path=ref_frame, params=params or {}))
        except Exception:
            if not ref_frame:
                raise
            logger.warning("图生视频（参考帧 %s）生成失败，自动降级为无参考图重试", ref_frame, exc_info=True)
            self._report("参考图生成失败，已自动改为「无参考图」重试一次…")
            return asyncio.run(self._generate(prompt, video=True, ref_path=None, params=params or {}))

    # ---------------- 音画同步自检 ----------------
    def _remux_if_av_desynced(self, result, out: Path, fps: int, model: str) -> int:
        """一体化音视频模型（如 LTX）自带的音轨应与视频基本等长；明显不等长说明帧率取错了，按音轨反推并重封装。

        返回最终封装用的帧率。无 ffprobe / 无音轨 / 时长正常时原样返回；重封装失败保留原文件（不阻塞生成）。
        """
        v_dur, a_dur = probe_video_audio_durations(out)
        frames = len(result)
        if not (v_dur > 0 and a_dur > 0 and frames > 0):
            return fps
        if v_dur * _AV_SYNC_RATIO <= a_dur <= v_dur / _AV_SYNC_RATIO:
            return fps  # 音画基本等长（容差内），无需干预
        # 视频时长 = 帧数 / 帧率，音轨时长 ≈ 帧数 / 真实帧率 → 真实帧率 ≈ 帧数 / 音轨时长
        corrected = int(round(frames / a_dur))
        if corrected == fps or not (_FPS_MIN <= corrected <= _FPS_MAX):
            logger.warning(
                "视频音画不同步：模型 %s 视频 %.2fs / 音轨 %.2fs（反推帧率 %s，不可靠时不改文件）。"
                "请为该模型补充帧率。", model, v_dur, a_dur, corrected)
            return fps
        logger.warning("视频音画不同步：模型 %s 视频 %.2fs / 音轨 %.2fs，帧率 %s → %s 重新封装",
                       model, v_dur, a_dur, fps, corrected)
        try:
            result.to_video(str(out), fps=corrected)
        except Exception as e:
            logger.warning("重新封装失败，保留原文件：%s", e)
            return fps
        v2, a2 = probe_video_audio_durations(out)
        if v2 > 0 and a2 > 0 and not (v2 * _AV_SYNC_RATIO <= a2 <= v2 / _AV_SYNC_RATIO):
            logger.warning("重新封装后仍不同步：视频 %.2fs / 音轨 %.2fs（模型 %s）", v2, a2, model)
        return corrected

    # ---------------- 内部 ----------------
    def _gen_config(self, model: str, video: bool = False):
        try:
            from drawthings_py import Configs
        except Exception as e:  # 未安装 drawthings-py
            raise RuntimeError(
                "未安装 drawthings-py，无法使用 Draw Things。请 `pip install \"drawthings-py[ffmpeg]\"`。"
                f" / drawthings-py is not installed ({e})")
        preset = infer_preset(model)
        if not preset:
            raise RuntimeError(
                f"无法根据模型名「{model}」推断生成预设：该模型不受 drawthings-py 预设支持。"
                "请改用受支持的模型（如 ltx / flux / z_image / ernie_image / qwen_image / wan / hunyuan 等）。"
                f" / Cannot infer a preset for model '{model}'.")
        try:
            cfg = Configs.from_preset(preset)
        except Exception as e:
            raise RuntimeError(f"无法加载 Draw Things 预设 {preset}：{e} / Cannot load preset {preset}: {e}")
        _check_preset_fit(model, preset, cfg)
        if model:
            cfg["model"] = model
        # 族修正（上游预设缺该型号 / 参数不匹配时兜底），先于用户步数覆盖生效
        for key, val in _family_fixups(model).items():
            try:
                cfg[key] = val
            except Exception:
                pass
        # 步数：按图像 / 视频取对应的功能级 max_steps，> 0 时覆盖预设自带步数；0 = 跟随预设。
        steps = self.max_steps_video if video else self.max_steps_image
        if steps > 0:
            try:
                cfg["steps"] = int(steps)
            except Exception:
                pass
        try:
            _log = "steps=%s guidance=%s" % (cfg["steps"], cfg["guidance"])
        except Exception:
            _log = ""
        logger.info("生成参数：%s %s → 预设 %s（%s）", "视频" if video else "图像", model, preset, _log)
        return cfg

    async def _port_open(self, timeout: float = 2.0) -> bool:
        """探测 DrawThings gRPC 端口是否可达（快速判断 app 存活 / 已闪退）。"""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _report(self, msg: str) -> None:
        """生成状态回调（吞掉回调自身异常，不影响生成流程）。"""
        if self.on_status:
            try:
                self.on_status(msg)
            except Exception:
                pass

    async def _with_cancel(self, coro):
        """在 cancel_event 置位时取消内部协程（协作式）：无 cancel_event 时直通。"""
        if self.cancel_event is None:
            return await coro
        task = asyncio.ensure_future(coro)

        async def _watch():
            while not task.done():
                if self._cancelled():
                    task.cancel()
                    return
                await asyncio.sleep(0.3)

        watcher = asyncio.ensure_future(_watch())
        try:
            return await task
        except asyncio.CancelledError:
            if self._cancelled():
                raise GenerationCancelled("生成已取消 / Generation cancelled")
            raise
        finally:
            watcher.cancel()

    async def _wait_recovery(self) -> bool:
        """app 断连后等待用户重启 DrawThings（端口恢复）。True=已恢复，False=超时。
        等待期间通过 on_status 定时上报进度（界面可展示「正在等待 Draw Things 恢复…」）。
        被取消（cancel_event 置位）时抛 GenerationCancelled。"""
        t0 = time.monotonic()
        deadline = t0 + RECOVERY_TIMEOUT
        last_report = 0.0
        while time.monotonic() < deadline:
            if self._cancelled():
                raise GenerationCancelled("生成已取消 / Generation cancelled")
            if await self._port_open():
                return True
            now = time.monotonic()
            if now - last_report >= 10:
                last_report = now
                self._report(f"正在等待 Draw Things 恢复…（已等待 {int(now - t0)}s / 上限 {RECOVERY_TIMEOUT:.0f}s）")
            await asyncio.sleep(RECOVERY_POLL)
        return False

    async def _retry_video_fewer_frames(self, cfg, req, prompt: str, ref_path,
                                        info: str, first_err: Exception,
                                        avail_video: list[str] | None = None):
        """视频「无帧返回」时按更少帧数重试一次（帧数减半并落到合法帧步长）。
        成功返回结果；仍失败则抛出带排查提示的错误。"""
        from drawthings_py import DrawThings, RequestBuilder
        model = self._model_for(True)
        step = frame_step_for(model)
        try:
            cur = int(cfg["num_frames"] or 0)
        except Exception:
            cur = 0
        reduced = snap_frames(max(step + 1, cur // 2), step, "floor") if cur > 0 else 0
        if reduced <= 0 or reduced >= cur:
            raise RuntimeError(self._no_frames_hint(info, first_err, avail_video=avail_video))
        self._report(f"未收到视频帧，正在按更少帧数（{cur}→{reduced} 帧）自动重试一次…")
        logger.warning("视频无帧返回，降帧重试：%d→%d 帧（%s）", cur, reduced, info)
        cfg["num_frames"] = reduced
        req2 = RequestBuilder(cfg, prompt)
        if ref_path:
            try:
                req2.init_image(ref_path)
            except Exception:
                req2 = RequestBuilder(cfg, prompt)
        svc = DrawThings.grpc(host=self.host, port=self.port,
                              progressbar=False, disable_messages=True)
        await svc.connect()
        try:
            result = await self._with_cancel(svc.generate(req2))
        except GenerationCancelled:
            raise
        except Exception as e2:
            if _is_no_frames(e2):
                raise RuntimeError(self._no_frames_hint(info, e2, reduced=reduced, avail_video=avail_video))
            raise RuntimeError(
                f"Draw Things 生成失败（降帧重试）：{e2}（本次请求：{info}）"
                f" / Draw Things generation failed (reduced-frames retry): {e2} (request: {info})")
        finally:
            try:
                await svc.close()
            except Exception:
                pass
        return result

    @staticmethod
    def _no_frames_hint(info: str, err: Exception, reduced: int = 0,
                        avail_video: list[str] | None = None) -> str:
        """「无帧返回」的可操作排查提示（Draw Things app 侧原因，非本应用请求错误）。"""
        extra = f"，已自动降帧（{reduced} 帧）重试仍无帧" if reduced else ""
        avail = ""
        if avail_video:
            avail = (f" 当前 app 内检测到的视频模型：{', '.join(sorted(set(avail_video)))}——"
                     "若本次所用模型不在此列，说明它未下载/文件名不符，请改用其中的模型。")
        return (
            f"Draw Things 未返回任何视频帧（本次请求：{info}{extra}）。"
            "请求已正确送出，属 Draw Things app 侧未产出画面，常见原因与排查："
            "① app 内当前**加载/选中的模型**与本次请求不一致（请在 Draw Things 里**选中**该视频模型后再试；"
            "gRPC 请求只有在 app 当前模型与请求模型一致时才会出画面）；"
            "② 模型显存占用过高（LTX 高分辨率/多帧容易爆显存）——请**降低分辨率或减少帧数/时长**后重试；"
            "③ 该模型/该帧数在 app 内不受支持——换用更短时长或换模型；"
            "④ 先在 Draw Things 里**手动跑一次同类生成**确认可用，再回到本应用重试。"
            + avail +
            f" / Draw Things returned no video frames (request: {info}). The request was sent correctly; "
            f"this is Draw Things app-side. Check: the model currently selected in Draw Things matches the request; "
            f"lower resolution/frames (possible OOM); model/frame-count support; run a manual generation first.")

    async def _generate(self, prompt: str, video: bool, ref_path: str | None, params: dict) -> str:
        from drawthings_py import DrawThings, RequestBuilder
        model = self._model_for(video)
        if not model:
            kind = "视频" if video else "图像"
            raise RuntimeError(
                f"未配置{kind}模型：请在 DrawThings 配置里填写{kind}模型文件名。"
                f" / No {kind} model configured.")
        cfg = self._gen_config(model, video)
        # 尺寸：图片 = 调用方 params > 预设，再受 max_side 限幅；视频尺寸由预设/模型决定
        # 帧率：预设**显式声明**优先，否则按模型族推断。
        # 不能直接读 cfg["fps"]：GenConfig 会给未声明的键回填 schema 默认值 5，
        # 把 25fps 的 LTX 当 5fps → 视频被拉长 5 倍、帧数换算错误、音轨只覆盖开头（音画不同步）。
        try:
            explicit = int(cfg["fps"]) if "fps" in list(cfg) else 0
        except Exception:
            explicit = 0
        fps = explicit or video_fps(model)
        if not video:
            w = int(params.get("width") or 0)
            h = int(params.get("height") or 0)
            if not (w and h):
                try:
                    w, h = int(cfg["width"]), int(cfg["height"])
                except Exception:
                    w = h = 0
            native = _native_size(model, w, h, self.max_side)
            if native:
                # 原生档位优先于 max_side：非原生尺寸出的图本身就是坏的（qwen 文字崩坏），
                # 宁可超出软上限也要落在原生档。
                if self.max_side and max(native) > self.max_side:
                    logger.warning("模型 %s 只在原生分辨率下正常，已用 %dx%d 超过最大分辨率 %d；"
                                   "请把「最大分辨率」调到 %d 以上，否则出图布局会崩。",
                                   model, native[0], native[1], self.max_side, max(native))
                w, h = native
            elif w and h and self.max_side:
                w, h = cap_size(w, h, self.max_side)
            if w and h:
                cfg["width"], cfg["height"] = w, h
        else:
            # 时长（秒）：调用方指定 > 配置上限（0=不限→内置上限）> 内置上限；
            # 三者都受「内置 MAX_VIDEO_SECONDS 秒上限」约束；帧数 = 秒数 × 帧率（吸附到合法 8n+1）。
            cap_sec = self.max_seconds if self.max_seconds > 0 else MAX_VIDEO_SECONDS
            cap_sec = min(cap_sec, MAX_VIDEO_SECONDS)
            try:
                req_sec = float(params.get("seconds") or 0)
            except (TypeError, ValueError):
                req_sec = 0
            sec = req_sec if req_sec > 0 else float(cap_sec)
            sec = max(1.0, min(sec, float(cap_sec)))
            step = frame_step_for(model)
            frames = snap_frames(max(1, int(sec * fps)), step, "nearest")
            # 预设帧数只作为「未显式指定时长/帧数」时的默认参考，不再当作硬上限：
            # 否则永远卡在预设的 121 帧，无法生成更长/更短的片段。
            explicit = bool(params.get("num_frames")) or req_sec > 0
            if not explicit:
                try:
                    preset_frames = int(cfg["num_frames"] or 0)
                except Exception:
                    preset_frames = 0
                if preset_frames > 0:
                    frames = min(frames, snap_frames(preset_frames, step, "floor"))
            if params.get("num_frames"):
                frames = int(params["num_frames"])
            # 硬上限 = 生效上限（配置 max_seconds，封顶内置 10s）换算的帧数；显式 num_frames 也不得越过。
            hard = max(1, int(fps * cap_sec))
            if frames > hard:
                frames = snap_frames(hard, step, "floor")   # 硬上限以下的最大合法帧数
            frames = min(frames, hard)
            cfg["num_frames"] = max(1, frames)
            # 尺寸：调用方 params > 预设（0 = 用预设），再受 max_side（最长边）限幅 —— 与图片分支一致，
            # 否则「项目分辨率」设置对视频不生效，且短剧记录的 width/height 会与真实输出不符。
            try:
                w, h = int(params.get("width") or 0), int(params.get("height") or 0)
            except (TypeError, ValueError):
                w = h = 0
            if not (w and h):
                try:
                    w, h = int(cfg["width"]), int(cfg["height"])
                except Exception:
                    w = h = 0
            if w and h and self.max_side:
                w, h = cap_size(w, h, self.max_side)
            if w and h:
                cfg["width"], cfg["height"] = w, h

        # 本次请求尺寸（供摘要；模型可能在预检中被同族替换，故摘要放到预检之后）
        try:
            _w, _h = int(cfg["width"]), int(cfg["height"])
        except Exception:
            _w = _h = 0
        _size = f"{_w}×{_h}" if (_w and _h) else "预设尺寸"

        # 生成前预检：取 app 实际可用的（视频）模型清单，供「无帧返回」时诊断，并提前拦截「模型未下载」。
        avail_video: list[str] = []
        try:
            _mi = await _fetch_models_async(self.host, self.port, refresh=True)
            _all = set()
            for _m in _mi:
                _f = str(_m.get("file") or "")
                if _f:
                    _all.add(_f)
                    if is_video_model(_f):
                        avail_video.append(_f)
            if _all and model not in _all:
                # 名称不完全匹配：再试「归一化」匹配（忽略量化后缀 / 版本号，q6p vs q8p 等）。
                # 命中 → 说明这是同一模型的其它量化：用 app 里**实际存在**的文件名替换后继续
                #（app 只会加载它已有的文件名；发送不存在的文件名会「无帧返回」）。
                near = sorted(f for f in _all if _norm_model(f) == _norm_model(model))
                if not near:
                    raise RuntimeError(
                        f"模型「{model}」不在 Draw Things 的模型列表中（请确认文件名，或在 Draw Things 内下载该模型）。"
                        + (f" 可用视频模型：{', '.join(sorted(avail_video))}。" if avail_video else "")
                        + f" / Model '{model}' is not in Draw Things' model list.")
                # 优先与原请求同量化/同扩展名的候选，否则取第一个
                pick = next((f for f in near if f == model), None) or \
                       next((f for f in near if Path(f).suffix == Path(model).suffix), near[0])
                self._report(f"请求模型「{model}」本机不存在，改用同族模型「{pick}」生成…")
                logger.warning("请求模型「%s」不在 app 列表，自动改用同族文件「%s」（候选：%s）",
                                model, pick, near)
                model = pick
                cfg["model"] = pick
        except RuntimeError:
            raise
        except Exception as _e:
            logger.warning("生成前取模型清单失败（忽略，继续生成）：%s", _e)

        # 本次请求摘要（预检之后构建：模型可能已被同族替换）
        if video:
            try:
                _nf = int(cfg["num_frames"] or 0)
            except Exception:
                _nf = 0
            _info = f"模型「{model}」 · {_size} · {_nf} 帧 · {fps} fps"
        else:
            _info = f"模型「{model}」 · {_size}"
        if ref_path:
            _info += " · 有参考图"

        req = RequestBuilder(cfg, prompt)
        if ref_path:
            try:
                if not Path(ref_path).is_file():
                    raise FileNotFoundError(f"参考图不存在：{ref_path}")
                req.init_image(ref_path)
            except Exception as e:
                # 冗余防错：参考图不可用（文件丢失 / 格式异常 / 加载失败）→ 本次按无参考图生成，不中断整章
                logger.warning("参考图不可用，本次改为无参考图生成（%s）：%s", ref_path, e)
                self._report("参考图不可用，已自动改为「无参考图」生成…")
                req = RequestBuilder(cfg, prompt)

        async def _attempt():
            """一次完整生成：连接 → 校验模型 → 生成（finally 保证关闭连接）。"""
            svc = DrawThings.grpc(host=self.host, port=self.port,
                                  progressbar=False, disable_messages=True)
            await svc.connect()
            try:
                return await svc.generate(req)
            finally:
                try:
                    await svc.close()
                except Exception:
                    pass

        try:
            result = await self._with_cancel(_attempt())
        except GenerationCancelled:
            raise
        except Exception as e:
            if not _looks_disconnected(e) and await self._port_open():
                # app 仍在：普通生成错误（含「无帧返回」）
                if video and _is_no_frames(e):
                    # 视频「无帧返回」常见于本次帧数/尺寸对当前 app 状态过大（显存不足）：
                    # 自动降帧重试一次（减半，仍受合法帧步长约束），成功则继续；仍失败则抛排查提示。
                    result = await self._retry_video_fewer_frames(cfg, req, prompt, ref_path,
                                                                  _info, e, avail_video)
                else:
                    # 附带本次请求摘要后抛出（便于定位「No images received」等）
                    raise RuntimeError(
                        f"Draw Things 生成失败：{e}（本次请求：{_info}）"
                        f" / Draw Things generation failed: {e} (request: {_info})")
            else:
                # 连接中断 / app 已掉线：Draw Things 图生图闪退的已知缺陷（社区 issue #121）
                # → 等待用户重启 app，自动重试生成（最多 MAX_RECOVERY_RETRIES 轮）
                last = e
                for retry in range(1, MAX_RECOVERY_RETRIES + 1):
                    logger.warning("DrawThings 连接中断（app 可能已闪退），等待恢复（第 %d/%d 轮重试）：%s",
                                    retry, MAX_RECOVERY_RETRIES, e)
                    self._report(f"与 Draw Things 的连接中断（app 可能已闪退），正在等待 app 重启后自动重试…（第 {retry}/{MAX_RECOVERY_RETRIES} 轮）")
                    if not await self._wait_recovery():
                        raise RuntimeError(
                            f"与 Draw Things 应用的连接中断（app 可能已闪退），等待 {RECOVERY_TIMEOUT:.0f} 秒后仍未恢复。"
                            "请重启 Draw Things 后重试本次生成。"
                            f" / Draw Things connection was interrupted (the app may have crashed); "
                            f"it did not recover within {RECOVERY_TIMEOUT:.0f}s. Please restart Draw Things and retry.")
                    logger.info("DrawThings 已恢复，自动重试生成（第 %d/%d 轮）", retry, MAX_RECOVERY_RETRIES)
                    self._report(f"Draw Things 已恢复，自动重试生成（第 {retry}/{MAX_RECOVERY_RETRIES} 轮）")
                    await asyncio.sleep(RECOVERY_SETTLE)  # 刚重启的 app 先稳定几秒
                    try:
                        result = await self._with_cancel(_attempt())
                        break
                    except GenerationCancelled:
                        raise
                    except Exception as e2:
                        last = e2
                        if not (_looks_disconnected(e2) or not await self._port_open()):
                            # 不是断连：普通生成错误，立即抛出（不再等待）
                            if isinstance(e2, RuntimeError):
                                raise
                            raise RuntimeError(
                                f"Draw Things 生成失败（恢复后自动重试）：{e2}"
                                f" / Draw Things generation failed (auto-retry after recovery): {e2}")
                else:
                    # 每轮重试都再次闪退 / 失败：如实报错
                    if isinstance(last, RuntimeError):
                        raise last
                    raise RuntimeError(
                        f"Draw Things 生成失败（恢复后自动重试 {MAX_RECOVERY_RETRIES} 轮仍失败）：{last}"
                        f" / Draw Things generation failed (still failing after {MAX_RECOVERY_RETRIES} auto-retries): {last}")

        if video:
            out = self.media_dir / f"gen_{uuid.uuid4().hex[:8]}.mp4"
            assembled = False
            if ensure_ffmpeg_on_path():
                # 有 ffmpeg（系统或 imageio-ffmpeg 自带）：用 drawthings-py 合成（保留模型音轨）
                try:
                    result.to_video(str(out), fps=fps)
                    assembled = out.is_file()
                except Exception as e:
                    logger.warning("ffmpeg 合成视频失败，改试 OpenCV：%s", e)
            if not assembled:
                # 无 ffmpeg（或合成失败）：用 OpenCV 合成（纯视频，不含音频）
                assembled = _frames_to_video_cv2(result, out, fps)
            if not assembled or not out.is_file():
                raise RuntimeError(
                    f"视频合成失败：未找到可用的 ffmpeg，且 OpenCV 合成不可用（本次请求：{_info}）。"
                    "请安装 opencv-python（或 ffmpeg：brew install ffmpeg）后重试。"
                    " / Failed to assemble video: no usable ffmpeg and OpenCV unavailable "
                    f"(request: {_info}). Install opencv-python.")
            # 音画同步自检（仅 ffmpeg 合成含音轨时有意义；无 ffprobe 时内部自动跳过）
            self._remux_if_av_desynced(result, out, fps, model)
            return str(out)

        if not len(result):
            raise RuntimeError("Draw Things 未返回任何图像 / Draw Things returned no image")
        out = self.media_dir / f"gen_{uuid.uuid4().hex[:8]}.png"
        result[0].to_file(str(out))
        return str(out)


def norm_ref_flag(v) -> int | None:
    """功能级「支持参考图片」开关归一：''/None = 跟随配置（返回 None）；其余 → 0/1。"""
    if v in (None, ""):
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, int):
        return 1 if v else 0
    return 1 if str(v).strip().lower() in ("1", "true", "yes", "on") else 0


def build_drawthings_client(cfg, data_dir: Path,
                             model_image: str = "", model_video: str = "",
                             ref_image: int | None = None, ref_video: int | None = None,
                             max_steps_image: int = 0, max_steps_video: int = 0,
                             max_side: int = 0, max_seconds: int = 0) -> DrawThingsClient:
    """构造 Draw Things 客户端（仅 gRPC）。

    model_image / model_video：功能级模型覆盖（项目 / 微创作各自选模型）：
    留空 = 跟随 DrawThings 配置里的模型；`MODEL_NONE`（"不启用"）= 明确禁用该类型；
    其余 = 指定模型。
    ref_image / ref_video：功能级「支持参考图片」覆盖；None = 跟随配置，0/1 = 显式关/开。
    max_steps_image / max_steps_video：功能级最大 Step 数（随所选模型一起配，两者分开）；
    0 = 跟随预设自带步数。
    max_side / max_seconds：功能级最大分辨率（最长边，图像与视频都限幅）/ 视频秒数上限；
    0 = 不限 / 用内置上限。**已从 DrawThings 连接配置移到功能级**（不同项目 / 作品需求差别很大）。
    """
    c = DrawThingsClient(cfg, data_dir)
    # 「不启用」要能覆盖配置里的模型，故与「留空=跟随」分开判断
    for attr, ov in (("model_image", model_image), ("model_video", model_video)):
        if ov == MODEL_NONE:
            setattr(c, attr, "")
        elif ov:
            setattr(c, attr, str(ov).strip())
    if ref_image is not None:
        c.ref_image = bool(ref_image)
    if ref_video is not None:
        c.ref_video = bool(ref_video)
    if max_steps_image > 0:
        c.max_steps_image = int(max_steps_image)
    if max_steps_video > 0:
        c.max_steps_video = int(max_steps_video)
    if max_side > 0:
        c.max_side = int(max_side)
    if max_seconds > 0:
        c.max_seconds = int(max_seconds)
    return c
