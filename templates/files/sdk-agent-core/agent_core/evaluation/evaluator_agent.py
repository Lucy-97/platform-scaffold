"""
Evaluator Agent 评估服务 — evaluator_agent.py
==============================================

借鉴 agency-agents 的 EvidenceQA 机制，实现 Dev-QA 闭环验证。

核心模式::

    生成 Agent → 产出内容
        ↓
    Evaluator Agent → 评估 + PASS/FAIL
        ↓  (FAIL)
    生成 Agent → 根据反馈修正 (最多 max_retries 次)
        ↓
    Evaluator Agent → 再次评估
        ↓  (PASS)
    进入下一步

使用场景（通用）：
  - 业务流水线各步骤间的质量校验
  - 生成内容的逻辑自洽性检查
  - 结构化输出的格式验证
  - 代码生成的正确性审计

在编排中的集成方式::

    result = await evaluate_with_retry(
        generator=my_gen_fn,
        evaluator=evaluator,
        context={"task": "...", "step": "review"},
        max_retries=3,
    )
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# 评估结果数据模型
# ---------------------------------------------------------------------------

class EvalVerdict(str, Enum):
    """评估判定结果。"""
    PASS = "PASS"          # 通过
    FAIL = "FAIL"          # 不通过，需修正
    PASS_WITH_NOTES = "PASS_WITH_NOTES"  # 通过但有建议
    SKIP = "SKIP"          # 跳过评估（如评估器不可用）


@dataclass
class EvalCriterion:
    """评估标准项。

    Attributes:
        name: 标准名称（如 "叙事连贯性"）。
        description: 标准描述。
        weight: 权重 (0.0-1.0)，用于加权评分。
        required: 是否必须通过（HARD-GATE 语义）。
    """
    name: str
    description: str = ""
    weight: float = 1.0
    required: bool = False


@dataclass
class EvalResult:
    """单次评估结果。

    Attributes:
        verdict: PASS / FAIL / PASS_WITH_NOTES。
        score: 综合评分 (0-100)。
        feedback: 评估意见文本（供生成 Agent 参考修正）。
        criteria_results: 每个标准项的评分和意见。
        retry_count: 当前已重试次数。
        metadata: 额外元数据。
    """
    verdict: EvalVerdict = EvalVerdict.FAIL
    score: float = 0.0
    feedback: str = ""
    criteria_results: List[Dict[str, Any]] = field(default_factory=list)
    retry_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 评估 Prompt 模板
# ---------------------------------------------------------------------------

EVALUATOR_SYSTEM_PROMPT = """你是一个质量评估专家（QA Evaluator Agent）。
你的任务是评估其他 Agent 的产出质量，提供结构化的通过/失败判定。

## 🧠 Identity & Memory
你是一个严谨的审查者。你见过大量高质量和低质量的内容，
能准确识别逻辑漏洞、不完整产出和低质量表达。

## 🚨 Critical Rules
1. **必须基于给定标准评估**：不要自创标准
2. **每项标准独立评分**：给出 0-100 分和具体意见
3. **FAIL 必须可操作**：失败意见必须具体到修改建议
4. **PASS 不等于完美**：通过但有建议时用 PASS_WITH_NOTES

## 🛠️ 输出格式 (严格 JSON)

```json
{
  "verdict": "PASS|FAIL|PASS_WITH_NOTES",
  "score": 75,
  "feedback": "整体评价摘要",
  "criteria_results": [
    {
      "criterion": "标准名",
      "score": 80,
      "passed": true,
      "comment": "具体意见"
    }
  ]
}
```
"""

EVALUATION_PROMPT_TEMPLATE = """## 评估任务

请评估以下内容是否满足质量标准。

### 评估上下文
{context}

### 待评估内容
{content}

### 评估标准
{criteria}

