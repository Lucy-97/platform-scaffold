// Command platform 是技术共享平台脚手架的 CLI 入口。
//
// 用法:
//
//	platform init <project-name>     交互式生成新项目
//	platform version                 打印版本信息
package main

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/platform-scaffold/cli/internal/config"
	"github.com/platform-scaffold/cli/internal/generator"
	"github.com/platform-scaffold/cli/internal/prompt"
)

const version = "0.1.0"

func main() {
	root := &cobra.Command{
		Use:   "platform",
		Short: "Polyglot microservice platform scaffold (Go + Python + Next.js)",
		Long: `platform 是基于 xdd / aigc 两个生产项目沉淀的微服务脚手架。
它一次性生成 Go Gateway + Go API + Python AI Engine + Next.js Web + React Admin 全套骨架，
并附带 deploy-local / deploy-k3s / database 等开箱即用配置；通用组件（errcode/crypto/dynconfig/cache/lock/middleware/response）内聚在各 Go 服务的 internal/ 下。`,
	}

	root.AddCommand(initCmd())
	root.AddCommand(versionCmd())

	if err := root.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func initCmd() *cobra.Command {
	var nonInteractive bool
	var outputDir string

	cmd := &cobra.Command{
		Use:   "init [project-name]",
		Short: "在当前目录生成一个新的微服务平台项目",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			defaultName := ""
			if len(args) > 0 {
				defaultName = args[0]
			}

			cfg, err := prompt.AskProjectConfig(defaultName, nonInteractive)
			if err != nil {
				return err
			}

			if outputDir == "" {
				outputDir = cfg.ProjectName
			}
			cfg.OutputDir = outputDir

			if err := config.Validate(cfg); err != nil {
				return fmt.Errorf("配置不合法: %w", err)
			}

			gen := generator.New(cfg)
			count, err := gen.Run()
			if err != nil {
				return fmt.Errorf("生成失败: %w", err)
			}

			fmt.Printf("\n生成完成: %d 个文件已写入 %s\n", count, outputDir)
			fmt.Println()
			fmt.Println("Next steps:")
			fmt.Printf("  cd %s\n", outputDir)
			fmt.Println("  cp deploy/local/.env.example deploy/local/.env")
			fmt.Println("  ./deploy/local/start.sh start")
			return nil
		},
	}

	cmd.Flags().BoolVar(&nonInteractive, "yes", false, "使用全部默认值，不进入交互模式")
	cmd.Flags().StringVarP(&outputDir, "output", "o", "", "输出目录（默认与项目名一致）")
	return cmd
}

func versionCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "打印版本信息",
		Run: func(cmd *cobra.Command, args []string) {
			fmt.Printf("platform %s\n", version)
		},
	}
}
