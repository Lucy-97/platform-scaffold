# agent-core

通用 AI Agent 运行时框架 — 可跨项目复用的 Agent 基础设施。

## 安装

```bash
# 本地开发
pip install -e .

# 带 Neo4j GraphRAG 支持
pip install -e ".[neo4j]"

# 全部可选依赖
pip install -e ".[all]"
```

## 模块结构

```
agent_core/
├── llm/             LLM 客户端抽象 (LiteLLM 统一封装)
├── middleware/       可组合中间件链 (7 个中间件)
├── channels/         IM 渠道适配 (飞书等)
├── memory/           记忆管理 + GraphRAG + Neo4j
├── execution/        沙箱执行 + 子代理并行
├── evaluation/       评估闭环 (Dev-QA Loop) + 循环检测
├── skills/           Skills-as-Markdown 技能注册 + 行业技能包过滤
└── templates/        Agent 模板解析 + YAML 配置热加载
```

---

## 环境变量参考

> 所有环境变量均有合理默认值，未配置时使用降级模式运行。
> 在 Docker Compose 环境中，通过 `.env` 文件统一注入。

### 飞书渠道 (channels/feishu.py)

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `FEISHU_APP_ID` | 是* | `""` | 飞书开放平台的 App ID，[控制台](https://open.feishu.cn) 创建应用获取 |
| `FEISHU_APP_SECRET` | 是* | `""` | 飞书 App Secret，与 App ID 配对使用 |
| `FEISHU_VERIFICATION_TOKEN` | 否 | `""` | Webhook 签名验证 Token；未配置时跳过验证 (仅开发环境) |

> *仅在启用飞书渠道时必填。未配置时 `FeishuAdapter.send_message()` 会静默返回 `False`。

**使用位置**: `FeishuAdapter.__init__()` 构造函数  
**调用时机**: 实例化 FeishuAdapter 时从环境变量读取，也可通过构造参数显式传入覆盖

### Neo4j 图谱存储 (memory/neo4j_store.py)

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `NEO4J_URI` | 否 | `""` | Bolt 连接地址 (如 `bolt://localhost:7687`) |
| `NEO4J_USER` | 否 | `"neo4j"` | 认证用户名 |
| `NEO4J_PASSWORD` | 否 | `"agent_neo4j_dev"` | 认证密码 |

**降级策略**: `NEO4J_URI` 未设置时，`create_graph_store()` 工厂方法自动切换到 `InMemoryGraphStore`，无需 Neo4j 服务即可运行。

```python
# 自动选择后端
from agent_core.memory.neo4j_store import create_graph_store
store = create_graph_store()  # NEO4J_URI 存在 → Neo4j；否则 → 内存模式
```

### 沙箱执行 (execution/)

| 变量名 | 必填 | 默认值 | 说明 | 所在文件 |
|--------|------|--------|------|----------|
| `SANDBOX_ROOT` | 否 | `"/data/sandbox"` | Per-session 沙箱目录根路径 | `sandbox_session.py` |
| `SANDBOX_MAX_SIZE` | 否 | `"104857600"` (100MB) | 每个 session 最大磁盘占用 (bytes) | `sandbox_session.py` |
| `SANDBOX_DOCKER_IMAGE` | 否 | `"agent-sandbox:latest"` | Docker 沙箱镜像名 | `sandbox_executor.py` |
| `SANDBOX_MODE` | 否 | `"subprocess"` | 沙箱执行模式: `subprocess` (开发) / `docker` (生产) | `sandbox_executor.py` |

**两种模式**:
- `subprocess`: 使用 `asyncio.create_subprocess_exec` 在子进程中执行代码，适合开发调试
- `docker`: 使用 Docker 容器执行，提供网络隔离、内存限制、只读 FS 等生产级安全

### YAML 配置中的环境变量引用 (templates/config.py)

`agent_config.yaml` 支持 `$ENV_VAR` 或 `${ENV_VAR}` 语法引用环境变量：

```yaml
middleware:
  skill_injection:
    api_key: $LLM_API_KEY          # 引用 LLM_API_KEY 环境变量
    endpoint: ${CUSTOM_ENDPOINT}   # 花括号语法
```

**解析规则**: 加载 YAML 后递归扫描所有字符串值，匹配 `$ENV_VAR` 或 `${ENV_VAR}` 格式，替换为 `os.environ.get(VAR_NAME, "")`。

---

## `.env` 配置示例

```bash
# ---- 飞书渠道 ----
FEISHU_APP_ID=cli_xxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_VERIFICATION_TOKEN=xxxxxxxx

# ---- Neo4j GraphRAG ----
# NEO4J_URI=bolt://neo4j:7687    # 取消注释启用 Neo4j
# NEO4J_USER=neo4j
# NEO4J_PASSWORD=your_password

# ---- 沙箱执行 ----
SANDBOX_MODE=subprocess            # 开发用 subprocess，生产改 docker
SANDBOX_ROOT=/data/sandbox
# SANDBOX_DOCKER_IMAGE=agent-sandbox:latest
# SANDBOX_MAX_SIZE=104857600       # 100MB
```

---

## 快速使用

```python
# 中间件链
from agent_core.middleware.chain import MiddlewareChain
from agent_core.middleware.summarization import SummarizationMiddleware

chain = MiddlewareChain()
chain.add(SummarizationMiddleware(max_tokens=8000))

# 评估闭环
from agent_core.evaluation.evaluator_agent import EvaluatorAgent, evaluate_with_retry

evaluator = EvaluatorAgent(name="qa", pass_threshold=70)
result = await evaluate_with_retry(generator=my_fn, evaluator=evaluator, context={})

# 记忆管理
from agent_core.memory.memory_service import build_memory_injection

# 技能注册 (含行业过滤)
from agent_core.skills.skill_registry import SkillRegistry

registry = SkillRegistry(skills_dir="/path/to/skills")
registry.scan()
matches = registry.match_skills("代码审查")
mfg_skills = registry.get_skills_by_industry("manufacturing")

# 图谱存储 (自动选择后端)
from agent_core.memory.neo4j_store import create_graph_store
store = create_graph_store()
```

## 在其他项目使用

```bash
# 方式 1: 本地路径 (开发模式)
pip install -e /path/to/aigc/agent-core

# 方式 2: Git 仓库
pip install "agent-core @ git+https://your-repo.git#subdirectory=agent-core"

# 方式 3: 私有制品仓库 (阿里云)
pip install agent-core -i https://packages.aliyun.com/your-repo/pypi/simple/
```

## 生产性能优化

```python
# main.py
import uvloop; uvloop.install()  # 异步 2-4x 加速
```

```bash
# 部署命令
gunicorn -k uvicorn.workers.UvicornWorker -w 4 main:app
```
