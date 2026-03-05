#!/usr/bin/env bash
set -euo pipefail

HOOKS_DIR="$(git rev-parse --git-dir)/hooks"
SCRIPTS_DIR="$(cd "$(dirname "$0")/hooks" && pwd)"

for hook in pre-commit pre-push; do
    cp "$SCRIPTS_DIR/$hook" "$HOOKS_DIR/$hook"
    chmod +x "$HOOKS_DIR/$hook"
    echo "✓ Installed $hook"
done

echo "✓ All git hooks installed"
