/**
 * 事件命名常量。所有 logEvent 必须使用此处的常量，避免散落字符串拼写漂移。
 */

export const Events = {
  AppBoot: 'app.boot',
  AppFocus: 'app.focus',
  AppBlur: 'app.blur',
  AuthLogin: 'auth.login',
  AuthLogout: 'auth.logout',
  AuthRefresh: 'auth.refresh',
  ChatSend: 'chat.send',
  ChatFirstToken: 'chat.first_token',
  ChatComplete: 'chat.complete',
  ChatAborted: 'chat.aborted',
  ChatRegenerated: 'chat.regenerated',
  ChatEdited: 'chat.edited',
  ChatFeedback: 'chat.feedback',
  ToolApproved: 'tool.approved',
  ToolRejected: 'tool.rejected',
  ToolModified: 'tool.modified',
  ComposerModelChange: 'composer.model_change',
  ComposerAttachmentAdd: 'composer.attachment_add',
  ComposerAttachmentRemove: 'composer.attachment_remove',
  PerfFpsDrop: 'perf.fps_drop',
  ErrorRender: 'error.render',
  ErrorNetwork: 'error.network',
  ErrorParse: 'error.parse',
} as const;

export type EventName = (typeof Events)[keyof typeof Events];
