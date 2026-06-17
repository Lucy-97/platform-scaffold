"""
Agent Template Markdown DSL 解析器
====================================

将标准化的 Markdown Agent 模板文件解析为结构化数据。
支持 YAML Frontmatter 元数据提取、锚点段落切割、模板继承 (extends)。

文件格式约定:
  ---
  name: 剧情总监
  id: -1
  role_key: game_director
  emoji: 🎬
  extends: base_engineer.md
  tools: ["web_search"]
  ---
  # 🧠 Identity & Memory
  ...
  # 🎯 Core Mission
  ...

使用方式:
  parsed = parse_agent_markdown(content)
  agents = parse_agent_directory(Path("aigc-backend/agent_templates/agents"))
"""

import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from loguru import logger
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 解析结果 Pydantic 模型
# ---------------------------------------------------------------------------

class AgentTemplateParsed(BaseModel):
    """解析后的 Agent 模板结构化数据。

    Frontmatter 中的元数据字段 + Markdown body 全文。
    Markdown body 被完整保留，作为 system_instruction 写入数据库；
    同时按锚点切割为 sections dict，供需要按段落使用的场景调用。
    """
    # --- 来自 Frontmatter ---
    name: str = Field(..., description="Agent 显示名称")
    description: Optional[str] = Field(None, description="Agent 一句话描述 (tagline)")
    emoji: Optional[str] = Field(None, description="Agent 图标 emoji")
    color: Optional[str] = Field(None, description="UI 主题色")
    vibe: Optional[str] = Field(None, description="氛围简述 (agency-agents 风格)")
    extends: Optional[str] = Field(None, description="继承的父模板文件名")
    tools: List[str] = Field(default_factory=list, description="绑定的工具 ID 列表")
    # 系统 Agent 专用字段
    agent_id: Optional[int] = Field(None, description="固定 Agent ID (系统Agent用负数)")
    role_key: Optional[str] = Field(None, description="角色绑定键 (如 game_director)")
    pipeline_step: Optional[str] = Field(None, description="绑定的 Pipeline Step ID")
    # 额外 Frontmatter 字段 (透传)
    extra_meta: Dict = Field(default_factory=dict, description="Frontmatter 中未映射的额外字段")

    # --- 来自 Markdown Body ---
    system_instruction: str = Field("", description="完整的 Markdown body (作为 system_instruction)")
    sections: Dict[str, str] = Field(
        default_factory=dict,
        description="按 H1/H2 锚点切割的段落字典 (key=标题, value=内容)",
    )
    source_file: Optional[str] = Field(None, description="来源文件名 (用于日志和调试)")


# ---------------------------------------------------------------------------
# Frontmatter 已知字段映射
# ---------------------------------------------------------------------------

_KNOWN_FRONTMATTER_KEYS = {
    "name", "description", "emoji", "color", "vibe",
    "extends", "tools", "id", "role_key", "pipeline_step",
}

# 匹配 YAML Frontmatter 的正则: 以 --- 开头和结尾
_FRONTMATTER_PATTERN = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)

