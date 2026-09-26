"""一体化应用：FastAPI JSON API + 本地 vendor 的 Vue3 / Element Plus SPA（无构建、离线可用）。

品牌：Drawthings Studio。
两个功能：
- 漫画走向（连续生图）
- 短剧走向（连续出视频，参考上一视频末帧）
统一走一条流水线：一句话 -> 设定篇幅 -> 章节设定 -> 剧本编写 -> 单任务进行。

配置（LLM / DrawThings）由用户在页面管理，存 SQLite；项目创建时选择所用配置。
前端：static/spa（Vue3 + Element Plus UMD 本地引用），/api/* 为 JSON 接口，
其余路径返回 SPA 外壳（前端路由接管）。
界面支持中文 / English（前端 I18N 切换 + 后端按 Accept-Language 本地化错误文案）。
"""
import os

# 关闭 pydantic 的第三方插件加载：logfire 的 pydantic 插件会调用 inspect.getsource()，
# 在 PyInstaller 冻结环境中取不到源码会抛 OSError 导致启动崩溃；本应用不使用 pydantic 插件。
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from paths import resource_root, env_int, APP_NAME
from config import data_dir
from db import engine, get_db, init_db
from i18n import L
from config_store import ConfigStore
from services.agent import make_httpx_client
from services.logging_setup import setup_logging
from services.api_common import (
    MEDIA_DIR, _lang, _json_body, _llm_view, _dt_view, _dt_gen_fields,
)
from services import api_comic, api_drama, api_micro, api_idea

setup_logging()  # 统一日志（LOG_LEVEL 控制；重复调用无副作用）

STATIC_ROOT = resource_root() / "static"

MAX_REQUEST_BODY = 64 * 1024 * 1024  # 请求体上限 64MB（容纳多张 base64 附图；超限直接拒绝）


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """启动/关闭钩子：启动打印关键信息；关闭释放数据库连接池（进程退出更干净）。"""
    logging.getLogger("drawthings").info(
        "Drawthings Studio 启动：data=%s host=%s port=%s",
        data_dir, os.getenv("HOST", "127.0.0.1"), os.getenv("PORT", "8010"))
    try:
        yield
    finally:
        try:
            engine.dispose()
        except Exception:
            pass
        logging.getLogger("drawthings").info("Drawthings Studio 已关闭")


app = FastAPI(title=APP_NAME, lifespan=_lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)  # HTML/CSS/JS 压缩，减少传输体积
app.include_router(api_comic.router)   # 漫画 API（/api/comics/*）
app.include_router(api_drama.router)   # 短剧 API（/api/dramas/*）
app.include_router(api_micro.router)   # 微创作 API（/api/micro/*）
app.include_router(api_idea.router)    # 新建创作 AI 生成标题/主题（/api/idea/*）

logger = logging.getLogger("drawthings")




@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    """参数校验失败：统一返回本地化的 JSON，避免前端拿到结构化 detail 数组无法展示。"""
    return JSONResponse(status_code=422,
                        content={"detail": L(_lang(request), "请求参数不合法",
                                             "Invalid request parameters")})


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception):
    """兜底：任何未捕获异常都返回 JSON（而非 HTML 纯文本），并记录堆栈便于排查。"""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500,
                        content={"detail": L(_lang(request), "服务器内部错误，请查看服务端日志",
                                             "Internal server error — check the server logs")})

app.mount("/static", StaticFiles(directory=str(STATIC_ROOT)), name="static")


