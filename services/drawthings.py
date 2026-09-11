"""Draw Things（Mac 本地出图/出视频）客户端。

Draw Things 提供两种 API 协议（均在 app 内开启），本客户端两种都支持，
协议字段均按 drawthingsai/Draw-Things-community 源码校准（2026-09）：

1) HTTP（A1111/SD-WebUI 兼容，推荐，图/视频都走这条）
   - POST /sdapi/v1/txt2img   文生图
   - POST /sdapi/v1/img2img   图生图 / 视频（带 init_images 时）
   - GET  /  或 /sdapi/v1/options   返回 app 当前参数集（含 width/height）
   - 请求 JSON 只接受以下字段（未知字段直接 422 拒绝）：
       prompt, negative_prompt, model, width, height, steps,
       guidance_scale(=cfg_scale), seed, sampler, batch_count(=n_iter), batch_size,
       strength(=denoising_strength), mask_blur, image_guidance, restore_faces,
       init_images([base64 原图字节]),
       视频: num_frames, fps, motion_scale, guiding_frame_noise, start_frame_guidance,
             stage_2_steps, stage_2_guidance, stage_2_shift,
             compression_artifacts(disabled/h264/h265/jpeg), compression_artifacts_quality
   - sampler 取枚举缩写：\"DPM++ 2M Karras\" / \"Euler a\" / \"DDIM\" / \"UniPC\" / \"LCM\" …
   - 响应：{\"images\": [\"<base64 原始字节>\", ...]}
   - 注意：img2img 的 init_images 尺寸必须与 width/height 完全一致，否则 422

2) gRPC（ImageGenerationService，适合远程 GPU 服务器）
   - 默认 7859 端口，TLS 默认开（信任 drawthings_proto/root_ca.crt），认证 Echo+sharedSecret
   - ImageGenerationRequest.configuration 为 FlatBuffer（GenerationConfiguration，
     drawthings_proto/config.fbs 生成）
   - 参考图走内容寻址：先 UploadFile（InitUploadRequest → FileChunk 流），
     再把 sha256 摘要字节传给 image 字段
   - 响应为流：generatedImages 分块（chunkState MORE_CHUNKS/LAST_CHUNK）需拼接

策略：默认只发 prompt（+参考图），其余参数留空 = 用 app 当前选中的设置；
调用方通过 params 显式传入时才覆盖（width/height/steps/sampler/视频参数…）。
"""
import base64
import hashlib
import io
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from models import DrawThingConfig
from services.agent import make_httpx_client

# HTTP sampler 缩写 → FlatBuffer SamplerType 属性名（gRPC 模式用）
_SAMPLER_ATTR = {
    "DPM++ 2M Karras": "DPMPP2MKarras",
    "Euler a": "EulerA",
    "DDIM": "DDIM",
    "PLMS": "PLMS",
    "DPM++ SDE Karras": "DPMPPSDEKarras",
    "UniPC": "UniPC",
    "LCM": "LCM",
    "Euler A Substep": "EulerASubstep",
    "DPM++ SDE Substep": "DPMPPSDESubstep",
    "TCD": "TCD",
    "TCD Trailing": "TCDTrailing",
    "Euler A Trailing": "EulerATrailing",
    "DPM++ SDE Trailing": "DPMPPSDETrailing",
    "DPM++ 2M AYS": "DPMPP2MAYS",
    "Euler A AYS": "EulerAAYS",
    "DPM++ SDE AYS": "DPMPPSDEAYS",
    "DPM++ 2M Trailing": "DPMPP2MTrailing",
    "DDIM Trailing": "DDIMTrailing",
    "UniPC Trailing": "UniPCTrailing",
    "UniPC AYS": "UniPCAYS",
}

# HTTP compression_artifacts 值 → FlatBuffer CompressionMethod 属性名
_COMP_ATTR = {"disabled": "Disabled", "h264": "H264", "h265": "H265", "jpeg": "Jpeg"}

