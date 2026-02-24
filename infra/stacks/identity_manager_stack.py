"""
IdentityManagerStack — Lambda + API Gateway + DynamoDB + CloudWatch for ugsys-identity-manager.

Resources:
  - DynamoDB table: ugsys-identity-manager-users-{env}
  - Lambda function: ugsys-identity-manager-{env}
  - API Gateway HTTP API: ugsys-identity-manager-{env}
  - CloudWatch Log Group (KMS-encrypted)
  - IAM execution role (least privilege)
"""

import aws_cdk as cdk
import aws_cdk.aws_apigatewayv2 as apigwv2
import aws_cdk.aws_apigatewayv2_integrations as integrations
import aws_cdk.aws_dynamodb as dynamodb
import aws_cdk.aws_iam as iam
import aws_cdk.aws_kms as kms
import aws_cdk.aws_lambda as lambda_
import aws_cdk.aws_logs as logs
from constructs import Construct


class IdentityManagerStack(cdk.Stack):
    """Provisions all AWS resources for the ugsys-identity-manager service."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str,
        platform_key: kms.IKey,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── DynamoDB — Users table ─────────────────────────────────────────────
        self.users_table = dynamodb.Table(
            self,
            "UsersTable",
            table_name=f"ugsys-identity-manager-users-{env_name}",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
        )

        # GSI: email → user lookup
        self.users_table.add_global_secondary_index(
            index_name="email-index",
            partition_key=dynamodb.Attribute(name="email", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ── CloudWatch Log Group (KMS-encrypted) ──────────────────────────────
        log_group = logs.LogGroup(
            self,
            "LambdaLogGroup",
            log_group_name=f"/aws/lambda/ugsys-identity-manager-{env_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            encryption_key=platform_key,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # ── Lambda execution role ─────────────────────────────────────────────
        execution_role = iam.Role(
            self,
            "LambdaExecutionRole",
            role_name=f"ugsys-identity-manager-lambda-{env_name}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Execution role for ugsys-identity-manager Lambda",
        )

        # Basic Lambda execution (CloudWatch Logs)
        execution_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )

        # DynamoDB access — scoped to this table only
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="DynamoDBAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                ],
                resources=[
                    self.users_table.table_arn,
                    f"{self.users_table.table_arn}/index/*",
                ],
            )
        )

        # KMS — allow Lambda to use the platform key for DynamoDB/logs
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="KMSAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "kms:Decrypt",
                    "kms:GenerateDataKey*",
                    "kms:DescribeKey",
                ],
                resources=[platform_key.key_arn],
            )
        )

        # EventBridge — publish domain events
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="EventBridgePublish",
                effect=iam.Effect.ALLOW,
                actions=["events:PutEvents"],
                resources=[
                    f"arn:aws:events:{self.region}:{self.account}:event-bus/ugsys-platform-bus"
                ],
            )
        )

        # ── Lambda function ───────────────────────────────────────────────────
        # Deployed with a placeholder zip; CI/CD updates the code on every push.
        self.function = lambda_.Function(
            self,
            "LambdaFunction",
            function_name=f"ugsys-identity-manager-{env_name}",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="handler.handler",
            code=lambda_.Code.from_inline(
                # Minimal bootstrap — replaced by CI/CD on first deploy
                "def handler(event, context): return {'statusCode': 200, 'body': 'bootstrapping'}"
            ),
            role=execution_role,
            timeout=cdk.Duration.seconds(30),
            memory_size=512,
            environment={
                "APP_ENV": env_name,
                "AWS_ACCOUNT_ID": self.account,
                "DYNAMODB_TABLE_NAME": self.users_table.table_name,
                "EVENT_BUS_NAME": "ugsys-platform-bus",
                "LOG_LEVEL": "INFO",
            },
            log_group=log_group,
            tracing=lambda_.Tracing.ACTIVE,
        )

        # ── API Gateway HTTP API ──────────────────────────────────────────────
        self.api = apigwv2.HttpApi(
            self,
            "HttpApi",
            api_name=f"ugsys-identity-manager-{env_name}",
            description="ugsys Identity Manager API",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=["https://cbba.cloud.org.bo"],
                allow_methods=[apigwv2.CorsHttpMethod.ANY],
                allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
                max_age=cdk.Duration.days(1),
            ),
        )

        self.api.add_routes(
            path="/{proxy+}",
            methods=[apigwv2.HttpMethod.ANY],
            integration=integrations.HttpLambdaIntegration("LambdaIntegration", self.function),
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "ApiUrl",
            value=self.api.api_endpoint,
            export_name=f"UgsysIdentityManagerApiUrl-{env_name}",
            description="Identity Manager API Gateway endpoint",
        )
        cdk.CfnOutput(
            self,
            "FunctionName",
            value=self.function.function_name,
            export_name=f"UgsysIdentityManagerFunctionName-{env_name}",
        )
        cdk.CfnOutput(
            self,
            "UsersTableName",
            value=self.users_table.table_name,
            export_name=f"UgsysIdentityManagerUsersTable-{env_name}",
        )
