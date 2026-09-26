"""SSE 事件契约（后端单一来源）。

前端镜像：`static/spa/js/events.js`（`window.EVENTS`）。两边必须保持一致；
新增/改名事件时同时改这两处与消费方，避免「后端改名、前端漏改」。

事件分两类：
- 传输/引擎事件：token / tool / tool_status / media / tool_error / error / done
- 流水线进度事件：progress / chapter / score
"""

# ---- 微创作会话流事件 ----
TOKEN = "token"            # 文本增量 {text}
TOOL = "tool"              # 开始一次生成 {id,label,prompt,media}
TOOL_STATUS = "tool_status"  # 生成中状态文案 {id,message}
MEDIA = "media"            # 生成结果 {id,media,url,prompt}
MEDIA_SCORE = "media_score"  # 生成后自动评分结果 {id,score,note}
FIELDS = "fields"          # 新建创作：AI 写入表单字段 {title,origin}
TOOL_ERROR = "tool_error"  # 生成失败 {id,message,prompt}
ERROR = "error"           # 会话错误 {message}
DONE = "done"             # 结束 {}

# ---- 流水线进度事件（项目侧逐章推进）----
PROGRESS = "progress"     # {current,total,title}
CHAPTER = "chapter"       # 单章进度
SCORE = "score"           # 评分进度

# ---- 传输层内部标记（不出现在线路上）----
EOF = "__eof__"

__all__ = [
    "TOKEN", "TOOL", "TOOL_STATUS", "MEDIA", "MEDIA_SCORE", "FIELDS", "TOOL_ERROR", "ERROR", "DONE",
    "PROGRESS", "CHAPTER", "SCORE", "EOF",
]
