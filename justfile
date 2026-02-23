# ugsys-platform-infrastructure task runner
default:
    @just --list

# Install git hooks (run once after cloning)
install-hooks:
    @bash scripts/install-hooks.sh

# Uninstall git hooks
uninstall-hooks:
    @rm -f .git/hooks/pre-commit .git/hooks/pre-push
    @echo "✓ Git hooks removed"

# Create a feature branch
branch name:
    git checkout -b feature/{{name}}

# Install all dependencies (including dev)
sync:
    uv sync --extra dev

# Lint CDK stacks
lint:
    uv tool run ruff check infra/stacks/

# Format CDK stacks
format:
    uv tool run ruff format infra/stacks/

# Format check without modifying
format-check:
    uv tool run ruff format --check infra/stacks/

# Synthesize all CDK stacks (validates without deploying)
cdk-synth:
    uv run cdk synth --app "python infra/app.py" --quiet

# Deploy all stacks (requires AWS credentials)
cdk-deploy env="dev":
    uv run cdk deploy --all --app "python infra/app.py" --context env={{env}}

# Destroy all stacks
cdk-destroy env="dev":
    uv run cdk destroy --all --app "python infra/app.py" --context env={{env}}

# Show CDK diff
cdk-diff:
    uv run cdk diff --app "python infra/app.py"

# IaC security scan with Checkov
iac-scan:
    @echo "=== CDK Synth ==="
    uv run cdk synth --app "python infra/app.py" --quiet
    @echo "=== Checkov ==="
    uv tool run checkov -d infra/cdk.out --framework cloudformation --soft-fail || true

# Security scan
security-scan:
    uv tool install bandit
    uv tool run bandit -r infra/stacks/ -ll -ii || true
