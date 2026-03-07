---
inclusion: always
---

# Enterprise Code Quality Patterns

These rules apply to ALL `ugsys-*` services without exception.
They complement `architecture.md` (layer structure) and `logging.md` (structlog).

> **Sections 1–9** are mandatory for every service. **Sections 10–13** (Outbox, Unit of Work, Circuit Breaker, Query Object) are enterprise patterns that should be adopted when the service's requirements call for them — not blindly applied. Evaluate each pattern against your service's specific needs: if you have dual-write scenarios, use the Outbox; if you call external services, use the Circuit Breaker; if you have multi-aggregate writes, use the Unit of Work; if your list endpoints have growing filter parameters, use the Query Object.

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

## 9. Test-Driven Development (mandatory workflow)

**Tests come FIRST. Code comes second.** Every feature follows the Red → Green → Refactor cycle:

### The TDD Cycle

```
1. RED    — Write a failing test that defines the expected behavior
2. GREEN  — Write the MINIMUM code to make the test pass
3. REFACTOR — Clean up the code while keeping tests green
4. REPEAT — Next small behavior, next test
```

### Workflow per feature unit

```
# Step 1: Write the test FIRST (it will fail — that's correct)
→ tests/unit/domain/test_user.py::test_user_creation_sets_defaults

# Step 2: Run it — confirm it fails (RED)
→ uv run pytest tests/unit/domain/test_user.py -v

# Step 3: Write ONLY enough code to pass (GREEN)
→ src/domain/entities/user.py

# Step 4: Run it — confirm it passes (GREEN)
→ uv run pytest tests/unit/domain/test_user.py -v

# Step 5: Refactor if needed, run tests again
→ uv run pytest tests/unit/ -v

# Step 6: Next behavior — back to Step 1
```

### What "one unit" means — test each in isolation

| Layer | One unit = | Test file |
|-------|-----------|-----------|
| Domain | One entity behavior, one value object rule | `tests/unit/domain/` |
| Application | One service method, one use case | `tests/unit/application/` |
| Presentation | One endpoint, one middleware behavior | `tests/unit/presentation/` |
| Infrastructure | One repository method (integration) | `tests/integration/` |

### TDD example — domain entity

```python
# STEP 1: Write test FIRST
# tests/unit/domain/test_user.py
def test_user_creation_sets_active_status() -> None:
    user = User(email="dev@example.com", full_name="Test User")
    assert user.is_active is True
    assert user.created_at is not None

# STEP 2: Run → RED (User doesn't exist yet)
# STEP 3: Implement ONLY what the test needs
# src/domain/entities/user.py
@dataclass
class User:
    email: str
    full_name: str
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)

# STEP 4: Run → GREEN
```

### TDD example — application service

```python
# STEP 1: Write test FIRST
# tests/unit/application/test_register_user.py
async def test_register_user_saves_and_returns_user() -> None:
    # Arrange
    repo = AsyncMock(spec=UserRepository)
    repo.find_by_email.return_value = None
    repo.save.return_value = make_user(email="dev@example.com")
    service = RegisterUserService(repo)

    # Act
    result = await service.execute(RegisterUserCommand(email="dev@example.com", full_name="Dev"))

    # Assert
    repo.save.assert_called_once()
    assert result.email == "dev@example.com"

# STEP 2: Run → RED (RegisterUserService doesn't exist yet)
# STEP 3: Implement ONLY the happy path
# STEP 4: Run → GREEN
# STEP 5: Next test — what if email already exists? → ConflictError test → implement
```

### Mandatory rules

- **NEVER write implementation code without a failing test first**
- **NEVER write more than ONE test before making it pass** — one RED at a time
- **NEVER skip running tests** — run after every change, no exceptions
- **NEVER implement multiple behaviors at once** — one test, one behavior, one cycle
- **Fix failures immediately** — if a test breaks, stop and fix it before writing anything else

### Anti-patterns

- ❌ Writing 5 files then running tests for the first time
- ❌ Implementing all endpoints before testing any
- ❌ "I'll add tests later" — the test MUST exist before the code
- ❌ Writing a test that passes immediately — if it's green on first run, you wrote the code first
- ❌ Skipping the RED step — seeing the test fail confirms it actually tests something

### When to run tests

