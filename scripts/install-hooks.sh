#!/usr/bin/env bash
set -euo pipefail
HOOKS_DIR="$(git rev-parse --git-dir)/hooks"
SCRIPTS_DIR="$(cd "$(dirname "$0")/hooks" && pwd)"
cp "$SCRIPTS_DIR/pre-commit" "$HOOKS_DIR/pre-commit"
chmod +x "$HOOKS_DIR/pre-commit"
echo "✓ Git hooks installed"
