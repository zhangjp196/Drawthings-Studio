# 漫剧坊

本地一体化应用：**FastAPI（JSON API + SSE）后端 + Vue 3 / Element Plus 单页前端（SPA）**。
前端为**本地 vendor、免构建**（Vue / vue-router / Element Plus 均下载至 `static/vendor/` 以 UMD 引入），
离线可用；FastAPI 兜底返回 SPA 外壳，支持任意路径刷新（history 路由）。

从**一句话**创意出发，走一条流水线生成**连续漫画**或**连续短剧**：

> 一句话 → 设定篇幅 → 整体路线（总纲）→ 章节设定 → 剧本编写 → 单任务进行

生成环节通过 OpenAI 协议走大模型，出图/出视频由 Mac 上的 **Draw Things** 生成。
因为支持图片，**生成下一章时参考上一张图（漫画）/ 上一视频末帧（短剧）**，保证画面连贯。

## 两个功能

| 功能 | 走向 | 产出 | 连续性参考 |
|------|------|------|------------|
| 漫画 | 连续生图 | 每章一张图 | 第 1 章 = 首图；其余 = 上一张图 |
| 短剧 | 连续出视频 | 每章一段视频 | 第 1 章 = 首图（首帧）；其余 = 上一视频末帧 |

## 首页（/）

- **品牌区**：平台名 + 一句话价值主张 + 主 CTA（进入创作中心 / ✨ 微创作）。
- **两大功能入口**：漫画走向（连续生图）/ 短剧走向（连续出视频），说明连续性参考策略。
- **流水线说明**：一句话主题 → 篇幅 → 总纲 → 章节 → 剧本 → 逐章生成，六步流程卡片。

## 我的创作（列表页 /projects）

- **管理**：表格行内可**打开 / 重命名 / 删除**（删除带二次确认）；项目页头部也有「删除该创作」。
  删除会连同全部章节记录与已生成的图/视频文件一并清理。
- **筛选**：按类型（漫画/短剧）、状态（中文标签）、排序（最新/最早）、每页条数（10/20/50）。
- **信息**：类型标签、标题/主题、状态标签、章节进度（已定/总篇幅）、首图缩略图（点击预览）、创建日期；
  工具条显示总数，「＋ 新建创作」弹框（与 /new 页同一表单组件）。

## 创作控制（项目页）

- **首图**：项目页可**上传首图**或**提示词生成首图**（留空提示词时由 LLM 按一句话创意+风格
  自动撰写）。首图作为第 1 章的参考（漫画 = img2img 参考图；短剧 = 视频首帧），
  并在剧本阶段作为全片角色/风格基准（多模态 LLM 可见）。
- **风格选择（支持自定义）**：新建项目时选预设风格（日系漫画风 / 国风水墨 / Q版 /
  写实电影感 / 皮克斯3D / 赛博朋克）或「自定义…」填任意描述；
  风格贯穿篇幅/总纲/章节/剧本各阶段，用户指定优先于 LLM 推荐。
- **整体路线控制**：篇幅确定后先生成**整体故事总纲**（开端→发展→高潮→结局）。
  总纲可在项目页直接**编辑**（改剧情走向），改完点「按总纲重新生成章节」重排章节；
  也可「重新生成总纲」。章节拆分严格遵循总纲节奏。

## 微创作（/micro，作品 → 独立会话）

不建项目的轻量创作台（顶栏「✨ 微创作」）——**一个微创作包含多个独立会话**，历史持久化：

- **三级结构**（与创作中心的 项目→章节 同款思路）：
  - `/micro` **作品列表页**：卡片网格（进入/删除），分页每页 10 条、按最近活跃排序，
    点「＋ 新建作品」弹框创建（作品标题/LLM/DrawThings）。
  - `/micro/{id}` 与 `/micro/{id}/{sid}` **同一作品页**（左右布局，无需分开；有会话时自动定位最近会话）：
    **进入作品自动打开最近更新的会话**（无会话时显示空状态）；
    **左侧**为该作品下的多个独立会话（进入/重命名/删除 +「＋ 新建会话」弹框，选中高亮），
    可**折叠/展开**（侧栏 « 收起，左缘竖排 tab 展开，状态按作品记住）；
    **右侧**为当前会话对话；作品选项弹框（作用于全部会话）+ 返回作品列表/删除作品，
    各会话历史与上下文相互独立。