| After... | Run |
|----------|-----|
| Writing a new test | `uv run pytest path/to/test_file.py::test_name -v` (expect RED) |
| Writing implementation | `uv run pytest path/to/test_file.py::test_name -v` (expect GREEN) |
| Refactoring | `uv run pytest tests/unit/ -v` (all must stay GREEN) |
| Before commit | `uv run pytest tests/unit/ -v --tb=short` (full suite) |


---

## 10. Outbox Pattern (reliable event delivery)

The Outbox Pattern solves the dual-write problem: when a service must persist state AND publish an event, doing both as separate I/O calls risks partial failure. If the DynamoDB write succeeds but EventBridge publish fails, the system is inconsistent.

Instead of two separate calls, write the domain event to an outbox table in the SAME DynamoDB transaction as the business write. A separate delivery process reads the outbox and publishes to EventBridge.

### When to use vs log-and-continue

| Scenario | Approach |
|----------|----------|
| Event loss is acceptable (analytics, notifications) | Log-and-continue |
| Event loss causes data inconsistency (subscription approved + participant count) | Outbox |
| Event triggers financial or compliance actions | Outbox |
| Event is consumed by another service to create/update its own state | Outbox |

### Outbox table schema

```
Table: ugsys-outbox-{service}-{env}
PK: OUTBOX#{ulid}
SK: EVENT

Attributes:
  id: S (ULID)
  aggregate_type: S (e.g., "Project", "Subscription")
  aggregate_id: S (entity ID)
  event_type: S (e.g., "projects.subscription.approved")
  payload: S (JSON-serialized event body)
  created_at: S (ISO 8601)
  published_at: S | None (set after successful delivery)
  retry_count: N (default 0)
  status: S ("pending" | "published" | "failed")

GSI: StatusIndex
  PK: status
  SK: created_at
```

### Transactional write (application service)

```python
# src/application/services/subscription_service.py
async def approve_subscription(self, subscription_id: str, admin_id: str) -> Subscription:
    subscription = await self._subscription_repo.find_by_id(subscription_id)
    if subscription is None:
        raise NotFoundError(message=f"Subscription {subscription_id} not found", user_message="Subscription not found")

    subscription.approve(approved_by=admin_id)

    # Single DynamoDB TransactWriteItems — both succeed or both fail
    await self._unit_of_work.execute([
        self._subscription_repo.save_operation(subscription),
        self._outbox_repo.save_operation(OutboxEvent(
            aggregate_type="Subscription",
            aggregate_id=str(subscription.id),
            event_type="projects.subscription.approved",
            payload=subscription.to_event_payload(),
        )),
    ])
    return subscription
```

### Outbox delivery process

The delivery process runs on a schedule (EventBridge Scheduler → Lambda, every 1 minute):

```python
# src/infrastructure/messaging/outbox_processor.py
import structlog
from src.domain.repositories.outbox_repository import OutboxRepository
from src.domain.repositories.event_publisher import EventPublisher

logger = structlog.get_logger()

class OutboxProcessor:
    def __init__(self, outbox_repo: OutboxRepository, publisher: EventPublisher) -> None:
        self._outbox_repo = outbox_repo
        self._publisher = publisher

    async def process_pending(self, batch_size: int = 25) -> int:
        events = await self._outbox_repo.find_pending(limit=batch_size)
        published = 0
        for event in events:
            try:
                await self._publisher.publish(event.event_type, event.payload_dict)
                await self._outbox_repo.mark_published(event.id)
                published += 1
            except Exception as e:
                logger.error("outbox.delivery_failed", event_id=str(event.id), error=str(e))
                await self._outbox_repo.increment_retry(event.id)
        return published
```

### Rules

- Outbox events older than 7 days with status `published` → delete (DynamoDB TTL or scheduled cleanup)
- Events with `retry_count >= 5` → set status to `failed`, alert via CloudWatch alarm
- Outbox processor is idempotent — re-publishing the same event is safe (consumers must be idempotent too)
- Never bypass the outbox by publishing directly when the outbox pattern is in use for that aggregate


---

## 11. Unit of Work (transactional consistency)

The Unit of Work pattern groups multiple repository operations into a single atomic transaction. DynamoDB `TransactWriteItems` supports up to 100 operations in one atomic batch.

### When to use

- Approving a subscription AND incrementing the project's participant count
- Creating a form submission AND updating the project's submission count
- Any operation that modifies more than one aggregate and must be all-or-nothing

### Port definition (domain layer)

