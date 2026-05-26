"""察元 RAG 评估子系统（N-5）。

基于 RAGAS（https://github.com/explodinggradients/ragas）提供：
- context_precision / context_recall / faithfulness / answer_correctness
- 自建命中率（hit@k，相对 golden dataset）

用法：
    from chayuan.server.eval.runner import run_eval_against_golden
    report = run_eval_against_golden(golden_path="tests/.../golden.json", kb_name="samples")

未装 ragas → 自动降级为"仅命中率 + LLM 判定 answer_correctness"。
"""

from chayuan.server.eval.runner import run_eval_against_golden  # noqa: F401
