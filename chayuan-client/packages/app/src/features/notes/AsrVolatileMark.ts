/**
 * Tiptap mark — "ASR 未确认尾部"。
 *
 * 流式 ASR 体验需要"实时显示当前 whisper 猜测 + 后续校准"。volatile 文字带这个
 * mark 时:
 *   - 视觉上灰色斜体,跟已确认文字明显区分
 *   - 下次 chunk 来时,NoteEditor 通过 mark 范围找到 volatile,delete + 重新 insert
 *     当前 hypothesis 的新 volatile,实现"末尾持续修正"的实时体验
 *   - 用户主动 stop 录音时,volatile 整段被 delete(因为它本来就没确认)
 *
 * 设计要点:
 *   - mark 名 `asrVolatile`,Tiptap commands 可以 setMark/unsetMark/toggleMark
 *   - inclusive: false → 在 mark 范围**末尾**之后输入新字时,新字不继承 mark
 *     (避免用户中途切到键盘输入时,新输入的字也变灰)
 *   - excludes: '_' → 不和其他 mark 互斥,可与 bold/italic 共存
 *   - parseHTML / renderHTML:文本里直接写 <span class="cy-asr-volatile">...</span>
 *     就能保留(虽然实际不应该被 save,而是 stop 时清掉)
 */
import { Mark, mergeAttributes } from '@tiptap/core';

export const AsrVolatileMark = Mark.create({
  name: 'asrVolatile',
  inclusive: false,
  excludes: '_',
  parseHTML() {
    return [{ tag: 'span.cy-asr-volatile' }];
  },
  renderHTML({ HTMLAttributes }) {
    return [
      'span',
      mergeAttributes(HTMLAttributes, { class: 'cy-asr-volatile' }),
      0,
    ];
  },
});
