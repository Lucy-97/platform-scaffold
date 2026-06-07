# pkg-platform-core 文档

通用组件库，被 `backend-gateway` 和 `backend-api` 共同依赖。

## 包索引

| 包 | 说明 | 文档 |
|----|------|------|
| `errcode` | 六位业务错误码注册表 | [errcode.md](errcode.md) |
| `lock` | Redis 分布式锁 (Lua owner-verified) | [lock.md](lock.md) |
| `cache` | Cache-Aside 泛型缓存 | [cache.md](cache.md) |
| `crypto` | AES-256-GCM 加解密 (Go/Python 互通) | [crypto.md](crypto.md) |
| `dynconfig` | system_config 表动态配置加载 | [dynconfig.md](dynconfig.md) |
| `response` | 统一响应格式 `{code, msg, data}` | [response.md](response.md) |
| `middleware` | JWT / InternalAuth / RequestID / CORS / RateLimit / Metrics | [middleware.md](middleware.md) |

## 设计原则

1. **业务无关**：所有包不依赖任何业务 model，通过接口/回调解耦
2. **优雅降级**：Redis/加密失败不阻止启动，仅日志警告
3. **跨语言对齐**：`crypto` 的 SHA-256 派生 + AES-GCM 与 Python 端完全兼容
4. **测试独立**：`go test ./...` 不需要外部服务（Redis/MySQL 依赖由集成测试覆盖）
