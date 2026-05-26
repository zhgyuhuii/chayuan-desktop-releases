/**
 * 能力快捷条 / Skill 模板定义。
 *
 * 与后端无对应表;前端常量(详见 docs/contracts.md §6)。
 *
 * 形态:每个 Skill 是"前端 prompt 模板 + 分类卡 + 进入跳转"的组合;
 * 实际对话仍走 /chat/v2/chat,由 SkillTemplate 拼好系统提示。
 */

export interface SkillCategory {
  id: string;
  /** i18n key 或字面量 */
  label: string;
}

export interface SkillTemplateItem {
  id: string;
  category: string;
  /** 中文标题 */
  title: string;
  /** 一行简介 */
  description: string;
  /** lucide icon 名 / emoji 兜底 */
  icon: string;
  /** 角标背景色 token */
  iconBg: string;
  /** 提交时拼到 query 前面的系统提示 */
  systemPrompt: string;
}

export interface SkillSpec {
  id: string;
  /** 路由用,如 /skill/write */
  slug: string;
  /** 顶部大标题(中文) */
  title: string;
  /** 顶部副标题 */
  subtitle: string;
  /** lucide icon */
  icon: string;
  /** 顶部分类 Tab(第一项默认选中,常为"推荐") */
  categories: SkillCategory[];
  /** 模板卡列表 */
  templates: SkillTemplateItem[];
  /** Composer placeholder(默认"请输入主题和写作要求...") */
  placeholder?: string;
}

const writeCategories: SkillCategory[] = [
  { id: 'recommended', label: '推荐' },
  { id: 'work', label: '工作' },
  { id: 'study', label: '学习/教育' },
  { id: 'business', label: '商业营销' },
  { id: 'rewrite', label: '改写和回复' },
  { id: 'literary', label: '文学艺术' },
];

const writeTemplates: SkillTemplateItem[] = [
  { id: 'polish', category: 'recommended', title: '润色', description: '让文字表达更出彩', icon: '✨', iconBg: '#7C3AED', systemPrompt: '你是一名资深中文编辑,请润色以下文字。' },
  { id: 'article', category: 'recommended', title: '文章', description: '撰写主流平台文章', icon: '📝', iconBg: '#2563EB', systemPrompt: '你是一名内容创作者,根据主题撰写一篇适合主流平台的中文文章。' },
  { id: 'research', category: 'recommended', title: '研究报告', description: '深度研究,精准分析', icon: '🔬', iconBg: '#0284C7', systemPrompt: '你是一名行业研究员,围绕给定主题撰写一份结构化研究报告。' },
  { id: 'work-summary', category: 'recommended', title: '工作总结', description: '凝练你的工作成效', icon: '📊', iconBg: '#10B981', systemPrompt: '你是一名职业写作助手,帮我凝练以下内容为一份工作总结。' },
  { id: 'thesis', category: 'recommended', title: '论文', description: '专业论文高效写作', icon: '🎓', iconBg: '#2563EB', systemPrompt: '你是一名学术写作助手,帮我撰写论文片段或修改既有论文。' },
  { id: 'essay', category: 'recommended', title: '作文', description: '专为学生打造满分作文', icon: '✍️', iconBg: '#3B82F6', systemPrompt: '你是一名中学语文老师,帮学生撰写满分作文。' },
  { id: 'novel', category: 'recommended', title: '小说', description: '创作引人入胜的小说', icon: '📚', iconBg: '#7C3AED', systemPrompt: '你是一名小说家,根据用户给的设定创作引人入胜的小说章节。' },
  { id: 'redbook', category: 'recommended', title: '小红书', description: '打造吸睛的小红书', icon: '📕', iconBg: '#EF4444', systemPrompt: '你是一名小红书爆款笔记作者,根据主题写一篇带 emoji、口语化的笔记。' },
  { id: 'application', category: 'work', title: '申请书', description: '轻松生成各类申请', icon: '📋', iconBg: '#059669', systemPrompt: '你是一名公文助手,帮我撰写正式申请书。' },
  { id: 'speech', category: 'work', title: '话术', description: '多场景万能沟通宝典', icon: '💬', iconBg: '#F59E0B', systemPrompt: '你是一名沟通教练,根据场景生成可直接使用的中文话术。' },
  { id: 'weekly', category: 'work', title: '周报/月报', description: '凝练本周期工作成果', icon: '🗓️', iconBg: '#10B981', systemPrompt: '你是一名职场写作助手,帮我把工作流水生成周报或月报。' },
  { id: 'daily', category: 'work', title: '日报', description: '每日工作清晰总结', icon: '📅', iconBg: '#0EA5E9', systemPrompt: '你是一名职场写作助手,帮我把当日要点生成日报。' },
];

