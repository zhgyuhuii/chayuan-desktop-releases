# -*- coding: utf-8 -*-
import asyncio
import sys
from contextlib import AsyncExitStack

from langchain_classic.agents.agent import RunnableMultiActionAgent
from langchain_core.messages import SystemMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate, MessagesPlaceholder
from pydantic import BaseModel

from chayuan.server.utils import get_prompt_template_dict
from langchain_chayuan.agents.all_tools_agent import PlatformToolsAgentExecutor
from langchain_chayuan.agents.react.create_prompt_template import create_prompt_glm3_template, \
    create_prompt_structured_react_template, create_prompt_platform_template, create_prompt_gpt_tool_template, \
    create_prompt_platform_knowledge_mode_template
from langchain_chayuan.agents.structured_chat.glm3_agent import (
    create_structured_glm3_chat_agent,
)
from typing import (
    Any,
    AsyncIterable,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union, cast,
)

from langchain_classic.agents import AgentExecutor, create_openai_tools_agent, create_tool_calling_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseLanguageModel
from langchain_core.tools import BaseTool
from langchain_chayuan.agent_toolkits.mcp_kit.tools import MCPStructuredTool

from langchain_chayuan.agents.structured_chat.platform_knowledge_bind import create_platform_knowledge_agent
from langchain_chayuan.agents.structured_chat.platform_tools_bind import create_platform_tools_agent
from langchain_chayuan.agents.structured_chat.qwen_agent import create_qwen_chat_agent
from langchain_chayuan.agents.structured_chat.structured_chat_agent import create_chat_agent


import logging as _logging
_logger = _logging.getLogger(__name__)


def _builtin_action_template(agent_type: str) -> Dict[str, Any]:
    """
    从 chayuan.settings.Settings.prompt_settings.action_model 内嵌默认值取指定 agent_type 模板。

    YAML 文件缺失或残缺（例如历史遗留的扁平 2.x 结构里没有 platform-knowledge-mode）时，
    必须从这里回退，**而不是**返回一个通用 "You are a helpful assistant"——后者会剥掉
    {tools}/{mcp_tools}/{current_working_directory} 占位符，让 create_prompt_platform_knowledge_mode_template
    渲染错位，最终表现成"模型只复述系统人设、不回答用户问题"。
    """
    try:
        from chayuan.settings import Settings
        am = (getattr(Settings.prompt_settings, "action_model", None) or {})
        tpl = am.get(agent_type) or {}
        # 内嵌默认存储成 dict（SYSTEM_PROMPT + 可选 HUMAN_MESSAGE）
        if isinstance(tpl, dict) and tpl.get("SYSTEM_PROMPT"):
            return dict(tpl)
    except Exception as e:  # noqa: BLE001
        _logger.warning("读取 Settings.prompt_settings 内嵌 action_model 失败: %s", e)
    return {}


def _get_action_template(agent_type: str) -> Dict[str, Any]:
    """
    action_model 模板解析：先取 YAML（用户数据根）→ 再取 settings.py 内嵌默认 → 最后才抛。

    **不再**走"退到 default 再补 helpful assistant"的沉默降级路径：该路径会吞掉
    platform-knowledge-mode 所需的 {tools}/{mcp_tools}/{current_working_directory}
    占位符，从而把 agent 推入 PromptTemplate 渲染错位分支。
    """
    template = get_prompt_template_dict("action_model", agent_type) or {}
    if isinstance(template, dict) and template.get("SYSTEM_PROMPT"):
        # HUMAN_MESSAGE 为空时补空字符串（不会破坏 input_variables 推断），由 create_prompt_*
        # 自己判断 HUMAN_MESSAGE 是否渲染 {input} 占位符。
        template.setdefault("HUMAN_MESSAGE", "{input}")
        return template

    builtin = _builtin_action_template(agent_type)
    if builtin.get("SYSTEM_PROMPT"):
        _logger.warning(
            "prompt_settings.yaml 缺 action_model.%s，使用 settings.py 内嵌默认回填",
            agent_type,
        )
        builtin.setdefault("HUMAN_MESSAGE", "{input}")
        return builtin

    raise RuntimeError(
        f"未找到 agent_type={agent_type!r} 的 prompt 模板；请确认 "
        "CHAYUAN_ROOT/prompt_settings.yaml 的 action_model 段是嵌套结构且含 "
        "default/platform-agent/platform-knowledge-mode 子键"
    )