```python
# src/domain/repositories/unit_of_work.py
from abc import ABC, abstractmethod
from typing import Any

class TransactionalOperation:
    """Represents a single write operation within a transaction."""
    def __init__(self, operation_type: str, params: dict[str, Any]) -> None:
        self.operation_type = operation_type  # "Put", "Update", "Delete"
        self.params = params

class UnitOfWork(ABC):
    @abstractmethod
    async def execute(self, operations: list[TransactionalOperation]) -> None:
        """Execute all operations atomically. All succeed or all fail."""
        ...
```

### Infrastructure implementation

```python
# src/infrastructure/persistence/dynamodb_unit_of_work.py
import structlog
from typing import Any
from botocore.exceptions import ClientError
from src.domain.repositories.unit_of_work import UnitOfWork, TransactionalOperation
from src.domain.exceptions import RepositoryError

logger = structlog.get_logger()

class DynamoDBUnitOfWork(UnitOfWork):
    def __init__(self, client: Any) -> None:
        self._client = client

    async def execute(self, operations: list[TransactionalOperation]) -> None:
        if not operations:
            return
        if len(operations) > 100:
            raise RepositoryError(
                message=f"Transaction exceeds DynamoDB limit: {len(operations)} operations",
                user_message="An unexpected error occurred",
            )

        transact_items = []
        for op in operations:
            transact_items.append({op.operation_type: op.params})

        try:
            await self._client.transact_write_items(TransactItems=transact_items)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            logger.error(
                "unit_of_work.transaction_failed",
                error_code=code,
                operation_count=len(operations),
                error=str(e),
            )
            if code == "TransactionCanceledException":
                reasons = e.response.get("CancellationReasons", [])
                logger.error("unit_of_work.cancellation_reasons", reasons=reasons)
            raise RepositoryError(
                message=f"DynamoDB transaction failed: {e}",
                user_message="An unexpected error occurred",
            )
```

### Repository integration

Each repository exposes `*_operation()` methods that return `TransactionalOperation` instead of executing directly:

```python
# src/infrastructure/persistence/dynamodb_subscription_repository.py
class DynamoDBSubscriptionRepository(SubscriptionRepository):
    # ... existing methods ...

    def save_operation(self, subscription: Subscription) -> TransactionalOperation:
        """Return a transactional Put operation (does not execute)."""
        return TransactionalOperation(
            operation_type="Put",
            params={
                "TableName": self._table_name,
                "Item": self._to_item(subscription),
                "ConditionExpression": "attribute_not_exists(PK)",
            },
        )

    def update_operation(self, subscription: Subscription) -> TransactionalOperation:
        """Return a transactional Put operation for update (does not execute)."""
        return TransactionalOperation(
            operation_type="Put",
            params={
                "TableName": self._table_name,
                "Item": self._to_item(subscription),
                "ConditionExpression": "attribute_exists(PK)",
            },
        )
```

### Rules

- Unit of Work is wired in `main.py` alongside repositories — same DynamoDB client
- Never mix transactional and non-transactional writes for the same aggregate in the same use case
- DynamoDB transactions have a 100-operation limit — if you need more, redesign the aggregate boundaries
- All items in a transaction must be in the same AWS region (DynamoDB limitation)
- Unit of Work + Outbox Pattern combine naturally: the outbox write is just another operation in the transaction


---

## 12. Circuit Breaker (external service resilience)

The Circuit Breaker pattern wraps calls to external services and fast-fails after repeated failures, preventing cascade failures and giving the downstream service time to recover.

### State machine

```
CLOSED (normal) ──[N consecutive failures]──→ OPEN (fast-fail)
                                                  │
                                          [cooldown expires]
                                                  │
                                                  ▼
                                          HALF-OPEN (probe)
                                                  │
                                    ┌─────────────┴─────────────┐
                              [probe succeeds]            [probe fails]
                                    │                           │
                                    ▼                           ▼
                              CLOSED                        OPEN
```

### Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `failure_threshold` | 5 | Consecutive failures before opening |
| `cooldown_seconds` | 30 | Time in OPEN state before probing |
| `half_open_max_calls` | 1 | Probe calls allowed in HALF-OPEN |

### Port definition (domain layer)

