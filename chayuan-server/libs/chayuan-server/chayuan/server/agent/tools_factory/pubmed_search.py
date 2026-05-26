"""PubMed 医学文献检索工具。

通过 NCBI Entrez E-utilities 接口检索 PubMed 上的医学 / 生物学论文，
返回题目、摘要、作者、DOI 等；完全免费、**无需 API Key**（建议在 yaml 里填写
邮箱地址以便 NCBI 在触发限流时联系你）。

适合场景：
- 医药企业 / 研究机构的对话式文献助手
- 临床辅助决策（CDSS）中的证据检索
- 让 LLM 回答医学问题前先抓到同行评议资料，降低幻觉
"""
from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


@regist_tool(title="PubMed 医学文献检索")
def pubmed_search(
    query: str = Field(description="英文医学/生物学主题词 / 关键词 / MeSH 词汇"),
):
    """搜索 PubMed(NCBI 提供)上的医学/生物学文献摘要。
调用时机:用户问医学症状、疾病、药物、生物机理、临床试验等专业问题且需要循证文献支撑。
输入:英文医学/生物学关键词、MeSH 词汇或英文研究主题(中文也接受但召回不佳;建议先把中文术语翻成英文医学名词)。
输出:文献标题、作者、摘要、PMID。
不要用于:日常健康闲聊、非医学问题、非循证类咨询、最新医学新闻(用 search_internet)。"""
    from langchain_community.tools.pubmed.tool import PubmedQueryRun
    from langchain_community.utilities.pubmed import PubMedAPIWrapper

    cfg = get_tool_config("pubmed_search") or {}
    api_wrapper = PubMedAPIWrapper(
        top_k_results=int(cfg.get("top_k", 5) or 5),
        doc_content_chars_max=int(cfg.get("max_chars", 2000) or 2000),
        email=str(cfg.get("email", "") or ""),
    )
    tool = PubmedQueryRun(api_wrapper=api_wrapper)
    return BaseToolOutput(tool.run(tool_input=query))
