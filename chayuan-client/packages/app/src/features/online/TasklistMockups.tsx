/**
 * TasklistMockups —— 「我的待办」深度介绍页用的 SVG 示意截图(mockup)。
 *
 * 为什么是手绘 SVG 而不是真实截图:
 *   「我的待办」是在线办公能力,单机版里跑不起来,没法真截图。这里用风格化
 *   的 SVG 把在线版的关键界面画成「示意图」—— 像素化但能看懂是什么界面。
 *
 * 三张图严格对应在线版 chayuan-client 的真实组件:
 *   - <TasklistViewsMock>  ← TasklistPage.tsx:4 视图 Tab + 紧急置顶 + 任务卡片
 *   - <ConversationalFormMock> ← TaskChatPage.tsx:对话气泡 + 嵌入式表单卡 + ✨ 自动填入
 *   - <FlowHandoffMock>    ← human_task 节点流转(orchestration §22:挂起→认领→提交→续跑)
 *
 * 上色只用 var(--cy-*) 主题变量,深浅色主题都能看;不引外链网图。
 */

import * as React from 'react';

/** 三张 mockup 共用的画布尺寸,16:10 视窗比例。 */
const VB_W = 320;
const VB_H = 200;

const baseSvgProps = {
  viewBox: `0 0 ${VB_W} ${VB_H}`,
  xmlns: 'http://www.w3.org/2000/svg',
  className: 'block h-auto w-full',
  role: 'img' as const,
};

/** 窗口外框 + 顶部三个红绿灯点,所有 mockup 共用。 */
const WindowChrome: React.FC<{ children: React.ReactNode; title?: string }> = ({
  children,
  title,
}) => (
  <>
    <rect
      x={0.5}
      y={0.5}
      width={VB_W - 1}
      height={VB_H - 1}
      rx={10}
      fill="var(--cy-surface-base)"
      stroke="var(--cy-border-subtle)"
    />
    {/* 标题栏 */}
    <rect x={1} y={1} width={VB_W - 2} height={20} rx={10} fill="var(--cy-surface-1)" />
    <rect x={1} y={12} width={VB_W - 2} height={10} fill="var(--cy-surface-1)" />
    <circle cx={13} cy={11} r={2.6} fill="var(--cy-text-tertiary)" opacity={0.5} />
    <circle cx={23} cy={11} r={2.6} fill="var(--cy-text-tertiary)" opacity={0.35} />
    <circle cx={33} cy={11} r={2.6} fill="var(--cy-text-tertiary)" opacity={0.35} />
    {title ? (
      <text x={VB_W / 2} y={14.5} textAnchor="middle" fontSize={8} fill="var(--cy-text-tertiary)">
        {title}
      </text>
    ) : null}
    {children}
  </>
);

/**
 * 图 1 —— 4 视图任务列表。
 * 对应 TasklistPage.tsx:SegmentedTabs 4 Tab + 「紧急 · 距截止 < 4h」置顶分组 + 任务卡片。
 */
