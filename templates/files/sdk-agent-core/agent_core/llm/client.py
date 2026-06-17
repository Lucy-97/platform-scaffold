"""
LLM Client — 通用多厂商大模型调用层
====================================

提供 LiteLLMClient: 基于 LiteLLM 的多厂商统一接口（Gemini / OpenAI / Anthropic 等）。

本模块为 agent-core 通用包的一部分，**不依赖任何平台特定配置**。
配置通过 configure() 函数注入，AgentCore 后端在 main.py 中调用一次即可。

模块底部导出单例 litellm_client 供外部直接 import。
"""

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type

from loguru import logger
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# 可注入的配置 — 替代硬编码的 core.config.settings
# ---------------------------------------------------------------------------

@dataclass
class LLMSettings:
    """LLM 客户端配置项，通过 configure() 注入。

    Attributes:
        api_key: 默认 API Key（可被每次调用覆盖）。
        model: 默认模型标识符（如 "gemini/gemini-2.5-flash"）。
        api_base: 默认 API Base URL（可选）。
    """
    api_key: str = ""
    model: str = ""
    api_base: Optional[str] = None


# 模块级配置实例 — 通过 configure() 替换
_llm_settings = LLMSettings()


def configure(api_key: str = "", model: str = "", api_base: Optional[str] = None) -> None:
    """注入 LLM 配置（在应用启动时调用一次）。

    Example (AgentCore backend)::

        # main.py
        from agent_core.llm.client import configure as configure_llm
        from core.config import settings
        configure_llm(api_key=settings.LLM_API_KEY, model=settings.LLM_MODEL)

    Example (其他项目)::

        from agent_core.llm.client import configure as configure_llm
        configure_llm(api_key="sk-...", model="openai/gpt-4o")
    """
    global _llm_settings
    _llm_settings = LLMSettings(api_key=api_key, model=model, api_base=api_base)


# ---------------------------------------------------------------------------
# LiteLLMClient — 多厂商统一接口
# ---------------------------------------------------------------------------

