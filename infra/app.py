#!/usr/bin/env python3
"""
ugsys Platform Infrastructure CDK App.

Stacks:
  - EventBusStack       → Shared EventBridge custom bus
  - DnsStack            → Route53 hosted zone (awsugcbba.org)
  - GithubOidcStack     → OIDC provider for GitHub Actions (all repos)
  - SecurityStack       → Shared KMS key
  - ObservabilityStack  → Centralized CloudWatch dashboards + alarms
"""

import sys
from pathlib import Path

# Allow running from repo root via: uv run python infra/app.py
sys.path.insert(0, str(Path(__file__).parent))

import aws_cdk as cdk

from stacks.dns_stack import DnsStack
from stacks.event_bus_stack import EventBusStack
from stacks.github_oidc_stack import GithubOidcStack
from stacks.observability_stack import ObservabilityStack
from stacks.security_stack import SecurityStack

app = cdk.App()

env_name = app.node.try_get_context("env") or "dev"
aws_env = cdk.Environment(
    account=app.node.try_get_context("account"),
    region=app.node.try_get_context("region") or "us-east-1",
)

tags = {
    "Project": "ugsys-platform",
    "Environment": env_name,
    "ManagedBy": "cdk",
    "Repo": "awscbba/ugsys-platform-infrastructure",
}

# ── Stacks ────────────────────────────────────────────────────────────────────

security_stack = SecurityStack(
    app,
    f"UgsysPlatformSecurity-{env_name}",
    env=aws_env,
    tags=tags,
)

event_bus_stack = EventBusStack(
    app,
    f"UgsysPlatformEventBus-{env_name}",
    env=aws_env,
    tags=tags,
)

dns_stack = DnsStack(
    app,
    f"UgsysPlatformDns-{env_name}",
    env=aws_env,
    tags=tags,
)

github_oidc_stack = GithubOidcStack(
    app,
    f"UgsysPlatformGithubOidc-{env_name}",
    env=aws_env,
    tags=tags,
)

observability_stack = ObservabilityStack(
    app,
    f"UgsysPlatformObservability-{env_name}",
    event_bus=event_bus_stack.bus,
    env=aws_env,
    tags=tags,
)
observability_stack.add_dependency(event_bus_stack)

app.synth()