def agents_registry(
        agent_type: str,
        llm: BaseLanguageModel,
        llm_with_platform_tools: List[Dict[str, Any]] = [],
        tools: Sequence[Union[Dict[str, Any], Type[BaseModel], Callable, BaseTool]] = [],
        mcp_tools: Sequence[MCPStructuredTool] = [],
        callbacks: List[BaseCallbackHandler] = [],
        verbose: bool = False,
        **kwargs: Any,
):
    # Write any optimized method here.
    # TODO agent params of PlatformToolsAgentExecutor or AgentExecutor  enable return_intermediate_steps=True,
    if "glm3" == agent_type:
        # An optimized method of langchain Agent that uses the glm3 series model
        template = _get_action_template(agent_type)
        prompt = create_prompt_glm3_template(agent_type, template=template)
        agent = create_structured_glm3_chat_agent(llm=llm,
                                                  tools=tools,
                                                  prompt=prompt,
                                                  llm_with_platform_tools=llm_with_platform_tools
                                                  )

        agent_executor = PlatformToolsAgentExecutor(
            agent=agent,
            tools=tools,
            verbose=verbose,
            callbacks=callbacks,
            return_intermediate_steps=True,
        )
        return agent_executor
    elif "qwen" == agent_type:
        llm.streaming = False  # qwen agent not support streaming

        template = _get_action_template(agent_type)
        prompt = create_prompt_structured_react_template(agent_type, template=template)
        agent = create_qwen_chat_agent(llm=llm,
                                       tools=tools,
                                       prompt=prompt,
                                       llm_with_platform_tools=llm_with_platform_tools)

        agent_executor = PlatformToolsAgentExecutor(
            agent=agent,
            tools=tools,
            verbose=verbose,
            callbacks=callbacks,
            return_intermediate_steps=True,
        )
        return agent_executor
    elif "platform-agent" == agent_type:

        template = _get_action_template(agent_type)
        prompt = create_prompt_platform_template(agent_type, template=template)
        agent = create_platform_tools_agent(llm=llm,
                                            tools=tools,
                                            prompt=prompt,
                                            llm_with_platform_tools=llm_with_platform_tools)

        agent_executor = PlatformToolsAgentExecutor(
            agent=agent,
            tools=tools,
            verbose=verbose,
            callbacks=callbacks,
            return_intermediate_steps=True,
        )
        return agent_executor
    elif agent_type == 'structured-chat-agent':

        template = _get_action_template(agent_type)
        prompt = create_prompt_structured_react_template(agent_type, template=template)
        agent = create_chat_agent(llm=llm,
                                  tools=tools,
                                  prompt=prompt,
                                  llm_with_platform_tools=llm_with_platform_tools
                                  )

        agent_executor = PlatformToolsAgentExecutor(
            agent=agent,
            tools=tools,
            verbose=verbose,
            callbacks=callbacks,
            return_intermediate_steps=True,
        )
        return agent_executor
    elif agent_type == 'default':
        # this agent single chat
        template = _get_action_template("default")
        prompt = ChatPromptTemplate.from_messages([SystemMessage(content=template.get("SYSTEM_PROMPT"))])

        agent = create_chat_agent(llm=llm,
                                  tools=tools,
                                  prompt=prompt,
                                  llm_with_platform_tools=llm_with_platform_tools
                                  )

        agent_executor = AgentExecutor(
            agent=agent, tools=tools, verbose=verbose, callbacks=callbacks,
            return_intermediate_steps=True,
            **kwargs,
        )

        return agent_executor

    elif agent_type == "openai-functions":
        # agent only tools agent_scratchpad chat ,this runnable supper history message
        template = _get_action_template(agent_type)
        prompt = create_prompt_gpt_tool_template(agent_type, template=template)

        # prompt pre partial "tool_names" var
        prompt = prompt.partial(
            tool_names=", ".join([t.name for t in tools]),
        )
        runnable = create_openai_tools_agent(llm, tools, prompt)
        agent = RunnableMultiActionAgent(
            runnable=runnable,
            input_keys_arg=["input"],
            return_keys_arg=["output"],
            **kwargs,
        )
        agent_executor = AgentExecutor(
            agent=agent, tools=tools, verbose=verbose, callbacks=callbacks,

            return_intermediate_steps=True,
            **kwargs,
        )
        return agent_executor
    elif agent_type in ("openai-tools", "tool-calling"):
        # agent only tools agent_scratchpad chat ,this runnable not history message
        function_prefix = kwargs.get("FUNCTIONS_PREFIX")
        function_suffix = kwargs.get("FUNCTIONS_SUFFIX")
        messages = [
            SystemMessage(content=cast(str, function_prefix)),
            HumanMessagePromptTemplate.from_template("{input}"),
            AIMessage(content=function_suffix),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
        prompt = ChatPromptTemplate.from_messages(messages)
        if agent_type == "openai-tools":
            runnable = create_openai_tools_agent(llm, tools, prompt)
        else:
            runnable = create_tool_calling_agent(llm, tools, prompt)
        agent = RunnableMultiActionAgent(
            runnable=runnable,
            input_keys_arg=["input"],
            return_keys_arg=["output"],
            **kwargs,
        )
        agent_executor = AgentExecutor(
            agent=agent, tools=tools, verbose=verbose, callbacks=callbacks,
            return_intermediate_steps=True,
            **kwargs,
        )
        return agent_executor

    elif "platform-knowledge-mode" == agent_type:
  
        template = _get_action_template(agent_type)
        prompt = create_prompt_platform_knowledge_mode_template(agent_type, template=template)
        agent = create_platform_knowledge_agent(llm=llm,
                                                current_working_directory=kwargs.get("current_working_directory", "/tmp"),
                                                tools=tools,
                                                mcp_tools=mcp_tools,
                                                llm_with_platform_tools=llm_with_platform_tools,
                                                prompt=prompt)

        agent_executor = PlatformToolsAgentExecutor(
            agent=agent,
            tools=tools,
            mcp_tools=mcp_tools,
            verbose=verbose,
            callbacks=callbacks,
            return_intermediate_steps=True,
        )
        return agent_executor

    else:
        raise ValueError(
            f"Agent type {agent_type} not supported at the moment. Must be one of "
            "'tool-calling', 'openai-tools', 'openai-functions', "
            "'default','ChatGLM3','structured-chat-agent','platform-agent','qwen','glm3'"
        )

