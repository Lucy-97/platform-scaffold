"""
Trace / Span — 轻量级链路追踪系统
====================================

为多 Agent + 工具调用链路提供结构化的 Span 树追踪，
类似 OpenTelemetry 的 Span 概念，但极度轻量，无外部依赖。

核心概念:
  - Span: 最小追踪单元（一次 LLM 调用、一次工具执行、一次 Agent 运行）
  - Trace: 一次完整执行的 Span 树（从用户输入到最终输出）
  - TraceCollector: 在运行时收集 Span，构建层级关系

Span 层级示例::

    [supervisor] root
      ├── [llm] planning
      ├── [agent] worker:researcher
      │     ├── [llm] turn_1
      │     └── [tool] search_web
      ├── [agent] worker:analyst
      │     ├── [llm] turn_1
      │     └── [tool] analyze_data
      └── [llm] merge

未来扩展:
  - to_otlp() 导出到 OpenTelemetry Collector
  - to_langsmith() 导出到 LangSmith
"""

import time
import uuid
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SpanKind(str, Enum):
    """Span 类型。"""
    LLM = "llm"          # LLM 调用
    TOOL = "tool"        # 工具执行
    AGENT = "agent"      # Agent 级别（Worker / Supervisor）
    INTERNAL = "internal"  # 内部操作（如压缩、合并）


@dataclass
class Span:
    """单个追踪 Span。

    Attributes:
        span_id: 唯一 ID。
        parent_id: 父 Span ID（根 Span 为 None）。
        name: Span 名称（如 "llm:turn_1", "tool:search_web"）。
        kind: Span 类型。
        start_time: 开始时间戳。
        end_time: 结束时间戳（Span 结束时填入）。
        input_preview: 输入预览（截断后的字符串）。
        output_preview: 输出预览。
        metadata: 附加元数据（model, tokens, agent_name 等）。
        error: 错误信息（如有）。
    """
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    parent_id: Optional[str] = None
    name: str = ""
    kind: SpanKind = SpanKind.INTERNAL
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    input_preview: str = ""
    output_preview: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        """Span 持续时间（毫秒）。"""
        if self.end_time is None:
            return (time.time() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000

    @property
    def is_finished(self) -> bool:
        return self.end_time is not None

    def finish(self, output: str = "", error: str = "") -> None:
        """结束 Span。"""
        self.end_time = time.time()
        if output:
            self.output_preview = output[:200]
        if error:
            self.error = error

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典。"""
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind.value,
            "duration_ms": round(self.duration_ms, 1),
            "input": self.input_preview[:100] if self.input_preview else "",
            "output": self.output_preview[:100] if self.output_preview else "",
            "metadata": self.metadata,
            "error": self.error,
        }


@dataclass
class Trace:
    """一次完整执行的 Span 树。

    Attributes:
        trace_id: 追踪 ID。
        spans: 所有 Span 列表。
    """
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    spans: List[Span] = field(default_factory=list)

    @property
    def root_span(self) -> Optional[Span]:
        """获取根 Span。"""
        for s in self.spans:
            if s.parent_id is None:
                return s
        return self.spans[0] if self.spans else None

    @property
    def total_duration_ms(self) -> float:
        """总持续时间。"""
        root = self.root_span
        return root.duration_ms if root else 0

    @property
    def total_tokens(self) -> int:
        """汇总所有 LLM Span 的 Token。"""
        total = 0
        for s in self.spans:
            if s.kind == SpanKind.LLM:
                total += s.metadata.get("total_tokens", 0)
        return total

    @property
    def total_llm_calls(self) -> int:
        return sum(1 for s in self.spans if s.kind == SpanKind.LLM)

    @property
    def total_tool_calls(self) -> int:
        return sum(1 for s in self.spans if s.kind == SpanKind.TOOL)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可导出的字典。"""
        return {
            "trace_id": self.trace_id,
            "total_duration_ms": round(self.total_duration_ms, 1),
            "total_tokens": self.total_tokens,
            "llm_calls": self.total_llm_calls,
            "tool_calls": self.total_tool_calls,
            "spans": [s.to_dict() for s in self.spans],
        }

    def to_tree(self, indent: int = 0) -> str:
        """以缩进树形格式渲染（CLI 调试用）。"""
        # 构建 parent → children 映射
        children_map: Dict[Optional[str], List[Span]] = {}
        for s in self.spans:
            children_map.setdefault(s.parent_id, []).append(s)

        lines: List[str] = []
        self._render_node(self.root_span, children_map, lines, indent)
        return "\n".join(lines)

    def _render_node(
        self,
        span: Optional[Span],
        children_map: Dict[Optional[str], List[Span]],
        lines: List[str],
        depth: int,
    ) -> None:
        if not span:
            return
        prefix = "  " * depth
        # 根据类型选择图标
        icons = {
            SpanKind.LLM: "🤖",
            SpanKind.TOOL: "🔧",
            SpanKind.AGENT: "👤",
            SpanKind.INTERNAL: "⚙️",
        }
        icon = icons.get(span.kind, "·")
        dur = f"{span.duration_ms:.0f}ms"
        tokens = ""
        if span.kind == SpanKind.LLM and span.metadata.get("total_tokens"):
            tokens = f" | {span.metadata['total_tokens']}tok"
        error = f" ❌{span.error}" if span.error else ""
        lines.append(f"{prefix}{icon} {span.name} ({dur}{tokens}){error}")
        # 递归渲染子节点
        for child in children_map.get(span.span_id, []):
            self._render_node(child, children_map, lines, depth + 1)


class TraceCollector:
    """线程安全的 Span 收集器。

    在 Engine/Supervisor 运行过程中收集 Span，
    最终构建完整的 Trace 树。

    使用示例::

        collector = TraceCollector()
        span = collector.start_span("llm:turn_1", SpanKind.LLM)
        # ... 执行 LLM 调用 ...
        collector.end_span(span.span_id, output="你好",
                           metadata={"total_tokens": 150})
        trace = collector.get_trace()
        print(trace.to_tree())
    """

    def __init__(self) -> None:
        self._trace = Trace()
        self._spans: Dict[str, Span] = {}
        self._lock = threading.Lock()

    @property
    def trace_id(self) -> str:
        return self._trace.trace_id

    def start_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        parent_id: Optional[str] = None,
        input_preview: str = "",
    ) -> Span:
        """创建并启动一个新 Span。

        Args:
            name: Span 名称。
            kind: Span 类型。
            parent_id: 父 Span ID（形成树状层级）。
            input_preview: 输入预览文本。

        Returns:
            新建的 Span 实例。
        """
        span = Span(
            name=name,
            kind=kind,
            parent_id=parent_id,
            input_preview=input_preview[:200] if input_preview else "",
        )
        with self._lock:
            self._spans[span.span_id] = span
            self._trace.spans.append(span)
        return span

    def end_span(
        self,
        span_id: str,
        output: str = "",
        error: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """结束指定 Span。

        Args:
            span_id: Span ID。
            output: 输出预览。
            error: 错误信息。
            metadata: 附加元数据（如 token 用量）。
        """
        with self._lock:
            span = self._spans.get(span_id)
        if not span:
            return
        span.finish(output=output, error=error)
        if metadata:
            span.metadata.update(metadata)

    def get_trace(self) -> Trace:
        """获取当前 Trace 快照。"""
        return self._trace

    def get_span(self, span_id: str) -> Optional[Span]:
        """获取指定 Span。"""
        with self._lock:
            return self._spans.get(span_id)
