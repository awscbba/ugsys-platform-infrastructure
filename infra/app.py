#!/usr/bin/env python3
"""
ugsys Platform Infrastructure CDK App.

Stacks:
  - SecurityStack       -> Shared KMS key
  - EventBusStack       -> Shared EventBridge custom bus
  - DnsStack            -> Route53 hosted zone (apps.cloud.org.bo)
  - GithubOidcStack     -> OIDC provider for GitHub Actions (all repos)
  - ObservabilityStack  -> Centralized CloudWatch dashboards + alarms
  - FrontendStack       -> S3 + CloudFront for React SPA (registry.apps.cloud.org.bo)
"""

import sys
from pathlib import Path

# Allow running from repo root via: uv run python infra/app.py
sys.path.insert(0, str(Path(__file__).parent))

import aws_cdk as cdk
from stacks.admin_panel_ecr_stack import AdminPanelEcrStack
from stacks.admin_panel_stack import AdminPanelStack
from stacks.dns_stack import DnsStack
from stacks.event_bus_stack import EventBusStack
from stacks.frontend_stack import FrontendStack
from stacks.github_oidc_stack import GithubOidcStack
from stacks.identity_manager_stack import IdentityManagerStack
from stacks.observability_stack import ObservabilityStack
from stacks.profile_frontend_stack import ProfileFrontendStack
from stacks.projects_registry_stack import ProjectsRegistryStack
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

# Certificate ARN for apps.cloud.org.bo — must be in us-east-1 (CloudFront requirement)
# Set via CDK context: cdk deploy -c certificate_arn=arn:aws:acm:us-east-1:...
certificate_arn = app.node.try_get_context("certificate_arn") or ""

identity_manager_stack = IdentityManagerStack(
    app,
    f"UgsysIdentityManager-{env_name}",
    env_name=env_name,
    platform_key=security_stack.platform_key,
    hosted_zone=dns_stack.hosted_zone,
    certificate_arn=certificate_arn,
    env=aws_env,
    tags=tags,
)
identity_manager_stack.add_dependency(security_stack)
identity_manager_stack.add_dependency(dns_stack)

user_profile_service_stack = UserProfileServiceStack(
    app,
    f"UgsysUserProfileService-{env_name}",
    env_name=env_name,
    platform_key=security_stack.platform_key,
    hosted_zone=dns_stack.hosted_zone,
    certificate_arn=certificate_arn,
    env=aws_env,
    tags=tags,
)
user_profile_service_stack.add_dependency(security_stack)
user_profile_service_stack.add_dependency(dns_stack)

projects_registry_stack = ProjectsRegistryStack(
    app,
    f"UgsysProjectsRegistry-{env_name}",
    env_name=env_name,
    platform_key=security_stack.platform_key,
    hosted_zone=dns_stack.hosted_zone,
    certificate_arn=certificate_arn,
    env=aws_env,
    tags=tags,
)
projects_registry_stack.add_dependency(security_stack)
projects_registry_stack.add_dependency(dns_stack)

frontend_stack = FrontendStack(
    app,
    f"UgsysFrontend-{env_name}",
    env_name=env_name,
    hosted_zone=dns_stack.hosted_zone,
    certificate_arn=certificate_arn,
    env=aws_env,
    tags=tags,
)
frontend_stack.add_dependency(dns_stack)

profile_frontend_stack = ProfileFrontendStack(
    app,
    f"UgsysProfileFrontend-{env_name}",
    env_name=env_name,
    hosted_zone=dns_stack.hosted_zone,
    certificate_arn=certificate_arn,
    env=aws_env,
    tags=tags,
)
profile_frontend_stack.add_dependency(dns_stack)

admin_panel_ecr_stack = AdminPanelEcrStack(
    app,
    f"UgsysAdminPanelEcr-{env_name}",
    env_name=env_name,
    env=aws_env,
    tags=tags,
)

admin_panel_stack = AdminPanelStack(
    app,
    f"UgsysAdminPanel-{env_name}",
    env_name=env_name,
    ecr_repo=admin_panel_ecr_stack.repo,
    platform_key=security_stack.platform_key,
    hosted_zone=dns_stack.hosted_zone,
    certificate_arn=certificate_arn,
    env=aws_env,
    tags=tags,
)
admin_panel_stack.add_dependency(admin_panel_ecr_stack)
admin_panel_stack.add_dependency(security_stack)
admin_panel_stack.add_dependency(dns_stack)

app.synth()
