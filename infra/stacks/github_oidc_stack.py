"""
GithubOidcStack — GitHub Actions OIDC provider + deploy roles for all ugsys repos.

Each repo gets a scoped IAM role that GitHub Actions can assume via OIDC.
No long-lived AWS credentials needed in any repo.

Repos covered:
  awscbba/ugsys-identity-manager
  awscbba/ugsys-admin-panel
  awscbba/ugsys-projects-registry
  awscbba/ugsys-mass-messaging
  awscbba/ugsys-omnichannel-service
  awscbba/ugsys-platform-infrastructure
  awscbba/ugsys-shared-libs
"""

import aws_cdk as cdk
import aws_cdk.aws_iam as iam
from constructs import Construct

GITHUB_ORG = "awscbba"
GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"

REPOS = [
    "ugsys-identity-manager",
    "ugsys-admin-panel",
    "ugsys-projects-registry",
    "ugsys-mass-messaging",
    "ugsys-omnichannel-service",
    "ugsys-platform-infrastructure",
    "ugsys-shared-libs",
]


class GithubOidcStack(cdk.Stack):
    """Provisions GitHub Actions deploy roles, importing the existing OIDC provider."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── OIDC Provider ─────────────────────────────────────────────────────
        # AWS allows only one OIDC provider per URL per account.
        # If one already exists, import it; otherwise create it.
        # ARN format: arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com
        provider_arn = (
            f"arn:aws:iam::{self.account}:oidc-provider/token.actions.githubusercontent.com"
        )
        provider = iam.OpenIdConnectProvider.from_open_id_connect_provider_arn(
            self,
            "GithubOidcProvider",
            open_id_connect_provider_arn=provider_arn,
        )

        # ── Per-repo deploy roles ─────────────────────────────────────────────
        self.deploy_roles: dict[str, iam.Role] = {}

        for repo in REPOS:
            role = iam.Role(
                self,
                f"DeployRole-{repo}",
                role_name=f"ugsys-github-deploy-{repo}",
                assumed_by=iam.WebIdentityPrincipal(
                    provider.open_id_connect_provider_arn,
                    conditions={
                        "StringLike": {
                            "token.actions.githubusercontent.com:sub": (
                                f"repo:{GITHUB_ORG}/{repo}:ref:refs/heads/main"
                            )
                        },
                        "StringEquals": {
                            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                        },
                    },
                ),
                description=f"GitHub Actions deploy role for {GITHUB_ORG}/{repo}",
                max_session_duration=cdk.Duration.hours(1),
            )

            # PowerUserAccess is intentionally broad for CDK deploys.
            # Tighten per-repo once service boundaries are stable.
            role.add_managed_policy(
                iam.ManagedPolicy.from_aws_managed_policy_name("PowerUserAccess")
            )

            self.deploy_roles[repo] = role

            cdk.CfnOutput(
                self,
                f"DeployRoleArn-{repo}",
                value=role.role_arn,
                export_name=f"UgsysDeployRole-{repo}",
                description=f"Add to {repo} GitHub Actions secrets as AWS_ROLE_ARN",
            )
