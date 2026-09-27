# AGENTS.md — 开发约定（Drawthings Studio）

本文件是给后续开发者 / AI 助手的硬性约定，请先读再改。

## 项目形态
- 本地单用户应用：FastAPI（JSON + SSE）+ 免构建 Vue 3 / Element Plus SPA；SQLite + 本地媒体文件。
- 运行形态：BS（浏览器）/ CS（`python client.py`，pywebview 原生窗口）。

## 首要约定：漫画 / 短剧刻意分开，不要合并
- **漫画（comic）与短剧（drama）是两条独立、并行演进的业务线**，刻意保持重复，便于分开开发与维护。
- 以下重复是**设计选择，请勿去重 / 勿抽共享基座 / 勿加 `kind` 分支合并**：
  - 路由：`services/api_comic.py` ↔ `services/api_drama.py`（`/api/comics/*` ↔ `/api/dramas/*`）
  - 流水线：`services/pipeline_comic.py` ↔ `services/pipeline_drama.py`
  - 前端页面：`views/project-comic.js` ↔ `views/project-drama.js`、
    `chapter-card-comic` ↔ `chapter-card-drama`、`create-form-comic` ↔ `create-form-drama`
- **改一条线时，按需在另一条线做对应改动（对称维护）**，而不是合并成一份。
- 可以共享的只有**与类型无关的基础设施**：`api_common`、`events`、`media_files`、
  `capabilities`、`drawthings`、`agent`、`db`、`i18n`、`config_store`、`runtime` 等。
  不要往共享层塞类型特有逻辑。
- 微创作（`/micro`）是第三条独立的轻量线（`services/api_micro.py` + `micro_agent.py`）。

## 短剧术语（drama 专用）
- 短剧线把「章」称为 **片段（clip）**、把「季」称为 **集（episode）**；漫画线仍用「章 / 季」。
- 仅改**短剧线**的方法名 / 局部标识 / 前端组件与文案；**不改共享层**：
  - 模型 `Season` / `Chapter`、数据库表 `seasons` / `chapters`、列 `season_id`、路由 `/seasons/*`、`/chapters/*`、
    JSON 字段 `season_id` / `index`、SSE 事件名 `chapter` 等**全部保持不变**（漫画线共用）。
  - `services/pipeline.py` 门面按 `kind` 显式分发（comic 用 章/季 方法名，drama 用 片段/集 方法名），避免耦合。
- 文案：短剧专属字串放在 i18n 的 **`d.*`** 命名空间；漫画继续用 `p.*`，不要改共享 `p.*` 的值去适配短剧。

## 分层
- `main.py` 只做 app 装配、配置/健康检查/SPA 外壳；业务路由在 `services/api_*.py`。
- 业务逻辑在 `services/pipeline_*.py` / `services/micro_agent.py`；不要在路由里写业务。
- 长任务（生图/生视频）一律走 SSE：事件名以 `services/events.py` 为单一来源，
  前端镜像 `static/spa/js/events.js`，两处同步修改。

## 数据与配置
- 外部端点（LLM / DrawThings）配置一律在 UI（`/configs`）管理、存 SQLite；**不引入配置文件**。
- 数据库迁移写在 `db.py`（`ALTER TABLE ADD COLUMN` / `create_all`），启动幂等执行。
- 媒体文件存 `data/media/`，数据库只存路径引用。

## 前端
- 免构建：`static/spa/` 下用普通 `<script>` 加载的 UMD 全局组件（`window.Views`），改完即生效。
- 全局错误兜底在 `js/app.js`；有新的长任务请复用 `API.sse` 与事件契约。

## 打包
- `Drawthings Studio.spec` 是打包唯一依据；改打包参数改 spec，不要用额外 CLI 参数。
