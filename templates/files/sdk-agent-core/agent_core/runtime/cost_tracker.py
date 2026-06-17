"""
CostTracker — 真实 Token 成本追踪器
====================================

从 litellm 流式/非流式响应中提取真实的 usage 数据，
按 session / agent / model 维度累计追踪，替代不可靠的字符数估算。

集成方式:
  - AgentRuntimeEngine 在每次 LLM 调用后调用 tracker.record()
  - SupervisorAgent 的非流式调用（planning / merge）同样记录
  - 最终通过 RESULT 事件的 metadata 附带累计成本

使用示例::

    tracker = CostTracker()
    engine = AgentRuntimeEngine(..., cost_tracker=tracker)
    # ... 执行完成后
    print(tracker.get_summary())
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class CostRecord:
    """单次 LLM 调用的计费记录。

    Attributes:
        model: 模型标识符（如 gemini/gemini-2.0-flash）。
        prompt_tokens: 输入 Token 数。
        completion_tokens: 输出 Token 数。
        total_tokens: 总 Token 数。
        agent: 调用来源 Agent 名称（可选）。
        turn: 当前轮次。
        session_id: 会话 ID。
        timestamp: 记录时间戳。
    """
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    agent: str = ""
    turn: int = 0
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def cost_usd(self) -> float:
        """估算美元成本（基于常见定价，仅供参考）。

        实际价格应根据厂商定价动态计算，这里提供粗略参考。
        """
        # 粗略定价表（每百万 Token）— 取中间值
        _PRICING = {
            "gemini": {"input": 0.075, "output": 0.30},
            "gpt-4o": {"input": 2.5, "output": 10.0},
            "claude": {"input": 3.0, "output": 15.0},
        }
        provider = self.model.split("/")[0] if "/" in self.model else self.model
        prices = _PRICING.get(provider, {"input": 1.0, "output": 3.0})
        return (
            self.prompt_tokens * prices["input"] / 1_000_000
            + self.completion_tokens * prices["output"] / 1_000_000
        )


class CostTracker:
    """线程安全的 Token 成本累加器。

    支持多 Agent 并行场景下的安全记录。

    使用示例::

        tracker = CostTracker()
        tracker.record(model="gemini/gemini-2.0-flash",
                       usage={"prompt_tokens": 100, "completion_tokens": 50},
                       agent="researcher", session_id="s1")
        print(tracker.get_summary())
    """

    def __init__(self) -> None:
        self._records: List[CostRecord] = []
        self._lock = threading.Lock()

    def record(
        self,
        model: str = "",
        usage: Optional[Dict[str, Any]] = None,
        agent: str = "",
        turn: int = 0,
        session_id: str = "",
    ) -> CostRecord:
        """记录一次 LLM 调用的 Token 消耗。

        Args:
            model: 模型标识符。
            usage: litellm 返回的 usage 字典，
                   含 prompt_tokens / completion_tokens / total_tokens。
            agent: 调用来源 Agent。
            turn: 当前轮次。
            session_id: 会话 ID。

        Returns:
            创建的 CostRecord。
        """
        usage = usage or {}
        rec = CostRecord(
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            agent=agent,
            turn=turn,
            session_id=session_id,
        )
        with self._lock:
            self._records.append(rec)

        logger.debug(
            f"[CostTracker] 📊 {agent or 'engine'} | "
            f"model={model} | "
            f"in={rec.prompt_tokens} out={rec.completion_tokens} "
            f"total={rec.total_tokens}"
        )
        return rec

    def get_session_cost(self, session_id: str) -> Dict[str, Any]:
        """获取指定会话的成本汇总。"""
        with self._lock:
            records = [r for r in self._records if r.session_id == session_id]
        return self._aggregate(records)

    def get_summary(self) -> Dict[str, Any]:
        """获取全局成本汇总。"""
        with self._lock:
            records = list(self._records)
        return self._aggregate(records)

    def get_by_agent(self) -> Dict[str, Dict[str, Any]]:
        """按 Agent 维度汇总。"""
        with self._lock:
            records = list(self._records)
        agents: Dict[str, List[CostRecord]] = {}
        for r in records:
            agents.setdefault(r.agent or "engine", []).append(r)
        return {
            agent: self._aggregate(recs) for agent, recs in agents.items()
        }

    def reset(self) -> None:
        """清零所有记录。"""
        with self._lock:
            self._records.clear()

    @staticmethod
    def _aggregate(records: List[CostRecord]) -> Dict[str, Any]:
        """聚合计算。"""
        total_prompt = sum(r.prompt_tokens for r in records)
        total_completion = sum(r.completion_tokens for r in records)
        total_tokens = sum(r.total_tokens for r in records)
        total_cost = sum(r.cost_usd for r in records)
        return {
            "calls": len(records),
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(total_cost, 6),
        }
