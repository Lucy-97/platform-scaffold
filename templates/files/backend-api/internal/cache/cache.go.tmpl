// Package cache 提供 Cache-Aside 模式的 Redis 缓存封装。
//
// 设计要点：
//   - 泛型 GetOrLoad：先查 Redis，miss 则调 loadFn，成功后异步回填，不阻塞主流程
//   - InvalidatePattern：按通配符删除缓存（如 cache:user:* ）
//   - 不预设 key 命名规范，调用方自己组装 key（建议 cache:<entity>:<id>）
package cache

import (
	"context"
	"encoding/json"
	"log/slog"
	"time"

	"github.com/redis/go-redis/v9"
)

// Service Cache-Aside 封装。
type Service struct {
	rdb *redis.Client
}

// NewService 创建缓存服务。
func NewService(rdb *redis.Client) *Service {
	return &Service{rdb: rdb}
}

// GetOrLoad 缓存穿透：先查 Redis，命中则返回；未命中则调 loadFn 加载并异步回填。
func GetOrLoad[T any](cs *Service, ctx context.Context, key string, ttl time.Duration, loadFn func() (T, error)) (T, error) {
	var zero T

	cached, err := cs.rdb.Get(ctx, key).Result()
	if err == nil && cached != "" {
		var result T
		if jerr := json.Unmarshal([]byte(cached), &result); jerr == nil {
			return result, nil
		}
		// 反序列化失败视为 miss
	}

	result, err := loadFn()
	if err != nil {
		return zero, err
	}

	go func() {
		data, mErr := json.Marshal(result)
		if mErr != nil {
			slog.Warn("cache marshal failed", "key", key, "error", mErr)
			return
		}
		if sErr := cs.rdb.Set(context.Background(), key, data, ttl).Err(); sErr != nil {
			slog.Warn("cache set failed", "key", key, "error", sErr)
		}
	}()

	return result, nil
}

// Set 直接写入 (覆盖式)。
func (cs *Service) Set(ctx context.Context, key string, data []byte, ttl time.Duration) error {
	return cs.rdb.Set(ctx, key, data, ttl).Err()
}

// Get 直接读 raw bytes。key 不存在时 error 为 redis.Nil。
func (cs *Service) Get(ctx context.Context, key string) ([]byte, error) {
	return cs.rdb.Get(ctx, key).Bytes()
}

// Invalidate 删除指定 key。
func (cs *Service) Invalidate(ctx context.Context, key string) error {
	return cs.rdb.Del(ctx, key).Err()
}

// InvalidatePattern 按通配符批量删除（用 SCAN 而非 KEYS，避免阻塞）。
func (cs *Service) InvalidatePattern(ctx context.Context, pattern string) error {
	var cursor uint64
	for {
		keys, nextCursor, err := cs.rdb.Scan(ctx, cursor, pattern, 100).Result()
		if err != nil {
			return err
		}
		if len(keys) > 0 {
			cs.rdb.Del(ctx, keys...)
		}
		cursor = nextCursor
		if cursor == 0 {
			break
		}
	}
	return nil
}
