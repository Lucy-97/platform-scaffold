// Package lock 提供基于 Redis SETNX + Lua 原子释放的分布式互斥锁。
//
// 用法：
//
//	l, err := lock.Acquire(ctx, rdb, "LOCK:ORDER_PAY:"+orderID, 30*time.Second)
//	if err != nil || l == nil {
//	    return errcode.ErrTryLockFailed
//	}
//	defer l.Release(ctx)
package lock

import (
	"context"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// releaseScript 仅当持有者 value 匹配时才删除，防止误删他人持有的锁。
const releaseScript = `if redis.call("get",KEYS[1]) == ARGV[1] then return redis.call("del",KEYS[1]) else return 0 end`

// RedisLock 分布式锁实例。
type RedisLock struct {
	rdb   *redis.Client
	key   string
	value string
}

// Acquire 尝试获取分布式锁。
// 成功返回锁实例；已被他人持有时返回 (nil, nil)，调用方据此判断是否冲突。
// ttl 是自动过期时间，防止死锁。
func Acquire(ctx context.Context, rdb *redis.Client, key string, ttl time.Duration) (*RedisLock, error) {
	value := uuid.New().String()
	ok, err := rdb.SetNX(ctx, key, value, ttl).Result()
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, nil
	}
	return &RedisLock{rdb: rdb, key: key, value: value}, nil
}

// Release 安全释放（Lua 原子操作，仅释放自己持有的锁）。
func (l *RedisLock) Release(ctx context.Context) {
	l.rdb.Eval(ctx, releaseScript, []string{l.key}, l.value)
}