```python
# src/domain/repositories/circuit_breaker.py
from abc import ABC, abstractmethod
from enum import Enum

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker(ABC):
    @abstractmethod
    def state(self) -> CircuitState: ...

    @abstractmethod
    def record_success(self) -> None: ...

    @abstractmethod
    def record_failure(self) -> None: ...

    @abstractmethod
    def allow_request(self) -> bool: ...
```

### Infrastructure implementation

```python
# src/infrastructure/adapters/in_memory_circuit_breaker.py
import time
import structlog
from src.domain.repositories.circuit_breaker import CircuitBreaker, CircuitState

logger = structlog.get_logger()

class InMemoryCircuitBreaker(CircuitBreaker):
    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 5,
        cooldown_seconds: int = 30,
    ) -> None:
        self._service_name = service_name
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0

    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self._cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                logger.info("circuit_breaker.half_open", service=self._service_name)
        return self._state

    def allow_request(self) -> bool:
        current = self.state()
        return current in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            logger.info("circuit_breaker.closed", service=self._service_name)
        self._state = CircuitState.CLOSED
        self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "circuit_breaker.opened",
                service=self._service_name,
                failure_count=self._failure_count,
                cooldown_seconds=self._cooldown_seconds,
            )
```

### Usage in adapters

```python
# src/infrastructure/adapters/identity_manager_client.py
import structlog
from src.domain.repositories.identity_client import IdentityClient
from src.domain.repositories.circuit_breaker import CircuitBreaker
from src.domain.exceptions import ExternalServiceError

logger = structlog.get_logger()

class IdentityManagerClient(IdentityClient):
    def __init__(self, base_url: str, circuit_breaker: CircuitBreaker, http_client: Any) -> None:
        self._base_url = base_url
        self._cb = circuit_breaker
        self._http_client = http_client

    async def check_email_exists(self, email: str) -> bool:
        if not self._cb.allow_request():
            logger.warning("identity_client.circuit_open", operation="check_email_exists")
            raise ExternalServiceError(
                message="Identity Manager circuit breaker is open",
                user_message="Service temporarily unavailable, please try again later",
                error_code="SERVICE_UNAVAILABLE",
            )
        try:
            response = await self._http_client.get(f"{self._base_url}/api/v1/users/by-email/{email}")
            self._cb.record_success()
            return response.status_code == 200
        except Exception as e:
            self._cb.record_failure()
            raise ExternalServiceError(
                message=f"Identity Manager call failed: {e}",
                user_message="Service temporarily unavailable, please try again later",
                error_code="EXTERNAL_SERVICE_ERROR",
            )
```

### Rules

- One circuit breaker instance per external service — wired in `main.py`
- Circuit breaker is in-memory (Lambda cold starts reset it — acceptable for serverless)
- Log every state transition (`closed → open`, `open → half_open`, `half_open → closed`)
- Never use circuit breaker for DynamoDB calls — those are handled by repository error wrapping
- Services that use circuit breaker: `IdentityManagerClient`, `EventBridgePublisher` (when not using outbox)


---

## 13. Specification / Query Object (composable filters)

The Specification pattern encapsulates query criteria as first-class objects. This prevents `list_paginated()` from growing unbounded parameters as new filter combinations appear.

### The problem

```python
# ❌ NEVER — parameter explosion
async def list_paginated(
    self, page: int, page_size: int,
    status_filter: str | None = None,
    category_filter: str | None = None,
    owner_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    search_term: str | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
) -> tuple[list[Project], int]: ...
```

### The solution — query object

```python
# src/application/queries/project_queries.py
from dataclasses import dataclass, field

@dataclass(frozen=True)
class ProjectListQuery:
    """Encapsulates all filter/sort/pagination criteria for project listing."""
    page: int = 1
    page_size: int = 20
    status: str | None = None
    category: str | None = None
    owner_id: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    search_term: str | None = None
    sort_by: str = "created_at"
    sort_order: str = "desc"
    tags: list[str] = field(default_factory=list)

    def has_filters(self) -> bool:
        return any([
            self.status, self.category, self.owner_id,
            self.date_from, self.date_to, self.search_term, self.tags,
        ])
```


### Repository port uses the query object

```python
# src/domain/repositories/project_repository.py
from abc import ABC, abstractmethod
from src.domain.entities.project import Project

class ProjectRepository(ABC):
    @abstractmethod
    async def save(self, project: Project) -> Project: ...

    @abstractmethod
    async def find_by_id(self, project_id: str) -> Project | None: ...

    @abstractmethod
    async def list_by_query(self, query: "ProjectListQuery") -> tuple[list[Project], int]:
        """List projects matching the query criteria with total count."""
        ...
```

