"""Draw Things（Mac 本地出图/出视频）客户端。

使用 app 内置的 HTTP API（A1111/SD-WebUI 兼容，图/视频都走这条），
协议字段均按 drawthingsai/Draw-Things-community 源码校准（2026-09）：

- POST /sdapi/v1/txt2img   文生图
- POST /sdapi/v1/img2img   图生图 / 视频（带 init_images 时）
- GET  /  或 /sdapi/v1/options   返回 app 当前参数集（含 width/height）
- 请求 JSON 只接受以下字段（未知字段直接 422 拒绝）：
    prompt, negative_prompt, model, width, height, steps,
    guidance_scale(=cfg_scale), seed, sampler, batch_count(=n_iter), batch_size,
    strength(=denoising_strength), mask_blur, image_guidance, restore_faces,
    init_images([base64 原图字节]),
    视频: num_frames, motion_scale, guiding_frame_noise, start_frame_guidance,
          stage_2_steps, stage_2_guidance, stage_2_shift,
          compression_artifacts(disabled/h264/h265/jpeg), compression_artifacts_quality
- sampler 取枚举缩写：\"DPM++ 2M Karras\" / \"Euler a\" / \"DDIM\" / \"UniPC\" / \"LCM\" …
- 响应：{\"images\": [\"<base64 原始字节>\", ...]}
- 注意：img2img 的 init_images 尺寸必须与 width/height 完全一致，否则 422

> 不再支持 gRPC（ImageGenerationService）：app 的 gRPC 服务收到生成请求会闪退，
> 仅保留 HTTP 一种协议（app 内只需开启 HTTP 服务器，端口以 app 显示为准）。

模型只能跟随 app 当前选择（API 不支持指定）。
图像分辨率由智能体按场景/用户要求决定（调用方 params 传 width/height），受配置
max_side（最大分辨率，仅最长边）约束：最长边超过上限时等比缩小（64 的倍数）；
未指定分辨率时跟随 app 当前值，若 app 值超过上限同样限幅。
视频分辨率由 app/视频模型决定（模型专属，不发送 width/height）。
max_frames 为视频最大帧数上限：实际帧数 = min(app 当前帧数, 上限)。
"""
import base64
import io
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from models import DrawThingConfig
from services.agent import make_httpx_client

# 进程级共享连接（客户端按请求创建/销毁，共享底层连接避免每次请求新开 socket 泄漏）
import threading

# 两个共享实例：本地回环直连 / 云端走系统代理（系统代理常把 127.* 转到远端 → 502）
_shared_http: dict = {}
_shared_http_lock = threading.Lock()


def _http_client(base_url: str = "") -> httpx.Client:
    host = (urlparse(base_url or "").hostname or "").lower()
    is_local = host in ("127.0.0.1", "localhost", "0.0.0.0", "::1")
    if is_local not in _shared_http:
        with _shared_http_lock:
            if is_local not in _shared_http:
                _shared_http[is_local] = make_httpx_client(base_url, timeout=600.0)
    return _shared_http[is_local]


# 视频模型名关键词：子串匹配（无歧义）+ 词元匹配（易混短词，按 _/-/数字 切分后整词比较）
_VIDEO_SUBSTR = ("video", "svd", "i2v", "t2v", "cogvideo", "sora", "dynami", "kling", "vidu")
_VIDEO_TOKENS = {"wan", "animate", "motion", "animatediff"}


def is_video_model(model_name: str) -> bool:
    """按模型名判断是否视频模型（不确定时按图像）。"""
    name = (model_name or "").lower()
    if any(s in name for s in _VIDEO_SUBSTR):
        return True
    tokens = set(re.split(r"[^0-9a-z]+", name))
    return bool(tokens & _VIDEO_TOKENS)


