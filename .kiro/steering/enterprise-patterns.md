---
inclusion: always
---

# Enterprise Code Quality Patterns

These rules apply to ALL `ugsys-*` services without exception.
They complement `architecture.md` (layer structure) and `logging.md` (structlog).

---

## 1. Zero Code Duplication

**Search before you implement.** Before writing any function, class, or utility:

1. Search the current service's `src/` for existing implementations
2. Check `ugsys-shared-libs` — if it's there, use it
3. If similar logic exists in another layer, extract it to the right place

```python
# ❌ NEVER — duplicate validation logic across services
def validate_email(email: str) -> bool:
    return "@" in email  # already in Pydantic EmailStr

# ✅ ALWAYS — use what exists
from pydantic import EmailStr
class RegisterUserRequest(BaseModel):
    email: EmailStr
```

**Rule**: If you find yourself writing the same logic twice, stop. Extract it.

---

## 2. Enterprise Exception Hierarchy

Every service MUST define a domain exception hierarchy in `src/domain/exceptions.py`.
Never raise raw `Exception`, `ValueError`, or `HTTPException` from application or domain layers.

### Base hierarchy (copy this into every service)

```python
# src/domain/exceptions.py
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DomainError(Exception):
    """Base for all domain errors. Never expose internal details to callers."""
    message: str                          # internal — for logs only
    user_message: str = "An error occurred"  # safe — returned to client
    error_code: str = "INTERNAL_ERROR"    # machine-readable code
    additional_data: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


# ── Validation ────────────────────────────────────────────────────────────────
class ValidationError(DomainError):
    """Input failed business rule validation. HTTP 422."""
    error_code: str = "VALIDATION_ERROR"

class NotFoundError(DomainError):
    """Requested resource does not exist. HTTP 404."""
    error_code: str = "NOT_FOUND"

class ConflictError(DomainError):
    """Resource already exists or state conflict. HTTP 409."""
    error_code: str = "CONFLICT"

# ── Auth ──────────────────────────────────────────────────────────────────────
class AuthenticationError(DomainError):
    """Identity could not be verified. HTTP 401."""
    error_code: str = "AUTHENTICATION_FAILED"

class AuthorizationError(DomainError):
    """Authenticated identity lacks required permission. HTTP 403."""
    error_code: str = "FORBIDDEN"

class AccountLockedError(DomainError):
    """Account is locked. HTTP 423."""
    error_code: str = "ACCOUNT_LOCKED"

# ── Infrastructure ────────────────────────────────────────────────────────────
class RepositoryError(DomainError):
    """Data access failure. HTTP 500. Never expose DB details."""
    error_code: str = "REPOSITORY_ERROR"

class ExternalServiceError(DomainError):
    """Downstream service call failed. HTTP 502."""
    error_code: str = "EXTERNAL_SERVICE_ERROR"
```

### Exception handler (presentation layer)

```python
# src/presentation/middleware/exception_handler.py
import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from src.domain.exceptions import (
    DomainError, ValidationError, NotFoundError, ConflictError,
    AuthenticationError, AuthorizationError, AccountLockedError,
)

logger = structlog.get_logger()

STATUS_MAP = {
    ValidationError: 422,
    NotFoundError: 404,
    ConflictError: 409,
    AuthenticationError: 401,
    AuthorizationError: 403,
    AccountLockedError: 423,
}

async def domain_exception_handler(request: Request, exc: DomainError) -> JSONResponse:
    status = STATUS_MAP.get(type(exc), 500)
    logger.error(
        "domain_error",
        error_code=exc.error_code,
        message=exc.message,          # internal detail — logs only
        status=status,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=status,
        content={
            "error": exc.error_code,
            "message": exc.user_message,  # safe — never internal detail
            "data": exc.additional_data,
        },
    )

async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled_exception", error=str(exc), path=request.url.path, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "INTERNAL_ERROR", "message": "An unexpected error occurred"},
    )
```

Register in `main.py`:
```python
from src.domain.exceptions import DomainError
from src.presentation.middleware.exception_handler import (
    domain_exception_handler, unhandled_exception_handler
)

app.add_exception_handler(DomainError, domain_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)
```

### Usage in application layer

```python
# ✅ Raise domain exceptions — never HTTPException from application layer
async def execute(self, cmd: RegisterUserCommand) -> User:
    existing = await self._repo.find_by_email(cmd.email)
    if existing:
        raise ConflictError(
            message=f"Email {cmd.email} already registered",  # internal
            user_message="This email address is already in use",  # safe
            error_code="EMAIL_ALREADY_EXISTS",
        )
```

---

## 3. User-Safe Error Messages

**NEVER expose internal details to API callers.**

```python
# ❌ NEVER — exposes DB schema, internal state, stack traces
raise HTTPException(status_code=500, detail=f"DynamoDB error: {e}")
raise HTTPException(status_code=400, detail=f"Column 'email' violates unique constraint")

# ✅ ALWAYS — safe message for client, full detail in logs
raise ConflictError(
    message=f"DynamoDB ConditionalCheckFailed on users table: {e}",  # logs
    user_message="This email address is already in use",              # client
)
```

Rule: `message` is for logs. `user_message` is for the API response. They are never the same string.

---

## 4. Application Factory Pattern

Every service `src/main.py` MUST follow this exact structure:

