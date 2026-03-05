"""
AdminPanelEcrStack — ECR repository only.

Deployed first so the ECR repo exists before AdminPanelStack tries to
create the Lambda function (which requires a valid image in ECR).
"""

import aws_cdk as cdk
import aws_cdk.aws_ecr as ecr
from constructs import Construct


class AdminPanelEcrStack(cdk.Stack):
    """Provisions only the ECR repository for the ugsys-admin-panel service."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.repo = ecr.Repository(
            self,
            "EcrRepo",
            repository_name="ugsys-admin-panel",
            image_scan_on_push=True,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep last 10 images",
                    max_image_count=10,
                    tag_status=ecr.TagStatus.ANY,
                )
            ],
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
        )

        cdk.CfnOutput(
            self,
            "EcrRepositoryUri",
            value=self.repo.repository_uri,
            export_name=f"UgsysAdminPanelEcrUri-{env_name}",
            description="ECR URI — set as ECR_REPOSITORY_URI secret in the service repo",
        )
