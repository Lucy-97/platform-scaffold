"""
SupervisorAgent — 多智能体编排器
==================================

实现 Supervisor/Worker 模式的多 Agent 协调：
  1. 接收用户输入 → Supervisor LLM 拆分子任务
  2. 并行创建 Worker Agent 执行各子任务（复用 AgentRuntimeEngine）
  3. 收集 Worker 结果 → Supervisor LLM 合并为最终输出
  4. 全程 yield RuntimeEvent 事件流

核心特性:
  - 每个 Worker 是独立的 AgentRuntimeEngine 实例
  - Worker 并行执行（通过 asyncio.Semaphore 控制并发度）
  - Supervisor 自身也是一个 Agent，通过 LLM 决策拆分和合并
  - 与已有 Hook 体系兼容（每个 Worker 可注册独立 Hook）

设计参考:
  - DeerFlow 的 Planner→Researcher→Writer 链
  - Anthropic Agent SDK 的 Swarm 模式
  - 已有 SubagentExecutor 的并发控制
"""

import asyncio
import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from loguru import logger

from agent_core.runtime.agent_node import AgentNode
from agent_core.runtime.events import RuntimeEvent, RuntimeEventType
from agent_core.runtime.handoff import (
    HandoffRequest,
    HandoffResult,
    HandoffStatus,
)


# ---------------------------------------------------------------------------
# Supervisor 拆分 Prompt — 面向 LLM 使用英文
# ---------------------------------------------------------------------------

_PLANNING_SYSTEM_PROMPT = """You are a task planner. Given a user request and a list of available worker agents, decompose the request into subtasks.

## Available Workers
{workers}

## Output Format (strict JSON)

```json
{{
  "subtasks": [
    {{
      "worker": "worker_name",
      "task": "clear instruction for the worker",
      "priority": 5
    }}
  ]
}}
```

## Rules
1. Assign each subtask to the most appropriate worker based on their role
2. If a task can be handled by a single worker, output only one subtask
3. Set priority 1-10 (10 = highest)
4. Keep instructions clear and self-contained
5. Output pure JSON, no markdown"""

_MERGE_SYSTEM_PROMPT = """You are a result synthesizer. Given the user's original request and the results from multiple worker agents, produce a single coherent final answer.

## Worker Results
{results}

## Rules
1. Synthesize all worker outputs into one coherent response
2. Resolve any contradictions between workers
3. Use the language of the original user request
4. Be concise but comprehensive"""


