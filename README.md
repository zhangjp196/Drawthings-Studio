# Drawthings Studio

> From a single sentence to a full continuous comic / short drama (images + video) — a local, all-in-one creation studio.

[English](README.md) · [中文](README.zh-CN.md)

## Overview

A local, all-in-one app: **FastAPI (JSON API + SSE) backend + a Vue 3 / Element Plus single-page frontend (SPA)**.
Two shapes: **BS** (open it in a browser) and **CS** (`python client.py` native desktop client: auto-starts the server + a native window, no browser — see "Desktop client (CS)" below).
The frontend uses **local, build-free vendor files** (Vue / vue-router / Element Plus are all downloaded into `static/vendor/` and loaded as UMD),
so it runs fully offline; FastAPI falls back to serving the SPA shell, so refreshing on any route works (history routing).

Start from **one sentence** and run it through a single pipeline to produce a **continuous comic** or a **continuous short drama**:

> one sentence → Outline (style / characters / chapter plan) → Chapters (per-chapter script + media) → Complete (export ZIP/PDF)

The generation step talks to a large model over the OpenAI protocol; images and video are produced by **Draw Things** on the Mac.
Because images are supported, **the next chapter is generated with reference to the previous image (comic) / the last frame of the previous video (drama)**, keeping the visuals coherent
(enable "Supports reference image" for the work — picked per feature in the create form, config value as fallback;
otherwise it is text-to-image / text-to-video).

The UI **supports both Chinese and English**; toggle with one click in the toolbar (frontend `I18N` switch + the backend localizes error messages via `Accept-Language`).

## The two features

| Feature | Path | Output | Continuity reference |
|---------|------|--------|----------------------|
| Comic | Continuous image generation | one image per chapter | ch.1 = first image; the rest = the previous image |
| Short drama | Continuous video generation | one clip per chapter | ch.1 = first image (first frame); the rest = the last frame of the previous clip |

Continuity references require **"Supports reference image"** to be enabled for the work (picked per feature in the
create form; the config value is the fallback) — image-to-image / image-to-video; off = text-to-image / text-to-video.
With it on, each chapter's prompt is auto-written as an **edit instruction based on the reference image** (keep characters / style / composition consistent, describe only what changes this chapter) instead of a full re-description — so generations follow the reference.

> **Design convention — keep comic and short drama separate.** They are two **independent, parallel tracks**
> (`api_comic` ↔ `api_drama`, `pipeline_comic` ↔ `pipeline_drama`, `project-comic.js` ↔ `project-drama.js`, …)
> that intentionally duplicate structure so each can evolve and be maintained on its own. **Do not merge them /
> do not extract a shared base / do not add a `kind` branch.** Only type-agnostic infrastructure is shared
> (`api_common`, `events`, `media_files`, `capabilities`, `drawthings`, `agent`, `db`, `i18n`, `config_store`, `runtime`).
> When you change one track, mirror the change in the other by hand. See `AGENTS.md`.

## Workspace (home /)

- **Quick actions**: New project / Quick Create / Settings.
- **Recent creations**: the 6 most recently active projects as cards (first-image thumbnail + type + chapter count + last active); click to open the project, or "All →" for Studio.

## My Creations (list page /projects)

- **Management**: click the **title** to open / rename / delete (delete has a second confirmation); the project page header also has "Delete this project".
  Deleting also removes all chapter records and the generated image/video files.
- **Filters**: keyword (title/idea), type (comic/drama), status, sort (newest created / oldest created / recently active), and page size (10/20/50).
- **Info**: type tag, title/idea, status tag, chapter progress (generated/total chapters), first-image thumbnail (click to preview), last active (with created date);
  the toolbar shows the total count and a "＋ New Project" dialog (the same form component as the /new page).

## Creation control (project page)

The project page is organized into **three tabs — Outline / Chapters / Complete** — you can switch between them at any time (the Chapters tab has a one-click "Back to outline").

