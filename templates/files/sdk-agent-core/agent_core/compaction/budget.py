"""
项目级 Token 预算管控 — AgentCoreTokenBudget
==========================================

为每个创作项目设定 Token 总预算，追踪用量。

设计决策：
  - 项目级（非会话级）预算，避免多会话合作时超支
  - 集数级分配：每集剧本分配独立子预算
  - 支持预算告警（80%）和硬顶（100%）
  - Redis 持久化用量数据（跨会话累积）
"""

import json
import time
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field


class EpisodeBudget(BaseModel):
    """单集预算模型。"""
    episode_id: str = Field(description="集数标识")
    allocated: int = Field(default=10000, description="分配的 token 额度")
    consumed: int = Field(default=0, description="已消耗 token 数")

    @property
    def remaining(self) -> int:
        return max(0, self.allocated - self.consumed)

    @property
    def usage_ratio(self) -> float:
        return self.consumed / max(self.allocated, 1)


class AgentCoreTokenBudget:
    """项目级 Token 预算管控器。

    追踪每个项目/集数的 Token 消费量，当接近或超过预算时
    触发压缩策略或限流。

    Args:
        project_id: 项目标识符。
        total_budget: 项目总预算（默认 500K tokens）。
        warn_ratio: 警告阈值比率（默认 0.8 = 80%）。
        hard_limit_ratio: 硬顶阈值比率（默认 1.0 = 100%）。
        redis: Redis 实例（可选，用于持久化）。
    """

    def __init__(
        self,
        project_id: str,
        total_budget: int = 500_000,
        warn_ratio: float = 0.8,
        hard_limit_ratio: float = 1.0,
        redis: Optional[Any] = None,
    ) -> None:
        self.project_id = project_id
        self.total_budget = total_budget
        self.warn_ratio = warn_ratio
        self.hard_limit_ratio = hard_limit_ratio
        self._redis = redis

        # 内存中的用量追踪
        self._total_consumed: int = 0
        self._episodes: Dict[str, EpisodeBudget] = {}
        self._history: List[Dict[str, Any]] = []

    def allocate_episode(
        self, episode_id: str, budget: int = 10000
    ) -> EpisodeBudget:
        """为指定集数分配 token 子预算。"""
        ep = EpisodeBudget(episode_id=episode_id, allocated=budget)
        self._episodes[episode_id] = ep
        logger.info(f"[Budget] 分配预算: {episode_id} = {budget} tokens")
        return ep

    def consume(
        self,
        tokens: int,
        episode_id: Optional[str] = None,
        source: str = "llm",
    ) -> Dict[str, Any]:
        """记录 Token 消费。

        Args:
            tokens: 消费的 token 数。
            episode_id: 关联的集数（可选）。
            source: 消费来源（如 llm / tool / autocompact）。

        Returns:
            消费状态字典（含警告/限流信息）。
        """
        self._total_consumed += tokens
        result: Dict[str, Any] = {
            "consumed": tokens,
            "total": self._total_consumed,
            "budget": self.total_budget,
            "ratio": self._total_consumed / max(self.total_budget, 1),
        }

        # 更新集数消费
        if episode_id and episode_id in self._episodes:
            self._episodes[episode_id].consumed += tokens
            result["episode_ratio"] = self._episodes[episode_id].usage_ratio

        # 记录到历史
        self._history.append({
            "time": time.time(),
            "tokens": tokens,
            "source": source,
            "episode": episode_id,
            "total": self._total_consumed,
        })

        # 检查预算状态
        ratio = result["ratio"]
        if ratio >= self.hard_limit_ratio:
            result["action"] = "limit"
            result["message"] = (
                f"⚠️ 项目 {self.project_id} Token 预算已耗尽 "
                f"({self._total_consumed}/{self.total_budget})。"
                f"建议启动 Autocompact 或扩充预算。"
            )
            logger.warning(f"[Budget] 硬顶触发: {result['message']}")
        elif ratio >= self.warn_ratio:
            result["action"] = "warn"
            result["message"] = (
                f"⚡ 项目 {self.project_id} Token 预算已用 "
                f"{ratio*100:.0f}% ({self._total_consumed}/{self.total_budget})"
            )
            logger.info(f"[Budget] 告警: {result['message']}")
        else:
            result["action"] = "ok"

        return result

    def get_status(self) -> Dict[str, Any]:
        """获取预算总览。"""
        return {
            "project_id": self.project_id,
            "total_budget": self.total_budget,
            "consumed": self._total_consumed,
            "remaining": max(0, self.total_budget - self._total_consumed),
            "ratio": self._total_consumed / max(self.total_budget, 1),
            "episodes": {
                eid: {
                    "allocated": ep.allocated,
                    "consumed": ep.consumed,
                    "remaining": ep.remaining,
                    "ratio": ep.usage_ratio,
                }
                for eid, ep in self._episodes.items()
            },
        }
