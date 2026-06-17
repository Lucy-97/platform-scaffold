"""
解耦的 Agent 运行时引擎 — AgentRuntimeEngine
===============================================

将原 runtime.py 的同步 run()→str 重构为 AsyncGenerator 事件流。

核心设计原则（借鉴 Claude Code QueryEngine）：
  1. 引擎只 yield RuntimeEvent——永远不知道前端长什么样
  2. 所有外围逻辑通过 LifecycleHookRegistry 注册
  3. LLM 调用使用 litellm 流式接口（acompletion + stream=True）
  4. 工具执行复用现有微内核管线（ToolRegistry + ToolExecutor）
  5. 流式嗅探器在 LLM 输出过程中提前捕获工具名和参数
  6. CostTracker 从 litellm usage 提取真实 Token 用量
  7. TraceCollector 构建 Span 树用于链路追踪
  8. response_format 支持结构化输出（Pydantic Model）
"""

import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Type

from loguru import logger
from pydantic import BaseModel

from agent_core.runtime.events import RuntimeEvent, RuntimeEventType
from agent_core.runtime.hooks import HookPhase, LifecycleHookRegistry
from agent_core.runtime.preheat import PreheatScheduler
from agent_core.runtime.streaming_sniffer import StreamingToolSniffer
from agent_core.runtime.cost_tracker import CostTracker
from agent_core.runtime.trace import TraceCollector, SpanKind
from agent_core.tools.base import ToolExecutionResult


