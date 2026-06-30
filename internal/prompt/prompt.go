// Package prompt 用 charmbracelet/huh 实现交互式输入，收集 ProjectConfig。
package prompt

import (
	"fmt"
	"strconv"

	"github.com/charmbracelet/huh"

	"github.com/platform-scaffold/cli/internal/config"
)

// AskProjectConfig 交互式收集项目配置。nonInteractive=true 时直接用默认值返回。
func AskProjectConfig(defaultName string, nonInteractive bool) (config.ProjectConfig, error) {
	cfg := config.Defaults(defaultName)
	if nonInteractive {
		if cfg.ProjectName == "my-app" && defaultName == "" {
			return cfg, fmt.Errorf("--yes 模式必须显式指定项目名: platform init <name> --yes")
		}
		return cfg, nil
	}

	gatewayPort := strconv.Itoa(cfg.Ports.Gateway)
	apiPort := strconv.Itoa(cfg.Ports.API)
	aiPort := strconv.Itoa(cfg.Ports.AIEngine)
	webPort := strconv.Itoa(cfg.Ports.Web)
	adminPort := strconv.Itoa(cfg.Ports.Admin)

	features := []string{}
	if cfg.Features.AIEngine {
		features = append(features, "ai-engine")
	}
	if cfg.Features.Web {
		features = append(features, "web")
	}
	if cfg.Features.Admin {
		features = append(features, "admin")
	}
	if cfg.Features.BucketProxy {
		features = append(features, "bucketproxy")
	}

	form := huh.NewForm(
		huh.NewGroup(
			huh.NewInput().
				Title("项目名 (kebab-case)").
				Description("用作目录名、docker service、k8s namespace").
				Value(&cfg.ProjectName).
				Validate(notEmpty("ProjectName")),
			huh.NewInput().
				Title("品牌名 (Brand)").
				Description("展示用名称，比如 MyApp").
				Value(&cfg.Brand).
				Validate(notEmpty("Brand")),
			huh.NewInput().
				Title("域名").
				Description("用于 CORS 白名单和 Cookie domain").
				Value(&cfg.Domain).
				Validate(notEmpty("Domain")),
			huh.NewInput().
				Title("Go module 路径").
				Description("例如 github.com/me/my-app").
				Value(&cfg.GoModulePath).
				Validate(notEmpty("GoModulePath")),
		),
		huh.NewGroup(
			huh.NewInput().Title("Gateway 端口").Value(&gatewayPort),
			huh.NewInput().Title("API 端口").Value(&apiPort),
			huh.NewInput().Title("AI Engine 端口").Value(&aiPort),
			huh.NewInput().Title("Web 端口").Value(&webPort),
			huh.NewInput().Title("Admin 端口").Value(&adminPort),
		),
		huh.NewGroup(
			huh.NewMultiSelect[string]().
				Title("启用的模块").
				Options(
					huh.NewOption("ai-engine (Python FastAPI)", "ai-engine").Selected(cfg.Features.AIEngine),
					huh.NewOption("web (Next.js)", "web").Selected(cfg.Features.Web),
					huh.NewOption("admin (Vite+React)", "admin").Selected(cfg.Features.Admin),
					huh.NewOption("bucketproxy (Cloudflare R2 Worker)", "bucketproxy").Selected(cfg.Features.BucketProxy),
				).
				Value(&features),
			huh.NewConfirm().
				Title("初始化 git 仓库?").
				Value(&cfg.InitGit),
		),
	)

	if err := form.Run(); err != nil {
		return cfg, err
	}

	cfg.Ports.Gateway = atoiOr(gatewayPort, cfg.Ports.Gateway)
	cfg.Ports.API = atoiOr(apiPort, cfg.Ports.API)
	cfg.Ports.AIEngine = atoiOr(aiPort, cfg.Ports.AIEngine)
	cfg.Ports.Web = atoiOr(webPort, cfg.Ports.Web)
	cfg.Ports.Admin = atoiOr(adminPort, cfg.Ports.Admin)

	cfg.Features.AIEngine = contains(features, "ai-engine")
	cfg.Features.Web = contains(features, "web")
	cfg.Features.Admin = contains(features, "admin")
	cfg.Features.BucketProxy = contains(features, "bucketproxy")

	return cfg, nil
}

func notEmpty(field string) func(string) error {
	return func(s string) error {
		if s == "" {
			return fmt.Errorf("%s 不能为空", field)
		}
		return nil
	}
}

func atoiOr(s string, fallback int) int {
	if v, err := strconv.Atoi(s); err == nil && v > 0 {
		return v
	}
	return fallback
}

func contains(haystack []string, needle string) bool {
	for _, s := range haystack {
		if s == needle {
			return true
		}
	}
	return false
}