class LiteLLMClient:
    """
    基于 LiteLLM 的统一 LLM 调用接口。

    模型标识符格式: "provider/model_name"，例如:
      - "gemini/gemini-2.5-flash"
      - "openai/gpt-4o"
      - "anthropic/claude-3-5-sonnet"

    默认 API Key 与 Model 通过 configure() 注入（见模块顶部文档）。
    """

    def __init__(self) -> None:
        # 延迟导入：即使未安装 litellm，服务仍能启动（其他功能不受影响）
        try:
            import litellm
            self._litellm = litellm
        except ImportError:
            self._litellm = None
            logger.warning("litellm is not installed. LiteLLMClient will be unavailable.")


    # ----- 结构化输出（JSON Schema 约束） -----

    async def complete_with_schema(
        self,
        system_instruction: str,
        user_input: str,
        response_model: Type[BaseModel],
        trace_id: Optional[str] = None,
        max_retries: int = 3,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking_budget: Optional[int] = None,
    ) -> BaseModel:
        """
        调用 LLM 并将响应解析为 Pydantic 模型实例（结构化输出）。

        工作原理:
          1. 将 response_model (Pydantic class) 直接传给 LiteLLM 的 response_format，
             LiteLLM 会自动将其转换为对应厂商的 JSON Schema 格式
             （Gemini → schema, OpenAI → json_schema, 等）。
             这样 **LLM 在生成时就知道要输出哪些字段**。
          2. LiteLLM 返回后，用 Pydantic model_validate 做二次校验。
          3. 校验失败时将错误信息反馈给 LLM，让它自我修正（最多 max_retries 次）。

        Args:
            system_instruction: 系统提示词。
            user_input: 用户输入 / 任务描述。
            response_model: Pydantic 模型类 — 同时用于:
                (a) 告知 LLM 输出结构（传给 response_format）
                (b) 响应后的 Pydantic 校验
            trace_id: 仅用于日志追踪，不影响业务逻辑。
            max_retries: JSON / Pydantic 校验失败时的最大重试次数。
            model: 覆盖模型（如 "gemini/gemini-2.0-flash"）。
            api_key: 覆盖 API Key。
            api_base: 覆盖 API Base URL。
            temperature: 覆盖温度（默认 0.7）。
            max_tokens: 覆盖最大输出 Token 数。
            thinking_budget: 思考模式预算。0=禁用, -1=动态(high), >0=手动限制(medium)。

        Returns:
            response_model 的校验通过实例。

        Raises:
            RuntimeError: 全部重试耗尽仍失败时抛出。
        """
        if not self._litellm:
            raise RuntimeError("litellm is not installed. Cannot call LiteLLMClient.complete_with_schema().")

        resolved_api_key = api_key or _llm_settings.api_key
        resolved_model = model or _llm_settings.model
        resolved_api_base = api_base or _llm_settings.api_base
        resolved_temperature = temperature if temperature is not None else 0.7
        current_input = user_input
        last_error = ""

        # 构建可选参数
        completion_kwargs: Dict[str, Any] = {}
        if max_tokens is not None:
            completion_kwargs["max_tokens"] = max_tokens

        # thinking_budget → LiteLLM reasoning_effort 参数
        # 与 agent_agents.py 的 streaming test 逻辑保持一致
        if thinking_budget is not None and int(thinking_budget) != 0:
            completion_kwargs["reasoning_effort"] = (
                "high" if int(thinking_budget) == -1 else "medium"
            )

        logger.info(
            f"[LiteLLMClient] Requesting | model={resolved_model} "
            f"schema={response_model.__name__} trace_id={trace_id} "
            f"sys_len={len(system_instruction)} user_len={len(user_input)} "
            f"temperature={resolved_temperature} "
            f"thinking_budget={thinking_budget} "
            f"extra_kwargs={list(completion_kwargs.keys())}"
        )

        for attempt in range(max_retries):
            try:
                messages = [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": current_input},
                ]

                # 核心调用：将 Pydantic Model 直接传给 response_format
                # LiteLLM 会自动转换为厂商原生的 JSON Schema 约束
                # 例如 Gemini 会收到 response_schema / OpenAI 会收到 json_schema
                response = await self._litellm.acompletion(
                    model=resolved_model,
                    messages=messages,
                    api_key=resolved_api_key,
                    api_base=resolved_api_base,
                    response_format=response_model,
                    temperature=resolved_temperature,
                    **completion_kwargs,
                )

                # 解析响应并用 Pydantic 做二次校验（防御性）
                content = response.choices[0].message.content

                # --- 增强日志: Token 使用统计 ---
                usage = getattr(response, "usage", None)
                usage_str = ""
                if usage:
                    usage_str = (
                        f"prompt_tokens={getattr(usage, 'prompt_tokens', '?')} "
                        f"completion_tokens={getattr(usage, 'completion_tokens', '?')} "
                        f"total_tokens={getattr(usage, 'total_tokens', '?')}"
                    )

                # --- 增强日志: Thinking/Reasoning 内容（Gemini 等模型） ---
                thinking_content = None
                msg = response.choices[0].message
                # LiteLLM 可能将 thinking 放在 model_extra 或 reasoning_content 中
                if hasattr(msg, "reasoning_content") and msg.reasoning_content:
                    thinking_content = msg.reasoning_content
                elif hasattr(msg, "model_extra") and msg.model_extra:
                    thinking_content = msg.model_extra.get("thinking")

                if thinking_content:
                    thinking_preview = str(thinking_content)[:300]
                    logger.info(
                        f"[LiteLLMClient] Thinking content | trace_id={trace_id} "
                        f"attempt={attempt + 1} length={len(str(thinking_content))} "
                        f"preview={thinking_preview!r}"
                    )

                # --- 解析 JSON ---
                resp_json = json.loads(content)
                validated = response_model.model_validate(resp_json)

                # --- 增强日志: 成功时的完整状态 ---
                content_preview = content[:500] if content else ""
                logger.info(
                    f"[LiteLLMClient] complete_with_schema SUCCESS | "
                    f"model={resolved_model} trace_id={trace_id} attempt={attempt + 1} "
                    f"{usage_str} "
                    f"response_len={len(content) if content else 0} "
                    f"preview={content_preview!r}"
                )
                return validated

            except json.JSONDecodeError as e:
                # LLM 返回的不是合法 JSON — 打印原始内容帮助排查
                last_error = f"JSON parse error: {e}"
                raw_preview = content[:500] if content else "<empty>"
                logger.warning(
                    f"[LiteLLMClient] {last_error} | trace_id={trace_id} attempt={attempt + 1} "
                    f"raw_content_len={len(content) if content else 0} "
                    f"raw_preview={raw_preview!r}"
                )
                current_input = (
                    f"{user_input}\n\n"
                    f"[System Note] You MUST return valid JSON. Previous attempt failed: {last_error}"
                )

            except Exception as e:
                # 覆盖 Pydantic ValidationError 和 LLM API 层错误
                last_error = str(e)
                logger.warning(
                    f"[LiteLLMClient] Error on attempt {attempt + 1}: {last_error} | "
                    f"trace_id={trace_id} model={resolved_model}"
                )
                # 将校验错误反馈给 LLM，让它在下次尝试中自我修正
                current_input = (
                    f"{user_input}\n\n"
                    f"[System Note] Your previous output failed validation. "
                    f"Fix these errors: {last_error}"
                )

        raise RuntimeError(
            f"[LiteLLMClient] All {max_retries} attempts failed. Last error: {last_error}"
        )

    # ----- 工具调用（Function Calling） -----

    async def complete_with_tools(
        self,
        system_instruction: str,
        user_input: str,
        tools: List[Dict[str, Any]],
        trace_id: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        调用 LLM 并让其从注册的 Tools 中选择要执行的 Skill。

        tools 格式遵循 OpenAI 兼容标准:
        [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}]

        Args:
            system_instruction: 系统提示词。
            user_input: 用户输入（如果提供了 messages 则忽略）。
            tools: OpenAI 兼容格式的 tool 定义列表。
            trace_id: 日志追踪用。
            model: 覆盖模型。
            api_key: 覆盖 API Key。
            api_base: 覆盖 Base URL。
            messages: 完整消息列表覆盖（提供时 system_instruction 和 user_input 被忽略）。

        Returns:
            LLM 响应的 choices[0].message（调用方检查 .tool_calls 来获取被选中的 Skill）。
        """
        if not self._litellm:
            raise RuntimeError("litellm is not installed.")

        resolved_key = api_key or _llm_settings.api_key
        resolved_model = model or _llm_settings.model
        resolved_base = api_base or _llm_settings.api_base

        if messages is None:
            messages = [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_input},
            ]

        response = await self._litellm.acompletion(
            model=resolved_model,
            messages=messages,
            api_key=resolved_key,
            api_base=resolved_base,
            tools=tools,
            tool_choice="auto",
        )
        logger.info(
            f"[LiteLLMClient] complete_with_tools | model={resolved_model} "
            f"trace_id={trace_id} "
            f"tool_calls={[tc.function.name for tc in (response.choices[0].message.tool_calls or [])]}"
        )
        return response.choices[0].message


# ---------------------------------------------------------------------------
# 模块级单例 — 外部直接 import 使用
# ---------------------------------------------------------------------------

litellm_client = LiteLLMClient()
