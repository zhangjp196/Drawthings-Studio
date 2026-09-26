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
- 模型清单可从 app 读取（`get_models`，需 refresh_cache），生成前会校验模型已下载。

图像分辨率：调用方 params > 预设，受 max_side（最长边）限幅。
视频帧率：预设显式 fps > 按模型族推断（LTX 25 / Hunyuan 30 / SkyReels 24 / Wan 16）。
视频帧数：调用方 params > 预设，受 max_seconds（秒）上限与「8 秒硬上限」（fps × 8）双重约束。
音画同步自检：合成后用 ffprobe 校验音轨与视频是否等长，明显不等长（= 帧率取错）则按音轨反推帧率重封装。
`drawthings-py` 为懒加载：未安装时只有实际生成会报错。
"""
import asyncio
import logging
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

logger = logging.getLogger("drawthings")

DEFAULT_GRPC_PORT = 7859

# 视频模型名关键词：子串匹配（无歧义）+ 词元匹配（易混短词，按 _/-/数字 切分后整词比较）。
# 覆盖 Draw Things 常见视频模型：LTX-Video(ltx)、SVD、Wan、HunyuanVideo、CogVideoX、Mochi、
# FramePack、AnimateDiff、DynamiCrafter、EasyAnimate 等；新增模型时在此补充关键词即可。
_VIDEO_SUBSTR = (
    "video", "svd", "i2v", "t2v", "v2v", "img2vid", "vid2vid",
    "cogvideo", "sora", "kling", "vidu", "ltx", "mochi",
    "framepack", "dynamicrafter", "easyanimate", "hunyuanvideo", "seaweed",
)
_VIDEO_TOKENS = {"wan", "ltx", "animate", "animation", "motion", "animatediff", "framepack"}

# 单视频时长硬上限（秒）：上限帧数 = fps × 8（fps 由 video_fps(model) / 预设显式值确定）。
MAX_VIDEO_SECONDS = 8


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


def extract_last_frame(video_path: str, media_dir: Path) -> str | None:
    """上一段视频 → 末帧图（供下一段参考 / 喂多模态 LLM）。

    图片直接返回；GIF 用 PIL 取最后一帧；mp4/mov/webm 用 ffmpeg 抽末帧附近；
    无 ffmpeg 或失败返回 None（调用方降级为不附带参考帧）。
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
    if not shutil.which("ffmpeg"):
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
    """模型名归一化：去扩展名 / 量化精度 / 末尾版本号，便于跨量化变体匹配预设。"""
    s = (model or "").lower()
    s = re.sub(r"\.(ckpt|safetensors)$", "", s)
    s = re.sub(r"[._-](q\d+p|i\d+x|f16|bf16|fp16|f8|q\d|i\d)(?=[._-]|$)", "", s)
    s = re.sub(r"[._-]v?\d+(\.\d+)*$", "", s)   # 末尾版本号，如 _1.1 / _2.3
    return s.strip("._-")


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
                key = _norm_model(model)
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
    hit = _preset_model_map().get(_norm_model(n))
    if hit:
        return hit
    low = n.lower()
    if "ltx" in low:
        return "ltx_2_3_dev" if "dev" in low else "ltx_2_3_distilled"
    if "wan" in low:
        return "wan_2_2_14b_i2v" if "i2v" in low else "wan_2_2_14b_t2v"
    if "hunyuan" in low and "video" in low:
        return "hunyuan_video"
    return ""


def _model_files(mi) -> set[str]:
    """从 ModelsInfo 取基座模型文件名集合。"""
    out: set[str] = set()
    for m in getattr(mi, "models", []) or []:
        d = m if isinstance(m, dict) else getattr(m, "__dict__", {})
        f = str(d.get("file") or "")
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
        d = m if isinstance(m, dict) else getattr(m, "__dict__", {})
        f = str(d.get("file") or "")
        if not f:
            continue
        out.append({"file": f, "name": str(d.get("name") or ""),
                    "version": str(d.get("version") or ""), "video": is_video_model(f)})
    return out


def fetch_models(host: str, port: int, refresh: bool = True) -> list[dict]:
    """同步包装：gRPC 模型清单。"""
    return asyncio.run(_fetch_models_async(host, port, refresh))


