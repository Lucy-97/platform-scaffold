# platform-scaffold

把已经验证过的「Go 网关 + Go API + Python AI 引擎 + Next.js 前端 + 部署」架构沉淀为 CLI，让新项目从「复制粘贴改名字」升级为「一条命令生成定制化骨架」。

## 为什么

不希望每个新项目都重走一遍：选 Web 框架 → 拼鉴权中间件 → 设计错误码 → 写 `system_config` 加密读取 → 调通 Gateway 转发 → 写部署脚本……

这些选择已经在两个真实项目（剧场互动平台 + AIGC 内容生成平台）跑了上千小时。`platform` 把这套现成的最佳实践打包成模板。

## 用了什么

- **CLI**：[`spf13/cobra`](https://github.com/spf13/cobra) + [`charmbracelet/huh`](https://github.com/charmbracelet/huh) 交互式 TUI
- **模板渲染**：Go 标准库 `text/template` + `embed.FS`（所有模板内嵌进二进制）
- **生成的项目**：
  - Go 1.22+ / Gin / GORM / Prometheus / golang-jwt
  - Python 3.11+ / FastAPI / httpx
  - Next.js 15 / React 19 / Vite
  - Docker Compose / K3s

## 怎么用

```bash
# 安装
go install github.com/platform-scaffold/cli/cmd/platform@latest

# 初始化新项目
platform init my-app

# 交互式问完会得到完整目录结构：
# my-app/
# ├── backend-gateway/      Go Gin 网关（JWT/限流/CORS/proxy）
# ├── backend-api/          Go API（handler→service→repository）
# ├── backend-ai-engine/    Python FastAPI（AI 编排，只读）
# ├── frontend-web/         Next.js 15 App Router
# ├── frontend-admin/       Vite + React 19 后台
# ├── pkg-platform-core/    通用组件库（errcode/lock/cache/crypto/middleware）
# ├── deploy/local/         docker-compose + start.sh
# ├── deploy/k3s/           K3s manifests + 部署脚本
# ├── database/init.sql
# ├── README.md / CLAUDE.md / .gitignore
# └── .env.example

cd my-app
cp deploy/local/.env.example deploy/local/.env
docker compose -f deploy/local/docker-compose-all.yaml up -d
./deploy/local/start.sh start
```

## 项目内的核心约定（生成的项目会沿用）

- **写权限矩阵**：所有 DB 写操作只在 Go API 进程，Python AI Engine 只读
- **服务间鉴权**：Gateway → 下游用 `X-Internal-Secret`，constant-time 校验
- **JWT 解析在网关**：业务 API 收到 `X-User-UUID` 头即可识别用户
- **三层配置**：编译期常量 → 启动期 env → 运行期 `system_config`（敏感值 AES-256-GCM 加密）
- **统一错误码**：6 位业务码注册表，HTTP 状态仅用于基础设施层
- **Cache-Aside + Redis 分布式锁**（Lua owner-verified 释放）

## 模板目录

```
platform-scaffold/
├── cmd/platform/main.go            CLI 入口
├── internal/
│   ├── config/project.go           ProjectConfig 模板变量
│   ├── prompt/                     huh 交互式输入
│   └── generator/generator.go      embed.FS → 渲染 → 落盘
├── templates/
│   ├── embed.go                    //go:embed all:files
│   └── files/                      所有模板源
│       ├── backend-gateway/
│       ├── backend-api/
│       ├── backend-ai-engine/
│       ├── frontend-web/
│       ├── frontend-admin/
│       ├── pkg-platform-core/
│       ├── deploy/{local,k3s}/
│       ├── database/init.sql.tmpl
│       ├── README.md.tmpl
│       ├── CLAUDE.md.tmpl
│       └── .gitignore
└── README.md (本文件)
```

## 贡献指南

模板修改原则：

1. **保持业务无关**。任何具体业务（drama / aigc）字眼不应出现在模板里。
2. **新增模板变量必须更新** [`internal/config/project.go`](internal/config/project.go) 的 `ProjectConfig`。
3. **`.go` 文件必须存为 `.go.tmpl`**，避免 Go 工具链把它当本仓库的源码。同理 `.py` / `.tsx` / `.ts` 在涉及模板变量时统一加 `.tmpl` 后缀。
4. **跨模块依赖通过接口**。`pkg-platform-core` 不允许 `import` 业务包；middleware/JWT 用接口（`JWTValidator`）解耦。
5. **JSX 双大括号陷阱**：`style={{...}}` 与 Go template `{{}}` 冲突，改用 `const styleX = {...}` 变量绕开。
6. **改完跑** `go build ./cmd/platform`，再 `./platform init demo-test` 试跑一遍。

## License

MIT