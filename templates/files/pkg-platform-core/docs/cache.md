# cache — Cache-Aside 泛型缓存

## 概述

基于 Redis 的 Cache-Aside 封装，核心是泛型 `GetOrLoad`：先查缓存，miss 则调 `loadFn` 回源，成功后**异步回填**不阻塞主流程。

## API

```go
cs := cache.NewService(rdb)

// 泛型 GetOrLoad：自动 JSON 序列化/反序列化
user, err := cache.GetOrLoad[User](cs, ctx,
    "cache:user:"+uuid,       // key
    5 * time.Minute,          // TTL
    func() (User, error) {    // 回源函数
        return userRepo.FindByUUID(uuid)
    },
)

// 直接读写
cs.Set(ctx, "cache:config:xxx", data, 10*time.Minute)
raw, err := cs.Get(ctx, "cache:config:xxx")

// 单 key 失效
cs.Invalidate(ctx, "cache:user:"+uuid)

// 批量通配符失效（用 SCAN 而非 KEYS，不阻塞 Redis）
cs.InvalidatePattern(ctx, "cache:user:*")
```

## GetOrLoad 流程

```
GetOrLoad[T](cs, ctx, key, ttl, loadFn)
    │
    ├─ Redis.GET(key) ──命中──→ JSON 反序列化 → 返回
    │
    └─ miss ──→ loadFn() ──→ 返回结果
                    │
                    └─ goroutine: JSON.Marshal → Redis.SET(key, data, ttl)
                       （失败仅日志，不影响主流程）
```

## key 命名规范

建议格式：`cache:<实体>:<唯一标识>`

| 场景 | key 示例 | TTL |
|------|---------|-----|
| 用户信息 | `cache:user:uuid-123` | 5min |
| 动态配置 | `cache:config:GOOGLE_CLIENT_SECRET` | 5min |
| 列表缓存 | `cache:scripts:page:1` | 2min |

## 注意事项

- **异步回填**：`GetOrLoad` 的 `loadFn` 返回后立即给调用方，回填在后台 goroutine。这意味着极端情况下同一时刻可能有多个请求同时 miss 并回源（thundering herd）。如果你的场景需要防穿透，在 `loadFn` 里加 `lock.Acquire`
- **JSON 序列化**：泛型 `T` 的字段必须是 JSON 可序列化的。`time.Time` 会序列化为 RFC3339 字符串
- **缓存穿透**：`loadFn` 返回零值时不会缓存（空字符串 `""` 视为有效值会被缓存）
- **InvalidatePattern** 用 `SCAN` 而非 `KEYS`，对生产环境安全
