"""
知识图谱数据模型与实体抽取 — knowledge_graph.py
================================================

本文件承担两个核心职责：
  1. 定义图谱领域的数据模型（Entity / Relation）
  2. 提供 LLM 实体抽取 Prompt + 异步解析函数

设计原则：
  - 通用化：不预设特定领域的实体类型
  - 可扩展：通过字符串 entity_type 而非硬编码枚举
  - 所有持久化操作统一通过 GraphStore 抽象接口完成

命名变更说明：
  原 graph_rag.py → knowledge_graph.py
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from agent_core.memory.graph_store import GraphStore


# ---------------------------------------------------------------------------
# 数据模型（通用，不绑定领域）
# ---------------------------------------------------------------------------

@dataclass
class Entity:
    """知识图谱中的实体节点（纯数据模型）。

    Attributes:
        name: 实体名称（同一图谱内唯一标识）。
        entity_type: 实体类型字符串（不做枚举限制，由上层自定义）。
        description: 实体描述。
        attributes: 附加属性字典。
        source: 实体来源（从哪段文本中提取）。
    """
    name: str
    entity_type: str = "concept"
    description: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    source: str = ""


@dataclass
class Relation:
    """知识图谱中的关系边（纯数据模型）。

    Attributes:
        source: 源实体名称。
        target: 目标实体名称。
        relation_type: 关系类型。
        description: 关系描述。
        weight: 关系权重 (0.0-1.0)。
        bidirectional: 是否双向关系。
    """
    source: str
    target: str
    relation_type: str = ""
    description: str = ""
    weight: float = 1.0
    bidirectional: bool = False


# ---------------------------------------------------------------------------
# LLM 实体抽取 Prompt（通用，面向 LLM 使用英文）
# ---------------------------------------------------------------------------

ENTITY_EXTRACTION_PROMPT = """You are an entity-relation extraction expert. Extract entities and relations from the given text.

## Entity Types
Use domain-appropriate labels. Common examples:
- person, organization, location, item, event, concept, system, service

You are not limited to these — use whatever labels best describe the entities found.

## Output Format (strict JSON)

```json
{{
  "entities": [
    {{"name": "entity name", "entity_type": "type_label", "description": "one-line description", "attributes": {{"key": "value"}}}}
  ],
  "relations": [
    {{"source": "EntityA", "target": "EntityB", "relation_type": "relation label", "description": "relation description", "bidirectional": false}}
  ]
}}
```

## Rules
1. Only extract entities explicitly mentioned in the text
2. Use original names from the text
3. Use concise verb phrases for relation types
4. Every entity and relation needs a description
5. bidirectional=true for symmetric relations (e.g. allies, partners)

## Text

{text}"""


# ---------------------------------------------------------------------------
# 异步解析函数 — 解析 LLM 抽取结果并写入 GraphStore
# ---------------------------------------------------------------------------

async def parse_extraction_result(
    store: GraphStore,
    graph_id: str,
    result_json: str,
    source: str = "",
) -> Tuple[int, int]:
    """解析 LLM 的实体抽取结果并通过 GraphStore 持久化。

    通过 GraphStore 抽象接口写入，兼容 InMemory / Neo4j 等任何后端。

    Args:
        store: GraphStore 实例。
        graph_id: 图谱标识。
        result_json: LLM 返回的 JSON 字符串。
        source: 实体来源标识。

    Returns:
        (新增实体数, 新增关系数) 元组。
    """
    try:
        data = json.loads(result_json)
    except json.JSONDecodeError as e:
        logger.error(f"[KnowledgeGraph] JSON 解析失败: {e}")
        return (0, 0)

    entity_count = 0
    for e_data in data.get("entities", []):
        name = e_data.get("name", "")
        if not name:
            continue
        raw_type = e_data.get("entity_type", "concept")

        await store.upsert_entity(
            graph_id=graph_id,
            name=name,
            entity_type=raw_type,
            description=e_data.get("description", ""),
            attributes=e_data.get("attributes", {}),
            source=source,
        )
        entity_count += 1

    relation_count = 0
    for r_data in data.get("relations", []):
        src = r_data.get("source", "")
        tgt = r_data.get("target", "")
        if not src or not tgt:
            continue
        await store.upsert_relation(
            graph_id=graph_id,
            source=src,
            target=tgt,
            relation_type=r_data.get("relation_type", ""),
            description=r_data.get("description", ""),
            weight=r_data.get("weight", 1.0),
            bidirectional=r_data.get("bidirectional", False),
        )
        relation_count += 1

    logger.info(
        f"[KnowledgeGraph] 解析完成: "
        f"+{entity_count} entities, +{relation_count} relations "
        f"→ graph={graph_id}"
    )
    return (entity_count, relation_count)


# ---------------------------------------------------------------------------
# 图谱 Markdown 渲染 — 用于 Agent System Prompt 注入
# ---------------------------------------------------------------------------

def render_graph_markdown(graph_data: Dict[str, Any], name: str = "default") -> str:
    """将 GraphStore.get_all() 的返回值渲染为 Markdown 摘要。

    适合直接注入 Agent 的 System Prompt。

    Args:
        graph_data: GraphStore.get_all() 返回的字典。
        name: 图谱显示名称。

    Returns:
        格式化的 Markdown 文本。
    """
    entities = graph_data.get("entities", {})
    relations = graph_data.get("relations", [])

    lines = [f"## Knowledge Graph: {name}\n"]

    lines.append(f"### Entities ({len(entities)})\n")
    for ename, e in entities.items():
        attrs = e.get("attributes", {})
        attrs_str = ""
        if attrs:
            attrs_str = " | " + ", ".join(
                f"{k}={v}" for k, v in list(attrs.items())[:5]
            )
        desc = e.get("description", "")
        etype = e.get("entity_type", "concept")
        lines.append(f"- **{ename}** [{etype}]: {desc}{attrs_str}")

    lines.append(f"\n### Relations ({len(relations)})\n")
    for r in relations:
        arrow = "↔" if r.get("bidirectional") else "→"
        rtype = r.get("relation_type", "")
        rdesc = r.get("description", "")
        lines.append(f"- {r['source']} {arrow} {r['target']} [{rtype}]: {rdesc}")

    return "\n".join(lines)
