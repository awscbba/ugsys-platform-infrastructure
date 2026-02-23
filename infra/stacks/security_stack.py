"""
SecurityStack — Shared KMS key for all ugsys services.

Services import the key ARN to encrypt DynamoDB tables, S3 buckets,
SQS queues, and Secrets Manager secrets.
"""

import aws_cdk as cdk
import aws_cdk.aws_kms as kms
from constructs import Construct


class SecurityStack(cdk.Stack):
    """Provisions the shared platform KMS key."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Shared KMS Key ────────────────────────────────────────────────────
        self.platform_key = kms.Key(
            self,
            "UgsysPlatformKey",
            alias="alias/ugsys-platform",
            description="Shared encryption key for all ugsys platform services",
            enable_key_rotation=True,  # Rotate annually (security best practice)
            removal_policy=cdk.RemovalPolicy.RETAIN,  # Never auto-delete
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "PlatformKeyArn",
            value=self.platform_key.key_arn,
            export_name="UgsysPlatformKeyArn",
            description="Shared KMS key ARN — import in service stacks for encryption",
        )
        cdk.CfnOutput(
            self,
            "PlatformKeyId",
            value=self.platform_key.key_id,
            export_name="UgsysPlatformKeyId",
        )