```python
# src/main.py
import structlog
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from fastapi import FastAPI

from src.config import settings
from src.infrastructure.logging import configure_logging
from src.domain.exceptions import DomainError
from src.presentation.middleware.correlation_id import CorrelationIdMiddleware
from src.presentation.middleware.security_headers import SecurityHeadersMiddleware
from src.presentation.middleware.rate_limiting import RateLimitMiddleware
from src.presentation.middleware.exception_handler import (
    domain_exception_handler, unhandled_exception_handler
)
from src.presentation.api.v1 import health, auth, users  # domain routers

configure_logging(settings.service_name, settings.log_level)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("startup.begin", service=settings.service_name, version=settings.version)
    # wire dependencies here
    yield
    logger.info("shutdown.complete", service=settings.service_name)


def create_app() -> FastAPI:
    """Application factory — single place for all wiring."""
    app = FastAPI(
        title=settings.service_name,
        version=settings.version,
        docs_url="/docs" if settings.environment != "prod" else None,
        lifespan=lifespan,
    )

    # Middleware — order matters (last added = first executed)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware, requests_per_minute=60)

    # Exception handlers
    app.add_exception_handler(DomainError, domain_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # Domain routers — versioned
    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")

    return app


app = create_app()
```

Rules:
- `configure_logging()` is called at module level — before anything else
- `create_app()` is the single composition root — no wiring outside it
- Docs disabled in prod (`docs_url=None`)
- Exception handlers registered before routers

---

## 5. Domain Router Standards

Each router file handles ONE business domain. No cross-domain logic.

```python
# src/presentation/api/v1/users.py
import structlog
from fastapi import APIRouter, Depends, status
from src.application.services.user_service import UserService
from src.application.dtos.user_dtos import RegisterUserRequest, UserResponse
from src.presentation.dependencies import get_user_service

logger = structlog.get_logger()
router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    request: RegisterUserRequest,
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    """Register a new user."""
    # presentation layer: validate input, call application, return DTO
    # NO business logic here — that belongs in UserService
    user = await service.register(request)
    return UserResponse.from_domain(user)
```

Rules:
- Router injects its domain service only — never another domain's service directly
- No business logic in routers — delegate entirely to application service
- Response models are DTOs — never return domain entities directly
- `logger` at module level, not inside functions

---

## 6. Ubiquitous Language

Use consistent terminology within each domain. The same concept must have the same name everywhere — in code, logs, API responses, and documentation.

| Domain | Entity name | NOT |
|--------|-------------|-----|
| Identity | `User` | `Person`, `Member`, `Account` |
| Projects | `Project`, `Subscription` | `Enrollment`, `Registration` |
| Omnichannel | `Message`, `Channel` | `Notification`, `Alert` |
| Profiles | `UserProfile` | `Profile`, `PersonProfile` |

When porting from Registry (which uses `Person`/`people`), the ugsys name is `User`/`users` in identity-manager and `UserProfile`/`profiles` in user-profile-service.

---

## 7. Testing Standards

### Structure
```
tests/
├── unit/           # Pure unit tests — no AWS, no network, no DB
│   ├── domain/     # Domain entity and value object tests
│   ├── application/# Use case tests with mocked repositories
│   └── presentation/ # Router tests with mocked services
└── integration/    # moto-based DynamoDB tests — real AWS SDK, fake AWS
```

### AAA Pattern (mandatory)
```python
async def test_register_user_raises_conflict_when_email_exists() -> None:
    # Arrange
    repo = AsyncMock(spec=UserRepository)
    repo.find_by_email.return_value = existing_user_fixture()
    service = RegisterUserService(repo)
    command = RegisterUserCommand(email="test@example.com", password="Str0ng!Pass")

    # Act + Assert
    with pytest.raises(ConflictError) as exc_info:
        await service.execute(command)

    assert exc_info.value.error_code == "EMAIL_ALREADY_EXISTS"
    assert "already in use" in exc_info.value.user_message  # safe message
    assert "test@example.com" not in exc_info.value.user_message  # no PII in user_message
```

### Coverage gate
- Unit tests: **80% minimum** (CI blocks merge below this)
- Target: **90%+** for domain and application layers
- Integration tests: not counted toward coverage gate (they're slow, run separately)

### Rules
- Mock at the port boundary — mock `UserRepository`, not `boto3`
- Test the domain exception type, not the HTTP status code (that's the handler's job)
- Test `user_message` does NOT contain internal details or PII
- One `pytest.raises` per test — don't test multiple failure modes in one test

---

## 8. Performance Logging (mandatory at service boundaries)

```python
import time
import structlog

logger = structlog.get_logger()

async def execute(self, cmd: RegisterUserCommand) -> User:
    logger.info("register_user.started", email=cmd.email)
    start = time.perf_counter()
    try:
        user = await self._do_register(cmd)
        logger.info(
            "register_user.completed",
            user_id=str(user.id),
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
        )
        return user
    except DomainError:
        raise  # already logged by exception handler
    except Exception as e:
        logger.error(
            "register_user.failed",
            error=str(e),
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
            exc_info=True,
        )
        raise
```

Log `duration_ms` on every application service method. This feeds CloudWatch Logs Insights slow-operation queries.

---

## Summary Checklist (before every PR)

```
□ No duplicate logic — searched src/ and shared-libs first
□ Domain exceptions used — no raw Exception/ValueError/HTTPException from app/domain
□ user_message is safe — no internal details, no PII, no stack traces
□ create_app() factory pattern — configure_logging() called first
□ Each router handles ONE domain — no cross-domain service injection
□ Ubiquitous language consistent — entity names match the domain table above
□ Tests follow AAA — Arrange / Act / Assert
□ Tests verify user_message safety — no PII or internals in client-facing message
□ duration_ms logged on all application service methods
□ 80%+ unit test coverage
```
