"""Draw Things（Mac 本地出图/出视频）客户端 —— 仅 gRPC。

app 的 API server 设为 **gRPC**（默认端口 7859）；本应用只用这一种传输：
- gRPC 返回**帧序列**（`ImageGenerationResponse.generatedImages`），视频由客户端用 ffmpeg
  合成为 mp4（LTX 等还会带回音轨），因此**出图 / 出视频都支持**。
  （HTTP API 对视频模型只回单帧，已弃用并移除。）
- gRPC 请求必须自带完整生成配置（FlatBuffer）：用 `drawthings-py` 的**预设**（preset）提供
  steps / sampler / guidance / 尺寸等，再用本配置里的**图像模型 / 视频模型**覆盖 model
  （可只填其一 = 只支持该类型）。
- 模型清单可从 app 读取（`get_models`，需 refresh_cache），生成前会校验模型已下载。

图像分辨率：调用方 params > 预设，受 max_side（最长边）限幅。
视频帧数：调用方 params > 预设，受 max_seconds（秒）上限与「8 秒硬上限」（fps × 8）双重约束。
`drawthings-py` 为懒加载：未安装时只有实际生成会报错。
"""
import asyncio
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

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

# 单视频时长硬上限（秒）：上限帧数 = fps × 8（fps 取预设值；预设没有时回退 25）。
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


def infer_preset(model: str) -> str:
    """按模型文件名推断 drawthings-py 预设（仅覆盖常见视频模型；其它请显式选预设）。"""
    n = (model or "").lower()
    if "ltx" in n:
        return "ltx_2_3_dev" if "dev" in n else "ltx_2_3_distilled"
    if "wan" in n:
        return "wan_2_2_14b_i2v" if "i2v" in n else "wan_2_2_14b_t2v"
    if "hunyuan" in n and "video" in n:
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
        self.preset_image = str(getattr(cfg, "preset_image", "") or "").strip()
        self.preset_video = str(getattr(cfg, "preset_video", "") or "").strip()
        self.max_side = int(getattr(cfg, "max_side", 0) or 0)
        self.max_seconds = int(getattr(cfg, "max_seconds", 0) or 0)
        self.media_dir = Path(data_dir) / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- 能力 ----------------
    def supports_image(self) -> bool:
        return bool(self.model_image)

    def supports_video(self) -> bool:
        return bool(self.model_video)

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
        """返回生成图片的绝对路径。ref_path 为空/None = 文生图，否则图生图（参考上一章）。"""
        return asyncio.run(self._generate(prompt, video=False, ref_path=ref_path, params=params or {}))

    def generate_video(self, prompt: str, ref_video_path: str | None = None,
                       params: dict | None = None) -> str:
        """返回生成视频的绝对路径。
        ref_video_path = 上一章视频：先抽末帧作为参考（短剧帧连续的关键）。"""
        ref_frame = extract_last_frame(ref_video_path, self.media_dir) if ref_video_path else None
        return asyncio.run(self._generate(prompt, video=True, ref_path=ref_frame, params=params or {}))

    # ---------------- 内部 ----------------
    def _gen_config(self, model: str, preset: str = ""):
        try:
            from drawthings_py import Configs
        except Exception as e:  # 未安装 drawthings-py
            raise RuntimeError(
                "未安装 drawthings-py，无法使用 Draw Things。请 `pip install \"drawthings-py[ffmpeg]\"`。"
                f" / drawthings-py is not installed ({e})")
        preset = (preset or "").strip() or infer_preset(model)
        if not preset:
            raise RuntimeError(
                "Draw Things 配置需要选择「预设」来提供 steps/sampler 等生成参数；"
                "该模型无法自动推断，请在配置里手动选择。"
                " / A preset is required (cannot infer one for this model).")
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
        cfg = self._gen_config(model, self.preset_video if video else self.preset_image)
        # 尺寸：图片 = 调用方 params > 预设，再受 max_side 限幅；视频尺寸由预设/模型决定
        try:
            fps = int(cfg["fps"] or 0) or 25
        except Exception:
            fps = 25
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
            # 帧数 = 调用方 params > 预设，受 max_seconds（秒 × 帧率）与「8 秒硬上限」（fps×8）双重约束
            try:
                frames = int(cfg["num_frames"] or 0)
            except Exception:
                frames = 0
            if params.get("num_frames"):
                frames = int(params["num_frames"])
            if self.max_seconds > 0:
                sec_cap = max(1, int(self.max_seconds * fps))
                frames = min(frames, sec_cap) if frames > 0 else sec_cap
            hard = max(1, int(fps * MAX_VIDEO_SECONDS))
            frames = min(frames, hard) if frames > 0 else hard
            cfg["num_frames"] = frames
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
            return str(out)

        if not len(result):
            raise RuntimeError("Draw Things 未返回任何图像 / Draw Things returned no image")
        out = self.media_dir / f"gen_{uuid.uuid4().hex[:8]}.png"
        result[0].to_file(str(out))
        return str(out)


def build_drawthings_client(cfg, data_dir: Path) -> DrawThingsClient:
    """构造 Draw Things 客户端（仅 gRPC）。"""
    return DrawThingsClient(cfg, data_dir)
