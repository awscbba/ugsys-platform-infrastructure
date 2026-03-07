---
inclusion: always
---

# Repository Pattern — Enterprise Standard

Every `ugsys-*` service MUST implement the repository pattern as described here.
This applies to ALL services — identity-manager, user-profile-service, projects-registry, and all future services.

---

## 1. Port Definition (Domain Layer)

All outbound port interfaces live in `src/domain/repositories/` as abstract base classes.
The domain layer has ZERO knowledge of DynamoDB, boto3, or any infrastructure detail.

```python
# src/domain/repositories/user_repository.py
from abc import ABC, abstractmethod
from src.domain.entities.user import User

class UserRepository(ABC):
    @abstractmethod
    async def save(self, user: User) -> User: ...

    @abstractmethod
    async def find_by_id(self, user_id: str) -> User | None: ...

    @abstractmethod
    async def find_by_email(self, email: str) -> User | None: ...

    @abstractmethod
    async def update(self, user: User) -> User: ...

    @abstractmethod
    async def delete(self, user_id: str) -> None: ...

    @abstractmethod
    async def list_paginated(
        self, page: int, page_size: int,
        status_filter: str | None = None,
        role_filter: str | None = None,
    ) -> tuple[list[User], int]: ...
```

Rules:
- One ABC per aggregate root — `UserRepository`, `ProjectRepository`, `SubscriptionRepository`, etc.
- File name matches class name in snake_case: `user_repository.py`, `project_repository.py`
- Methods are `async` — all I/O is async
- Return types use domain entities only — never DynamoDB dicts or boto3 types
- `list_*` methods return `tuple[list[Entity], int]` where `int` is total count for pagination


---

## 2. External Service Ports (Domain Layer)

Non-persistence outbound ports also live in `src/domain/repositories/`:

```python
# src/domain/repositories/event_publisher.py
from abc import ABC, abstractmethod
from typing import Any

class EventPublisher(ABC):
    @abstractmethod
    async def publish(self, detail_type: str, payload: dict[str, Any]) -> None: ...

# src/domain/repositories/identity_client.py
from abc import ABC, abstractmethod

class IdentityClient(ABC):
    @abstractmethod
    async def check_email_exists(self, email: str) -> bool: ...

    @abstractmethod
    async def create_user(self, email: str, full_name: str, password: str) -> str: ...
    # returns user_id
```


---

## 3. Concrete Implementation (Infrastructure Layer)

All concrete implementations live in `src/infrastructure/persistence/` (DynamoDB) or `src/infrastructure/adapters/` (HTTP clients) or `src/infrastructure/messaging/` (EventBridge).

### 3.1 DynamoDB Repository Structure

