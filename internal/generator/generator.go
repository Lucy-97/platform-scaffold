// Package generator 把 templates/ 下的 embed.FS 模板树渲染到目标目录。
//
// 设计要点:
//   - 所有模板都嵌入到二进制（embed），脚手架本身就是一个 self-contained 二进制
//   - 文件路径本身也走 text/template 渲染，支持 {{.ProjectName}} 之类的占位符
//   - .tmpl 后缀会被剥掉
//   - 通过 ProjectConfig.Features 决定哪些子树被跳过
package generator

import (
	"bytes"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"text/template"

	"github.com/platform-scaffold/cli/internal/config"
	"github.com/platform-scaffold/cli/templates"
)

// Generator 持有用户配置 + 模板 FS。
type Generator struct {
	cfg config.ProjectConfig
}

// New 创建生成器。
func New(cfg config.ProjectConfig) *Generator {
	return &Generator{cfg: cfg}
}

// Run 遍历模板树，按规则渲染并写入磁盘。返回写入的文件数。
func (g *Generator) Run() (int, error) {
	if err := os.MkdirAll(g.cfg.OutputDir, 0o755); err != nil {
		return 0, err
	}

	count := 0
	// embed 路径会带 "files/" 前缀，walk 时统一剥掉。
	root := "files"
	err := fs.WalkDir(templates.FS, root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if path == root {
			return nil
		}
		rel := strings.TrimPrefix(path, root+"/")

		if g.skip(rel) {
			if d.IsDir() {
				return fs.SkipDir
			}
			return nil
		}

		if d.IsDir() {
			return nil
		}

		// 防御：永不把本地/IDE/OS 产物写进生成项目（即便误入模板树）。
		if isJunk(rel) {
			return nil
		}

		// 渲染目标路径（路径本身可包含模板变量）
		outRel, err := g.renderPath(rel)
		if err != nil {
			return fmt.Errorf("渲染路径 %s 失败: %w", rel, err)
		}
		// 剥掉 .tmpl 后缀
		outRel = strings.TrimSuffix(outRel, ".tmpl")

		// 读取模板内容
		raw, err := fs.ReadFile(templates.FS, path)
		if err != nil {
			return err
		}

		var content []byte
		if strings.HasSuffix(rel, ".tmpl") {
			rendered, rerr := g.renderBytes(rel, raw)
			if rerr != nil {
				return fmt.Errorf("渲染 %s 失败: %w", rel, rerr)
			}
			content = rendered
		} else {
			content = raw
		}

		dst := filepath.Join(g.cfg.OutputDir, outRel)
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			return err
		}
		mode := os.FileMode(0o644)
		if isExecutable(outRel) {
			mode = 0o755
		}
		if err := os.WriteFile(dst, content, mode); err != nil {
			return err
		}
		count++
		return nil
	})

	return count, err
}

// skip 决定是否跳过某个模板路径（基于 Features 开关）。
func (g *Generator) skip(path string) bool {
	if !g.cfg.Features.AIEngine && hasPrefix(path, "backend-ai-engine") {
		return true
	}
	if !g.cfg.Features.Web && hasPrefix(path, "frontend-web") {
		return true
	}
	if !g.cfg.Features.Admin && hasPrefix(path, "frontend-admin") {
		return true
	}
	if !g.cfg.Features.BucketProxy && hasPrefix(path, "bucketproxy") {
		return true
	}
	return false
}

// isJunk 判断某个相对路径是否为本地/IDE/OS 产物，不应进入生成项目。
func isJunk(path string) bool {
	path = filepath.ToSlash(path)
	base := filepath.Base(path)
	switch base {
	case ".DS_Store", "Thumbs.db", "*.iml":
		return true
	}
	if strings.HasSuffix(base, ".iml") {
		return true
	}
	for _, seg := range strings.Split(path, "/") {
		if seg == ".idea" || seg == ".wrangler" || seg == ".history" || seg == ".lh" {
			return true
		}
	}
	return false
}

func (g *Generator) renderPath(path string) (string, error) {
	if !strings.Contains(path, "{{") {
		return path, nil
	}
	return g.renderString("path:"+path, path)
}

func (g *Generator) renderBytes(name string, raw []byte) ([]byte, error) {
	out, err := g.renderString(name, string(raw))
	if err != nil {
		return nil, err
	}
	return []byte(out), nil
}

func (g *Generator) renderString(name, tpl string) (string, error) {
	t, err := template.New(name).Option("missingkey=error").Parse(tpl)
	if err != nil {
		return "", err
	}
	var buf bytes.Buffer
	if err := t.Execute(&buf, g.cfg); err != nil {
		return "", err
	}
	return buf.String(), nil
}

func hasPrefix(p, prefix string) bool {
	p = filepath.ToSlash(p)
	return p == prefix || strings.HasPrefix(p, prefix+"/")
}

// isExecutable 判断生成出来的文件是否需要 +x。模板里凡是 .sh 一律给执行权限。
func isExecutable(name string) bool {
	return strings.HasSuffix(name, ".sh")
}
