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
# ├── backend-gateway/      Go Gin 网关（JWT/限流/CORS/proxy + internal/{middleware,response}）
# ├── backend-api/          Go API（handler→engine→service→repository
# │                          + internal/{errcode,crypto,dynconfig,cache,lock,middleware,response,testutil}）
# ├── backend-ai-engine/    Python FastAPI（AI 编排，只读）   [可选]
# ├── frontend-web/         Next.js 15 App Router（src/lib 鉴权套件）   [可选]
# ├── frontend-admin/       Vite + React 19 后台              [可选]
# ├── bucketproxy/          Cloudflare R2 反向代理 Worker      [可选，默认关闭]
# ├── deploy/local/         docker-compose + start.sh
# ├── deploy/k3s/           K3s manifests + 部署脚本
# ├── docs/ skills/ .github/workflows/
# ├── database/init.sql     （含 system_config）
# └── README.md / CLAUDE.md / .gitignore

cd my-app
cp deploy/local/.env.example deploy/local/.env
docker compose -f deploy/local/docker-compose-all.yaml up -d
./deploy/local/start.sh start
```

> 通用组件不再是独立的共享 go module（曾经的 `pkg-platform-core` 已下线）。
> 每个 Go 服务自带所需的 `internal/` 组件，彼此独立可编译、无 `replace` 指向。

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
├── internal/generator/generator_test.go  黄金路径自检（生成 → go build）
├── scripts/smoke.sh                  本地一键自检
├── templates/
│   ├── embed.go                    //go:embed all:files
│   └── files/                      所有模板源
│       ├── backend-gateway/
│       ├── backend-api/
│       ├── backend-ai-engine/
│       ├── frontend-web/
│       ├── frontend-admin/
│       ├── bucketproxy/
│       ├── deploy/{local,k3s}/
│       ├── docs/ skills/ .github/
│       ├── database/init.sql.tmpl
│       ├── README.md.tmpl
│       ├── CLAUDE.md.tmpl
│       └── .gitignore
└── README.md (本文件)
```

## 贡献指南

模板修改原则：

1. **保持业务无关**。任何具体业务（drama / aigc 等）字眼不应出现在模板里。
2. **新增模板变量必须更新** [`internal/config/project.go`](internal/config/project.go) 的 `ProjectConfig`。
3. **`.go` 文件必须存为 `.go.tmpl`**，避免 Go 工具链把它当本仓库的源码。同理 `.py` / `.tsx` / `.ts` 在涉及模板变量时统一加 `.tmpl` 后缀（无模板变量的可保持原后缀，按原样复制）。
4. **通用组件按服务内聚**。`internal/{errcode,crypto,...}` 不允许 `import` 业务包；gateway/api 跨服务解耦用接口（如 `middleware.JWTValidator`）。新增可选顶层目录时，记得在 `Features` + `generator.skip()` + prompt 里接好开关。
5. **模板渲染陷阱**：`${{ }}`（GitHub Actions）与 `style={{...}}`（JSX）都会和 Go template `{{}}` 冲突——CI 用 `working-directory` 绕开 `${{ }}`，JSX 用 `const styleX = {...}` 变量绕开。
6. **本地/IDE 产物**（`.DS_Store`/`.idea`/`.wrangler`/`*.iml`）会被 `generator` 的 `isJunk` denylist 拦掉，但仍不要提交进 `templates/`。
7. **改完必须自检**：`go test ./...`（含生成 → `go build` 黄金路径），或 `bash scripts/smoke.sh` 跑全套（含前端 tsc）。

## License

MIT