```python
# src/infrastructure/persistence/dynamodb_user_repository.py
import structlog
from typing import Any
from botocore.exceptions import ClientError
from src.domain.entities.user import User
from src.domain.repositories.user_repository import UserRepository
from src.domain.exceptions import RepositoryError, NotFoundError

logger = structlog.get_logger()


class DynamoDBUserRepository(UserRepository):
    def __init__(self, table_name: str, client: Any) -> None:
        self._table_name = table_name
        self._client = client

    # ── Public interface ──────────────────────────────────────────────────────

    async def save(self, user: User) -> User:
        try:
            item = self._to_item(user)
            await self._client.put_item(
                TableName=self._table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(PK)",
            )
            return user
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ConditionalCheckFailedException":
                raise RepositoryError(
                    message=f"User {user.id} already exists: {e}",
                    user_message="An unexpected error occurred",
                    error_code="REPOSITORY_ERROR",
                )
            self._raise_repository_error("save", e)

    async def find_by_id(self, user_id: str) -> User | None:
        try:
            response = await self._client.get_item(
                TableName=self._table_name,
                Key={"PK": {"S": f"USER#{user_id}"}, "SK": {"S": "USER"}},
            )
            item = response.get("Item")
            return self._from_item(item) if item else None
        except ClientError as e:
            self._raise_repository_error("find_by_id", e)

    async def update(self, user: User) -> User:
        try:
            item = self._to_item(user)
            await self._client.put_item(
                TableName=self._table_name,
                Item=item,
                ConditionExpression="attribute_exists(PK)",
            )
            return user
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ConditionalCheckFailedException":
                raise NotFoundError(
                    message=f"User {user.id} not found for update",
                    user_message="User not found",
                    error_code="NOT_FOUND",
                )
            self._raise_repository_error("update", e)

    async def delete(self, user_id: str) -> None:
        try:
            await self._client.delete_item(
                TableName=self._table_name,
                Key={"PK": {"S": f"USER#{user_id}"}, "SK": {"S": "USER"}},
            )
        except ClientError as e:
            self._raise_repository_error("delete", e)

    # ── Serialization ─────────────────────────────────────────────────────────

    def _to_item(self, user: User) -> dict[str, Any]:
        """Convert domain entity to DynamoDB item. All fields explicit."""
        item: dict[str, Any] = {
            "PK": {"S": f"USER#{user.id}"},
            "SK": {"S": "USER"},
            "id": {"S": user.id},
            "email": {"S": user.email},
            "full_name": {"S": user.full_name},
            "is_active": {"BOOL": user.is_active},
            "created_at": {"S": user.created_at},
            "updated_at": {"S": user.updated_at},
        }
        # Optional fields — only write if present
        if user.role is not None:
            item["role"] = {"S": user.role}
        return item

    def _from_item(self, item: dict[str, Any]) -> User:
        """Convert DynamoDB item to domain entity. Backward-compatible defaults."""
        return User(
            id=item["id"]["S"],
            email=item["email"]["S"],
            full_name=item["full_name"]["S"],
            is_active=item.get("is_active", {}).get("BOOL", True),  # default True
            role=item.get("role", {}).get("S"),                      # default None
            created_at=item["created_at"]["S"],
            updated_at=item["updated_at"]["S"],
        )

    # ── Error handling ────────────────────────────────────────────────────────

    def _raise_repository_error(self, operation: str, e: ClientError) -> None:
        """Log full ClientError internally, raise safe RepositoryError to callers."""
        logger.error(
            "dynamodb.error",
            operation=operation,
            table=self._table_name,
            error_code=e.response["Error"]["Code"],
            error=str(e),
        )
        raise RepositoryError(
            message=f"DynamoDB {operation} failed on {self._table_name}: {e}",
            user_message="An unexpected error occurred",
            error_code="REPOSITORY_ERROR",
        )
```


### 3.2 Serialization Rules

- `_to_item(entity) -> dict` — domain entity → DynamoDB AttributeValue dict
- `_from_item(item) -> entity` — DynamoDB item → domain entity
- Both methods are private (`_`) — never called from outside the repository
- `_from_item` MUST use `.get()` with safe defaults for all optional/new fields (backward compatibility)
- `_to_item` MUST only write optional fields when they are not `None` (avoid storing null attributes)
- Never store Python `None` as `{"NULL": True}` — omit the attribute entirely

### 3.3 ClientError Wrapping Rules

Every `boto3` / `aioboto3` call MUST be wrapped in `try/except ClientError`:

```python
# ❌ NEVER — lets boto3 exceptions leak to application layer
async def find_by_id(self, user_id: str) -> User | None:
    response = await self._client.get_item(...)
    return self._from_item(response["Item"])

# ✅ ALWAYS — catch ClientError, log internally, raise RepositoryError
async def find_by_id(self, user_id: str) -> User | None:
    try:
        response = await self._client.get_item(...)
        item = response.get("Item")
        return self._from_item(item) if item else None
    except ClientError as e:
        self._raise_repository_error("find_by_id", e)
```

Special `ClientError` codes to handle explicitly:
- `ConditionalCheckFailedException` on `save` → raise `RepositoryError` (duplicate)
- `ConditionalCheckFailedException` on `update` → raise `NotFoundError`
- All others → `_raise_repository_error()`