export const TasklistViewsMock: React.FC = () => (
  <svg {...baseSvgProps} aria-label="我的待办四视图任务列表示意图">
    <WindowChrome title="我的待办">
      {/* 4 视图 Tab 条 */}
      <rect x={14} y={30} width={292} height={18} rx={9} fill="var(--cy-surface-1)" />
      <rect x={16} y={32} width={70} height={14} rx={7} fill="var(--cy-brand-500)" />
      <text x={51} y={41.5} textAnchor="middle" fontSize={7} fill="#fff">
        待我处理
      </text>
      <text x={123} y={41.5} textAnchor="middle" fontSize={7} fill="var(--cy-text-tertiary)">
        我发起的
      </text>
      <text x={195} y={41.5} textAnchor="middle" fontSize={7} fill="var(--cy-text-tertiary)">
        组待认领
      </text>
      <text x={267} y={41.5} textAnchor="middle" fontSize={7} fill="var(--cy-text-tertiary)">
        已完成
      </text>

      {/* 紧急分组标题 */}
      <circle cx={18} cy={60} r={2.4} fill="var(--cy-danger-500, #e11d48)" />
      <text x={25} y={62.5} fontSize={7} fill="var(--cy-danger-500, #e11d48)" fontWeight="600">
        紧急 · 距截止 &lt; 4h
      </text>

      {/* 紧急任务卡(红条) */}
      <rect
        x={14}
        y={68}
        width={292}
        height={34}
        rx={7}
        fill="var(--cy-surface-1)"
        stroke="var(--cy-danger-500, #e11d48)"
        strokeOpacity={0.5}
      />
      <rect x={14} y={68} width={3.5} height={34} rx={1.5} fill="var(--cy-danger-500, #e11d48)" />
      <rect x={26} y={76} width={18} height={18} rx={5} fill="var(--cy-brand-500)" opacity={0.16} />
      <path
        d="M35 80.5v4l2.6 1.6"
        stroke="var(--cy-brand-600)"
        strokeWidth={1.6}
        strokeLinecap="round"
        fill="none"
      />
      <circle cx={35} cy={85} r={5.4} stroke="var(--cy-brand-600)" strokeWidth={1.6} fill="none" />
      <rect x={52} y={76} width={120} height={6} rx={3} fill="var(--cy-text-primary)" opacity={0.82} />
      <rect x={52} y={87} width={150} height={4.5} rx={2.25} fill="var(--cy-text-tertiary)" opacity={0.55} />
      <rect x={246} y={78} width={48} height={16} rx={8} fill="var(--cy-brand-500)" />
      <text x={270} y={88.5} textAnchor="middle" fontSize={7} fill="#fff">
        去处理
      </text>

      {/* 其他分组标题 */}
      <text x={18} y={117} fontSize={7} fill="var(--cy-text-tertiary)">
        其他
      </text>

      {/* 普通任务卡 ×2 */}
      {[124, 162].map((y, i) => (
        <g key={y}>
          <rect
            x={14}
            y={y}
            width={292}
            height={32}
            rx={7}
            fill="var(--cy-surface-base)"
            stroke="var(--cy-border-subtle)"
          />
          <rect x={26} y={y + 7} width={18} height={18} rx={5} fill="var(--cy-surface-2)" />
          <path
            d={`M35 ${y + 11.5}v4l2.6 1.6`}
            stroke="var(--cy-text-tertiary)"
            strokeWidth={1.5}
            strokeLinecap="round"
            fill="none"
          />
          <circle
            cx={35}
            cy={y + 16}
            r={5.2}
            stroke="var(--cy-text-tertiary)"
            strokeWidth={1.5}
            fill="none"
          />
          <rect
            x={52}
            y={y + 9}
            width={i === 0 ? 96 : 78}
            height={5.5}
            rx={2.75}
            fill="var(--cy-text-primary)"
            opacity={0.7}
          />
          <rect
            x={52}
            y={y + 19}
            width={i === 0 ? 138 : 120}
            height={4}
            rx={2}
            fill="var(--cy-text-tertiary)"
            opacity={0.5}
          />
          <rect
            x={246}
            y={y + 8}
            width={48}
            height={16}
            rx={8}
            fill="var(--cy-surface-1)"
            stroke="var(--cy-border-default)"
          />
          <text
            x={270}
            y={y + 18.5}
            textAnchor="middle"
            fontSize={7}
            fill="var(--cy-text-secondary)"
          >
            去处理
          </text>
        </g>
      ))}
    </WindowChrome>
  </svg>
);

/**
 * 图 2 —— 对话式表单填写。
 * 对应 TaskChatPage.tsx:助手寒暄气泡 + 用户口述气泡 + 嵌入式「待办表单」卡 +
 * ✨ AI 自动填入标记 + 底部 composer。
 */