export const SKILLS: ReadonlyArray<SkillSpec> = [
  {
    id: 'write',
    slug: 'write',
    title: 'AI写作',
    subtitle: '让 AI 成为你的写作伙伴,持续完善每一个想法',
    icon: '✍️',
    categories: writeCategories,
    templates: writeTemplates,
    placeholder: '请输入主题和写作要求...',
  },
  {
    id: 'translate',
    slug: 'translate',
    title: 'AI翻译',
    subtitle: '中英日韩等多语种互译,保留语境',
    icon: '🌐',
    categories: [
      { id: 'recommended', label: '推荐' },
      { id: 'document', label: '文档翻译' },
      { id: 'live', label: '会话同传' },
    ],
    templates: [
      { id: 'zh2en', category: 'recommended', title: '中译英', description: '专业中译英', icon: '🔄', iconBg: '#2563EB', systemPrompt: '请把以下中文准确翻译为英文,保留语气与上下文。' },
      { id: 'en2zh', category: 'recommended', title: '英译中', description: '地道英译中', icon: '🔄', iconBg: '#0EA5E9', systemPrompt: '请把以下英文准确翻译为中文,保持自然流畅。' },
      { id: 'ja2zh', category: 'recommended', title: '日译中', description: '日译中', icon: '🇯🇵', iconBg: '#EF4444', systemPrompt: '请把以下日文准确翻译为中文。' },
    ],
    placeholder: '粘贴需要翻译的内容...',
  },
  {
    id: 'memo',
    slug: 'memo',
    title: 'AI妙记',
    subtitle: '会议 / 课堂转写 + 智能摘要',
    icon: '📝',
    categories: [
      { id: 'recommended', label: '推荐' },
      { id: 'meeting', label: '会议' },
      { id: 'class', label: '课堂' },
    ],
    templates: [
      { id: 'minutes', category: 'recommended', title: '会议纪要', description: '从录音转写到结构化纪要', icon: '📋', iconBg: '#10B981', systemPrompt: '从下面的会议转写中生成结构化中文会议纪要,包含决议项、待办项与责任人。' },
      { id: 'class-note', category: 'recommended', title: '课堂笔记', description: '课程要点 + 重难点', icon: '📚', iconBg: '#2563EB', systemPrompt: '把课堂转写整理成层次清晰的中文课堂笔记。' },
    ],
    placeholder: '上传录音 / 粘贴转写文本...',
  },
  {
    id: 'subtitle',
    slug: 'subtitle',
    title: '同传字幕',
    subtitle: '实时多语字幕',
    icon: '🎬',
    categories: [{ id: 'recommended', label: '推荐' }],
    templates: [
      { id: 'live-subtitle', category: 'recommended', title: '实时字幕', description: '麦克风输入即时翻译', icon: '🎤', iconBg: '#7C3AED', systemPrompt: '把麦克风输入实时翻译为目标语言并以字幕形式输出。' },
    ],
    placeholder: '点击麦克风开始实时字幕…',
  },
  {
    id: 'image-edit',
    slug: 'image-edit',
    title: 'AI修图',
    subtitle: '智能修图、风格化、抠图',
    icon: '🎨',
    categories: [{ id: 'recommended', label: '推荐' }],
    templates: [
      { id: 'enhance', category: 'recommended', title: '一键增强', description: '画质提升 + 降噪', icon: '✨', iconBg: '#0EA5E9', systemPrompt: '对上传的图片进行一键增强:提升清晰度、降噪、补偿曝光。' },
      { id: 'style', category: 'recommended', title: '国潮风格', description: '生成国潮头像', icon: '🎭', iconBg: '#EF4444', systemPrompt: '把图片中的人物按国潮风格再创作一张头像。' },
    ],
    placeholder: '上传图片或描述需求…',
  },
  {
    id: 'control',
    slug: 'control',
    title: 'AI操控',
    subtitle: 'AI 帮你操控应用与系统',
    icon: '🤖',
    categories: [{ id: 'recommended', label: '推荐' }],
    templates: [
      { id: 'launch-app', category: 'recommended', title: '启动应用', description: '一句话打开本地应用', icon: '🚀', iconBg: '#10B981', systemPrompt: '把用户意图解析为本地应用启动指令。' },
    ],
    placeholder: '请描述你想让 AI 做什么…',
  },
];

export function findSkill(slug: string): SkillSpec | undefined {
  return SKILLS.find((s) => s.slug === slug);
}