- **对话历史落库**：用户消息、助手回复（含生成媒体与提示词）均存入 SQLite
  （`micro_works` / `micro_sessions` / `micro_messages`），刷新不丢；多轮上下文从数据库取最近 20 条。
  空标题的会话/作品自动取首条用户消息命名；旧库（配置随会话的旧结构）启动时自动迁移。
- **统一对话 + 流式输出（SSE）**：与所选 LLM 多轮对话（构思/编剧/提示词），回复逐字流式显示。
- **function call 自动生成**：需要出图/出视频时，由模型**自动调用 `generate_media`
  工具**（结合上下文提炼详细英文提示词），再调 DrawThings 产出单张图/单个视频，
  结果直接嵌进对话气泡（含提示词）。
- **用户附图**：所选 LLM 支持视觉（`supports_vision`）时，输入框可点 📎 上传、**粘贴**或**拖拽**
  图片（每条最多 4 张，可只发图不发文字）；附图随消息落库，并随多轮上下文回传给模型。
  非视觉模型不提供该入口。
- **Markdown 渲染**：助手回复按 Markdown 渲染（标题/嵌套列表/表格/代码块/引用等，
  先转义后转换，防 XSS）；代码块支持一键复制，宽表格横向滚动；打开会话自动定位最新消息。
- **作品选项**：LLM 配置 / DrawThings 配置（可不选 = 纯对话）；随作品保存
  （供其下全部会话共用），作品页可修改。
  产出类型（图像/视频）**无需选择**：每次生成时按 app 当前加载的模型自动判断
  （模型名含 svd/wan/i2v 等视频关键词 → 出视频，否则出图像）。
- **删除会话/作品**时同步清理其生成的媒体文件与用户附图。

后端统一使用 **Pydantic AI v2**（`services/agent.py`）：流水线各阶段（篇幅/分章/剧本）
走结构化输出（Pydantic 模型），微创作走流式 + 工具调用，全部 OpenAI 兼容协议。

## 配置字段

- **LLM 配置**：`supports_vision`（图片输入）= 支持图片输入（多模态，剧本阶段可参考
  上一帧/首图）/ 纯文本（不附带任何参考图）。
- **DrawThings 配置**：无模型类型（出图/出视频由 app 里当前加载的模型决定，
  所有项目/作品可选任意 DrawThings 配置）。
  个性化参数：`max_side`（最大分辨率，仅最长边，具体分辨率由智能体按场景决定、
  最长边超过上限时等比缩小）、`max_frames`（视频最大帧数上限，
  实际帧数 = min(app 当前帧数, 上限)）——**0 = 不限/跟随 app 当前值**。
  模型不能指定，永远跟随 app 当前选择。

## 目录结构

```
.
├── main.py              # FastAPI 入口 + /api 路由 + SSE + SPA 外壳兜底（按请求注入 db 会话）
├── config.py            # 仅保留数据目录位置（读环境变量 DATA_DIR，可选）
├── db.py                # SQLAlchemy 引擎 / 会话 / init_db（含旧库结构迁移）
├── models.py            # ORM 模型：LLMConfig / DrawThingConfig / Project / Chapter / MicroWork / MicroSession / MicroMessage
├── config_store.py      # 配置增删查（含删除前的“被项目/微创作作品引用”保护）
├── requirements.txt
├── services/
│   ├── agent.py         # Pydantic AI v2 统一 Agent 层（模型构造 / 结构化输出 / 消息历史）
│   ├── drawthings.py    # Draw Things 客户端（可配置；HTTP 协议）
│   └── pipeline.py      # 流水线编排（按项目所选 config 运行时构建 Agent）
├── static/
│   ├── vendor/          # 前端依赖（本地下载，免构建/离线）：vue / vue-router / element-plus（js+css+dark+zh-cn）/ icons
│   └── spa/             # 单页前端（UMD 引入，无打包）
│       ├── index.html   #   外壳：顶栏 + <router-view> + 主题预渲染
│       ├── css/app.css  #   应用样式（Element Plus 主题变量映射 + 布局 + 对话区）
│       └── js/          #   app.js（入口/路由）api.js（fetch+SSE）theme.js md.js（Markdown）views/（7 个视图组件）
└── data/                # app.db（SQLite） media/（图片/视频）
```

## 前端架构（Vue 3 + Element Plus，免构建）