class AgentRuntimeEngine:
    """解耦的 Agent 运行时引擎——AsyncGenerator 事件流架构。

    消费方式::

        engine = AgentRuntimeEngine(...)
        async for event in engine.submit("帮我写第三集剧本"):
            if event.type == RuntimeEventType.STREAM_DELTA:
                print(event.data, end="", flush=True)
            elif event.type == RuntimeEventType.TOOL_START:
                print(f"⚙️ {event.data['name']}...")
            elif event.type == RuntimeEventType.RESULT:
                print(f"\\n✅ 完成")

    Args:
        model: LLM 模型标识符。
        api_key: LLM API Key。
        api_base: LLM API Base URL（可选）。
        max_turns: 最大工具调用轮次，默认 15。
        temperature: LLM 温度参数，默认 0.7。
        max_tokens: LLM 最大输出 token 数，默认 4096。
        hooks: 生命周期 Hook 注册表（可选，未传则创建空注册表）。
        tool_registry: 微内核工具注册中心（可选）。
        tool_executor: 微内核工具执行管线（可选）。
        sniffer: 流式工具嗅探器（可选）。
        preheater: 资源预热调度器（可选）。
        cost_tracker: Token 成本追踪器（可选）。
        trace_collector: 链路追踪收集器（可选）。
        memory_store: 五层记忆存储（可选，传入则自动注册 MemoryHooks）。
        memory_retriever: 记忆检索器（可选，与 memory_store 配套）。
        memory_extractor: 记忆提取器（可选，与 memory_store 配套）。
        compaction_budget: Token 预算管控器（可选，传入则自动注册 CompactionHooks）。
        autocompactor: LLM 驱动的自动摘要压缩器（可选）。
    """

    def __init__(
        self,
        model: str = "",
        api_key: str = "",
        api_base: Optional[str] = None,
        max_turns: int = 15,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        hooks: Optional[LifecycleHookRegistry] = None,
        tool_registry: Optional[Any] = None,
        tool_executor: Optional[Any] = None,
        sniffer: Optional[StreamingToolSniffer] = None,
        preheater: Optional[PreheatScheduler] = None,
        cost_tracker: Optional[CostTracker] = None,
        trace_collector: Optional[TraceCollector] = None,
        memory_store: Optional[Any] = None,
        memory_retriever: Optional[Any] = None,
        memory_extractor: Optional[Any] = None,
        compaction_budget: Optional[Any] = None,
        autocompactor: Optional[Any] = None,
    ) -> None:
        # 不可变配置
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.max_turns = max_turns
        self.temperature = temperature
        self.max_tokens = max_tokens

        # 可插拔组件
        self._hooks = hooks or LifecycleHookRegistry()
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._sniffer = sniffer
        self._preheater = preheater
        self._cost_tracker = cost_tracker
        self._trace_collector = trace_collector

        # 记忆与压缩组件引用（供外部获取状态）
        self._memory_store = memory_store
        self._compaction_budget = compaction_budget

        # ── 自动注册 Memory Hooks ──
        # 传入 memory_store + retriever 后，PRE_SAMPLING 自动注入记忆，
        # ON_COMPLETE 自动提取新事实
        if memory_store and memory_retriever:
            from agent_core.memory.hooks import MemoryHooks
            self._memory_hooks = MemoryHooks(
                store=memory_store,
                retriever=memory_retriever,
                extractor=memory_extractor,
            )
            self._memory_hooks.register_all(self._hooks)
            logger.info("[Engine] MemoryHooks 已自动注册")

        # ── 自动注册 Compaction Hooks ──
        # 传入 budget 或 autocompactor 后，PRE_SAMPLING 自动压缩，
        # POST_TOOL 自动截断大结果
        if compaction_budget or autocompactor:
            from agent_core.compaction.hooks import CompactionHooks
            self._compaction_hooks = CompactionHooks(
                hooks=self._hooks,
                budget=compaction_budget,
                autocompactor=autocompactor,
            )
            self._compaction_hooks.register_all()
            logger.info("[Engine] CompactionHooks 已自动注册")

        # 可变状态——每次 submit 时重置
        self._messages: List[Dict[str, Any]] = []

    @property
    def hooks(self) -> LifecycleHookRegistry:
        """获取 Hook 注册表（允许外部注册）。"""
        return self._hooks

    async def submit(
        self,
        user_input: str,
        messages: Optional[List[Dict[str, Any]]] = None,
        session_id: str = "default",
        response_format: Optional[Type[BaseModel]] = None,
        parent_span_id: Optional[str] = None,
    ) -> AsyncGenerator[RuntimeEvent, None]:
        """提交用户输入，返回事件流。

        Args:
            user_input: 用户输入文本。
            messages: 初始消息历史（含 system prompt）。
                若提供则使用，否则使用内部累积的历史。
            session_id: 会话标识符。
            response_format: Pydantic Model 类 — 启用结构化输出模式。
                传入后引擎切换为非流式调用，返回校验后的 Pydantic 实例。
            parent_span_id: 父 Span ID（用于链路追踪层级关联）。

        Yields:
            RuntimeEvent: 结构化运行时事件。
        """
        import litellm

        # 初始化消息历史
        if messages is not None:
            self._messages = messages
        self._messages.append({"role": "user", "content": user_input})

        # ── 结构化输出模式（非流式） ──
        if response_format is not None:
            async for event in self._submit_structured(
                litellm, session_id, response_format, parent_span_id
            ):
                yield event
            return

        for turn in range(1, self.max_turns + 1):
            yield RuntimeEvent(
                type=RuntimeEventType.TURN_START,
                turn=turn,
                data={"messages_count": len(self._messages)},
            )

            # ★ 生命周期：PRE_SAMPLING
            await self._hooks.execute(
                HookPhase.PRE_SAMPLING,
                messages=self._messages,
                turn=turn,
                session_id=session_id,
            )

            # 创建 LLM Span
            llm_span = None
            if self._trace_collector:
                llm_span = self._trace_collector.start_span(
                    f"llm:turn_{turn}",
                    SpanKind.LLM,
                    parent_id=parent_span_id,
                    input_preview=self._messages[-1].get("content", "")[:200],
                )

            # 构建 LLM 调用参数
            call_params: Dict[str, Any] = {
                "model": self.model,
                "messages": self._messages,
                "api_key": self.api_key,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": True,  # ★ 流式输出
                # 请求 litellm 在最后一个 chunk 中包含 usage
                "stream_options": {"include_usage": True},
            }
            if self.api_base:
                call_params["api_base"] = self.api_base

            # 从微内核注册表生成 tools 定义
            tool_defs = []
            if self._tool_registry:
                tool_defs = self._tool_registry.get_all_definitions()
                if tool_defs:
                    call_params["tools"] = tool_defs
                    call_params["tool_choice"] = "auto"

            # ── 流式 LLM 调用 ──
            try:
                # 重置嗅探器
                if self._sniffer:
                    self._sniffer.reset()

                full_content = ""
                tool_calls_data: List[Dict[str, Any]] = []
                finish_reason = None
                stream_usage = None  # 从流式 chunk 中提取的 usage

                # litellm 流式返回 chunk
                response = await litellm.acompletion(**call_params)
                async for chunk in response:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue

                    # 文本内容流
                    if delta.content:
                        full_content += delta.content
                        yield RuntimeEvent(
                            type=RuntimeEventType.STREAM_DELTA,
                            data=delta.content,
                            turn=turn,
                        )

                    # 工具调用流（增量拼接）
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            # 确保列表长度够
                            while len(tool_calls_data) <= idx:
                                tool_calls_data.append({
                                    "id": "",
                                    "name": "",
                                    "arguments": "",
                                })
                            entry = tool_calls_data[idx]
                            if tc_delta.id:
                                entry["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    entry["name"] = tc_delta.function.name
                                if tc_delta.function.arguments:
                                    entry["arguments"] += tc_delta.function.arguments

                                    # ★ 流式嗅探
                                    if self._sniffer:
                                        sniff = self._sniffer.feed(
                                            tc_delta.function.arguments
                                        )
                                        if sniff:
                                            # 嗅探结果处理
                                            async for ev in self._handle_sniff(
                                                sniff, turn
                                            ):
                                                yield ev

                    # 捕获 finish_reason
                    if chunk.choices and chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason

                    # 捕获流式 usage（litellm stream_options.include_usage）
                    if hasattr(chunk, "usage") and chunk.usage:
                        stream_usage = chunk.usage

                yield RuntimeEvent(
                    type=RuntimeEventType.STREAM_COMPLETE,
                    data={"finish_reason": finish_reason},
                    turn=turn,
                )

                # ★ 记录真实 Token 成本
                if stream_usage and self._cost_tracker:
                    usage_dict = {
                        "prompt_tokens": getattr(stream_usage, "prompt_tokens", 0),
                        "completion_tokens": getattr(stream_usage, "completion_tokens", 0),
                        "total_tokens": getattr(stream_usage, "total_tokens", 0),
                    }
                    self._cost_tracker.record(
                        model=self.model, usage=usage_dict,
                        turn=turn, session_id=session_id,
                    )
                    # 更新 LLM Span 元数据
                    if llm_span and self._trace_collector:
                        self._trace_collector.end_span(
                            llm_span.span_id,
                            output=full_content[:200],
                            metadata={"model": self.model, **usage_dict},
                        )
                elif llm_span and self._trace_collector:
                    # 没有 usage 也要结束 Span
                    self._trace_collector.end_span(
                        llm_span.span_id, output=full_content[:200],
                    )

            except Exception as e:
                logger.error(f"[Engine] LLM 调用失败: {e}")
                # ★ 生命周期：ON_ERROR
                await self._hooks.execute(
                    HookPhase.ON_ERROR,
                    error=e, turn=turn,
                )
                yield RuntimeEvent(
                    type=RuntimeEventType.ERROR,
                    data={"error": str(e)},
                    turn=turn,
                )
                return

            # ★ 生命周期：POST_SAMPLING（注入 usage 供 Hook 使用）
            await self._hooks.execute(
                HookPhase.POST_SAMPLING,
                content=full_content,
                tool_calls=tool_calls_data,
                finish_reason=finish_reason,
                turn=turn,
                usage=stream_usage,
            )

            # ── 无工具调用 → 输出文本结果 ──
            if not tool_calls_data or not any(
                tc["name"] for tc in tool_calls_data
            ):
                self._messages.append({
                    "role": "assistant",
                    "content": full_content,
                })
                # 构建 RESULT 元数据——附带成本和追踪信息
                result_meta: Dict[str, Any] = {}
                if self._cost_tracker:
                    result_meta["cost"] = self._cost_tracker.get_session_cost(session_id)
                if self._trace_collector:
                    result_meta["trace_id"] = self._trace_collector.trace_id

                yield RuntimeEvent(
                    type=RuntimeEventType.RESULT,
                    data=full_content,
                    turn=turn,
                    metadata=result_meta,
                )
                # ★ 生命周期：ON_COMPLETE
                await self._hooks.execute(
                    HookPhase.ON_COMPLETE,
                    result=full_content, turn=turn,
                )
                # ★ 输出 Trace 树
                if self._trace_collector:
                    yield RuntimeEvent(
                        type=RuntimeEventType.TRACE_COMPLETE,
                        data=self._trace_collector.get_trace().to_dict(),
                        turn=turn,
                    )
                return

            # ── 执行工具调用 ──
            logger.info(
                f"[Engine] 🔧 Turn {turn}: "
                f"{len(tool_calls_data)} 个工具调用"
            )

            # 追加 assistant 消息（含 tool_calls）
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": full_content or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                        },
                    }
                    for tc in tool_calls_data
                ],
            }
            self._messages.append(assistant_msg)

            # 逐个执行工具
            for tc in tool_calls_data:
                fn_name = tc["name"]
                try:
                    fn_args = json.loads(tc["arguments"])
                except json.JSONDecodeError:
                    fn_args = {}

                # ★ 生命周期：PRE_TOOL
                await self._hooks.execute(
                    HookPhase.PRE_TOOL,
                    tool_name=fn_name,
                    tool_input=fn_args,
                    turn=turn,
                )

                # 创建工具 Span
                tool_span = None
                if self._trace_collector:
                    tool_span = self._trace_collector.start_span(
                        f"tool:{fn_name}",
                        SpanKind.TOOL,
                        parent_id=parent_span_id,
                        input_preview=json.dumps(fn_args, ensure_ascii=False)[:200],
                    )

                yield RuntimeEvent(
                    type=RuntimeEventType.TOOL_START,
                    data={"name": fn_name, "args": fn_args},
                    turn=turn,
                )

                # 通过微内核管线执行
                result_str = await self._execute_tool(
                    fn_name, fn_args, session_id, turn
                )

                # 结束工具 Span
                if tool_span and self._trace_collector:
                    self._trace_collector.end_span(
                        tool_span.span_id, output=result_str[:200],
                    )

                yield RuntimeEvent(
                    type=RuntimeEventType.TOOL_COMPLETE,
                    data={
                        "name": fn_name,
                        "result": result_str[:300],
                    },
                    turn=turn,
                )

                # ★ 生命周期：POST_TOOL
                await self._hooks.execute(
                    HookPhase.POST_TOOL,
                    tool_name=fn_name,
                    tool_result=result_str,
                    turn=turn,
                )

                # 将 tool result 追加到历史
                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                })

            yield RuntimeEvent(
                type=RuntimeEventType.TURN_END,
                turn=turn,
            )

        # 超过最大轮次——强制文本输出
        logger.warning("[Engine] 达到最大轮次，强制文本输出")
        self._messages.append({
            "role": "user",
            "content": "[系统] 你已完成所有工具调用。现在请直接输出你的回复。",
        })

        try:
            import litellm
            response = await litellm.acompletion(
                model=self.model,
                messages=self._messages,
                api_key=self.api_key,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            final_text = response.choices[0].message.content or "（生成失败）"
            yield RuntimeEvent(
                type=RuntimeEventType.RESULT,
                data=final_text,
                turn=self.max_turns,
            )
        except Exception as e:
            yield RuntimeEvent(
                type=RuntimeEventType.ERROR,
                data={"error": str(e)},
                turn=self.max_turns,
            )

    async def _execute_tool(
        self,
        fn_name: str,
        fn_args: Dict[str, Any],
        session_id: str,
        turn: int,
    ) -> str:
        """通过微内核管线执行单个工具。

        如果没有配置 tool_registry/executor，返回错误信息。
        """
        if not self._tool_registry or not self._tool_executor:
            return json.dumps(
                {"status": "error", "message": "工具执行管线未配置"},
                ensure_ascii=False,
            )

        tool = self._tool_registry.get(fn_name)
        if not tool:
            return json.dumps(
                {"status": "error", "message": f"未知工具: {fn_name}"},
                ensure_ascii=False,
            )

        tool_ctx = {"session_id": session_id}
        try:
            exec_result: ToolExecutionResult = await self._tool_executor.execute(
                tool, fn_args, tool_ctx,
            )
            return exec_result.content
        except Exception as e:
            logger.error(f"[Engine] 工具执行异常: {fn_name} | {e}")
            # ★ 生命周期：ON_ERROR
            await self._hooks.execute(
                HookPhase.ON_ERROR,
                error=e, tool_name=fn_name, turn=turn,
            )
            return json.dumps(
                {"status": "error", "message": f"执行异常: {e}"},
                ensure_ascii=False,
            )

    async def _handle_sniff(
        self, sniff: Any, turn: int
    ) -> AsyncGenerator[RuntimeEvent, None]:
        """处理嗅探结果——触发预热或发出警告事件。"""
        if sniff.action == "preheat" and self._preheater:
            await self._preheater.schedule(sniff.tool_name, sniff.args)
            yield RuntimeEvent(
                type=RuntimeEventType.TOOL_PROGRESS,
                data={
                    "tool": sniff.tool_name,
                    "status": "预加载资源中...",
                    "args": sniff.args,
                },
                turn=turn,
            )
        elif sniff.action == "block":
            logger.warning(
                f"[Engine] 嗅探拦截: {sniff.tool_name} "
                f"reason={sniff.reason}"
            )
            # 阻断事件——引擎继续运行，由消费端决定是否展示
            yield RuntimeEvent(
                type=RuntimeEventType.TOOL_ERROR,
                data={
                    "name": sniff.tool_name,
                    "error": sniff.reason,
                    "source": "sniffer",
                },
                turn=turn,
            )

    async def _submit_structured(
        self,
        litellm_module: Any,
        session_id: str,
        response_format: Type[BaseModel],
        parent_span_id: Optional[str] = None,
        max_retries: int = 3,
    ) -> AsyncGenerator[RuntimeEvent, None]:
        """结构化输出模式——非流式调用 + Pydantic 校验 + 自动重试。

        当 submit() 传入 response_format 时，引擎切换到此方法。
        与流式模式的核心区别：
          - 使用非流式 acompletion（结构化输出不兼容流式）
          - 自动将 Pydantic Model 作为 response_format 传给 LiteLLM
          - 校验失败时自动重试（将错误信息反馈给 LLM 重新生成）

        Args:
            litellm_module: litellm 模块引用。
            session_id: 会话 ID。
            response_format: Pydantic Model 类。
            parent_span_id: 父 Span ID。
            max_retries: 最大重试次数。
        """
        yield RuntimeEvent(
            type=RuntimeEventType.TURN_START,
            turn=1,
            data={
                "messages_count": len(self._messages),
                "mode": "structured_output",
                "schema": response_format.__name__,
            },
        )

        # 创建 LLM Span
        llm_span = None
        if self._trace_collector:
            llm_span = self._trace_collector.start_span(
                f"llm:structured:{response_format.__name__}",
                SpanKind.LLM,
                parent_id=parent_span_id,
                input_preview=self._messages[-1].get("content", "")[:200],
            )

        call_params: Dict[str, Any] = {
            "model": self.model,
            "messages": self._messages,
            "api_key": self.api_key,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": response_format,
        }
        if self.api_base:
            call_params["api_base"] = self.api_base

        last_error = ""
        for attempt in range(max_retries):
            try:
                response = await litellm_module.acompletion(**call_params)
                content = response.choices[0].message.content

                # 提取 usage
                usage = getattr(response, "usage", None)
                if usage and self._cost_tracker:
                    usage_dict = {
                        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                        "completion_tokens": getattr(usage, "completion_tokens", 0),
                        "total_tokens": getattr(usage, "total_tokens", 0),
                    }
                    self._cost_tracker.record(
                        model=self.model, usage=usage_dict,
                        turn=1, session_id=session_id,
                    )

                # Pydantic 校验
                parsed = response_format.model_validate_json(content)

                # 结束 LLM Span
                if llm_span and self._trace_collector:
                    span_meta = {"model": self.model, "attempt": attempt + 1}
                    if usage:
                        span_meta["total_tokens"] = getattr(usage, "total_tokens", 0)
                    self._trace_collector.end_span(
                        llm_span.span_id,
                        output=content[:200],
                        metadata=span_meta,
                    )

                # 构建结果元数据
                result_meta: Dict[str, Any] = {
                    "schema": response_format.__name__,
                    "attempt": attempt + 1,
                }
                if self._cost_tracker:
                    result_meta["cost"] = self._cost_tracker.get_session_cost(session_id)
                if self._trace_collector:
                    result_meta["trace_id"] = self._trace_collector.trace_id

                yield RuntimeEvent(
                    type=RuntimeEventType.RESULT,
                    data=parsed.model_dump(),
                    turn=1,
                    metadata=result_meta,
                )

                # Hook: ON_COMPLETE
                await self._hooks.execute(
                    HookPhase.ON_COMPLETE,
                    result=parsed, turn=1,
                )

                # 输出 Trace
                if self._trace_collector:
                    yield RuntimeEvent(
                        type=RuntimeEventType.TRACE_COMPLETE,
                        data=self._trace_collector.get_trace().to_dict(),
                        turn=1,
                    )
                return

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"[Engine] 结构化输出校验失败 "
                    f"(attempt {attempt + 1}/{max_retries}): {e}"
                )
                # 将错误信息反馈给 LLM，让它重新生成
                if attempt < max_retries - 1:
                    self._messages.append({
                        "role": "user",
                        "content": (
                            f"[系统] 你的上一次输出无法通过 JSON Schema 校验。"
                            f"错误: {e}。请严格按照要求的 JSON Schema 重新输出。"
                        ),
                    })

        # 全部重试耗尽
        if llm_span and self._trace_collector:
            self._trace_collector.end_span(
                llm_span.span_id, error=last_error,
            )

        yield RuntimeEvent(
            type=RuntimeEventType.ERROR,
            data={"error": f"结构化输出失败 ({max_retries} 次重试): {last_error}"},
            turn=1,
        )