---

## 4. Dependency Injection

Repositories are wired in `src/main.py` `create_app()` — never instantiated elsewhere.

```python
# src/main.py — inside create_app() or lifespan
import aioboto3
from src.infrastructure.persistence.dynamodb_user_repository import DynamoDBUserRepository
from src.application.services.user_service import UserService

session = aioboto3.Session()

async with session.client("dynamodb", region_name=settings.aws_region) as dynamodb:
    user_repo = DynamoDBUserRepository(
        table_name=f"{settings.dynamodb_table_prefix}-users-{settings.environment}",
        client=dynamodb,
    )
    user_service = UserService(user_repo=user_repo)
```

FastAPI `Depends()` wires services into routers — never repositories directly:

```python
# src/presentation/dependencies.py
from fastapi import Depends
from src.application.services.user_service import UserService

def get_user_service() -> UserService:
    # resolved from app state set during lifespan
    return app.state.user_service
```


---

## 5. Unit Testing — Mock at the Port Boundary

Unit tests ALWAYS mock the ABC, never boto3 or DynamoDB directly.

```python
# tests/unit/application/test_register_user.py
from unittest.mock import AsyncMock
from src.domain.repositories.user_repository import UserRepository
from src.application.services.user_service import UserService
from src.domain.exceptions import ConflictError

async def test_register_raises_conflict_when_email_exists() -> None:
    # Arrange — mock the ABC, not boto3
    repo = AsyncMock(spec=UserRepository)
    repo.find_by_email.return_value = make_user()
    service = UserService(user_repo=repo)

    # Act + Assert
    with pytest.raises(ConflictError) as exc_info:
        await service.register(RegisterUserCommand(email="test@example.com", ...))

    assert exc_info.value.error_code == "EMAIL_ALREADY_EXISTS"
    assert "test@example.com" not in exc_info.value.user_message
```


---

## 6. Integration Testing — moto

Integration tests use `moto` to simulate DynamoDB — no real AWS calls.

```python
# tests/integration/test_dynamodb_user_repo.py
import pytest
import boto3
from moto import mock_aws
from src.infrastructure.persistence.dynamodb_user_repository import DynamoDBUserRepository

@pytest.fixture
def dynamodb_table():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="ugsys-users-test",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client

async def test_save_and_find_by_id_round_trip(dynamodb_table) -> None:
    repo = DynamoDBUserRepository(table_name="ugsys-users-test", client=dynamodb_table)
    user = make_user(id="01JXXX", email="dev@example.com")

    saved = await repo.save(user)
    found = await repo.find_by_id("01JXXX")

    assert found is not None
    assert found.email == "dev@example.com"
    assert found.id == saved.id
```

Integration test rules:
- Use `moto` `mock_aws` — never call real AWS in tests
- Create the table schema matching production (same GSIs, same key names)
- Test `_to_item` / `_from_item` round-trips for all entity fields
- Test backward compatibility: items missing new optional fields deserialize with safe defaults
- Test `ClientError` wrapping: verify `RepositoryError` is raised (not raw `ClientError`)


---

## 7. Repository Checklist (before every PR)

```
□ ABC defined in src/domain/repositories/ — no infrastructure imports
□ Concrete implementation in src/infrastructure/persistence/ or adapters/ or messaging/
□ Every boto3 call wrapped in try/except ClientError
□ _raise_repository_error() logs full detail internally, raises RepositoryError with safe user_message
□ _to_item / _from_item are private methods on the concrete class
□ _from_item uses .get() with safe defaults for all optional fields
□ Unit tests mock AsyncMock(spec=XxxRepository) — never mock boto3
□ Integration tests use moto mock_aws — no real AWS calls
□ Integration tests cover round-trip serialization of all entity fields
□ Repositories wired only in src/main.py create_app() — no global singletons
```