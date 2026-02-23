---
inclusion: always
---

# Enterprise Logging Standards

## Rule: ALL services use structlog — no print(), no logging.getLogger()

## Configuration (in `src/infrastructure/logging.py`)

```python
import structlog

def configure_logging(service_name: str, log_level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
```

Call `configure_logging(settings.service_name)` at the top of `src/main.py` before anything else.

## Log Format (JSON, CloudWatch-ready)

```json
{
  "event": "User registered",
  "timestamp": "2026-02-23T14:00:00.000Z",
  "level": "info",
  "service": "ugsys-identity-manager",
  "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "user_id": "usr-123",
  "duration_ms": 45.2
}
```

## Correlation ID (REQUIRED in every service)

Every service MUST have `src/presentation/middleware/correlation_id.py`:

```python
import structlog
from contextvars import ContextVar
from uuid import uuid4
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from collections.abc import Callable, Awaitable

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")

class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        correlation_id_var.set(request_id)
        with structlog.contextvars.bound_contextvars(correlation_id=request_id):
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
```

## Logging Patterns

### Always log at boundaries with context
```python
logger = structlog.get_logger()

async def execute(self, command: RegisterUserCommand) -> User:
    logger.info("register_user.started", email=command.email)
    try:
        user = await self._do_register(command)
        logger.info("register_user.completed", user_id=str(user.id))
        return user
    except ValueError as e:
        logger.warning("register_user.validation_failed", error=str(e))
        raise
    except Exception as e:
        logger.error("register_user.failed", error=str(e), exc_info=True)
        raise
```

### Log levels
| Level | When |
|-------|------|
| `debug` | Detailed diagnostics (disabled in prod) |
| `info` | Normal business events, boundaries |
| `warning` | Expected errors, validation failures |
| `error` | Unexpected errors needing attention |
| `critical` | System failures, immediate action needed |

### Performance logging
```python
import time
start = time.perf_counter()
result = await operation()
logger.info("operation.completed", duration_ms=round((time.perf_counter() - start) * 1000, 2))
```

## NEVER log sensitive data
```python
# ❌ NEVER
logger.info("login", password=password, token=token)

# ✅ ALWAYS
logger.info("login", user_id=user_id, ip=request.client.host)
logger.info("token_issued", user_id=user_id, expires_in=3600)
```

## CloudWatch Logs Insights queries
```sql
-- Trace a request across services
fields @timestamp, service, event, user_id
| filter correlation_id = "your-id"
| sort @timestamp asc

-- Error rate by service
fields service, level
| filter level = "error"
| stats count() by service, bin(1h)

-- Slow operations
fields @timestamp, service, event, duration_ms
| filter duration_ms > 500
| sort duration_ms desc
```
