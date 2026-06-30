// Package config 定义脚手架在生成项目时需要的所有变量。
//
// ProjectConfig 会被传给 text/template 渲染所有 .tmpl 文件。
package config

import (
	"fmt"
	"regexp"
	"strings"
)

// ProjectConfig 是模板渲染的总入口。所有模板变量必须放在这个结构体里。
type ProjectConfig struct {
	// ProjectName kebab-case 项目名（如 my-app），用作目录名、docker service、k8s namespace。
	ProjectName string

	// Brand 展示用品牌名（如 MyApp），用在 README、UI 标题、邮件模板等地方。
	Brand string

	// Domain 服务域名（如 myapp.ai），用于 CORS 白名单、Cookie domain、Cloudflare worker name。
	Domain string

	// GoModulePath Go 服务的 module 路径前缀。
	// 例如 github.com/me/my-app，则 api/go.mod 会写 module github.com/me/my-app/api。
	GoModulePath string

	// Ports 各服务监听端口。
	Ports Ports

	// Features 各模块的开关，false 时跳过对应模板。
	Features Features

	// InitGit 生成完成后是否执行 git init。
	InitGit bool

	// OutputDir 实际写入的目标目录（绝对或相对路径），由 CLI 注入，不需要用户填写。
	OutputDir string
}

// Ports 收纳所有服务端口，避免散落在不同模板里。
type Ports struct {
	Gateway  int
	API      int
	AIEngine int
	Web      int
	Admin    int
	MySQL    int
	Redis    int
}

// Features 控制哪些模板被渲染。Gateway 与 API 是必装核心，没有开关。
type Features struct {
	AIEngine bool
	Web      bool
	Admin    bool
	// BucketProxy 是否生成 Cloudflare R2 反向代理 Worker（bucketproxy/）。默认关闭。
	BucketProxy bool
}

// Defaults 返回一个填好合理默认值的 ProjectConfig，用作交互式提问的初始值。
func Defaults(projectName string) ProjectConfig {
	if projectName == "" {
		projectName = "my-app"
	}
	brand := toBrand(projectName)
	return ProjectConfig{
		ProjectName:  projectName,
		Brand:        brand,
		Domain:       projectName + ".ai",
		GoModulePath: "github.com/example/" + projectName,
		Ports: Ports{
			Gateway:  8080,
			API:      8001,
			AIEngine: 8002,
			Web:      3000,
			Admin:    5174,
			MySQL:    3306,
			Redis:    6379,
		},
		Features: Features{
			AIEngine:    true,
			Web:         true,
			Admin:       true,
			BucketProxy: false,
		},
		InitGit: true,
	}
}

// Validate 校验配置必填项与格式。
func Validate(cfg ProjectConfig) error {
	if !kebabCase.MatchString(cfg.ProjectName) {
		return fmt.Errorf("ProjectName 必须是 kebab-case (a-z0-9-)，得到 %q", cfg.ProjectName)
	}
	if cfg.Brand == "" {
		return fmt.Errorf("Brand 不能为空")
	}
	if cfg.GoModulePath == "" {
		return fmt.Errorf("GoModulePath 不能为空")
	}
	if cfg.Ports.Gateway == 0 || cfg.Ports.API == 0 {
		return fmt.Errorf("Gateway/API 端口必须大于 0")
	}
	return nil
}

var kebabCase = regexp.MustCompile(`^[a-z][a-z0-9-]*[a-z0-9]$`)

// toBrand 把 my-app -> MyApp，作为默认 Brand 提示词。
func toBrand(name string) string {
	parts := strings.Split(name, "-")
	for i, p := range parts {
		if p == "" {
			continue
		}
		parts[i] = strings.ToUpper(p[:1]) + p[1:]
	}
	return strings.Join(parts, "")
}