class DrawThingsClient:
    """Draw Things gRPC 客户端（出图 / 出视频）。"""

    def __init__(self, cfg, data_dir: Path):
        self.host, self.port = parse_endpoint(getattr(cfg, "base_url", ""))
        self.model_image = str(getattr(cfg, "model_image", "") or "").strip()
        self.model_video = str(getattr(cfg, "model_video", "") or "").strip()
        self.max_side = int(getattr(cfg, "max_side", 0) or 0)
        self.max_seconds = int(getattr(cfg, "max_seconds", 0) or 0)
        self.ref_image = bool(getattr(cfg, "ref_image", 0))   # 图像模型支持参考图片（图生图）
        self.ref_video = bool(getattr(cfg, "ref_video", 0))   # 视频模型支持参考图片（图生视频）
        self.media_dir = Path(data_dir) / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)

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
        开启「支持参考图片」（ref_image）且 ref_path 非空 = 图生图（参考上一章）；未开启则忽略参考图，按文生图。"""
        if not self.supports_image_ref():
            ref_path = None
        return asyncio.run(self._generate(prompt, video=False, ref_path=ref_path, params=params or {}))

    def generate_video(self, prompt: str, ref_video_path: str | None = None,
                       params: dict | None = None) -> str:
        """返回生成视频的绝对路径。
        开启「支持参考图片」（ref_video）时 ref_video_path = 上一章视频，先抽末帧作为参考（短剧帧连续的关键）；
        未开启则忽略参考图，按文生视频（不抽帧、不传 init_image）。"""
        ref_frame = None
        if ref_video_path and self.supports_video_ref():
            ref_frame = extract_last_frame(ref_video_path, self.media_dir)
        return asyncio.run(self._generate(prompt, video=True, ref_path=ref_frame, params=params or {}))

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
    def _gen_config(self, model: str):
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
        if model:
            cfg["model"] = model
        return cfg

    async def _generate(self, prompt: str, video: bool, ref_path: str | None, params: dict) -> str:
        from drawthings_py import DrawThings, RequestBuilder
        model = self._model_for(video)
        if not model:
            kind = "视频" if video else "图像"
            raise RuntimeError(
                f"未配置{kind}模型：请在 DrawThings 配置里填写{kind}模型文件名。"
                f" / No {kind} model configured.")
        cfg = self._gen_config(model)
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
            if w and h and self.max_side:
                w, h = cap_size(w, h, self.max_side)
            if w and h:
                cfg["width"], cfg["height"] = w, h
        else:
            # 时长（秒）：调用方/LLM 指定 > 配置上限（0=不限→内置上限）> 内置上限；
            # 三者都受「内置 8 秒上限」约束；帧数 = min(秒数 × 帧率, 预设帧数)。
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
            try:
                preset_frames = int(cfg["num_frames"] or 0)
            except Exception:
                preset_frames = 0
            if preset_frames > 0:
                frames = min(frames, snap_frames(preset_frames, step, "floor"))
            if params.get("num_frames"):
                frames = int(params["num_frames"])
            hard = max(1, int(fps * MAX_VIDEO_SECONDS))
            if frames > hard:
                frames = snap_frames(hard, step, "floor")   # 硬上限以下的最大合法帧数
            frames = min(frames, hard)
            cfg["num_frames"] = max(1, frames)
            # 视频分辨率也受 max_side 限幅（可显著降低显存/耗时；0 = 用预设尺寸）
            if self.max_side:
                try:
                    w, h = int(cfg["width"]), int(cfg["height"])
                    w, h = cap_size(w, h, self.max_side)
                    cfg["width"], cfg["height"] = w, h
                except Exception:
                    pass

        req = RequestBuilder(cfg, prompt)
        if ref_path:
            try:
                req.init_image(ref_path)
            except Exception as e:
                raise RuntimeError(f"加载参考图失败：{e} / Failed to load reference image: {e}")

        svc = DrawThings.grpc(host=self.host, port=self.port,
                              progressbar=False, disable_messages=True)
        await svc.connect()
        try:
            # 同一连接内先校验模型已下载（避免因模型不存在而让 app 退出）
            try:
                files = _model_files(await svc.get_models(refresh_cache=True))
                if files and model not in files:
                    raise RuntimeError(
                        f"模型「{model}」不在 Draw Things 已下载列表中（可用：{', '.join(sorted(files))}）。"
                        "请改为已下载的模型文件名后重试。"
                        f" / Model '{model}' is not among the models downloaded in Draw Things.")
            except RuntimeError:
                raise
            except Exception:
                pass  # 取不到模型清单时不阻塞
            result = await svc.generate(req)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(
                f"Draw Things gRPC 生成失败：{e}。请确认 app 已开启 gRPC API server（端口 {self.port}），"
                f"且模型「{model}」确实存在。"
                f" / Draw Things gRPC generation failed: {e}")
        finally:
            try:
                await svc.close()
            except Exception:
                pass

        if video:
            out = self.media_dir / f"gen_{uuid.uuid4().hex[:8]}.mp4"
            try:
                result.to_video(str(out), fps=fps)
            except Exception as e:
                raise RuntimeError(f"视频合成失败：{e} / Failed to assemble video: {e}")
            if not out.is_file():
                raise RuntimeError("Draw Things 未返回可合成的视频帧 / No frames returned for video")
            # 音画同步自检：一体化模型的音轨应与视频等长，明显不等长 = 帧率取错 → 按音轨反推后重封装
            self._remux_if_av_desynced(result, out, fps, model)
            return str(out)

        if not len(result):
            raise RuntimeError("Draw Things 未返回任何图像 / Draw Things returned no image")
        out = self.media_dir / f"gen_{uuid.uuid4().hex[:8]}.png"
        result[0].to_file(str(out))
        return str(out)


def build_drawthings_client(cfg, data_dir: Path) -> DrawThingsClient:
    """构造 Draw Things 客户端（仅 gRPC）。"""
    return DrawThingsClient(cfg, data_dir)
