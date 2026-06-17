---
name: structured_output
description: 结构化输出指导——帮助 Agent 生成 JSON/YAML/表格等结构化数据
triggers:
  - 结构化输出
  - 生成 JSON
  - 输出表格
  - 数据格式化
tags:
  - 通用
  - 数据处理
priority: 20
max_tokens: 1500
---

# 结构化输出技能

## JSON 输出规范

- 输出前声明 schema
- 使用明确的字段命名（camelCase 或 snake_case，保持一致）
- 必须包含 `status` 和 `data` 顶层字段
- 错误时返回 `{"status": "error", "message": "..."}`

## 表格输出规范

- 使用 Markdown 表格格式
- 列名使用中文
- 数值右对齐
- 超过 20 行时使用分页提示
