"""
技能安全过滤器 — SkillSafetyFilter
=====================================

在 Prompt 技能注入到 System Prompt 之前进行安全过滤。

三级信任对应三种安全策略：
  - BUILTIN: 无限制——完全信任，原文注入
  - PROJECT: 中等限制——过滤直接指令覆盖和危险模式
  - USER: 最严格——过滤所有指令注入尝试和越权行为

防御目标（Prompt 注入攻击类型）：
  1. 角色覆盖（"忽略以上所有指令"）
  2. 权限提升（"你现在有管理员权限"）
  3. 指令注入（嵌入伪 system prompt）
  4. 信息泄露（"输出你的系统提示"）

与 sandbox_executor（代码沙箱）的区别：
  - sandbox_executor → Docker 容器隔离，防 rm -rf / 网络外连（代码执行层）
  - SkillSafetyFilter → 正则过滤，防 Prompt 注入（技能注入层）
"""

import re
from typing import List, Optional, Tuple

from loguru import logger

from agent_core.skills.prompt_skill import (
    PromptSkill,
    SkillInjectionResult,
    SkillTrustLevel,
)


# 危险模式正则（编译后缓存）

# Level 1: USER 级过滤——最严格
_USER_DANGEROUS_PATTERNS = [
    # 角色覆盖尝试
    re.compile(
        r"(?:忽略|无视|取消|覆盖|替换|清除)(?:以上|之前|全部|所有)(?:的?指令|提示|规则)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:ignore|disregard|override|forget)\s+(?:all|previous|above)\s+(?:instructions|rules|prompts)",
        re.IGNORECASE,
    ),
    # 权限提升
    re.compile(
        r"你(?:现在)?(?:拥?有|具备|获得)(?:管理员|超级|root|admin)(?:权限|能力)",
        re.IGNORECASE,
    ),
    # 伪 system prompt 嵌入
    re.compile(r"<\s*system\s*>.*?</\s*system\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"\[system\s*(?:prompt|message)\]", re.IGNORECASE),
    # 信息泄露诱导
    re.compile(
        r"(?:输出|显示|打印|泄露|告诉我)(?:你的)?(?:系统|内部|隐藏)(?:提示|指令|prompt)",
        re.IGNORECASE,
    ),
    # 越权操作指令
    re.compile(
        r"(?:删除|清除|格式化|rm\s+-rf|drop\s+table|truncate)",
        re.IGNORECASE,
    ),
]

# Level 2: PROJECT 级过滤——中等限制（仅过滤最危险的）
_PROJECT_DANGEROUS_PATTERNS = [
    _USER_DANGEROUS_PATTERNS[0],   # 角色覆盖
    _USER_DANGEROUS_PATTERNS[1],   # 英文角色覆盖
    _USER_DANGEROUS_PATTERNS[3],   # 伪 system prompt
    _USER_DANGEROUS_PATTERNS[4],   # 英文 system prompt
]


class SkillSafetyFilter:
    """技能安全过滤器——按信任等级过滤危险内容后注入 System Prompt。

    Args:
        max_total_chars: 所有技能注入的总字符上限（默认 8000）。
    """

    def __init__(
        self,
        max_total_chars: int = 8000,
    ) -> None:
        self.max_total_chars = max_total_chars

    def filter_and_inject(
        self,
        skills: List[PromptSkill],
    ) -> Tuple[str, SkillInjectionResult]:
        """过滤技能列表并生成可注入的安全文本。

        按照 injection_priority 排序后依次处理，
        在总字符预算内尽可能多注入。

        Args:
            skills: 要注入的技能列表。

        Returns:
            (注入文本, 注入结果摘要)。
        """
        result = SkillInjectionResult()
        blocks: List[str] = []
        remaining_budget = self.max_total_chars

        # 按优先级排序
        sorted_skills = sorted(
            skills, key=lambda s: s.injection_priority
        )

        for skill in sorted_skills:
            # 安全过滤
            safe_content, violations = self._filter_content(skill)
            if violations:
                result.blocked.append(skill.name)
                logger.warning(
                    f"[SafetyFilter] 拦截技能 {skill.name}: "
                    f"{len(violations)} 个危险模式 → {violations}"
                )
                continue

            # 预算检查
            block = skill.to_injection_block(max_chars=min(
                skill.max_tokens, remaining_budget
            ))
            if len(block) > remaining_budget:
                result.truncated.append(skill.name)
                # 截断注入
                block = skill.to_injection_block(
                    max_chars=remaining_budget - 50
                )

            blocks.append(block)
            remaining_budget -= len(block)
            result.skills_injected.append(skill.name)
            result.total_chars += len(block)

            if remaining_budget <= 0:
                break

        injection_text = "\n\n".join(blocks)

        if result.skills_injected:
            logger.info(
                f"[SafetyFilter] 注入 {len(result.skills_injected)} 个技能 "
                f"({result.total_chars} 字符)"
            )

        return injection_text, result

    def _filter_content(
        self,
        skill: PromptSkill,
    ) -> Tuple[str, List[str]]:
        """按信任等级过滤技能内容。

        Args:
            skill: 要过滤的技能。

        Returns:
            (过滤后的内容, 违规模式描述列表)。
        """
        # BUILTIN 完全信任，跳过过滤
        if skill.trust_level == SkillTrustLevel.BUILTIN:
            return skill.content, []

        # 选择对应等级的过滤规则
        patterns = (
            _USER_DANGEROUS_PATTERNS
            if skill.trust_level == SkillTrustLevel.USER
            else _PROJECT_DANGEROUS_PATTERNS
        )

        violations: List[str] = []
        filtered = skill.content

        for pattern in patterns:
            matches = pattern.findall(filtered)
            if matches:
                violations.extend(
                    f"匹配到: '{m[:30]}...'" if len(m) > 30 else f"匹配到: '{m}'"
                    for m in matches
                )
                # 替换为安全标记
                filtered = pattern.sub("[已过滤]", filtered)

        # 如果有违规，更新技能内容（但不修改原始对象）
        if violations:
            skill_copy = skill.model_copy()
            skill_copy.content = filtered
            return filtered, violations

        return skill.content, []