# 匹配 Markdown H1/H2 标题行的正则
_SECTION_HEADER_PATTERN = re.compile(
    r"^(#{1,2})\s+(.+)$",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# 核心解析函数
# ---------------------------------------------------------------------------

def parse_agent_markdown(
    content: str,
    source_file: Optional[str] = None,
) -> AgentTemplateParsed:
    """解析单个 Agent Markdown 模板字符串。

    从 Markdown 内容中提取 YAML Frontmatter 元数据和 Body 段落。
    Body 全文保留为 system_instruction，同时按标题切割为 sections。

    Args:
        content: Markdown 文件的完整文本内容。
        source_file: 来源文件名 (用于日志)。

    Returns:
        解析后的 AgentTemplateParsed 实例。

    Raises:
        ValueError: 如果文件缺少必要的 Frontmatter 或 name 字段。
    """
    # 1. 提取 Frontmatter
    fm_match = _FRONTMATTER_PATTERN.match(content)
    if not fm_match:
        raise ValueError(
            f"Agent 模板文件缺少 YAML Frontmatter (---...---): {source_file or '(inline)'}"
        )

    raw_yaml = fm_match.group(1)
    body = content[fm_match.end():].strip()

    # 2. 解析 YAML
    try:
        meta = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as e:
        raise ValueError(
            f"Agent 模板 YAML Frontmatter 解析失败: {source_file or '(inline)'}: {e}"
        )

    if not isinstance(meta, dict):
        raise ValueError(
            f"Agent 模板 Frontmatter 必须是字典格式: {source_file or '(inline)'}"
        )

    if "name" not in meta:
        raise ValueError(
            f"Agent 模板 Frontmatter 缺少必要的 'name' 字段: {source_file or '(inline)'}"
        )

    # 3. 映射已知字段，其余放入 extra_meta
    extra_meta = {}
    for key, value in meta.items():
        if key not in _KNOWN_FRONTMATTER_KEYS:
            extra_meta[key] = value

    # 4. 按 H1/H2 标题切割 Body 为 sections
    sections = _split_sections(body)

    # 5. 组装结果
    return AgentTemplateParsed(
        name=meta["name"],
        description=meta.get("description"),
        emoji=meta.get("emoji"),
        color=meta.get("color"),
        vibe=meta.get("vibe"),
        extends=meta.get("extends"),
        tools=meta.get("tools", []),
        agent_id=meta.get("id"),
        role_key=meta.get("role_key"),
        pipeline_step=meta.get("pipeline_step"),
        extra_meta=extra_meta,
        system_instruction=body,
        sections=sections,
        source_file=source_file,
    )


def parse_agent_directory(
    dir_path: Path,
    recursive: bool = False,
) -> List[AgentTemplateParsed]:
    """扫描目录下的所有 .md 文件并批量解析。

    忽略解析失败的文件 (记录 warning 但不中断)。

    Args:
        dir_path: 包含 Agent 模板 .md 文件的目录路径。
        recursive: 是否递归子目录。

    Returns:
        成功解析的 AgentTemplateParsed 列表。
    """
    if not dir_path.is_dir():
        logger.warning(f"[agent_template_parser] 目录不存在: {dir_path}")
        return []

    glob_pattern = "**/*.md" if recursive else "*.md"
    md_files = sorted(dir_path.glob(glob_pattern))

    if not md_files:
        logger.info(f"[agent_template_parser] 目录下无 .md 文件: {dir_path}")
        return []

    results: List[AgentTemplateParsed] = []
    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8")
            parsed = parse_agent_markdown(content, source_file=md_file.name)
            results.append(parsed)
            logger.debug(
                f"[agent_template_parser] 解析成功: {md_file.name} -> {parsed.name}"
            )
        except (ValueError, Exception) as e:
            # 单个文件解析失败不影响其他文件
            logger.warning(
                f"[agent_template_parser] 跳过无效模板 {md_file.name}: {e}"
            )

    logger.info(
        f"[agent_template_parser] 从 {dir_path} 解析了 {len(results)}/{len(md_files)} 个 Agent 模板"
    )
    return results


def resolve_inheritance(
    templates: List[AgentTemplateParsed],
) -> List[AgentTemplateParsed]:
    """处理模板继承 (extends 字段)。

    将父模板的 sections 和 tools 合并到子模板中。
    子模板中已存在的 section 会覆盖父模板的同名 section。

    Args:
        templates: parse_agent_directory 返回的模板列表。

    Returns:
        继承处理后的模板列表 (原地修改并返回)。
    """
    # 建立 source_file -> template 索引
    by_file: Dict[str, AgentTemplateParsed] = {}
    for t in templates:
        if t.source_file:
            by_file[t.source_file] = t

    for t in templates:
        if not t.extends:
            continue

        parent = by_file.get(t.extends)
        if not parent:
            logger.warning(
                f"[agent_template_parser] 继承目标不存在: "
                f"{t.source_file} extends {t.extends}"
            )
            continue

        # 合并 sections: 父模板的段落作为默认值，子模板覆盖
        merged_sections = {**parent.sections, **t.sections}
        t.sections = merged_sections

        # 合并 tools: 并集
        merged_tools = list(set(parent.tools + t.tools))
        t.tools = merged_tools

        # 重组 system_instruction: 从合并后的 sections 重建
        t.system_instruction = _rebuild_body_from_sections(merged_sections)

        logger.debug(
            f"[agent_template_parser] 继承合并: {t.source_file} <- {t.extends} "
            f"(sections: {len(merged_sections)}, tools: {len(merged_tools)})"
        )

    return templates


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------

def _split_sections(body: str) -> Dict[str, str]:
    """按 H1/H2 标题将 Markdown body 切割为段落字典。

    Args:
        body: Markdown 正文 (不含 Frontmatter)。

    Returns:
        {标题文本: 段落内容} 字典。如果标题前有内容则用 "_preamble" 键。
    """
    sections: Dict[str, str] = {}
    matches = list(_SECTION_HEADER_PATTERN.finditer(body))

    if not matches:
        # 没有标题，整个 body 作为 _preamble
        if body.strip():
            sections["_preamble"] = body.strip()
        return sections

    # 标题前的内容
    preamble = body[:matches[0].start()].strip()
    if preamble:
        sections["_preamble"] = preamble

    # 按标题切分
    for i, match in enumerate(matches):
        title = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        sections[title] = content

    return sections


def _rebuild_body_from_sections(sections: Dict[str, str]) -> str:
    """从 sections 字典重建 Markdown body 文本。

    Args:
        sections: {标题: 内容} 字典。

    Returns:
        重组后的 Markdown 文本。
    """
    parts: List[str] = []

    # _preamble 放最前面
    if "_preamble" in sections:
        parts.append(sections["_preamble"])

    for title, content in sections.items():
        if title == "_preamble":
            continue
        parts.append(f"## {title}\n{content}")

    return "\n\n".join(parts)
