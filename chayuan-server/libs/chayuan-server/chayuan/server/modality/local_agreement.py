"""LocalAgreement-N 算法 — 流式 ASR 用前 N 次 hypothesis 的共同前缀做 commit 决策。

来源:Polák et al., IWSLT 2022 "TurboTranscribe"。OpenAI whisper-streaming 也用同款。

核心思想:
    把流式 ASR 拆成 "稳定部分(committed)+ 不稳定部分(volatile)"。
    每收到一个新 chunk,把累积音频整段送 whisper 拿到 new_hypothesis;
    比对 last_hypothesis 和 new_hypothesis 的共同前缀,共同的部分 = N=2 次都
    认可,可以 commit 到笔记里。后面不一致的 suffix = whisper 还在犹豫,等
    下一个 chunk 再说。

这解决了"chunk-by-chunk 转写不准"的核心痛点:
  - 单 chunk(2.5s)内 whisper 上下文不足,容易听错(尤其中文同音字)
  - 累积音频跑(5-15s)+ 跨次比对,识别质量接近 offline 转写

字符级 vs 词级:
  英文用 word 比对(按空格 split)更合理;中文没明确词边界,字符级也 OK。
  这里用字符级(中文/英文统一,代码简单),实测中文场景效果够用。
"""
from __future__ import annotations

from typing import Tuple


def common_prefix_len(a: str, b: str) -> int:
    """两个字符串的最长共同前缀长度(以 Python char,即 grapheme code unit 算)。"""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def local_agreement_2(prev_hyp: str, curr_hyp: str) -> Tuple[str, str]:
    """LocalAgreement-2:返 (stable_prefix, volatile_suffix)。

    stable_prefix = prev_hyp 和 curr_hyp 的共同前缀(从 curr_hyp 取);
    volatile_suffix = curr_hyp 减去 stable_prefix。

    第一次调用(prev_hyp 空):全部归 volatile,等下次再 confirm。
    """
    if not prev_hyp:
        return "", curr_hyp
    n = common_prefix_len(prev_hyp, curr_hyp)
    return curr_hyp[:n], curr_hyp[n:]


def compute_increment(
    prev_committed: str,
    new_common_prefix: str,
) -> Tuple[str, bool]:
    """算"这次 commit 新增的部分" = new_common_prefix - prev_committed。

    返 (increment, retroactive_change):
      - increment:新 commit 的字符(前端 insert 到笔记尾部)
      - retroactive_change=True:whisper 改了之前已 commit 的内容(罕见,但要
        告诉 caller,通常意味着 last commit 用力太猛 / window slide 出问题)

    正常情况(new_common_prefix 是 prev_committed 的扩展):
      prev = "你好今天"
      new  = "你好今天天气"
      → increment = "天气", retro = False

    异常(whisper 改写了已 commit 的字):
      prev = "你好今天"
      new  = "你好昨天"  # 之前 commit 的"今"被改成"昨"
      → increment = "", retro = True(前端可弹提示但不撤销已写入文字)
    """
    if new_common_prefix.startswith(prev_committed):
        return new_common_prefix[len(prev_committed):], False
    if prev_committed.startswith(new_common_prefix):
        # 这次 common 比上次短(可能 audio 抖动 / whisper 不确定),保持原 commit
        return "", False
    # 真分叉 — 至少某个字被改了
    return "", True
