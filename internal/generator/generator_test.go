package generator

import (
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/platform-scaffold/cli/internal/config"
)

// TestGenerateProducesBuildableProject 是脚手架的黄金路径自检：
// 生成一个项目 → 断言关键文件存在 / 无垃圾文件泄漏 / 旧共享库已下线，
// 非 -short 模式下进一步对 Go 服务跑 go build（需联网拉依赖）。
func TestGenerateProducesBuildableProject(t *testing.T) {
	dir := t.TempDir()
	cfg := config.Defaults("demo-test")
	cfg.OutputDir = dir
	if err := config.Validate(cfg); err != nil {
		t.Fatalf("validate: %v", err)
	}

	n, err := New(cfg).Run()
	if err != nil {
		t.Fatalf("generate: %v", err)
	}
	if n == 0 {
		t.Fatal("no files written")
	}

	// 关键文件必须存在。
	for _, f := range []string{
		"backend-gateway/go.mod",
		"backend-gateway/internal/middleware/middleware.go",
		"backend-gateway/internal/response/response.go",
		"backend-api/go.mod",
		"backend-api/internal/middleware/middleware.go",
		"backend-api/internal/errcode/errcode.go",
		"backend-api/internal/crypto/aes_gcm.go",
		"backend-api/internal/dynconfig/loader.go",
		"backend-api/internal/cache/cache.go",
		"backend-api/internal/lock/redis_lock.go",
		"backend-api/internal/testutil/db.go",
		"frontend-web/src/lib/apiClient.ts",
		"frontend-admin/src/api.ts",
		"README.md",
	} {
		if _, err := os.Stat(filepath.Join(dir, f)); err != nil {
			t.Errorf("expected file missing: %s", f)
		}
	}

	// 旧共享库不应再被生成。
	if _, err := os.Stat(filepath.Join(dir, "pkg-platform-core")); err == nil {
		t.Error("pkg-platform-core should no longer be generated")
	}
	// bucketproxy 默认关闭，不应生成。
	if _, err := os.Stat(filepath.Join(dir, "bucketproxy")); err == nil {
		t.Error("bucketproxy should be off by default")
	}

	// 不得有任何本地/IDE/OS 垃圾文件泄漏。
	_ = filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		base := filepath.Base(p)
		if base == ".DS_Store" || base == ".idea" || base == ".wrangler" || strings.HasSuffix(base, ".iml") {
			t.Errorf("junk leaked into generated project: %s", p)
		}
		return nil
	})

	// 模板里不应残留未渲染的占位符。
	for _, f := range []string{"backend-api/go.mod", "README.md", "frontend-web/src/lib/apiUrl.ts"} {
		raw, rerr := os.ReadFile(filepath.Join(dir, f))
		if rerr == nil && strings.Contains(string(raw), "{{") {
			t.Errorf("unrendered template placeholder in %s", f)
		}
	}

	if testing.Short() {
		t.Skip("skip `go build` of generated services in -short mode")
	}
	if _, err := exec.LookPath("go"); err != nil {
		t.Skip("go toolchain not available")
	}
	for _, svc := range []string{"backend-gateway", "backend-api"} {
		svcDir := filepath.Join(dir, svc)
		run(t, svcDir, "go", "mod", "tidy")
		run(t, svcDir, "go", "build", "./...")
	}
}

func run(t *testing.T, dir, name string, args ...string) {
	t.Helper()
	cmd := exec.Command(name, args...)
	cmd.Dir = dir
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("%s %s (in %s): %v\n%s", name, strings.Join(args, " "), dir, err, out)
	}
}
