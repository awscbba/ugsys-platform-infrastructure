#!/usr/bin/env python3
"""
ugsys Platform Infrastructure CDK App.

Stacks:
  - SecurityStack       -> Shared KMS key
  - EventBusStack       -> Shared EventBridge custom bus
  - DnsStack            -> Route53 hosted zone (cbba.cloud.org.bo)
  - GithubOidcStack     -> OIDC provider for GitHub Actions (all repos)
  - ObservabilityStack  -> Centralized CloudWatch dashboards + alarms
"""

import sys
from pathlib import Path

# Allow running from repo root via: uv run python infra/app.py
sys.path.insert(0, str(Path(__file__).parent))

import aws_cdk as cdk

from stacks.dns_stack import DnsStack
from stacks.event_bus_stack import EventBusStack
from stacks.github_oidc_stack import GithubOidcStack
from stacks.identity_manager_stack import IdentityManagerStack
from stacks.observability_stack import ObservabilityStack
from stacks.security_stack import SecurityStack
from stacks.user_profile_service_stack import UserProfileServiceStack

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
    kms_key=security_stack.platform_key,
    env=aws_env,
    tags=tags,
)
event_bus_stack.add_dependency(security_stack)

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
    kms_key=security_stack.platform_key,
    env=aws_env,
    tags=tags,
)
observability_stack.add_dependency(event_bus_stack)
observability_stack.add_dependency(security_stack)

identity_manager_stack = IdentityManagerStack(
    app,
    f"UgsysIdentityManager-{env_name}",
    env_name=env_name,
    platform_key=security_stack.platform_key,
    env=aws_env,
    tags=tags,
)
identity_manager_stack.add_dependency(security_stack)

user_profile_service_stack = UserProfileServiceStack(
    app,
    f"UgsysUserProfileService-{env_name}",
    env_name=env_name,
    platform_key=security_stack.platform_key,
    env=aws_env,
    tags=tags,
)
user_profile_service_stack.add_dependency(security_stack)

app.synth()
