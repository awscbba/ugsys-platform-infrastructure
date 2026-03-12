"""
UserProfileServiceStack — Lambda (container image) + API Gateway + DynamoDB + CloudWatch.

Service: ugsys-user-profile-service

Resources:
  - ECR repository: ugsys-user-profile-service
  - DynamoDB table: ugsys-user-profiles-{env}
  - Lambda function (container image): ugsys-user-profile-service-{env}
  - API Gateway HTTP API: ugsys-user-profile-service-{env}
  - CloudWatch Log Group (KMS-encrypted)
  - IAM execution role (least privilege)
"""

import aws_cdk as cdk
import aws_cdk.aws_apigatewayv2 as apigwv2
import aws_cdk.aws_apigatewayv2_integrations as integrations
import aws_cdk.aws_certificatemanager as acm
import aws_cdk.aws_dynamodb as dynamodb
import aws_cdk.aws_ecr as ecr
import aws_cdk.aws_iam as iam
import aws_cdk.aws_kms as kms
import aws_cdk.aws_lambda as lambda_
import aws_cdk.aws_logs as logs
import aws_cdk.aws_route53 as route53
import aws_cdk.aws_route53_targets as route53_targets
from constructs import Construct

CUSTOM_DOMAIN = "profiles.apps.cloud.org.bo"


class UserProfileServiceStack(cdk.Stack):
    """Provisions all AWS resources for the ugsys-user-profile-service."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str,
        platform_key: kms.IKey,
        hosted_zone: route53.IHostedZone,
        certificate_arn: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── ECR Repository ────────────────────────────────────────────────────
        self.ecr_repo = ecr.Repository(
            self,
            "EcrRepo",
            repository_name="ugsys-user-profile-service",
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

        # ── DynamoDB — Profiles table ──────────────────────────────────────────
        self.profiles_table = dynamodb.Table(
            self,
            "ProfilesTable",
            table_name=f"ugsys-user-profiles-{env_name}",
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

        # GSI: email -> profile lookup (for cross-service queries)
        self.profiles_table.add_global_secondary_index(
            index_name="email-index",
            partition_key=dynamodb.Attribute(name="email", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ── CloudWatch Log Group (KMS-encrypted) ──────────────────────────────
        log_group = logs.LogGroup(
            self,
            "LambdaLogGroup",
            log_group_name=f"/aws/lambda/ugsys-user-profile-service-{env_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            encryption_key=platform_key,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # ── Lambda execution role ─────────────────────────────────────────────
        execution_role = iam.Role(
            self,
            "LambdaExecutionRole",
            role_name=f"ugsys-user-profile-service-lambda-{env_name}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Execution role for ugsys-user-profile-service Lambda",
        )

        execution_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )

        # ECR — pull container image
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="ECRPullAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:BatchCheckLayerAvailability",
                ],
                resources=[self.ecr_repo.repository_arn],
            )
        )

        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="ECRAuthToken",
                effect=iam.Effect.ALLOW,
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            )
        )

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
                    self.profiles_table.table_arn,
                    f"{self.profiles_table.table_arn}/index/*",
                ],
            )
        )

        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="KMSAccess",
                effect=iam.Effect.ALLOW,
                actions=["kms:Decrypt", "kms:GenerateDataKey*", "kms:DescribeKey"],
                resources=[platform_key.key_arn],
            )
        )

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

        # ── Lambda function (container image) ─────────────────────────────────
        self.function = lambda_.DockerImageFunction(
            self,
            "LambdaFunction",
            function_name=f"ugsys-user-profile-service-{env_name}",
            code=lambda_.DockerImageCode.from_ecr(
                self.ecr_repo,
                tag_or_digest="latest",
            ),
            role=execution_role,
            timeout=cdk.Duration.seconds(30),
            memory_size=512,
            environment={
                "APP_ENV": env_name,
                "DYNAMODB_TABLE_PREFIX": "ugsys",
                "ENVIRONMENT": env_name,
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
            description="ugsys User Profile Service API",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=[
                    "https://profile.apps.cloud.org.bo",
                    "https://registry.apps.cloud.org.bo",
                    "https://admin.apps.cloud.org.bo",
                ],
                allow_methods=[apigwv2.CorsHttpMethod.ANY],
                allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-CSRF-Token"],
                allow_credentials=True,
                max_age=cdk.Duration.days(1),
            ),
        )

        # Explicitly list methods — do NOT include OPTIONS.
        # When OPTIONS is included in a /{proxy+} route, API Gateway forwards preflights
        # to Lambda instead of handling them natively via cors_preflight config above.
        # Lambda (FastAPI with no CORSMiddleware) returns 405, causing CORS failures.
        self.api.add_routes(
            path="/{proxy+}",
            methods=[
                apigwv2.HttpMethod.GET,
                apigwv2.HttpMethod.POST,
                apigwv2.HttpMethod.PUT,
                apigwv2.HttpMethod.PATCH,
                apigwv2.HttpMethod.DELETE,
                apigwv2.HttpMethod.HEAD,
            ],
            integration=integrations.HttpLambdaIntegration("LambdaIntegration", self.function),
        )

        # ── Custom domain ─────────────────────────────────────────────────────
        if certificate_arn:
            certificate = acm.Certificate.from_certificate_arn(self, "Certificate", certificate_arn)
            domain = apigwv2.DomainName(
                self,
                "DomainName",
                domain_name=CUSTOM_DOMAIN,
                certificate=certificate,
            )
            apigwv2.ApiMapping(self, "ApiMapping", api=self.api, domain_name=domain)
            route53.ARecord(
                self,
                "AliasRecord",
                zone=hosted_zone,
                record_name=CUSTOM_DOMAIN,
                target=route53.RecordTarget.from_alias(
                    route53_targets.ApiGatewayv2DomainProperties(
                        domain.regional_domain_name, domain.regional_hosted_zone_id
                    )
                ),
            )
            cdk.CfnOutput(
                self,
                "CustomDomainUrl",
                value=f"https://{CUSTOM_DOMAIN}",
                export_name=f"UgsysUserProfileServiceCustomUrl-{env_name}",
            )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "ApiUrl",
            value=self.api.api_endpoint,
            export_name=f"UgsysUserProfileServiceApiUrl-{env_name}",
            description="User Profile Service API Gateway endpoint",
        )
        cdk.CfnOutput(
            self,
            "FunctionName",
            value=self.function.function_name,
            export_name=f"UgsysUserProfileServiceFunctionName-{env_name}",
        )
        cdk.CfnOutput(
            self,
            "EcrRepositoryUri",
            value=self.ecr_repo.repository_uri,
            export_name=f"UgsysUserProfileServiceEcrUri-{env_name}",
            description="ECR URI — set as ECR_REPOSITORY_URI secret in the service repo",
        )
        cdk.CfnOutput(
            self,
            "ProfilesTableName",
            value=self.profiles_table.table_name,
            export_name=f"UgsysUserProfilesTable-{env_name}",
        )
