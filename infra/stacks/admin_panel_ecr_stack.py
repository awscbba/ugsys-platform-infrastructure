"""
AdminPanelEcrStack — ECR repository reference.

References the existing ECR repo (or creates it if absent) so that
AdminPanelStack can depend on this stack and get the repo object.

The repo is created outside CDK (or was created by a previous deploy attempt)
and is referenced here via from_repository_name to avoid CloudFormation
"already exists" errors. Lifecycle rules are applied via a custom resource
only when the repo is first created.
"""

import aws_cdk as cdk
import aws_cdk.aws_ecr as ecr
import aws_cdk.aws_iam as iam
import aws_cdk.custom_resources as cr
from constructs import Construct

REPO_NAME = "ugsys-admin-panel"


class AdminPanelEcrStack(cdk.Stack):
    """Ensures the ECR repository exists and exposes it for other stacks."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create the repo via a custom resource that is idempotent —
        # uses create-repository with --no-fail-if-exists equivalent via
        # AwsCustomResource which calls ECR CreateRepository and ignores
        # RepositoryAlreadyExistsException.
        ecr_create = cr.AwsCustomResource(
            self,
            "EnsureEcrRepo",
            on_create=cr.AwsSdkCall(
                service="ECR",
                action="createRepository",
                parameters={
                    "repositoryName": REPO_NAME,
                    "imageScanningConfiguration": {"scanOnPush": True},
                    "imageTagMutability": "MUTABLE",
                },
                physical_resource_id=cr.PhysicalResourceId.of(REPO_NAME),
                ignore_error_codes_matching="RepositoryAlreadyExistsException",
            ),
            on_delete=cr.AwsSdkCall(
                service="ECR",
                action="deleteRepository",
                parameters={
                    "repositoryName": REPO_NAME,
                    "force": True,
                },
                ignore_error_codes_matching="RepositoryNotFoundException",
            )
            if env_name != "prod"
            else None,
            policy=cr.AwsCustomResourcePolicy.from_statements(
                [
                    iam.PolicyStatement(
                        actions=[
                            "ecr:CreateRepository",
                            "ecr:DeleteRepository",
                            "ecr:DescribeRepositories",
                        ],
                        resources=["*"],
                    )
                ]
            ),
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
        )

        # Reference the repo by name — works whether CDK created it or it pre-existed
        self.repo = ecr.Repository.from_repository_name(self, "RepoRef", REPO_NAME)
        self.repo.node.add_dependency(ecr_create)

        cdk.CfnOutput(
            self,
            "EcrRepositoryUri",
            value=self.repo.repository_uri,
            export_name=f"UgsysAdminPanelEcrUri-{env_name}",
            description="ECR URI — set as ECR_REPOSITORY_URI secret in the service repo",
        )
