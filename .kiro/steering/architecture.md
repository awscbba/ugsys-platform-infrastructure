---
inclusion: always
---

# Architecture Principles

## Canonical Layer Structure (ALL services MUST follow this)

This is the reference from `devsecops-poc` and the `MICROSERVICES_DECOUPLING_PROPOSAL_EN.md`.
Every `ugsys-*` service MUST use this exact structure — no exceptions.

```
src/
├── presentation/              # HTTP boundary — routers, middleware, DTOs
│   ├── api/
│   │   └── v1/                # Always versioned under /api/v1/
│   │       ├── auth.py
│   │       ├── users.py
│   │       └── health.py
│   └── middleware/
│       ├── correlation_id.py  # X-Request-ID propagation
│       ├── security_headers.py
│       └── rate_limiting.py
├── application/               # Use cases, orchestration — NO infra imports
│   ├── services/              # Application services (orchestrate domain + ports)
│   ├── commands/              # Write operations (CQRS)
│   ├── queries/               # Read operations (CQRS)
│   ├── dtos/                  # Request/response data transfer objects
│   └── interfaces/            # Inbound port interfaces (use case contracts)
├── domain/                    # Pure business logic — ZERO external dependencies
│   ├── entities/              # Aggregate roots, pure Python dataclasses
│   ├── value_objects/         # Immutable value types
│   └── repositories/          # Outbound port interfaces (abstract base classes)
└── infrastructure/            # Concrete implementations — all external I/O lives here
    ├── persistence/           # DynamoDB / PostgreSQL adapters
    ├── adapters/              # HTTP clients, S3, SES, SNS, etc.
    ├── messaging/             # EventBridge publisher/subscriber
    └── logging.py             # structlog configuration
```

Plus at root of `src/`:
```
src/
├── config.py                  # pydantic-settings Settings class
└── main.py                    # Composition root — wires all dependencies, lifespan
```

## Layer Dependency Rules (enforced by arch-guard in CI)

```
presentation  →  application  →  domain
infrastructure  →  domain
infrastructure  →  application (implements interfaces)
```

- `domain/` has ZERO imports from any other layer
- `application/` imports ONLY from `domain/` — never from `infrastructure/` or `presentation/`
- `infrastructure/` implements interfaces defined in `domain/repositories/` or `application/interfaces/`
- `presentation/` calls `application/services/` — never calls `infrastructure/` directly

## What the arch-guard CI job checks
```bash
# domain must not import infra or presentation or application
grep -rn "from src.infrastructure\|from src.presentation\|from src.application" src/domain/

# application must not import infra or presentation
grep -rn "from src.infrastructure\|from src.presentation" src/application/
```

## Naming Conventions

| Layer | Suffix | Example |
|-------|--------|---------|
| Domain entity | none | `User`, `Project`, `Campaign` |
| Domain value object | none | `Email`, `UserId`, `CampaignStatus` |
| Domain repository interface | `Repository` | `UserRepository` (ABC) |
| Application service | `Service` | `AuthService`, `RegisterUserService` |
| Application command | `Command` | `RegisterUserCommand` |
| Application query | `Query` | `GetUserQuery` |
| Application DTO | `DTO` or `Request`/`Response` | `RegisterUserRequest` |
| Infrastructure adapter | concrete name | `DynamoDBUserRepository`, `SESEmailAdapter` |
| Presentation router file | domain name | `auth.py`, `users.py` |

## Dependency Injection

- All dependencies injected via constructor — no global singletons
- `main.py` is the composition root — it wires everything
- FastAPI `Depends()` used only in `presentation/` layer
- Use `Protocol` or `ABC` for port interfaces

```python
# ✅ Correct — domain port as ABC
from abc import ABC, abstractmethod

class UserRepository(ABC):
    @abstractmethod
    async def find_by_email(self, email: str) -> User | None: ...

    @abstractmethod
    async def save(self, user: User) -> User: ...

# ✅ Correct — infrastructure implements the port
class DynamoDBUserRepository(UserRepository):
    def __init__(self, table_name: str, client: Any) -> None:
        self._table_name = table_name
        self._client = client
```

## API Versioning

All routes MUST be under `/api/v1/`. No unversioned routes except `/health` and `/`.

```python
app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
```

## Config Pattern

Every service MUST have `src/config.py` using `pydantic-settings`:

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    service_name: str = "ugsys-<service>"
    environment: str = "dev"
    aws_region: str = "us-east-1"
    dynamodb_table_prefix: str = "ugsys"
    event_bus_name: str = "ugsys-event-bus"
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()
```

## Lifespan Pattern

Every service MUST use FastAPI lifespan for startup/shutdown:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting", service=settings.service_name)
    yield
    logger.info("Shutdown complete")

app = FastAPI(lifespan=lifespan)
```

## Anti-Patterns — NEVER DO THESE

- ❌ Layer named `api/` instead of `presentation/`
- ❌ Ports in `domain/ports/` — they belong in `domain/repositories/` (outbound) or `application/interfaces/` (inbound)
- ❌ Direct boto3 calls in application or domain layers
- ❌ Unversioned API routes (e.g. `/auth/login` instead of `/api/v1/auth/login`)
- ❌ Missing `src/config.py`
- ❌ Missing correlation ID middleware
- ❌ Global service singletons
- ❌ Domain entities with DynamoDB/SQLAlchemy annotations