class SupervisorAgent:
    """多智能体编排器 — Supervisor/Worker 模式。

    用法::

        supervisor = SupervisorAgent(
            model="gemini/gemini-2.5-flash",
            api_key="...",
            workers=[
                AgentNode(name="researcher", role="研究员", system_prompt="..."),
                AgentNode(name="writer", role="写作者", system_prompt="..."),
            ],
        )

        async for event in supervisor.run("帮我分析并写一篇关于 AI Agent 的报告"):
            print(event)

    Args:
        model: Supervisor 使用的 LLM 模型标识符。
        api_key: LLM API Key。
        api_base: LLM API Base URL（可选）。
        workers: Worker AgentNode 列表。
        max_concurrent: 最大并行 Worker 数。
        worker_timeout: 单个 Worker 超时（秒）。
        tool_registry: 工具注册表（Worker 共享）。
        tool_executor: 工具执行器（Worker 共享）。
        hooks: 生命周期 Hook 注册表（Worker 共享，可选）。
        cost_tracker: Token 成本追踪器（可选）。
        trace_collector: 链路追踪收集器（可选）。
        memory_store: 五层记忆存储（可选，Worker 共享）。
        memory_retriever: 记忆检索器（可选，Worker 共享）。
        memory_extractor: 记忆提取器（可选，Worker 共享）。
        compaction_budget: Token 预算管控器（可选，Worker 共享）。
        autocompactor: 自动摘要压缩器（可选，Worker 共享）。
    """

    def __init__(
        self,
        model: str = "",
        api_key: str = "",
        api_base: Optional[str] = None,
        workers: Optional[List[AgentNode]] = None,
        max_concurrent: int = 3,
        worker_timeout: float = 120.0,
        tool_registry: Optional[Any] = None,
        tool_executor: Optional[Any] = None,
        hooks: Optional[Any] = None,
        cost_tracker: Optional[Any] = None,
        trace_collector: Optional[Any] = None,
        memory_store: Optional[Any] = None,
        memory_retriever: Optional[Any] = None,
        memory_extractor: Optional[Any] = None,
        compaction_budget: Optional[Any] = None,
        autocompactor: Optional[Any] = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.workers = {w.name: w for w in (workers or [])}
        self.max_concurrent = max_concurrent
        self.worker_timeout = worker_timeout
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._hooks = hooks
        self._cost_tracker = cost_tracker
        self._trace_collector = trace_collector
        self._memory_store = memory_store
        self._memory_retriever = memory_retriever
        self._memory_extractor = memory_extractor
        self._compaction_budget = compaction_budget
        self._autocompactor = autocompactor
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def add_worker(self, node: AgentNode) -> None:
        """动态注册 Worker Agent。"""
        self.workers[node.name] = node
        logger.info(f"[Supervisor] 注册 Worker: {node.name} ({node.role})")

    async def run(
        self,
        user_input: str,
        session_id: str = "default",
    ) -> AsyncGenerator[RuntimeEvent, None]:
        """执行多 Agent 编排流程。

        完整流程: 拆分 → 分发 → 并行执行 → 合并

        Args:
            user_input: 用户输入。
            session_id: 会话 ID。

        Yields:
            RuntimeEvent: 包含 AGENT_* 系列事件。
        """
        import litellm

        start_time = time.time()

        if not self.workers:
            yield RuntimeEvent(
                type=RuntimeEventType.ERROR,
                data={"error": "没有注册任何 Worker Agent"},
            )
            return

        # ═══════════════════════════════════════════════════════════
        # Stage 1: Supervisor LLM 拆分子任务
        # ═══════════════════════════════════════════════════════════

        # 构建 Worker 描述列表
        workers_desc = "\n".join(
            f"- **{w.name}**: {w.role}"
            for w in self.workers.values()
        )
        planning_prompt = _PLANNING_SYSTEM_PROMPT.format(workers=workers_desc)

        yield RuntimeEvent(
            type=RuntimeEventType.AGENT_SPAWN,
            data={
                "agent": "supervisor",
                "action": "planning",
                "workers": list(self.workers.keys()),
            },
        )

        logger.info(
            f"[Supervisor] 开始拆分任务: '{user_input[:80]}...' "
            f"| workers={list(self.workers.keys())}"
        )

        # 创建 Supervisor 根 Span
        supervisor_span = None
        if self._trace_collector:
            from agent_core.runtime.trace import SpanKind
            supervisor_span = self._trace_collector.start_span(
                "supervisor", SpanKind.AGENT,
                input_preview=user_input[:200],
            )

        # 创建 planning Span
        planning_span = None
        if self._trace_collector:
            from agent_core.runtime.trace import SpanKind
            planning_span = self._trace_collector.start_span(
                "llm:planning", SpanKind.LLM,
                parent_id=supervisor_span.span_id if supervisor_span else None,
                input_preview=user_input[:200],
            )

        try:
            plan_response = await litellm.acompletion(
                model=self.model,
                messages=[
                    {"role": "system", "content": planning_prompt},
                    {"role": "user", "content": user_input},
                ],
                api_key=self.api_key,
                api_base=self.api_base,
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            plan_text = plan_response.choices[0].message.content or "{}"
            plan_data = json.loads(plan_text)
            subtasks = plan_data.get("subtasks", [])

            # ★ 记录 planning 调用成本
            plan_usage = getattr(plan_response, "usage", None)
            if plan_usage and self._cost_tracker:
                self._cost_tracker.record(
                    model=self.model,
                    usage={
                        "prompt_tokens": getattr(plan_usage, "prompt_tokens", 0),
                        "completion_tokens": getattr(plan_usage, "completion_tokens", 0),
                        "total_tokens": getattr(plan_usage, "total_tokens", 0),
                    },
                    agent="supervisor:planning",
                    session_id=session_id,
                )
            # 结束 planning Span
            if planning_span and self._trace_collector:
                span_meta = {"model": self.model}
                if plan_usage:
                    span_meta["total_tokens"] = getattr(plan_usage, "total_tokens", 0)
                self._trace_collector.end_span(
                    planning_span.span_id,
                    output=plan_text[:200],
                    metadata=span_meta,
                )
        except Exception as e:
            logger.error(f"[Supervisor] 任务拆分失败: {e}")
            if planning_span and self._trace_collector:
                self._trace_collector.end_span(
                    planning_span.span_id, error=str(e),
                )
            yield RuntimeEvent(
                type=RuntimeEventType.ERROR,
                data={"error": f"任务拆分失败: {e}"},
            )
            return

        if not subtasks:
            logger.warning("[Supervisor] 拆分结果为空，回退到单 Agent 模式")
            # 回退：选第一个 Worker 直接执行
            first_worker = next(iter(self.workers.values()))
            subtasks = [{"worker": first_worker.name, "task": user_input}]

        logger.info(f"[Supervisor] 拆分为 {len(subtasks)} 个子任务")

        # ═══════════════════════════════════════════════════════════
        # Stage 2: 创建 Handoff 请求并分发
        # ═══════════════════════════════════════════════════════════

        handoff_requests: List[HandoffRequest] = []
        for st in subtasks:
            worker_name = st.get("worker", "")
            if worker_name not in self.workers:
                logger.warning(
                    f"[Supervisor] Worker '{worker_name}' 不存在，跳过"
                )
                continue

            req = HandoffRequest(
                from_agent="supervisor",
                to_agent=worker_name,
                task=st.get("task", ""),
                context={"user_input": user_input},
                priority=st.get("priority", 5),
                timeout=self.worker_timeout,
            )
            handoff_requests.append(req)

            yield RuntimeEvent(
                type=RuntimeEventType.AGENT_HANDOFF,
                data={
                    "request_id": req.id,
                    "from": req.from_agent,
                    "to": req.to_agent,
                    "task": req.task[:200],
                },
            )

        # ═══════════════════════════════════════════════════════════
        # Stage 3: 并行执行 Worker Agent
        # ═══════════════════════════════════════════════════════════

        results: List[HandoffResult] = []
        # 收集事件的队列——Worker 产生的事件通过 queue 传递给主生成器
        event_queue: asyncio.Queue[Optional[RuntimeEvent]] = asyncio.Queue()

        async def _run_worker(req: HandoffRequest) -> HandoffResult:
            """在信号量控制下执行单个 Worker。"""
            async with self._semaphore:
                worker_node = self.workers[req.to_agent]
                worker_start = time.time()

                # 为 Worker 创建独立的 Engine 实例
                from agent_core.runtime.engine import AgentRuntimeEngine

                # 创建 Worker Span
                worker_span = None
                if self._trace_collector:
                    from agent_core.runtime.trace import SpanKind
                    worker_span = self._trace_collector.start_span(
                        f"worker:{req.to_agent}",
                        SpanKind.AGENT,
                        parent_id=supervisor_span.span_id if supervisor_span else None,
                        input_preview=req.task[:200],
                    )

                worker_model = worker_node.model or self.model
                engine = AgentRuntimeEngine(
                    model=worker_model,
                    api_key=self.api_key,
                    api_base=self.api_base,
                    max_turns=worker_node.max_turns,
                    temperature=worker_node.temperature,
                    tool_registry=self._tool_registry,
                    tool_executor=self._tool_executor,
                    hooks=self._hooks,
                    cost_tracker=self._cost_tracker,
                    trace_collector=self._trace_collector,
                    memory_store=self._memory_store,
                    memory_retriever=self._memory_retriever,
                    memory_extractor=self._memory_extractor,
                    compaction_budget=self._compaction_budget,
                    autocompactor=self._autocompactor,
                )

                messages = [
                    {"role": "system", "content": worker_node.system_prompt},
                ]

                result_content = ""
                try:
                    async for event in engine.submit(
                        req.task,
                        messages=messages,
                        session_id=f"{req.id}_{req.to_agent}",
                        parent_span_id=worker_span.span_id if worker_span else None,
                    ):
                        # 包装 Worker 事件——添加 agent 来源标识
                        event.metadata["source_agent"] = req.to_agent
                        event.metadata["handoff_id"] = req.id
                        await event_queue.put(event)

                        # 捕获最终结果
                        if event.type == RuntimeEventType.RESULT:
                            result_content = event.data or ""

                    duration = time.time() - worker_start
                    # 结束 Worker Span
                    if worker_span and self._trace_collector:
                        self._trace_collector.end_span(
                            worker_span.span_id,
                            output=result_content[:200],
                            metadata={"duration": round(duration, 2)},
                        )
                    return HandoffResult(
                        request_id=req.id,
                        agent=req.to_agent,
                        status=HandoffStatus.COMPLETED,
                        content=result_content,
                        duration=duration,
                    )

                except asyncio.TimeoutError:
                    return HandoffResult(
                        request_id=req.id,
                        agent=req.to_agent,
                        status=HandoffStatus.TIMEOUT,
                        error=f"Worker 超时 ({self.worker_timeout}s)",
                        duration=time.time() - worker_start,
                    )
                except Exception as e:
                    logger.error(f"[Supervisor] Worker {req.to_agent} 异常: {e}")
                    # 结束 Worker Span（异常）
                    if worker_span and self._trace_collector:
                        self._trace_collector.end_span(
                            worker_span.span_id, error=str(e),
                        )
                    return HandoffResult(
                        request_id=req.id,
                        agent=req.to_agent,
                        status=HandoffStatus.FAILED,
                        error=str(e),
                        duration=time.time() - worker_start,
                    )

        # 启动所有 Worker 协程
        worker_tasks = [
            asyncio.create_task(
                asyncio.wait_for(
                    _run_worker(req), timeout=req.timeout
                )
            )
            for req in handoff_requests
        ]

        # 哨兵：所有 Worker 完成后向 queue 发送 None
        async def _sentinel():
            """等待所有 Worker 完成，然后发送结束信号。"""
            gathered = await asyncio.gather(
                *worker_tasks, return_exceptions=True
            )
            for r in gathered:
                if isinstance(r, HandoffResult):
                    results.append(r)
                elif isinstance(r, Exception):
                    # asyncio.TimeoutError 等
                    results.append(HandoffResult(
                        request_id="unknown",
                        agent="unknown",
                        status=HandoffStatus.FAILED,
                        error=str(r),
                    ))
            await event_queue.put(None)

        sentinel_task = asyncio.create_task(_sentinel())

        # 消费 Worker 事件
        while True:
            event = await event_queue.get()
            if event is None:
                break
            yield event

        await sentinel_task

        # Worker 结果汇报事件
        for r in results:
            yield RuntimeEvent(
                type=RuntimeEventType.AGENT_RESULT,
                data={
                    "agent": r.agent,
                    "status": r.status.value,
                    "content_length": len(r.content),
                    "duration": round(r.duration, 2),
                    "error": r.error or None,
                },
            )

        # ═══════════════════════════════════════════════════════════
        # Stage 4: Supervisor LLM 合并结果
        # ═══════════════════════════════════════════════════════════

        # 只合并成功的结果
        successful = [r for r in results if r.success]
        if not successful:
            yield RuntimeEvent(
                type=RuntimeEventType.ERROR,
                data={"error": "所有 Worker 均执行失败"},
            )
            return

        # 如果只有一个成功的 Worker，直接输出其结果
        if len(successful) == 1:
            yield RuntimeEvent(
                type=RuntimeEventType.AGENT_MERGE,
                data={
                    "strategy": "direct",
                    "workers_count": 1,
                },
            )
            yield RuntimeEvent(
                type=RuntimeEventType.RESULT,
                data=successful[0].content,
            )
            return

        # 多个结果需要 LLM 合并
        results_desc = "\n\n".join(
            f"### {r.agent} (耗时 {r.duration:.1f}s)\n{r.content}"
            for r in successful
        )
        merge_prompt = _MERGE_SYSTEM_PROMPT.format(results=results_desc)

        yield RuntimeEvent(
            type=RuntimeEventType.AGENT_MERGE,
            data={
                "strategy": "llm_synthesis",
                "workers_count": len(successful),
            },
        )

        try:
            # 创建 merge Span
            merge_span = None
            if self._trace_collector:
                from agent_core.runtime.trace import SpanKind
                merge_span = self._trace_collector.start_span(
                    "llm:merge", SpanKind.LLM,
                    parent_id=supervisor_span.span_id if supervisor_span else None,
                    input_preview=f"合并 {len(successful)} 个 Worker 结果",
                )

            # 流式合并——Supervisor LLM 输出最终结果
            merge_response = await litellm.acompletion(
                model=self.model,
                messages=[
                    {"role": "system", "content": merge_prompt},
                    {
                        "role": "user",
                        "content": f"原始请求: {user_input}",
                    },
                ],
                api_key=self.api_key,
                api_base=self.api_base,
                temperature=0.5,
                stream=True,
            )

            full_merge = ""
            async for chunk in merge_response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    full_merge += delta.content
                    yield RuntimeEvent(
                        type=RuntimeEventType.STREAM_DELTA,
                        data=delta.content,
                        metadata={"source_agent": "supervisor"},
                    )

            # 结束 merge Span
            if merge_span and self._trace_collector:
                self._trace_collector.end_span(
                    merge_span.span_id, output=full_merge[:200],
                )

            # 结束 Supervisor 根 Span
            if supervisor_span and self._trace_collector:
                self._trace_collector.end_span(
                    supervisor_span.span_id, output=full_merge[:200],
                )

            # 构建结果元数据
            result_meta: Dict[str, Any] = {
                "total_duration": round(time.time() - start_time, 2),
                "workers_used": [r.agent for r in successful],
            }
            if self._cost_tracker:
                result_meta["cost"] = self._cost_tracker.get_summary()
            if self._trace_collector:
                result_meta["trace_id"] = self._trace_collector.trace_id

            yield RuntimeEvent(
                type=RuntimeEventType.RESULT,
                data=full_merge,
                metadata=result_meta,
            )

            # 输出 Trace 树
            if self._trace_collector:
                yield RuntimeEvent(
                    type=RuntimeEventType.TRACE_COMPLETE,
                    data=self._trace_collector.get_trace().to_dict(),
                )

        except Exception as e:
            logger.error(f"[Supervisor] 结果合并失败: {e}")
            # 合并失败时拼接原始结果
            fallback = "\n\n---\n\n".join(
                f"**[{r.agent}]**\n{r.content}" for r in successful
            )
            yield RuntimeEvent(
                type=RuntimeEventType.RESULT,
                data=fallback,
                metadata={"merge_fallback": True},
            )