- **技术栈**：Vue 3（组合式 API，UMD 全局构建）+ vue-router（history 模式）+ Element Plus 2.x
  （组件 / 暗色主题变量 / 中文 locale / 图标包），全部以 `<script>`/`<link>` 从 `static/vendor/` 本地加载，
  **无需 Node / 构建工具**，改 `static/spa/` 下文件即生效。
- **路由**：`/` 首页 · `/projects` 列表 · `/new` 新建 · `/project/:id` 详情 · `/configs` 配置 ·
  `/micro` 作品列表 · `/micro/:id(/:sid)` 作品对话。未知路径由 FastAPI 兜底返回 SPA 外壳，
  任意深链刷新可用（前端路由再匹配；未匹配重定向首页）。
- **数据**：所有页面经 `static/spa/js/api.js`（fetch 封装 + SSE 解析）调 `/api/*`；
  对话页用 fetch 流读 SSE（`POST /api/micro/{id}/{sid}/chat`），逐事件更新气泡/媒体/状态行。
- **组件**：列表用 `el-table`+筛选+`el-pagination`，弹窗用 `el-dialog`，删除用 `el-popconfirm`，
  步骤用 `el-steps`，首图/章节图用 `el-image`（teleported 预览），图片放大用 `el-image-viewer`，
  表单用 `el-form`/`el-select`/`el-radio-group`；提示统一 `ElMessage`。

## 配置与存储（本次重构重点）

- **配置搬到页面**：LLM / DrawThings 的端点、模型、API Key 等都在 **⚙ 配置管理**（`/configs`）
  页面里新增、选用、删除，**不再使用配置文件**。支持**多套配置**，建项目时下拉选择。
- **存储**：结构化数据（配置 / 项目 / 章节）走 **SQLite + SQLAlchemy ORM**；
  媒体文件（图/视频）仍存 `data/media/`，数据库只存路径引用。
- **列表页**：创作中心（/projects）支持**类型筛选 / 状态筛选 / 时间排序**、**分页**（10/20/50 每页）；
  微创作（/micro）按最近活跃分页（10 每页）。
- **安全删除**：删除配置前会检查是否仍被项目或微创作作品选用，避免变成“孤儿”无法运行。
- ⚠️ API Key 以**明文**存于本地 SQLite（本地单用户应用）；若部署为多用户服务请改用加密存储。

## 页面风格（light / dark / system）

- 顶栏右侧三键切换：**☀ 浅色 / ⚙ 系统 / ☾ 深色**；选择存入 `localStorage`，
  刷新/重开浏览器保持。`system` 时跟随系统 `prefers-color-scheme`（并实时响应切换）。
- 首屏前用内联脚本先落 `data-theme` 与 `html.dark`，**无白屏闪烁**；主题同时驱动
  Element Plus 暗色模式（`static/vendor/element-plus/dark.css` 变量）与应用自定义 CSS 变量，
  组件（表格/弹窗/标签/输入）与布局底色整体联动。

## 性能优化

- **SQLite WAL 模式**：读写不互斥，`synchronous=NORMAL` 减少 fsync，`busy_timeout` 抗锁竞争。
- **常用索引**：`chapters.project_id`、`projects.status/kind/created_at`。
- **响应压缩**：JSON/HTML/CSS/JS 走 GZip（`GZipMiddleware`，>500B 才压缩）。
- **静态缓存**：`/static/*`（含 vendor 依赖与 SPA 文件）带 `Cache-Control: max-age=3600`；
  SPA 外壳返回 `no-cache`（依赖路径变化即时生效）。
- **图片懒加载**：章节图 `loading="lazy"`；列表/作品页按需拉取 JSON，媒体不随列表下发。
- **本地响应**：API 均为本地调用，页面数据毫秒级返回。

## 为什么继续用 SQLite（而非换成 Postgres/MySQL）

本机单用户应用、数据量小（配置/项目/章节数百行级）、媒体在磁盘：
- SQLite 零运维、零网络跳，WAL 下并发读 + 串行写完全够用，**比 client-server 数据库更快**（少一次 socket 往返）。
- Postgres/MySQL 只在需要**多进程/多机写入、并发写、超大表、行级锁**时才值得引入——本应用用不到，
  反而多一个要维护的服务与连接管理。故保留 SQLite，并用 WAL + 索引把它的性能吃满。