# 进程级共享连接（客户端按请求创建/销毁，共享底层连接避免每次请求新开 socket 泄漏）
import threading

# 两个共享实例：本地回环直连 / 云端走系统代理（系统代理常把 127.* 转到远端 → 502）
_shared_http: dict = {}
_shared_http_lock = threading.Lock()
_grpc_stubs: dict = {}
_grpc_lock = threading.Lock()


def _http_client(base_url: str = "") -> httpx.Client:
    host = (urlparse(base_url or "").hostname or "").lower()
    is_local = host in ("127.0.0.1", "localhost", "0.0.0.0", "::1")
    if is_local not in _shared_http:
        with _shared_http_lock:
            if is_local not in _shared_http:
                _shared_http[is_local] = make_httpx_client(base_url, timeout=600.0)
    return _shared_http[is_local]


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
        self.protocol = (getattr(cfg, "protocol", None) or "http").lower()
        self.model_name = getattr(cfg, "model_name", None) or ""
        self.shared_secret = getattr(cfg, "shared_secret", None) or ""
        self.media_dir = Path(data_dir) / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- 对外接口 ----------------
    def generate_image(self, prompt: str, ref_path: str | None = None,
                       params: dict | None = None) -> str:
        """返回生成图片的绝对路径。ref_path 为空/None = 文生图，否则图生图（参考上一章）。"""
        params = params or {}
        if self.protocol == "grpc":
            return self._grpc_media(prompt, params, video=False, ref_path=ref_path)
        return self._http_media(prompt, params, video=False, ref_path=ref_path)

    def generate_video(self, prompt: str, ref_video_path: str | None = None,
                       params: dict | None = None) -> str:
        """返回生成视频的绝对路径。
        ref_video_path = 上一章视频：先抽末帧作为 img2img 参考图（短剧帧连续的关键）。"""
        params = params or {}
        ref_frame = self._extract_last_frame(ref_video_path) if ref_video_path else None
        if self.protocol == "grpc":
            return self._grpc_media(prompt, params, video=True, ref_path=ref_frame)
        return self._http_media(prompt, params, video=True, ref_path=ref_frame)

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
        if self.model_name:
            payload["model"] = self.model_name

        if ref_path:
            # init_images 尺寸必须与 width/height 完全一致（否则 422）：
            # 优先调用方显式宽高 → app 当前宽高 → 参考图自身尺寸
            if "width" in params and "height" in params:
                w, h = int(params["width"]), int(params["height"])
            else:
                w, h = self._ref_size(ref_path, self._http_options())
            payload["init_images"] = [self._ref_to_b64(ref_path, w, h)]
            if "strength" in params:
                payload["strength"] = float(params["strength"])

        # 其余参数只发调用方显式给出的（避免 "Unrecognized keys" 422）
        for k in ("width", "height", "steps", "seed", "batch_size", "sampler",
                  "negative_prompt", "model"):
            if k in params:
                payload[k] = params[k]
        if "guidance_scale" in params:
            payload["guidance_scale"] = float(params["guidance_scale"])
        if "cfg_scale" in params:
            payload["cfg_scale"] = float(params["cfg_scale"])
        if video:
            for k in ("num_frames", "fps", "motion_scale", "stage_2_steps"):
                if k in params:
                    payload[k] = int(params[k])
            for k in ("guiding_frame_noise", "start_frame_guidance",
                      "stage_2_guidance", "stage_2_shift"):
                if k in params:
                    payload[k] = float(params[k])
            if params.get("compression_artifacts"):
                payload["compression_artifacts"] = str(params["compression_artifacts"])

        route = "img2img" if ref_path else "txt2img"
        resp = _http_client(self.base).post(f"{self.base}/sdapi/v1/{route}", json=payload)
        data = self._parse_http_media(resp)
        return self._save_bytes(data, ".mp4" if video else ".png")

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

    # ---------------- gRPC 模式 ----------------
    def _grpc_target(self) -> tuple[str, int]:
        t = self.base
        for prefix in ("http://", "https://", "grpc://", "grpcs://"):
            if t.startswith(prefix):
                t = t[len(prefix):]
        t = t.split("/")[0]
        if ":" in t:
            host, port = t.rsplit(":", 1)
            return host, int(port)
        return t, 7859

    def _grpc_stub(self):
        """返回 ImageGenerationService stub（进程级按 host:port 缓存，channel 线程安全可复用）。"""
        host, port = self._grpc_target()
        with _grpc_lock:
            stub = _grpc_stubs.get((host, port))
            if stub is None:
                stub = self._build_grpc_stub(host, port)
                _grpc_stubs[(host, port)] = stub
            return stub

    @staticmethod
    def _build_grpc_stub(host: str, port: int):
        """建立 gRPC 连接（TLS 优先，失败降级明文）。"""
        import grpc
        from drawthings_proto import imageService_pb2_grpc
        ca_path = Path(__file__).resolve().parent.parent / "drawthings_proto" / "root_ca.crt"
        channel = None
        if ca_path.is_file():
            # app 的 gRPC 默认开 TLS；信任官方根 CA（本地证书主机名不一定匹配，逐个试）
            creds = grpc.ssl_channel_credentials(root_certificates=ca_path.read_bytes())
            for target_name in ("localhost", "127.0.0.1"):
                try:
                    ch = grpc.secure_channel(
                        f"{host}:{port}", creds,
                        options=[("grpc.ssl_target_name_override", target_name)])
                    grpc.channel_ready_future(ch).result(timeout=5)
                    channel = ch
                    break
                except Exception:
                    ch.close()
        if channel is None:
            try:
                ch = grpc.insecure_channel(f"{host}:{port}")
                grpc.channel_ready_future(ch).result(timeout=5)
                channel = ch
            except Exception as e:
                raise RuntimeError(
                    f"无法连接 Draw Things gRPC 服务 {host}:{port}（已试 TLS 与明文）。"
                    f"请确认 app 已开启 gRPC 服务：{e}")
        return imageService_pb2_grpc.ImageGenerationServiceStub(channel)

    def _grpc_echo(self, stub) -> None:
        """Echo 认证：验证连通与共享密钥。"""
        from drawthings_proto import imageService_pb2
        req = imageService_pb2.EchoRequest(name="agentMvPro")
        if self.shared_secret:
            req.shared_secret = self.shared_secret
        try:
            reply = stub.Echo(req, timeout=15)
        except Exception as e:
            raise RuntimeError(f"gRPC Echo 认证失败：{e}")
        if reply.shared_secret_missing and not self.shared_secret:
            raise RuntimeError("Draw Things gRPC 服务设置了共享密钥，请在配置里填写「共享密钥」")

    def _grpc_upload_ref(self, stub, ref_path: str) -> bytes:
        """参考图 → 内容寻址上传（InitUploadRequest + FileChunk 流）→ 返回 sha256 摘要字节。"""
        from drawthings_proto import imageService_pb2
        data = Path(ref_path).read_bytes()
        sha = hashlib.sha256(data).digest()
        try:
            q = imageService_pb2.FileListRequest(files_with_hash=[sha])
            if self.shared_secret:
                q.shared_secret = self.shared_secret
            r = stub.FilesExist(q, timeout=15)
            if r.existences and r.existences[0]:
                return sha  # 服务端已有该文件
        except Exception:
            pass
        name = Path(ref_path).name
        chunk = 256 * 1024

        def gen():
            m = imageService_pb2.FileUploadRequest()
            m.init_request.filename = name
            m.init_request.sha256 = sha
            m.init_request.total_size = len(data)
            if self.shared_secret:
                m.shared_secret = self.shared_secret
            yield m
            for i in range(0, len(data), chunk):
                c = imageService_pb2.FileUploadRequest()
                c.chunk.content = data[i:i + chunk]
                c.chunk.filename = name
                c.chunk.offset = i
                if self.shared_secret:
                    c.shared_secret = self.shared_secret
                yield c

        for _ in stub.UploadFile(gen(), timeout=600):
            pass
        return sha

    def _grpc_config(self, params: dict, video: bool) -> bytes:
        """构建 GenerationConfiguration FlatBuffer（ImageGenerationRequest.configuration）。
        只写调用方显式给出的参数；未给出 = 用服务端/app 当前值。"""
        import flatbuffers
        from drawthings_proto import GenerationConfiguration as GC
        from drawthings_proto.SamplerType import SamplerType
        from drawthings_proto.CompressionMethod import CompressionMethod as Comp

        b = flatbuffers.Builder(512)
        # 字符串必须先于 StartObject 创建（flatbuffers 25.x 限制嵌套）
        model_off = b.CreateString(self.model_name) if self.model_name else None
        GC.Start(b)
        if model_off is not None:
            GC.AddModel(b, model_off)
        if "width" in params:
            GC.AddStartWidth(b, max(2, int(params["width"]) // 64))   # fbs 以 64px 为单位
        if "height" in params:
            GC.AddStartHeight(b, max(2, int(params["height"]) // 64))
        if "steps" in params:
            GC.AddSteps(b, int(params["steps"]))
        if "guidance_scale" in params or "cfg_scale" in params:
            GC.AddGuidanceScale(b, float(params.get("guidance_scale", params.get("cfg_scale"))))
        if params.get("seed") is not None and int(params["seed"]) >= 0:
            GC.AddSeed(b, int(params["seed"]) & 0xFFFFFFFF)
        sampler = params.get("sampler")
        if sampler in _SAMPLER_ATTR:
            GC.AddSampler(b, getattr(SamplerType, _SAMPLER_ATTR[sampler]))
        GC.AddBatchCount(b, 1)
        if video:
            if "num_frames" in params:
                GC.AddNumFrames(b, int(params["num_frames"]))
            if "fps" in params:
                GC.AddFpsId(b, int(params["fps"]))
            if "motion_scale" in params:
                GC.AddMotionBucketId(b, int(params["motion_scale"]))
            if "stage_2_steps" in params:
                GC.AddStage2Steps(b, int(params["stage_2_steps"]))
            if "stage_2_guidance" in params:
                GC.AddStage2Cfg(b, float(params["stage_2_guidance"]))
            if "stage_2_shift" in params:
                GC.AddStage2Shift(b, float(params["stage_2_shift"]))
            comp = params.get("compression_artifacts")
            if comp in _COMP_ATTR:
                GC.AddCompressionArtifacts(b, getattr(Comp, _COMP_ATTR[comp]))
        off = GC.End(b)
        b.Finish(off)
        return bytes(b.Output())

    def _grpc_media(self, prompt: str, params: dict, video: bool,
                    ref_path: str | None) -> str:
        from drawthings_proto import imageService_pb2
        stub = self._grpc_stub()
        self._grpc_echo(stub)
        req = imageService_pb2.ImageGenerationRequest(
            prompt=prompt,
            negative_prompt=str(params.get("negative_prompt") or ""),
            configuration=self._grpc_config(params, video),
            image=self._grpc_upload_ref(stub, ref_path) if ref_path else b"",
            user="agentMvPro",
        )
        out = bytearray()
        for resp in stub.GenerateImage(req, timeout=3600):
            if resp.remote_download:
                raise RuntimeError("服务端要求远程下载（远程 GPU 模式），暂不支持")
            if resp.generated_images:
                out.extend(resp.generated_images)
        if not out:
            raise RuntimeError("gRPC 未返回图/视频数据，请确认 app 已加载模型且服务在运行")
        return self._save_bytes(bytes(out), ".mp4" if video else ".png")