def extract_last_frame(video_path: str, media_dir: Path) -> str | None:
    """上一章视频 → 末帧图（供下一章参考 / 喂多模态 LLM）。

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


class DrawThingsClient:
    def __init__(self, cfg: DrawThingConfig, data_dir: Path):
        self.base = (cfg.base_url or "").rstrip("/")
        # 个性化参数（0 = 不启用，跟随 app 当前值）
        self.max_side = int(getattr(cfg, "max_side", 0) or 0)
        self.max_frames = int(getattr(cfg, "max_frames", 0) or 0)
        self.media_dir = Path(data_dir) / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- 对外接口 ----------------
    def generate_image(self, prompt: str, ref_path: str | None = None,
                       params: dict | None = None) -> str:
        """返回生成图片的绝对路径。ref_path 为空/None = 文生图，否则图生图（参考上一章）。"""
        return self._http_media(prompt, params or {}, video=False, ref_path=ref_path)

    def generate_video(self, prompt: str, ref_video_path: str | None = None,
                       params: dict | None = None) -> str:
        """返回生成视频的绝对路径。
        ref_video_path = 上一章视频：先抽末帧作为 img2img 参考图（短剧帧连续的关键）。"""
        ref_frame = self._extract_last_frame(ref_video_path) if ref_video_path else None
        return self._http_media(prompt, params or {}, video=True, ref_path=ref_frame)

    def current_model(self) -> str:
        """app 当前加载的模型名（/sdapi/v1/options 的 model 字段）。取不到返回 ""。"""
        return str(self._http_options().get("model") or "")

    def detect_media_type(self) -> str:
        """根据 app 当前模型自动判断产出类型：video | image。"""
        return "video" if is_video_model(self.current_model()) else "image"

    # ---------------- HTTP 模式（A1111 兼容）----------------
    def _http_options(self) -> dict:
        """GET /sdapi/v1/options：返回 app 当前参数集（含 width/height/model 等）。失败返回 {}。"""
        for path in ("/sdapi/v1/options", "/"):
            try:
                r = _http_client(self.base).get(f"{self.base}{path}")
                if r.status_code == 200:
                    j = r.json()
                    if isinstance(j, dict) and ("width" in j or "prompt" in j):
                        return j
            except Exception:
                continue
        return {}

    def _http_media(self, prompt: str, params: dict, video: bool,
                    ref_path: str | None) -> str:
        payload: dict = {"prompt": prompt}
        if params.get("negative_prompt"):
            payload["negative_prompt"] = params["negative_prompt"]

        # 有效值 = 调用方显式 params > 配置个性化（0/空 = 不发，跟随 app）
        def eff(key: str):
            v = params.get(key)
            if v in (None, ""):
                v = getattr(self, key, 0)
            return v if v not in (None, "", 0) else None

        # 分辨率：图像 = 智能体决定（调用方 params）> app 当前值，再受 max_side 限幅；
        # 视频 = 分辨率由 app/视频模型决定（模型专属，不可任意指定）
        w = h = None
        if not video:
            w, h = eff("width"), eff("height")
            if not (w and h) and self.max_side > 0:
                o = self._http_options()
                w, h = int(o.get("width") or 0), int(o.get("height") or 0)
            if w and h:
                # 先限幅再同时用于 payload 与参考图缩放：二者尺寸必须完全一致，否则 img2img 422
                w, h = self._cap_size(int(w), int(h))
                payload["width"], payload["height"] = w, h
        for k in ("seed", "batch_size", "sampler"):
            v = eff(k)
            if v is not None:
                payload[k] = v
        if "cfg_scale" in params:
            payload["cfg_scale"] = float(params["cfg_scale"])
        if video:
            if params.get("num_frames") not in (None, ""):
                payload["num_frames"] = int(params["num_frames"])
            elif self.max_frames > 0:
                # 最大帧数上限：实际帧数 = min(app 当前帧数, 上限)；取不到 app 值直接用上限
                app_frames = int(self._http_options().get("num_frames") or 0)
                payload["num_frames"] = min(app_frames, self.max_frames) if app_frames else self.max_frames
            for k in ("motion_scale", "stage_2_steps"):
                if k in params:
                    payload[k] = int(params[k])
            for k in ("guiding_frame_noise", "start_frame_guidance",
                      "stage_2_guidance", "stage_2_shift"):
                if k in params:
                    payload[k] = float(params[k])
            if params.get("compression_artifacts"):
                payload["compression_artifacts"] = str(params["compression_artifacts"])

        if ref_path:
            # init_images 尺寸必须与 width/height 完全一致（否则 422）：
            # 已定宽高（调用方/配置）→ app 当前宽高 → 参考图自身尺寸
            if not (w and h):
                w, h = self._ref_size(ref_path, self._http_options())
            payload["init_images"] = [self._ref_to_b64(ref_path, w, h)]
            if "strength" in params:
                payload["strength"] = float(params["strength"])

        route = "img2img" if ref_path else "txt2img"
        resp = _http_client(self.base).post(f"{self.base}/sdapi/v1/{route}", json=payload)
        data = self._parse_http_media(resp)
        return self._save_bytes(data, ".mp4" if video else ".png")

    def _cap_size(self, w: int, h: int) -> tuple[int, int]:
        """最大分辨率约束：最长边超过 max_side 时等比缩小（取整到 64 的倍数，最小 64）。"""
        if not self.max_side or max(w, h) <= self.max_side:
            return w, h
        s = self.max_side / max(w, h)
        cap = lambda v: max(64, int(v * s / 64 + 0.5) * 64)
        w, h = cap(w), cap(h)
        if max(w, h) > self.max_side:  # 取整后仍超 → 最长边强制等于上限
            w = self.max_side if w >= h else w
            h = self.max_side if h > w else h
        return w, h

    def _ref_size(self, ref_path: str, options: dict) -> tuple[int, int]:
        """参考图应缩放到的 (w,h)：app 当前 width/height（>=128 有效），否则参考图自身尺寸。"""
        try:
            iw, ih = Image.open(ref_path).size
        except Exception:
            return 512, 512
        try:
            w, h = int(options.get("width") or 0), int(options.get("height") or 0)
            if w >= 128 and h >= 128:
                return w, h
        except Exception:
            pass
        return iw, ih

    @staticmethod
    def _ref_to_b64(path: str, w: int, h: int) -> str:
        """参考图 → 精确缩放到 (w,h) → PNG → base64（init_images 要求原图字节的 base64）。"""
        img = Image.open(path).convert("RGB").resize((w, h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def _parse_http_media(self, resp: httpx.Response) -> bytes:
        """从响应取第一个媒体的原始字节。
        成功：{\"images\": [\"<base64>\", ...]}；错误：422 + {\"error\",\"detail\",...}；
        个别版本可能直接返回文件字节流。"""
        if resp.status_code >= 400:
            raise RuntimeError(f"Draw Things HTTP 错误（status={resp.status_code}）：{resp.text[:500]}")
        body = resp.content
        try:
            j = resp.json()
        except Exception:
            return body  # 非 JSON（个别版本直接返回媒体字节流）
        if isinstance(j, dict):
            if j.get("error"):
                raise RuntimeError(
                    f"Draw Things 返回错误：{j.get('error')} {str(j.get('detail'))[:300]}")
            imgs = j.get("images") or []
            if imgs:
                first = imgs[0]
                if isinstance(first, str):
                    return base64.b64decode(first)
                if isinstance(first, (bytes, bytearray)):
                    return bytes(first)
            raise RuntimeError(f"Draw Things 响应未含 images：{str(j)[:300]}")
        return body

    def _extract_last_frame(self, video_path: str) -> str | None:
        return extract_last_frame(video_path, self.media_dir)

    def _save_bytes(self, data: bytes, default_ext: str) -> str:
        """媒体落盘；按 magic 字节推断扩展名（视频 compression_artifacts=h264 返回 mp4 流）。"""
        ext = default_ext
        if data[:4] == b"\x89PNG":
            ext = ".png"
        elif data[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        elif len(data) > 8 and data[4:8] == b"ftyp":
            ext = ".mp4"
        out = self.media_dir / f"gen_{uuid.uuid4().hex[:8]}{ext}"
        out.write_bytes(data)
        return str(out)