- 将来若真要多用户服务化，再迁移 Postgres（SQLAlchemy 层已抽象，换 `DATABASE_URL` 即可）。

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 仅按需改数据目录
python main.py                # 访问 http://127.0.0.1:8010
```

> 若遇到 SSL 证书校验失败（macOS Python 常见），装包时加：
> `SSL_CERT_FILE=/etc/ssl/cert.pem pip install -r requirements.txt`

## 环境变量（见 .env.example）

> 配置项已迁到页面管理，环境变量仅保留数据目录。

| 变量 | 说明 | 默认 |
|------|------|------|
| `DATA_DIR` | 数据库与媒体文件存放目录 | `./data` |

## 运行前提（真实端点）

本应用直接调用真实服务，无内置模拟模式：

1. **LLM**：任意 OpenAI 协议端点（Ollama `http://127.0.0.1:11434/v1`、vLLM、云端 OpenAI 等），
   在 **⚙ 配置管理** 里新增配置；按模型能力勾选「图片输入」（多模态 / 纯文本）。
2. **Draw Things**：Mac 上运行 Draw Things app，开启 HTTP 服务器（见下节），
   在 **⚙ 配置管理** 里创建 DrawThings 配置（出图/出视频由 app 里加载的模型决定）。

端点未启动时，对应步骤会在项目页显示**友好错误提示**（如连接失败、未开启服务等），
修复后重新点该步骤即可，不影响已生成内容。

> 系统代理（Clash 等）：本地回环端点（127.0.0.1 / localhost）自动**直连、不走系统代理**
> （系统代理常把 127.* 转到远端导致 502）；云端 LLM 端点保留系统代理设置。

## 接入真实 Draw Things

使用 Draw Things app 内置 **HTTP API**（A1111 / SD-WebUI 兼容，图、视频都走这条），
协议字段已按 `drawthingsai/Draw-Things-community` 源码逐一对齐（2026-09）。

在 Draw Things app 内开启 HTTP 服务器，端口以 app 显示为准（如 `http://127.0.0.1:7860`）。端点：

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/sdapi/v1/txt2img` | 文生图 |
| POST | `/sdapi/v1/img2img` | 图生图 / 视频（带 `init_images`） |
| GET  | `/`、`/sdapi/v1/options` | 返回 app 当前参数集（含 `width`/`height`） |

- 请求只接受以下字段（**未知字段直接 422 拒绝**）：
  `prompt, negative_prompt, model, width, height, steps, guidance_scale(=cfg_scale),
  seed, sampler, batch_count(=n_iter), batch_size, strength(=denoising_strength),
  restore_faces, init_images([base64 原图字节])`，
  视频另加 `num_frames, motion_scale, guiding_frame_noise, start_frame_guidance,
  stage_2_steps, stage_2_guidance, stage_2_shift, compression_artifacts(h264/h265/jpeg/disabled)`。
- `sampler` 取枚举缩写：`"DPM++ 2M Karras"` / `"Euler a"` / `"DDIM"` / `"UniPC"` / `"LCM"` …
- 响应：`{"images": ["<base64 原始字节>", ...]}`。
- **注意**：`img2img` 的 `init_images` 尺寸必须与 `width`/`height` **完全一致**，
  否则 422——本应用会自动读 `/sdapi/v1/options` 的当前宽高并把参考图缩放到该尺寸。

分辨率由**智能体在剧本阶段按场景构图决定**（`ScriptOut.width/height`，64 的倍数），
流水线生成时传给客户端；客户端按配置 `max_side`（最大分辨率，仅最长边）限幅
（超长边等比缩小到上限内）。微创作等无智能体决定的场景只发 `prompt`（+参考图），
其余参数留空 = 用 app 当前选中的设置；调用方也可通过 `params` 显式覆盖。

在 **⚙ 配置管理 → 新建配置 → DrawThings** 里填端点地址（`http://host:port`），
可按需设置：最大分辨率（仅最长边：不限 / 512 / 768 / 1024）、
最大帧数上限（视频，实际帧数 = min(app 当前帧数, 上限)）
（**0 = 跟随 app 当前值**）。模型不能指定，永远跟随 app 里当前选中的模型。

> 不使用 gRPC（ImageGenerationService）：app 的 gRPC 服务收到生成请求会闪退，
> 本应用已移除 gRPC 支持，app 内也请只开启 HTTP 服务器。
