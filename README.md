# Drawthings Studio

> 从一句话到连续漫画 / 短剧（图 + 视频），本地一体化创作台。
> From a single sentence to a full continuous comic / short drama (images + video) — a local, all-in-one creation studio.

本仓库包含一份**中文**文档与一份**英文**文档（内容一致，便于中英双语团队阅读）。

This repository ships a **Chinese** document and an **English** document (identical content, so both Chinese- and English-speaking teams can read it comfortably).

- [中文文档](#中文文档)
- [English Documentation](#english-documentation)

---

## 中文文档

### 简介

本地一体化应用：**FastAPI（JSON API + SSE）后端 + Vue 3 / Element Plus 单页前端（SPA）**。
前端为**本地 vendor、免构建**（Vue / vue-router / Element Plus 均下载至 `static/vendor/` 以 UMD 引入），
离线可用；FastAPI 兜底返回 SPA 外壳，支持任意路径刷新（history 路由）。

从**一句话**创意出发，走一条流水线生成**连续漫画**或**连续短剧**：

> 一句话 → 大纲（风格/角色/章节规划）→ 章节（按章剧本 + 出图/出视频）→ 完成（导出 ZIP/PDF）

生成环节通过 OpenAI 协议走大模型，出图 / 出视频由 Mac 上的 **Draw Things** 生成。
因为支持图片，**生成下一章时参考上一张图（漫画）/ 上一视频末帧（短剧）**，保证画面连贯。

界面**支持中文 / English 双语**，顶栏一键切换（前端 `I18N` 切换 + 后端按 `Accept-Language` 本地化错误文案）。

### 两个功能

| 功能 | 走向 | 产出 | 连续性参考 |
|------|------|------|------------|
| 漫画 | 连续生图 | 每章一张图 | 第 1 章 = 首图；其余 = 上一张图 |
| 短剧 | 连续出视频 | 每章一段视频 | 第 1 章 = 首图（首帧）；其余 = 上一视频末帧 |

### 首页（/）

- **品牌区**：平台名（Drawthings Studio）+ 一句话价值主张 + 主 CTA（进入创作中心 / ✨ 微创作）。
- **两大功能入口**：漫画走向（连续生图）/ 短剧走向（连续出视频），说明连续性参考策略。
- **流水线说明**：一句话主题 → 大纲（风格/角色/章节规划）→ 章节（按章剧本 + 出图/出视频）→ 完成（导出 ZIP/PDF）；项目页为三个页签（大纲 / 章节 / 完成）。

### 我的创作（列表页 /projects）

- **管理**：点击**标题**打开 / 重命名 / 删除（删除带二次确认）；项目页头部也有「删除该创作」。
  删除会连同全部章节记录与已生成的图/视频文件一并清理。
- **筛选**：关键词（标题/主题）、类型（漫画/短剧）、状态、排序（最新创建 / 最早创建 / 最近活跃）、每页条数（10/20/50）。
- **信息**：类型标签、标题/主题、状态标签、章节进度（已生成/总章节数）、首图缩略图（点击预览）、最近活跃（含创建日期）；
  工具条显示总数，「＋ 新建创作」弹框（与 /new 页同一表单组件）。

### 创作控制（项目页）

项目页用**三个页签**组织：**大纲 / 章节 / 完成**，可随时互相切换（章节页可一键「返回大纲」调整）。

- **首图**：项目页可**上传首图**或**提示词生成首图**（留空提示词时由 LLM 按一句话创意+风格自动撰写）。
  首图作为第 1 章的参考（漫画 = 图生图参考图；短剧 = 视频首帧），
  并在剧本阶段作为全片角色/风格基准（多模态 LLM 可见）。
- **风格选择（支持自定义）**：新建项目时选预设风格（日系漫画风 / 国风水墨 / Q版 /
  写实电影感 / 皮克斯3D / 赛博朋克）或「自定义…」填任意描述；
  风格写入大纲，供后续各章保持一致（用户指定优先于 LLM 推荐）。

- **① 大纲**：点「生成大纲」一次性规划 **风格 / 主题 / 基调 + 整体故事大纲（开端→发展→高潮→结局）+ 角色设定**，
  并按「章节数量」生成**每章标题 + 主题摘要**（章节规划）。
  大纲、角色设定、**默认分辨率**、章节规划（标题 / 摘要 / 数量）都可**手动编辑**后「保存大纲」；
  也可「重新生成大纲」（按当前设定重做大纲+角色+章节）或「按大纲重新生成章节」（仅重拆章节）。
  角色设定供后续各章保持一致（写进每章提示词）。
- **② 章节**：对大纲规划出的各章做生产。**一键生成**「全部生成剧本 / 全部生成画面 / 重新生成全部画面」（SSE 逐章进度）；
  每章手风琴卡片（默认**只显照片**，点击展开，一次只展开一章，生成后自动展开）内**按章多步**：
  「生成剧本」（写剧本/提示词/分辨率，沿用大纲风格+角色+该章摘要）→「生成画面」，
  还有 **重新生成画面 / 保存提示词 / 调整分辨率 / 上移 / 下移 / 删除**。
  漫画每章 = **一页多格漫画**（一张图多个分镜、竖版构图，画面带旁白/对白文字）；短剧每章 = 一段视频。
- **③ 完成**：生成完成后**导出 ZIP**（全部媒体 + 大纲/角色/各章剧本文本）与**导出 PDF**
  （漫画：各章图拼成多页；短剧为视频不支持 PDF），并点「完成」手动标记项目为「已完成」。
- **重新设定**：项目页头部可随时点「重新设定」，在弹框里调整**标题 / 一句话创意（主题）/ 风格**。
  默认**非破坏**——保留现有大纲、角色、章节与已生成媒体；
  也可勾选「清空并重排下游」，清空大纲/角色/章节/媒体并回到「大纲」阶段（相当于按新设定重开）。
- **长步骤逐章进度（SSE）**：「全部生成剧本 / 全部生成画面 / 重新生成全部画面」均为较长步骤
  （LLM 逐章撰写 / 逐章出图出视频，阻塞调用放入线程池、不卡事件循环），
  走 SSE 流式下发逐章进度（第 x/y 章《标题》），完成后自动刷新；单章仍可用卡片内按钮单独生成剧本/生成画面/重生成。

### 微创作（/micro，作品 → 独立会话）

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
  助手回复按**有序内容块**（`micro_messages.parts`：文本 / 生成 / 错误）落库，
  文本与生成结果严格保持发生顺序，一次回复可含多张图/视频；刷新后与流式过程完全一致。
  空标题的会话/作品自动取首条用户消息命名；旧库（配置随会话的旧结构）启动时自动迁移。
- **统一对话 + 流式输出（SSE）**：与所选 LLM 多轮对话（构思/编剧/提示词），回复逐字流式显示。
  生成块按事件 id 从「生成中（转圈）」收束为「✓ 已生成 / ⚠ 生成失败」，提示词默认折叠（点「提示词」展开），不再重复展示。
- **对话区交互**：消息区高度自适应视口（不再写死偏移）；打开会话 / 图片陆续加载时自动贴底，
  用户上翻后停止跟随并出现「↓ 回到底部」；会话侧栏可折叠（按作品记忆），切换会话即中断进行中的生成。
- **function call 自动生成**：需要出图/出视频时，由模型**自动调用 `generate_media` 工具**
  （结合上下文提炼详细英文提示词），再调 DrawThings 产出单张图/单个视频，
  结果直接嵌进对话气泡（含提示词）。
- **用户附图**：所选 LLM 支持视觉（`supports_vision`）时，输入框可点 📎 上传、**粘贴**或**拖拽**
  图片（每条最多 4 张，可只发图不发文字）；附图随消息落库，并随多轮上下文回传给模型。
  非视觉模型不提供该入口。
- **Markdown 渲染**：助手回复按 Markdown 渲染（标题/嵌套列表/表格/代码块/引用等，
  先转义后转换，防 XSS）；代码块支持一键复制，宽表格横向滚动；打开会话自动定位最新消息。
- **作品页签（图 / 视频二级 tab + 导出）**：作品页「作品」页签把该作品下**全部会话**生成的媒体用**二级 tab 切换「图片 / 视频」**，
  各 tab 支持 **单个 / 勾选 / 全部** 导出——图片可导出 **ZIP 或 PDF**（多页），视频导出 **ZIP**
  （ZIP 按导出顺序命名，分 `images/` 与 `videos/` 子目录）；仍可多选批量删除（连同消息与媒体文件）。
- **作品选项**：LLM 配置 / DrawThings 配置（可不选 = 纯对话）；随作品保存
  （供其下全部会话共用），作品页可修改。
  产出类型（图像/视频）**无需选择**：每次生成时按 app 当前加载的模型自动判断
  （模型名含 svd/wan/i2v 等视频关键词 → 出视频，否则出图像）。
- **删除会话/作品**时同步清理其生成的媒体文件与用户附图。

后端统一使用 **Pydantic AI v2**（`services/agent.py`）：流水线各阶段（大纲/分章/剧本）
走结构化输出（Pydantic 模型），微创作走流式 + 工具调用，全部 OpenAI 兼容协议。

### 配置字段

- **LLM 配置**：`supports_vision`（图片输入）= 支持图片输入（多模态，剧本阶段可参考上一帧/首图）/ 纯文本（不附带任何参考图）。
- **DrawThings 配置**（仅 gRPC，app 里 API server 设为 gRPC）：
  **图像模型 / 视频模型**（`model_image` / `model_video`，各可空、至少填一个；点「获取模型」从 app 读取已下载模型）。
  生成参数（预设）按模型名**自动推断**，无需填写。所有项目/作品可选任意 DrawThings 配置。
  个性化参数：`max_side`（最大分辨率，仅最长边，图片与视频都限幅）、
  `max_seconds`（视频最大时长，秒，**默认 8 = 内置上限**，0 = 用内置上限）——实际时长可由模型在生成时按用户要求决定（不超过上限）。
  **单视频时长硬上限 8 秒**：按 fps 换算成帧数（fps × 8）后强制限幅。

### 目录结构

```
.
├── main.py              # FastAPI 入口 + /api 路由 + SSE + SPA 外壳兜底（按请求注入 db 会话）
├── config.py            # 仅保留数据目录位置（读环境变量 DATA_DIR，可选）
├── db.py                # SQLAlchemy 引擎 / 会话 / init_db（含旧库结构迁移）
├── models.py            # ORM 模型：LLMConfig / DrawThingConfig / Project / Chapter / MicroWork / MicroSession / MicroMessage
├── config_store.py      # 配置增删查（含删除前的“被项目/微创作作品引用”保护）
├── i18n.py              # 后端中英文本地化（Accept-Language → zh|en + L() 文案助手）
├── requirements.txt
├── services/
│   ├── agent.py         # Pydantic AI v2 统一 Agent 层（模型构造 / 结构化输出 / 消息历史）
│   ├── drawthings.py    # Draw Things gRPC 客户端（出图 + 出视频，drawthings-py）+ 共享工具/工厂
│   └── pipeline.py      # 流水线编排（按项目所选 config 运行时构建 Agent）
├── static/
│   ├── vendor/          # 前端依赖（本地下载，免构建/离线）：vue / vue-router / element-plus（js+css+dark+zh-cn+en）/ icons
│   └── spa/             # 单页前端（UMD 引入，无打包）
│       ├── index.html   #   外壳：顶栏 + <router-view> + 主题/语言预渲染
│       ├── css/app.css  #   应用样式（Element Plus 主题变量映射 + 布局 + 对话区）
│       └── js/          #   app.js（入口/路由）api.js（fetch+SSE）theme.js i18n.js（中英词典）md.js（Markdown）views/（7 个路由视图 + first-image / chapter-card 等子组件）
└── data/                # app.db（SQLite） media/（图片/视频）
```

### 前端架构（Vue 3 + Element Plus，免构建）

- **技术栈**：Vue 3（组合式 API，UMD 全局构建）+ vue-router（history 模式）+ Element Plus 2.x
  （组件 / 暗色主题变量 / 中英文 locale / 图标包），全部以 `<script>`/`<link>` 从 `static/vendor/` 本地加载，
  **无需 Node / 构建工具**，改 `static/spa/` 下文件即生效。
- **路由**：`/` 首页 · `/projects` 列表 · `/new` 新建 · `/project/:id` 详情 · `/configs` 配置 ·
  `/micro` 作品列表 · `/micro/:id(/:sid)` 作品对话。未知路径由 FastAPI 兜底返回 SPA 外壳，
  任意深链刷新可用（前端路由再匹配；未匹配重定向首页）。
- **数据**：所有页面经 `static/spa/js/api.js`（fetch 封装 + SSE 解析）调 `/api/*`；
  对话页用 fetch 流读 SSE（`POST /api/micro/{id}/{sid}/chat`），按事件 id 更新有序内容块（文本/生成/媒体）。
- **组件**：列表用 `el-table`+筛选+`el-pagination`，弹窗用 `el-dialog`，删除用 `el-popconfirm`，
  步骤用 `el-steps`，首图/章节图用 `el-image`（teleported 预览），图片放大用 `el-image-viewer`，
  表单用 `el-form`/`el-select`/`el-radio-group`；提示统一 `ElMessage`。
- **国际化**：`static/spa/js/i18n.js` 内置完整中英词典（约 250 键/语言），`I18N.t(key, ...)` 按当前语言取词，
  缺失键回退中文；语言存 `localStorage`，切换时重建应用实例以应用 Element Plus 语言包（`zh-cn.js` / `en.js`）。

### 配置与存储（本次重构重点）

- **配置搬到页面**：LLM / DrawThings 的端点、模型、API Key 等都在 **⚙ 配置管理**（`/configs`）
  页面里新增、选用、删除，**不再使用配置文件**。支持**多套配置**，建项目时下拉选择。
- **存储**：结构化数据（配置 / 项目 / 章节）走 **SQLite + SQLAlchemy ORM**；
  媒体文件（图/视频）仍存 `data/media/`，数据库只存路径引用。
- **列表页**：创作中心（/projects）支持**类型筛选 / 状态筛选 / 时间排序**、**分页**（10/20/50 每页）；
  微创作（/micro）按最近活跃分页（10 每页）。
- **安全删除**：删除配置前会检查是否仍被项目或微创作作品选用，避免变成“孤儿”无法运行。
- ⚠️ API Key 以**明文**存于本地 SQLite（本地单用户应用）；若部署为多用户服务请改用加密存储。

### 页面风格与语言（light / dark / system · 中文 / EN）

- **主题**：顶栏右侧三键切换：**☀ 浅色 / ⚙ 系统 / ☾ 深色**；选择存入 `localStorage`，
  刷新/重开浏览器保持。`system` 时跟随系统 `prefers-color-scheme`（并实时响应切换）。
  首屏前用内联脚本先落 `data-theme` 与 `html.dark`，**无白屏闪烁**；主题同时驱动
  Element Plus 暗色模式（`static/vendor/element-plus/dark.css` 变量）与应用自定义 CSS 变量，
  组件（表格/弹窗/标签/输入）与布局底色整体联动。
- **语言**：顶栏 **中文 / EN** 一键切换；选择存 `localStorage`，刷新保持。首屏前内联脚本先落
  `<html lang>`，避免语言闪烁。切换时整个 Vue 应用重建，Element Plus 组件文案随之刷新。

### 性能优化

- **SQLite WAL 模式**：读写不互斥，`synchronous=NORMAL` 减少 fsync，`busy_timeout` 抗锁竞争。
- **常用索引**：`chapters.project_id`、`projects.status/kind/created_at`。
- **响应压缩**：JSON/HTML/CSS/JS 走 GZip（`GZipMiddleware`，>500B 才压缩）。
- **静态缓存**：`/static/*`（含 vendor 依赖与 SPA 文件）带 `Cache-Control: max-age=3600`；
  SPA 外壳返回 `no-cache`（依赖路径变化即时生效）。
- **图片懒加载**：章节图 `loading="lazy"`；列表/作品页按需拉取 JSON，媒体不随列表下发。
- **本地响应**：API 均为本地调用，页面数据毫秒级返回。

### 为什么继续用 SQLite（而非换成 Postgres/MySQL）

本机单用户应用、数据量小（配置/项目/章节数百行级）、媒体在磁盘：
- SQLite 零运维、零网络跳，WAL 下并发读 + 串行写完全够用，**比 client-server 数据库更快**（少一次 socket 往返）。
- Postgres/MySQL 只在需要**多进程/多机写入、并发写、超大表、行级锁**时才值得引入——本应用用不到，
  反而多一个要维护的服务与连接管理。故保留 SQLite，并用 WAL + 索引把它的性能吃满。
- 将来若真要多用户服务化，再迁移 Postgres（SQLAlchemy 层已抽象，换 `DATABASE_URL` 即可）。

### 快速开始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 仅按需改数据目录
python main.py                # 访问 http://127.0.0.1:8010
```

> 若遇到 SSL 证书校验失败（macOS Python 常见），装包时加：
> `SSL_CERT_FILE=/etc/ssl/cert.pem pip install -r requirements.txt`

### 环境变量（见 .env.example）

> 配置项已迁到页面管理，环境变量仅保留数据目录。

| 变量 | 说明 | 默认 |
|------|------|------|
| `DATA_DIR` | 数据库与媒体文件存放目录 | `./data` |

### 运行前提（真实端点）

本应用直接调用真实服务，无内置模拟模式：

1. **LLM**：任意 OpenAI 协议端点（Ollama `http://127.0.0.1:11434/v1`、vLLM、云端 OpenAI 等），
   在 **⚙ 配置管理** 里新增配置；按模型能力勾选「图片输入」（多模态 / 纯文本）。
2. **Draw Things**：Mac 上运行 Draw Things app，开启 HTTP 服务器（见下节），
   在 **⚙ 配置管理** 里创建 DrawThings 配置（出图/出视频由 app 里加载的模型决定）。

端点未启动时，对应步骤会在项目页显示**友好错误提示**（如连接失败、未开启服务等，按语言本地化），
修复后重新点该步骤即可，不影响已生成内容。

> 系统代理（Clash 等）：本地回环端点（127.0.0.1 / localhost）自动**直连、不走系统代理**
> （系统代理常把 127.* 转到远端导致 502）；云端 LLM 端点保留系统代理设置。

### 接入真实 Draw Things

只使用 Draw Things app 的 **gRPC API**（app 内「API server」设为 gRPC，默认端口 7859；端点填 `host:port`）。
HTTP API 已移除：它对视频模型只返回单帧 PNG，无法出视频。

#### 配置要点

- 依赖 `drawthings-py`（`requirements.txt` 已含 `drawthings-py[ffmpeg]`）：构建 FlatBuffer 生成配置、
  接收**帧序列**，并用 ffmpeg 合成视频（LTX 等还会带回音轨，浏览器可直接播放）。
- gRPC 请求**必须自带完整生成配置**，因此配置里要指定：
  - **图像模型 / 视频模型**（`model_image` / `model_video`）：各可留空，**至少填一个**（只填一个 = 只支持该类型）。
    点「获取模型」即可从 app 读取**已下载的模型**（gRPC `get_models`，带名称与是否视频）。
  - 生成参数（**预设**）按模型名**自动推断**（归一化匹配 drawthings-py 预设模型，去量化/版本后缀；
    如 `ltx_2.3_22b_distilled_1.1_q6p` → `ltx_2_3_distilled`、`flux_2_klein_9b_q6p` → `flux_2_klein_9b`），
    无需填写。推断不到的模型会给出明确错误（drawthings-py 预设不支持该模型）。
- 生成前会在同一连接内校验模型已下载，不存在直接报错（避免 app 退出）。
- 分辨率：图片 = 调用方（智能体）决定 > 预设，受 `max_side`（最长边）限幅；**视频同样受 `max_side` 限幅**
  （0 = 用预设尺寸。LTX 预设默认 1280×768，很吃显存，实测 25 帧 >10 分钟；建议 `max_side=768` → 768×448，
  25 帧约 90 秒）。
- 时长：配置 `max_seconds`（**默认 8 = 内置上限**，0 = 用内置上限）为上限；**模型可在生成时按用户要求决定更短的时长**
  （微创作工具带 `seconds` 参数）。帧数 = 秒数 × 帧率，并吸附到该模型的**合法帧数**（LTX 为 `8n+1`、Wan/Hunyuan 等为 `4n+1`），
  再受预设帧数与 **8 秒内置上限**约束。（例：LTX 预设 fps=5 时 5s=25 帧、6.6s=33 帧，8s 因 8n+1 约束实际取 33 帧。）
- 连续性参考：漫画沿用上一张图、短剧沿用上一段视频末帧（客户端自动抽取）。

分辨率优先级（项目）：章节自身的宽/高 > 大纲里的**默认分辨率** > 智能体在剧本阶段按场景构图决定
（`ScriptOut.width/height`，64 的倍数）；生成时再按配置 `max_side`（最长边）限幅。
大纲页可设**默认分辨率**，每章卡片里也可**手动调整分辨率**。

---

## English Documentation

### Overview

A local, all-in-one app: **FastAPI (JSON API + SSE) backend + a Vue 3 / Element Plus single-page frontend (SPA)**.
The frontend uses **local, build-free vendor files** (Vue / vue-router / Element Plus are all downloaded into `static/vendor/` and loaded as UMD),
so it runs fully offline; FastAPI falls back to serving the SPA shell, so refreshing on any route works (history routing).

Start from **one sentence** and run it through a single pipeline to produce a **continuous comic** or a **continuous short drama**:

> one sentence → Outline (style / characters / chapter plan) → Chapters (per-chapter script + media) → Complete (export ZIP/PDF)

The generation step talks to a large model over the OpenAI protocol; images and video are produced by **Draw Things** on the Mac.
Because images are supported, **the next chapter is generated with reference to the previous image (comic) / the last frame of the previous video (drama)**, keeping the visuals coherent.

The UI **supports both Chinese and English**; toggle with one click in the top bar (frontend `I18N` switch + the backend localizes error messages via `Accept-Language`).

### The two features

| Feature | Path | Output | Continuity reference |
|---------|------|--------|----------------------|
| Comic | Continuous image generation | one image per chapter | ch.1 = first image; the rest = the previous image |
| Short drama | Continuous video generation | one clip per chapter | ch.1 = first image (first frame); the rest = the last frame of the previous clip |

### Home (/)

- **Brand area**: platform name (Drawthings Studio) + a one-line value proposition + the primary CTAs (Enter Studio / ✨ Quick Create).
- **Two feature entries**: the comic path (continuous images) and the drama path (continuous video), each explaining its continuity strategy.
- **Pipeline explainer**: idea → Outline (style / characters / chapter plan) → Chapters (per-chapter script + media) → Complete (export ZIP/PDF); the project page is three tabs (Outline / Chapters / Complete).

### My Creations (list page /projects)

- **Management**: click the **title** to open / rename / delete (delete has a second confirmation); the project page header also has "Delete this project".
  Deleting also removes all chapter records and the generated image/video files.
- **Filters**: keyword (title/idea), type (comic/drama), status, sort (newest created / oldest created / recently active), and page size (10/20/50).
- **Info**: type tag, title/idea, status tag, chapter progress (generated/total chapters), first-image thumbnail (click to preview), last active (with created date);
  the toolbar shows the total count and a "＋ New Project" dialog (the same form component as the /new page).

### Creation control (project page)

The project page is organized into **three tabs — Outline / Chapters / Complete** — you can switch between them at any time (the Chapters tab has a one-click "Back to outline").

- **First image**: the project page lets you **upload a first image** or **generate one from a prompt** (leave the prompt empty and the LLM writes one from the idea + style).
  The first image is the reference for chapter 1 (comic = img2img reference; drama = the first video frame),
  and serves as the character/style baseline for the whole work during scripting (visible to a multimodal LLM).
- **Style selection (with custom)**: when creating a project, pick a preset style (Japanese manga / Chinese ink wash / chibi /
  realistic cinematic / Pixar 3D / cyberpunk) or choose "Custom…" and type any description;
  the style is stored in the outline and keeps every chapter consistent (a user-specified style takes priority over the LLM's recommendation).

- **① Outline**: click "Generate outline" to plan, in one pass, **style / theme / tone + the overall story outline (beginning → development → climax → ending) + the characters**,
  and to produce the **chapter plan** — each chapter's title + one-line topic summary — based on the "chapter count".
  The outline, characters, **default resolution** and the chapter plan (title / summary / count) are all **manually editable**, then "Save outline";
  you can also "Regenerate outline" (redoes outline + characters + chapters from the current settings) or "Rebuild chapters from outline" (re-plans the chapters only).
  The character sheet keeps the cast consistent across chapters (fed into each chapter's prompt).
- **② Chapters**: produce the content for the planned chapters. **One-click generate** — "Generate all scripts / Generate all media / Regenerate all frames" (SSE per-chapter progress);
  each chapter's accordion card (**shows only its photo** by default; click to expand, one open at a time, auto-expands when generated) supports **per-chapter steps**:
  "Generate script" (writes script/prompt/resolution, using the outline's style + characters + that chapter's summary) → "Generate media",
  plus **Regenerate media / Save prompt / adjust resolution / Move up / Move down / Delete**.
  A comic chapter = **one multi-panel comic page** (several panels in a single image, portrait, with caption/dialogue text); a drama chapter = one video clip.
- **③ Complete**: once generated, **Export ZIP** (all media + the outline/characters/per-chapter script text) and **Export PDF**
  (comic: the chapter images combined into a multi-page PDF; drama is video and has no PDF), then click "Complete" to mark the project as done.
- **Re-set**: at any time, open "Re-set" on the project page to adjust the **title / one-line idea (theme) / style** in a dialog.
  It is **non-destructive by default** — the existing outline, characters, chapters and generated media are kept;
  you can optionally tick "Clear & rebuild downstream" to wipe the outline/characters/chapters/media and return to the "Outline" stage (a fresh restart with the new settings).
- **Per-chapter progress for long steps (SSE)**: "Generate all scripts / Generate all media / Regenerate all frames" are long steps
  (the LLM writes chapter by chapter / media renders chapter by chapter; blocking calls run in a thread pool so the event loop stays responsive),
  streamed over SSE with per-chapter progress (chapter x/y "title"), then auto-refreshes; a single chapter can still generate its script/media or regenerate from its own card buttons.

### Quick Create (/micro, work → independent sessions)

A lightweight, no-project creation desk (top bar "✨ Quick Create") — **a Quick Create work contains multiple independent sessions**, with persisted history:

- **Three-level structure** (same idea as Studio's project→chapters):
  - `/micro` **work list page**: card grid (open/delete), paginated 10 per page, sorted by most recently active;
    "＋ New work" opens a dialog to create one (work title / LLM / DrawThings).
  - `/micro/{id}` and `/micro/{id}/{sid}` are the **same work page** (left/right layout, no need to split; auto-locates the most recent session when one exists):
    **entering a work opens its most recently updated session** (empty state shown when there are none);
    **left side** lists the work's independent sessions (open/rename/delete + "＋ New session" dialog, selected one highlighted),
    and can be **collapsed/expanded** (collapse via «, expand via the left-edge vertical tab, state remembered per work);
    **right side** is the current session's chat; a work-options dialog (applies to all sessions) + return to work list / delete work,
    with each session's history and context fully independent.
- **Chat history persisted**: user messages and assistant replies (including generated media and prompts) are stored in SQLite
  (`micro_works` / `micro_sessions` / `micro_messages`), so they survive refreshes; multi-turn context is pulled from the database (last 20 messages).
  Assistant replies are stored as **ordered content blocks** (`micro_messages.parts`: text / generation / error), so text and generated
  results keep their exact chronological order, one reply can contain several images/videos, and a refresh matches the streaming exactly.
  Untitled sessions/works are auto-named from the first user message; old databases (the legacy per-session config structure) are migrated on startup.
- **Unified chat + streaming output (SSE)**: multi-turn conversation with the chosen LLM (ideation / scripting / prompts), with replies displayed token by token.
  A generation block settles by event id from "generating (spinner)" to "✓ Generated / ⚠ Failed"; the prompt is collapsed by default (click "Prompt" to expand) and shown only once.
- **Chat area interaction**: the message area adapts to the viewport height (no more brittle offset); opening a session / images loading
  auto-pins to the bottom, while scrolling up stops the follow and reveals a "↓ Back to bottom" button; the session sidebar collapses
  (remembered per work), and switching sessions aborts any in-flight generation.
- **Auto-generation via function calling**: when an image/video is needed, the model **auto-calls the `generate_media` tool**
  (distilling a detailed English prompt from the context), then calls Draw Things to produce a single image/video;
  the result is embedded directly into the chat bubble (with the prompt).
- **User image attachments**: when the chosen LLM supports vision (`supports_vision`), the input box lets you click 📎 to upload, **paste**, or **drag**
  images (up to 4 per message; you can send images without text). Attachments are stored with the message and sent back to the model as part of the multi-turn context.
  Non-vision models do not show this entry.
- **Markdown rendering**: assistant replies are rendered as Markdown (headings / nested lists / tables / code blocks / quotes, etc.,
  escaped first then converted, to prevent XSS); code blocks support one-click copy, wide tables scroll horizontally; opening a session auto-scrolls to the latest message.
- **Works tab (image / video second-level tabs + export)**: the work page's "Works" tab groups all media generated by **all sessions** of the work
  under a **second-level tab switch (Images / Videos)**; each tab supports **single / selected / all** export — images as **ZIP or PDF** (multi-page),
  videos as **ZIP** (the ZIP is named in export order and split into `images/` and `videos/` subfolders); multi-select batch delete is still available (removes the messages and media files too).
- **Work options**: LLM config / Draw Things config (optional = chat only); saved with the work
  (shared by all its sessions) and editable on the work page.
  The output type (image/video) **needs no selection**: each generation auto-detects from the model currently loaded in the app
  (model names containing svd/wan/i2v and other video keywords → video, otherwise image).
- **Deleting a session/work** also cleans up its generated media files and user attachments.

The backend uses **Pydantic AI v2** uniformly (`services/agent.py`): the pipeline stages (outline/chapters/script)
use structured output (Pydantic models), while Quick Create uses streaming + tool calls, all over the OpenAI-compatible protocol.

### Configuration fields

- **LLM config**: `supports_vision` (image input) = supports image input (multimodal; can reference the previous frame/first image during scripting) / text-only (no reference images).
- **Draw Things config** (gRPC only; set the app's API server to gRPC):
  **image model / video model** (`model_image` / `model_video`; each may be empty but at least one is required; click
  "Fetch models" to read the downloaded models from the app). The generation preset (steps / sampler / size) is
  **inferred from the model name**, no input needed. Any project/work can use any Draw Things config.
  Personalized params: `max_side` (max resolution, longest side only; caps both images and video) and
  `max_seconds` (max video duration in seconds; **default 8 = built-in cap**; 0 = use the built-in cap) — the model may choose a shorter duration per request (never above the cap).
  There is also a **hard 8-second cap** on a single video (frames = fps × 8).

### Directory structure

```
.
├── main.py              # FastAPI entry + /api routes + SSE + SPA shell fallback (db session injected per request)
├── config.py            # keeps only the data directory (reads env DATA_DIR, optional)
├── db.py                # SQLAlchemy engine / session / init_db (incl. legacy-structure migration)
├── models.py            # ORM models: LLMConfig / DrawThingConfig / Project / Chapter / MicroWork / MicroSession / MicroMessage
├── config_store.py      # config CRUD (with "referenced by a project/work" protection before delete)
├── i18n.py              # backend zh/en localization (Accept-Language → zh|en + L() text helper)
├── requirements.txt
├── services/
│   ├── agent.py         # Pydantic AI v2 unified agent layer (model construction / structured output / message history)
│   ├── drawthings.py    # Draw Things gRPC client (images + video, via drawthings-py) + shared helpers/factory
│   └── pipeline.py      # pipeline orchestration (builds the agent at runtime from the project's config)
├── static/
│   ├── vendor/          # frontend deps (downloaded locally, build-free/offline): vue / vue-router / element-plus (js+css+dark+zh-cn+en) / icons
│   └── spa/             # single-page frontend (UMD, no bundler)
│       ├── index.html   #   shell: top bar + <router-view> + pre-paint theme/lang
│       ├── css/app.css  #   app styles (Element Plus theme variable mapping + layout + chat area)
│       └── js/          #   app.js (entry/router) api.js (fetch+SSE) theme.js i18n.js (zh/en dict) md.js (Markdown) views/ (7 routed views + sub-components like first-image / chapter-card)
└── data/                # app.db (SQLite) media/ (images/videos)
```

### Frontend architecture (Vue 3 + Element Plus, build-free)

- **Stack**: Vue 3 (Composition API, UMD global build) + vue-router (history mode) + Element Plus 2.x
  (components / dark theme variables / zh & en locales / icon package), all loaded locally from `static/vendor/` via `<script>`/`<link>`,
  **no Node / build tooling required** — editing files under `static/spa/` takes effect immediately.
- **Routes**: `/` home · `/projects` list · `/new` create · `/project/:id` detail · `/configs` settings ·
  `/micro` work list · `/micro/:id(/:sid)` work chat. Unknown paths fall back to the SPA shell from FastAPI,
  so deep-link refreshes work (the frontend router re-matches; unmatched routes redirect home).
- **Data**: every page calls `/api/*` through `static/spa/js/api.js` (fetch wrapper + SSE parser);
  the chat page reads SSE via a fetch stream (`POST /api/micro/{id}/{sid}/chat`), updating ordered content blocks (text / generation / media) by event id.
- **Components**: lists use `el-table`+filters+`el-pagination`, dialogs use `el-dialog`, deletes use `el-popconfirm`,
  steps use `el-steps`, first/chapter images use `el-image` (teleported preview), image zoom uses `el-image-viewer`,
  forms use `el-form`/`el-select`/`el-radio-group`; toasts use `ElMessage` uniformly.
- **Internationalization**: `static/spa/js/i18n.js` ships a full zh/en dictionary (~250 keys per language); `I18N.t(key, ...)`
  picks the string for the current language, falling back to Chinese for missing keys; the language is stored in `localStorage`,
  and switching rebuilds the app instance so the Element Plus locale (`zh-cn.js` / `en.js`) is applied.

### Configuration & storage (the focus of this refactor)

- **Configs moved to the UI**: the endpoint, model, API key, etc. for LLM / Draw Things are all added, selected, and deleted on the
  **⚙ Settings** (`/configs`) page — **no config files are used**. **Multiple configs** are supported, chosen via a dropdown when creating a project.
- **Storage**: structured data (configs / projects / chapters) uses **SQLite + SQLAlchemy ORM**;
  media files (images/videos) still live in `data/media/`, with the database storing only path references.
- **List pages**: Studio (/projects) supports **type / status / time filters** and **pagination** (10/20/50 per page);
  Quick Create (/micro) paginates by most recently active (10 per page).
- **Safe delete**: before deleting a config, the app checks whether it is still used by a project or Quick Create work, to avoid creating an orphan that can no longer run.
- ⚠️ API Keys are stored in **plaintext** in local SQLite (a local single-user app); if you deploy it as a multi-user service, switch to encrypted storage.

### Appearance & language (light / dark / system · Chinese / EN)

- **Theme**: three buttons in the top bar: **☀ Light / ⚙ System / ☾ Dark**; the choice is stored in `localStorage`
  and persists across refreshes/restarts. `system` follows the OS `prefers-color-scheme` (and reacts live to changes).
  Before first paint, an inline script sets `data-theme` and `html.dark` first, **with no white flash**; the theme drives both
  the Element Plus dark mode (`static/vendor/element-plus/dark.css` variables) and the app's custom CSS variables,
  so components (tables/dialogs/tags/inputs) and layout colors all change together.
- **Language**: **中文 / EN** toggle in the top bar; the choice is stored in `localStorage` and persists across refreshes.
  An inline script sets `<html lang>` before first paint to avoid a language flash. On switch, the whole Vue app is rebuilt and Element Plus component text updates with it.

### Performance

- **SQLite WAL mode**: reads and writes don't block each other; `synchronous=NORMAL` reduces fsyncs; `busy_timeout` resists lock contention.
- **Common indexes**: `chapters.project_id`, `projects.status/kind/created_at`.
- **Response compression**: JSON/HTML/CSS/JS go through GZip (`GZipMiddleware`, only >500B is compressed).
- **Static caching**: `/static/*` (vendor deps + SPA files) gets `Cache-Control: max-age=3600`;
  the SPA shell returns `no-cache` (so path changes take effect immediately).
- **Image lazy loading**: chapter images use `loading="lazy"`; list/work pages fetch JSON on demand; media is not bundled with lists.
- **Local responsiveness**: all APIs are local calls, so page data returns in milliseconds.

### Why keep SQLite (instead of switching to Postgres/MySQL)

A local single-user app, small data (hundreds of rows for configs/projects/chapters), media on disk:
- SQLite is zero-ops and zero network hops; under WAL, concurrent reads + serial writes are plenty, and it's **faster than a client-server database** (one fewer socket round trip).
- Postgres/MySQL are only worth introducing when you need **multi-process/multi-machine writes, concurrent writes, very large tables, or row-level locking** — none of which this app needs, and it would add another service and connection management to maintain. So we keep SQLite and squeeze out its performance with WAL + indexes.
- If it truly needs to become a multi-user service later, migrate to Postgres then (the SQLAlchemy layer is abstracted, so just change `DATABASE_URL`).

### Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # only adjust the data directory if needed
python main.py                # visit http://127.0.0.1:8010
```

> If you hit an SSL certificate verification failure (common on macOS Python), install with:
> `SSL_CERT_FILE=/etc/ssl/cert.pem pip install -r requirements.txt`

### Environment variables (see .env.example)

> Config items have moved to the UI; the only remaining env variable is the data directory.

| Variable | Description | Default |
|----------|-------------|---------|
| `DATA_DIR` | Directory for the database and media files | `./data` |

### Prerequisites (real endpoints)

This app calls real services directly; there is no built-in mock mode:

1. **LLM**: any OpenAI-protocol endpoint (Ollama `http://127.0.0.1:11434/v1`, vLLM, cloud OpenAI, etc.);
   add a config in **⚙ Settings**; check "image input" per model capability (multimodal / text-only).
2. **Draw Things**: run the Draw Things app on the Mac and enable its HTTP server (see below);
   create a Draw Things config in **⚙ Settings** (image/video output is decided by the model loaded in the app).

When an endpoint is not running, the relevant step shows a **friendly error** on the project page (e.g. connection failed, service not enabled — localized per language);
fix it and re-run that step; already-generated content is unaffected.

> System proxy (Clash, etc.): local loopback endpoints (127.0.0.1 / localhost) **connect directly, bypassing the system proxy**
> (proxies often forward 127.* to a remote host, causing 502); cloud LLM endpoints keep the system proxy settings.

### Connecting to a real Draw Things

This app uses **only the Draw Things gRPC API** (set "API server" to gRPC in the app; default port 7859; enter the
endpoint as `host:port`). The HTTP API has been removed: it only returns a single still frame for video models.

#### Configuration notes

- Depends on `drawthings-py` (already in `requirements.txt` as `drawthings-py[ffmpeg]`): it builds the FlatBuffer
  generation config, receives the **frame sequence**, and assembles video with ffmpeg (LTX etc. also return audio,
  playable in the browser).
- A gRPC request **must carry the full generation config**, so the config specifies:
  - **image model / video model** (`model_image` / `model_video`): each may be empty, but **at least one is required**
    (only one set = only that type is supported). Click "Fetch models" to read the **downloaded models** from the app
    (gRPC `get_models`, with names and a video flag).
  - The generation **preset** (steps / sampler / size) is **inferred from the model name** (normalized match against
    drawthings-py preset models, ignoring quantization/version suffixes, e.g. `ltx_2.3_22b_distilled_1.1_q6p` →
    `ltx_2_3_distilled`, `flux_2_klein_9b_q6p` → `flux_2_klein_9b`), no input needed. A model with no matching preset
    gets a clear error.
- The model is validated (on the same connection) before generation, so a missing model errors out instead of quitting the app.
- Resolution: images = caller (agent) > preset, capped by `max_side` (longest side); **video is also capped by `max_side`**
  (0 = preset size. The LTX preset defaults to 1280×768 which is very VRAM-heavy — 25 frames took >10 min; use `max_side=768`
  → 768×448, ~90 s for 25 frames).
- Duration: the config's `max_seconds` (**default 8 = built-in cap**; 0 = use the built-in cap) is the upper bound; the **model may choose a shorter duration per request** (the Quick Create tool takes a `seconds` argument). Frames = seconds × fps, snapped to the model's **valid frame counts** (LTX: `8n+1`; Wan/Hunyuan etc.: `4n+1`), then bounded by the preset frame count and the **built-in 8s cap**. (E.g. with the LTX preset at fps=5: 5s = 25 frames, 6.6s = 33 frames; 8s actually yields 33 frames due to the 8n+1 constraint.)
- Continuity: comics reference the previous image, dramas the last frame of the previous clip (extracted automatically).

Resolution priority (projects): the chapter's own width/height > the outline's **default resolution** > the agent's
per-scene choice during scripting (`ScriptOut.width/height`, multiples of 64); then capped by `max_side`.
