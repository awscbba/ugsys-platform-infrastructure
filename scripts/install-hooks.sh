#!/usr/bin/env bash
# Installs git hooks for ugsys-platform-infrastructure.
# Run once after cloning: bash scripts/install-hooks.sh
set -euo pipefail

HOOKS_DIR="$(git rev-parse --git-dir)/hooks"
SCRIPTS_DIR="$(cd "$(dirname "$0")/hooks" && pwd)"

echo "Installing git hooks..."

for hook in pre-commit pre-push; do
  cp "$SCRIPTS_DIR/$hook" "$HOOKS_DIR/$hook"
  chmod +x "$HOOKS_DIR/$hook"
  echo "  ✓ $hook installed"
done

echo ""
echo "Done. Hooks will run automatically on commit and push."