- **First image**: the project page lets you **upload a first image** or **generate one from a prompt** (leave the prompt empty and the LLM writes one from the idea + style).
  The first image is the reference for chapter 1 (comic = img2img reference; drama = the first video frame;
  requires "Supports reference image" for the work (config value as fallback),
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
- **Generation jobs (observable / cancellable)**: each long step (chapter planning / generation / batch scoring / single-chapter generation)
  runs as a **job** (`running → done | error | interrupted | cancelled`) with a progress note; `GET /api/comics|dramas/{id}/jobs`
  lists them and `POST .../jobs/{id}/cancel` stops a running one **without relying on the client connection**. The page's existing
  "Stop" button aborts the SSE stream, which now also stops the in-flight Draw Things render (cooperative cancel).
  (Comic and short drama each implement this on their own track.)

## Quick Create (/micro, work → independent sessions)

A lightweight, no-project creation desk (sidebar "Quick Create") — **a Quick Create work contains multiple independent sessions**, with persisted history:

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
- **Durable stream (recoverable output)**: the assistant reply is written to the database **incrementally** (a draft is
  created up front, updated as text/media accrue, then finalized). If the browser disconnects, the page is refreshed, the
  app is closed or the process crashes mid-generation, the partial reply is kept and marked **interrupted** (stale
  drafts are swept on startup) — instead of losing the whole reply.
- **Parameter snapshot + one-click re-run**: every generation records its snapshot (model / size / seconds / reference)
  in the content block; the card shows it and offers **Re-run**, which reproduces the result with the **same parameters
  directly (no LLM in the loop)** — deterministic re-generation.
- **Cross-turn reference**: with "Supports reference image" on, when no image is attached the generation falls back to
  the **session's most recently generated media** (across turns), not just the current one.
- **Asset graph**: every successful generation is also recorded as an **asset** (media + prompt + model / size /
  seconds / reference), independent of the message that displays it — so gallery cards show the generation parameters,
  assets can be reused as references, and `GET /api/micro/{work}/assets` exposes them for filtering/export.
- **Generation jobs**: each chat / re-run is a **job** (`running → done | error | interrupted | cancelled`) with a note
  and prompt; `GET /api/micro/{work}/jobs` lists them and `POST .../jobs/{id}/cancel` stops a running one **without
  relying on the client connection** (a live job also shows a Stop control in the chat).
- **Reference orchestration**: the model can target an **earlier asset** of the session via the `ref_index` argument of
  `generate_media` (1 = most recent, 2 = second most recent…), so multi-step "use the 2nd image as reference" flows are
  deterministic instead of always chaining the latest.
- **User image attachments**: when the chosen LLM supports vision (`supports_vision`), the input box lets you click 📎 to upload, **paste**, or **drag**
  images (up to 4 per message; you can send images without text). Attachments are stored with the message and sent back to the model as part of the multi-turn context.
  Non-vision models do not show this entry.
  Attached images double as the **reference image** of the next generation (image-to-image / image-to-video, governed by the Draw Things config's
  **Support reference image** switches; with a switch off, generation stays text-to-image / text-to-video).
  With no attachment, the session's most recently generated media is used as the reference.
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

## Configuration fields

- **LLM config**: `supports_vision` (image input) = supports image input (multimodal; can reference the previous frame/first image during scripting) / text-only (no reference images).
- **Draw Things config** (gRPC only; set the app's API server to gRPC): endpoint + caps + the
  **reference image** capability. **Models are now picked per feature** (the project / micro-creation create form;
  "Fetch models" reads the downloaded models from the app). The model stored in the config
  (`model_image` / `model_video`) is kept only as a fallback default (each may be empty). The generation preset
  (steps / sampler / size) is **inferred from the model name**, no input needed. Any project/work can use any
  Draw Things config.
  Personalized params: `max_side` (max resolution, longest side only; caps both images and video) and
  `max_seconds` (max video duration in seconds; **default 8 = built-in cap**; 0 = use the built-in cap) — the model may choose a shorter duration per request (never above the cap).
  **Supports reference image** (now picked per feature in the project / micro-creation create form; the config-level
  `ref_image` / `ref_video` is kept as a fallback default, **off by default**): per type, whether the generation uses
  the reference image (image-to-image / image-to-video). When on, comics pass the previous image and dramas the last
  frame of the previous clip as `init_image`; off = plain text-to-image / text-to-video.
  Known Draw Things bug: on some app versions, reference-image generation may crash the app — this app then detects the
  disconnection, waits up to 2 minutes per round for the app to restart, and retries the generation automatically
  (up to twice; community issue #121).
  There is also a **hard 8-second cap** on a single video (frames = fps × 8).

## Directory structure

```
.
├── main.py              # FastAPI 入口 + app 装配 + 配置/健康检查/SPA 外壳（API 路由在 services/）
├── app.py               # unified entry (PyInstaller target: no args = client, --server = server mode)
├── client.py            # CS desktop client (pywebview native window: auto-starts server / native "Save As" / notifications / single instance)
├── paths.py             # path resolution (resource & data dirs for source / packaged modes)
├── config.py            # keeps only the data directory (reads env DATA_DIR, optional)
├── db.py                # SQLAlchemy engine / session / init_db (incl. migrations; marks stale streams interrupted)
├── models.py            # ORM models: LLMConfig / DrawThingConfig / Project / Chapter / MicroWork / MicroSession / MicroMessage / Asset / GenerationJob
├── config_store.py      # config CRUD (with "referenced by a project/work" protection before delete)
├── i18n.py              # backend zh/en localization (Accept-Language → zh|en + L() text helper)
├── build_app.sh         # one-step .app packaging (PyInstaller)
├── build_dmg.sh         # one-step distributable DMG (.app + Applications alias, optional sign/notarize)
├── Drawthings Studio.spec  # PyInstaller build config (resources/icon/excludes/metadata; single source of truth)
├── tools/make_icon.py   # app icon generator (purple gradient rounded square + four-point star)
├── requirements.txt
├── services/
│   ├── agent.py         # Pydantic AI v2 unified agent layer (model construction / structured output / message history)
│   ├── api_common.py    # shared API infra (localization / media URL / serialization / SSE frame / paging)
│   ├── api_comic.py     # comic project routes (/api/comics/*)
│   ├── api_drama.py     # short-drama project routes (/api/dramas/*)
│   ├── api_micro.py     # micro-creation routes (/api/micro/*) + SSE transport (drive engine queue + heartbeat)
│   ├── micro_agent.py   # micro-creation session engine (system prompt / generate_media tool / parts / durable writes / rerun)
│   ├── micro_parts.py   # assistant "ordered content blocks" schema + versioned (de)serialization
│   ├── capabilities.py  # effective generation capabilities (feature-level model / ref-image overrides; shared)
│   ├── media_files.py   # media cleanup / path resolve / user-attachment save / ZIP·PDF export (shared)
│   ├── events.py        # SSE event contract (single source; frontend mirror = static/spa/js/events.js)
│   ├── drawthings.py    # Draw Things gRPC client (images + video, via drawthings-py) + shared helpers/factory + cooperative cancel
│   ├── pipeline.py      # pipeline facade (routes by kind → comic / drama)
│   ├── pipeline_common.py / pipeline_comic.py / pipeline_drama.py  # shared utils + the two independent pipelines
│   └── runtime.py       # runtime pipeline singleton
├── static/
│   ├── vendor/          # frontend deps (downloaded locally, build-free/offline): vue / vue-router / element-plus (js+css+dark+zh-cn+en) / icons
│   └── spa/             # single-page frontend (UMD, no bundler)
│       ├── index.html   #   shell: left sidebar + toolbar + <router-view> + pre-paint theme/lang
│       ├── css/app.css  #   app styles (Element Plus theme variable mapping + layout + chat area)
│       └── js/          #   app.js (entry/router) api.js (fetch+SSE) events.js (event constants) theme.js i18n.js (zh/en dict) md.js (Markdown)
│                        #   views/ (routed views + sub-components: micro-block / micro-session-list / micro-chat / micro-works-gallery; per-track comic/drama: chapter-card-*, create-form-*, chapter-list-*, chapter-toolbar-*, season-preview-*, season-export-*, gen-dialog-*, pdf-dialog-comic)
└── data/                # app.db (SQLite) media/ (images/videos)
```

## Frontend architecture (Vue 3 + Element Plus, build-free)

- **Stack**: Vue 3 (Composition API, UMD global build) + vue-router (history mode) + Element Plus 2.x
  (components / dark theme variables / zh & en locales / icon package), all loaded locally from `static/vendor/` via `<script>`/`<link>`,
  **no Node / build tooling required** — editing files under `static/spa/` takes effect immediately.
- **Desktop shell (CS style)**: a collapsible left **sidebar** (Workspace / Comic Studio / Video Studio / Quick Create / Settings) + a compact top **toolbar**
  (current page title + language / theme / quit); the content area fills the window and scrolls internally; panels are **solid, native-style**
  (no glass, small radii, high density); on narrow windows the sidebar collapses to icons.
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

## Configuration & storage (the focus of this refactor)

- **Configs moved to the UI**: the endpoint, model, API key, etc. for LLM / Draw Things are all added, selected, and deleted on the
  **⚙ Settings** (`/configs`) page — **no config files are used**. **Multiple configs** are supported, chosen via a dropdown when creating a project.
- **Storage**: structured data (configs / projects / chapters) uses **SQLite + SQLAlchemy ORM**;
  media files (images/videos) still live in `data/media/`, with the database storing only path references.
- **List pages**: Studio (/projects) supports **type / status / time filters** and **pagination** (10/20/50 per page);
  Quick Create (/micro) paginates by most recently active (10 per page).
- **Safe delete**: before deleting a config, the app checks whether it is still used by a project or Quick Create work, to avoid creating an orphan that can no longer run.
- ⚠️ API Keys are stored in **plaintext** in local SQLite (a local single-user app); if you deploy it as a multi-user service, switch to encrypted storage.

## Appearance & language (light / dark / system · Chinese / EN)

- **Theme**: three buttons in the toolbar: **☀ Light / ⚙ System / ☾ Dark**; the choice is stored in `localStorage`
  and persists across refreshes/restarts. `system` follows the OS `prefers-color-scheme` (and reacts live to changes).
  Before first paint, an inline script sets `data-theme` and `html.dark` first, **with no white flash**; the theme drives both
  the Element Plus dark mode (`static/vendor/element-plus/dark.css` variables) and the app's custom CSS variables,
  so components (tables/dialogs/tags/inputs) and layout colors all change together.
- **Language**: **中文 / EN** toggle in the toolbar; the choice is stored in `localStorage` and persists across refreshes.
  An inline script sets `<html lang>` before first paint to avoid a language flash. On switch, the whole Vue app is rebuilt and Element Plus component text updates with it.

## Performance

- **SQLite WAL mode**: reads and writes don't block each other; `synchronous=NORMAL` reduces fsyncs; `busy_timeout` resists lock contention.
- **Common indexes**: `chapters.project_id`, `projects.status/kind/created_at`.
- **Response compression**: JSON/HTML/CSS/JS go through GZip (`GZipMiddleware`, only >500B is compressed).
- **Static caching**: `/static/*` (vendor deps + SPA files) gets `Cache-Control: max-age=3600`;
  the SPA shell returns `no-cache` (so path changes take effect immediately).
- **Image lazy loading**: chapter images use `loading="lazy"`; list/work pages fetch JSON on demand; media is not bundled with lists.
- **Local responsiveness**: all APIs are local calls, so page data returns in milliseconds.

## Why keep SQLite (instead of switching to Postgres/MySQL)

A local single-user app, small data (hundreds of rows for configs/projects/chapters), media on disk:
- SQLite is zero-ops and zero network hops; under WAL, concurrent reads + serial writes are plenty, and it's **faster than a client-server database** (one fewer socket round trip).
- Postgres/MySQL are only worth introducing when you need **multi-process/multi-machine writes, concurrent writes, very large tables, or row-level locking** — none of which this app needs, and it would add another service and connection management to maintain. So we keep SQLite and squeeze out its performance with WAL + indexes.
- If it truly needs to become a multi-user service later, migrate to Postgres then (the SQLAlchemy layer is abstracted, so just change `DATABASE_URL`).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # only adjust the data directory if needed
python main.py                # visit http://127.0.0.1:8010
python client.py              # (optional) CS desktop client: auto-start server + native window, no browser
```

> If you hit an SSL certificate verification failure (common on macOS Python), install with:
> `SSL_CERT_FILE=/etc/ssl/cert.pem pip install -r requirements.txt`

## Desktop client (CS)

By default the app runs **BS**-style (open `http://127.0.0.1:8010` in a browser); it can also run **CS**-style — a native desktop app, no browser:

```bash
pip install pywebview        # already in requirements.txt
python client.py             # auto-starts the local server + opens a native window (macOS = system WKWebView)
```

What the client does:

- **One-click launch**: probes `/api/health`; if the server isn't running it auto-starts it (subprocess, production mode, no reload); if already running it just connects (no duplicate server);
- **Native window**: loads the same SPA (features / bilingual / theme fully consistent with the browser version);
- **Native OS features**:
  - ZIP/PDF export goes through the **system "Save As" dialog** (the frontend detects `window.pywebview` and switches automatically; in a browser it stays a regular download);
  - system notifications, external links open in the system browser;
- **Single instance**: a PID lock prevents duplicate windows (cleaned up on exit).

Env vars: `HOST` (default `127.0.0.1` — local only, not exposed) / `PORT` (default `8010`) / `RELOAD` (default `0`; use `RELOAD=1` for dev hot-reload) / `LOG_LEVEL` (default `INFO`). A `.env` file is loaded automatically (see "Environment variables").

> This shape is **local single-user only** (no multi-user accounts, no remote access); API keys and data stay on this machine.
> Packaging: `./build_app.sh` builds `dist/Drawthings Studio.app` in one step (see below).

## Packaging as a .app (PyInstaller)

The client + server can be packaged as a standard macOS app (with icon, double-click to run):

```bash
./build_app.sh          # produces dist/Drawthings Studio.app
```

- **Unified entry `app.py`**: the PyInstaller target; no args = desktop client (auto-starts the server),
  `--server` = server mode (the client spawns it as a subprocess — no external Python needed).
- **Build config `Drawthings Studio.spec`**: the **single source of truth** for packaging — resources (`static/`), the icon,
  excluded modules (`tkinter` / `logfire`), `copy_metadata` (genai_prices / pydantic_ai_slim, etc.) and the BUNDLE identifier all live here;
  `build_app.sh` builds straight from the spec, so **change packaging options in the spec** (not via extra CLI flags).
- **Resources / data separation**: `static/` is baked into the bundle (read-only); data (SQLite + media) lives in
  `~/Library/Application Support/Drawthings Studio/data` (source mode stays `<project root>/data`; the two don't affect each other).
- **ffmpeg** is not bundled: last-frame extraction relies on the system `ffmpeg` (`brew install ffmpeg`); it degrades gracefully when missing.

## Packaging as a DMG (release)

Build a **directly distributable** DMG on top of the `.app` (contains the `.app` and an "Applications" alias — mount and drag to install):

```bash
./build_dmg.sh          # produces dist/Drawthings Studio.dmg
```

- **One step**: `build_dmg.sh` runs `build_app.sh` first, then creates and verifies a compressed DMG.
  `SKIP_BUILD=1 ./build_dmg.sh` reuses the **existing** `.app` and only re-makes the DMG;
  `DMG_LAYOUT=1 ./build_dmg.sh` additionally lets Finder position the icons (asks for Automation permission on first run; skipped if it fails).
- **Signing (strongly recommended for other Macs)**: set `CODESIGN_IDENTITY` to sign the `.app` with a Developer ID and hardened runtime:
  `CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" ./build_dmg.sh`
- **Notarization (removes "unidentified developer")**: first store credentials with `xcrun notarytool store-credentials <profile>`, then set `NOTARY_PROFILE`:
  `NOTARY_PROFILE=<profile> ./build_dmg.sh` — runs `notarytool submit --wait` + `stapler staple` automatically.
- **Without signing**: the recipient must **right-click → Open** the first time, or run
  `xattr -dr com.apple.quarantine "/Applications/Drawthings Studio.app"`.
- Verify: `hdiutil verify "dist/Drawthings Studio.dmg"`.

## Environment variables (see .env.example)

> Config items have moved to the UI; the remaining env variables control storage / serving / logging.
> A `.env` file (project root, working dir or app support dir) is **loaded automatically at startup**
> (existing environment variables are not overridden).

| Variable | Description | Default |
|----------|-------------|---------|
| `DATA_DIR` | Directory for the database and media files | `./data` |
| `HOST` | Listen address (loopback only by default; `0.0.0.0` for LAN) | `127.0.0.1` |
| `PORT` | Listen port (non-numeric values fall back to the default with a warning) | `8010` |
| `RELOAD` | Dev hot-reload (`1` = on; the desktop client always uses `0`) | `0` |
| `LOG_LEVEL` | Log level: `DEBUG` / `INFO` / `WARNING` / `ERROR` | `INFO` |

## Prerequisites (real endpoints)

This app calls real services directly; there is no built-in mock mode:

1. **LLM**: any OpenAI-protocol endpoint (Ollama `http://127.0.0.1:11434/v1`, vLLM, cloud OpenAI, etc.);
   add a config in **⚙ Settings**; check "image input" per model capability (multimodal / text-only).
2. **Draw Things**: run the Draw Things app on the Mac and enable its HTTP server (see below);
   create a Draw Things config in **⚙ Settings** (the generation model is then picked in the create form).

When an endpoint is not running, the relevant step shows a **friendly error** on the project page (e.g. connection failed, service not enabled — localized per language);
fix it and re-run that step; already-generated content is unaffected.

> System proxy (Clash, etc.): local loopback endpoints (127.0.0.1 / localhost) **connect directly, bypassing the system proxy**
> (proxies often forward 127.* to a remote host, causing 502); cloud LLM endpoints keep the system proxy settings.

## Connecting to a real Draw Things

This app uses **only the Draw Things gRPC API** (set "API server" to gRPC in the app; default port 7859; enter the
endpoint as `host:port`). The HTTP API has been removed: it only returns a single still frame for video models.

### Configuration notes

- Depends on `drawthings-py` (already in `requirements.txt` as `drawthings-py[ffmpeg]`): it builds the FlatBuffer
  generation config, receives the **frame sequence**, and assembles video with ffmpeg (LTX etc. also return audio,
  playable in the browser). Video models here are **integrated audio+video** (LTX 2.3 generates both in one pass), so the
  model's audio is muxed as-is — never generated separately. The mp4 frame rate is the model's **native fps**, inferred
  per family (LTX 25 / Hunyuan 30 / SkyReels 24 / Wan 16) unless the preset sets `fps` explicitly: `drawthings-py`
  backfills `fps` with an unrelated schema default (5) for presets that omit it, and stamping 25 fps frames as 5 fps
  stretches the video 5× and leaves the audio covering only the start. After muxing, the file is verified with `ffprobe`
  (when available): if the audio ends well before the video, the fps is re-derived from the audio duration (`frames ÷
  audio`) and the file is re-muxed once — logged, never silent. (`tools/check_video_fps.py` covers this end to end.)
- A gRPC request **must carry the full generation config**, so the config specifies:
  - **image model / video model** (`model_image` / `model_video`): each may be empty — the effective model comes from the feature (project / micro-creation), falling back to the config.
    Click "Fetch models" to read the **downloaded models** from the app
    (gRPC `get_models`, with names and a video flag).
  - The generation **preset** (steps / sampler / size) is **inferred from the model name** (normalized match against
    drawthings-py preset models, ignoring quantization/version suffixes, e.g. `ltx_2.3_22b_distilled_1.1_q6p` →
    `ltx_2_3_distilled`, `flux_2_klein_9b_q6p` → `flux_2_klein_9b`), no input needed. A model with no matching preset
    gets a clear error.
- The model is validated (on the same connection) before generation, so a missing model errors out instead of quitting the app.
- Resolution: images = caller (agent) > preset, capped by `max_side` (longest side); **video is also capped by `max_side`**
  (0 = preset size. The LTX preset defaults to 1280×768 which is very VRAM-heavy — 25 frames took >10 min; use `max_side=768`
  → 768×448, ~90 s for 25 frames).
- Duration: the config's `max_seconds` (**default 8 = built-in cap**; 0 = use the built-in cap) is the upper bound; the **model may choose a shorter duration per request** (the Quick Create tool takes a `seconds` argument). Frames = seconds × fps, snapped to the model's **valid frame counts** (LTX: `8n+1`; Wan/Hunyuan etc.: `4n+1`), then bounded by the preset frame count and the **built-in 8s cap**. (E.g. with the LTX preset at fps=25: 2s = 49 frames, 4s = 97 frames ≈ 3.9s; longer requests are capped by the preset's 121 frames ≈ 4.84s.)
- Continuity: comics reference the previous image, dramas the last frame of the previous clip (extracted automatically);
  requires "Supports reference image" on the work (config value as fallback) — off = text-to-image / text-to-video.

Resolution priority (projects): the chapter's own width/height > the outline's **default resolution** > the agent's
per-scene choice during scripting (`ScriptOut.width/height`, multiples of 64); then capped by `max_side`.