export const ConversationalFormMock: React.FC = () => (
  <svg {...baseSvgProps} aria-label="对话式表单填写示意图">
    <WindowChrome title="处理待办 · 对话式表单">
      {/* 助手气泡 */}
      <rect x={14} y={30} width={196} height={26} rx={7} fill="var(--cy-surface-1)" />
      <rect x={22} y={37} width={140} height={4.5} rx={2.25} fill="var(--cy-text-secondary)" opacity={0.7} />
      <rect x={22} y={45} width={170} height={4.5} rx={2.25} fill="var(--cy-text-tertiary)" opacity={0.5} />

      {/* 用户气泡(右,品牌色) */}
      <rect x={150} y={61} width={156} height={20} rx={7} fill="var(--cy-brand-500)" />
      <rect x={158} y={68} width={120} height={4.5} rx={2.25} fill="#fff" opacity={0.92} />
      <rect x={158} y={75.5} width={64} height={3.5} rx={1.75} fill="#fff" opacity={0.6} />

      {/* 嵌入式表单卡 */}
      <rect
        x={14}
        y={87}
        width={246}
        height={94}
        rx={8}
        fill="var(--cy-surface-1)"
        stroke="var(--cy-border-subtle)"
      />
      {/* 卡头:📋 待办表单 + 填写中徽章 */}
      <circle cx={24} cy={97} r={5} fill="var(--cy-brand-500)" />
      <rect x={21.5} y={94.5} width={5} height={5} rx={1} fill="#fff" opacity={0.9} />
      <rect x={33} y={94} width={42} height={6} rx={3} fill="var(--cy-text-primary)" opacity={0.8} />
      <rect x={82} y={93.5} width={28} height={9} rx={4.5} fill="var(--cy-brand-500)" opacity={0.16} />
      <text x={96} y={100} textAnchor="middle" fontSize={5.5} fill="var(--cy-brand-700)">
        填写中
      </text>

      {/* 字段 1:已 AI 填入(带 ✨) */}
      <rect x={24} y={110} width={36} height={4} rx={2} fill="var(--cy-text-secondary)" opacity={0.7} />
      <rect x={64} y={108} width={26} height={8} rx={4} fill="var(--cy-brand-500)" opacity={0.14} />
      <text x={77} y={114} textAnchor="middle" fontSize={5} fill="var(--cy-brand-700)">
        ✨ AI
      </text>
      <rect
        x={24}
        y={117}
        width={224}
        height={13}
        rx={4}
        fill="var(--cy-surface-base)"
        stroke="var(--cy-brand-400)"
      />
      <rect x={30} y={122} width={86} height={4} rx={2} fill="var(--cy-text-primary)" opacity={0.6} />

      {/* 字段 2:待填 */}
      <rect x={24} y={137} width={48} height={4} rx={2} fill="var(--cy-text-secondary)" opacity={0.7} />
      <rect
        x={24}
        y={144}
        width={224}
        height={13}
        rx={4}
        fill="var(--cy-surface-base)"
        stroke="var(--cy-border-subtle)"
      />

      {/* 提交按钮 */}
      <rect x={24} y={164} width={104} height={4} rx={2} fill="var(--cy-text-tertiary)" opacity={0.5} />
      <rect x={196} y={161} width={52} height={13} rx={6.5} fill="var(--cy-brand-500)" />
      <text x={222} y={169.5} textAnchor="middle" fontSize={6} fill="#fff">
        提交完成
      </text>

      {/* 右侧 composer 提示 */}
      <rect
        x={266}
        y={87}
        width={40}
        height={94}
        rx={8}
        fill="var(--cy-surface-base)"
        stroke="var(--cy-border-subtle)"
        strokeDasharray="3 3"
      />
      <text x={286} y={130} textAnchor="middle" fontSize={6} fill="var(--cy-text-tertiary)">
        口述
      </text>
      <text x={286} y={140} textAnchor="middle" fontSize={6} fill="var(--cy-text-tertiary)">
        即填
      </text>

      {/* 底部输入框 */}
      <rect
        x={14}
        y={186}
        width={292}
        height={9}
        rx={4.5}
        fill="var(--cy-surface-1)"
        stroke="var(--cy-border-subtle)"
      />
      <text x={22} y={192.5} fontSize={5.5} fill="var(--cy-text-tertiary)">
        直接说出表单内容,或问助手…
      </text>
    </WindowChrome>
  </svg>
);

/**
 * 图 3 —— 流程流转 / 人工节点。
 * 对应 human_task 节点(orchestration §22):AI 自动节点跑到「人工节点」时挂起,
 * 派单给某人/某组,认领→提交→流程续跑;紧接的流程恢复时间线。
 */
