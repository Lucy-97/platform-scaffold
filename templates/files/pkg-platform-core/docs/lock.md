# lock — Redis 分布式锁

## 概述

基于 `SETNX` + Lua 原子释放的互斥锁。适用于订单处理、配置更新等需要防并发的场景。

## API

```go
// 获取锁。成功返回 *RedisLock，已被他人持有返回 (nil, nil)，Redis 错误返回 error。
l, err := lock.Acquire(ctx, rdb, "LOCK:ORDER_PAY:"+orderID, 30*time.Second)
if err != nil {
    return err // Redis 连接异常
}
if l == nil {
    return errcode.ErrTryLockFailed // 他人持有
}
// 动作完成后释放（Lua 脚本：仅删自己持有的 key，不会误删他人的锁）
defer l.Release(ctx)
```

## 设计要点

### 为什么用 Lua 释放？

```
// 非原子操作的危险场景：
1. 进程 A 持有锁，TTL 到期自动释放
2. 进程 B SETNX 成功，获得锁
3. 进程 A 执行 DEL —— 误删了 B 的锁！
4. 进程 C SETNX 成功 → B 和 C 同时持有"锁"
```

Lua 脚本 `if GET key == owner THEN DEL key` 保证"检查+删除"原子执行。

### key 命名规范

建议格式：`LOCK:<业务域>:<实体ID>`

| 场景 | key 示例 |
|------|---------|
| 订单支付 | `LOCK:ORDER_PAY:order-456` |
| 配置热更新 | `LOCK:CONFIG:system_config` |
| 用户操作 | `LOCK:USER_ACTION:uuid-123` |

### TTL 选择

- 太短（<5s）：业务未完成锁就过期，并发失控
- 太长（>120s）：进程崩溃后其他进程等待过久
- **建议 10~30s**，配合业务侧幂等保证

## 注意事项

- **不可重入**：同一进程对同一 key 二次 `Acquire` 会失败。如需重入，用 `LOCK:<key>:<goroutine-id>` 区分
- **不是红锁**：单 Redis 实例，不保证多节点强一致。多节点场景请用 Redlock 或 etcd
- `Release` 不返回 error：锁已过期被自动清理属于正常情况