### Infrastructure implementation

```python
# src/infrastructure/persistence/dynamodb_project_repository.py
class DynamoDBProjectRepository(ProjectRepository):
    async def list_by_query(self, query: ProjectListQuery) -> tuple[list[Project], int]:
        try:
            if query.status:
                # Use GSI StatusIndex for status-based queries
                response = await self._client.query(
                    TableName=self._table_name,
                    IndexName="StatusIndex",
                    KeyConditionExpression="GSI1PK = :status",
                    ExpressionAttributeValues={":status": {"S": f"STATUS#{query.status}"}},
                )
            else:
                # Scan with filter expressions for complex queries
                filter_parts, expr_values = self._build_filter_expression(query)
                scan_params = {"TableName": self._table_name}
                if filter_parts:
                    scan_params["FilterExpression"] = " AND ".join(filter_parts)
                    scan_params["ExpressionAttributeValues"] = expr_values
                response = await self._client.scan(**scan_params)

            items = response.get("Items", [])
            projects = [self._from_item(item) for item in items]

            # Apply in-memory sorting and pagination
            projects.sort(
                key=lambda p: getattr(p, query.sort_by, p.created_at),
                reverse=(query.sort_order == "desc"),
            )
            total = len(projects)
            start = (query.page - 1) * query.page_size
            page_items = projects[start : start + query.page_size]
            return page_items, total
        except ClientError as e:
            self._raise_repository_error("list_by_query", e)

    def _build_filter_expression(self, query: ProjectListQuery) -> tuple[list[str], dict]:
        parts: list[str] = []
        values: dict = {}
        if query.category:
            parts.append("category = :cat")
            values[":cat"] = {"S": query.category}
        if query.owner_id:
            parts.append("owner_id = :owner")
            values[":owner"] = {"S": query.owner_id}
        if query.date_from:
            parts.append("created_at >= :date_from")
            values[":date_from"] = {"S": query.date_from}
        if query.date_to:
            parts.append("created_at <= :date_to")
            values[":date_to"] = {"S": query.date_to}
        return parts, values
```


### Presentation layer — query params to query object

```python
# src/presentation/api/v1/projects.py
from fastapi import APIRouter, Depends, Query
from src.application.queries.project_queries import ProjectListQuery

router = APIRouter(prefix="/projects", tags=["Projects"])

@router.get("/")
async def list_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    category: str | None = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    service: ProjectService = Depends(get_project_service),
) -> PaginatedResponse[ProjectResponse]:
    query = ProjectListQuery(
        page=page, page_size=page_size,
        status=status, category=category,
        sort_by=sort_by, sort_order=sort_order,
    )
    projects, total = await service.list_projects(query)
    return PaginatedResponse.build(items=projects, total=total, query=query)
```

### Rules

- One query object per aggregate list operation — `ProjectListQuery`, `SubscriptionListQuery`, etc.
- Query objects are frozen dataclasses — immutable after creation
- Query objects live in `src/application/queries/` — they are application-layer concerns
- Repository port accepts the query object — infrastructure decides how to translate it to DynamoDB operations
- Adding a new filter = add a field to the query object + update `_build_filter_expression()` — no signature changes anywhere else
- Admin endpoints and public endpoints can use the same query object with different defaults

---

## Summary Checklist (before every PR)

```
□ No duplicate logic — searched src/ and shared-libs first
□ Domain exceptions used — no raw Exception/ValueError/HTTPException from app/domain
□ user_message is safe — no internal details, no PII, no stack traces
□ create_app() factory pattern — configure_logging() called first
□ Each router handles ONE domain — no cross-domain service injection
□ Ubiquitous language consistent — entity names match the domain table above
□ TDD workflow followed — every implementation has a test that was written FIRST
□ Tests follow AAA — Arrange / Act / Assert
□ Tests verify user_message safety — no PII or internals in client-facing message
□ duration_ms logged on all application service methods
□ 80%+ unit test coverage
□ Outbox pattern used for events that must not be lost (dual-write scenarios)
□ Unit of Work used for multi-aggregate atomic operations
□ Circuit breaker wraps all external service calls (IdentityManagerClient, EventBridgePublisher)
□ Query objects used for list endpoints — no parameter explosion on repository methods
```
