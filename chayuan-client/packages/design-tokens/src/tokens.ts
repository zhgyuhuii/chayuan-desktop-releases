/**
 * 察元设计 Token —— 单源(TypeScript 常量)。
 *
 * 同源派生:
 *   - tokens.css        : 浅 / 深 CSS variables(运行时主题切换)
 *   - tokens.dtcg.json  : DTCG 标准导出(供 Figma / 设计工具)
 *
 * 数值单位:px(spacing/radius)、HSL string(color)、ms(motion);消费方按需转换。
 *
 * 设计依据:`chayuan-server-frontend/界面参考/*.jpg`(2026-04 整理)。
 */

export const colors = {
  /** 主色:CTA 蓝 — 来自登录页"下一步"按钮 #3D7BFF */
  brand: {
    50: '#EEF4FF',
    100: '#D9E5FF',
    200: '#B3CBFF',
    300: '#7CA7FF',
    400: '#4F87FF',
    500: '#3D7BFF',
    600: '#2563EB',
    700: '#1D4ED8',
    800: '#1E40AF',
    900: '#1E3A8A',
  },
  /** 选中黑 pill — 来自 Tab/分类选中态 */
  ink: {
    50: '#F4F4F5',
    100: '#E4E4E7',
    200: '#D4D4D8',
    300: '#A1A1AA',
    400: '#52525B',
    500: '#27272A',
    600: '#18181B',
    700: '#0A0A0A',
    800: '#000000',
    900: '#000000',
  },
  /** 紫蓝渐变 — 欢迎球 / 用户头像 */
  iris: {
    from: '#A78BFA',
    via: '#7C3AED',
    to: '#4F46E5',
  },
  /** 霓虹光晕(Composer 外发光)*/
  glow: {
    pink: '#FFB6C1',
    blue: '#7CB7FF',
    green: '#86E6BE',
  },
  /** 角标 New / Hot */
  badge: {
    hot: '#F97316',
    new: '#10B981',
  },
} as const;

export const radius = {
  none: 0,
  sm: 4,
  md: 8,
  lg: 12,
  /** 卡片标准 */
  xl: 16,
  '2xl': 20,
  /** Composer */
  '3xl': 24,
  /** Pill */
  full: 9999,
} as const;

/** 8 倍数栅格 */
export const spacing = {
  px: 1,
  0: 0,
  0.5: 2,
  1: 4,
  1.5: 6,
  2: 8,
  3: 12,
  4: 16,
  5: 20,
  6: 24,
  8: 32,
  10: 40,
  12: 48,
  16: 64,
  20: 80,
  24: 96,
} as const;

export const fontFamily = {
  sans: ['"PingFang SC"', '"HarmonyOS Sans"', '"Microsoft YaHei"', 'system-ui', 'sans-serif'],
  mono: ['"JetBrains Mono"', '"SF Mono"', 'Consolas', 'monospace'],
} as const;

/** 行高比例固定 1.5,字号成等比 */
export const fontSize = {
  xs: 12,
  sm: 13,
  base: 14,
  md: 16,
  lg: 18,
  xl: 20,
  '2xl': 24,
  '3xl': 30,
  '4xl': 36,
} as const;

export const fontWeight = {
  regular: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
} as const;

export const shadow = {
  /** 卡片轻浮 */
  sm: '0 1px 2px 0 rgba(0,0,0,0.04)',
  md: '0 2px 8px 0 rgba(0,0,0,0.06)',
  lg: '0 8px 24px 0 rgba(0,0,0,0.08)',
  /** 弹层 */
  popover: '0 8px 32px 0 rgba(0,0,0,0.12)',
} as const;

/** Composer 霓虹外发光 — radial gradient 三色叠加 */
export const glow = {
  composer:
    'radial-gradient(120% 200% at 0% 100%, rgba(255,182,193,0.55) 0%, transparent 50%),' +
    'radial-gradient(120% 200% at 100% 100%, rgba(124,183,255,0.55) 0%, transparent 50%),' +
    'radial-gradient(140% 200% at 50% -20%, rgba(134,230,190,0.45) 0%, transparent 55%)',
  /** 模糊量,搭配 filter: blur() 使用 */
  blur: {
    sm: 16,
    md: 24,
    lg: 40,
  },
} as const;

export const motion = {
  duration: {
    fast: 120,
    normal: 200,
    slow: 320,
  },
  easing: {
    standard: 'cubic-bezier(0.2, 0, 0, 1)',
    emphasized: 'cubic-bezier(0.2, 0, 0.2, 1.4)',
  },
} as const;

export const zIndex = {
  base: 0,
  sticky: 10,
  dropdown: 50,
  modal: 100,
  popover: 200,
  tooltip: 300,
  toast: 400,
} as const;

/** 字号比例 -2..+2(用户字号滑动条用,基线 14px → 14*scale) */
export const fontScale = [0.85, 0.92, 1, 1.08, 1.16] as const;

export type Tokens = {
  colors: typeof colors;
  radius: typeof radius;
  spacing: typeof spacing;
  fontFamily: typeof fontFamily;
  fontSize: typeof fontSize;
  fontWeight: typeof fontWeight;
  shadow: typeof shadow;
  glow: typeof glow;
  motion: typeof motion;
  zIndex: typeof zIndex;
};

export const tokens: Tokens = {
  colors,
  radius,
  spacing,
  fontFamily,
  fontSize,
  fontWeight,
  shadow,
  glow,
  motion,
  zIndex,
};
