#!/usr/bin/env bash
#
# smoke.sh — 生成一个示例项目并验证它真的能构建。
#
# 步骤：build CLI → init demo → 断言无垃圾/无旧共享库 →
#       go build 各 Go 服务 → 前端 npm install + tsc。
#
# 用法：bash scripts/smoke.sh [输出目录]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$(mktemp -d)/smoke-demo}"
BIN="$(mktemp -d)/platform"   # 构建到临时目录，避免污染仓库工作区

echo "==> build CLI"
go build -o "$BIN" "$ROOT/cmd/platform"

echo "==> generate -> $OUT"
rm -rf "$OUT"
"$BIN" init smoke-demo --yes -o "$OUT"

echo "==> assert no junk leaked"
if find "$OUT" \( -name .DS_Store -o -path '*.idea*' -o -path '*.wrangler*' -o -name '*.iml' \) | grep -q .; then
  echo "FAIL: junk files leaked into generated project"; exit 1
fi

echo "==> assert pkg-platform-core absent"
[ ! -e "$OUT/pkg-platform-core" ] || { echo "FAIL: pkg-platform-core was generated"; exit 1; }

for svc in backend-gateway backend-api; do
  echo "==> go build $svc"
  ( cd "$OUT/$svc" && go mod tidy && go vet ./... && go build ./... )
done

for fe in frontend-web frontend-admin; do
  if [ -d "$OUT/$fe" ]; then
    echo "==> tsc $fe"
    ( cd "$OUT/$fe" && npm install --no-audit --no-fund && npx tsc --noEmit )
  fi
done

echo "==> ALL GREEN ($OUT)"