### 指令
按照上述标准逐项评估，给出结构化的 JSON 结果。
如果任何 required 标准未通过，verdict 必须为 FAIL。
"""


# ---------------------------------------------------------------------------
# Evaluator Agent
# ---------------------------------------------------------------------------

class EvaluatorAgent:
    """评估 Agent — 负责对其他 Agent 的产出进行质量校验。

    借鉴 agency-agents 的 EvidenceQA Agent 模式：
    - 支持自定义评估标准
    - 支持 HARD-GATE (required) 强制校验
    - 提供结构化评估结果和修正建议

    Args:
        name: 评估器名称。
        criteria: 评估标准列表。
        pass_threshold: 通过分数阈值 (0-100)。
        llm_evaluator: LLM 评估回调函数（用于实际 LLM 调用）。
    """

    def __init__(
        self,
        name: str = "default_evaluator",
        criteria: Optional[List[EvalCriterion]] = None,
        pass_threshold: float = 70.0,
        llm_evaluator: Optional[Callable] = None,
    ):
        self.name = name
        self.criteria = criteria or []
        self.pass_threshold = pass_threshold
        self.llm_evaluator = llm_evaluator

    async def evaluate(
        self,
        content: str,
        context: str = "",
    ) -> EvalResult:
        """评估内容质量。

        如果配置了 llm_evaluator，则调用 LLM 做评估；
        否则使用基于规则的评估（检查内容非空、长度等）。

        Args:
            content: 待评估的内容文本。
            context: 评估上下文（如故事背景、步骤信息）。

        Returns:
            EvalResult 评估结果。
        """
        if self.llm_evaluator:
            return await self._llm_evaluate(content, context)
        else:
            return self._rule_based_evaluate(content, context)

    async def _llm_evaluate(
        self,
        content: str,
        context: str,
    ) -> EvalResult:
        """使用 LLM 进行评估。

        构建评估 Prompt 并调用 LLM，解析结构化评估结果。

        Args:
            content: 待评估内容。
            context: 评估上下文。

        Returns:
            LLM 评估结果。
        """
        criteria_text = "\n".join(
            f"- **{c.name}** (权重={c.weight}, 必须={'是' if c.required else '否'}): {c.description}"
            for c in self.criteria
        )

        prompt = EVALUATION_PROMPT_TEMPLATE.format(
            context=context or "无特殊上下文",
            content=content[:5000],
            criteria=criteria_text or "- 内容完整性\n- 逻辑自洽性\n- 格式规范性",
        )

        try:
            raw_result = await self.llm_evaluator(prompt)
            return self._parse_llm_result(raw_result)
        except Exception as e:
            logger.error(
                f"[EvaluatorAgent:{self.name}] LLM evaluation failed: {e}"
            )
            return EvalResult(
                verdict=EvalVerdict.SKIP,
                feedback=f"评估器异常: {e}",
            )

    def _parse_llm_result(self, raw: str) -> EvalResult:
        """解析 LLM 返回的 JSON 评估结果。

        Args:
            raw: LLM 返回的文本（应为 JSON）。

        Returns:
            解析后的 EvalResult。
        """
        try:
            # 尝试提取 JSON（LLM 可能返回 Markdown 包裹的 JSON）
            json_str = raw
            if "```json" in raw:
                json_str = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                json_str = raw.split("```")[1].split("```")[0]

            data = json.loads(json_str.strip())

            verdict_str = data.get("verdict", "FAIL").upper()
            try:
                verdict = EvalVerdict(verdict_str)
            except ValueError:
                verdict = EvalVerdict.FAIL

            return EvalResult(
                verdict=verdict,
                score=float(data.get("score", 0)),
                feedback=data.get("feedback", ""),
                criteria_results=data.get("criteria_results", []),
            )
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(
                f"[EvaluatorAgent:{self.name}] Failed to parse LLM result: {e}"
            )
            return EvalResult(
                verdict=EvalVerdict.FAIL,
                feedback=f"评估结果解析失败: {raw[:200]}",
            )

    def _rule_based_evaluate(
        self,
        content: str,
        context: str,
    ) -> EvalResult:
        """基于规则的简单评估（LLM 不可用时的 fallback）。

        检查内容是否非空、长度足够、包含关键结构等。

        Args:
            content: 待评估内容。
            context: 评估上下文。

        Returns:
            规则评估结果。
        """
        criteria_results = []
        total_score = 0.0
        total_weight = 0.0
        all_required_pass = True

        for criterion in self.criteria:
            # 默认规则：内容是否包含标准名称相关关键词
            passed = bool(content.strip())
            score = 80.0 if passed else 0.0
            comment = "内容存在" if passed else "内容为空"

            if not passed and criterion.required:
                all_required_pass = False

            criteria_results.append({
                "criterion": criterion.name,
                "score": score,
                "passed": passed,
                "comment": comment,
            })

            total_score += score * criterion.weight
            total_weight += criterion.weight

        final_score = (total_score / total_weight) if total_weight > 0 else 0.0

        if not all_required_pass:
            verdict = EvalVerdict.FAIL
        elif final_score >= self.pass_threshold:
            verdict = EvalVerdict.PASS
        else:
            verdict = EvalVerdict.FAIL

        return EvalResult(
            verdict=verdict,
            score=final_score,
            feedback=f"基于规则评估: 综合分数 {final_score:.1f}/{self.pass_threshold}",
            criteria_results=criteria_results,
        )


# ---------------------------------------------------------------------------
# Dev-QA 闭环执行器
# ---------------------------------------------------------------------------

async def evaluate_with_retry(
    generator: Callable,
    evaluator: EvaluatorAgent,
    context: Dict[str, Any],
    max_retries: int = 3,
    pass_threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """Dev-QA 闭环：生成 → 评估 → 修正 → 再评估。

    借鉴 agency-agents 的 EvidenceQA 循环模式。
    生成器每次接收上一次的评估反馈进行修正。

    Args:
        generator: 内容生成函数，签名 async def(context, feedback?) -> str。
        evaluator: 评估 Agent 实例。
        context: 生成上下文。
        max_retries: 最大重试次数。
        pass_threshold: 覆盖评估器的通过阈值。

    Returns:
        包含最终内容、评估结果和重试历史的字典::

            {
                "content": "最终生成的内容",
                "eval_result": EvalResult,
                "passed": True/False,
                "retries": 2,
                "history": [...]
            }
    """
    if pass_threshold is not None:
        evaluator.pass_threshold = pass_threshold

    history = []
    feedback = ""

    for attempt in range(max_retries + 1):
        # --- 生成阶段 ---
        try:
            if attempt == 0:
                content = await generator(context)
            else:
                # 后续尝试携带评估反馈
                context_with_feedback = {
                    **context,
                    "previous_feedback": feedback,
                    "retry_attempt": attempt,
                }
                content = await generator(context_with_feedback)
        except Exception as e:
            logger.error(
                f"[evaluate_with_retry] Generator failed at attempt {attempt}: {e}"
            )
            history.append({
                "attempt": attempt,
                "error": str(e),
                "verdict": "ERROR",
            })
            continue

        # --- 评估阶段 ---
        eval_context = context.get("eval_context", "")
        eval_result = await evaluator.evaluate(content, eval_context)
        eval_result.retry_count = attempt

        history.append({
            "attempt": attempt,
            "content_length": len(content),
            "verdict": eval_result.verdict.value,
            "score": eval_result.score,
            "feedback": eval_result.feedback[:200],
        })

        logger.info(
            f"[evaluate_with_retry] Attempt {attempt + 1}/{max_retries + 1}: "
            f"verdict={eval_result.verdict.value}, score={eval_result.score:.1f}"
        )

        # --- 判定 ---
        if eval_result.verdict in (EvalVerdict.PASS, EvalVerdict.PASS_WITH_NOTES):
            return {
                "content": content,
                "eval_result": eval_result,
                "passed": True,
                "retries": attempt,
                "history": history,
            }

        # 提取反馈用于下一次重试
        feedback = eval_result.feedback
        if eval_result.criteria_results:
            failed_items = [
                cr for cr in eval_result.criteria_results
                if not cr.get("passed", True)
            ]
            if failed_items:
                feedback += "\n\n未通过项:\n" + "\n".join(
                    f"- {item['criterion']}: {item.get('comment', '')}"
                    for item in failed_items
                )

    # 所有重试用尽
    logger.warning(
        f"[evaluate_with_retry] All {max_retries + 1} attempts exhausted. "
        f"Last score: {eval_result.score:.1f}"
    )
    return {
        "content": content,
        "eval_result": eval_result,
        "passed": False,
        "retries": max_retries,
        "history": history,
    }



