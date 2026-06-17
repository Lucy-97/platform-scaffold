"""
Compaction 管线单元测试
========================

验证 SnipCompactor、AgentCoreTokenBudget、ToolResultDehydrator 和 Autocompactor 的核心逻辑。
"""

import pytest

from agent_core.compaction.snip import SnipCompactor
from agent_core.compaction.budget import AgentCoreTokenBudget
from agent_core.compaction.microcompact import ToolResultDehydrator
from agent_core.compaction.autocompact import Autocompactor


# ── SnipCompactor Tests ──


class TestSnipCompactor:
    """正则清洗器测试。"""

    def test_compact_removes_dead_text(self):
        """应清除空行、多余空白等死文本。"""
        snip = SnipCompactor()
        messages = [
            {"role": "user", "content": "你好"},
            {
                "role": "assistant",
                "content": "你好！\n\n\n\n   \n以下是分析结果：\n\n\n\n点一\n\n\n\n\n点二\n\n\n",
            },
        ]
        new_msgs, freed = snip.compact(messages)
        # 应压缩多余空行
        assert freed > 0
        # 内容中不应有连续 3 个以上空行
        content = new_msgs[1]["content"]
        assert "\n\n\n\n" not in content

    def test_compact_no_op_on_clean(self):
        """干净消息不应被修改。"""
        snip = SnipCompactor()
        messages = [
            {"role": "user", "content": "简单问题"},
            {"role": "assistant", "content": "简单回答"},
        ]
        _, freed = snip.compact(messages)
        assert freed == 0


# ── AgentCoreTokenBudget Tests ──


class TestTokenBudget:
    """Token 预算管控器测试。"""

    def test_consume_normal(self):
        """正常消费不触发告警。"""
        budget = AgentCoreTokenBudget(project_id="test", total_budget=10000)
        result = budget.consume(1000, source="llm")
        assert result["action"] == "ok"
        assert budget._total_consumed == 1000

    def test_consume_warn_threshold(self):
        """消费达到告警阈值时应返回 warn。"""
        budget = AgentCoreTokenBudget(
            project_id="test", total_budget=10000, warn_ratio=0.7,
        )
        # 消费 70%+
        budget.consume(7500, source="llm")
        result = budget.consume(100, source="llm")
        assert result["action"] == "warn"

    def test_consume_limit_threshold(self):
        """消费超出硬顶阈值时应返回 limit。"""
        budget = AgentCoreTokenBudget(
            project_id="test", total_budget=10000, hard_limit_ratio=0.95,
        )
        budget.consume(9600, source="llm")
        result = budget.consume(100, source="llm")
        assert result["action"] == "limit"

    def test_budget_status(self):
        """状态汇总应反映累积消费。"""
        budget = AgentCoreTokenBudget(project_id="test", total_budget=10000)
        budget.consume(3000, source="llm")
        budget.consume(500, source="tool")
        status = budget.get_status()
        assert status["consumed"] == 3500
        assert status["total_budget"] == 10000
        assert status["remaining"] == 6500


# ── ToolResultDehydrator Tests ──


class TestDehydrator:
    """工具返回脱水器测试。"""

    def test_dehydrate_large_results(self):
        """超过保留轮次的大工具结果应被脱水。"""
        dehydrator = ToolResultDehydrator(keep_turns=1)
        messages = [
            {"role": "user", "content": "读取文件"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "A" * 500,  # 超长工具结果
                "_tool_name": "read_file",
                "_turn": 0,
            },
        ]
        # current_turn=2 → turn_gap=2 > keep_turns=1 → 触发脱水
        _, freed = dehydrator.dehydrate(messages, current_turn=2)
        assert freed > 0
        assert len(messages[2]["content"]) < 500


# ── Autocompactor Tests ──


class TestAutocompactor:
    """自动摘要压缩器测试——仅测试触发判定，不调用真实 LLM。"""

    def test_should_trigger_below_threshold(self):
        """Token 未超阈值时不触发。"""
        compactor = Autocompactor(
            trigger_ratio=0.8,
            token_limit=100_000,
        )
        messages = [
            {"role": "user", "content": "短消息"},
            {"role": "assistant", "content": "短回复"},
        ]
        assert compactor.should_trigger(messages) is False

    def test_should_trigger_above_threshold(self):
        """Token 超阈值时触发。"""
        compactor = Autocompactor(
            trigger_ratio=0.8,
            token_limit=100,  # 极低阈值
        )
        messages = [
            {"role": "user", "content": "A" * 200},
            {"role": "assistant", "content": "B" * 200},
        ]
        assert compactor.should_trigger(messages) is True

    def test_estimate_tokens(self):
        """Token 估算准确性。"""
        compactor = Autocompactor()
        messages = [
            {"role": "user", "content": "A" * 100},
            {"role": "assistant", "content": "B" * 100},
        ]
        estimated = compactor.estimate_tokens(messages)
        # 200 chars × 0.75 ≈ 150 tokens
        assert 140 <= estimated <= 160

    @pytest.mark.asyncio
    async def test_compact_no_trigger(self):
        """未触发时应原样返回。"""
        compactor = Autocompactor(
            trigger_ratio=0.8,
            token_limit=100_000,
        )
        messages = [
            {"role": "user", "content": "短消息"},
        ]
        result, freed = await compactor.compact(messages)
        assert freed == 0
        assert result == messages
