---
inclusion: always
---

# Project Goals & Ecosystem Overview

## What We Are Building

AWS User Group Cbba — a 5-microservice platform for the community at `cbba.cloud.org.bo`.

## The 6 Services

| Repo | Purpose | Status |
|------|---------|--------|
| `ugsys-identity-manager` | Centralized auth, users, RBAC | 🔨 Phase 1 — partial |
| `ugsys-user-profile-service` | User profiles, preferences, avatars | 🔨 Phase 1 — partial |
| `ugsys-projects-registry` | Project catalog, volunteer enrollment, dynamic forms | ⏳ Pending |
| `ugsys-omnichannel-service` | Multi-channel messaging (SES, SNS, WhatsApp, Slack, Telegram) | ⏳ Pending |
| `ugsys-mass-messaging` | Campaign orchestration, audiences, analytics | ⏳ Pending |
| `ugsys-admin-panel` | Plugin-based unified admin UI (React SPA + BFF proxy) | ⏳ Pending |

## Supporting Repos

| Repo | Purpose | Status |
|------|---------|--------|
| `ugsys-shared-libs` | auth-client, logging-lib, event-lib, testing-lib | ✅ Published v0.1.0 |
| `ugsys-platform-infrastructure` | CDK: EventBus, DNS, OIDC, KMS, Observability | ✅ Deployed prod |
| `ugsys-documentation` | ADRs, guides | ✅ PR #1 merged |

## GitHub Org
All repos live under `awscbba` org. Never push directly to `main` — always `feature/` branch → PR.

## Domain
`cbba.cloud.org.bo`

## Execution Phases
- **Phase 0** (current): Platform infra + shared libs + identity-manager scaffold
- **Phase 1**: Identity Manager — full implementation, data migration from Registry
- **Phase 2**: Projects Registry — extract from Registry monolith
- **Phase 3**: Omnichannel Service — evolve from devsecops-poc
- **Phase 4**: Mass Messaging + Admin Panel

## Key Decisions (ADRs)
- Hexagonal architecture for all services (see `architecture.md`)
- All serverless — Lambda + API Gateway, no ECS
- REST + EventBridge, no GraphQL
- DynamoDB for all new services (PostgreSQL stays only in omnichannel/devsecops-poc)
- Shared libs distributed via uv git sources pinned to version tags
- Python 3.13+, FastAPI, Pydantic v2, uv, ruff, just, devbox for all services
- Conventional commits: `feat:`, `fix:`, `ci:`, `refactor:`, `docs:`

## Inter-Service Communication
- **Sync**: REST with JWT (service-to-service uses `client_credentials` S2S tokens)
- **Async**: EventBridge custom bus `ugsys-event-bus`

## Events Contract
```
identity.user.created / updated / deleted / role_changed
identity.auth.login_success / login_failed
projects.project.created / updated / published
projects.subscription.created / cancelled
omnichannel.message.queued / sent / delivered / failed
campaigns.campaign.created / scheduled / executing / completed / failed
```

## CI/CD Rules
- CI runs on every push to `feature/**` and on every PR to `main`
- Deploy runs only on merge to `main`, behind `environment: prod` gate
- OIDC auth — no long-lived AWS keys in CI
- Slack notifications to channel `C0AE6QV0URH` via bot token, username `ugsys CI/CD`
- Repo secrets per service: `AWS_ROLE_ARN`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`
