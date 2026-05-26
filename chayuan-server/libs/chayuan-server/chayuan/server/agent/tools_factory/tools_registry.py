from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Optional, Tuple, Type, Union, List

from langchain_classic.agents import tool
from langchain_core.tools import BaseTool

from chayuan.server.knowledge_base.kb_doc_api import DocumentWithVSId
from chayuan.server.pydantic_v1 import BaseModel, Extra

__all__ = ["regist_tool", "BaseToolOutput", "format_context"]


_TOOLS_REGISTRY = {}


# patch BaseTool to support extra fields e.g. a title
try:
    BaseTool.Config.extra = Extra.allow
except Exception:
    # Pydantic v2 models no longer expose Config class.
    if hasattr(BaseTool, "model_config") and isinstance(BaseTool.model_config, dict):
        BaseTool.model_config["extra"] = "allow"

################################### TODO: workaround to langchain #15855
# patch BaseTool to support tool parameters defined using pydantic Field


def _new_parse_input(
    self,
    tool_input: Union[str, Dict],
    tool_call_id: Optional[str] = None,
    *_args: Any,
    **_kwargs: Any,
) -> Union[str, Dict[str, Any]]:
    """把工具输入字典转成 pydantic 模型(替换 langchain 默认实现以兼容 v1/v2)。

    上游 langchain-core 在 0.3.x 之后给 ``_parse_input`` 加了 ``tool_call_id``
    第三个位置参数,此处签名跟随它(同时用 ``*_args``/``**_kwargs`` 收尾,
    避免日后再加参数又抛 TypeError)。``tool_call_id`` 我们用不到,直接吞。
    """
    del tool_call_id  # 显式弃用
    input_args = self.args_schema
    if isinstance(tool_input, str):
        if input_args is not None:
            key_ = next(iter(input_args.__fields__.keys()))
            input_args.validate({key_: tool_input})
        return tool_input
    else:
        if input_args is not None:
            result = input_args.parse_obj(tool_input)
            return result.dict()


def _new_to_args_and_kwargs(
    self,
    tool_input: Union[str, Dict],
    tool_call_id: Optional[str] = None,
    *_args: Any,
    **_kwargs: Any,
) -> Tuple[Tuple, Dict]:
    """与 ``_new_parse_input`` 一样:同步上游签名,新加的 ``tool_call_id`` 吞掉。

    历史问题:0.3.x 之前签名只有 ``(self, tool_input)``,我们的 patch 也只接两参,
    升级 langchain 后 runtime 报 ``TypeError: takes 2 positional arguments
    but 3 were given``。这里加 ``tool_call_id`` + 兜底 ``*args``/``**kwargs``。
    """
    del tool_call_id
    # For backwards compatibility, if run_input is a string,
    # pass as a positional argument.
    if isinstance(tool_input, str):
        return (tool_input,), {}
    else:
        # for tool defined with `*args` parameters
        # the args_schema has a field named `args`
        # it should be expanded to actual *args
        # e.g.: test_tools
        #       .test_named_tool_decorator_return_direct
        #       .search_api
        if "args" in tool_input:
            args = tool_input["args"]
            if args is None:
                tool_input.pop("args")
                return (), tool_input
            elif isinstance(args, tuple):
                tool_input.pop("args")
                return args, tool_input
        return (), tool_input


BaseTool._parse_input = _new_parse_input
BaseTool._to_args_and_kwargs = _new_to_args_and_kwargs
###############################


def regist_tool(
    *args: Any,
    title: str = "",
    description: str = "",
    return_direct: bool = False,
    args_schema: Optional[Type[BaseModel]] = None,
    infer_schema: bool = True,
) -> Union[Callable, BaseTool]:
    """LangChain @tool 装饰器的薄壳:自动把函数注册到全局 ``_TOOLS_REGISTRY`` 里,
    便于 ``get_tool(name)`` 与 ``/tools`` REST 端点统一暴露。"""

    def _parse_tool(t: BaseTool):
        nonlocal description, title

        _TOOLS_REGISTRY[t.name] = t

        # change default description
        if not description:
            if t.func is not None:
                description = t.func.__doc__
            elif t.coroutine is not None:
                description = t.coroutine.__doc__
        t.description = " ".join(re.split(r"\n+\s*", description))
        # set a default title for human
        if not title:
            title = "".join([x.capitalize() for x in t.name.split("_")])
        try:
            t.title = title
        except Exception:
            # Pydantic v2 tools may disallow unknown attrs.
            pass

    def wrapper(def_func: Callable) -> BaseTool:
        effective_description = (description or (def_func.__doc__ or "")).strip()
        if not effective_description:
            effective_description = f"Tool `{def_func.__name__}`"
        partial_ = tool(
            *args,
            description=effective_description,
            return_direct=return_direct,
            args_schema=args_schema,
            infer_schema=infer_schema,
        )
        t = partial_(def_func)
        _parse_tool(t)
        return t

    if len(args) == 0:
        return wrapper
    else:
        t = tool(
            *args,
            return_direct=return_direct,
            args_schema=args_schema,
            infer_schema=infer_schema,
        )
        _parse_tool(t)
        return t


def format_context(self: BaseToolOutput) -> str:
    '''
    将包含知识库输出的ToolOutput格式化为 LLM 需要的字符串
    '''
    context = ""
    docs = self.data["docs"]
    source_documents = []

    for inum, doc in enumerate(docs):
        doc = DocumentWithVSId.parse_obj(doc)
        source_documents.append(doc.page_content)

    if len(source_documents) == 0:
        context = "没有找到相关文档,请更换关键词重试"
    else:
        for doc in source_documents:
            context += doc + "\n\n"

    return context
