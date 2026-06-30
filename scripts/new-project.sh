#!/bin/bash

# Usage: ./scripts/new-project.sh <name> <owner> [brand] [domain]
# Example: ./scripts/new-project.sh browser-agent Lucy-97 BrowserAgent browser-agent.ai

set -e

NAME=$1
OWNER=$2
BRAND=${3:-$NAME}
DOMAIN=${4:-"${NAME}.ai"}
CODE_DIR=${CODE_DIR:-"$HOME/Downloads/Agent"}

if [ -z "$NAME" ] || [ -z "$OWNER" ]; then
  echo "Usage: $0 <name> <owner> [brand] [domain]"
  echo "Example: $0 browser-agent Lucy-97 BrowserAgent browser-agent.ai"
  exit 1
fi

OUT_DIR="$CODE_DIR/$NAME"

if [ -d "$OUT_DIR" ]; then
  echo "Error: Directory $OUT_DIR already exists. If you want to use a different directory name, please rename it or set CODE_DIR."
  exit 1
fi

echo "🚀 Building platform CLI..."
go build -o /tmp/platform ./cmd/platform

echo "🚀 Scaffolding new project: $NAME..."
/tmp/platform init "$NAME" --yes \
  --module "github.com/$OWNER/$NAME" \
  --brand "$BRAND" \
  --domain "$DOMAIN" \
  -o "$OUT_DIR"

echo "🚀 Tidying up Go modules..."
for svc in backend-api backend-gateway; do
  if [ -d "$OUT_DIR/$svc" ]; then
    echo "  -> tidying $svc"
    (cd "$OUT_DIR/$svc" && go mod tidy && go build ./...)
  fi
done

echo "🚀 Initializing git repository..."
(
  cd "$OUT_DIR"
  git init -b main
  git add -A
  git commit -m "chore: bootstrap $NAME from platform-scaffold"
)

echo "🚀 Creating GitHub repository $OWNER/$NAME and pushing..."
(
  cd "$OUT_DIR"
  # Note: Requires GitHub CLI (gh) to be installed and authenticated
  gh repo create "$OWNER/$NAME" --public --source=. --push
)

echo "✅ Done! Project created at $OUT_DIR and pushed to https://github.com/$OWNER/$NAME"
