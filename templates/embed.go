// Package templates 通过 embed 把 templates 下所有模板内嵌进二进制。
package templates

import "embed"

// FS 是所有模板文件的根。
// 注意：embed 路径会去掉 templates/ 前缀，因此遍历得到的相对路径就是
// 目标项目里的相对路径（例如 backend-api/cmd/api/main.go.tmpl）。
//
//go:embed all:files
var FS embed.FS