export const FlowHandoffMock: React.FC = () => (
  <svg {...baseSvgProps} aria-label="人工节点流程流转示意图">
    <WindowChrome title="流程处理 · 人工节点">
      {/* 流程链:AI 节点 → 人工节点 → AI 节点 */}
      {/* 节点 1:AI 自动 */}
      <rect x={18} y={36} width={70} height={30} rx={7} fill="var(--cy-surface-1)" stroke="var(--cy-border-subtle)" />
      <circle cx={32} cy={51} r={6} fill="var(--cy-brand-500)" opacity={0.18} />
      <path
        d="M32 47.5l1.3 2.7 2.9.3-2.1 2 .6 2.8-2.6-1.4-2.6 1.4.6-2.8-2.1-2 2.9-.3z"
        fill="var(--cy-brand-600)"
      />
      <rect x={43} y={45} width={36} height={5} rx={2.5} fill="var(--cy-text-primary)" opacity={0.7} />
      <rect x={43} y={54} width={28} height={4} rx={2} fill="var(--cy-text-tertiary)" opacity={0.5} />

      {/* 连线 1 → 2 */}
      <path d="M88 51h22" stroke="var(--cy-border-default)" strokeWidth={1.6} />
      <path d="M108 48l4 3-4 3" fill="none" stroke="var(--cy-border-default)" strokeWidth={1.6} />

      {/* 节点 2:人工节点(高亮,品红描边) */}
      <rect
        x={113}
        y={32}
        width={94}
        height={38}
        rx={8}
        fill="var(--cy-surface-base)"
        stroke="var(--cy-brand-500)"
        strokeWidth={2}
      />
      <circle cx={128} cy={51} r={7} fill="var(--cy-brand-500)" opacity={0.16} />
      <circle cx={128} cy={48} r={2.6} fill="var(--cy-brand-600)" />
      <path d="M123 56c0-3 2.4-4.6 5-4.6s5 1.6 5 4.6" fill="var(--cy-brand-600)" />
      <rect x={140} y={40} width={52} height={5.5} rx={2.75} fill="var(--cy-text-primary)" opacity={0.82} />
      <rect x={140} y={49} width={44} height={4} rx={2} fill="var(--cy-text-tertiary)" opacity={0.55} />
      <rect x={140} y={56} width={34} height={9} rx={4.5} fill="var(--cy-brand-500)" opacity={0.16} />
      <text x={157} y={62.5} textAnchor="middle" fontSize={5.5} fill="var(--cy-brand-700)">
        等待提交
      </text>

      {/* 连线 2 → 3 */}
      <path d="M207 51h22" stroke="var(--cy-border-default)" strokeWidth={1.6} strokeDasharray="3 2.5" />
      <path d="M227 48l4 3-4 3" fill="none" stroke="var(--cy-border-default)" strokeWidth={1.6} />

      {/* 节点 3:AI 续跑 */}
      <rect x={232} y={36} width={72} height={30} rx={7} fill="var(--cy-surface-1)" stroke="var(--cy-border-subtle)" />
      <circle cx={246} cy={51} r={6} fill="var(--cy-brand-500)" opacity={0.12} />
      <path
        d="M246 47.5l1.3 2.7 2.9.3-2.1 2 .6 2.8-2.6-1.4-2.6 1.4.6-2.8-2.1-2 2.9-.3z"
        fill="var(--cy-text-tertiary)"
      />
      <rect x={257} y={45} width={38} height={5} rx={2.5} fill="var(--cy-text-secondary)" opacity={0.6} />
      <rect x={257} y={54} width={26} height={4} rx={2} fill="var(--cy-text-tertiary)" opacity={0.45} />

      {/* 流转动作行:派单 / 认领 / 提交 / 退回 */}
      <text x={18} y={88} fontSize={7} fill="var(--cy-text-tertiary)" fontWeight="600">
        任务流转
      </text>
      {[
        { x: 18, label: '派单', sub: '按身份/组' },
        { x: 92, label: '认领', sub: '组任务领取' },
        { x: 166, label: '提交', sub: '续跑流程' },
        { x: 240, label: '退回', sub: '复审/转派' },
      ].map((s) => (
        <g key={s.label}>
          <rect
            x={s.x}
            y={94}
            width={64}
            height={24}
            rx={6}
            fill="var(--cy-surface-1)"
            stroke="var(--cy-border-subtle)"
          />
          <circle cx={s.x + 12} cy={106} r={4.2} fill="var(--cy-brand-500)" opacity={0.18} />
          <circle cx={s.x + 12} cy={106} r={1.8} fill="var(--cy-brand-600)" />
          <text x={s.x + 22} y={104} fontSize={6.5} fill="var(--cy-text-primary)" fontWeight="600">
            {s.label}
          </text>
          <text x={s.x + 22} y={113} fontSize={5} fill="var(--cy-text-tertiary)">
            {s.sub}
          </text>
        </g>
      ))}

      {/* 流程恢复时间线 */}
      <rect
        x={18}
        y={126}
        width={286}
        height={62}
        rx={7}
        fill="var(--cy-surface-1)"
        stroke="var(--cy-border-subtle)"
      />
      <text x={26} y={138} fontSize={6} fill="var(--cy-text-tertiary)" letterSpacing="1">
        流程恢复时间线
      </text>
      {[
        { y: 148, label: '用户输入 · 知识库检索', done: true },
        { y: 160, label: 'LLM 分析 → 需补充资料', done: true },
        { y: 172, label: '等待你提交(本步骤)', done: false },
      ].map((row) => (
        <g key={row.y}>
          <circle
            cx={30}
            cy={row.y}
            r={3}
            fill={row.done ? 'var(--cy-brand-500)' : 'var(--cy-surface-base)'}
            stroke={row.done ? 'var(--cy-brand-500)' : 'var(--cy-brand-500)'}
            strokeWidth={1.4}
          />
          {row.done ? (
            <path
              d={`M28.2 ${row.y}l1.3 1.3 2.4-2.6`}
              stroke="#fff"
              strokeWidth={1.2}
              fill="none"
              strokeLinecap="round"
            />
          ) : null}
          <rect
            x={40}
            y={row.y - 2.2}
            width={row.done ? 150 : 122}
            height={4.4}
            rx={2.2}
            fill={row.done ? 'var(--cy-text-tertiary)' : 'var(--cy-brand-600)'}
            opacity={row.done ? 0.5 : 0.85}
          />
        </g>
      ))}
      {/* 时间线竖线 */}
      <path
        d="M30 151v18"
        stroke="var(--cy-border-default)"
        strokeWidth={1}
        strokeDasharray="2 2"
      />
    </WindowChrome>
  </svg>
);
