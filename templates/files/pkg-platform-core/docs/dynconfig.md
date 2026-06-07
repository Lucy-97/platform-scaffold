# dynconfig — 动态配置加载

## 概述

在应用启动时从 `system_config` 表加载配置项，加密值自动解密，通过 `Setter` 回调写入业务 Config。**仅启动时加载一次，不支持热更新**（热更新走 Redis 缓存 + 定时刷新是下一阶段）。

## API

```go
// 最简用法：默认表名 system_config、默认列名
setters := map[string]dynconfig.Setter{
    "GOOGLE_CLIENT_SECRET": func(v string) { cfg.GoogleClientSecret = v },
    "PADDLE_API_KEY":       func(v string) { cfg.PaddleAPIKey = v },
    "SMTP_ENABLED":         func(v string) { cfg.SMTPEnabled = v == "true" },
}
dynconfig.Load(db, masterKey, setters)
// Load 完成后 cfg.GoogleClientSecret 等字段已被填充
```

## 自定义表名/列名

如果你的 system_config 表结构不同，用 `LoadWithOptions`：

```go
dynconfig.LoadWithOptions(db, masterKey, setters, dynconfig.Options{
    TableName:       "app_settings",      // 默认 "system_config"
    KeyColumn:       "setting_key",       // 默认 "config_key"
    ValueColumn:     "setting_value",     // 默认 "config_val"
    EncryptedColumn: "is_encrypted",      // 默认 "encrypted"
    LogPrefix:       "[MyConfig]",        // 默认 "[DynConfig]"
})
```

## 优雅降级

| 场景 | 行为 |
|------|------|
| masterKey 为空 | 跳过所有 `encrypted=1` 的项，其他明文项正常加载 |
| 数据库查询失败 | 日志警告，跳过该 key，不阻止启动 |
| 解密失败 | 日志警告，跳过该 key |
| key 不存在 | 跳过（setter 不被调用，业务 config 保持零值） |

这个设计确保新部署时即使 Admin 后台还没录入凭据，服务也能启动。

## 在 bootstrap 中使用

```go
// internal/app/bootstrap.go
func Bootstrap() {
    // ... 初始化 DB、Redis ...
    dynconfig.Load(deps.DB, cfg.ConfigMasterKey, map[string]dynconfig.Setter{
        "GOOGLE_CLIENT_SECRET": func(v string) { cfg.GoogleClientSecret = v },
        "PADDLE_API_KEY":       func(v string) { cfg.PaddleAPIKey = v },
    })
    // ... 继续初始化依赖 ...
}
```

## 与 Python 端对齐

Python AI Engine 通过 `app/services/dynamic_config.py` 读取同样的 `system_config` 表，使用相同的 `CONFIG_MASTER_KEY` 解密。两端看到的值一致。

## 注意事项

- **仅启动时加载**：启动后通过 Admin 后台修改的配置不会自动生效，需重启服务
- `Setter` 中不要做耗时操作——`Load` 是同步阻塞的
- 如果需要在运行时热更新，请用 `cache.GetOrLoad` + Redis TTL 的方案
