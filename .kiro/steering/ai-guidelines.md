---
inclusion: always
---

# AI Assistant Guidelines — ugsys Platform

## NEVER DO THESE

1. **NEVER push directly to `main`** — always `feature/` branch → PR
2. **NEVER merge to `main` without explicit user approval**
3. **NEVER create or trigger production deployments without confirmation**
4. **NEVER modify `.github/workflows/` files without explaining the change first**
5. **NEVER delete branches without confirmation**
6. **NEVER force push (`git push --force`)**
7. **NEVER create files at the workspace root** — only work inside a named repo (`ugsys-identity-manager/`, `ugsys-shared-libs/`, etc.)
8. **NEVER run commands at the workspace root** — devbox dependencies only exist inside each repo; always use `cwd` pointing to the repo
9. **NEVER introduce a new dependency without checking if it's already in `pyproject.toml` or covered by a shared lib**
10. **NEVER skip the architecture layer rules** — domain imports nothing external, application imports nothing from infra

## ALWAYS DO THESE

### Git workflow
- Work on `feature/<description>` or `fix/<description>` branches
- Conventional commits: `feat:`, `fix:`, `ci:`, `refactor:`, `docs:`, `test:`
- One logical change per commit
- Push and open a PR — never commit directly to `main`

### Before writing any code
- Check if similar functionality already exists in the codebase or in `ugsys-shared-libs`
- Identify which layer the code belongs to (`presentation/`, `application/`, `domain/`, `infrastructure/`)
- Confirm the file goes in the right directory per the canonical structure

### Before any AWS operation
- State which service, environment, and resources are affected
- Estimate cost impact if creating new resources
- Confirm there is a rollback plan

### After writing code
- Run `uv run ruff check src/ tests/` and `uv run ruff format src/ tests/` before committing
- Run `uv run pytest tests/unit/ -v` to confirm tests pass
- Let the pre-commit hook run — do not bypass it with `--no-verify`

## Branch Naming

```
feature/description-of-feature
fix/description-of-fix
docs/documentation-update
refactor/code-improvement
ci/pipeline-change
```

## Canonical Directory Structure (per service)

```
ugsys-<service>/
├── src/
│   ├── presentation/api/v1/     # Routers — versioned routes only
│   ├── presentation/middleware/ # correlation_id, security_headers, rate_limiting
│   ├── application/services/    # Use case orchestration
│   ├── application/commands/    # Write DTOs
│   ├── application/queries/     # Read DTOs
│   ├── application/dtos/        # Shared DTOs
│   ├── application/interfaces/  # Inbound port interfaces
│   ├── domain/entities/         # Pure Python dataclasses
│   ├── domain/value_objects/    # Immutable types
│   ├── domain/repositories/     # Abstract outbound ports (ABC)
│   ├── infrastructure/persistence/  # DynamoDB adapters
│   ├── infrastructure/adapters/     # HTTP clients, S3, SES, SNS
│   ├── infrastructure/messaging/    # EventBridge publisher/subscriber
│   ├── infrastructure/logging.py    # structlog configure_logging()
│   ├── config.py                # pydantic-settings Settings
│   └── main.py                  # Composition root + lifespan
├── tests/
│   ├── unit/                    # Pure unit tests, no AWS
│   └── integration/             # moto-based DynamoDB tests
├── scripts/
│   ├── hooks/                   # pre-commit, pre-push
│   └── install-hooks.sh
├── .github/workflows/
│   ├── ci.yml                   # lint, typecheck, test, sast, secret-scan, arch-guard
│   └── deploy.yml               # deploy on merge to main, environment: prod gate
├── devbox.json
├── justfile
└── pyproject.toml
```

**Files that do NOT belong at workspace root:**
- test files, scripts, documentation summaries, temp files
- Any `*.py` file that isn't part of a named repo

## Architecture Checklist (before every PR)

```
□ Layer structure matches canonical: presentation/application/domain/infrastructure
□ domain/ has zero imports from infra, application, or presentation
□ application/ has zero imports from infrastructure or presentation
□ All routes versioned under /api/v1/
□ src/config.py exists with pydantic-settings
□ src/main.py uses lifespan pattern
□ infrastructure/logging.py has configure_logging()
□ CorrelationIdMiddleware present in presentation/middleware/
□ SecurityHeadersMiddleware present
□ RateLimitMiddleware present
□ structlog used everywhere — no print(), no logging.getLogger()
□ No hardcoded secrets or credentials
□ Bandit + ruff S rules pass on src/
□ All new code has unit tests
```

## Shared Libs — Use Before Reinventing

| Need | Use |
|------|-----|
| JWT validation / auth middleware | `ugsys-auth-client` |
| Structured logging setup | `ugsys-logging-lib` |
| EventBridge publish/subscribe | `ugsys-event-lib` |
| Test fixtures, mocks, factories | `ugsys-testing-lib` |

Add via `pyproject.toml`:
```toml
[tool.uv.sources]
ugsys-auth-client = { git = "https://github.com/awscbba/ugsys-shared-libs", tag = "auth-client/v0.1.0", subdirectory = "auth-client" }
```

## CI/CD — What Each Workflow Does

### `ci.yml` (every push to `feature/**`, every PR to `main`)
1. `lint` — ruff check + format check
2. `typecheck` — mypy strict
3. `test` — pytest unit tests, 80% coverage gate
4. `sast` — Bandit
5. `sast-semgrep` — Semgrep (`p/python`, `p/security-audit`, `p/owasp-top-ten`, `p/secrets`)
6. `dependency-scan` — Safety (advisory)
7. `sbom` — CycloneDX → Trivy scan (CRITICAL/HIGH, blocks merge)
8. `secret-scan` — Gitleaks (`fetch-depth: 0`)
9. `arch-guard` — grep for layer boundary violations
10. `notify-failure` — Slack on any failure

### `codeql.yml` (PR to `main` + weekly Monday 06:00 UTC)
- CodeQL Python analysis — `security-extended,security-and-quality`
- Results to GitHub Security tab (SARIF) — advisory, does not block merge

### `security-scan.yml` (DAST — post-deploy to `main`, also `workflow_dispatch`)
- OWASP ZAP baseline scan — blocks on FAIL rules (XSS, SQLi, CORS, HSTS, cookie flags)
- Nuclei scan — blocks on critical findings
- Slack notification on any finding

### `deploy.yml` (merge to `main` only)
- Requires `environment: prod` gate approval
- OIDC auth — no static AWS keys
- Slack success/failure notification

### Git hooks (install via `just install-hooks`)
- `pre-commit`: blocks commits to `main`; ruff lint + format
- `pre-push`: blocks push to `main`; mypy strict + unit tests
- Never bypass with `--no-verify`

## Emergency Procedures

### Accidental push to main
```bash
git revert <commit-hash>
git push origin main
```

### Accidental branch delete
```bash
git checkout -b <branch-name> <last-known-sha>
git push origin <branch-name>
```

### Rollback a Lambda deploy
```bash
# From inside the repo
just diff   # see what changed
# Then revert the commit and let CI/CD redeploy
```

## Communication Format

When proposing a significant change, use this structure:

```
What: [clear description of the change]
Layer: [which layer(s) are affected]
Files: [list of files to be created/modified]
Tests: [what tests will cover this]
Risk: [low/medium/high — why]
```

No need to ask for confirmation on routine tasks (lint fixes, test runs, formatting).
Ask before: creating new repos, modifying CI workflows, any AWS resource creation, merging PRs.
