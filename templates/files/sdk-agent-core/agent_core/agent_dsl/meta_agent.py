"""
Meta-Agent 模板自动生成 — meta_agent.py
=========================================

借鉴 agency-agents 的 150+ Agent 模式库理念，
提供基于简单描述自动生成完整 Agent 模板的能力。

用户只需输入一句话描述（如 "帮我建一个擅长写恐怖小说的 Agent"），
Meta-Agent 即可生成符合 8 段式标准的 Markdown 模板。

8 段式结构 (agency-agents 标准)::

    1. 🧠 Identity & Memory     → 身份认同与记忆
    2. 🎯 Core Mission           → 核心任务
    3. 🚨 Critical Rules         → 关键红线
    4. 🛠️ Technical Deliverables → 技术交付物
    5. 🔄 Workflow Process       → 工作流
    6. 💬 Communication Style    → 沟通风格
    7. 📊 Success Metrics        → 成功指标
    8. 🎭 Persona & Vibe         → 人格氛围 (可选)
"""

import re
from typing import Any, Callable, Dict, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# 模板生成 Prompt
# ---------------------------------------------------------------------------

META_AGENT_PROMPT = """你是一个 Agent 模板设计专家。根据用户的简单描述，生成一个完整的 、高质量的 Agent 模板。

## 你的任务

根据以下描述，生成一个符合 8 段式标准结构的 Agent Markdown 模板。

## 用户描述
{description}

## 附加参数
- 目标领域: {domain}
- 语言偏好: {language}

## 输出格式要求

必须输出完整的 Markdown 文件，包含三个部分：

### 1. YAML Frontmatter
```yaml
---
name: Agent名称（中文）
role_key: agent_role_key（英文snake_case）
emoji: 合适的emoji
description: 一句话描述
vibe: 氛围描述（2-3个形容词）
tools: []
---
```

### 2. Markdown Body（8段式，每段一个 H1 标题）

# 🧠 Identity & Memory
描述角色身份、背景经验、记忆法则。给 LLM 注入强烈角色认同。

# 🎯 Core Mission
将工作职责划分为几个具体子领域（3-5项）。

# 🚨 Critical Rules
不可违反的严格规则（3-5条，用列表格式）。

# 🛠️ Technical Deliverables
明确说明产出格式和结构要求。

# 🔄 Workflow Process
完成一项任务的标准步骤（Step 1 → Step 2 → ...）。

# 💬 Communication Style
语言基调和沟通方式（2-3句话）。

# 📊 Success Metrics
成果评估标准（3-5项可衡量指标）。

## 注意事项
- 内容要有深度和专业感，不是泛泛而谈
- 规则要具体可执行，不是空话
- 工作流步骤要完整且有逻辑顺序
- 交付物模板要尽可能具体
"""


# ---------------------------------------------------------------------------
# 模板生成器
# ---------------------------------------------------------------------------

async def generate_agent_template(
    description: str,
    domain: str = "通用",
    language: str = "中文",
    llm_generator: Optional[Callable] = None,
) -> str:
    """根据自然语言描述自动生成 Agent Markdown 模板。

    使用 Meta-Agent Prompt 调用 LLM 生成完整的 8 段式模板。
    如果 LLM 不可用，返回一个框架模板供手动填充。

    Args:
        description: 用户的自然语言描述。
        domain: 目标领域。
        language: 输出语言偏好。
        llm_generator: LLM 调用函数，签名 async def(prompt: str) -> str。

    Returns:
        完整的 Markdown 模板字符串。
    """
    if llm_generator:
        prompt = META_AGENT_PROMPT.format(
            description=description,
            domain=domain,
            language=language,
        )
        try:
            result = await llm_generator(prompt)
            # 清理可能的代码块包裹
            if result.startswith("```markdown"):
                result = result[len("```markdown"):].strip()
            if result.startswith("```"):
                result = result[3:].strip()
            if result.endswith("```"):
                result = result[:-3].strip()
            logger.info(
                f"[MetaAgent] Generated template from description: "
                f"'{description[:50]}...' ({len(result)} chars)"
            )
            return result
        except Exception as e:
            logger.error(f"[MetaAgent] LLM generation failed: {e}")

    # LLM 不可用时返回框架模板
    return _generate_scaffold(description, domain)


def _generate_scaffold(description: str, domain: str) -> str:
    """生成脚手架模板（LLM 不可用时的 fallback）。

    Args:
        description: Agent 描述。
        domain: 目标领域。

    Returns:
        待填充的 Markdown 框架。
    """
    # 从描述中提取关键角色名
    role_name = description[:20].replace(" ", "")
    role_key = re.sub(r'[^a-zA-Z0-9_]', '',
                      description[:30].lower().replace(" ", "_"))[:20]
    if not role_key:
        role_key = "custom_agent"

    return f"""---
name: {role_name}
role_key: {role_key}
emoji: 🤖
description: {description}
vibe: 专业、严谨、创新
domain: {domain}
tools: []
---

# 🧠 Identity & Memory
你是{description}。你拥有丰富的{domain}领域经验。

# 🎯 Core Mission

## 核心职责
1. TODO: 第一项职责
2. TODO: 第二项职责
3. TODO: 第三项职责

# 🚨 Critical Rules
- TODO: 规则一
- TODO: 规则二
- TODO: 规则三

# 🛠️ Technical Deliverables
以结构化格式输出，确保包含所有必要字段。

# 🔄 Workflow Process

## 标准流程
1. **Discovery**: 理解需求和上下文
2. **Analysis**: 分析并制定方案
3. **Execution**: 执行并输出结果
4. **Review**: 自检产出质量

# 💬 Communication Style
专业、简洁、有深度。用具体数据和示例支撑观点。

# 📊 Success Metrics
- 产出完整度 > 95%
- 格式规范通过率 100%
- 上下文相关性评分 > 80
"""


# ---------------------------------------------------------------------------
# 预制 Agent 模板定义（常用创作角色）
# ---------------------------------------------------------------------------

PRESET_AGENTS: Dict[str, Dict[str, Any]] = {
    "narratologist": {
        "description": "悬疑小说剧情铺排师",
        "domain": "叙事设计",
        "emoji": "📖",
    },
    "game_economy_designer": {
        "description": "游戏数值策划师",
        "domain": "游戏设计",
        "emoji": "📊",
    },
    "world_builder": {
        "description": "世界观架构师",
        "domain": "世界观设计",
        "emoji": "🌍",
    },
    "character_psychologist": {
        "description": "角色心理学家（角色行为动机分析）",
        "domain": "角色设计",
        "emoji": "🧠",
    },
    "dialogue_director": {
        "description": "对白导演（打磨角色对话的自然度和个性）",
        "domain": "台词设计",
        "emoji": "💬",
    },
}
