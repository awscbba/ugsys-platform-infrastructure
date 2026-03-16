# ugsys-platform-infrastructure task runner.
# Run all commands from inside the repo root: cd ugsys-platform-infrastructure && just <recipe>
set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# ── Setup ─────────────────────────────────────────────────────────────────────

# Install git hooks (run once after cloning)
install-hooks:
    bash scripts/install-hooks.sh

# Install all dependencies (including dev)
sync:
    uv sync --extra dev

# Create a feature branch
branch name:
    git checkout -b feature/{{name}}

# ── Code quality ──────────────────────────────────────────────────────────────

# Lint CDK stacks
lint:
    uv run ruff check infra/

# Format CDK stacks
format:
    uv run ruff format infra/

# Format check without modifying
format-check:
    uv run ruff format --check infra/

# ── CDK (all run from infra/ where cdk.json lives) ───────────────────────────

# Synthesize all stacks — validates without deploying
# Uses npx aws-cdk to avoid conflicts with other tools named 'cdk' on PATH.
synth:
    JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1 npx aws-cdk synth --app "uv run python infra/app.py"

# Bootstrap CDK in your AWS account (one-time per account/region)
# Usage: just bootstrap <account_id> [region] [env]
bootstrap account region="us-east-1" env="prod":
    @echo "=== CDK Bootstrap (account={{account}}, region={{region}}, env={{env}}) ==="
    cd infra && npx aws-cdk bootstrap aws://{{account}}/{{region}} \
        --context env={{env}} \
        --context account={{account}} \
        --context region={{region}}
    @echo "✓ Bootstrap complete"

# Deploy OIDC stack first — creates GitHub Actions IAM roles
# Then add AWS_ROLE_ARN + AWS_ACCOUNT_ID to GitHub repo/org secrets
# Usage: just deploy-oidc <account_id> [region] [env]
deploy-oidc account region="us-east-1" env="prod":
    @echo "=== Deploying OIDC stack (account={{account}}, region={{region}}, env={{env}}) ==="
    cd infra && npx aws-cdk deploy UgsysPlatformGithubOidc-{{env}} \
        --context env={{env}} \
        --context account={{account}} \
        --context region={{region}} \
        --require-approval never
    @echo ""
    @echo "✓ OIDC stack deployed. Add these to GitHub secrets:"
    @echo "  AWS_ROLE_ARN   = arn:aws:iam::{{account}}:role/ugsys-github-deploy-ugsys-platform-infrastructure"
    @echo "  AWS_ACCOUNT_ID = {{account}}"

# Show diff against deployed state
# Usage: just diff <account_id> [region] [env]
diff account region="us-east-1" env="prod":
    cd infra && npx aws-cdk diff --all \
        --context env={{env}} \
        --context account={{account}} \
        --context region={{region}} \
        2>&1 || true

# Deploy all stacks
# Usage: just deploy <account_id> [region] [env]
deploy account region="us-east-1" env="prod":
    @echo "=== CDK Deploy all (account={{account}}, region={{region}}, env={{env}}) ==="
    cd infra && npx aws-cdk deploy --all \
        --context env={{env}} \
        --context account={{account}} \
        --context region={{region}} \
        --require-approval never \
        --outputs-file cdk-outputs.json
    @echo "✓ Deploy complete — outputs saved to infra/cdk-outputs.json"

# Destroy all stacks (destructive!)
# Usage: just destroy <account_id> [region] [env]
destroy account region="us-east-1" env="dev":
    @echo "=== CDK Destroy all (account={{account}}, region={{region}}, env={{env}}) ==="
    cd infra && npx aws-cdk destroy --all \
        --context env={{env}} \
        --context account={{account}} \
        --context region={{region}}

# ── Security ──────────────────────────────────────────────────────────────────

# IaC security scan with Checkov (runs synth first)
iac-scan:
    cd infra && npx aws-cdk synth --app "uv run python app.py" --quiet
    uv tool run checkov -d infra/cdk.out --framework cloudformation --soft-fail || true

# Static security scan with Bandit
security-scan:
    uv tool install bandit
    uv tool run bandit -r infra/stacks/ -ll -ii || true
