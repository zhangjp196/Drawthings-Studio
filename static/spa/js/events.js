// SSE 事件契约（前端镜像；后端单一来源见 services/events.py）
// 新增/改名事件时，同步改两处与消费方，避免「后端改名、前端漏改」。
window.EVENTS = {
  // 微创作会话流
  TOKEN: 'token',
  TOOL: 'tool',
  TOOL_STATUS: 'tool_status',
  MEDIA: 'media',
  MEDIA_SCORE: 'media_score',
  FIELDS: 'fields',
  TOOL_ERROR: 'tool_error',
  ERROR: 'error',
  DONE: 'done',
  // 流水线进度（项目侧逐章推进）
  PROGRESS: 'progress',
  CHAPTER: 'chapter',
  SCORE: 'score',
};