@app.middleware("http")
async def _static_cache(request: Request, call_next):
    """静态资源缓存头：
    - vendor 依赖文件名固定、内容不变 → 一年强缓存 + immutable（不再发校验请求）
    - spa 源码常改 → no-cache（仍发条件请求，命中 304）
    - /media 媒体 URL 已带 ?v=mtime 版本号（覆盖同名文件即换 URL）→ 一年强缓存
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/vendor/") or path.startswith("/media/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/static/spa/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def _limit_request_body(request: Request, call_next):
    """请求体上限保护：Content-Length 超限直接 413，避免解析超大 JSON 撑爆内存。"""
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_REQUEST_BODY:
        return JSONResponse(
            status_code=413,
            content={"detail": L(_lang(request), "请求体过大（上限 64MB）",
                                 "Request body too large (64MB max)")})
    return await call_next(request)

init_db()  # 建表（幂等）
















# ---------------- 静态媒体 ----------------
@app.get("/media/{filename}")
def serve_media(filename: str):
    if "/" in filename or "\\" in filename or filename.startswith((".", "%")):
        raise HTTPException(status_code=404, detail="not found")
    path = MEDIA_DIR / filename
    if not path.is_file() or not path.resolve().is_relative_to(MEDIA_DIR.resolve()):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)




# ---------------- 配置管理（JSON API） ----------------
CFG_LIMITS = {"llm": 3, "drawthings": 9}  # 配置数量上限：LLM 最多 3 个，DrawThings 最多 9 个


@app.get("/api/choices")
def choices(db: Session = Depends(get_db)):
    """新建作品/创作时的可选项：全部 LLM + 全部 DrawThings 配置。"""
    cs = ConfigStore(db)
    return {"llm_configs": [_llm_view(c) for c in cs.list_llm()],
            "drawthing_configs": [_dt_view(c) for c in cs.list_drawthing()]}


@app.get("/api/configs")
def configs_list(db: Session = Depends(get_db)):
    """配置列表：一次返回 LLM + DrawThings 两组（页面无 tab，分区展示），附数量与上限。"""
    cs = ConfigStore(db)
    llms = cs.list_llm()
    dts = cs.list_drawthing()
    return {
        "llm_items": [_llm_view(c) for c in llms],
        "dt_items": [_dt_view(c) for c in dts],
        "llm_count": len(llms), "drawthing_count": len(dts),
        "llm_max": CFG_LIMITS["llm"], "dt_max": CFG_LIMITS["drawthings"],
    }


_DT_PRESETS_CACHE: list[dict] | None = None


@app.get("/api/dt-presets")
def dt_presets():
    """gRPC 可选预设（drawthings-py）：名称 + 默认模型 + 是否视频模型。未安装返回空列表。"""
    global _DT_PRESETS_CACHE
    if _DT_PRESETS_CACHE is not None:
        return {"presets": _DT_PRESETS_CACHE, "available": True}
    try:
        from drawthings_py import Configs
        from drawthings_py.configs.presets import Presets
        from services.drawthings import is_video_model
    except Exception:
        return {"presets": [], "available": False}
    items: list[dict] = []
    for p in Presets:
        name = str(p.value)
        model = ""
        try:
            model = str(Configs.from_preset(name)["model"] or "")
        except Exception:
            pass
        items.append({"name": name, "model": model, "video": is_video_model(model or name)})
    _DT_PRESETS_CACHE = items
    return {"presets": items, "available": True}


@app.get("/api/dt-models")
def dt_models(request: Request, base_url: str = "", refresh: int = 1):
    """连接 Draw Things gRPC 返回已下载的基座模型清单（供配置页下拉选择）。"""
    lang = _lang(request)
    if not base_url.strip():
        raise HTTPException(status_code=400,
                            detail=L(lang, "请先填写 gRPC 端点（host:port）",
                                     "Enter the gRPC endpoint (host:port) first"))
    try:
        from services.drawthings import parse_endpoint, fetch_models
        host, port = parse_endpoint(base_url)
        models = fetch_models(host, port, refresh=bool(refresh))
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"获取模型列表失败：{e}", f"Failed to fetch models: {e}"))
    return {"models": models}




@app.post("/api/configs")
async def config_create(request: Request, db: Session = Depends(get_db)):
    lang = _lang(request)
    body = await _json_body(request)
    config_type = str(body.get("config_type") or "")
    name = str(body.get("name") or "").strip()
    base_url = str(body.get("base_url") or "").strip()
    if not name or not base_url:
        raise HTTPException(status_code=400,
                            detail=L(lang, "名称和端点地址不能为空", "Name and endpoint URL are required"))
    thinking = str(body.get("thinking") or "default").lower()
    if thinking not in ("default", "yes", "no"):
        raise HTTPException(status_code=400,
                            detail=L(lang, "深度思考选项无效", "Invalid thinking option"))
    thinking_param = str(body.get("thinking_param") or "auto").lower()
    if thinking_param not in ("auto", "reasoning_effort", "enable_thinking"):
        raise HTTPException(status_code=400,
                            detail=L(lang, "思考参数选项无效", "Invalid thinking parameter option"))
    cs = ConfigStore(db)
    limit = CFG_LIMITS.get(config_type)
    if limit is not None:
        cur = len(cs.list_llm()) if config_type == "llm" else len(cs.list_drawthing())
        if cur >= limit:
            if config_type == "llm":
                msg = L(lang, f"VLM 配置已达上限（最多 {limit} 个），请先删除不再使用的配置",
                        f"VLM config limit reached ({limit} max) — delete an unused one first")
            else:
                msg = L(lang, f"DrawThings 配置最多 {limit} 个，请先删除现有配置",
                        f"Only {limit} DrawThings config allowed — delete the existing one first")
            raise HTTPException(status_code=400, detail=msg)
    try:
        if config_type == "llm":
            model = str(body.get("model") or "").strip()
            if not model:
                raise HTTPException(status_code=400,
                                    detail=L(lang, "VLM 配置需要模型名", "VLM config requires a model name"))
            cs.create_llm(name, base_url, str(body.get("api_key") or ""), model,
                          thinking=thinking, thinking_param=thinking_param)
        elif config_type == "drawthings":
            cs.create_drawthing(name, base_url, **_dt_gen_fields(body, lang))
        else:
            raise HTTPException(status_code=400,
                                detail=L(lang, "未知配置类型", "Unknown config type"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=L(lang, f"保存失败：{e}", f"Save failed: {e}"))
    db.commit()
    return {"ok": True}


@app.put("/api/configs/{config_type}/{config_id}")
async def config_update(request: Request, config_type: str, config_id: str, db: Session = Depends(get_db)):
    """编辑配置。API Key 留空 = 保持原值不变（列表接口不返回 Key）。"""
    lang = _lang(request)
    body = await _json_body(request)
    name = str(body.get("name") or "").strip()
    base_url = str(body.get("base_url") or "").strip()
    if not name or not base_url:
        raise HTTPException(status_code=400,
                            detail=L(lang, "名称和端点地址不能为空", "Name and endpoint URL are required"))
    cs = ConfigStore(db)
    try:
        if config_type == "llm":
            model = str(body.get("model") or "").strip()
            if not model:
                raise HTTPException(status_code=400,
                                    detail=L(lang, "VLM 配置需要模型名", "VLM config requires a model name"))
            thinking = str(body.get("thinking") or "default").lower()
            if thinking not in ("default", "yes", "no"):
                raise HTTPException(status_code=400,
                                    detail=L(lang, "深度思考选项无效", "Invalid thinking option"))
            thinking_param = str(body.get("thinking_param") or "auto").lower()
            if thinking_param not in ("auto", "reasoning_effort", "enable_thinking"):
                raise HTTPException(status_code=400,
                                    detail=L(lang, "思考参数选项无效", "Invalid thinking parameter option"))
            raw_key = body.get("api_key")
            updated = cs.update_llm(config_id, name=name, base_url=base_url,
                                    api_key=None if raw_key in (None, "") else str(raw_key),
                                    model=model,
                                    thinking=thinking, thinking_param=thinking_param)
        elif config_type == "drawthings":
            updated = cs.update_drawthing(config_id, name=name, base_url=base_url,
                                          **_dt_gen_fields(body, lang))
        else:
            raise HTTPException(status_code=400,
                                detail=L(lang, "未知配置类型", "Unknown config type"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=L(lang, f"保存失败：{e}", f"Save failed: {e}"))
    if not updated:
        raise HTTPException(status_code=404,
                            detail=L(lang, "配置不存在", "Config not found"))
    db.commit()
    return {"ok": True}


@app.get("/api/llm/models")
def llm_models(request: Request, base_url: str = "", api_key: str = "", config_id: str = "",
               db: Session = Depends(get_db)):
    """从 OpenAI 兼容端点的 /models 接口拉取可用模型 id 列表（新建/编辑 LLM 配置时自动填充）。

    编辑已有配置时前端传 config_id：表单未填地址/Key 时回退用库里已存值
    （密钥不在列表接口回传，编辑弹窗里是空的，须由服务端代填）。
    """
    lang = _lang(request)
    cfg = ConfigStore(db).get_llm(config_id) if config_id else None
    base_url = (base_url or "").strip() or (cfg.base_url if cfg else "")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400,
                            detail=L(lang, "端点地址需为 http(s) URL", "Endpoint must be an http(s) URL"))
    key = (api_key or "").strip() or (cfg.api_key if cfg else "")
    url = base_url.rstrip("/") + "/models"
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with make_httpx_client(base_url, timeout=15.0) as client:
            r = client.get(url, headers=headers)
        if r.status_code in (401, 403):
            detail = L(lang, "端点鉴权失败：请检查 API Key" if key else "端点需要鉴权（401）：请先填写 API Key",
                       "Endpoint authentication failed: check the API Key" if key
                       else "Endpoint requires auth (401): please fill in an API Key")
            raise HTTPException(status_code=400, detail=detail)
        if r.status_code != 200:
            raise HTTPException(status_code=400,
                                detail=L(lang, f"端点返回 {r.status_code}，请检查地址与 API Key",
                                         f"Endpoint returned {r.status_code}; check the URL and API Key"))
        data = r.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=L(lang, f"获取模型失败：{e}", f"Failed to fetch models: {e}"))
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []
    models: list[str] = []
    for it in items:
        mid = str(it.get("id") or it.get("name") or "").strip() if isinstance(it, dict) else str(it).strip()
        if mid and mid not in models:
            models.append(mid)
    return {"models": sorted(models)}


@app.post("/api/configs/{config_type}/{config_id}/delete")
def config_delete(request: Request, config_type: str, config_id: str, db: Session = Depends(get_db)):
    lang = _lang(request)
    cs = ConfigStore(db)
    if config_type in ("llm", "drawthings"):
        if cs.is_referenced(config_id):
            raise HTTPException(status_code=400,
                                detail=L(lang, "该配置仍被项目使用，无法删除",
                                         "This config is still used by a project and cannot be deleted"))
        ok = cs.delete_llm(config_id) if config_type == "llm" else cs.delete_drawthing(config_id)
    else:
        raise HTTPException(status_code=400, detail=L(lang, "未知配置类型", "Unknown config type"))
    if not ok:
        raise HTTPException(status_code=404,
                            detail=L(lang, "配置不存在", "Config not found"))
    cs.clear_default_ref(config_type, config_id)  # 删掉的配置若被设为默认 → 清空引用
    db.commit()
    return {"ok": True}


@app.get("/api/settings")
def settings_get(db: Session = Depends(get_db)):
    """基础配置：新建创作的默认 LLM / DrawThings 配置。"""
    return ConfigStore(db).get_settings()


@app.put("/api/settings")
async def settings_update(request: Request, db: Session = Depends(get_db)):
    """保存基础配置（白名单字段；引用不存在的配置 id 直接 400）。"""
    lang = _lang(request)
    body = await _json_body(request)
    cs = ConfigStore(db)
    llm_id = str(body.get("default_llm_config_id") or "").strip()
    dt_id = str(body.get("default_dt_config_id") or "").strip()
    if llm_id and not cs.get_llm(llm_id):
        raise HTTPException(status_code=400,
                            detail=L(lang, "默认 VLM 配置不存在", "Default VLM config not found"))
    if dt_id and not cs.get_drawthing(dt_id):
        raise HTTPException(status_code=400,
                            detail=L(lang, "默认 DrawThings 配置不存在", "Default DrawThings config not found"))
    return cs.update_settings({
        "default_llm_config_id": llm_id,
        "default_dt_config_id": dt_id,
    })



# ---------------- 应用健康检查（CS 桌面客户端探测用） ----------------
@app.get("/api/health")
def health():
    """健康检查：桌面客户端（CS）启动时轮询此端点判断本地服务是否就绪；亦可用于存活监控。"""
    return {"ok": True, "app": "drawthings-studio"}


# ---------------- SPA 外壳 ----------------
SPA_SHELL = STATIC_ROOT / "spa" / "index.html"


@app.get("/{full_path:path}")
def spa_shell(full_path: str):
    """非 /api、/static、/media 的路径一律返回 SPA 外壳（前端路由接管，刷新深链可用）。"""
    return FileResponse(SPA_SHELL, media_type="text/html", headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    import os as _os
    import uvicorn
    # CS 桌面客户端以 RELOAD=0 拉起本进程（生产模式）；开发时可用 RELOAD=1 python main.py。
    # 默认只绑 127.0.0.1（本地单用户，不对外；如需局域网访问可显式设 HOST=0.0.0.0）。
    uvicorn.run("main:app",
                host=_os.getenv("HOST", "127.0.0.1"),
                port=env_int("PORT", 8010),
                reload=_os.getenv("RELOAD", "0") == "1")
