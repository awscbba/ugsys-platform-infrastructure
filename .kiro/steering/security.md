---
inclusion: always
---

# Security Standards

## CI/CD Security Gates (ALL services MUST have these in ci.yml)

| Job | Tool | Blocks merge? |
|-----|------|---------------|
| SAST | Bandit (`bandit -r src/ -c pyproject.toml -ll`) | ✅ Yes |
| SAST | Semgrep (`p/python`, `p/security-audit`, `p/owasp-top-ten`, `p/secrets`) | ✅ Yes |
| Dependency CVEs | Safety (`safety check`) | Advisory only |
| SBOM + CVE scan | CycloneDX → Trivy (CRITICAL/HIGH, ignore-unfixed) | ✅ Yes |
| Secret scan | Gitleaks (every push, `fetch-depth: 0`) | ✅ Yes |
| IaC scan | Checkov (when `infra/` exists) | ✅ Yes |
| Type safety | mypy strict | ✅ Yes |
| Architecture guard | grep domain/application layer imports | ✅ Yes |
| CodeQL | `codeql.yml` — weekly + on PR (Python, `security-extended`) | Advisory (SARIF) |
| DAST | `security-scan.yml` — OWASP ZAP + Nuclei, post-deploy | ✅ Yes (critical) |

**Why both Bandit and Semgrep**: Bandit is fast and Python-specific; Semgrep catches complex patterns (SQL injection, XSS, SSRF, unsafe deserialization) that Bandit misses. No single scanner catches everything.

**Git hooks** (install via `just install-hooks`):
- `pre-commit`: blocks commits to `main`; runs ruff lint + format — fast, every commit
- `pre-push`: blocks push to `main`; runs mypy + unit tests — catches regressions before CI
- Never bypass with `--no-verify`

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
- Algorithm restriction checked BEFORE signature verification (prevents algorithm confusion)
- All endpoints require auth except `/health`, `/`, `/api/v1/auth/login`, `/api/v1/auth/register`
- Service-to-service: `client_credentials` grant via Identity Manager `/api/v1/auth/service-token`
- Token validation delegated to `ugsys-auth-client` shared lib

**JWT strict validation requirements:**
- `aud` must match the service's Cognito client ID — never skip audience validation
- `iss` must match the exact Cognito User Pool URL
- Required claims: `sub`, `exp`, `iat`, `iss` — reject if any are missing
- JWKS cache with TTL (1 hour) + forced refresh when `kid` not found (handles key rotation)
- On validation failure: return generic 401 — never expose JWT error details to caller

```python
# ✅ Always check resource ownership (IDOR prevention)
async def get_resource(resource_id: UUID, requester_id: str, is_admin: bool) -> Resource:
    resource = await self._repo.find_by_id(resource_id)
    if resource is None:
        raise NotFoundError(message=f"Resource {resource_id} not found", user_message="Resource not found")
    if not is_admin and str(resource.owner_id) != requester_id:
        raise AuthorizationError(message=f"User {requester_id} attempted IDOR on {resource_id}", user_message="Access denied")
    return resource
```

## Required Middleware (every service, in this order)

```python
# src/presentation/middleware/ — all MUST exist
correlation_id.py      # CorrelationIdMiddleware  — request tracing
security_headers.py    # SecurityHeadersMiddleware — HSTS, CSP, X-Frame-Options
rate_limiting.py       # RateLimitMiddleware       — per-user, 60 req/min default
```

Security headers MUST include (see Section 9.2 of platform-contract.md for full implementation):
```python
"X-Content-Type-Options": "nosniff"
"X-Frame-Options": "DENY"
"X-XSS-Protection": "0"                    # Disabled — CSP is the correct defense
"Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload"
"Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; ..."  # strict for API
"Referrer-Policy": "strict-origin-when-cross-origin"
"Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()"
"Cross-Origin-Opener-Policy": "same-origin"
"Cross-Origin-Resource-Policy": "same-origin"
"Cache-Control": "no-store, no-cache, must-revalidate"  # on /api/* routes
# Server header: REMOVE — prevents technology fingerprinting
```

**Rate limiting** — per authenticated user (JWT `sub`), not just per IP:
- 60 requests/minute (default)
- 1000 requests/hour
- 10 requests/second burst limit
- Fallback to IP-based limiting for unauthenticated requests
- Response headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- 429 response includes `Retry-After` header

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

## CSRF Protection (browser-facing services)

Services that issue `httpOnly` cookies to browser clients MUST add `CSRFMiddleware` using the Double Submit Cookie pattern. Pure API services using Bearer tokens do not need it.

- Protected methods: `POST`, `PUT`, `PATCH`, `DELETE`
- Token: `{random_hex}.{timestamp}.{hmac_signature}` — signed with service secret key, 1-hour TTL
- Cookie: `SameSite=Strict`, NOT `httpOnly` (JS must read it), `Secure=True` in prod
- Header: `X-CSRF-Token` — must match cookie value (constant-time comparison)
- See Section 9.14 of platform-contract.md for full implementation

## Cookie Security (browser-facing services)

Auth tokens issued as cookies MUST use:
- `httpOnly=True` — prevents JavaScript access (XSS protection)
- `Secure=True` — HTTPS only in production
- `SameSite="lax"` — CSRF protection while allowing navigation
- Never store tokens in `localStorage` or `sessionStorage`

## CORS Policy (all services)

```python
# ✅ Explicit allowlist — never wildcard
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,  # ["https://admin.cbba.cloud.org.bo"]
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRF-Token"],
)
```

- Never reflect arbitrary `Origin` headers back
- Never allow `null` origin
- No `Access-Control-Allow-Origin: *` when credentials are involved

## Error Disclosure Prevention

```python
# ❌ NEVER — exposes framework, DB schema, stack traces
raise HTTPException(status_code=500, detail=f"DynamoDB error: {e}")

# ✅ ALWAYS — safe message to client, full detail in logs only
raise RepositoryError(
    message=f"DynamoDB ConditionalCheckFailed: {e}",  # logs
    user_message="An unexpected error occurred",       # client
)
```

- Remove `Server` response header — prevents technology fingerprinting
- Error responses must never contain: `Traceback`, file paths, framework names, DB column names
- `user_message` and `message` are NEVER the same string (see Section 9.11)

## Security Monitoring (all services)

CloudWatch metric filters and alarms must be defined in each service's CDK stack:

| Alarm | Threshold | Period | Action |
|-------|-----------|--------|--------|
| Error rate | ≥ 10 | 5 min | SNS → Slack |
| Critical errors | ≥ 1 | 1 min | SNS → Slack |
| Auth failures | ≥ 100 | 1 hour | SNS → Slack |
| Access denied spikes | ≥ 30 | 5 min | SNS → Slack |
| Slow operations p95 | > 1000 ms | 5 min | SNS → Slack |

See Section 9.17 of platform-contract.md for metric filter patterns.
