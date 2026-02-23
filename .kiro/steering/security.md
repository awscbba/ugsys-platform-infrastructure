---
inclusion: always
---

# Security Standards

## CI/CD Security Gates (ALL services MUST have these in ci.yml)

| Job | Tool | Blocks merge? |
|-----|------|---------------|
| SAST | Bandit (`bandit -r src/ -c pyproject.toml -ll`) | ✅ Yes |
| Dependency CVEs | Safety (`safety check`) | Advisory only |
| Secret scan | Gitleaks (every push, not just PRs) | ✅ Yes |
| IaC scan | Checkov (when `infra/` exists) | ✅ Yes |
| Type safety | mypy strict | ✅ Yes |
| Architecture guard | grep domain/application layer imports | ✅ Yes |

## Ruff Security Rules (REQUIRED in pyproject.toml)

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "S", "ANN", "RUF"]
ignore = ["S101"]  # assert ok in tests

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "S105", "S106", "ANN"]

[tool.bandit]
exclude_dirs = ["tests", ".venv"]
skips = ["B101"]
```

## Authentication

- JWT validation: RS256 only — reject HS256 and `none`
- All endpoints require auth except `/health`, `/`, `/api/v1/auth/login`, `/api/v1/auth/register`
- Service-to-service: `client_credentials` grant via Identity Manager `/api/v1/auth/service-token`
- Token validation delegated to `ugsys-auth-client` shared lib

```python
# ✅ Always check resource ownership (IDOR prevention)
async def get_user(self, user_id: UUID, requester_id: str) -> User:
    user = await self._repo.find_by_id(user_id)
    if user and str(user.id) != requester_id:
        raise ForbiddenError("Access denied")
    return user
```

## Required Middleware (every service, in this order)

```python
# src/presentation/middleware/ — all MUST exist
correlation_id.py      # CorrelationIdMiddleware  — request tracing
security_headers.py    # SecurityHeadersMiddleware — HSTS, CSP, X-Frame-Options
rate_limiting.py       # RateLimitMiddleware       — per-user, 60 req/min default
```

Security headers MUST include:
```python
{
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": "default-src 'self'",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
```

## Secrets Management

```python
# ❌ NEVER hardcode secrets
SECRET_KEY = "abc123"

# ✅ Always use pydantic-settings + env vars / AWS Secrets Manager
class Settings(BaseSettings):
    secret_key: str  # loaded from env or Secrets Manager at runtime
```

- No long-lived AWS credentials in CI — OIDC only (`AWS_ROLE_ARN` via GitHub secret)
- Rotate any exposed token immediately (e.g. Slack bot tokens)
- KMS encryption on all CloudWatch log groups (`CKV_AWS_158`)

## Input Validation

```python
from pydantic import BaseModel, field_validator
import html

class CreateUserRequest(BaseModel):
    email: EmailStr
    full_name: str

    @field_validator("full_name")
    @classmethod
    def sanitize(cls, v: str) -> str:
        return html.escape(v.strip())
```

- Request size limit: 1 MB max (middleware)
- All inputs validated via Pydantic v2 before reaching application layer
- Never trust client-provided user IDs — always use the authenticated identity from JWT

## Never Log Sensitive Data

```python
# ❌ NEVER
logger.info("login", password=password, token=token, api_key=key)

# ✅ ALWAYS
logger.info("login.success", user_id=user_id)
logger.info("token.issued", user_id=user_id, expires_in=3600)
```

## IaC Security (CDK stacks)

- All CloudWatch log groups: `encryption_key=kms_key` (CKV_AWS_158)
- KMS key policy must grant `logs.<region>.amazonaws.com` permission
- OIDC trust scoped to `ref:refs/heads/main` and `environment:prod`
- No wildcard `*` in IAM policies — least privilege always
